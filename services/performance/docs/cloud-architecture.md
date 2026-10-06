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
