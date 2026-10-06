-- Rolling 28-day SLA breach rate PER CATEGORY, using a RANGE window frame over an actual time
-- interval (not ROWS -- ROWS BETWEEN 27 PRECEDING would give the last 28 *rows*, which is wrong
-- whenever case volume isn't perfectly uniform day to day; RANGE ... INTERVAL '27 days'
-- PRECEDING gives the last 28 *calendar days* regardless of how many cases fall in them).
--
-- Segmentation note: this project's data has no "business unit" field. `category` is the real
-- segmentation column this project uses throughout (docs/data-contract.md) -- a synthetic field
-- standing in for the same kind of grouping a business unit would provide. Named here as
-- `category` rather than aliased to "business_unit" so the query doesn't imply a column that
-- doesn't exist.
--
-- One row per case, each showing the breach rate of ITS OWN category's trailing 28-day
-- cohort -- PARTITION BY category keeps one category's cases from bleeding into another's
-- rolling window, which a single global window (an earlier version of this file) would do.
SELECT
    case_id,
    category,
    start_time,
    sla_breach,
    ROUND(
        AVG(CASE WHEN sla_breach THEN 1.0 ELSE 0.0 END) OVER (
            PARTITION BY category ORDER BY start_time
            RANGE BETWEEN INTERVAL '27 days' PRECEDING AND CURRENT ROW
        )::numeric, 3
    ) AS trailing_28d_breach_rate,
    COUNT(*) OVER (
        PARTITION BY category ORDER BY start_time
        RANGE BETWEEN INTERVAL '27 days' PRECEDING AND CURRENT ROW
    ) AS trailing_28d_case_count
FROM (
    SELECT
        pc.case_id, pc.category, pc.start_time,
        pc.cycle_time_hours > COALESCE(r.target_hours, 240) AS sla_breach
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules r ON r.category = pc.category
    WHERE pc.category IS NOT NULL
) scored
ORDER BY category, start_time
LIMIT 40;
