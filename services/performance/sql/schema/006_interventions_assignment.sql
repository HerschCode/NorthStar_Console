-- Upgrade 3: randomized holdout. Each ledger row records which experiment arm the case was assigned to by
-- src/roi/randomizer.py (deterministic hash of experiment_id + case_id). 'holdout' rows are cases the team
-- must NOT treat: they are logged with cost 0 so their outcomes can be compared with the treated arm.
ALTER TABLE analytics.interventions
    ADD COLUMN IF NOT EXISTS assignment TEXT NOT NULL DEFAULT 'treat' CHECK (assignment IN ('treat', 'holdout'));
ALTER TABLE analytics.interventions ADD COLUMN IF NOT EXISTS experiment_id TEXT;
