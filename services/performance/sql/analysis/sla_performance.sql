-- SLA breach rate, overall and by category. Targets come from analytics.sla_rules
-- (see sql/schema/001_create_tables.sql); a case whose category isn't in that table
-- falls back to the 240-hour default, matching config/sla.yaml's Python-side default
-- (src/analytics/sla_analysis.py:load_sla_targets) -- keep these two in sync manually.
WITH sla_targets AS (
    SELECT
        pc.case_id,
        pc.cycle_time_hours,
        pc.category,
        COALESCE(sr.target_hours, 240) AS target_hours
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules sr ON pc.category = sr.category
)
SELECT
    category,
    COUNT(*)                                                          AS case_count,
    SUM(CASE WHEN cycle_time_hours > target_hours THEN 1 ELSE 0 END)  AS breach_count,
    ROUND(
        100.0 * SUM(CASE WHEN cycle_time_hours > target_hours THEN 1 ELSE 0 END) / COUNT(*),
        1
    ) AS breach_rate_pct
FROM sla_targets
GROUP BY ROLLUP(category)  -- ROLLUP adds an overall (category IS NULL) row alongside per-category rows
ORDER BY category NULLS FIRST;
