# PostgreSQL vs. BigQuery — What Changed and Why

This project runs locally on PostgreSQL first (Phases 1-7), then migrates the analytics layer to
BigQuery (`scripts/migrate_to_bigquery.py`, `cloud/bigquery/schema.sql`). This doc is the actual
interview answer -- the migration script proves it can be executed, this explains the reasoning.

## What actually changed

| | PostgreSQL | BigQuery |
|---|---|---|
| Model | Row-oriented, transactional | Columnar, analytical (OLAP) |
| Keys/constraints | Enforced (PK/FK, NOT NULL) | Not enforced -- documented in comments as intent only |
| Scaling unit | Vertical (bigger instance) | Horizontal, fully managed -- no instance sizing at all |
| Cost model | Fixed (you pay for the running instance) | Pay-per-query (bytes scanned), plus storage |
| Indexes | B-tree indexes you create and maintain | No traditional indexes -- partitioning + clustering instead |
| Percentiles | `PERCENTILE_CONT` | `APPROX_QUANTILES` (approximate, not exact -- see below) |

## Why partitioning and clustering replace indexes
BigQuery doesn't have row-level indexes the way Postgres does -- a query against an unpartitioned,
unclustered BigQuery table scans the whole table, and you pay for every byte scanned. Partitioning
(`PARTITION BY DATE(timestamp)`) means a query with a date filter only scans the relevant day(s),
not the whole table's history. Clustering (`CLUSTER BY case_id, category`) sorts data within each
partition so filters/joins on those columns can skip irrelevant blocks too. This is the single
most important BigQuery-specific decision in `cloud/bigquery/schema.sql` -- get it wrong and every
query costs far more than it needs to, silently.

## Why APPROX_QUANTILES instead of PERCENTILE_CONT
`APPROX_QUANTILES` is BigQuery's standard approach for percentiles at scale -- it trades exactness
for speed and cost using an approximation algorithm, and the error margin is negligible for
business reporting at this data volume. This is a genuine BigQuery-specific substitution, not a
stylistic choice -- worth naming directly if asked why the SQL isn't identical between the two
`cloud/bigquery/tables.sql` views and the Postgres `sql/analysis/` queries.

## Why the migration script doesn't enforce partitioning automatically
`load_table_from_dataframe` with `autodetect=True` creates a plain table from the DataFrame's
schema -- it does **not** apply the `PARTITION BY`/`CLUSTER BY` from `schema.sql` unless that DDL
was run first to create the table shell. The script flags this directly rather than silently
producing an unpartitioned table and letting cost surprise show up later. In a real migration,
the correct order is: run `schema.sql` to create partitioned/clustered empty tables, *then* run
the migration script to load into them.

## When you'd actually choose which
- **Postgres**: transactional workloads, data that needs referential integrity enforced by the
  database itself, moderate data volume, a team that wants full control over indexing and query
  planning, or a workload where per-query cost predictability (fixed instance cost) matters more
  than infinite scale.
- **BigQuery**: large-scale analytical workloads, ad-hoc exploratory querying across large
  historical datasets, a team that doesn't want to manage database infrastructure at all, or a
  reporting/BI layer (like `operations-assistant`'s tools later) that needs to query large volumes
  without worrying about instance sizing.

For a system this size, Postgres alone would be perfectly sufficient in production -- the BigQuery
migration here exists to demonstrate the skill and the reasoning, not because this specific
dataset actually needs BigQuery's scale. Saying that plainly, unprompted, in an interview is a
better signal than pretending the migration was operationally necessary.
