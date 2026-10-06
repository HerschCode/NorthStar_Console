# Northstar

One repository for the whole Northstar trilogy and its cloud foundation: a console UI over an analytics service, an evidence-grounded
assistant, and the LLM security gateway that is the only path from the UI to AI and to actions. History of all five original
repositories is preserved (`git log --follow` works across the move).

| Path | Was | What it is |
|---|---|---|
| `apps/console` | `NorthStar_Console` | React/Vite UI: monitor → investigate → prioritise → act → measure |
| `services/performance` | `operations-performance` (P1) | Process mining, SLA-risk models, dbt, Prefect, AP controls, ROI ledger |
| `services/assistant` | `operations-assistant` (P2) | Hybrid-RAG copilot, agents, answer gate, investigations |
| `services/gateway` | `llm-security-gateway` (P3) | Text guard, action firewall, PII, red-team harness |
| `infra` | `northstar-infra` | Terraform, 21 policy-as-code rules, budget guard (written, not applied) |

* **All features:** [docs/FEATURES.md](docs/FEATURES.md)
* **Status and next steps:** [docs/ROADMAP.md](docs/ROADMAP.md)
* Each directory keeps its own README, tests and lockfiles; the services remain separate deployables on purpose.

## Migration status
Imported with `git merge -s ours` + `read-tree --prefix` (history kept). **GitHub Actions workflows still sit in their old nested
`.github/` folders and do not run from there** until hoisted to the repository root: see the roadmap, step 1. Remote spec checks in
`apps/console` still read the old repositories' `main` until those are archived.
