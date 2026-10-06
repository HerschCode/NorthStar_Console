# Analytical Methodology

Precise definitions for every metric used across `src/analytics/`. If a number in the dashboard
or an API response doesn't match one of these definitions exactly, that's a bug.

## Cycle time
Wall-clock time from a case's first event to its last event (`build_process_cases.py`):
`end_time - start_time`, in hours. Includes both active processing time and idle/waiting time --
we don't currently distinguish the two at the case level (see Stage duration below for that).

## Stage duration
Time between one activity and the *next* activity within the same case
(`cycle_time.stage_durations`). Reported as `"Activity A -> Activity B"`. This is where
waiting-time analysis actually happens -- a long stage duration means the case sat idle between
those two steps, not that the second activity itself took long.

## Bottleneck
A stage (as defined above) with high average duration relative to other stages, expressed as a
% contribution to total average delay (`bottlenecks.identify_bottlenecks`). We rank by mean, but
also report median and p90 alongside it -- a stage with a high mean and a much lower median usually
means a handful of extreme outliers, not a systemic bottleneck, and that distinction matters when
recommending a fix.

## SLA breach
A case where `cycle_time_hours > sla_target_hours`, where the target comes from
`config/sla.yaml` (per category, with a default fallback). SLA is evaluated at the *case* level
against total cycle time, not per-stage -- a per-stage SLA is a reasonable future addition, not
implemented yet.

## Process variant
The full ordered sequence of activities in a case, joined as a string (`"A -> B -> C"`). Two cases
have the same variant only if their activities occur in the identical order. Variant frequency is
how many cases share a given variant -- a low-frequency variant is, by definition, an unusual path
through the process, not necessarily a wrong one.

## Conformance
A case is **conformant** if it contains no deviation from `config/process.yaml`'s
`expected_sequence`. Four deviation types, checked independently (a case can have more than one):
- `skipped_step` -- an expected activity never occurred
- `unexpected_activity` -- an activity occurred that isn't part of the expected process at all
- `repeated_activity` -- an activity occurred more than once and isn't in `allowed_repeats`
- `out_of_order` -- the expected activities that did occur, occurred in the wrong relative order,
  after deduplicating repeats (so a repeated activity -- allowed or not -- never by itself
  produces an `out_of_order` false positive on top of, or instead of, the more specific
  `repeated_activity` deviation; see `docs/data-correctness-audit.md` for the real bug this
  behavior was fixed to prevent)

Conformance rate = conformant cases / total cases. This is a strict definition (any deviation
type = non-conformant); a looser "how far off" scoring is a reasonable Tier 3 addition.

## Rework
A repeated activity that is **not** in `config/process.yaml`'s `allowed_repeats` list (e.g. a
repeated Approval after rejection is rework; a repeated Goods Receipt for a multi-line PO is not,
since that's normal operation, not a process failure). `rework_count` per case is the number of
"extra" occurrences beyond the first for each non-allowed repeated activity.

## Root cause / driver
We report **association**, not causation. `sla_breach_drivers` reports breach-rate lift at each
level of a candidate driver (supplier, category, variant, etc.) versus the overall breach rate --
this identifies where to look, not a proven cause. We only call something a "cause" in written
findings when there's a plausible causal mechanism to go with the correlation (e.g. "orders
requiring manual review breach more" is causally plausible; "orders created on a Tuesday breach
more" would need a mechanism before we'd call it anything but a curiosity).

## Supplier scorecard
Supplier-level aggregation of order count, average and std-dev of cycle time, and SLA breach rate.
Suppliers with fewer than `min_volume` (default 5) orders are excluded from ranking -- a supplier
with 2 orders and 1 breach isn't meaningfully "worse" than one with 200 orders and 50 breaches,
and ranking them together would be misleading.

## Correlation vs. causation (general note)
Anywhere we compute a Pearson correlation (`root_causes.correlation_matrix`) or a group comparison,
treat the result as a hypothesis-generation tool. It tells you where an SLA-breach relationship
*might* exist; it does not establish that changing the driver would change the outcome. That claim
needs either a controlled comparison (e.g. before/after a real process change) or domain reasoning
about mechanism, not just a coefficient.
