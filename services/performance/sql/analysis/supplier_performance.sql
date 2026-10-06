-- Supplier scorecard, matching src/analytics/supplier_analysis.py:supplier_scorecard.
-- HAVING enforces the same min-volume threshold that function's min_volume=5 default
-- does, in code -- keep this literal `5` in sync with that default if it changes.
WITH sla_eval AS (
    SELECT
        pc.*,
        COALESCE(sr.target_hours, 240) AS target_hours
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules sr ON pc.category = sr.category
)
SELECT
    supplier_id,
    COUNT(*)                                                            AS order_count,
    ROUND(AVG(cycle_time_hours)::numeric, 2)                            AS avg_cycle_time_hours,
    ROUND(STDDEV(cycle_time_hours)::numeric, 2)                         AS cycle_time_std_hours,
    ROUND(
        AVG(CASE WHEN cycle_time_hours > target_hours THEN 1.0 ELSE 0.0 END)::numeric, 3
    ) AS sla_breach_rate
FROM sla_eval
GROUP BY supplier_id
HAVING COUNT(*) >= 5
ORDER BY avg_cycle_time_hours;
