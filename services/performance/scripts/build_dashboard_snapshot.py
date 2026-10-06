"""Build reports/dashboard_snapshot.json from the local event log (no database needed).

Run: PYTHONPATH=. python -m scripts.build_dashboard_snapshot   (needs RAW_EVENT_LOG_PATH in .env)
Same pipeline as src/ml/train.py, then the dashboard's own build_sections().
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.api.dashboard import build_sections
from src.api.snapshot import SNAPSHOT_PATH
from src.cleaning.clean_events import clean_events
from src.controls.ap_controls import run_all_controls
from src.ingestion.load_event_log import load_event_log
from src.transformation.build_process_cases import build_process_cases


def main():
    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    events, _ = clean_events(raw)
    cases = build_process_cases(events)
    ap = run_all_controls(events)
    sections = build_sections(cases, events, ap_df=ap)
    sections["snapshot"] = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "local event log via the same pipeline and section functions as the live dashboard",
        "n_cases": int(len(cases)), "n_events": int(len(events)),
    }
    Path(SNAPSHOT_PATH).write_text(json.dumps(sections, indent=1, default=str), encoding="utf-8")
    print({k: v.get("available") if isinstance(v, dict) and "available" in v else v for k, v in sections.items()})


if __name__ == "__main__":
    main()
