# App integration

What each application repository has to change to run behind this infrastructure. **None of this
has been done yet**, and none of it can be verified without a deployed project, so treat the code
below as the intended change, not tested code.

The infrastructure is the *authentication* half: Cloud Run's IAM check refuses any call without a
valid Google ID token from an allowed identity. The apps are the *presenting a token* half.

## The pattern: presenting an ID token

A caller proves who it is by attaching a short-lived, Google-signed ID token whose **audience** is the
URL of the service it is calling. On Cloud Run the token is minted by the metadata server for the
service's attached service account, so there is no key and no secret to store.

```python
import time
import google.auth.transport.requests
import google.oauth2.id_token

_request = google.auth.transport.requests.Request()
_cache: dict[str, tuple[float, str]] = {}


def id_token_for(audience: str) -> str:
    """ID token for `audience` (the callee's URL). Tokens live about an hour; reuse for 50 minutes."""
    now = time.monotonic()
    cached = _cache.get(audience)
    if cached and now < cached[0]:
        return cached[1]
    token = google.oauth2.id_token.fetch_id_token(_request, audience)
    _cache[audience] = (now + 50 * 60, token)
    return token

# headers = {"Authorization": f"Bearer {id_token_for(base_url)}"}
```

`fetch_id_token` only works where there is a service-account identity (Cloud Run, or a service
account in ADC). For local development and `docker compose`, add an explicit switch such as
`UPSTREAM_AUTH=none` and skip the header, so nothing pretends to be authenticated locally.

Add `google-auth` and `requests` to the dependencies. The Terraform in `envs/dev` already sets each
caller's target URL as an environment variable, and that value is exactly the audience to use.

## llm-security-gateway (P3): the public front door

| Change | Detail |
|---|---|
| Authenticate to the assistant | In `gateway/adapters/operations_assistant_adapter.py`, replace the static `X-API-Key` / public keyless route with `Authorization: Bearer <id_token_for(OPS_ASSISTANT_URL)>`. |
| Use the protected route | `OPS_ASSISTANT_CHAT_PATH=/chat` (Terraform already sets it). The public `/demo/chat` fallback goes away because the assistant is no longer reachable anonymously. |
| Drop the static secret | Remove `OPS_ASSISTANT_API_KEY` and the `401 -> /demo/chat` auto-fallback. |
| **Log decisions as JSON on stdout** | `gateway/logging_schema.py` currently appends to `logs/gateway.jsonl`, which Cloud Run discards. Also `print(json.dumps({**asdict(record), "severity": "WARNING" if record.decision == "block" else "INFO"}), flush=True)`. The block-rate alert counts `jsonPayload.decision = "block"`, so without this it never fires. |
| Keep `/health` anonymous | The public uptime check expects a 2xx there. |
| Unchanged | The detection layers, PII checks and the LLM action firewall are the second line of defence described in [cloud-security.md](cloud-security.md#defence-in-depth-with-the-gateways-llm-action-firewall). |

## operations-assistant (P2): private, callable only by the gateway

| Change | Detail |
|---|---|
| Authenticate to the performance API | In `src/tools/client.py`, replace `X-API-Key: OPS_PERFORMANCE_API_KEY` with `Authorization: Bearer <id_token_for(OPS_PERFORMANCE_API_URL)>` and delete `OPS_PERFORMANCE_API_KEY`. |
| Rely on IAM for inbound auth | Leave `API_KEY` unset in Cloud Run. Today the app *fails open* when it is unset, which is only safe because IAM is in front. Prefer an explicit switch (for example `AUTH_MODE=iam`) so a misdeployed copy fails closed instead. |
| LLM keys | Already read from the environment; Terraform injects them from Secret Manager (`GROQ_API_KEY` by default). |
| Vector store | Chroma writes to a local directory, and Cloud Run instances are ephemeral and scale to zero. Build the index into the image (run `scripts.index_documents` in CI) or the first request after a cold start sees an empty store. `docs/deployment.md` in that repo already flags this. |
| Cold start | If the embedding model is downloaded on first use, bake it into the image too, or every cold start pays for the download. |

## operations-performance (P1): private, callable only by the assistant

| Change | Detail |
|---|---|
| Inbound auth | As for the assistant: `API_KEY` unset, IAM is the check. |
| BigQuery credentials | `bigquery.Client(project=...)` already uses Application Default Credentials, so the attached service account is used automatically. Remove `GOOGLE_APPLICATION_CREDENTIALS` and the key-file instructions from `.env.example` and the docs; policy rule `KEY-001` forbids creating keys. |
| Database roles | The API uses `API_DB_USER` / `API_DB_PASSWORD` (the read-only role); the pipeline uses `DB_USER` / `DB_PASSWORD` (the writer). Terraform wires exactly that split. |
| Load into the existing tables | Terraform creates the partitioned, clustered tables. `scripts/migrate_to_bigquery.py` must *append to or truncate* them; a load into a table that does not exist creates a plain unpartitioned one. |
| Pipeline entrypoint | The job runs `python -m scripts.run_pipeline` (override with the `pipeline_args` variable). The repository does not use Prefect, so the job is simply that script on a schedule. |

## Deploy workflow (all three repositories)

Copy to `.github/workflows/deploy.yml` and replace `SERVICE_NAME`. It authenticates with workload
identity federation (no key), **scans the image with Trivy and refuses to deploy on a HIGH or
CRITICAL finding**, pushes to Artifact Registry and updates the *image only*: env vars, secrets,
scaling and IAM stay owned by Terraform.

```yaml
name: deploy

on:
  push:
    branches: [main]

permissions:
  contents: read
  id-token: write # federation token

concurrency:
  group: deploy
  cancel-in-progress: false

env:
  IMAGE: ${{ vars.GCP_REGION }}-docker.pkg.dev/${{ vars.GCP_PROJECT_ID }}/northstar/SERVICE_NAME

jobs:
  deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      # (run the repository's own tests here first)

      - uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3.0.0
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOY_SA }}

      - name: Build
        run: docker build -t "$IMAGE:${{ github.sha }}" .

      - name: Scan (a HIGH or CRITICAL finding stops the deploy)
        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
        with:
          image-ref: ${{ env.IMAGE }}:${{ github.sha }}
          severity: HIGH,CRITICAL
          ignore-unfixed: true
          exit-code: "1"

      - name: Push
        run: |
          gcloud auth configure-docker "${{ vars.GCP_REGION }}-docker.pkg.dev" --quiet
          docker push "$IMAGE:${{ github.sha }}"

      - name: Roll out the new image (image only)
        run: |
          gcloud run services update SERVICE_NAME \
            --region "${{ vars.GCP_REGION }}" \
            --image "$IMAGE:${{ github.sha }}"
          # operations-performance also owns the job that shares its image:
          # gcloud run jobs update operations-pipeline --region ... --image "$IMAGE:${{ github.sha }}"
```

The repository variables (`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`) are printed
by `terraform output github_repository_variables` in `envs/bootstrap`. The deploy identity can be
assumed only by a workflow on `refs/heads/main` of that repository, and holds `run.developer` on
its own service plus `artifactregistry.writer` on the one repository, nothing else.

Prefer deploying by digest (`docker push` prints it) over a tag if you want the rollout to be
immutable end to end.
