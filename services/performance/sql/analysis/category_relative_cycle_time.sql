-- Where does each case's cycle time sit relative to its OWN category's distribution, not the
-- global one? PERCENT_RANK() partitioned by category answers "is this case slow for a
-- Consignment order specifically", which a single global percentile (cycle_time.sql) can't --
-- a Consignment order's normal cycle time may be a 3-way match order's fast one. Also
-- reports each case's z-score against its category using the window AVG/STDDEV pair, so a
-- case can be flagged as an outlier by two different statistical definitions side by side.
SELECT
    case_id,
    category,
    ROUND(cycle_time_hours::numeric, 1)                                                        AS cycle_time_hours,
    ROUND(
        PERCENT_RANK() OVER (PARTITION BY category ORDER BY cycle_time_hours)::numeric, 3
    ) AS pct_rank_within_category,
    ROUND(
        (
            (cycle_time_hours - AVG(cycle_time_hours) OVER (PARTITION BY category))
            / NULLIF(STDDEV(cycle_time_hours) OVER (PARTITION BY category), 0)
        )::numeric,
    2) AS z_score_within_category
FROM analytics.process_cases
WHERE category IS NOT NULL
ORDER BY pct_rank_within_category DESC
LIMIT 20;
