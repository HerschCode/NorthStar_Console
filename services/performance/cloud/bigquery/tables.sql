-- Analytical views mirroring the sql/analysis/ queries, rewritten for BigQuery SQL
-- (Postgres and BigQuery both use standard-ish SQL, but window function syntax,
-- date functions, and STRING/STRUCT handling differ enough to be worth writing
-- these out explicitly rather than assuming they'd just work unchanged).

CREATE OR REPLACE VIEW `operations_performance_analytics.v_cycle_time_summary` AS
SELECT
    COUNT(*) AS case_count,
    AVG(cycle_time_hours) AS mean_hours,
    APPROX_QUANTILES(cycle_time_hours, 100)[OFFSET(50)] AS median_hours,
    APPROX_QUANTILES(cycle_time_hours, 100)[OFFSET(90)] AS p90_hours,
    APPROX_QUANTILES(cycle_time_hours, 100)[OFFSET(99)] AS p99_hours
FROM `operations_performance_analytics.process_cases`;

-- BigQuery has no built-in exact PERCENTILE_CONT the way Postgres does by default in
-- older versions; APPROX_QUANTILES is the standard, cost-aware BigQuery approach and is
-- accurate enough for this use case -- worth being able to explain this substitution
-- specifically, since it's a genuine Postgres-vs-BigQuery difference, not a stylistic one.

CREATE OR REPLACE VIEW `operations_performance_analytics.v_sla_breach_by_category` AS
SELECT
    category,
    COUNT(*) AS case_count,
    COUNTIF(cycle_time_hours > sla_target_hours) AS breach_count,
    ROUND(COUNTIF(cycle_time_hours > sla_target_hours) / COUNT(*) * 100, 1) AS breach_rate_pct
FROM (
    SELECT
        pc.*,
        COALESCE(sr.target_hours, 240) AS sla_target_hours
    FROM `operations_performance_analytics.process_cases` pc
    LEFT JOIN `operations_performance_analytics.sla_rules` sr
        ON pc.category = sr.category
)
GROUP BY category
ORDER BY breach_rate_pct DESC;

CREATE OR REPLACE VIEW `operations_performance_analytics.v_supplier_scorecard` AS
SELECT
    supplier_id,
    COUNT(*) AS order_count,
    AVG(cycle_time_hours) AS avg_cycle_time_hours,
    STDDEV(cycle_time_hours) AS cycle_time_std_hours
FROM `operations_performance_analytics.process_cases`
GROUP BY supplier_id
HAVING COUNT(*) >= 5  -- same min-volume threshold as supplier_analysis.py, kept consistent
ORDER BY avg_cycle_time_hours;
