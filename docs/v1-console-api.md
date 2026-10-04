# `/v1` — the gateway as the Northstar console's front door

Contract: `openapi/p3.json` (regenerate with `python -m scripts.export_openapi`). Everything the console asks an AI or does as an
action goes through here; the console reads P1 data directly (read-only).

## Identity
`POST /v1/demo/login {role}` (only when `GATEWAY_DEMO_MODE=1`, per-IP rate-limited) returns a short-lived HMAC-signed token for
`viewer | analyst | manager | finance | admin`, labelled **demo identity** everywhere (`GET /v1/me`). Each login gets a fresh
user id, so "a different manager" is a different login. Forged, tampered, expired or foreign-secret tokens are rejected (401), and
tokens stop working when demo mode is off. With `GATEWAY_REQUIRE_IDENTITY=1` the trusted-proxy headers are used instead.
Set `GATEWAY_DEMO_TOKEN_SECRET` for tokens that survive a restart; otherwise a per-process secret is used.

## AI routes: `POST /v1/ask`, `/v1/investigations`, `/v1/nl-filter`, `/v1/briefing`
pre-flight (PII, normalisation, rules + classifier, session limits) -> assistant (P2, service key, user identity in
`X-Northstar-User/Role`) -> post-flight (jailbreak compliance, leak, role exposure). Every response carries
`gateway: {decision, reason, phase, layers: {layer: {decision, latency_ms}}, pii_found, latency_ms, trace_id}`; a blocked request
returns `blocked: true` with no answer and the assistant is not called. `traceparent` is accepted or generated, forwarded to P2
(and so P1), echoed as `X-Trace-ID`, stored with every decision, and emitted as OpenTelemetry spans (API only: a no-op until the
operator configures an exporter; durations are ~0 because spans are emitted after the request, the measured layer latency is an
attribute). The briefing route has no user text to inspect, and says so.

## Actions and approvals
Proposals go console -> P2 -> this gateway's action firewall (`/gateway/actions/authorize`: policy -> taint -> approval). The
queue and policy are the gateway's. `GET /v1/approvals`, `POST /v1/approvals/{id}/approve|reject` need `manager`/`admin`;
the queue itself enforces that the decider is a different person and, for `release_payment`, a **different role** from the
requester. The policy now lists `finance` for payment hold/release and `analyst` for standard proposals (all still require
approval), so the console's five personas are meaningful.

## Governance (persisted, not in-process)
`gateway/v1/store.py` ingests `logs/gateway.jsonl` and `logs/actions.jsonl` into SQLite incrementally and idempotently; session and
user ids are stored as short salted hashes; prompt text is never stored.
- `GET /v1/governance/summary?window=5m|1h|24h|7d`: requests (counted once), blocks by layer and rule, actions allowed / held /
  denied by stage, PII requests, p50/p95 latency, time series.
- `GET /v1/governance/events`: redacted for viewers (hashed sessions, no reasons), full detail for `admin`.
- `GET /v1/governance/policy-matrix`: role x tool -> allow / approval / deny, generated from `config/tool_policies.yaml`, with the
  argument constraints and a policy hash. `GET /v1/rules`: every detector rule with OWASP LLM id (2025 numbering), ATLAS pointer
  (**unverified**), red-team links (`config/rules_catalog.yaml`; a test fails on an unmapped rule). `GET /v1/config`: backends,
  thresholds, policy hash, model-integrity hashes.

## Attack Lab (live)
`GET /v1/lab/scenarios`, `POST /v1/lab/run {scenario_id, defenses: on|off, target: stub|p2}`. Text scenarios (direct injection,
Unicode tag smuggling, base64, ROT13, authority pretext, admin command, a benign control) run through the real middleware;
action scenarios (86 from `corpus/agentic_attacks.yaml`, six curated: indirect injection, taint evasion, unauthorized tool,
argument exfiltration, poisoned-invoice payment release, self-approval) replay against a fresh isolated firewall. The response is
the step-by-step timeline (PII, each detector, policy, taint, each approval attempt) plus whether any tool executed. `defenses: off`
runs the undefended stub only, with a simulated upstream. `target: p2` costs model tokens and needs `P2_URL`. Limited to 20 runs
per minute per identity. The agent in action scenarios is **scripted** (it obeys the injection); this measures the firewall, not
how often real models are hijacked. ROT13 is a known weakness (RT-06) and is reported as whatever it does.

## Fixed: request double-counting
The dashboard counted one record per phase, so a forwarded request counted twice: 8 prompts of which 4 were blocked read as 12
requests / 33% blocked instead of 8 / 50%. Counting is now per request id (`gateway/dashboard.py: summarize_requests`), with a
regression test using exactly that scenario; the governance summary uses the same rule.

## Finding: the deployed classifier blocks some benign console phrasing
`python -m scripts.measure_console_false_positives <nl_filter_40.json>` -> `reports/p3_console_false_positives.json`: **3 of 40**
natural-language filter phrasings (7.5%) are blocked by `scratch_classifier` ("Show me orders above 50k", "suppliers with the
worst breach rates", "the twenty biggest cases"); **0 of 30** page questions are. These are false positives of the existing
short-input weakness, measured here, not tuned away. The console must show the gateway block with an explanation and let the user
rephrase; whether to exempt schema-validated filter requests from the classifier is a decision that needs its own evaluation.

## Honest limits
Governance numbers are only as complete as the log on this host's disk (ephemeral on free hosting); the lab's action agent is a
script; ATLAS ids are unverified; `/metrics` needs `prometheus-client` installed (not in the locked requirements yet: the
endpoint returns 501 until it is); the demo identity proves nothing about a real person.
