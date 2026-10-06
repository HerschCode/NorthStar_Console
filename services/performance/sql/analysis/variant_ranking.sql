-- Business question: which process variants (distinct activity sequences) show up most often,
-- and separately, which ones have the worst SLA breach rate -- these are two different rankings
-- of the same rows, and a variant can rank very differently on each (a rare variant can have a
-- terrible breach rate; a common one can be perfectly healthy). Reporting only one would hide
-- that distinction, so this file runs two statements, not one.
--
-- Top 10 by volume: RANK() (not ROW_NUMBER -- ties matter: two variants with the identical case
-- count should share a rank, not be arbitrarily split by insertion order) and DENSE_RANK() for a
-- gap-free ranking alongside it, so the difference between the two is visible in one result set.
-- The rank is computed in a subquery and filtered in the outer WHERE -- a window function's
-- own result can't be referenced in the WHERE/HAVING of the SAME SELECT it's computed in
-- (Postgres has no QUALIFY clause, unlike Snowflake/BigQuery -- checked by trying it and
-- getting a syntax error, not assumed).
SELECT * FROM (
    SELECT
        pc.category,
        pc.variant,
        COUNT(*)                                                                        AS case_count,
        SUM((pc.cycle_time_hours > COALESCE(r.target_hours, 240))::int)                 AS breach_count,
        RANK()       OVER (PARTITION BY pc.category ORDER BY COUNT(*) DESC)             AS rank_by_volume,
        DENSE_RANK() OVER (PARTITION BY pc.category ORDER BY COUNT(*) DESC)             AS dense_rank_by_volume,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY pc.category), 1)      AS pct_of_category_cases
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules r ON r.category = pc.category
    WHERE pc.category IS NOT NULL
    GROUP BY pc.category, pc.variant
) ranked
WHERE rank_by_volume <= 10
ORDER BY category, rank_by_volume;

-- Top 10 by breach rate, minimum 3 cases so a single unlucky case doesn't produce a 100% "rate"
-- (same min-volume principle as bottlenecks.sql's HAVING COUNT(*) >= 5).
SELECT * FROM (
    SELECT
        pc.category,
        pc.variant,
        COUNT(*)                                                                   AS case_count,
        SUM((pc.cycle_time_hours > COALESCE(r.target_hours, 240))::int)            AS breach_count,
        ROUND(
            100.0 * SUM((pc.cycle_time_hours > COALESCE(r.target_hours, 240))::int)
            / NULLIF(COUNT(*), 0), 1
        ) AS breach_rate_pct,
        RANK() OVER (
            PARTITION BY pc.category
            ORDER BY SUM((pc.cycle_time_hours > COALESCE(r.target_hours, 240))::int)::numeric
                     / NULLIF(COUNT(*), 0) DESC
        ) AS rank_by_breach_rate
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules r ON r.category = pc.category
    WHERE pc.category IS NOT NULL
    GROUP BY pc.category, pc.variant
    HAVING COUNT(*) >= 3
) ranked
WHERE rank_by_breach_rate <= 10
ORDER BY category, rank_by_breach_rate;
