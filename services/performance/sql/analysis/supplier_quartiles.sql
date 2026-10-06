-- Supplier performance quartiles via NTILE(4) and PERCENT_RANK() -- two different window
-- functions answering related but distinct questions: NTILE assigns each supplier to a
-- fixed bucket (useful for "which suppliers are in our worst quartile, review them first");
-- PERCENT_RANK gives a continuous 0-1 position (useful for a scorecard that shouldn't jump
-- discretely at a bucket boundary). min_orders filters out suppliers with too few cases for
-- their average to mean anything -- the same principle as bottlenecks.sql's
-- HAVING COUNT(*) >= 5, applied here to suppliers instead of process stages.
WITH supplier_stats AS (
    SELECT
        supplier_id,
        COUNT(*)                              AS order_count,
        ROUND(AVG(cycle_time_hours)::numeric, 1) AS avg_cycle_hours
    FROM analytics.process_cases
    WHERE supplier_id IS NOT NULL
    GROUP BY supplier_id
    HAVING COUNT(*) >= 3
)
SELECT
    supplier_id,
    order_count,
    avg_cycle_hours,
    NTILE(4) OVER (ORDER BY avg_cycle_hours DESC)       AS slowest_quartile,  -- 1 = slowest
    ROUND(
        PERCENT_RANK() OVER (ORDER BY avg_cycle_hours DESC)::numeric, 3
    ) AS slowness_percentile
FROM supplier_stats
ORDER BY avg_cycle_hours DESC;
