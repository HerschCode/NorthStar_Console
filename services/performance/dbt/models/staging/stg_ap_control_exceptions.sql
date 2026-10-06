-- 1:1 with analytics.ap_control_exceptions. Unlike every other model in dbt/models/, this source
-- table is written by a Python job (scripts/run_ap_controls.py -> src/controls/ap_controls.py),
-- not computed in SQL: the checks use pandas rolling time windows, fuzzy amount/date matching and
-- a chi-square test, none of which are reasonably portable SQL. This model is a documented
-- passthrough, kept in dbt for lineage and testing (accepted_values, not_null), not a
-- reimplementation of the control logic in a different language.
select
    exception_id,
    case_id,
    control_id,
    severity,
    exposure_eur,
    evidence,
    generated_at
from {{ source('raw_analytics', 'ap_control_exceptions') }}
