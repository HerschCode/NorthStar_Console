# Cost

**Expected bill at portfolio traffic: ₹0, and in a bad month a few tens of rupees.** The stack is
built to sit inside the Google Cloud free tier, and the guardrails below exist because the realistic
way it stops doing so is one forgotten always-on resource.

Prices and allowances below were read from Google's pricing pages on **24 September 2026**.
Verify them again before relying on them; free tiers change.

## What is free, and what this stack actually uses

| Item | Free allowance (per month) | This stack's expected use | Cost |
|---|---|---|---|
| **Cloud Run** (request-based billing) | 180,000 vCPU-seconds, 360,000 GiB-seconds, 2 million requests | A few thousand real requests, plus ~52,000 uptime probes on the gateway (see below); scale to zero, `max_instances = 2` | free |
| Cloud Run network | 1 GiB egress within North America; same-region service-to-service traffic is free | small JSON responses | free |
| **Artifact Registry** | 0.5 GiB storage, then about $0.10 per GiB-month | 3 images. Python images with numpy / onnx / mlflow can be ~1 GB each; the cleanup policy keeps the newest 3 versions of each | **the one line that can cost money**: roughly $0.25–0.90 a month if images are large. Unknown until real images exist |
| **Secret Manager** | 6 active versions, 10,000 access operations | 3 secrets × (placeholder + real value) = 6 active versions | free at exactly the limit; each extra live version is about $0.06 a month. Destroy old versions after rotating |
| **BigQuery** | 10 GiB storage, 1 TiB of queries | a few MB of analytics tables | free |
| **Cloud Scheduler** | 3 jobs per billing account | 1 job | free |
| **Monitoring: uptime checks** | 1,000,000 executions per project | 3 checks × 6 probe locations × 12 per hour ≈ 155,000 | free |
| **Monitoring: alerting policies** | none, but see below | 3 policies, about 3 metric references | free until the date below |
| **Logging** | 50 GiB of log data per project, default retention free | structured request logs and the gateway's decision log | free |
| **Cloud Storage** (the two state buckets) | 5 GiB regional storage, only in `us-central1`, `us-east1`, `us-west1` | kilobytes | free |
| **Billing budgets, IAM, Workload Identity Federation, Cloud Audit Logs (admin)** | not metered | | free |

### Two facts worth knowing

* **The free tier is a spending-based discount computed at Tier 1 prices.** It is applied to the
  billing account, not the project, and a region with higher prices (Mumbai, for example) burns the
  same dollar allowance faster. That is why the default region is `us-central1`, even though the
  author is in India: the latency cost is irrelevant for a demo and the free tier goes furthest there.
* **Alerting-policy charges are scheduled.** Google's Monitoring pricing page lists $0.35 per month
  for each metric reference in an alerting policy, with an effective date of **1 September 2027**.
  The three policies here have about three references, so roughly a dollar a month from then.

### The uptime probes are not free of consequence

The gateway is public, so each probe is a real request that wakes (or keeps warm) an instance:
about 52,000 a month, a few hundred vCPU-seconds, far inside the free tier. The two private
services are probed for HTTP 403, which Cloud Run rejects at the front end before any container
runs, so they cost nothing and, importantly, never wake the container or the Neon database behind
it. (Neon's free compute hours are a separate limit that a deep `/health` probe every five
minutes would exhaust.)

## What is deliberately avoided

Each of these bills around the clock whether or not it is used, and your own estimate is roughly
$7–20+ a month apiece:

| Avoided | Why it is tempting | What is done instead |
|---|---|---|
| Cloud SQL | "a real database" | Neon Postgres (external, free tier) |
| Load balancer + Cloud Armor | WAF and rate limiting in front of the gateway | Cloud Run's own URL; `max_instances` as the cost ceiling; see the residual-risks section of [cloud-security.md](cloud-security.md) |
| Cloud NAT, Serverless VPC Access connector | "internal-only" networking between services | IAM invoker checks (403 for anyone without a token) |
| GPUs, always-on instances | faster cold starts | scale to zero, startup CPU boost |
| Cloud KMS / CMEK | customer-managed keys | Google-managed encryption (see the note in `.checkov.yaml`) |

They are not just avoided by convention: conftest rule **`COST-001`** fails a plan that contains one,
and **`COST-002`** warns on a service that keeps instances warm. Adding one on purpose means editing
`policies/config.rego`, which is code-owned.

## The budget guard

Alerts fire at **₹100, ₹400 and ₹800** of a ₹800 monthly budget (12.5%, 50%, 100%), plus a forecast
alert if the month is projected to reach 100%. The budget watches **gross usage** (credits are not
subtracted), so a paid resource left running is noticed even while free-trial credits absorb the
charge.

A budget **notifies; it does not stop spending.** Billing data also lags real usage by hours. So the
protection is layered: the budget email, the `max_instances` ceilings, scale-to-zero, the policy that
forbids always-on resource types, and the tested teardown in [runbook.md](runbook.md). An automatic
cut-off (a function that unlinks billing when the budget is exceeded) is designed but not built; see
`modules/budget_guard/README.md` for why and for the permission it would actually need.

## Outside this budget

LLM API usage (Groq, Gemini, Anthropic) is billed by those providers, not by Google Cloud, so the
budget above cannot see it. The default provider (Groq) has a free tier; set spend limits in the
provider's console if you switch.
