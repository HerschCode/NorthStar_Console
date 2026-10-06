# SQL Analysis

`sql/analysis/*.sql` -- SQL equivalents of the Python analytics in `src/analytics/`, kept
visible and browsable on their own rather than only reachable through Python. Each file's own
header comment names the Python function it mirrors.

| File | Mirrors | Notable technique |
|---|---|---|
| `cycle_time.sql` | `cycle_time.py:cycle_time_percentiles` | `PERCENTILE_CONT` |
| `bottlenecks.sql` | `cycle_time.py:stage_durations` + `bottlenecks.py` | `LEAD()` window function |
| `sla_performance.sql` | `sla_analysis.py` | `ROLLUP` for overall + per-category in one query |
| `supplier_performance.sql` | `supplier_analysis.py:supplier_scorecard` | `HAVING` for the min-volume threshold |
| `process_variants.sql` | `process_variants.py:variant_summary` | plain `GROUP BY` on a precomputed column |
| `root_cause_analysis.sql` | `root_causes.py:sla_breach_drivers` | `UNION ALL` across multiple driver dimensions, breach-rate lift vs. overall |
| `conformance.sql` | **partial** -- see below | `HAVING COUNT(*) > 1` for a repeated-activity signal only |

## Why `conformance.sql` is deliberately partial
Full conformance checking (`src/analytics/conformance.py`) needs skipped-step detection,
unexpected-activity detection, AND order-sequence comparison against
`config/process.yaml`'s dynamic `expected_sequence` -- expressing that last part correctly in a
single portable SQL statement is meaningfully harder and more fragile than in Python, and getting
it subtly wrong would silently disagree with the authoritative Python implementation. `conformance.sql`
covers what's genuinely straightforward in SQL (repeated activities) and says so directly in its
own header comment, rather than a full but risky reimplementation. Use `GET /metrics/conformance`
for the authoritative answer.

## Why there's no `sql/transformations/`
The ETL transformation logic (raw events → cleaned events → process cases) lives in Python
(`src/cleaning/clean_events.py`, `src/transformation/build_process_cases.py`) as the single
source of truth. A parallel SQL implementation of the same logic would risk drifting from the
Python version over time -- the same "don't duplicate the source of truth" principle
`operations-assistant` follows by calling this project's API instead of re-deriving values itself.
