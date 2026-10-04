"""Routes for the Northstar product shell: GET /app (page) and GET /app/data (one JSON payload for every view).

Unauthenticated like the original dashboard (aggregate, non-sensitive analytics). The payload is built from the
database when reachable and otherwise comes from the committed reports/product_snapshot.json, labelled as a snapshot.
"""
import json
import time
from pathlib import Path

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from src.api.product import ROOT, build_product_data

router = APIRouter()
SNAPSHOT_PATH = ROOT / "reports" / "product_snapshot.json"
_CACHE_TTL_SECONDS = 300
_cache: dict = {"data": None, "at": 0.0}


def load_product_snapshot(path: Path = SNAPSHOT_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = data.get("snapshot") or {}
    meta["live"] = False
    data["snapshot"] = meta
    return data


def _compute() -> dict:
    from src.api import db

    try:
        cases, events = db.load_cases(), db.load_events()
    except Exception as exc:
        snap = load_product_snapshot()
        if snap is not None:
            snap["snapshot"]["reason"] = str(exc)[:200]
            return snap
        return {"available": False, "reason": str(exc)}
    try:
        ap = pd.read_sql("select control_id, severity, exposure_eur from analytics.ap_control_exceptions", db.get_engine())
        ap = ap if not ap.empty else None
    except Exception:
        ap = None
    data = build_product_data(cases, events, ap)
    data["snapshot"] = {"live": True}
    return data


@router.get("/app/data")
def app_data():
    now = time.monotonic()
    if _cache["data"] is None or now - _cache["at"] > _CACHE_TTL_SECONDS:
        _cache["data"] = _compute()
        _cache["at"] = now
    return _cache["data"]


@router.get("/app", response_class=HTMLResponse, include_in_schema=False)
def app_page() -> str:
    return (Path(__file__).parent / "app.html").read_text(encoding="utf-8")
