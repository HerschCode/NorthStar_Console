-- Daily grain: one row per (category, day) -- what a dashboard's "SLA trend" chart would query
-- directly, so it never has to re-aggregate fct_cases on every page load.
select
    date_trunc('day', start_time) as day,
    category,
    count(*) as case_count,
    sum(case when sla_breach then 1 else 0 end) as breach_count,
    round(100.0 * sum(case when sla_breach then 1 else 0 end) / nullif(count(sla_breach), 0), 1) as breach_rate_pct,
    round(avg(cycle_time_hours)::numeric, 1) as avg_cycle_hours
from {{ ref('fct_cases') }}
where category is not null
group by 1, 2
