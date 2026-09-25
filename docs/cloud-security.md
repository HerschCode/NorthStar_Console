# Cloud security: how the trilogy is locked down

The three services (a security gateway in front of an LLM agent in front of an analytics API) run on
Cloud Run. This document explains what an attacker can reach, which identity can do what, which
secrets were removed, how the rules are enforced by code, how to prove the claims, how this layer
fits with the gateway's own LLM firewall, and what is *not* protected.

> **What is proven and what is not.** The design, the Terraform and the policies are complete and
> verified **offline**: every module has unit tests, both root modules plan without credentials, the
> policy gate passes on those plans, and 20 deliberately bad changes are each blocked (see
> [Policy-as-code](#policy-as-code)). **Nothing has been applied to a live project yet**, so the
> [Verification](#verification) commands are written but their output is still to be captured.
> [runbook.md](runbook.md#assumptions-to-confirm-on-first-apply) lists the seven assumptions a live
> apply would confirm.

## 1. Threat model

What is being protected, from whom, and what stops each.

| Asset | Why it matters |
|---|---|
| LLM API keys | They spend money and can be abused. |
| The database and BigQuery data | The analytics the whole demo serves. |
| The ability to deploy code | Whoever can roll out a revision runs code with that service's identity. |
| The billing account | A runaway or malicious workload costs real money. |
| The pipeline's own permissions | If CI can grant itself anything, every other control is moot. |

| Adversary | What they can reach | What stops them |
|---|---|---|
| **Anyone on the internet** | **Only the gateway's URL.** The assistant and the performance API are also on public `*.run.app` hostnames, but answer HTTP 403 to any caller without a valid token from an allowed identity. | Cloud Run IAM invoker check; policy rules `RUN-001`, `RUN-002`; a continuous uptime probe that fails if a private service ever stops returning 403. |
| **Prompt injection that beats the gateway** | The assistant, as `assistant-sa`. | That identity can call one API and read one secret. It cannot reach the database, BigQuery, other secrets or other services. The blast radius of a subverted model is deliberately small. |
| **A compromised app repository or dependency** | Deploy a revision of *that repository's* service, from `main` only. | Per-app deploy identities pinned to repository and branch, holding only `run.developer` on one service and push access to one registry. They cannot change env vars, secrets, IAM or scaling. Trivy gates the deploy. |
| **A malicious or mistaken infrastructure change** | Whatever the merged change grants. | Every plan is checked by 20+ policy rules before a human can apply it; module-level guards fire earlier still; `CODEOWNERS` covers the policies, identities and pipelines. |
| **A compromised infrastructure pipeline** | The apply identity's permissions, and only inside a protected GitHub environment. | It cannot change the budget, the workload identity pool, its own roles, or API enablement (all in a human-only root), cannot grant any project role but one, and its use is audit-logged. The read-only plan identity holds no write role at all. |
| **A leaked credential** | Nothing long-lived exists to leak: no service account keys, no static API keys between services. Tokens live about an hour. | Workload identity federation for CI, attached identities for workloads, Secret Manager for the rest; `KEY-001` forbids creating keys. |
| **Cost abuse** | A flood on the public gateway. | `max_instances` ceilings, scale-to-zero, a budget with alerts, and `COST-001` blocking always-on resource types. (No WAF or edge rate limit: see [residual risks](#8-residual-risks-and-what-fixing-them-would-cost).) |

## 2. Identity map

Ten service accounts in two **planes**, applied separately so the pipeline cannot rewrite its own
permissions:

* **The identity plane, `envs/bootstrap`,** is applied only by a person. It owns API enablement, the
  budget, the workload identity pool and the five GitHub identities, and it keeps its state in a
  bucket the pipeline cannot reach.
* **The workload plane, `envs/dev`,** holds the services, secrets, data and monitoring, and can be
  applied by CI.

The tables below are **generated from the Terraform plans** by `scripts/identity-map.py`, and CI
fails if they differ from this file, so they are the actual grants and not a description of
intent. Only the last two columns of the first table (why an identity exists, what it cannot do)
are hand-written.

<!-- BEGIN_IDENTITY_MAP -->
### Every service account

| Identity | Plane | Who can use it | What it can do | Why it exists | What it cannot do |
|---|---|---|---|---|---|
| `gateway-sa` | workload (`envs/dev`) | Cloud Run service `llm-security-gateway`<br>*may be attached by:* `gh-deploy-gateway`, `gh-tf-apply` | `roles/run.invoker` on Cloud Run service `operations-assistant` | Runtime identity of `llm-security-gateway`, the only public service. Its job is to forward screened requests to the assistant with a Google ID token. | Call `operations-performance`, read any secret or table, or act as another service account. Holds no project-level role. |
| `assistant-sa` | workload (`envs/dev`) | Cloud Run service `operations-assistant`<br>*may be attached by:* `gh-deploy-assistant`, `gh-tf-apply` | `roles/run.invoker` on Cloud Run service `operations-performance`<br>`roles/secretmanager.secretAccessor` on secret `groq-api-key` | Runtime identity of `operations-assistant`. Calls the performance API as the agent's tool and reads its own LLM API key. | Reach the database or BigQuery directly, read any other secret, or be invoked by anything except the gateway. |
| `perf-sa` | workload (`envs/dev`) | Cloud Run service `operations-performance`<br>*may be attached by:* `gh-deploy-perf`, `gh-tf-apply` | `roles/bigquery.jobUser` on the project<br>`roles/bigquery.dataViewer` on BigQuery dataset `operations_performance_analytics`<br>`roles/secretmanager.secretAccessor` on secret `neon-api-reader-password` | Runtime identity of `operations-performance`. Reads its read-only database password and queries the analytics dataset. | Write to BigQuery, read the pipeline's write-capable password, or be invoked by anything except the assistant. |
| `pipeline-sa` | workload (`envs/dev`) | Cloud Run job `operations-pipeline`<br>*may be attached by:* `gh-deploy-perf`, `gh-tf-apply` | `roles/bigquery.jobUser` on the project<br>`roles/bigquery.dataEditor` on BigQuery dataset `operations_performance_analytics`<br>`roles/bigquery.dataEditor` on BigQuery dataset `operations_performance_staging`<br>`roles/secretmanager.secretAccessor` on secret `neon-pipeline-writer-password` | Runtime identity of the pipeline job. Loads Postgres and BigQuery on a schedule. | Be called from the internet, read the API's password, or invoke any service. |
| `scheduler-sa` | workload (`envs/dev`) | Cloud Scheduler job `operations-pipeline-trigger`<br>*may be attached by:* `gh-tf-apply` | `roles/run.invoker` on Cloud Run job `operations-pipeline` | Lets Cloud Scheduler start the pipeline job. | Do anything other than run that one job. |
| `gh-deploy-gateway` | pipeline (`envs/bootstrap`) | GitHub Actions in `HerschCode/llm-security-gateway` on `refs/heads/main` | `roles/artifactregistry.writer` on Artifact Registry repository `northstar`<br>`roles/run.developer` on Cloud Run service `llm-security-gateway` | Assumed by the gateway repository's deploy workflow, on `main` only, to push an image and roll out a revision. | Change env vars, secrets, IAM, scaling or any other service; deploy from another branch or repository. |
| `gh-deploy-assistant` | pipeline (`envs/bootstrap`) | GitHub Actions in `HerschCode/operations-assistant` on `refs/heads/main` | `roles/artifactregistry.writer` on Artifact Registry repository `northstar`<br>`roles/run.developer` on Cloud Run service `operations-assistant` | Assumed by the assistant repository's deploy workflow, on `main` only, to push an image and roll out a revision. | Change env vars, secrets, IAM, scaling or any other service; deploy from another branch or repository. |
| `gh-deploy-perf` | pipeline (`envs/bootstrap`) | GitHub Actions in `HerschCode/operations-performance` on `refs/heads/main` | `roles/artifactregistry.writer` on Artifact Registry repository `northstar`<br>`roles/run.developer` on Cloud Run job `operations-pipeline`<br>`roles/run.developer` on Cloud Run service `operations-performance` | Assumed by the performance repository's deploy workflow, on `main` only, to push an image and roll out a revision of the API and of the pipeline job. | Change env vars, secrets, IAM, scaling or any other service; deploy from another branch or repository. |
| `gh-tf-plan` | pipeline (`envs/bootstrap`) | GitHub Actions in `HerschCode/northstar-infra` (any ref) | `roles/artifactregistry.reader` on the project<br>`roles/bigquery.metadataViewer` on the project<br>`roles/cloudscheduler.viewer` on the project<br>`roles/iam.securityReviewer` on the project<br>`roles/logging.viewer` on the project<br>`roles/monitoring.viewer` on the project<br>`roles/run.viewer` on the project<br>`roles/secretmanager.viewer` on the project<br>`roles/serviceusage.serviceUsageConsumer` on the project<br>`roles/storage.objectViewer` on the dev Terraform state bucket | Read-only identity for pull-request plans of `envs/dev`. | Write anything, read a secret's value or table data, or reach the bootstrap state. |
| `gh-tf-apply` | pipeline (`envs/bootstrap`) | GitHub Actions in `HerschCode/northstar-infra`, environment `dev-apply` | `roles/artifactregistry.admin` on the project<br>`roles/bigquery.admin` on the project<br>`roles/cloudscheduler.admin` on the project<br>`roles/iam.serviceAccountAdmin` on the project<br>`roles/logging.configWriter` on the project<br>`roles/monitoring.editor` on the project<br>`roles/resourcemanager.projectIamAdmin` on the project *(conditional)*<br>`roles/run.admin` on the project<br>`roles/secretmanager.admin` on the project<br>`roles/serviceusage.serviceUsageConsumer` on the project<br>`roles/storage.objectUser` on the dev Terraform state bucket | Applies `envs/dev`. The most powerful identity in the stack. | Touch the budget, the workload identity pool or its own roles (all in the human-only bootstrap root), enable APIs, grant any project role except `roles/bigquery.jobUser`, or be assumed outside the protected GitHub environment. |

### Who can call what

| Target | Invokers | Note |
|---|---|---|
| Cloud Run job `operations-pipeline` | `scheduler-sa` | Anonymous callers receive HTTP 403. |
| Cloud Run service `llm-security-gateway` | **anyone on the internet** (`allUsers`) | The only public entry point. |
| Cloud Run service `operations-assistant` | `gateway-sa` | Anonymous callers receive HTTP 403. |
| Cloud Run service `operations-performance` | `assistant-sa` | Anonymous callers receive HTTP 403. |

### Who can read which secret

| Secret | The one identity that can read it |
|---|---|
| `groq-api-key` | `assistant-sa` |
| `neon-api-reader-password` | `perf-sa` |
| `neon-pipeline-writer-password` | `pipeline-sa` |
<!-- END_IDENTITY_MAP -->

Design rules the map follows:

1. **One identity per workload,** so compromising one grants nothing that belongs to another.
2. **Grants sit on the resource they protect,** never on the project. The only project-level roles
   for workloads are `bigquery.jobUser` (Google defines job creation at project scope); for the
   pipeline they are the roles Terraform needs, discussed next.
3. **Authoritative bindings.** Invokers, secret readers, dataset access, registry writers and
   impersonation grants are `*_iam_binding`s, so anything added out of band (console, `gcloud`) is
   drift and is removed on the next apply.
4. **No primitive roles and no keys, anywhere.** Enforced by module variable validation *and* by
   policy, independently.
5. **The apply identity is the risk to be honest about.** It holds `secretmanager.admin`, which can
   read secret payloads: whoever can set a secret's IAM policy can grant themselves access, so no
   narrower role would change that. The compensating controls are the protected environment,
   Data Access audit logs on Secret Manager, the fact that it cannot touch the identity plane, and
   that project IAM edits are limited to one role by a `modifiedGrantsByRole` condition that policy
   rule `IAM-004` refuses to let anyone remove.

## 3. Removed secrets: before and after

"Before" is taken from the applications' own `render.yaml`, `.env.example` and docs.

| Before | After |
|---|---|
| A shared static `API_KEY` on `operations-performance`, and the same value copied into the assistant as `OPS_PERFORMANCE_API_KEY` (`sync: false` dashboard variables) | Each call carries a Google **ID token** minted for the caller's service account. Cloud Run IAM verifies it. No shared secret exists. |
| `OPS_ASSISTANT_API_KEY`, or the gateway falling back to the assistant's **public, keyless** `/demo/chat` | The gateway's ID token, on the authenticated `/chat` route. The assistant is not reachable anonymously, so there is no public keyless endpoint left to fall back to. |
| `GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json` for the BigQuery migration | Application Default Credentials from the attached service account. **No key file exists**; `KEY-001` fails any plan that creates one. |
| LLM keys (`GROQ_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`) pasted into a hosting dashboard | Secret Manager, one secret per key, **readable only by the assistant**, injected at runtime, and added with `gcloud` so the value never enters Terraform state. |
| Database passwords as dashboard variables, one credential set for everything | Two secrets for two roles: the API gets only the **read-only** password, the pipeline only the writer's, each readable by a single identity. |
| Deploys from a Git-connected host or a developer's own credentials | GitHub Actions through **workload identity federation**: no stored key, pinned to a repository and branch, with a Trivy gate. |

The "after" column for the app-to-app calls depends on the app-side changes in
[app-integration.md](app-integration.md). The ID-token calls are written and unit-tested behind
`AUTH_MODE=google_id_token` (on the branches linked there) but have not run against Cloud Run, and the
`GOOGLE_APPLICATION_CREDENTIALS` cleanup in operations-performance's docs is not done.

## 4. Policy-as-code

Every plan is turned into JSON and checked by [conftest](https://www.conftest.dev/) rules in
`policies/` before anything can be applied. Failures name the rule:

| Rule | Fails the plan if... |
|---|---|
| `IAM-001` | any principal is granted `roles/owner`, `roles/editor` or `roles/viewer` (a superset of "no service account gets Owner or Editor") |
| `IAM-002` | an impersonation-capable role (`serviceAccountUser`, `serviceAccountTokenCreator`, `serviceAccountKeyAdmin`, `workloadIdentityUser`) is granted on the whole project |
| `IAM-003` | anything is granted to `allAuthenticatedUsers` (every Google account) |
| `IAM-004` | an IAM-admin role (for example `projectIamAdmin`) lacks a `modifiedGrantsByRole` condition |
| `IAM-005` | a whole-policy resource (`*_iam_policy`) or a project-wide authoritative binding is used |
| `IAM-006` | folder, organization or billing-account IAM is managed (out of scope for the pipeline) |
| `PUB-001` | `allUsers` is granted on anything that is not a Cloud Run service, function or bucket (which have their own rules) |
| `RUN-001` | any Cloud Run service, job or function **other than the gateway** allows `allUsers`, or the gateway grants it anything but `roles/run.invoker` |
| `RUN-002` | a service sets `invoker_iam_disabled = true` |
| `RUN-003` | a service or job would run as the default compute service account (checked against the *configuration*, because a missing `service_account` looks identical to an unknown one in the plan) |
| `SEC-001` | a secret is readable by **more than one** principal |
| `SEC-002` | a secret-reading role is granted on the whole project (one reviewed exception, for the apply identity, in `policies/config.rego`) |
| `SEC-003` | secret IAM names a secret that is not known at plan time |
| `GCS-001` | a bucket is public, has an ACL for the public, or lacks public-access prevention or uniform access |
| `BQ-001` | a dataset is open to the public or to every Google account |
| `WIF-001` | a GitHub provider does not restrict which repositories may authenticate |
| `WIF-002` | a `workloadIdentityUser` grant is not scoped to a repository (no pool-wide wildcards) |
| `KEY-001` | a service account key is created |
| `COST-001` | a resource type that bills around the clock is added (Cloud SQL, VMs, load balancers, NAT, VPC connectors, Cloud Armor, static IPs, GKE, Memorystore) |
| `COST-002` | *(warning)* a service keeps instances warm |
| `UNK-001` | a grant's role or principal is "known after apply", so it cannot be checked: the gate **fails closed** |

The first four requirements of the original brief map to `IAM-001`, `RUN-001`, `SEC-001` and
`GCS-001`. The rest were added because they are the ways those four get bypassed.

### It runs on the change, before the apply

The gate runs in CI on every pull request from a **credential-free plan** (computed against an empty
state), so it can check code from anyone, including forks. A second, credentialed plan runs for
branches of this repository and is gated the same way, and the apply job runs the gate again on the
exact plan it is about to apply. A blocked change looks like this (real output, path shortened):

```text
$ conftest test plan.json -p policies
FAIL - plan.json - main - [IAM-001] google_project_iam_member.mutation grants the primitive role
roles/owner to serviceAccount:gh-tf-apply@northstar-offline.iam.gserviceaccount.com. Primitive
roles are forbidden for every principal: grant a predefined or custom role on the narrowest
resource instead.

26 tests, 25 passed, 0 warnings, 1 failure, 0 exceptions
```

```text
$ conftest test plan.json -p policies
FAIL - plan.json - main - [RUN-001] module.run_assistant.google_cloud_run_v2_service_iam_binding.invoker
lets anyone on the internet invoke "operations-assistant". Only llm-security-gateway may be public
(and only through roles/run.invoker); every other service, job and function must require a token.

26 tests, 25 passed, 0 warnings, 1 failure, 0 exceptions
```

> The second case is instructive: the change sets *both* keys the `cloud_run_service` module demands
> for a public service, so the module's own guard is satisfied and only the policy layer stops it.
> With only one key set, the module refuses first (`policies/mutations/04-...`).
>
> *Add a screenshot of a real pull request being blocked here once the repository is on GitHub
> (`docs/img/blocked-plan.png`); the text above is what it will show.*

### How I know the rules work

* **104 unit tests** in `policies/tests/` exercise each rule firing and not firing, including
  unknown values, deleted resources and alternative resource types.
* **20 mutation tests** (`policies/mutations/`) plan real, deliberately bad Terraform ("give the
  pipeline Editor", "open the assistant to the internet", "add a second reader to a secret",
  "create a public bucket", ...) with real `terraform plan`, and require the gate to block each with
  the named rule. They run in CI, and again after any Terraform, provider or policy-engine upgrade.

The mutation tests earned their keep. Hand-written fixtures passed while **two real gaps** existed,
found only when real plans were used: a Cloud Run service with no `service_account` shows the
account as *unknown* in the plan (the provider fills in the default), so it looked identical to a
legitimately computed reference and slipped through `RUN-003`; and a grant built from a service
account's `member` attribute turned out to be known at plan time, so my "unverifiable grant" test
was not testing what I thought. Both are fixed, and the first is why `RUN-003` reads the plan's
`configuration` section.

### Defence in depth inside the pipeline

```text
module input validation  ->  module preconditions  ->  conftest on the plan  ->  authoritative bindings
 (no primitive roles,         (public needs two         (the rules above)         (out-of-band grants are
  no default SA)               keys)                                               drift and get removed)
```

A mistake has to get past all four, and the last one keeps working after the apply: someone adding an
invoker in the console is undone by the next plan/apply, and shows up as a diff on any pull request
in the meantime.

## 5. Verification

The claims, each with the command that proves it, live in
[runbook.md](runbook.md#9-verify-it-is-locked-down). Expected results:

| Check | Expected |
|---|---|
| Anonymous call to the gateway | `200` |
| Anonymous call to the assistant or performance API | **`403`** |
| A valid Google identity token that is *not* an allowed invoker, to the assistant | **`403`** |
| The gateway's token to the assistant | `200` |
| The gateway's token to the performance API (skipping a hop) | **`403`** |
| Service account keys that exist in the project | none |

**Captured output: pending the first live apply.** Paste it here when it exists.

The 403 claim is also monitored continuously, not just tested once: the uptime check for each private
service passes *only* on HTTP 403, so it flips to failing if a private service ever becomes reachable.

## 6. Defence in depth with the gateway's LLM action firewall

These two layers guard different things, and each covers the other's blind spot.

| | Cloud identity layer (this repository) | The gateway's LLM firewall (P3) |
|---|---|---|
| Question it answers | *Who* may call *what*? | *What* is being said, and what may the model do? |
| Operates on | Identities and network reachability | Content: prompts, responses, tool actions |
| Stops | Bypassing the gateway (calling the assistant directly), stolen static keys, a compromised service reaching data it should not, unauthorized deploys | Prompt injection, jailbreaks, PII and secret leakage, dangerous tool calls, an authorized caller sending a hostile prompt |
| Cannot see | Whether an *authorized* call carries an injection | Who is calling, or whether a request came around it |

* **IAM makes the gateway unavoidable.** The firewall is only a control if it cannot be skipped.
  Because the assistant's only permitted invoker is the gateway's service account, there is no route
  to the agent that does not pass through it.
* **The gateway makes IAM sufficient.** A perfectly authenticated request can still be a prompt
  injection. Content inspection is the only thing that sees it.
* **They bound each other's failure.** If an injection beats the firewall, the subverted agent still
  runs as `assistant-sa`: it can call one API and read one secret. If the identity layer were
  misconfigured, the firewall is still inspecting every request that arrives. A compromised *model*
  is not a compromised *project*.
* **Their logs correlate.** The gateway's decision log (who asked, allow or block, which layer, which
  pattern) records the application view; Cloud Audit Logs record the identity view (token
  exchanges, impersonation, secret reads). An incident needs both.

## 7. Detective controls

Prevention is not enough. What would tell you something went wrong:

* **Budget alerts** at â‚¹100 / â‚¹400 / â‚¹800 and on forecast, from a budget the pipeline cannot edit.
* **Uptime check per service**, including the "still private" canary above.
* **5xx alert** per service, and a **gateway block-rate alert** driven by the decision log.
* **Data Access audit logs** for the identity plane: who exchanged a GitHub token, who impersonated
  which service account, who read or changed which secret (`envs/bootstrap/audit.tf`).
* **Drift.** Authoritative IAM bindings mean out-of-band changes appear in every plan.

## 8. Residual risks, and what fixing them would cost

Deliberately not done. Each is a real gap, with the reason and the price of closing it. The prices
in the last column are planning estimates (the brief's own: roughly $7-20+ a month for each of the
paid networking items); check current pricing before deciding.

| Residual risk | Why it is accepted here | What closing it takes |
|---|---|---|
| **No WAF, no edge rate limiting on the public gateway.** A flood is bounded only by `max_instances` and the budget. | Cloud Armor needs an external load balancer in front of Cloud Run; both bill continuously. | Global external Application Load Balancer + Cloud Armor policy: a fixed monthly cost well outside the free tier. |
| **The private services are reachable at the network layer** (ingress is `ALL`); only IAM stops a call. A bug in the IAM configuration would expose them. | `INTERNAL_ONLY` ingress makes calls between Cloud Run services fail unless traffic goes through a VPC, which needs Direct VPC egress plus Cloud NAT to keep internet access (Neon, LLM APIs). | A VPC, NAT and DNS work: NAT alone is a continuous charge. Mitigated meanwhile by policy `RUN-001`/`RUN-002` and the 403 canary. |
| **No VPC Service Controls.** No perimeter stops data leaving through an authorized identity. | Perimeters are configured at the organization level through Access Context Manager, so a project that is not inside a Google Cloud organization cannot use them. | An organization (Cloud Identity), perimeter design and ongoing dry-run tuning. Effort more than money. |
| **No egress filtering.** A subverted workload can call any internet host. | Needs the same VPC and NAT as above, plus firewall policy. | See above. |
| **The apply identity is powerful** (`secretmanager.admin` can read payloads; `run.admin`, `bigquery.admin`). Through `serviceAccountAdmin` a *compromised* apply job could also add another impersonator to a service account as a foothold, though only for repositories already allowed by the pool. | The narrower roles do not exist for what Terraform must do; see the identity-map notes. The foothold is reverted (and shows as a diff) the next time the authoritative bindings in `envs/bootstrap` are applied, so run a bootstrap `plan` regularly. | Custom roles per product (a maintenance burden that fails applies when a permission is missed), or moving secret and service-account IAM into the human-only plane. |
| **A pull-request plan runs the PR's Terraform with read-only credentials.** A collaborator could read what those credentials can read. | Standard for plan-on-PR; the identity is read-only, fork PRs get no token, and the state holds no secrets by design. | Required review before plans run, or plans only after approval. |
| **No customer-managed encryption keys.** | Nothing sensitive is stored; a disabled key would make data unreadable. Not a cost reason (KMS has 100 free key versions). | KMS keys, key IAM, and a key-availability runbook. |
| **Service account key creation is blocked by policy, not by Google.** A human can still run `gcloud ... keys create`. | Org policies (`iam.disableServiceAccountKeyCreation`) need an organization. | Put the project in an organization and enforce the constraint. |
| **Supply chain:** Actions are pinned by commit SHA and tools by checksum, but the Terraform provider and container base images come from public registries. | Provider hashes are locked; Trivy scans images. | Mirroring, SLSA provenance and image signing (Binary Authorization). |
| **Logging is not tamper-proof.** Audit logs live in the project. | Fine for a demo. | A log sink to a separate, locked-down project. |

## 9. What changed relative to the original design, and why

* **Two root modules instead of one**, so the pipeline cannot edit the budget, the trust
  configuration or its own permissions.
* **Uptime checks for private services expect 403** instead of authenticating, which both proves the
  services are private and avoids granting an extra invoker to Google's monitoring agent.
* **The optional billing kill-switch is not built.** It needs a Cloud Functions deployment whose
  service-account and trigger permissions cannot be verified without a live billing account, and it
  is destructive. The integration point (`pubsub_topic_id`) exists, and the permission it really needs
  (`roles/billing.projectManager` on the project, not the billing-account admin role in Google's sample)
  is recorded in `modules/budget_guard/README.md`.
