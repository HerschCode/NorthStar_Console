CREATE TABLE IF NOT EXISTS analytics.pipeline_runs (
    run_id           SERIAL PRIMARY KEY,
    started_at       TIMESTAMP NOT NULL,
    finished_at      TIMESTAMP,
    status           TEXT NOT NULL DEFAULT 'running',  -- running | success | failed
    source_path      TEXT NOT NULL,
    raw_row_count    INTEGER,
    cleaned_row_count INTEGER,
    case_count       INTEGER,
    error_message    TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at ON analytics.pipeline_runs(started_at);
