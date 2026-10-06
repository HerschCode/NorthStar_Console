"""Data layer for the Northstar product shell (src/api/app.html, served at /app).

Everything here is derived from the same frames as the original dashboard (cases, events, AP exceptions) plus the
committed evaluation reports. Nothing is invented: a view whose source data does not exist returns
{"available": False, "reason": ...} and the page says so. The BPI 2019 log is a *historical replay* -- every case
is already closed -- and the UI labels it that way.

build_product_data() is shared by the live path (frames from the database) and scripts/build_product_snapshot.py
(frames from the local event log), so the committed snapshot cannot drift from the live computation.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
QUEUE_SIZE = 150
SCATTER_SIZE = 700
MIN_SUPPLIER_CASES = 5
SUPPLIER_DETAIL_SIZE = 60


def _read_json(rel: str):
    try:
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _order_value(events: pd.DataFrame) -> pd.Series:
    if "net_worth_eur" not in events.columns:
        return pd.Series(dtype=float)
    return events.groupby("case_id")["net_worth_eur"].max()


def _pct(a, b):
    return None if not b else round(100.0 * a / b, 1)


def score_cases(evaluated: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    """Per-case model risk (probability, level) aligned to `evaluated` rows."""
    from src.ml.features import build_features
    from src.ml.predict import predict_sla_risk

    X, _ = build_features(evaluated)
    pred = predict_sla_risk(X, bundle).reset_index(drop=True)
    out = evaluated[["case_id"]].reset_index(drop=True).copy()
    out["risk"] = pred["breach_probability"].astype(float).values
    # The deployed model is trained on the configured SLA targets, under which ~95% of cases breach, so its absolute
    # HIGH/MEDIUM/LOW bands are not informative (almost everything is HIGH). The product therefore ranks cases by
    # model-score percentile and labels the tiers as relative priority, never as a probability of breach.
    pct = out["risk"].rank(pct=True, method="first")
    out["risk_level"] = np.where(pct > 0.98, "CRITICAL", np.where(pct > 0.90, "HIGH", np.where(pct > 0.75, "ELEVATED", "STANDARD")))
    out["score_percentile"] = (100 * pct).round(1)
    return out, X


def _monthly_trend(evaluated: pd.DataFrame) -> list[dict]:
    d = evaluated.dropna(subset=["end_time"]).copy()
    d["month"] = d["end_time"].dt.strftime("%Y-%m")
    g = d.groupby("month").agg(n=("sla_breach", "size"), breaches=("sla_breach", "sum"),
                               avg_cycle_days=("cycle_time_hours", lambda s: float(s.mean()) / 24.0))
    g = g[g["n"] >= 30]
    return [{"month": m, "n": int(r.n), "breach_rate": round(100 * r.breaches / r.n, 1),
             "avg_cycle_days": round(r.avg_cycle_days, 1)} for m, r in g.iterrows()]


def _period_delta(trend: list[dict], key: str):
    if len(trend) < 2:
        return None
    return round(trend[-1][key] - trend[-2][key], 1)


def _stage_share(bottlenecks: pd.DataFrame) -> list[dict]:
    rows = []
    # rare transitions (a dozen cases) top the raw average-delay ranking; rank by share of total delay among stages
    # that occur in at least 30 cases
    b = bottlenecks[bottlenecks["case_count"] >= 30].sort_values("pct_of_total_delay", ascending=False)
    for r in b.head(8).to_dict("records"):
        rows.append({"stage": r["stage"], "avg_hours": round(float(r["avg_hours"]), 1),
                     "median_hours": round(float(r["median_hours"]), 1), "p90_hours": round(float(r["p90_hours"]), 1),
                     "case_count": int(r["case_count"]), "pct_of_total_delay": round(float(r["pct_of_total_delay"]), 1)})
    return rows


def _process_flow(events: pd.DataFrame, breach_by_case: pd.Series, max_edges: int = 28) -> dict:
    ev = events.sort_values(["case_id", "timestamp"])[["case_id", "activity", "timestamp"]].copy()
    ev["next_activity"] = ev.groupby("case_id")["activity"].shift(-1)
    ev["next_ts"] = ev.groupby("case_id")["timestamp"].shift(-1)
    ev = ev.dropna(subset=["next_activity"])
    ev["hours"] = (ev["next_ts"] - ev["timestamp"]).dt.total_seconds() / 3600.0
    ev["breach"] = ev["case_id"].map(breach_by_case)
    edges = []
    for (a, b), g in ev.groupby(["activity", "next_activity"]):
        edges.append({"from": a, "to": b, "cases": int(g["case_id"].nunique()), "transitions": int(len(g)),
                      "median_hours": round(float(g["hours"].median()), 1),
                      "p75_hours": round(float(g["hours"].quantile(0.75)), 1),
                      "p90_hours": round(float(g["hours"].quantile(0.90)), 1),
                      "total_hours": round(float(g["hours"].sum()), 0),
                      "breach_rate": None if g["breach"].isna().all() else round(100 * float(g["breach"].mean()), 1)})
    edges.sort(key=lambda e: e["transitions"], reverse=True)
    edges = edges[:max_edges]
    names = {e["from"] for e in edges} | {e["to"] for e in edges}
    counts = events["activity"].value_counts()
    nodes = [{"id": n, "events": int(counts.get(n, 0))} for n in sorted(names, key=lambda n: -counts.get(n, 0))]
    return {"nodes": nodes, "edges": edges}


def _supplier_block(evaluated, scored, values, bundle_ok: bool) -> tuple[list[dict], dict]:
    d = evaluated.merge(scored[["case_id", "risk", "risk_level"]], on="case_id", how="left")
    d["value"] = d["case_id"].map(values).fillna(0.0)
    d["month"] = d["end_time"].dt.strftime("%Y-%m")
    rows, details = [], {}
    for sup, g in d.dropna(subset=["supplier_id"]).groupby("supplier_id"):
        if len(g) < MIN_SUPPLIER_CASES:
            continue
        br = float(g["sla_breach"].mean())
        row = {"supplier_id": str(sup), "cases": int(len(g)), "breach_rate": round(100 * br, 1),
               "avg_cycle_days": round(float(g["cycle_time_hours"].mean()) / 24, 1),
               "median_cycle_days": round(float(g["cycle_time_hours"].median()) / 24, 1),
               "total_value": round(float(g["value"].sum()), 0),
               "high_risk_cases": int(g["risk_level"].isin(["CRITICAL", "HIGH"]).sum()) if bundle_ok else None,
               "avg_risk": round(float(g["risk"].mean()), 3) if bundle_ok else None,
               "rework_avg": round(float(g["rework_count"].mean()), 2)}
        rows.append(row)
    peer_median = float(d["cycle_time_hours"].median()) / 24
    rows.sort(key=lambda r: (r["high_risk_cases"] or 0, r["total_value"]), reverse=True)
    for row in rows[:SUPPLIER_DETAIL_SIZE]:
        g = d[d["supplier_id"] == row["supplier_id"]]
        tr = g.groupby("month").agg(n=("sla_breach", "size"), b=("sla_breach", "mean")).reset_index()
        top = g.sort_values("risk", ascending=False).head(8)
        details[row["supplier_id"]] = {
            **row, "peer_median_cycle_days": round(peer_median, 1),
            "cycle_vs_peer": round(row["median_cycle_days"] / peer_median, 2) if peer_median else None,
            "trend": [{"month": r.month, "n": int(r.n), "breach_rate": round(100 * r.b, 1)} for r in tr.itertuples() if r.n >= 2],
            "top_cases": [{"case_id": str(r.case_id), "risk": round(float(r.risk), 3), "value": round(float(r.value), 0),
                           "level": r.risk_level} for r in top.itertuples()] if bundle_ok else [],
        }
    return rows, details


def _po_details(evaluated, scored, values, events, X, bundle, queue_ids: list[str]) -> dict:
    from src.api.dashboard import _humanize_feature
    from src.ml.explain import explain_shap_batch

    pos = scored.reset_index(drop=True)
    idx = [int(pos.index[pos["case_id"] == c][0]) for c in queue_ids if (pos["case_id"] == c).any()]
    Xa = X.reindex(columns=bundle["columns"], fill_value=0).reset_index(drop=True)
    shap = explain_shap_batch(Xa.iloc[idx], bundle, top_n=6)
    ev = events[events["case_id"].isin(queue_ids)].sort_values(["case_id", "timestamp"])
    ev_by = {c: g for c, g in ev.groupby("case_id")}
    ev_idx = evaluated.set_index("case_id")
    cat_median = evaluated.groupby("category")["cycle_time_hours"].median() if "category" in evaluated else {}
    out = {}
    for i, factors in zip(idx, shap):
        cid = str(pos.loc[i, "case_id"])
        row = ev_idx.loc[cid]
        g = ev_by.get(cid)
        timeline = []
        if g is not None:
            t0 = g["timestamp"].iloc[0]
            prev = t0
            for r in g.itertuples():
                timeline.append({"activity": r.activity, "at": r.timestamp.strftime("%Y-%m-%d %H:%M"),
                                 "day": round((r.timestamp - t0).total_seconds() / 86400, 1),
                                 "gap_hours": round((r.timestamp - prev).total_seconds() / 3600, 1)})
                prev = r.timestamp
        gaps = sorted(timeline[1:], key=lambda t: -t["gap_hours"])[:1]
        med = float(cat_median.get(row.get("category"), np.nan)) if len(cat_median) else float("nan")
        out[cid] = {
            "case_id": cid, "supplier_id": str(row.get("supplier_id")), "category": str(row.get("category")),
            "risk": round(float(pos.loc[i, "risk"]), 3), "level": str(pos.loc[i, "risk_level"]),
            "value": round(float(values.get(cid, 0.0)), 0),
            "cycle_days": round(float(row["cycle_time_hours"]) / 24, 1),
            "sla_target_days": round(float(row["sla_target_hours"]) / 24, 1),
            "breached": bool(row["sla_breach"]),
            "category_median_days": None if np.isnan(med) else round(med / 24, 1),
            "events": int(row["event_count"]), "rework": int(row["rework_count"]),
            "longest_wait": gaps[0] if gaps else None,
            "shap": [{"label": _humanize_feature(f["feature"]), "feature": f["feature"], "shap": f["shap"],
                      "direction": f["direction"]} for f in factors],
            "timeline": timeline,
        }
    return out


def _data_quality(evaluated, cases, events, sla_targets) -> dict:
    explicit = {k for k in sla_targets if k != "default"}
    cat = evaluated["category"] if "category" in evaluated else pd.Series(dtype=str)
    default_share = float((~cat.isin(explicit)).mean()) if len(cat) else None
    checks = [
        {"check": "Required case fields present", "status": "pass" if evaluated[["case_id", "start_time", "end_time"]].notna().all().all() else "fail"},
        {"check": "Duplicate case ids", "status": "pass" if cases["case_id"].is_unique else "fail"},
        {"check": "Supplier id present", "status": "pass" if evaluated["supplier_id"].notna().mean() > 0.99 else "warn",
         "detail": f"{100 * evaluated['supplier_id'].notna().mean():.1f}% of cases"},
        {"check": "Explicit SLA target coverage",
         "status": "warn" if default_share and default_share > 0.5 else "pass",
         "detail": f"{100 * (1 - default_share):.0f}% of cases have an explicit category SLA; "
                   f"{100 * default_share:.0f}% use the {sla_targets.get('default', 240)}h fallback"},
        {"check": "Unmeasurable (zero-duration) cases excluded", "status": "info",
         "detail": f"{len(cases) - len(evaluated)} of {len(cases)} cases dropped from SLA labels"},
    ]
    return {"cases": int(len(cases)), "events": int(len(events)), "suppliers": int(cases["supplier_id"].nunique()),
            "explicit_sla_share_pct": None if default_share is None else round(100 * (1 - default_share), 1),
            "checks": checks}


def _models() -> dict:
    meta = _read_json("models/sla_risk_model.meta.json")
    mvr = _read_json("reports/model_vs_rules.json")
    if not meta:
        return {"available": False, "reason": "model metadata not found"}
    served = meta.get("served_model") or {}
    out = {"available": True, "name": meta.get("model_name"), "trained_at": meta.get("trained_at"),
           "sklearn_version": meta.get("sklearn_version"), "calibration": meta.get("calibration_method"),
           "feature_code_sha256": (meta.get("feature_code_sha256") or "")[:12],
           "split": meta.get("split"),
           "served": {k: served.get(k) for k in ("roc_auc", "brier_score", "precision", "recall", "f1") if k in served},
           "validation": ["Forward-in-time split", "Purchase orders kept on one side of the split",
                          "Calibration fitted on a held-out slice", "Supplier history uses completed cases only",
                          "scikit-learn version pinned and recorded", "Feature-code hash checked by a test"]}
    try:
        from src.ml.feature_drift import compute_feature_drift
        out["drift_note"] = "PSI computed in /health/drift/features against the training baseline"
        out["has_baselines"] = bool(meta.get("feature_baselines"))
    except Exception:  # pragma: no cover
        pass
    if mvr:
        pk = mvr.get("precision_at_k", {})
        rows = []
        for name in ("M0_current", "order_value", "supplier_history_rule", "random"):
            for key, val in pk.items():
                if key == name or key.startswith(name) or (name == "supplier_history_rule" and "supplier" in key and "history" in key):
                    rows.append({"strategy": key, **{s: pk[key][s]["precision"] for s in pk[key]}})
                    break
        out["model_vs_rules"] = {"rows": rows, "roc_auc": mvr.get("roc_auc"), "n_test": mvr.get("n_test"),
                                 "base_rate": mvr.get("base_rate"),
                                 "verdict": "Model advantage over the order-value rule is not statistically established "
                                            "at any tested treated share (paired cluster-bootstrap CI includes 0)."}
    return out


def _finance(events, ap_df, section_fn) -> dict:
    from src.api.dashboard import _ap_controls, _working_capital

    ap = section_fn(_ap_controls, ap_df) if ap_df is not None else section_fn(_ap_controls)
    wc = section_fn(_working_capital, events)
    ev = _read_json("reports/ap_controls_evaluation.json") or {}
    ext = _read_json("reports/external_validation.json") or {}
    controls = []
    for cid, c in (ev.get("controls") or {}).items():
        controls.append({"id": cid, "recall": c.get("recall"), "recall_ci95": c.get("recall_ci95"),
                         "false_positive_rate_upper_bound": c.get("false_positive_rate"), "status": "operational"})
    benford_rate = (ext.get("retail") or {}).get("C6", {}).get("flag_rate_among_eligible")
    controls.append({"id": "C6_benford_deviation", "recall": None, "status": "not_valid",
                     "note": f"Flags ~{round(100 * benford_rate)}% of ordinary customers on external data (null 5%); "
                             "do not use for decisions." if benford_rate else "External validation not run."})
    return {"ap": ap, "working_capital": wc, "control_health": controls,
            "external_c4": (ext.get("retail") or {}).get("C4")}


def _evidence() -> dict:
    return {
        "claims": [
            {"claim": "The model beats a simple order-value rule", "status": "not_established",
             "evidence": "PO-isolated holdout, cluster bootstrap: paired CIs include 0 at 5-30% treated share (docs/model-vs-rules.md)."},
            {"claim": "A look-ahead leak inflated supplier-history features", "status": "found_and_fixed",
             "evidence": "History now uses only cases completed before the scored case starts; model retrained 2026-10-03."},
            {"claim": "Intervention ROI", "status": "simulation_only",
             "evidence": "Effects are assumptions; no real intervention outcomes recorded (docs/uplift-method.md)."},
            {"claim": "Benford screen finds suspicious invoices", "status": "refuted",
             "evidence": "Flags ~55% of ordinary external data (docs/external-validation.md)."},
            {"claim": "AP controls catch planted anomalies", "status": "established",
             "evidence": "Planted-anomaly evaluation: C1 97.5%, C4 80%, C3 66.7% recall (synthetic injections only)."},
            {"claim": "Reported ROC-AUC ~0.98", "status": "inflated",
             "evidence": "Default SLA target is ~97% breach; realistic p75 targets give 0.78-0.93."},
            {"claim": "Fraud detection", "status": "not_supported",
             "evidence": "Anomaly flags only; no ground-truth fraud labels exist in any dataset used."},
        ],
        "experiments": [
            {"name": "Isolation Forest vs rules (AP)", "result": "failed", "note": "Did not beat the rule-based controls."},
            {"name": "Benford first-digit screen", "result": "failed", "note": "~55% flag rate on ordinary data."},
            {"name": "Supplier workload features", "result": "no_gain", "note": "Did not beat rules at 5-10% treated."},
            {"name": "Isotonic vs Platt calibration", "result": "resolved", "note": "Held-out Platt kept; isotonic fitted on train rows had collapsed AUC."},
            {"name": "Duplicate invoice control on Online Retail II", "result": "promising", "note": "21x lift over base reversal rate (proxy label)."},
        ],
        "limitations": [
            "The event log is a closed historical replay: no live cases, so 'at risk' means the model's score on finished cases.",
            "Supplier and user ids are anonymised; there are no bank accounts or devices to link.",
            "Order value is the maximum net worth seen in the case, not an invoiced amount.",
            "Most cases use the fallback SLA target rather than an explicit one.",
            "Intervention effects are simulated assumptions.",
            "Held-out cases cluster by purchase order, so confidence intervals remain optimistic.",
        ],
    }


def _intervention_config() -> dict:
    import yaml

    try:
        raw = yaml.safe_load((ROOT / "config" / "interventions.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        return {"available": False}
    return {"available": True, "breach_cost": raw.get("breach_cost"), "capacity_pct": raw.get("capacity_pct"),
            "types": raw.get("types", {}),
            "label": "SIMULATION ASSUMPTIONS, NOT MEASURED EFFECTS (config/interventions.yaml)"}


LINEAGE = {
    "nodes": [
        {"id": "raw", "label": "Raw BPI 2019 event log", "layer": 0},
        {"id": "staging", "label": "staging.events (cleaned)", "layer": 1},
        {"id": "cases", "label": "analytics.process_cases (dbt)", "layer": 2},
        {"id": "marts", "label": "dbt_marts.fct_cases", "layer": 3},
        {"id": "sla", "label": "SLA evaluation", "layer": 4},
        {"id": "model", "label": "SLA risk model", "layer": 4},
        {"id": "controls", "label": "AP controls", "layer": 4},
        {"id": "dash", "label": "Northstar dashboard & API", "layer": 5},
    ],
    "edges": [["raw", "staging"], ["staging", "cases"], ["cases", "marts"], ["marts", "sla"], ["marts", "model"],
              ["staging", "controls"], ["sla", "dash"], ["model", "dash"], ["controls", "dash"]],
}


def build_product_data(cases: pd.DataFrame, events: pd.DataFrame, ap_df: pd.DataFrame | None = None) -> dict:
    from src.analytics.bottlenecks import identify_bottlenecks
    from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
    from src.api.dashboard import _section
    from src.ml.predict import load_model

    sla_targets = load_sla_targets()
    evaluated = evaluate_sla(cases, sla_targets)
    values = _order_value(events)
    bundle = None
    try:
        bundle = load_model()
    except Exception:
        pass

    result: dict = {"replay": True,
                    "replay_note": "BPI 2019 historical event log: every case is already closed. Risk is the model's score "
                                   "on the finished case, shown to demonstrate the workflow, not live monitoring."}

    trend = _monthly_trend(evaluated)
    overall_breach = float(evaluated["sla_breach"].mean())
    scored = X = None
    if bundle is not None:
        scored, X = score_cases(evaluated, bundle)
        scored["value"] = scored["case_id"].map(values).fillna(0.0)
        high = scored[scored["risk_level"].isin(["CRITICAL", "HIGH"])]
        high_value_cut = float(scored["value"].quantile(0.75))
        funnel = [{"label": "Total cases", "n": int(len(scored))},
                  {"label": "Top-quartile model score", "n": int((scored["risk_level"] != "STANDARD").sum())},
                  {"label": "Top-decile score (high priority)", "n": int(len(high))},
                  {"label": "High priority and top-quartile value", "n": int((high["value"] >= high_value_cut).sum())},
                  {"label": "Interventions recorded", "n": 0, "note": "No real interventions recorded; effects are simulated."}]
        kpis = {"cases": int(len(evaluated)), "breach_rate": round(100 * overall_breach, 1),
                "high_risk": int(len(high)), "high_risk_value": round(float(high["value"].sum()), 0),
                "avg_cycle_days": round(float(evaluated["cycle_time_hours"].mean()) / 24, 1),
                "breach_rate_delta_pp": None, "cycle_delta_days": None,
                "delta_note": "No period comparison: months of a closed replay are right-censored (late months contain only slow cases).",
                "sla_note": "Breach rate is against the configured SLA targets (86% of cases use the 240h fallback); it is not a realistic service level.",
                "suppliers": int(evaluated["supplier_id"].nunique())}
        sample = scored.sample(min(SCATTER_SIZE, len(scored)), random_state=0)
        sup = evaluated.set_index("case_id")["supplier_id"]
        result["scatter"] = [{"case_id": str(r.case_id), "risk": round(r.risk, 3), "value": round(float(r.value), 0),
                              "supplier_id": str(sup.get(r.case_id))} for r in sample.itertuples()]
        queue = scored.sort_values(["risk", "value"], ascending=False).head(QUEUE_SIZE)
        qids = [str(c) for c in queue["case_id"]]
        result["po"] = _po_details(evaluated, scored, values, events, X, bundle, qids)
        result["queue"] = [{"case_id": c, "supplier_id": result["po"][c]["supplier_id"], "risk": result["po"][c]["risk"],
                            "level": result["po"][c]["level"], "value": result["po"][c]["value"],
                            "top_reason": result["po"][c]["shap"][0]["label"] if result["po"][c]["shap"] else None,
                            "category": result["po"][c]["category"]} for c in qids if c in result["po"]]
    else:
        funnel = [{"label": "Total cases", "n": int(len(evaluated))}]
        kpis = {"cases": int(len(evaluated)), "breach_rate": round(100 * overall_breach, 1),
                "avg_cycle_days": round(float(evaluated["cycle_time_hours"].mean()) / 24, 1),
                "suppliers": int(evaluated["supplier_id"].nunique())}
        result["queue"], result["po"], result["scatter"] = [], {}, []

    rows, details = _supplier_block(evaluated, scored if scored is not None else pd.DataFrame(
        {"case_id": evaluated["case_id"], "risk": np.nan, "risk_level": None}), values, bundle is not None)
    result.update({
        "kpis": kpis, "trend": trend, "funnel": funnel,
        "where_time": _stage_share(identify_bottlenecks(events)),
        "suppliers": rows, "supplier_detail": details,
        "process": _process_flow(events, evaluated.set_index("case_id")["sla_breach"].astype(float)),
        "finance": _finance(events, ap_df, _section),
        "models": _models(), "data_quality": _data_quality(evaluated, cases, events, sla_targets),
        "evidence": _evidence(), "lineage": LINEAGE,
        "governance": _read_json("reports/governance_summary.json") or {"available": False},
        "intervention_config": _intervention_config(),
    })
    return result
