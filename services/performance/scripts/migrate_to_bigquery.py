"""
Postgres -> BigQuery migration for the analytics layer. Run after scripts/run_pipeline.py
has populated Postgres. Reads via pandas/SQLAlchemy, writes via the BigQuery client's
native load_table_from_dataframe (which handles schema inference + the actual upload,
rather than round-tripping through CSV).

Requires GOOGLE_APPLICATION_CREDENTIALS pointing at a service account key with BigQuery
Data Editor + Job User roles, and GCP_PROJECT_ID / BQ_DATASET_STAGING / BQ_DATASET_ANALYTICS
set in .env.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from google.cloud import bigquery
import pandas as pd


def get_pg_engine():
    url = (
        f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
    )
    return create_engine(url)


def migrate_table(bq_client: bigquery.Client, df: pd.DataFrame, table_id: str, write_disposition: str = "WRITE_TRUNCATE"):
    job_config = bigquery.LoadJobConfig(
        write_disposition=write_disposition,
        autodetect=True,
    )
    job = bq_client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()  # blocks until the load finishes
    table = bq_client.get_table(table_id)
    print(f"  loaded {table.num_rows:,} rows into {table_id}")


def run():
    load_dotenv()
    pg_engine = get_pg_engine()
    bq_client = bigquery.Client(project=os.environ["GCP_PROJECT_ID"])

    staging_ds = os.environ.get("BQ_DATASET_STAGING", "operations_performance_staging")
    analytics_ds = os.environ.get("BQ_DATASET_ANALYTICS", "operations_performance_analytics")
    project = os.environ["GCP_PROJECT_ID"]

    print("1/2 Migrating staging.events...")
    events = pd.read_sql("SELECT * FROM staging.events", pg_engine)
    migrate_table(bq_client, events, f"{project}.{staging_ds}.events")

    print("2/2 Migrating analytics.process_cases...")
    cases = pd.read_sql("SELECT * FROM analytics.process_cases", pg_engine)
    migrate_table(bq_client, cases, f"{project}.{analytics_ds}.process_cases")

    print("\nMigration complete. Apply cloud/bigquery/schema.sql's PARTITION BY / CLUSTER BY "
          "definitions via `bq query` or the console if you need those on tables created this "
          "way -- load_table_from_dataframe with autodetect creates a plain table, not a "
          "partitioned one, unless the target table already exists with that DDL applied first.")


if __name__ == "__main__":
    run()
