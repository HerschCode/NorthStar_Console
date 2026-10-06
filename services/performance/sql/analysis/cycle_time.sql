-- Cycle time distribution across all cases. Python equivalent: src/analytics/cycle_time.py
SELECT
    COUNT(*)                                                        AS case_count,
    ROUND(AVG(cycle_time_hours)::numeric, 2)                        AS mean_hours,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cycle_time_hours)::numeric, 2)  AS median_hours,
    ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY cycle_time_hours)::numeric, 2)  AS p90_hours,
    ROUND(PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY cycle_time_hours)::numeric, 2) AS p99_hours
FROM analytics.process_cases;
