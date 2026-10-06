"""
A real, built dashboard -- closing the gap `dashboard/README.md` documented as a
Power BI/Looker build spec that was never actually built (a `.pbix` file can't live in
this repo or be inspected by anyone without that tool installed). Serves the same
`dashboard/README.md` pages (Executive Overview, Process Performance/Bottlenecks,
Supplier Performance, Conformance, SLA Risk) as one FastAPI-rendered page instead,
against the exact same `src/analytics`/`src/ml` functions the REST API and CLI reports
already use -- no separate "dashboard logic" that could drift from what `/metrics/*`
returns.

Deliberately unauthenticated (like operations-assistant's /demo/chat) -- a portfolio
visitor viewing aggregate, non-sensitive analytics shouldn't need an API key. Each
section degrades independently (a missing model or empty dataset shows that section's
own "not available" state) rather than one failure blanking the whole page.
"""
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.db import load_events, load_cases
from src.analytics.cycle_time import cycle_time_percentiles
from src.analytics.bottlenecks import identify_bottlenecks
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla, sla_summary
from src.analytics.supplier_analysis import supplier_scorecard
from src.analytics.conformance import check_conformance, conformance_report
from src.ml.predict import load_model, predict_sla_risk
from src.ml.features import build_features
from src.ml.explain import explain_shap_batch
from src.api.db import get_engine
from src.api.snapshot import load_snapshot
from src.controls.ap_controls import load_config as load_ap_config
from src.analytics.working_capital import working_capital_summary

router = APIRouter()


def _section(fn, *args):
    """Runs one dashboard section's data-gathering function and normalizes its
    outcome to {"available": bool, ...} -- so one section failing (no model trained
    yet, empty table, missing config) shows as that section's own empty state on the
    page instead of a 500 that blanks every other section too."""
    try:
        data = fn(*args)
        return {"available": True, **data}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _executive_overview(cases):
    if cases.empty:
        raise ValueError("No process cases loaded yet")
    evaluated = evaluate_sla(cases, load_sla_targets())
    summary = sla_summary(evaluated)
    total_breach = int(summary["breach_count"].sum())
    total_cases = int(summary["case_count"].sum())
    cycle = cycle_time_percentiles(cases)
    return {
        "case_count": total_cases,
        "mean_cycle_hours": round(cycle["mean_hours"], 1),
        "median_cycle_hours": round(cycle["median_hours"], 1),
        "p90_cycle_hours": round(cycle["p90_hours"], 1),
        "breach_rate_pct": round((total_breach / total_cases * 100) if total_cases else 0, 1),
    }


def _bottlenecks(events):
    if events.empty:
        raise ValueError("No events loaded yet")
    result = identify_bottlenecks(events, top_n=8)
    return {"stages": result.to_dict(orient="records")}


def _suppliers(cases):
    if "supplier_id" not in cases.columns:
        raise ValueError("No supplier_id column in this dataset")
    evaluated = evaluate_sla(cases, load_sla_targets())
    scorecard = supplier_scorecard(evaluated, min_volume=3)
    return {"suppliers": scorecard.head(8).to_dict(orient="records")}


def _conformance(events):
    if events.empty:
        raise ValueError("No events loaded yet")
    result = check_conformance(events)
    report = conformance_report(result)
    return {
        "conformance_rate_pct": report["conformance_rate_pct"],
        "total_cases": report["total_cases"],
        "conformant_cases": report["conformant_cases"],
        "deviation_breakdown": report["deviation_breakdown"],
    }


def _humanize_feature(name: str) -> str:
    simple = {
        "unique_activity_count": "Process complexity (many unique steps)",
        "event_count": "High event count",
        "rework_count": "Repeated activities (rework)",
        "supplier_historical_breach_rate": "Supplier breach history",
        "supplier_historical_median_cycle_time": "Slow supplier cycle time",
        "sla_target_hours": "Tight SLA deadline",
        "variant_frequency": "Unusual process path",
        "start_hour": "Unusual start hour",
        "start_dayofweek": "Day-of-week pattern",
        "start_month": "Seasonal timing",
        "start_quarter": "Quarterly timing",
    }
    if name in simple:
        return simple[name]
    if name.startswith("last_activity_"):
        return f"Final stage: {name[14:]}"
    if name.startswith("first_activity_"):
        return f"Opened as: {name[15:]}"
    if name.startswith("supplier_id_"):
        return "Supplier risk profile"
    if name.startswith("category_"):
        return f"Category: {name[9:]}"
    return name.replace("_", " ").title()


