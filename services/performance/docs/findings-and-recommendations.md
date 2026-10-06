# Findings & Recommendations

This document is intentionally NOT a hand-maintained copy of the report -- that would drift out of
sync with the actual data the moment the pipeline reruns. The real findings live at:

- **API**: `GET /reports/management` (returns current Finding -> Evidence -> Impact ->
  Recommendation markdown, generated fresh from whatever's currently in the database)
- **CLI**: `python -m src.reports.generate_management_report` (same output, no API needed)
- **Source logic**: `src/reports/generate_management_report.py`

## Why this doc doesn't contain the findings itself
Every finding is derived from live data (`_bottleneck_finding`, `_rework_finding`,
`_conformance_finding`, `_sla_driver_finding` in `generate_management_report.py`). A markdown file
with specific numbers pasted in would be accurate on the day it was written and wrong the next time
the pipeline runs against new data. The report generator exists specifically so there's one source
of truth -- pointing this doc at it, rather than duplicating its output, keeps that true.

## What the report structure guarantees
Every finding follows Finding -> Evidence -> Impact -> Recommendation, with a priority
(HIGH/MEDIUM/LOW based on magnitude, not analyst judgment) -- see the docstring and each
`_*_finding` function in `generate_management_report.py` for the exact priority thresholds used.

## For a portfolio snapshot
If you need a static example for a README or a slide (rather than a live query), run
`python -m src.reports.generate_management_report` against a real loaded dataset and paste that
specific output somewhere clearly dated -- don't paste it here as if it were current.
