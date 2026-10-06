import os
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        # Prefers a dedicated read-only API credential (API_DB_USER/API_DB_PASSWORD --
        # should be the ops_api_reader role from sql/schema/004_create_roles.sql) over
        # the pipeline's write credential, so the API can never write to the database
        # even if a bug in this code tried to. Falls back to DB_USER/DB_PASSWORD for
        # simple local dev where roles haven't been set up yet.
        user = os.environ.get("API_DB_USER", os.environ["DB_USER"])
        password = os.environ.get("API_DB_PASSWORD", os.environ["DB_PASSWORD"])
        # sslmode defaults to "prefer" (psycopg2's own default, safe for a plain local
        # Postgres with no SSL configured) but a hosted instance like Neon requires SSL
        # -- set DB_SSLMODE=require for those rather than hardcoding one assumption.
        sslmode = os.environ.get("DB_SSLMODE", "prefer")
        url = (
            f"postgresql+psycopg2://{user}:{password}"
            f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
            f"?sslmode={sslmode}"
        )
        # pool_pre_ping: a hosted serverless Postgres (Neon, this deployment's target)
        # auto-suspends its compute after a period of no traffic -- a connection sitting
        # idle in SQLAlchemy's pool across that suspend/resume becomes stale, and reusing
        # it raises PendingRollbackError rather than transparently reconnecting. Found by
        # an actual live call landing after enough idle time for Neon to have suspended,
        # not by any test (every test here mocks the DB layer entirely). pre_ping issues
        # a cheap liveness check before handing out a pooled connection and silently
        # replaces it if the check fails, which is the standard fix for exactly this.
        _engine = create_engine(url, pool_size=5, pool_timeout=30, pool_pre_ping=True)
    return _engine


_write_engine: Engine | None = None


def get_write_engine() -> Engine:
    """Separate engine for the one write path the API has (POST /interventions). The normal engine
    prefers the SELECT-only ops_api_reader role, which cannot INSERT by design; a ledger write needs
    a role with INSERT on analytics.interventions -- LEDGER_DB_USER/LEDGER_DB_PASSWORD, falling back
    to DB_USER/DB_PASSWORD for simple local/dev setups."""
    global _write_engine
    if _write_engine is None:
        user = os.environ.get("LEDGER_DB_USER", os.environ["DB_USER"])
        password = os.environ.get("LEDGER_DB_PASSWORD", os.environ["DB_PASSWORD"])
        url = (f"postgresql+psycopg2://{user}:{password}"
               f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
               f"?sslmode={os.environ.get('DB_SSLMODE', 'prefer')}")
        _write_engine = create_engine(url, pool_size=2, pool_timeout=30, pool_pre_ping=True)
    return _write_engine


def load_events() -> pd.DataFrame:
    engine = get_engine()
    df = pd.read_sql("SELECT * FROM staging.events", engine)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def load_case_events(case_id: str) -> pd.DataFrame:
    """One case's events only (the early-risk endpoint does not need the whole event table)."""
    from sqlalchemy import text

    df = pd.read_sql(text("SELECT case_id, activity, timestamp FROM staging.events WHERE case_id = :c"),
                     get_engine(), params={"c": case_id})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def load_cases() -> pd.DataFrame:
    engine = get_engine()
    df = pd.read_sql("SELECT * FROM analytics.process_cases", engine)
    df["start_time"] = pd.to_datetime(df["start_time"])
    df["end_time"] = pd.to_datetime(df["end_time"])
    return df


def check_connection() -> bool:
    try:
        get_engine().connect().close()
        return True
    except Exception:
        return False
