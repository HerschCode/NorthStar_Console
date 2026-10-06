-- FIRST_VALUE / LAST_VALUE to pull each case's single slowest and fastest stage transition
-- onto every row, without a self-join or a second GROUP BY -- an ordered-window pull rather
-- than an aggregate. LAST_VALUE needs an explicit frame clause
-- (ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING); the default frame for an
-- ORDER BY window is "up to the current row", which would silently make LAST_VALUE return
-- the CURRENT row's own value instead of the true last one -- a well-known Postgres trap,
-- avoided here on purpose rather than by accident.
WITH stage_durations AS (
    SELECT
        case_id,
        activity || ' -> ' || LEAD(activity) OVER (PARTITION BY case_id ORDER BY timestamp) AS stage,
        EXTRACT(EPOCH FROM (
            LEAD(timestamp) OVER (PARTITION BY case_id ORDER BY timestamp) - timestamp
        )) / 3600.0 AS duration_hours
    FROM staging.events
),
ranked AS (
    SELECT
        case_id, stage, duration_hours,
        FIRST_VALUE(stage) OVER w                                                    AS slowest_stage,
        FIRST_VALUE(duration_hours) OVER w                                           AS slowest_hours,
        LAST_VALUE(stage) OVER (
            PARTITION BY case_id ORDER BY duration_hours
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        )                                                                            AS fastest_stage
    FROM stage_durations
    WHERE stage IS NOT NULL
    WINDOW w AS (
        PARTITION BY case_id ORDER BY duration_hours DESC
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )
)
SELECT DISTINCT case_id, slowest_stage, ROUND(slowest_hours::numeric, 2) AS slowest_hours, fastest_stage
FROM ranked
ORDER BY slowest_hours DESC
LIMIT 20;
