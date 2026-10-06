# Performance Notes (Phase 30)

Real, runnable measurement -- `scripts/benchmark_pipeline.py` times every pipeline stage against
either the Phase 27 golden dataset (7 cases) or a generated synthetic set at any scale
(`--synthetic --n-cases N`). Run it: `python -m scripts.benchmark_pipeline --synthetic --n-cases 2000`.

## A real bug this benchmark found
The first run at 2,000 cases (12,000 events) showed `rework_by_case` taking **978ms** -- roughly
8-40x slower than every sibling analytics function doing comparable work (`build_process_cases`:
49ms, `stage_summary`: 19ms, `identify_bottlenecks`: 18ms). Root cause: `rework_by_case` looped
over every case in plain Python (`for case_id, group in events.groupby("case_id")`), calling
`.value_counts()` per iteration -- 2,000 separate small pandas operations plus Python loop
overhead, instead of one vectorized groupby over the whole DataFrame.

Rewritten to do a single `events.groupby(["case_id", "activity"]).size()` and aggregate from
there. Re-benchmarked: **9.93ms** -- a **~98x speedup**, dropping total pipeline time for 2,000
cases from 1,248ms to 277ms (4.5x overall, from fixing one function). Correctness reverified
against the Phase 27 golden dataset after the rewrite -- `test_rework_matches_hand_calculation`
still passes exactly, so this was a pure performance fix, not a behavior change.

## A known, smaller optimization -- attempted twice, honest about the first miss
`check_conformance` had the same per-case Python-loop shape as `rework_by_case` did, and was
deliberately left unfixed in the first performance pass (documented here at the time as "not
urgent, and rewriting it carelessly right after fixing a real correctness bug in the same area
risks introducing a new one").

**First vectorization attempt** converted `repeated_activity` and `unexpected_activity` to real
vectorized groupby operations, but left `skipped_step` using `.apply(set)` (a Python function
called once per group -- not actually vectorized, despite the groupby syntax) and `out_of_order`
still had an explicit per-case for-loop. Re-benchmarked at **134.58ms** -- essentially unchanged
from the original 128ms. The honest lesson: partially vectorizing a function and declaring victory
without re-measuring would have shipped a change that looked like a fix but wasn't one.

**Second attempt** replaced `skipped_step` with a loop over `expected` (a fixed 6-item list) doing
per-activity boolean-mask lookups instead of per-case set operations, and replaced `out_of_order`
with `groupby().shift()` -- a genuinely vectorized (Cython-implemented) pandas operation, comparing
each case's deduped expected-activity occurrences against the previous one within that case, no
Python loop over cases at all. Re-benchmarked at **32.99ms** -- a real ~4x speedup this time,
verified by actually re-running the benchmark rather than assuming the second attempt worked
better than the first just because it looked more vectorized.

Correctness reverified against the Phase 27 golden dataset after each attempt -- unchanged both
times, confirming both were pure performance changes, not behavior changes.

Total pipeline time for 2,000 cases: **162ms**, down from the original 1,248ms before any
performance work -- a ~7.7x overall improvement across the two functions fixed
(`rework_by_case`'s ~98x speedup plus `check_conformance`'s ~4x).

## What's NOT measured here (stated plainly, not implied to be covered)
- **BigQuery query cost** -- needs a real BigQuery project to measure actual bytes-scanned/cost;
  `cloud/README.md`'s discussion of partitioning/clustering is architectural reasoning, not a
  measured cost figure.
- **API response time under load** -- `src/api/` has never been benchmarked under concurrent
  requests (same gap `operations-assistant`'s `docs/failure-modes.md` names for its own API).
- **Postgres query performance at real data volume** -- this benchmark measures the Python/Pandas
  analytics layer, not SQL query execution against a live database (none available in this
  environment).

## Benchmark results (2,000 synthetic cases, this environment's hardware -- illustrative of
relative stage cost, not an absolute production figure)
| Stage | Duration | Notes |
|---|---|---|
| `build_process_cases` | 40ms | |
| `check_conformance` | 33ms | after Phase 30's second vectorization pass (was 128ms originally, 134ms after an incomplete first attempt) |
| `clean_events` | 21ms | |
| `identify_bottlenecks` | 18ms | |
| `stage_summary` | 18ms | |
| `load_event_log` | 11ms | |
| `rework_by_case` | 9ms | after vectorization (was 978ms) |
| `validate_event_log` | 6ms | |
| `supplier_scorecard` | 4ms | |
| `evaluate_sla` | 1ms | |
| `cycle_time_percentiles` | 1ms | |
| `enforce_data_contract` | <1ms | |
