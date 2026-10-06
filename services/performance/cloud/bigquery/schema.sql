-- BigQuery equivalent of sql/schema/001_create_tables.sql + 003_create_pipeline_runs.sql.
-- Key differences from Postgres, all deliberate -- see cloud/README.md for why:
--   * no PRIMARY KEY / FOREIGN KEY enforcement (BigQuery doesn't enforce these; kept in
--     comments as documentation of intent, not as constraints the engine checks)
--   * PARTITION BY on the date-ish column of each large table, so queries that filter by
--     date only scan the relevant partitions instead of the whole table (this is where
--     BigQuery cost control actually happens)
--   * CLUSTER BY on the columns most queries filter/group by, for further pruning within
--     a partition

CREATE SCHEMA IF NOT EXISTS `operations_performance_staging`;
CREATE SCHEMA IF NOT EXISTS `operations_performance_analytics`;

CREATE TABLE IF NOT EXISTS `operations_performance_staging.events` (
    case_id            STRING NOT NULL,
    activity           STRING NOT NULL,
    timestamp          TIMESTAMP NOT NULL,
    resource           STRING,
    purchase_order_id  STRING,
    item_id            STRING,
    category           STRING,
    supplier_id        STRING
)
PARTITION BY DATE(timestamp)
CLUSTER BY case_id, category;

CREATE TABLE IF NOT EXISTS `operations_performance_analytics.process_cases` (
    case_id            STRING NOT NULL,  -- logical PK, not enforced by BigQuery
    first_activity     STRING,
    last_activity      STRING,
    event_count        INT64,
    start_time         TIMESTAMP,
    end_time           TIMESTAMP,
    cycle_time_hours   FLOAT64,
    supplier_id        STRING,
    category           STRING,
    variant            STRING,
    variant_frequency  INT64
)
PARTITION BY DATE(start_time)
CLUSTER BY supplier_id, category;

CREATE TABLE IF NOT EXISTS `operations_performance_analytics.suppliers` (
    supplier_id    STRING NOT NULL,
    supplier_name  STRING,
    region         STRING,
    supplier_tier  STRING
);

CREATE TABLE IF NOT EXISTS `operations_performance_analytics.sla_rules` (
    category      STRING NOT NULL,
    target_hours  FLOAT64 NOT NULL
);

CREATE TABLE IF NOT EXISTS `operations_performance_analytics.sla_predictions` (
    case_id             STRING NOT NULL,  -- logical FK to process_cases, not enforced
    predicted_at        TIMESTAMP NOT NULL,
    breach_probability  FLOAT64 NOT NULL,
    risk_level          STRING NOT NULL,
    top_factors         JSON
)
PARTITION BY DATE(predicted_at);

CREATE TABLE IF NOT EXISTS `operations_performance_analytics.pipeline_runs` (
    run_id             INT64,
    started_at         TIMESTAMP NOT NULL,
    finished_at        TIMESTAMP,
    status             STRING NOT NULL,
    source_path        STRING NOT NULL,
    raw_row_count      INT64,
    cleaned_row_count  INT64,
    case_count         INT64,
    error_message      STRING
)
PARTITION BY DATE(started_at);
