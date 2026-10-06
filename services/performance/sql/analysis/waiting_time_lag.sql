-- Waiting time before each activity, using LAG() instead of bottlenecks.sql's LEAD().
-- LEAD looks forward from the current event to the next one (stage duration, "how long did
-- THIS step take"); LAG looks backward to the previous event ("how long did the case wait
-- before THIS step started"). Same window, opposite direction, genuinely different question:
-- this answers "which activities make callers wait the longest before they even start",
-- LEAD answers "which stage transitions themselves take the longest".
WITH waits AS (
    SELECT
        case_id,
        activity,
        timestamp,
        EXTRACT(EPOCH FROM (
            timestamp - LAG(timestamp) OVER (PARTITION BY case_id ORDER BY timestamp)
        )) / 3600.0 AS wait_hours
    FROM staging.events
)
SELECT
    activity,
    COUNT(*)                                                            AS occurrences,
    ROUND(AVG(wait_hours)::numeric, 3)                                  AS avg_wait_hours,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wait_hours)::numeric, 3) AS median_wait_hours
FROM waits
WHERE wait_hours IS NOT NULL  -- excludes each case's first event, which has no prior activity to wait behind
GROUP BY activity
HAVING COUNT(*) >= 5
ORDER BY avg_wait_hours DESC;
