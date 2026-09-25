# App integration

What each application repository changes to run behind this infrastructure, and where each change
stands. The infrastructure is the *authentication* half: Cloud Run's IAM check refuses any call
without a valid Google ID token from an allowed identity. The applications are the *presenting a
token* half.

**None of it has run against a real project.** The code is unit-tested with Google's
`fetch_id_token` mocked and the workflows are linted, but the first real token, the first real
deploy and the first real 403 are still ahead (see [runbook.md](runbook.md)).

## Status

| Repository | Branch | What it changes | State |
|---|---|---|---|
| [`llm-security-gateway`](https://github.com/HerschCode/llm-security-gateway) (P3) | [`feat/google-id-token-auth`](https://github.com/HerschCode/llm-security-gateway/tree/feat/google-id-token-auth) | `AUTH_MODE=google_id_token` for calls to operations-assistant; manual `deploy.yml`; deployment notes | Written. 49 new tests; the whole suite passes locally (428 passed, 1 skipped, 26 expected failures that were already there). Not run on Cloud Run |
| [`operations-assistant`](https://github.com/HerschCode/operations-assistant) (P2) | [`feat/google-id-token-auth`](https://github.com/HerschCode/operations-assistant/tree/feat/google-id-token-auth) | `AUTH_MODE=google_id_token` for calls to operations-performance and for its `/health` probe; manual `deploy.yml`; deployment notes | Written. 54 new tests; the whole suite passes locally (304). Not run on Cloud Run |
| [`operations-performance`](https://github.com/HerschCode/operations-performance) (P1) | [`ci/manual-deploy-workflow`](https://github.com/HerschCode/operations-performance/tree/ci/manual-deploy-workflow) | Manual `deploy.yml` that also updates the pipeline job; a pointer from `docs/cloud-architecture.md`. No code change: it only receives calls | Written. Workflow linted with actionlint; never run |

**The branches are pushed but no pull request is open yet**, so the repositories' own CI (`Tests`,
`lint`, `security`) has not run on these changes; opening the pull requests is what runs it, and it is
the check on the code itself that this page cannot make. What is *not* proven by anything yet is listed
at the end of this page.

## The contract: `AUTH_MODE`

A caller proves who it is by attaching a short-lived, Google-signed ID token whose **audience** is the
URL of the service it is calling. On Cloud Run the token is minted by the metadata server for the
revision's service account, so there is no key to store, rotate or leak.

`AUTH_MODE` selects how a service authenticates its **outbound** calls. Terraform sets it to
`google_id_token` on the gateway and the assistant, the two services that call another one. It does
not decide who may call the service itself: that is Cloud Run IAM.

| Value | Behaviour |
|---|---|
| unset, or `api_key` (the default) | What the service did before: a static key in `X-API-Key`, if one is configured. Nothing changes for local runs, `docker compose` or Render |
| `google_id_token` | `Authorization: Bearer <ID token>`, from `google.oauth2.id_token.fetch_id_token(Request(), audience)`. The audience is the callee's base URL (`OPS_ASSISTANT_URL`, `OPS_PERFORMANCE_API_URL`: the value Terraform already sets, with any trailing slash trimmed). No `X-API-Key` is sent |
| anything else | An error, never a silent fallback: a typo must not downgrade a service to sending nothing. Callers report it as "upstream unavailable" |

The details that matter:

* **Cached until shortly before expiry.** One token per audience, reused until five minutes before its
  `exp` claim (Google issues them for an hour), so a request does not pay for a metadata-server round
  trip. Concurrent requests share one refresh, and a token that is already inside the margin is never
  cached.
* **Fails closed.** If no token can be minted (no service-account identity, as on a laptop) the call is
  not made unauthenticated: P2's client raises `OpsPerformanceUnavailable`, P3's adapter returns a
  `[backend-error]` string, and P2's `/health` probe reports the dependency unreachable. In
  `google_id_token` mode P3 also stops retrying a 401 against the anonymous `/demo/chat` route, which
  would hide a credentials problem.
* **Local development is unchanged.** `fetch_id_token` only works where Google supplies an identity, so
  leave `AUTH_MODE` unset everywhere else. `google-auth` is imported lazily.
* **One implementation, two copies.** `upstream_auth.py` is byte-identical in P2 (`src/tools/`) and P3
  (`gateway/adapters/`): they are separate repositories with no shared package, and each docstring says
  to change both.

**What the tests prove.** With `fetch_id_token` and the clock mocked: the token is attached, the
audience is the target URL, the token is reused until near expiry and then refreshed, an unknown mode
or a failure to mint fails closed, and the default mode still sends exactly what it always did (a
regression test per caller, on the real request headers via `httpx.MockTransport`). Each of those was
also checked by mutation: removing the cache, the refresh margin, the trailing-slash trim, the lock,
the error handling or the token on the health probe makes a test fail.

**What they cannot prove** is Google's side: that Cloud Run accepts the deterministic URL as the
audience (assumption 4 in the [runbook](runbook.md#assumptions-to-confirm-on-first-apply)), and that the
metadata server hands a real revision a token.

## llm-security-gateway (P3): the public front door

| Change | State |
|---|---|
| Authenticate to the assistant with an ID token (`gateway/adapters/operations_assistant_adapter.py`) | Done, on the branch |
| Use the protected route (`OPS_ASSISTANT_CHAT_PATH=/chat`) | Terraform sets it. The `401 -> /demo/chat` fallback is off in `google_id_token` mode and unchanged in the default mode |
| Drop the static secret | Terraform provisions no `OPS_ASSISTANT_API_KEY`, and `google_id_token` mode never sends one. The code path stays for the default mode |
| **Log decisions as JSON on stdout** | **Not done.** `gateway/logging_schema.py` appends to `logs/gateway.jsonl`, which Cloud Run discards. Also `print(json.dumps({**asdict(record), "severity": "WARNING" if record.decision == "block" else "INFO"}), flush=True)`. The block-rate alert counts `jsonPayload.decision = "block"`, so without this it never fires |
| Keep `/health` anonymous | Unchanged. The public uptime check expects a 2xx there |
| `TRUSTED_PROXY_HOPS` | Unset, as on Render: the per-IP limit sees Google's front end as one client until the real `X-Forwarded-For` chain has been verified |
| Unchanged | The detection layers, PII checks and the LLM action firewall: the second line of defence in [cloud-security.md](cloud-security.md#defence-in-depth-with-the-gateways-llm-action-firewall) |

Terraform's `EMBEDDING_BACKEND` is `none`, the gateway's own default and its Render setting: its
author disabled layer 2 after it scored 0% on their corpus, and this repository matches that rather
than second-guessing it.

## operations-assistant (P2): private, callable only by the gateway

| Change | State |
|---|---|
| Authenticate to the performance API (`src/tools/client.py`) and probe `/health` with the same token (`src/api/dependencies.py`) | Done, on the branch |
| Rely on IAM for inbound auth | Terraform leaves `API_KEY` unset. The app *fails open* when it is unset, which is only safe because IAM is in front. **Not done:** an explicit fail-closed switch, so a misdeployed copy refuses instead. Name it something other than `AUTH_MODE`, which is about outbound calls |
| LLM keys | Already read from the environment; Terraform injects them from Secret Manager (`GROQ_API_KEY` by default). Nothing to change |
| Vector store | **Not done.** Chroma writes to a local directory and Cloud Run instances are ephemeral and scale to zero: build the index into the image (run `scripts.index_documents` in CI) or the first request after a cold start sees an empty store. `docs/deployment.md` in that repository already flags this |
| Cold start | **Not done.** If the embedding model is downloaded on first use, bake it into the image too, or every cold start pays for the download |
| Image size | **Not done.** `sentence-transformers` brings in torch: roughly 2 GB of torch and CUDA libraries on Linux (torch's wheel alone is 555 MB on PyPI, before its CUDA dependencies). Use a CPU-only torch or split the requirements before the first real build; it also pushes Artifact Registry past its free 0.5 GiB ([cost.md](cost.md)) |

## operations-performance (P1): private, callable only by the assistant

| Change | State |
|---|---|
| Inbound auth | As for the assistant: `API_KEY` unset and IAM is the check, with the same fail-open caveat |
| BigQuery credentials | `bigquery.Client(project=...)` already uses Application Default Credentials, so the attached service account is used automatically. **Not done:** remove the `GOOGLE_APPLICATION_CREDENTIALS` key-file instructions from `.env.example` and the docstring of `scripts/migrate_to_bigquery.py`; policy rule `KEY-001` forbids creating keys |
| Database roles | Already there: the API uses `API_DB_USER` / `API_DB_PASSWORD` (the read-only role); the pipeline uses `DB_USER` / `DB_PASSWORD` (the writer). Terraform wires exactly that split |
| Load into the existing tables | Terraform creates the partitioned, clustered tables. `scripts/migrate_to_bigquery.py` loads with `WRITE_TRUNCATE` by default; confirm on the first real load that the partitioning survives (a load into a table that does not exist creates a plain one) |
| Pipeline entrypoint | Exists: the job runs `python -m scripts.run_pipeline` (override with the `pipeline_args` variable). The image installs `requirements.txt`, which has no Prefect, so this is the plain script and not `flows.pipeline_flow` |

## The deploy workflow (all three repositories)

Each repository has `.github/workflows/deploy.yml`, rendered from one template so the three cannot
drift apart.

* **Manual dispatch only, and only from `main`.** The first deploys should be deliberate, and the
  deploy identity's workload-identity grant is pinned to `refs/heads/main` of that repository, so
  Google would refuse any other ref anyway.
* **A no-op until configured.** A `preflight` job checks the ref and the four repository variables
  (`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`; `terraform output
  github_repository_variables` in `envs/bootstrap` prints them). If any is missing it passes, green,
  with a notice naming it, and the `deploy` job is skipped. There is no secret anywhere.
* **Authenticate** with workload identity federation: the run's OIDC token is exchanged for the deploy
  service account's short-lived credentials. No key.
* **Build, scan, push.** Build the repository's image (P3 builds `Dockerfile.render`), scan it with
  Trivy (a fixable HIGH or CRITICAL finding stops the run), push it to Artifact Registry.
* **Roll out by digest, image only.** `gcloud run deploy SERVICE --image <repo>@sha256:...`, so the
  revision runs exactly the bytes that were scanned. Environment variables, secrets, scaling and IAM
  stay with Terraform: `ignore_changes` on the image in
  [`modules/cloud_run_service`](../modules/cloud_run_service/main.tf) is what stops the next
  `terraform apply` reverting the rollout, and the command passes neither `--allow-unauthenticated`
  nor `--no-allow-unauthenticated`, so a deploy can never change who may call a service.
  operations-performance also updates the `operations-pipeline` job, which shares its image.

What the deploy identity holds is small and exact: `run.developer` on its own service,
`iam.serviceAccountUser` on that service's runtime account, and `artifactregistry.writer` on the one
repository. It is generated in [`envs/dev`](../envs/dev/services.tf) and listed in the
[identity map](cloud-security.md#2-identity-map). Whether those roles are *sufficient* for `gcloud run
deploy` on an existing service is assumption 7 in the runbook: the first deploy will name any missing
permission.

## Not done, and known gaps

* **The gateway's JSON decision log on stdout** (above): until it lands, the block-rate alert cannot
  fire.
* **P2's image is large** (above), and its index and embedding model are not baked in.
* **Fail-closed inbound auth** for P1 and P2 (above).
* **The deploy workflows have never run.** They are linted, and the preflight logic is the same pattern
  as this repository's `plan.yml` and `apply.yml`, which are exercised on GitHub. Everything after the
  preflight (federation, build, scan, push, roll-out) is untested until a project exists.
* **The first real token.** Whether the metadata server returns one on a revision, and whether Cloud
  Run accepts the deterministic service URL as its audience, is what the runbook's step 9 confirms.
