# Tour

One page. Then read the [identity map](cloud-security.md#2-identity-map).

**The system.** The internet reaches one Cloud Run service, the gateway. It calls the assistant, which
calls the analytics API. Every hop carries a Google ID token and Cloud Run IAM answers 403 to anyone
else. Ten service accounts, no keys, no primitive roles.

## The five rules that matter most

| Rule | The attack it prevents |
|---|---|
| `IAM-001` no Owner, Editor or Viewer, for anyone | A stolen CI or workload token becoming a project takeover |
| `RUN-001` only the gateway may allow `allUsers` | Reaching the agent or the data API around the gateway's LLM firewall (prompt injection, cost abuse) |
| `SEC-001` no secret has more than one reader | A compromised gateway reading the LLM key or the database password |
| `WIF-001` / `WIF-002` GitHub tokens are accepted only from named repositories, and every grant is scoped to one (the module pins it further, to `main` or a protected environment) | Someone else's repository, a fork or, by that further pin, a feature branch minting tokens as the deploy or apply identity |
| `KEY-001` no service-account keys | A long-lived credential leaking from a laptop, repository or log, and working forever |

## Three design choices

**Two root modules.** `bootstrap` (budget, workload-identity trust, CI identities) is applied by a person,
with state CI cannot read; `dev` (the workloads) is applied by CI. A pipeline that can edit its own
permissions can be talked into granting itself Owner, so what CI applies must not include what decides
what CI may do. Its one IAM-admin grant is conditional: `bigquery.jobUser` and nothing else.

**ID tokens over API keys.** A key is a shared secret: it does not say who is calling, is rotated by hand,
and in these apps fails open when unset. An ID token names the caller, is
minted for one audience (the assistant's is useless against the analytics API) and lives about an hour.
Google checks it before your code runs, so anonymous calls cost nothing, and who-may-call-whom is IAM
that a plan can be tested for.

**No VPC, no Cloud Armor.** Both need always-on paid pieces (NAT or a connector, a load balancer;
`COST-001` blocks them) and neither fits the threat model: private services are protected by identity,
not network location, and the one public surface is protected by the gateway's LLM firewall, which is
not a WAF's job. Given up: volumetric-DDoS protection (`max_instances` and the budget cap the damage)
and IP allow-listing.

## Three questions to expect

1. **"The gateway is compromised. What can the attacker do?"** Call the assistant, which it could already
   do. Not the analytics API (a hop cannot be skipped), no secret, no data, no other identity.
2. **"A malicious pull request: what stops it becoming Owner?"** The plan is policy-gated before anything
   applies (`IAM-001`, `IAM-004`). The apply identity is only issued to jobs in the protected `dev-apply`
   environment (main only, required reviewer, both set in GitHub) and has no access to `bootstrap`'s state,
   trust or budget. `policies/` and the workflows are CODEOWNERS-protected. Residual: a compromised apply
   job can create service accounts, a foothold.
3. **"How do you know the policies work, and what is not proven?"** 104 unit tests plus 20 mutation tests:
   real bad changes planned with real `terraform plan`, each blocked by the named rule (they found two gaps
   that hand-written fixtures missed). Not proven: it has never been applied, so the 403s, the uptime
   checks and seven assumptions wait for a live project ([runbook](runbook.md)).
