-- Phase 6: intervention ledger. One row per action taken on a case flagged as high-risk.
-- is_simulated = TRUE rows come from scripts/simulate_interventions.py (a policy replay over
-- held-out cases), never from real operations; /roi/summary always reports the two separately.
CREATE TABLE IF NOT EXISTS analytics.interventions (
    intervention_id    BIGSERIAL PRIMARY KEY,
    case_id            TEXT NOT NULL,   -- no FK: the pipeline rebuilds process_cases with to_sql(replace), which drops its PK;
                                -- POST /interventions checks the case exists instead
    intervention_type  TEXT NOT NULL,
    applied_at         TIMESTAMP NOT NULL DEFAULT now(),
    risk_at_intervention NUMERIC NOT NULL CHECK (risk_at_intervention BETWEEN 0 AND 1),
    cost               NUMERIC NOT NULL CHECK (cost >= 0),
    breached_after     BOOLEAN,          -- NULL until the case's outcome is known
    is_simulated       BOOLEAN NOT NULL DEFAULT FALSE,
    notes              TEXT,
    UNIQUE (case_id, intervention_type)
);

CREATE INDEX IF NOT EXISTS idx_interventions_simulated ON analytics.interventions (is_simulated);
