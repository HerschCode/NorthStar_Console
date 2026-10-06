# Analytical SQL: window functions, verified live

`sql/analysis/bottlenecks.sql` was, for a while, the only file in this project using a window
function (`LEAD()`). That's thinner than the project claimed. This adds 8 more files, each run
live against the real Neon Postgres instance holding the BPI 2019 sample — not written and left
untested. Two bugs were found and fixed by actually running them (below).

Reproduce: `python -m scripts.run_sql_analysis` (or run any file directly against `psql`/a
Postgres client). All 8 return real rows from the live `staging.events` (32,274 rows) /
`analytics.process_cases` (3,000 rows) tables.

## The 8 files, and what each demonstrates that the others don't

| File | Window function(s) | Real question it answers |
|---|---|---|
| `waiting_time_lag.sql` | `LAG()` | Which activities make callers wait longest *before* they start (opposite direction from `bottlenecks.sql`'s `LEAD()`, which measures the stage itself) |
| `variant_ranking.sql` | `RANK()`, `DENSE_RANK()`, `SUM() OVER` | Rank process variants per category, showing the tie-handling difference between `RANK` (gaps) and `DENSE_RANK` (no gaps) side by side |
| `rolling_sla_breach_rate.sql` | `AVG()`/`COUNT() OVER (RANGE BETWEEN INTERVAL ...)` | A trailing 28-*calendar*-day breach rate — `RANGE` on a time interval, not `ROWS`, which would silently be wrong on non-uniform daily volume |
| `monthly_cohort_breach_trend.sql` | `SUM() OVER (ORDER BY ...)` | Cumulative breach rate by start-month cohort — cohort-grained and running, distinct from the case-grained rolling window above |
| `supplier_quartiles.sql` | `NTILE(4)`, `PERCENT_RANK()` | Two different ways to rank supplier slowness: discrete buckets vs. a continuous 0–1 score |
| `slowest_stage_per_case.sql` | `FIRST_VALUE()`, `LAST_VALUE()` | Each case's single slowest/fastest stage pulled onto every row without a self-join — and demonstrates the classic `LAST_VALUE` default-frame trap (see below) |
| `running_event_count.sql` | `ROW_NUMBER()` + a correlated subquery | Running distinct-activity count per event — and documents *why* this specific one isn't a window function (see below) |
| `category_relative_cycle_time.sql` | `PERCENT_RANK()`, `AVG()`/`STDDEV() OVER` | Where a case sits in *its own category's* distribution, not the global one — percentile and z-score side by side |

## Two real bugs, found by running these, not by inspection

1. **`COUNT(DISTINCT ...) OVER (...)` doesn't exist in Postgres** — "DISTINCT is not implemented
   for window functions." `running_event_count.sql` needs a running *distinct*-activity count,
   which isn't expressible as a single window aggregate at all: a window `FILTER` clause can only
   restrict frame rows using *their own* columns, it can't compare a frame row against the current
   row's value, which is exactly what "activities seen up to this point" needs. Fixed with a
   correlated subquery instead of forcing a window-function shape that doesn't fit — the file's
   own comment explains the distinction rather than hiding it.
2. **`ROUND(double precision, integer)` doesn't exist** — only `ROUND(numeric, integer)` does.
   `category_relative_cycle_time.sql`'s z-score expression needed the `::numeric` cast wrapped
   around the *entire* division, not just the final result, because of operator precedence.

## A third, more consequential bug: the migration runner itself, and a schema drift it hid

Adding the index below meant editing `sql/schema/002_create_indexes.sql` and re-running
`python -m scripts.setup_database` against the live database. That run failed:
`psycopg2.ProgrammingError: can't execute an empty query`. Root cause:
`scripts/setup_database.py`'s `run_sql_file` naively splits the raw file text on `;` to get
individual statements — which also splits **inside `--` line comments**. The new index's own
explanatory comment happened to contain a semicolon, producing a comment-only chunk with no SQL
in it, which Postgres doesn't silently skip. Fixed by stripping `--` comments before splitting
(`scripts/setup_database.py`, tested in `tests/test_setup_database.py`).

Debugging that surfaced something more important than the bug itself: **`pg_indexes` on the live
Neon database showed zero indexes on the `staging` schema, and none of `002_create_indexes.sql`'s
indexes on `analytics.process_cases` either.** The file existed in the repo with the correct
intent, but nothing had ever actually run it against the deployed database — real schema drift
between what the repo claims and what's live, not something a code review would have caught,
only running the actual setup path does. Fixed by running the corrected `setup_database.py`
against Neon for real: `idx_events_case_id_timestamp`, `idx_process_cases_supplier`, and
`idx_process_cases_category` are now genuinely present (verified via `pg_indexes` after the run,
not assumed from the script exiting 0).

`slowest_stage_per_case.sql` also deliberately avoids a third trap: `LAST_VALUE()`'s default
window frame is "up to the current row," which would make it return the *current* row's own
value instead of the true last one. Fixed with an explicit
`ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING` frame.

## `EXPLAIN ANALYZE` and an index — with an honest negative result

`staging.events` had no index on `(case_id, timestamp)`, despite every window function above
partitioning and ordering by exactly that pair. `EXPLAIN (ANALYZE, BUFFERS)` on
`waiting_time_lag.sql`, before and after adding one:

| | Plan | Execution time |
|---|---|---|
| Before | `Seq Scan on events` (cost 0.00–827.74) | 58.4 ms |
| After `CREATE INDEX idx_events_case_id_timestamp ON staging.events (case_id, timestamp)` | `Index Scan using idx_events_case_id_timestamp` (cost 0.41–2350.02) | 59.2 ms |

**The index changed the plan but not the measured speed.** The planner's own cost estimate
dropped (7341 → 6366), and the scan node genuinely switched from sequential to index — but at
32,274 rows the whole table fits in a handful of sequential reads and Postgres's buffer cache
either way, so I/O was never the bottleneck. The `WindowAgg` and the two `Sort` nodes above it
dominate the runtime (34ms and 12–14ms respectively) regardless of how the base table is
scanned. Kept as a real result rather than reported as "added an index, made it faster" — an
index that measurably helps here would need either a much larger table or a query that filters
to a small slice of `staging.events` before hitting the window function, neither of which is
true of this query. The index is still added (it's the theoretically correct one, and would
matter as this table's 3,000-case sample grows toward the full 251K-case BPI 2019 log), but the
before/after number is reported honestly rather than assumed.
