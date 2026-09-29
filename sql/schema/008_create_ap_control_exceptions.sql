-- Finance module Phase F2: one row per AP control exception. Computed by
-- scripts/run_ap_controls.py (src/controls/ap_controls.py) -- pandas rolling-window, fuzzy
-- duplicate matching and a chi-square test aren't reasonably expressed in portable SQL, so this
-- table is Python-computed and dbt-exposed (dbt/models/staging/stg_ap_control_exceptions.sql is a
-- documented passthrough over it, not a SQL reimplementation of the logic). No FK to
-- process_cases for the same reason process_cases itself has none (sql/schema/005, the pipeline's
-- to_sql replace drops PKs) -- checked against live case_ids at write time instead.
CREATE TABLE IF NOT EXISTS analytics.ap_control_exceptions (
    exception_id  BIGSERIAL PRIMARY KEY,
    case_id       TEXT,               -- NULL for vendor-level exceptions (C6 Benford)
    control_id    TEXT NOT NULL,
    severity      TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    exposure_eur  NUMERIC,
    evidence      JSONB NOT NULL,
    generated_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ap_exceptions_control ON analytics.ap_control_exceptions (control_id);
CREATE INDEX IF NOT EXISTS idx_ap_exceptions_case ON analytics.ap_control_exceptions (case_id);
