-- Distinct process paths and their frequency/performance, matching
-- src/analytics/process_variants.py:variant_summary. `variant` is precomputed during
-- ETL (build_process_cases.py) as the ordered activity sequence joined with " -> ".
SELECT
    variant,
    COUNT(*)                                  AS case_count,
    ROUND(AVG(cycle_time_hours)::numeric, 2)  AS avg_cycle_time_hours,
    ROUND(
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cycle_time_hours)::numeric, 2
    ) AS median_cycle_time_hours
FROM analytics.process_cases
GROUP BY variant
ORDER BY case_count DESC;
