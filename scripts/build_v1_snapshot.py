"""Build reports/v1_snapshot/*.json: one file per /v1 endpoint for the default replay clock, from the local event log
(no database). The router serves these when the database is unreachable.

Run: PYTHONPATH=. python -m scripts.build_v1_snapshot   (needs RAW_EVENT_LOG_PATH in .env)
Also exports the OpenAPI contract to openapi/p1.json (the console generates its typed client from it).
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.replay.clock import DEFAULT_AS_OF
from src.v1 import build

OUT = Path("reports/v1_snapshot")
CASES_IN_SNAPSHOT = 120
SUPPLIERS_IN_SNAPSHOT = 80


def _dump(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, default=str, separators=(",", ":")), encoding="utf-8")


def build_context():
    from src.cleaning.clean_events import clean_events
    from src.controls.ap_controls import run_all_controls
    from src.ingestion.load_event_log import load_event_log
    from src.transformation.build_process_cases import build_process_cases

    load_dotenv()
    events, _ = clean_events(load_event_log(os.environ["RAW_EVENT_LOG_PATH"]))
    cases = build_process_cases(events)
    return build.Context(cases, events, run_all_controls(events))


def main():
    ctx = build_context()
    t = DEFAULT_AS_OF
    stamp = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "local event log, same build functions as the live API"}
    q = build.queue(ctx, t, limit=500)
    parts = {
        "overview": build.overview(ctx, t), "queue": q, "suppliers": build.suppliers(ctx, t, min_n=5),
        "process_flow_stage": build.process_flow(ctx, t, "median", "stage"), "process_flow_activity": build.process_flow(ctx, t, "median", "activity"),
        "process_variants": build.process_variants(ctx, t), "risk_map": build.risk_map(ctx, t),
        "finance_controls": build.finance_controls(ctx, t), "finance_exceptions": build.finance_exceptions(ctx),
        "finance_working_capital": build.finance_working_capital(ctx), "interventions_roi": build.interventions_roi(ctx),
        "models": build.models(ctx), "data_quality": build.data_quality(ctx), "lineage": build.lineage(ctx), "briefing": build.briefing(ctx, t),
    }
    for name, data in parts.items():
        data["snapshot"] = dict(stamp)
        _dump(name, data)
    top_cases = [r["case_id"] for r in q["rows"][:CASES_IN_SNAPSHOT]]
    _dump("cases", {cid: build.case_360(ctx, cid, t) for cid in top_cases})
    sup_ids = [r["supplier_id"] for r in parts["suppliers"]["rows"][:SUPPLIERS_IN_SNAPSHOT]]
    _dump("supplier_360", {sid: build.supplier_360(ctx, sid, t) for sid in sup_ids})
    # search is live-only; no snapshot (a missing snapshot returns 503 with a clear message)
    from src.api.main import app

    Path("openapi").mkdir(exist_ok=True)
    spec = {**app.openapi()}
    spec["paths"] = {k: v for k, v in spec["paths"].items() if k.startswith("/v1")}
    Path("openapi/p1.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    print("snapshot files:", sorted(p.name for p in OUT.glob("*.json")))


if __name__ == "__main__":
    main()
