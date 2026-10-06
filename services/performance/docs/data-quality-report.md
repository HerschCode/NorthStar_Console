# Data Quality Report

This documents the data-quality *methodology* and how to read the output of
`src/cleaning/data_quality.py:run_data_quality_checks`. It is not a one-time snapshot -- every
pipeline run produces a fresh quality report (logged via `scripts/run_pipeline.py`'s "Cleaning
complete" log line, and stored in `analytics.pipeline_runs`), so treat any specific numbers below
as illustrative of the *shape* of the report, not a permanent finding. Re-run against your actual
BPI 2019 export and replace the example numbers with real ones before this doc is final.

## What gets checked
See `DataQualityReport` in `src/cleaning/data_quality.py` for the authoritative field list:

| Check | What it catches |
|---|---|
| `duplicate_rows` | Fully identical rows (same case, activity, timestamp, everything) |
| `duplicate_within_case` | Same case/activity/timestamp combination repeated |
| `missing_resource` | Events with no recorded resource (who performed it) |
| `missing_supplier` | Cases with no supplier/vendor recorded |
| `unparseable_timestamps` | Timestamp values that don't parse to a valid datetime |
| `out_of_order_events` | A case where an event's timestamp precedes the previous event's timestamp *as given* (not after sorting -- see the Phase 9 bug note below) |

## What happens to each issue
- **Missing `case_id`, `activity`, or `timestamp`**: row dropped entirely (`clean_events.py`) --
  these are required for every downstream calculation, so a row missing any of them can't be
  processed at all.
- **Exact duplicates** (same case/activity/timestamp): dropped, keeping one copy.
- **Missing `resource`/`supplier_id`**: filled with `"UNKNOWN"` rather than dropped -- these are
  informative fields, not required for cycle-time/SLA calculations, so losing the row over a
  missing resource would discard otherwise-valid process data.
- **Out-of-order events**: **not corrected automatically** -- flagged in the report, not silently
  re-sorted, because an event log where events genuinely happened out of the expected order might
  itself be a real finding (a backdated correction, a system clock issue, a real process
  irregularity), not just bad data to paper over.
- **Duplicate rate above 5%**: the pipeline adds a note recommending investigation of the ingestion
  source before proceeding, rather than just cleaning silently -- a duplicate rate that high
  usually indicates an upstream export problem, not normal noise.

## A real bug this caught (worth keeping in the record)
During Phase 9 testing, `_count_out_of_order` was found to be sorting events by timestamp *before*
computing the diff used to detect out-of-order events -- which silently fixed the exact problem it
was meant to catch and always reported zero. This is exactly the kind of data-quality-report bug
that's dangerous precisely because it fails quietly (a report that says "0 problems found" looks
fine, not broken). Fixed in `src/cleaning/data_quality.py`; the test that caught it is
`tests/test_cleaning.py::test_data_quality_report_counts_out_of_order_events`.

## Where to see quality results for a specific run
- CLI: `python -m src.cleaning.clean_events` prints the full report for the current
  `RAW_EVENT_LOG_PATH`
- Pipeline logs: every `scripts/run_pipeline.py` run logs `dropped_missing`,
  `dropped_duplicates`, and `rows_after` for that specific run (structured JSON, see
  `src/observability/logging_config.py`)
- API: `GET /observability/pipeline-runs` shows row counts per run over time, so a sudden change
  in raw/cleaned row count between runs is visible without re-running the cleaning step manually
