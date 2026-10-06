-- Stage-to-stage duration, ranked by average -- the SQL equivalent of
-- src/analytics/bottlenecks.py + cycle_time.py's stage_durations(). LEAD() gets each
-- event's next event within the same case, ordered by timestamp -- the same window
-- function pattern discussed since the very first SQL planning notes for this project.
WITH stage_durations AS (
    SELECT
        case_id,
        activity || ' -> ' || LEAD(activity) OVER (PARTITION BY case_id ORDER BY timestamp) AS stage,
        EXTRACT(EPOCH FROM (
            LEAD(timestamp) OVER (PARTITION BY case_id ORDER BY timestamp) - timestamp
        )) / 3600.0 AS duration_hours
    FROM staging.events
)
SELECT
    stage,
    COUNT(*)                                                        AS case_count,
    ROUND(AVG(duration_hours)::numeric, 3)                          AS avg_hours,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_hours)::numeric, 3) AS median_hours,
    ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY duration_hours)::numeric, 3) AS p90_hours
FROM stage_durations
WHERE stage IS NOT NULL
GROUP BY stage
-- Sample-size floor: on the real BPI 2019 data, a stage transition only 1-2 cases
-- ever go through can average tens of thousands of hours from a single real
-- anomalous case, crowding out systemic bottlenecks that actually affect many
-- cases -- the same fix applied in src/analytics/bottlenecks.py's
-- identify_bottlenecks(min_case_count=5), kept in sync here since this query is
-- the SQL equivalent of that function.
HAVING COUNT(*) >= 5
ORDER BY avg_hours DESC;
