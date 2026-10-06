# Data Correctness Audit

Phase 27: independently verify that the analytics functions produce the numbers a human doing
the arithmetic by hand would get -- not just "the code runs and returns something," but "the
code returns the *right* thing." This is different from `tests/test_analytics.py`'s unit tests,
which check behavior on small synthetic inputs; this audit hand-derives every metric from a
purpose-built 7-case dataset (`data/golden/golden_raw_events.csv`) *before* running any code, then
proves `tests/test_data_correctness.py` gets the identical numbers.

**Why not audit against real BPI 2019 data**: that would need the actual dataset loaded, which
this environment doesn't have. A hand-verifiable golden set is what's achievable here, and it's
arguably the more rigorous check anyway -- with a real dataset you can't independently verify a
breach rate of 18.4% by hand; with this one, every number below was computed with a calculator
before any code ran, specifically so there's no way the test and the implementation could share
the same mistake.

## The dataset
7 cases, deliberately designed to hit every deviation type conformance checking looks for, plus
rework, SLA breach, and a clear supplier ranking -- not a random sample, a constructed one.

| Case | Supplier | Category | Cycle time | Deviation type | SLA breach |
|---|---|---|---|---|---|
| GC01 | S1 | 3-way match | 72h | none (conformant) | no |
| GC02 | S1 | 3-way match | 264h | none (conformant) | **yes** |
| GC03 | S2 | Consignment | 96h | repeated_activity (Approval x2) | no |
| GC04 | S1 | 3-way match | 48h | none (Goods Receipt repeat is *allowed*) | no |
| GC05 | S3 | Consignment | 36h | skipped_step (no Goods Receipt) | no |
| GC06 | S2 | 3-way match | 36h | unexpected_activity ("Dispute Hold") | no |
| GC07 | S3 | 3-way match | 252h | none (conformant) | **yes** |

## Cycle time -- hand-derived
GC01: 2024-01-04 00:00 − 2024-01-01 00:00 = **72h**. GC02: 2024-01-12 − 2024-01-01 = **264h**.
GC03: 2024-01-05 − 2024-01-01 = **96h**. GC04: 2024-01-03 − 2024-01-01 = **48h**. GC05:
2024-01-02 12:00 − 2024-01-01 = **36h**. GC06: 2024-01-02 12:00 − 2024-01-01 = **36h**. GC07:
2024-01-11 12:00 − 2024-01-01 = **252h**.

Sorted: [36, 36, 48, 72, 96, 252, 264], n=7.
- **mean** = 804 / 7 = **114.857h**
- **median** = 4th value = **72h**
- **p90** (pandas linear interpolation, position 0.9×6=5.4, between index 5 (252) and 6 (264)) =
  252 + 0.4×12 = **256.8h**
- **p99** (position 0.99×6=5.94) = 252 + 0.94×12 = **263.28h**
- **case_count** = **7**

## SLA breach -- hand-derived
Targets from `config/sla.yaml`: 3-way match = 240h, Consignment = 336h.
- GC01 (72h ≤ 240h): no breach. GC02 (264h > 240h): **breach**. GC03 (96h ≤ 336h): no breach.
  GC04 (48h ≤ 240h): no breach. GC05 (36h ≤ 336h): no breach. GC06 (36h ≤ 240h): no breach.
  GC07 (252h > 240h): **breach**.
- **breach_count = 2, case_count = 7, breach_rate = 28.57%**

## Rework -- hand-derived
Only a repeat *not* in `config/process.yaml`'s `allowed_repeats` (`Goods Receipt`) counts.
- GC03 repeats **Approval** -- not allowed -- **has_rework = True**.
- GC04 repeats **Goods Receipt** -- allowed -- **has_rework = False**.
- Every other case has no repeated activity.
- **rework_count = 1 of 7 = 14.29%**

## Conformance -- hand-derived
Expected sequence (`config/process.yaml`): Purchase Requisition → Approval → Purchase Order →
Goods Receipt → Invoice Receipt → Payment.
- GC01, GC02, GC07: exact sequence, in order -- **conformant**.
- GC04: Goods Receipt repeats, but it's an allowed repeat, full sequence otherwise present and in
  order -- **conformant**.
- GC03: Approval repeats and is *not* allowed -- **non-conformant** (`repeated_activity`).
- GC05: no Goods Receipt event at all -- **non-conformant** (`skipped_step`).
- GC06: contains "Dispute Hold," not part of the expected sequence -- **non-conformant**
  (`unexpected_activity`).
