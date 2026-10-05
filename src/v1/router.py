"""Versioned read API for the Northstar console: GET /v1/...

Public and read-only in demo mode (aggregate, non-sensitive replay data). Every endpoint takes `as_of` (the replay
clock, default src/replay/clock.DEFAULT_AS_OF). When the database is unreachable each endpoint falls back to the
committed snapshot in reports/v1_snapshot/ (default clock only) and says so in a `snapshot` field.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.replay.clock import DEFAULT_AS_OF
from src.v1 import build

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["v1"])
SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "reports" / "v1_snapshot"
_CTX: dict = {"ctx": None, "at": 0.0}
_CTX_TTL = 600
DEFAULT_AS_OF_STR = str(DEFAULT_AS_OF.date())


class Metric(BaseModel):
    """Every number the API returns about the business is this shape (see build.metric)."""
    value: float | int | None
    unit: str
    provenance: str = Field(description="measured | simulated | experimental | pending | descriptive")
    source: str
    as_of: str | None = None
    n: int | None = None
    note: str | None = None


def get_context() -> build.Context:
    now = time.monotonic()
    if _CTX["ctx"] is not None and now - _CTX["at"] < _CTX_TTL:
        return _CTX["ctx"]
    from src.api import db

    cases, events = db.load_cases(), db.load_events()
    try:
        ap = pd.read_sql("select * from analytics.ap_control_exceptions", db.get_engine())
        ap = ap if not ap.empty else None
    except Exception:
        ap = None
    _CTX.update(ctx=build.Context(cases, events, ap), at=now)
    return _CTX["ctx"]


def _snapshot(name: str, key: str | None = None):
    try:
        data = json.loads((SNAPSHOT_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if key is not None:
        data = data.get(key)
        if data is None:
            return None
    if isinstance(data, dict):
        data["snapshot"] = {"live": False, "built_for_as_of": DEFAULT_AS_OF_STR, **(data.get("snapshot") or {})}
    return data


def _filter_snapshot(name: str, data: dict, params: dict) -> dict:
    """Snapshots hold the default view; apply the cheap request filters so a snapshot answers the same question a live call would
    (a `limit=6` call must not return 500 rows)."""
    if name == "queue" and isinstance(data.get("rows"), list):
        rows = data["rows"]
        if params.get("min_value"):
            rows = [r for r in rows if (r.get("value_eur") or 0) >= params["min_value"]]
        if params.get("supplier"):
            rows = [r for r in rows if r.get("supplier_id") == params["supplier"]]
        if params.get("stage"):
            from src.v1.build import _yaml
            label = next((s["label"] for s in _yaml("config/stages.yaml")["stages"] if s["id"] == params["stage"]), None)
            rows = [r for r in rows if r.get("stage") == label]
        data = {**data, "total_matching": len(rows), "rows": rows[: int(params.get("limit") or 100)], "limit": int(params.get("limit") or 100)}
    return data


def _serve(request: Request, name: str, fn, *, key: str | None = None, default_clock_only: bool = True, **params):
    as_of = params.get("as_of")
    try:
        ctx = get_context()
    except Exception as exc:                                     # database unreachable
        if default_clock_only and as_of is not None and str(pd.Timestamp(as_of).date()) != DEFAULT_AS_OF_STR:
            raise HTTPException(503, f"database unavailable and the snapshot only covers as_of={DEFAULT_AS_OF_STR}")
        snap = _snapshot(name, key)
        if snap is None:
            raise HTTPException(503 if key is None else 404, f"database unavailable and no snapshot for {name}{'/' + key if key else ''}")
        snap = _filter_snapshot(name, snap, params)
        snap.setdefault("snapshot", {})["reason"] = str(exc)[:160]
        return snap
    data = fn(ctx, **params)
    if data is None:
        raise HTTPException(404, "not found")
    if isinstance(data, dict):
        data["snapshot"] = {"live": True}
    return data


AsOf = Query(default=DEFAULT_AS_OF_STR, description="Replay clock (ISO date/time). Open cases and their features use only events up to this instant.")


@router.get("/overview")
def v1_overview(request: Request, as_of: str = AsOf):
    return _serve(request, "overview", build.overview, as_of=as_of)


@router.get("/queue")
def v1_queue(request: Request, as_of: str = AsOf, min_value: float = 0.0, supplier: str | None = None, stage: str | None = None,
             limit: int = Query(100, ge=1, le=500)):
    return _serve(request, "queue", build.queue, as_of=as_of, min_value=min_value, supplier=supplier, stage=stage, limit=limit)


@router.get("/cases/{case_id}")
def v1_case(request: Request, case_id: str, as_of: str = AsOf):
    return _serve(request, "cases", lambda ctx, **p: build.case_360(ctx, case_id, p["as_of"]), key=case_id, as_of=as_of)


@router.get("/suppliers")
def v1_suppliers(request: Request, as_of: str = AsOf, sort: str = "ci_lower", min_n: int = Query(20, ge=1)):
    return _serve(request, "suppliers", build.suppliers, as_of=as_of, sort=sort, min_n=min_n)


@router.get("/suppliers/{supplier_id}")
def v1_supplier(request: Request, supplier_id: str, as_of: str = AsOf):
    return _serve(request, "supplier_360", lambda ctx, **p: build.supplier_360(ctx, supplier_id, p["as_of"]), key=supplier_id, as_of=as_of)


@router.get("/process/flow")
def v1_flow(request: Request, as_of: str = AsOf, metric: str = Query("median", pattern="^(median|p75|p90|breach_contribution)$"),
            group: str = Query("stage", pattern="^(stage|activity)$")):
    return _serve(request, f"process_flow_{group}", lambda ctx, **p: build.process_flow(ctx, p["as_of"], metric, group), as_of=as_of)


@router.get("/process/variants")
def v1_variants(request: Request, as_of: str = AsOf):
    return _serve(request, "process_variants", build.process_variants, as_of=as_of)


@router.get("/risk-map")
def v1_risk_map(request: Request, as_of: str = AsOf):
    return _serve(request, "risk_map", build.risk_map, as_of=as_of)


@router.get("/finance/controls")
def v1_fin_controls(request: Request, as_of: str = AsOf):
    return _serve(request, "finance_controls", build.finance_controls, as_of=as_of)


@router.get("/finance/exceptions")
def v1_fin_exc(request: Request, control: str | None = None, min_exposure: float = 0.0):
    return _serve(request, "finance_exceptions", build.finance_exceptions, default_clock_only=False, control=control, min_exposure=min_exposure)


@router.get("/finance/working-capital")
def v1_fin_wc(request: Request):
    return _serve(request, "finance_working_capital", build.finance_working_capital, default_clock_only=False)


@router.get("/interventions/roi")
def v1_roi(request: Request):
    return _serve(request, "interventions_roi", build.interventions_roi, default_clock_only=False)


@router.get("/models")
def v1_models(request: Request):
    return _serve(request, "models", build.models, default_clock_only=False)


@router.get("/data-quality")
def v1_dq(request: Request):
    return _serve(request, "data_quality", build.data_quality, default_clock_only=False)


@router.get("/lineage")
def v1_lineage(request: Request, metric: str | None = None):
    return _serve(request, "lineage", lambda ctx, **p: build.lineage(ctx, metric), default_clock_only=False)


@router.get("/experiments")
def v1_experiments():
    return build.experiments()


@router.get("/evidence")
def v1_evidence():
    return build.evidence()


@router.get("/briefing")
def v1_briefing(request: Request, as_of: str = AsOf):
    return _serve(request, "briefing", build.briefing, as_of=as_of)


@router.get("/search")
def v1_search(request: Request, q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=50)):
    return _serve(request, "search", lambda ctx, **p: build.search(ctx, q, limit), default_clock_only=False)


class OutcomeBody(BaseModel):
    breached_after: bool
    notes: str | None = Field(default=None, max_length=500)


def _write_outcome(intervention_id: int, body: OutcomeBody) -> bool:
    from sqlalchemy import text
    from src.api.db import get_write_engine

    with get_write_engine().begin() as conn:
        res = conn.execute(text("UPDATE analytics.interventions SET breached_after = :b, notes = COALESCE(:n, notes) "
                                "WHERE intervention_id = :i"), {"b": body.breached_after, "n": body.notes, "i": intervention_id})
    return res.rowcount > 0


from fastapi import Depends  # noqa: E402

from src.api.auth import require_api_key, require_role  # noqa: E402


@router.patch("/interventions/{intervention_id}/outcome", dependencies=[Depends(require_api_key), Depends(require_role("admin"))])
def v1_record_outcome(intervention_id: int, body: OutcomeBody):
    """Record whether the case breached after an intervention (admin key required). Outcomes on holdout rows are what
    turn the assumed effects into measured uplift; until some exist, /v1/interventions/roi reports only simulated results."""
    if not _write_outcome(intervention_id, body):
        raise HTTPException(404, f"No intervention with id {intervention_id}")
    return {"intervention_id": intervention_id, "breached_after": body.breached_after, "recorded": True}
