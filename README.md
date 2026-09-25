# northstar-infra

[![ci](https://github.com/HerschCode/northstar-infra/actions/workflows/ci.yml/badge.svg)](https://github.com/HerschCode/northstar-infra/actions/workflows/ci.yml)

Terraform, policy-as-code and CI for the **Northstar trilogy**: an LLM security gateway in front of an
LLM agent in front of an analytics API, all on Cloud Run, sized for the free tier, and locked down so
that **the internet can reach the gateway and nothing else.**

> **Status: code-complete and verified offline; not yet applied.** It was built before a billing
> account was available, so everything that needs a live project is written but unexercised. What is
> and is not proven is spelled out [below](#what-is-verified-and-what-is-not), and the exact commands
> for the first apply are in [docs/runbook.md](docs/runbook.md).

```mermaid
flowchart LR
  internet([Internet]) -->|"the only public ingress"| gw
  subgraph run["Cloud Run"]
    gw["llm-security-gateway<br/>gateway-sa"]
    as["operations-assistant<br/>assistant-sa"]
    pf["operations-performance<br/>perf-sa"]
    job["pipeline job<br/>pipeline-sa"]
  end
  gw -->|"ID token; invoker = gateway-sa"| as
  as -->|"ID token; invoker = assistant-sa"| pf
  pf --> neon[("Neon Postgres<br/>external")]
  pf --> bq[("BigQuery<br/>dataViewer + jobUser")]
  sched["Cloud Scheduler<br/>scheduler-sa"] --> job
  job --> neon
  job --> bq
  sm[("Secret Manager<br/>one reader per secret")] -.-> as
  sm -.-> pf
  sm -.-> job
  gh["GitHub Actions<br/>workload identity federation, no keys"] -->|"deploy the image only"| run
```

## What is different about it

* **Ten service accounts, no keys, no primitive roles.** One identity per workload, every grant on
  the resource it protects. Between services there are no shared API keys: each call carries a Google
  ID token, and Cloud Run IAM answers **403** to anyone else. The full table of who can do what is
  *generated from the plans*, so it cannot drift from the code:
  [docs/cloud-security.md](docs/cloud-security.md#2-identity-map).
* **The pipeline cannot rewrite its own permissions.** Two root modules: `envs/bootstrap`
  (budget, workload identity trust, CI identities; applied by a person, state in a bucket CI cannot
  reach) and `envs/dev` (the workloads; applied by CI, in a protected GitHub environment).
* **Policy-as-code that is itself tested against real plans.** 21 rules (no Owner/Editor for anyone,
  only the gateway may be public, one reader per secret, no public buckets, no service account keys, no
  always-on paid resources, ...) gate every plan. Beyond unit tests, **20 deliberately bad changes are
  planned with real `terraform plan` and must each be blocked**; that caught two gaps hand-written
  fixtures missed.
* **The "still private" claim is monitored, not just tested.** The uptime check for each private
  service passes *only* on HTTP 403, so it starts failing if one is ever opened to the public.
* **Cost guardrails as code.** A budget with alerts at ₹100 / ₹400 / ₹800 that the pipeline cannot
  edit, `max_instances` ceilings, scale-to-zero, and a rule that fails any plan adding Cloud SQL, a load
  balancer, NAT, a VPC connector or Cloud Armor. See [docs/cost.md](docs/cost.md).
* **Nothing floats.** Tools are pinned and checksum-verified, GitHub Actions are pinned by commit
  SHA, the provider is hash-locked for four platforms, and the CI job list is the same one
  `scripts/check.sh` runs on your laptop.

## What is verified, and what is not

**Verified offline, with no cloud credentials** (all reproducible with `bash scripts/check.sh`):

| | Result |
|---|---|
| `terraform fmt` / `validate` | 10 modules and 2 roots clean |
| Module unit tests (mocked provider) | 95 passing |
| Credential-free plans | `envs/bootstrap` 51 resources, `envs/dev` 55, every IAM attribute known at plan time |
| tflint (with the Google ruleset) | 0 issues |
| checkov | 108 passed, 0 failed; every suppression is justified in place |
| Trivy (IaC + secrets) | 0 findings; suppressions justified in place |
| Policy unit tests | 104 passing |
| Policy mutation tests (real plans) | baseline passes; 20 of 20 bad changes blocked by the named rule |
| Generated docs | module READMEs and the identity map match the code (CI-enforced) |

**Not verified, because it needs a live project:** the apply itself; the GitHub Actions workflows
(their YAML parses and every action is SHA-pinned, but they have not run); the HTTP-403 verification
in [docs/cloud-security.md](docs/cloud-security.md#5-verification); the uptime checks, alerts and log
metric on real traffic; and six specific assumptions listed in
[docs/runbook.md](docs/runbook.md#assumptions-to-confirm-on-first-apply). The application changes
(ID-token calls instead of API keys, JSON logging) are written up in
[docs/app-integration.md](docs/app-integration.md) but not made.

**Deliberately not built:** the optional automatic billing cut-off. It cannot be validated without a
live billing account and it is destructive; the reasons and the permission it would really need are
in [`modules/budget_guard`](modules/budget_guard/README.md).

## Repository map

```text
envs/
  bootstrap/   identity plane, applied by a person: APIs, budget, workload identity, CI identities, audit logs
  dev/         workloads, applied by CI: three Cloud Run services, the pipeline job, secrets, BigQuery, monitoring
modules/       naming  service_account  secret  artifact_registry  cloud_run_service  cloud_run_job
               bigquery_dataset  wif_github  monitoring  budget_guard      (each with tests and a README)
policies/      conftest / OPA rules, their unit tests, and the mutation tests that plan real bad changes
scripts/       offline-plan  policy-mutation-tests  identity-map  install-tools  check  gen-docs
docs/          cloud-security (the IAM write-up)  runbook  cost  app-integration
.github/       ci (no credentials), plan (read-only identity), apply (manual, protected), dependabot, codeowners
```

## Try it (no cloud account needed)

Needs `bash`, `curl`, `unzip` and Python 3 (on Windows, Git Bash).

```bash
bash scripts/install-tools.sh              # pinned Terraform, tflint, conftest, Trivy, terraform-docs, checkov
export PATH="$PWD/.tools/bin:$PATH"        # (the tools are checksum-verified downloads, kept in ./.tools)
bash scripts/check.sh                      # everything CI runs, about three minutes
bash scripts/check.sh quick                # skip the slow mutation tests
```

To see the gate stop a bad change, plan one of the deliberate mistakes and hand it to conftest:

```bash
bash scripts/offline-plan.sh dev plan.json policies/mutations/03-assistant-made-public.override.tfmutation
conftest test plan.json -p policies        # FAIL ... [RUN-001] ... lets anyone on the internet invoke "operations-assistant"
```

## Documentation

| | |
|---|---|
| [docs/cloud-security.md](docs/cloud-security.md) | Threat model, identity map, removed secrets, policy rules, verification, defence in depth with the gateway's LLM firewall, residual risks |
| [docs/runbook.md](docs/runbook.md) | Create the project, apply in order (budget first), verify the 403s, tear down and prove nothing billable is left |
| [docs/cost.md](docs/cost.md) | What is free, what this actually uses, what is deliberately avoided (prices read on 2026-09-24) |
| [docs/app-integration.md](docs/app-integration.md) | What each app repository must change, plus a deploy workflow with a Trivy gate |

## The applications

[`operations-performance`](https://github.com/HerschCode/operations-performance) (analytics API, the data
layer) → [`operations-assistant`](https://github.com/HerschCode/operations-assistant) (LLM agent that uses it as
tools) → [`llm-security-gateway`](https://github.com/HerschCode/llm-security-gateway) (the firewall in front).
Each links back here for how it is deployed and secured.
