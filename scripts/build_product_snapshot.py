"""Build reports/product_snapshot.json (the data behind /app) from the local event log -- no database needed.

Run: PYTHONPATH=. python -m scripts.build_product_snapshot   (needs RAW_EVENT_LOG_PATH in .env)
"""
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.api.app_routes import SNAPSHOT_PATH
from src.api.product import build_product_data
from src.cleaning.clean_events import clean_events
from src.controls.ap_controls import run_all_controls
from src.ingestion.load_event_log import load_event_log
from src.transformation.build_process_cases import build_process_cases


def main():
    load_dotenv()
    events, _ = clean_events(load_event_log(os.environ["RAW_EVENT_LOG_PATH"]))
    cases = build_process_cases(events)
    data = build_product_data(cases, events, run_all_controls(events))
    data["snapshot"] = {"live": False, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "source": "local event log via the same build_product_data() as the live path"}
    SNAPSHOT_PATH.write_text(json.dumps(data, default=str, separators=(",", ":")), encoding="utf-8")
    print("wrote", SNAPSHOT_PATH, f"{SNAPSHOT_PATH.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
