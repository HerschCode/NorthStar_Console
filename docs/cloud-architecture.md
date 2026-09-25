# Cloud Architecture

The full PostgreSQL-vs-BigQuery breakdown lives in [`cloud/README.md`](../cloud/README.md) --
this file exists mainly so `docs/` has a complete index (see `project-overview.md`), rather than
duplicating that content here and risking the two drifting apart.

Quick summary: this project runs on PostgreSQL locally (Phases 1-7), then migrates the analytics
layer to BigQuery (`cloud/bigquery/schema.sql`, `scripts/migrate_to_bigquery.py`) as Phase 8.
`cloud/README.md` covers what actually changes (partitioning/clustering replacing indexes,
`APPROX_QUANTILES` replacing `PERCENTILE_CONT`, the cost model, when you'd choose which) and is
honest that this specific dataset doesn't strictly need BigQuery's scale -- the migration exists
to demonstrate the skill and reasoning.

## Deploying to Cloud Run

[`northstar-infra`](https://github.com/HerschCode/northstar-infra) is the Terraform for running this API,
and the pipeline job that shares its image, on Cloud Run as a **private** service that only
operations-assistant's service account may call. `.github/workflows/deploy.yml` builds this image, scans
it with Trivy, pushes it to Artifact Registry and rolls both the service and the `operations-pipeline`
job over to it. Manual dispatch only, from `main`, and a no-op until the repository variables that
Terraform prints exist; it has not been run against a real project yet. The application-side changes
that deployment still wants are listed, with their status, in northstar-infra's
[`docs/app-integration.md`](https://github.com/HerschCode/northstar-infra/blob/main/docs/app-integration.md).
