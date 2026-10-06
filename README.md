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

## Run it
```bash
cp .env.example .env        # DB_* for your Neon project, GEMINI_API_KEY / GROQ_API_KEY (free tiers), never committed
docker compose up --build   # console :5173, gateway :8002; P1/P2 stay private inside the network
make test                   # console unit tests + gateway suite
```

## Migration status
History of all five repositories is imported (`git log` shows every original commit). The Google ID-token auth branches
(gateway, assistant) and the manual-deploy workflow (performance) are merged. All 15 GitHub Actions workflows now live in the root
`.github/workflows/` as `<service>-<name>.yml`, with `paths:` filters and `working-directory` set per service.
Gateway suite: 1049 passed. Console: type-check clean, 26 unit tests pass. Not yet run here: the other services' suites, Playwright, `docker compose up`
(no Docker engine in the build environment), and the workflows on GitHub (they run once this branch is on `main` or a PR is open).
`apps/console` remote spec checks still read the old repositories' `main` until those are archived.
