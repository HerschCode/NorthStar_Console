-- Rework flag/count per case -- the dbt-model version of sql/analysis/rework_detection.sql's
-- CTE, using a window ROW_NUMBER (not a self-join) to detect repeated activities within a case.
with activity_occurrences as (
    select
        case_id,
        activity,
        row_number() over (partition by case_id, activity order by timestamp) as occurrence_no
    from {{ ref('stg_events') }}
)
select
    case_id,
    count(*) filter (where occurrence_no > 1) as rework_event_count,
    count(distinct activity) filter (where occurrence_no > 1) as reworked_activity_count,
    count(*) filter (where occurrence_no > 1) > 0 as has_rework
from activity_occurrences
group by case_id