def _top_risk_cases(cases):
    if cases.empty:
        raise ValueError("No process cases loaded yet")
    evaluated = evaluate_sla(cases, load_sla_targets())
    X, _ = build_features(evaluated)
    bundle = load_model()
    predictions = predict_sla_risk(X, bundle)

    meta = evaluated[["case_id"]].copy()
    if "supplier_id" in evaluated.columns:
        meta["supplier_id"] = evaluated["supplier_id"].astype(str)
    else:
        meta["supplier_id"] = "—"
    meta = meta.reset_index(drop=True)

    result = pd.concat([meta, predictions.reset_index(drop=True)], axis=1)
    high_risk = result[result["risk_level"] == "HIGH"].nlargest(8, "breach_probability")

    if high_risk.empty:
        return {"cases": []}

    X_aligned = X.reindex(columns=bundle["columns"], fill_value=0).reset_index(drop=True)
    high_risk_positions = list(high_risk.index)
    X_high = X_aligned.iloc[high_risk_positions]

    # Real SHAP values — signed per-instance contributions, not global importance
    shap_explanations = explain_shap_batch(X_high, bundle, top_n=5)

    output = []
    for idx, (pos, shap_factors) in enumerate(zip(high_risk_positions, shap_explanations)):
        row = high_risk.loc[pos]
        top_reason = "High breach probability"
        shap_display = []
        if shap_factors:
            top_reason = _humanize_feature(shap_factors[0]["feature"])
            shap_display = [
                {
                    "label": _humanize_feature(f["feature"]),
                    "shap": f["shap"],
                    "direction": f["direction"],
                }
                for f in shap_factors
            ]
        output.append({
            "case_id": str(row["case_id"]),
            "supplier_id": str(row.get("supplier_id", "—")),
            "breach_probability": int(round(float(row["breach_probability"]) * 100)),
            "risk_level": str(row["risk_level"]),
            "top_reason": top_reason,
            "shap_factors": shap_display,
        })

    return {"cases": output}


def _sla_risk(cases):
    if cases.empty:
        raise ValueError("No process cases loaded yet")
    evaluated = evaluate_sla(cases, load_sla_targets())
    X, _ = build_features(evaluated)
    bundle = load_model()  # raises FileNotFoundError if untrained -- caught by _section
    predictions = predict_sla_risk(X, bundle)
    counts = predictions["risk_level"].value_counts()
    return {
        "total_scored": len(predictions),
        "low": int(counts.get("LOW", 0)),
        "medium": int(counts.get("MEDIUM", 0)),
        "high": int(counts.get("HIGH", 0)),
    }


def _ap_controls(df=None):
    """Top-line AP controls summary for the dashboard: real exception counts + exposure, per
    control, straight from analytics.ap_control_exceptions (docs/ap-controls.md). Labelled
    anomaly triage, not fraud, same as the API's /controls/summary."""
    if df is None:
        df = pd.read_sql("select control_id, severity, exposure_eur from analytics.ap_control_exceptions", get_engine())
    if df.empty:
        raise ValueError("No AP control run yet -- run scripts.run_ap_controls")
    rows = []
    for control_id, g in df.groupby("control_id"):
        rows.append({"control_id": control_id, "count": int(len(g)),
                     "total_exposure_eur": round(float(g["exposure_eur"].fillna(0).sum()), 2),
                     "high": int((g["severity"] == "high").sum()), "medium": int((g["severity"] == "medium").sum()),
                     "low": int((g["severity"] == "low").sum())})
    return {"total_exceptions": int(len(df)), "controls": sorted(rows, key=lambda r: r["count"], reverse=True)}


