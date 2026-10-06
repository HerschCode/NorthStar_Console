-- Stage-to-stage duration per case, via LEAD() -- the dbt-model version of
-- sql/analysis/bottlenecks.sql's CTE, reused as a real intermediate model instead of copied
-- inline into every mart that needs it.
select
    case_id,
    activity || ' -> ' || lead(activity) over (partition by case_id order by timestamp) as stage,
    extract(epoch from (
        lead(timestamp) over (partition by case_id order by timestamp) - timestamp
    )) / 3600.0 as duration_hours
from {{ ref('stg_events') }}
