-- Least-privilege DB roles. Run once per environment (not idempotent for CREATE ROLE
-- since Postgres has no IF NOT EXISTS for roles pre-v16 in all cases; wrapped in DO
-- blocks below for portability).
--
-- Three roles instead of everyone using the same superuser/owner credentials:
--   ops_pipeline_writer -- scripts/run_pipeline.py, scripts/setup_database.py.
--                          Can create/alter schema and write data. This is the only
--                          role that should ever run DDL or INSERT/UPDATE/DELETE.
--   ops_api_reader       -- src/api/ (the FastAPI service) and the dashboard/BI tool.
--                          SELECT-only. The API never writes to the database, so it
--                          should not be able to, even by accident from a bug.
--   ops_readonly_report  -- a narrower reporting role, in case a BI tool needs a
--                          connection that can't see pipeline_runs (operational
--                          metadata, not business data a report consumer needs).

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ops_pipeline_writer') THEN
        CREATE ROLE ops_pipeline_writer WITH LOGIN PASSWORD :'pipeline_writer_password';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ops_api_reader') THEN
        CREATE ROLE ops_api_reader WITH LOGIN PASSWORD :'api_reader_password';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ops_readonly_report') THEN
        CREATE ROLE ops_readonly_report WITH LOGIN PASSWORD :'readonly_report_password';
    END IF;
END
$$;

-- Pipeline writer: full read/write on staging + analytics schemas
GRANT USAGE, CREATE ON SCHEMA staging, analytics TO ops_pipeline_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA staging, analytics TO ops_pipeline_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA staging, analytics
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ops_pipeline_writer;

-- API reader: read-only on both schemas, including pipeline_runs (the observability
-- endpoint at GET /observability/pipeline-runs needs this)
GRANT USAGE ON SCHEMA staging, analytics TO ops_api_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA staging, analytics TO ops_api_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA staging, analytics
    GRANT SELECT ON TABLES TO ops_api_reader;

-- Reporting role: read-only on analytics only, and explicitly NOT on pipeline_runs --
-- a BI tool building a business dashboard has no reason to see pipeline operational
-- metadata, and narrowing this is cheap.
GRANT USAGE ON SCHEMA analytics TO ops_readonly_report;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO ops_readonly_report;
REVOKE SELECT ON analytics.pipeline_runs FROM ops_readonly_report;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
    GRANT SELECT ON TABLES TO ops_readonly_report;
