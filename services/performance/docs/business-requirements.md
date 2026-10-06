# Business Requirements

## Client scenario
Northstar Manufacturing (fictional client) wants to understand why its Procure-to-Pay (P2P)
process is slow, where it breaks down, which suppliers/business units are responsible, which
orders are at risk of missing SLA, and what to do about it. This is the same scenario used in
`FEATURES.md` and `docs/analytical-methodology.md` -- restated here as the requirements those
features trace back to.

## Questions the system needs to answer
1. Where in the process is time actually being lost? (Phase 3: `bottlenecks.py`, `cycle_time.py`)
2. Is the process being followed as designed, or are cases deviating from it? (Phase 3:
   `conformance.py`)
3. How much of the delay is caused by rework/rejection loops rather than normal processing?
   (Phase 3: `rework.py`)
4. Which suppliers, categories, or business units are disproportionately responsible for delay or
   SLA breaches? (Phase 3: `root_causes.py`, `supplier_analysis.py`)
5. Can we flag an at-risk order before it actually breaches, so someone can intervene? (Phase 4:
   `src/ml/`)
6. Can findings be delivered as something a non-technical manager can act on, not just a chart?
   (Phase 7: `src/reports/generate_management_report.py`)

## Explicit non-goals (things this project deliberately does not attempt)
- **Causal inference.** Every driver/root-cause finding is stated as association, not proven
  causation -- see `analytical-methodology.md`'s "Root cause / driver" section. A true causal
  claim would need a controlled before/after comparison this project doesn't have.
- **Real-time processing.** The pipeline is batch (`scripts/run_pipeline.py`, run on demand or on
  a schedule), not a streaming system reacting to individual events as they happen.
- **Prescriptive automation.** The system recommends ("investigate X", "consider automating Y");
  it doesn't automatically approve/reject purchase orders or change the live process itself.

## Success criteria
- A manager can read `GET /reports/management` (or the dashboard) and identify the single highest-
  priority process issue without needing to interpret raw data themselves.
- An analyst can trace any number in the dashboard back to the exact query/function that produced
  it (`analytical-methodology.md` + the `src/analytics/` module it names).
- The SLA-risk model surfaces genuinely actionable early warnings, not just a probability score
  with no explanation (`src/ml/explain.py`).
