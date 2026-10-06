# `/v1` — the copilot API behind the Northstar console

All routes need the service API key (`X-API-Key`); the console reaches them through the P3 gateway, which also supplies the
end user's identity (`X-Northstar-User`, `X-Northstar-Role`: viewer < analyst < manager | finance < admin). A missing
identity is a read-only viewer. `traceparent` is accepted and propagated to P1 and the gateway. OpenAPI: `openapi/p2.json`.

| Route | What it does |
|---|---|
| `POST /v1/ask` | Context-aware answer. The page context (`case_id`, `supplier_id`, `control`, `as_of`) pre-fetches the matching P1 `/v1` objects, policy chunks are retrieved, every fact gets an evidence id, the model must cite ids, and **every claim is then verified**: figures must appear in the cited evidence, policy claims pass the claim-support gate. Unsupported claims are returned with `supported: false`, not hidden. Abstains with a reason (no evidence / outside procurement). With no model it returns a labelled template built from the same evidence. |
| `POST /v1/investigations`, `GET .../{id}`, `GET /v1/investigations`, `GET .../{id}/export?format=md\|pdf` | Persisted fixed-shape report: summary, evidence (data vs documents, linked), root causes (association language; causal wording is flagged), policy, recommendations (each a proposable action or null), limitations. SQLite store. PDF is produced by a dependency-free writer. |
| `POST /v1/briefing` | 3-item executive brief from P1's `/v1/briefing` facts; every sentence cites fact ids and any figure must appear in a cited fact, otherwise a labelled deterministic template is used. Cached per `as_of`. |
| `POST /v1/nl-filter` | Words -> parameters for P1 `/v1/queue` or `/v1/suppliers`, validated against a JSON schema; anything outside it (dates, owners, regions) is rejected. The model never touches data. |
| `POST /v1/interventions`, `GET`, `GET /{id}`, `POST /{id}/approve`, `/reject`, `/outcome` | Persisted ledger with a status history. A proposal is authorised by **P3's action firewall** (policy -> taint -> approval); a held proposal needs a *different* manager/admin (separation of duties); approval executes by writing to P1's ledger (P1 randomizes treat/holdout; a holdout case is logged, not acted on); outcomes are written back to P1. |
| `GET /v1/traces/{trace_id}` | Spans: P1 calls, retrieval, model calls (model, tokens, cost), the claim gate. Kept 14 days. |
| `GET /v1/spend` | Current spend against the caps. |

## Spend guard
Every model call is checked **before** it is made against a per-request, per-day and per-month cap (defaults $0.05 / $2 /
$20, env `P2_SPEND_PER_REQUEST_USD`, `_PER_DAY_USD`, `_PER_MONTH_USD`), persisted in SQLite; a call that would cross a cap
returns HTTP 429. The provider is `P2_LLM_PROVIDER` (`anthropic` when `ANTHROPIC_API_KEY` is set, otherwise `none`; `ollama` only when asked
for); the static system prompt is marked for prompt caching. The key is read from the environment by the SDK and never logged.

## Running without a key: a local model
`P2_LLM_PROVIDER=ollama` uses a local [Ollama](https://ollama.com) (`OLLAMA_MODEL`, default `qwen2.5:7b-instruct`; `OLLAMA_BASE_URL`, default
`http://localhost:11434`; `OLLAMA_NUM_CTX`, default 8192; `OLLAMA_TIMEOUT_S`, default 180). It is free, offline and **much weaker than Claude**; every
result carries `model: "ollama:<name>"` and cost 0 (so the money caps never refuse it; each call is still recorded). It is never auto-detected, so a
deployed instance does not probe localhost. Ollama truncates a prompt longer than its context window without saying so, so the provider sets the window and
refuses a prompt that would not fit (the caller then returns the labelled template) instead of letting the model answer from part of the evidence. When the
prompt asks for JSON the server is asked for valid JSON. If Ollama is not running or the model is not pulled, the answer is the template, with the reason in
`notes`. Whether the whole platform works with it is checked by `scripts/local_chain_check.py` in the P3 repo.

## Evaluation of the new surfaces
- `python -m scripts.eval_v1_ask --dry-run` estimates the cost of 130 questions (30 per page type: case, supplier, overview,
  finance; plus 10 abstention probes). Scores: entity, grounded (>= 80% of claims supported), cited, probe abstention.
  Needs P1 reachable and `ANTHROPIC_API_KEY`; **not yet run** (no key was available to the session that built it).
- `python -m scripts.eval_v1_nl_filter`: exact-match accuracy on `data/eval/nl_filter_40.json` (35 filters + 5 that must be
  rejected). The deterministic rule parser scores 40/40, but the set and the parser were written by the same author in one
  sitting, so that is a smoke test, not a generalisation estimate. Model accuracy: pending (`--model --max-cost-usd`).
- Briefing: every sentence is checked automatically against its fact ids (unit tests); a model output that fails the check is
  replaced by the template, and the response says which one it is.

## Honest limits
Claim verification checks figures and key terms against cited evidence; it cannot tell whether a sentence's *reasoning* is
sound. In particular **a claim with no figure that cites real evidence passes the live-data check, whatever it says**: "supported" means "its numbers match what
it cites", not "true". That matters more for a small local model than for Claude, so any local-model evaluation must be labelled as such. Descriptive drivers come from P1, not model attributions. The ledger and traces are SQLite on the service's disk
(ephemeral on free hosting). Without a model the copilot is a template over the same evidence.
