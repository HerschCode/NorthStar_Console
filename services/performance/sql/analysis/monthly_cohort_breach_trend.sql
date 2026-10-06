-- Cohort analysis: group cases by the calendar month they STARTED (their cohort), and for
-- each cohort compute a running (cumulative) breach rate as later months are added, using
-- SUM/COUNT as window aggregates ordered by cohort_month -- a "how is our SLA performance
-- trending, cumulatively, cohort over cohort" view, distinct from the per-case rolling
-- window in rolling_sla_breach_rate.sql (that one is case-grained and time-windowed; this
-- one is cohort-grained and cumulative).
WITH cohorts AS (
    SELECT
        DATE_TRUNC('month', pc.start_time) AS cohort_month,
        COUNT(*)                                                        AS cases_in_cohort,
        SUM((pc.cycle_time_hours > COALESCE(r.target_hours, 240))::int) AS breaches_in_cohort
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules r ON r.category = pc.category
    GROUP BY DATE_TRUNC('month', pc.start_time)
)
SELECT
    cohort_month,
    cases_in_cohort,
    breaches_in_cohort,
    ROUND(100.0 * breaches_in_cohort / NULLIF(cases_in_cohort, 0), 1)                  AS cohort_breach_rate_pct,
    SUM(cases_in_cohort)    OVER (ORDER BY cohort_month)                              AS cumulative_cases,
    SUM(breaches_in_cohort) OVER (ORDER BY cohort_month)                              AS cumulative_breaches,
    ROUND(
        100.0 * SUM(breaches_in_cohort) OVER (ORDER BY cohort_month)
              / NULLIF(SUM(cases_in_cohort) OVER (ORDER BY cohort_month), 0), 1
    ) AS cumulative_breach_rate_pct
FROM cohorts
ORDER BY cohort_month;