- **conformant = 4 of 7 = 57.14%**. Deviation breakdown: `repeated_activity: 1`,
  `skipped_step: 1`, `unexpected_activity: 1`.

## Supplier scorecard -- hand-derived (min_volume=2 for this 7-case set)
- **S1**: GC01(72h,no breach), GC02(264h,breach), GC04(48h,no breach) → avg = (72+264+48)/3 =
  **128h**, breach rate = 1/3 = **33.3%**
- **S2**: GC03(96h,no breach), GC06(36h,no breach) → avg = **66h**, breach rate = **0%**
- **S3**: GC05(36h,no breach), GC07(252h,breach) → avg = **144h**, breach rate = **50%**
- **S3 is worst by both average cycle time and breach rate** -- deliberately unambiguous, so the
  scenario test in Phase 28 has exactly one correct answer to "which supplier is worst."

## Bottleneck analysis -- hand-derived (the most arithmetic-heavy check, and the most valuable)
Every stage transition across all 7 cases, grouped by `"Activity A -> Activity B"`:

| Stage | Values (hours) | n | mean | median |
|---|---|---|---|---|
| Purchase Requisition → Approval | 10,120,24,8,6,5,120 | 7 | **41.857** | **10** |
| Approval → Approval | 24 | 1 | 24 | 24 |
| Purchase Order → Goods Receipt | 24,48,12,8,24 | 5 | 23.2 | 24 |
| Invoice Receipt → Payment | 12,48,12,8,12,6,60 | 7 | 22.571 | 12 |
| Goods Receipt → Invoice Receipt | 12,24,12,8,6,24 | 6 | 14.333 | 12 |
| Approval → Purchase Order | 14,24,12,8,6,5,24 | 7 | 13.286 | 12 |
| Purchase Order → Invoice Receipt | 12 | 1 | 12 | 12 |
| Dispute Hold → Goods Receipt | 9 | 1 | 9 | 9 |
| Goods Receipt → Goods Receipt | 8 | 1 | 8 | 8 |
| Purchase Order → Dispute Hold | 5 | 1 | 5 | 5 |

Sum of all 10 stage means ≈ **173.247h**. Top bottleneck by mean: **"Purchase Requisition →
Approval"** at 41.857h, contributing **41.857 / 173.247 ≈ 24.16%** of total average delay.

**This is a genuinely useful illustration for `docs/analytical-methodology.md`'s point about
mean vs. median**, not a contrived one: this stage's mean (41.857h) is over 4x its median (10h) --
two cases (GC02, GC07) both happen to take exactly 120h for this step, dragging the average up
hard while most cases clear it in under a day. Ranking by mean surfaces this stage as the #1
bottleneck; ranking by median would surface "Approval → Approval" (24h) or
"Purchase Order → Goods Receipt" (23.2h/24h) instead. Both rankings are legitimate and answer
different questions -- exactly the reasoning the methodology doc already argues for reporting both.

## Result
`tests/test_data_correctness.py` runs the real pipeline (`load_event_log` →
`validate_event_log` → `clean_events` → `build_process_cases` → `evaluate_sla` →
`rework_by_case` → `check_conformance` → `supplier_scorecard` → `identify_bottlenecks`) against
`golden_raw_events.csv` and asserts every number above, exactly.

## A real bug this audit found (not a discrepancy in the hand math)
The first run of this audit failed on GC04 -- the code said it was non-conformant; the hand
derivation above says it should be conformant (its only deviation is a Goods Receipt repeat,
which `config/process.yaml`'s `allowed_repeats` explicitly permits).

Root cause: `conformance.py`'s out-of-order check built `present_expected` (the case's activities
that are part of the expected sequence) *without* deduplicating repeats, then compared it against
`expected_order_of_present` (built by filtering the inherently-unique `expected` list). A case
with **any** repeated activity -- allowed or not -- has a longer `present_expected` than
`expected_order_of_present`, so the equality check could never pass, regardless of whether the
actual step order was correct. GC04's Goods Receipt repeat isn't an ordering problem at all; the
bug flagged it as one anyway.

This wasn't a contrived edge case -- it's the exact scenario `allowed_repeats` exists to handle
(a multi-line PO producing multiple Goods Receipt events), and the bug meant **`allowed_repeats`
never actually worked for conformance checking**, only for `rework.py`'s separate rework
detection. Fixed by deduplicating `present_expected` (preserving first-occurrence order) before
the comparison. `test_conformance_matches_hand_calculation` now explicitly asserts the
`deviation_breakdown` contains no `out_of_order` entries for this golden set, specifically to
guard against this regressing.
