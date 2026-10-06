-- SLA-breach lift by candidate driver (category, supplier), matching
-- src/analytics/root_causes.py:sla_breach_drivers. "Lift" = this segment's breach
-- rate minus the overall breach rate -- reports association, not causation, same
-- caveat as the Python version and docs/analytical-methodology.md.
WITH sla_eval AS (
    SELECT
        pc.*,
        COALESCE(sr.target_hours, 240) AS target_hours,
        CASE WHEN pc.cycle_time_hours > COALESCE(sr.target_hours, 240) THEN 1.0 ELSE 0.0 END AS breached
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules sr ON pc.category = sr.category
),
overall AS (
    SELECT AVG(breached) AS overall_rate FROM sla_eval
)
SELECT
    'category'                                       AS driver,
    category                                          AS level,
    COUNT(*)                                          AS case_count,
    ROUND(AVG(breached)::numeric, 3)                  AS breach_rate,
    ROUND((AVG(breached) - (SELECT overall_rate FROM overall))::numeric, 3) AS lift_vs_overall
FROM sla_eval
GROUP BY category
HAVING COUNT(*) >= 3

UNION ALL

SELECT
    'supplier_id'                                     AS driver,
    supplier_id                                        AS level,
    COUNT(*)                                           AS case_count,
    ROUND(AVG(breached)::numeric, 3)                   AS breach_rate,
    ROUND((AVG(breached) - (SELECT overall_rate FROM overall))::numeric, 3) AS lift_vs_overall
FROM sla_eval
GROUP BY supplier_id
HAVING COUNT(*) >= 3

ORDER BY lift_vs_overall DESC;
