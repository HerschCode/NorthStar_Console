# Project Overview

Operations Performance is an analytics and process-intelligence platform for a Procure-to-Pay
process, built for a fictional client (Northstar Manufacturing) -- see `docs/business-requirements.md`
for the full scenario and questions it answers.

## Start here, in this order
1. `docs/business-requirements.md` -- what the client needs and why
2. `docs/data-dictionary.md` + `docs/data-contract.md` -- what the data is (real BPI 2019 +
   synthetic business context) and what every field means
3. `docs/process-model.md` -- the expected process this project measures deviation against
4. `docs/analytical-methodology.md` -- exact definitions for every metric
5. `docs/ml-model.md` -- the SLA-risk prediction model
6. `docs/data-quality-report.md` -- how source data is validated and cleaned
7. `cloud/README.md` -- Postgres vs. BigQuery, and why the migration exists

## Architecture
See the diagram in the repo root `README.md`. In short: raw event log -> ETL (contract check,
validate, clean) -> PostgreSQL -> analytics/ML -> FastAPI (`src/api/`) -> dashboard + management
report -> optional BigQuery migration.

## Companion project
[`operations-assistant`](../operations-assistant) is a separate repo that consumes this project's
`src/api/` endpoints as agent tools rather than reimplementing any of this logic -- one analytics
engine, two front doors (a human-facing dashboard here, an AI-agent interface there).

## Build status
See `PLAN.md` for the phase-by-phase build log, including two real bugs the test suite caught
during Phase 9 (worth reading if you want the actual "how was this tested" story).
