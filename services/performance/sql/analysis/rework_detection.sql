-- Business question: which activities get repeated within a case (rework -- a returned
-- goods receipt re-done, an invoice re-entered, a payment block re-cleared), and how much
-- extra cycle time does rework correlate with? src/analytics/rework.py answers this in
-- pandas already; this is the SQL equivalent, using a window COUNT (not a self-join --
-- a self-join on case_id would multiply rows quadratically per case for no benefit here,
-- since "was this activity seen before in this case" is a running count, not a pairwise
-- comparison between specific rows).
WITH activity_occurrences AS (
    SELECT
        case_id,
        activity,
        timestamp,
        ROW_NUMBER() OVER (PARTITION BY case_id, activity ORDER BY timestamp) AS occurrence_no
    FROM staging.events
),
rework_flags AS (
    SELECT
        case_id,
        COUNT(*) FILTER (WHERE occurrence_no > 1) AS rework_event_count,
        COUNT(DISTINCT activity) FILTER (WHERE occurrence_no > 1) AS reworked_activity_count
    FROM activity_occurrences
    GROUP BY case_id
)
SELECT
    r.rework_event_count > 0                                      AS has_rework,
    COUNT(*)                                                       AS case_count,
    ROUND(AVG(pc.cycle_time_hours)::numeric, 1)                    AS avg_cycle_hours,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pc.cycle_time_hours)::numeric, 1) AS median_cycle_hours
FROM rework_flags r
JOIN analytics.process_cases pc ON pc.case_id = r.case_id
GROUP BY (r.rework_event_count > 0)
ORDER BY has_rework;
