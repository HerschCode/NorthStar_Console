"""Runs every AP control (src/controls/ap_controls.py) against the real cleaned event log and
writes the exceptions to analytics.ap_control_exceptions (idempotent replace -- same pattern as
scripts/simulate_interventions.py: DELETE the previous real run's rows, then append).

Run: python -m scripts.run_ap_controls
"""
import json
import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.cleaning.clean_events import clean_events
from src.controls.ap_controls import load_config, run_all_controls
from src.ingestion.load_event_log import load_event_log


def main() -> int:
    load_dotenv()
    events = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(events)
    cfg = load_config()

    exceptions = run_all_controls(cleaned, cfg)
    print(f"{len(exceptions)} exceptions across {exceptions['control_id'].nunique() if len(exceptions) else 0} controls")
    if len(exceptions):
        print(exceptions["control_id"].value_counts().to_string())

    out = exceptions.copy()
    if len(out):
        out["evidence"] = out["evidence"].map(json.dumps)

    url = (f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@{os.environ['DB_HOST']}:"
           f"{os.environ['DB_PORT']}/{os.environ['DB_NAME']}?sslmode={os.environ.get('DB_SSLMODE', 'prefer')}")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM analytics.ap_control_exceptions"))
    if len(out):
        out.to_sql("ap_control_exceptions", engine, schema="analytics", if_exists="append", index=False)
    print("loaded into analytics.ap_control_exceptions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
