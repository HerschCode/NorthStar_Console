CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS staging.events (
    case_id           TEXT NOT NULL,
    activity          TEXT NOT NULL,
    timestamp         TIMESTAMP NOT NULL,
    resource          TEXT,
    purchase_order_id TEXT,
    item_id           TEXT,
    category          TEXT,
    supplier_id       TEXT
);

CREATE TABLE IF NOT EXISTS analytics.process_cases (
    case_id           TEXT PRIMARY KEY,
    first_activity    TEXT,
    last_activity     TEXT,
    event_count       INTEGER,
    start_time        TIMESTAMP,
    end_time          TIMESTAMP,
    cycle_time_hours  NUMERIC,
    supplier_id       TEXT,
    category          TEXT,
    variant           TEXT,
    variant_frequency INTEGER
);

CREATE TABLE IF NOT EXISTS analytics.suppliers (
    supplier_id    TEXT PRIMARY KEY,
    supplier_name  TEXT,
    region         TEXT,
    supplier_tier  TEXT
);

CREATE TABLE IF NOT EXISTS analytics.sla_rules (
    category      TEXT PRIMARY KEY,
    target_hours  NUMERIC NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.sla_predictions (
    case_id             TEXT PRIMARY KEY REFERENCES analytics.process_cases(case_id),
    predicted_at        TIMESTAMP NOT NULL DEFAULT now(),
    breach_probability  NUMERIC NOT NULL,
    risk_level          TEXT NOT NULL,
    top_factors         JSONB
);
