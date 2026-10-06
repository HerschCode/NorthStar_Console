-- 1:1 with analytics.process_cases -- this project's already-computed case-level rollup
-- (src/transformation/build_process_cases.py's output, loaded by scripts/run_pipeline.py).
select
    case_id,
    first_activity,
    last_activity,
    event_count,
    start_time,
    end_time,
    cycle_time_hours,
    supplier_id,
    purchase_order_id,
    category,
    variant,
    variant_frequency
from {{ source('raw_analytics', 'process_cases') }}
