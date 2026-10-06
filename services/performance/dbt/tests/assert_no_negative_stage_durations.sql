-- Custom singular test (dbt fails the build if this returns any rows): a stage-to-stage
-- duration should never be negative -- that would mean an event's LEAD()-matched successor has
-- an earlier timestamp than the event itself, which is only possible from out-of-order data
-- (the real BPI 2019 log has known timestamp anomalies -- see docs/data-contract.md and
-- src/cleaning/clean_events.py's own out-of-order handling) or a bug in int_stage_durations.
-- Same principle as fct_cases.cycle_time_hours (already checked not_null in _staging.yml at
-- the stg_process_cases layer) applied one level lower, at the stage-transition grain.
select *
from {{ ref('int_stage_durations') }}
where duration_hours < 0
