# Runbook

How to stand the stack up, prove it is locked down, and tear it down again. Everything up to
step 3 is a one-off; the rest is repeatable.

> **Status.** Steps 1 to 10 have **not been run against a live project yet** (this repository was
> built before a billing account was available). Everything below is written from provider and
> Google documentation, and the parts that only a live project can confirm are collected in
> [Assumptions to confirm on first apply](#assumptions-to-confirm-on-first-apply). If a step fails,
> the troubleshooting section is the first place to look, and please fix the doc.

Placeholders: `PROJECT` is your project ID, `NUMBER` its project number, `BILLING` the billing
account ID (`gcloud billing accounts list`), `OWNER` your GitHub user.

## 0. Prerequisites

* A Google account with a billing account, and a GitHub account with the four repositories
  (`northstar-infra`, `operations-performance`, `operations-assistant`, `llm-security-gateway`).
* On your machine: `gcloud`, and the pinned toolchain
  (`bash scripts/install-tools.sh && export PATH="$PWD/.tools/bin:$PATH"`).
* Run `bash scripts/check.sh quick` once. It needs no credentials and proves the toolchain works.

## 1. Create the project and link billing (by hand, on purpose)

```bash
gcloud projects create PROJECT --name="Northstar"
gcloud billing projects link PROJECT --billing-account=BILLING
gcloud config set project PROJECT
gcloud projects describe PROJECT --format='value(projectNumber)'   # this is NUMBER
```

Terraform does not create the project: doing so needs organisation-level rights and would put the
billing link in a place the pipeline could change.

## 2. Let Terraform enable the rest (the minimum it needs to start)

```bash
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com iam.googleapis.com billingbudgets.googleapis.com
gcloud auth application-default login
```

Your user needs **Owner** on the project, and **Billing Account Costs Manager** (or Administrator)
on the billing account, or the budget cannot be created.

## 3. Create the two state buckets

Two buckets, on purpose: the pipeline identities can reach only the dev one, so a compromised
pipeline can neither read nor rewrite the state that describes its own permissions. Both are private,
versioned (state history is your undo button) and in a free-tier region.

```bash
for suffix in bootstrap dev; do
  gcloud storage buckets create gs://PROJECT-tfstate-$suffix --project=PROJECT --location=us-central1 \
    --uniform-bucket-level-access --public-access-prevention
  gcloud storage buckets update gs://PROJECT-tfstate-$suffix --versioning
done
```

## 4. Apply the identity plane: budget first

```bash
cd envs/bootstrap
cp terraform.tfvars.example terraform.tfvars      # fill in project, number, billing account, owner, bucket names
terraform init -backend-config="bucket=PROJECT-tfstate-bootstrap" -backend-config="prefix=northstar/bootstrap"
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

Nothing in this root costs money. It enables the APIs, creates the **₹100 / ₹400 / ₹800 budget**,
the workload identity pool, the five GitHub identities and the audit logging. Confirm the budget in
the console (Billing > Budgets & alerts) and that its email recipients are you before going further:
this is the alarm everything after it relies on.

Then print what GitHub needs:

```bash
terraform output -json github_repository_variables
```

## 5. Configure GitHub

Set the values from that output as Actions **variables** (they are not secrets) on each repository:

```bash
gh variable set GCP_PROJECT_ID      --body PROJECT --repo OWNER/northstar-infra   # ...and the others in the output
```

On `northstar-infra`, also:

* **Environment `dev-apply`**: add yourself as a required reviewer and limit deployment branches to `main`.
  Google only mints the apply identity's token for a job running in this environment.
* **Branch protection on `main`**: require a pull request, require review from Code Owners, and require
  the `ci` checks (`fmt, validate, module tests`, `tflint`, `checkov and trivy`, `policy-as-code`,
  `generated docs up to date`).

## 6. Apply the workloads

The first time, apply by hand as yourself. Afterwards CI can do it (Actions > *apply* > Run workflow).

```bash
cd envs/dev
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config="bucket=PROJECT-tfstate-dev" -backend-config="prefix=northstar/dev"
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

The services start on Google's public *hello* image and the job's schedule starts **paused**, so this
apply succeeds before any app has shipped. If it fails with *"service account does not exist"*,
that is eventual consistency; apply again.

## 7. Add the secrets (they never touch Terraform)

```bash
terraform output secrets     # which secret, which identity reads it, what env var it becomes

echo -n "$GROQ_API_KEY"        | gcloud secrets versions add groq-api-key                  --data-file=-
echo -n "$NEON_API_PASSWORD"   | gcloud secrets versions add neon-api-reader-password      --data-file=-
echo -n "$NEON_WRITER_PASSWORD"| gcloud secrets versions add neon-pipeline-writer-password --data-file=-
```

Version 1 is a placeholder; `latest` now resolves to your value. Optionally disable the placeholder
(`gcloud secrets versions disable 1 --secret=<id>`) to stay at one live version per secret.

## 8. Ship the apps

Each app repository has a manual `deploy.yml` and, for the two that call another service, the
`AUTH_MODE=google_id_token` client code, in the pull requests listed in
[app-integration.md](app-integration.md#status). Merge those first, then, in each repository:

1. Settings > Secrets and variables > Actions > **Variables**: add the variables that
   `terraform output github_repository_variables` printed for it (`GCP_PROJECT_ID`, `GCP_REGION`,
   `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`). The workflow does nothing until they exist.
2. Actions > deploy > **Run workflow**, on `main`. Deploy backwards along the call chain:
   operations-performance, then operations-assistant, then llm-security-gateway.
3. Before the gateway, make the remaining changes marked *Not done* in
   [app-integration.md](app-integration.md) that you care about: above all the JSON decision log on
   stdout (the block-rate alert cannot fire without it) and a slimmer operations-assistant image.

When a real image is live:

```hcl
# envs/dev/terraform.tfvars
use_http_startup_probes  = true
pipeline_schedule_paused = false
```

## 9. Verify it is locked down

These are the claims the design makes; each has a command. Run them and paste the output into
[cloud-security.md](cloud-security.md#verification).

```bash
cd envs/dev
GATEWAY=$(terraform output -json service_urls | jq -r .gateway)
ASSISTANT=$(terraform output -json service_urls | jq -r .assistant)
PERF=$(terraform output -json service_urls | jq -r .performance)
code() { curl -s -o /dev/null -w '%{http_code}\n' "$@"; }

# 1. Anonymous: only the gateway answers.
code "$GATEWAY/health"        # expect 200
code "$ASSISTANT/health"      # expect 403
code "$PERF/health"           # expect 403

# 2. A valid Google identity that is NOT an allowed invoker is still refused.
code -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$ASSISTANT/health"   # expect 403

# 3. The right identity works. Impersonating a service account needs a temporary grant that
#    Terraform does not manage; remove it straight afterwards.
gcloud iam service-accounts add-iam-policy-binding gateway-sa@PROJECT.iam.gserviceaccount.com \
  --member="user:YOU@example.com" --role=roles/iam.serviceAccountTokenCreator
TOKEN=$(gcloud auth print-identity-token --impersonate-service-account=gateway-sa@PROJECT.iam.gserviceaccount.com --audiences="$ASSISTANT")
code -H "Authorization: Bearer $TOKEN" "$ASSISTANT/health"   # expect 200
code -H "Authorization: Bearer $TOKEN" "$PERF/health"        # expect 403 (the gateway may not skip a hop)
gcloud iam service-accounts remove-iam-policy-binding gateway-sa@PROJECT.iam.gserviceaccount.com \
  --member="user:YOU@example.com" --role=roles/iam.serviceAccountTokenCreator

# 4. No keys exist anywhere.
for sa in $(gcloud iam service-accounts list --format='value(email)'); do
  gcloud iam service-accounts keys list --iam-account="$sa" --managed-by=user --format='value(name)'
done   # expect no output
```

Also open the Cloud Monitoring uptime checks: the two private services should show *passing*
(they pass on 403). If either flips to failing, treat it as a security incident first.

## 10. Day-2 operations

| Task | How |
|---|---|
| Rotate an LLM key or DB password | `gcloud secrets versions add ...`, then `gcloud secrets versions destroy <old> --secret=<id>`. Restart or redeploy the consumer. |
| Add a workload identity or repository | Edit `modules/naming` (names) and `envs/bootstrap` (identities, WIF), add its `NOTES` entry in `scripts/identity-map.py`, apply bootstrap by hand, then `dev`. |
| Add or change a policy exception | `policies/config.rego` only. It is code-owned, and the justification goes next to the entry. |
| Upgrade Terraform, the provider or a scanner | Bump `tools.env` / the constraint, run `bash scripts/check.sh`. The mutation tests notice if a new version changes the plan JSON so that a policy stops matching. |
| See who used which identity | Cloud Audit Logs, Data Access, for `sts.googleapis.com`, `iamcredentials.googleapis.com`, `secretmanager.googleapis.com` (enabled by `envs/bootstrap`). |

## 11. Tear down (and prove nothing billable is left)

```bash
# 1. workloads (data, services, secrets, registry, monitoring)
cd envs/dev && terraform destroy -var-file=terraform.tfvars

# 2. see what is left that could bill (Monitoring > Uptime checks and Alerting should also be empty)
gcloud run services list ; gcloud run jobs list ; gcloud scheduler jobs list --location=us-central1
gcloud artifacts repositories list ; gcloud secrets list ; bq ls
gcloud logging metrics list ; gcloud storage buckets list

# 3. identity plane. This removes the budget too, which is why workloads go first and are
#    verified above: you keep budget protection until nothing billable remains.
cd ../bootstrap && terraform destroy -var-file=terraform.tfvars

# 4. by hand: the state buckets, then (optionally) unlink billing / delete the project
gcloud storage rm --recursive gs://PROJECT-tfstate-dev gs://PROJECT-tfstate-bootstrap
gcloud billing projects unlink PROJECT      # or: gcloud projects delete PROJECT
```

What `destroy` leaves behind, and why that is fine: the **APIs stay enabled** (they cost nothing
unused), and the two **state buckets** are yours to delete (kilobytes, inside the free tier).

Two things that surprise people:

* **Workload identity pools and providers are soft-deleted for 30 days.** You cannot re-create the
  same ID during that window. Undelete it (`gcloud iam workload-identity-pools undelete github
  --location=global`) or change `pool_id` / `provider_id` in `envs/bootstrap`.
* **BigQuery and Artifact Registry delete their contents** because the dev environment sets
  `delete_contents_on_destroy` and the registry has no deletion protection. Anywhere that holds data
  worth keeping must not do that.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `API [x] not enabled` during a `dev` plan or apply | `envs/bootstrap` has not been applied, or a new resource needs an API that is not in `apis.tf`. Add it there and apply bootstrap by hand (the pipeline cannot enable APIs, by design). |
| `403` on a CI plan for one resource type | The plan identity lacks a viewer role for it. Add the product's viewer role to `plan_roles` in `envs/bootstrap/identities.tf`. |
| Budget creation fails with a currency error | `currency_code` must equal the billing account's currency. |
| Budget creation fails with `403` / quota project | Your user needs `serviceusage.services.use` on the project and a Costs Manager role on the billing account. |
| Cloud Run revision fails: *secret version not found* | The placeholder version was disabled or the secret has no version yet. Add one (step 7). |
| Service never becomes ready on the first apply | `use_http_startup_probes` was set to `true` while the placeholder image (no `/health`) was still deployed. Set it back to `false` until a real image ships. |
| The policy gate fails a plan | The message starts with the rule ID (for example `[RUN-001]`); see the table in [cloud-security.md](cloud-security.md#policy-as-code). Fix the change, do not weaken the rule. |
| `terraform init` hangs | The registry is unreachable. Retry, or set `TF_PLUGIN_CACHE_DIR` so the provider is downloaded once. |

## Assumptions to confirm on first apply

Written from documentation, not observed. Each is cheap to check and, if wrong, is a one-line fix:

1. **The plan identity's viewer roles are sufficient** to refresh every resource type (the most likely
   gap; see the troubleshooting row above).
2. **The uptime check for a private service passes on 403.** The `accepted_response_status_codes`
   behaviour is documented; the exact status Cloud Run returns for an anonymous request should be 403.
3. **The gateway writes its decision log as JSON to stdout**, so `jsonPayload.decision` exists for
   the block-rate metric. It currently writes a file; see [app-integration.md](app-integration.md).
4. **Cloud Run accepts the deterministic URL as the ID token audience** (`https://<service>-<number>.<region>.run.app`).
5. **`secret_data_wo` placeholder versions** are created and can be disabled without Terraform complaining.
6. **The conditional `projectIamAdmin` grant** (`modifiedGrantsByRole`) lets the apply identity grant
   `roles/bigquery.jobUser` and nothing else. The syntax is copied from Google's documentation.
7. **The deploy identities' roles are enough for `gcloud run deploy` on an existing service**
   (`run.developer` on the service, `iam.serviceAccountUser` on its runtime account,
   `artifactregistry.writer`). The first deploy names any permission that is missing.
