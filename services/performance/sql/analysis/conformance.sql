-- Partial SQL conformance check -- stated honestly, not overclaimed. This covers
-- what's genuinely straightforward in portable SQL: repeated-activity detection
-- (rework) and case-level activity counts. The full conformance logic in
-- src/analytics/conformance.py (skipped-step, unexpected-activity, AND
-- order-sequence comparison against config/process.yaml's expected_sequence) is
-- meaningfully harder to express correctly in SQL than in Python -- sequence-order
-- comparison against a dynamic, config-driven expected list is exactly the kind of
-- logic procedural code handles more safely than a single SQL statement, and forcing
-- it into SQL here would risk a second implementation drifting from the Python one
-- (the same "one source of truth" principle applied elsewhere in this project, e.g.
-- operations-assistant's tools calling this project's API rather than re-deriving
-- values). Use this query for a quick repeated-activity signal in a SQL client;
-- use GET /metrics/conformance (wraps the real Python logic) for the authoritative
-- conformance rate and deviation breakdown.
SELECT
    case_id,
    activity,
    COUNT(*) AS occurrence_count
FROM staging.events
GROUP BY case_id, activity
HAVING COUNT(*) > 1
ORDER BY case_id, occurrence_count DESC;
