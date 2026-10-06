-- Running (cumulative) distinct-activity count within each case, per event.
--
-- Postgres has no COUNT(DISTINCT ...) OVER (...) -- "DISTINCT is not implemented for window
-- functions" -- caught by actually running this against live data, not assumed from the
-- other window-function examples in this project. A window FILTER doesn't fix it either:
-- FILTER only restricts which frame rows are counted using THAT row's own columns, it can't
-- compare a frame row against the current row's value, which is what "distinct activities
-- seen up to this point" needs. The correct tool here is a correlated subquery, not a
-- window function -- included specifically to show that distinction, not to avoid it.
--
-- ROW_NUMBER() for event_seq IS a real window function, kept for genuinely gap-free,
-- 1-indexed ordering (a COUNT(*) OVER (...) would work too here but ROW_NUMBER is the more
-- direct tool when ties in timestamp shouldn't collapse into one sequence position).
SELECT
    e1.case_id,
    e1.activity,
    e1.timestamp,
    ROW_NUMBER() OVER (PARTITION BY e1.case_id ORDER BY e1.timestamp) AS event_seq,
    (
        SELECT COUNT(DISTINCT e2.activity)
        FROM staging.events e2
        WHERE e2.case_id = e1.case_id AND e2.timestamp <= e1.timestamp
    ) AS distinct_activities_so_far
FROM staging.events e1
WHERE e1.case_id = (SELECT case_id FROM staging.events GROUP BY case_id ORDER BY COUNT(*) DESC LIMIT 1)
ORDER BY e1.timestamp;