def _working_capital(events):
    cfg = load_ap_config()
    wc = working_capital_summary(events, cfg)
    return {"late_payment": wc["late_payment"], "early_discount_scenario": wc["early_discount_scenario"],
            "dpo_by_spend_area": wc["dpo_by_spend_area"][:8]}


_CACHE_TTL_SECONDS = 120
_cache: dict = {"data": None, "computed_at": 0.0}


def _compute_dashboard_data() -> dict:
    # Each of load_cases()/load_events() is a full-table pull that, against this
    # project's actual free-tier Neon deployment, costs real tens-of-seconds -- see
    # the docstring on dashboard_data() below. Loading each table exactly ONCE and
    # sharing it across every section that needs it (three sections need cases, two
    # need events) cuts this from 5 round trips to 2, not 5 separate ones, which is
    # the difference between a slow-but-tolerable first load and a multi-minute one.
    # Loaded outside the per-section try/except: if the DB itself is unreachable,
    # every section should show that failure, not attempt 5 identical failing calls.
    cases_error = events_error = None
    try:
        cases = load_cases()
    except Exception as exc:
        cases = None
        cases_error = str(exc)
    try:
        events = load_events()
    except Exception as exc:
        events = None
        events_error = str(exc)

    if cases is None and events is None:
        # Database unreachable (e.g. free-tier Neon quota/suspend): serve the last committed snapshot,
        # clearly labelled, instead of an empty page. See scripts/build_dashboard_snapshot.py.
        snap = load_snapshot()
        if snap is not None:
            return snap
    return build_sections(cases, events, cases_error=cases_error, events_error=events_error)


def build_sections(cases, events, ap_df=None, cases_error=None, events_error=None) -> dict:
    """Every dashboard section from already-loaded frames (the live path and the snapshot builder
    share this, so a snapshot can never drift from what the live page computes)."""
    def cases_section(fn):
        if cases is None:
            return {"available": False, "reason": cases_error}
        return _section(fn, cases)

    def events_section(fn):
        if events is None:
            return {"available": False, "reason": events_error}
        return _section(fn, events)

    return {
        "executive_overview": cases_section(_executive_overview),
        "bottlenecks": events_section(_bottlenecks),
        "suppliers": cases_section(_suppliers),
        "conformance": events_section(_conformance),
        "sla_risk": cases_section(_sla_risk),
        "top_risk_cases": cases_section(_top_risk_cases),
        "ap_controls": _section(_ap_controls, ap_df) if ap_df is not None else _section(_ap_controls),
        "working_capital": events_section(_working_capital),
    }


@router.get("/dashboard/data")
def dashboard_data():
    # A hosted free-tier Postgres (Neon, this project's actual deployment) has real,
    # non-trivial per-query latency on a full-table pull -- confirmed directly: a
    # plain `SELECT * FROM staging.events` on this project's 32K-row real BPI 2019
    # table took 60+ seconds against Neon's pooled endpoint, consistent with the
    # pipeline's own load step taking 104s for 3K rows (see operations-performance's
    # PLAN.md). Five sections each independently re-querying that same data would
    # make every dashboard page load take over a minute; a short server-side cache
    # is the honest fix here, not a workaround -- this data changes when the
    # pipeline reruns (hourly/daily at most), not per-request.
    now = time.monotonic()
    if _cache["data"] is None or (now - _cache["computed_at"]) > _CACHE_TTL_SECONDS:
        _cache["data"] = _compute_dashboard_data()
        _cache["computed_at"] = now
    return _cache["data"]


def _dashboard_html() -> str:
    return (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


def _console_url() -> str | None:
    """CONSOLE_URL, when it is a real http(s) address. Anything else (unset, a javascript: or relative value) is ignored, so a bad value cannot turn / into an arbitrary redirect."""
    url = (os.environ.get("CONSOLE_URL") or "").strip()
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else None


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def root_page():
    """With CONSOLE_URL set, the Northstar console (its own repository) is the front door and / redirects to it. Otherwise this is the single-file Northstar
    shell (src/api/app.html, also kept at /app); the original single-page dashboard stays at /dashboard."""
    console = _console_url()
    if console:
        return RedirectResponse(console, status_code=302)
    return (Path(__file__).parent / "app.html").read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page():
    return _dashboard_html()
