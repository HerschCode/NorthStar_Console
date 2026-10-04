"""Builders for the versioned /v1 API (src/v1/router.py). Pure functions of a Context, so the live path and the snapshot
builder share one implementation.

Rules every builder follows:
- every number is wrapped by `metric()` with provenance (measured | simulated | experimental | pending | descriptive),
  its source and the clock (`as_of`);
- nothing after `as_of` is used for open-case state (see src/replay/clock.py);
- money is EUR; no `$`;
- failed / tied / simulated results are returned, not filtered out.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.ml.early_risk import EarlyRiskModel, breach_flags
from src.replay.clock import DEFAULT_AS_OF, replay_state, to_utc

ROOT = Path(__file__).resolve().parents[2]
MODEL_NAME = "early-warning GRU ensemble (ONNX), realistic p75 target"


def _yaml(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8")) or {}


def _json(rel: str):
    try:
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def metric(value, unit="", provenance="measured", source="", as_of=None, n=None, note=None, **extra) -> dict:
    out = {"value": None if value is None or (isinstance(value, float) and math.isnan(value)) else value, "unit": unit,
           "provenance": provenance, "source": source, "as_of": None if as_of is None else str(as_of)}
    if n is not None:
        out["n"] = int(n)
    if note:
        out["note"] = note
    out.update(extra)
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _r(x, d=1):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), d)


class Context:
    """Frames + cached replay state. `cases` need case_id/start_time/end_time/supplier_id/category/cycle_time_hours."""

    def __init__(self, cases: pd.DataFrame, events: pd.DataFrame, ap_df: pd.DataFrame | None = None, model: EarlyRiskModel | None = None):
        self.cases = cases.reset_index(drop=True)
        self.events = events
        self.ap_df = ap_df
        self.model = model or EarlyRiskModel.load()
        self.targets = self.model.meta["target"]["targets_hours"]
        self.cfg = _yaml("config/expected_loss.yaml")
        self.stages = _yaml("config/stages.yaml")["stages"]
        self.stage_of = {a: s["id"] for s in self.stages for a in s["activities"]}
        self.stage_label = {s["id"]: s["label"] for s in self.stages}
        self.cases["breach_realistic"] = breach_flags(self.cases, self.targets)
        if "net_worth_eur" in events.columns:
            self.final_value = events.groupby("case_id")["net_worth_eur"].max()
        else:
            self.final_value = pd.Series(dtype=float)
        self._states: dict = {}

    # ── replay state with expected loss ──
    def state(self, as_of=DEFAULT_AS_OF) -> pd.DataFrame:
        t = to_utc(as_of)
        key = t.isoformat()
        if key in self._states:
            return self._states[key]
        st = replay_state(self.cases, self.events, t, self.model)
        pct = float(self.cfg["breach_cost_pct_of_value"])
        if st.empty:
            st = st.assign(p_eff=pd.Series(dtype=float), expected_loss_eur=pd.Series(dtype=float), tier=pd.Series(dtype=object))
            self._states[key] = st
            return st
        st = st.copy()
        p = np.where(st["status"] == "already_late", 1.0, st["breach_probability"])
        st["p_eff"] = p
        st["value_so_far"] = st["value_so_far"].fillna(0.0)
        st["expected_loss_eur"] = np.where(np.isnan(p), np.nan, p * st["value_so_far"] * pct)
        ranked = st["expected_loss_eur"].dropna()
        cuts = {k: float(ranked.quantile(v)) for k, v in self.cfg["tiers"].items()} if len(ranked) else {}
        el = st["expected_loss_eur"]
        tier = np.where(el.isna(), "UNSCORED", "STANDARD")
        if cuts:
            tier = np.where(el >= cuts["elevated"], "ELEVATED", tier)
            tier = np.where(el >= cuts["high"], "HIGH", tier)
            tier = np.where(el >= cuts["critical"], "CRITICAL", tier)
        st["tier"] = tier
        self._states[key] = st
        return st

    def closed(self, as_of) -> pd.DataFrame:
        return self.cases[self.cases["end_time"] <= to_utc(as_of)]

    def decided(self, as_of) -> pd.DataFrame:
        """Cases whose target window has elapsed at `as_of` (start + realistic target <= as_of). Whether each breached is
        knowable at the clock: it ended after its deadline (a case still open counts as breached). Unlike a closed-only
        rate this is not biased towards fast cases early in a replay."""
        t = to_utc(as_of)
        tgt = self.cases["category"].map(lambda c: self.targets.get(c, self.targets["default"]))
        deadline = self.cases["start_time"] + pd.to_timedelta(tgt, unit="h")
        d = self.cases[deadline <= t].copy()
        d["deadline"] = deadline[d.index]
        d["breached_by_clock"] = (d["end_time"] > d["deadline"]).astype(int)
        return d


def _assumption(ctx, as_of):
    return metric(ctx.cfg["breach_cost_pct_of_value"], "fraction of order value", "simulated",
                  "config/expected_loss.yaml", as_of,
                  note="Assumed cost of a breach as a share of order value; no cost-of-delay data exists in the log.")


# ───────────────────────── overview ─────────────────────────
def overview(ctx: Context, as_of=DEFAULT_AS_OF) -> dict:
    t = to_utc(as_of)
    st = ctx.state(t)
    src = "replay clock + early-warning model"
    late = st[st["status"] == "already_late"]
    scored = st[st["status"] == "scored"]
    at_risk = scored[scored["breach_probability"] >= ctx.cfg["at_risk_probability"]]
    flagged = pd.concat([late, at_risk])
    closed = ctx.closed(t)
    dec = ctx.decided(t)
    win = pd.Timedelta(days=30)
    cur = dec[dec["deadline"] > t - win]
    prev = dec[(dec["deadline"] <= t - win) & (dec["deadline"] > t - 2 * win)]

    def rate(df):
        return None if len(df) < 30 else _r(100 * df["breached_by_clock"].mean(), 1)

    cur_rate, prev_rate = rate(cur), rate(prev)
    tgt_cfg = None
    cfg_closed = closed[closed["cycle_time_hours"] > 0]
    if len(cfg_closed) >= 30:
        from src.analytics.sla_analysis import load_sla_targets

        sla = load_sla_targets()
        tgt = cfg_closed["category"].map(sla).fillna(sla.get("default", 240))
        tgt_cfg = _r(100 * (cfg_closed["cycle_time_hours"] > tgt).mean(), 1)
    ap_total = None
    if ctx.ap_df is not None and len(ctx.ap_df):
        ap_total = int(len(ctx.ap_df))
    trend = []
    for month, g in dec.groupby(dec["deadline"].dt.strftime("%Y-%m")):
        if len(g) >= 30:
            k, n = int(g["breached_by_clock"].sum()), len(g)
            lo, hi = wilson(k, n)
            trend.append({"month": month, "n": n, "breach_rate": _r(100 * k / n, 1), "ci95": [_r(100 * lo, 1), _r(100 * hi, 1)]})
    return {
        "as_of": str(t), "model": MODEL_NAME,
        "trend": {"series": trend, "provenance": "measured", "unit": "%",
                  "source": "cases grouped by the month their realistic target window ended (still-open cases count as breached), Wilson 95% interval",
                  "note": "Cases are grouped by deadline month, not completion month, so the series is not biased towards fast cases. Months with fewer than 30 cases are omitted."},
        "kpis": {
            "open_cases": metric(len(st), "cases", "measured", src, t, n=len(st)),
            "already_late": metric(len(late), "cases", "measured", src, t, n=len(st),
                                   note="Open cases already older than their realistic target: breach is determined, not predicted."),
            "at_risk_open": metric(len(at_risk), "cases", "experimental", src, t, n=len(scored),
                                   note=f"Open cases with P(breach) >= {ctx.cfg['at_risk_probability']}. Early-warning model: weak signal, see /v1/models."),
            "unscored_open": metric(int((st["status"] == "too_early").sum()), "cases", "measured", src, t,
                                    note="Fewer than 2 events seen so far; no early-warning score yet."),
            "value_flagged_eur": metric(_r(flagged["value_so_far"].sum(), 0), "EUR", "measured", src, t, n=len(flagged),
                                        note="Order value seen so far on already-late and at-risk open cases."),
            "expected_loss_eur": metric(_r(st["expected_loss_eur"].sum(), 0), "EUR", "simulated", "p x value x breach cost share", t,
                                        n=int(st["expected_loss_eur"].notna().sum()),
                                        assumption=_assumption(ctx, t)),
            "breach_rate_realistic_30d": metric(cur_rate, "%", "measured", "cases whose target window ended in the 30 days before as_of", t, n=len(cur),
                                                 previous=metric(prev_rate, "%", "measured", "previous 30 days", t, n=len(prev)),
                                                 note="Share of cases that ended after their realistic target (still-open cases count as breached). Not biased by fast cases closing first. Null when n < 30."),
            "breach_rate_configured_all_closed": metric(tgt_cfg, "%", "measured", "configured SLA fallback (240 h)", t, n=len(cfg_closed),
                                                         note="Secondary only: 86% of cases fall back to a 240 h target; not a realistic service level."),
            "ap_exceptions": metric(ap_total, "exceptions", "measured", "AP controls over the whole log (not clocked)", t,
                                    note="Anomaly triage, not fraud."),
            "interventions_recorded": metric(0, "interventions", "pending", "analytics.interventions ledger", t,
                                             note="No real interventions recorded; ROI is simulated."),
        },
        "funnel": [
            {"label": "Open cases", "n": int(len(st))},
            {"label": "Scored or already late", "n": int(len(late) + len(scored))},
            {"label": "At risk or already late", "n": int(len(flagged))},
            {"label": "CRITICAL or HIGH expected loss", "n": int(st["tier"].isin(["CRITICAL", "HIGH"]).sum())},
            {"label": "Interventions recorded", "n": 0},
        ],
        "stage_mix": [{"stage": ctx.stage_label.get(s, s), "open_cases": int(n)} for s, n in
                      st["current_activity"].map(ctx.stage_of).value_counts().items()],
    }


# ───────────────────────── queue / cases ─────────────────────────
def _drivers(row, st: pd.DataFrame, sup_hist: float | None, changes: int) -> list[dict]:
    med_idle = float(st["idle_hours"].median())
    out = [
        {"name": "Elapsed vs target", "value": _r(row["elapsed_hours"] / row["target_hours"], 2), "unit": "x target",
         "raises_risk": bool(row["elapsed_hours"] / row["target_hours"] > 0.5)},
        {"name": "Idle since last event", "value": _r(row["idle_hours"], 0), "unit": "h",
         "raises_risk": bool(row["idle_hours"] > med_idle), "note": f"open-case median {_r(med_idle, 0)} h"},
        {"name": "Order changes so far", "value": changes, "unit": "events", "raises_risk": changes > 0},
    ]
    if sup_hist is not None:
        out.append({"name": "Supplier history (ended cases only)", "value": _r(100 * sup_hist, 0), "unit": "% past target",
                    "raises_risk": bool(sup_hist > 0.3)})
    return out


def queue(ctx: Context, as_of=DEFAULT_AS_OF, min_value: float = 0.0, supplier: str | None = None, stage: str | None = None,
          limit: int = 100) -> dict:
    t = to_utc(as_of)
    st = ctx.state(t)
    d = st[st["expected_loss_eur"].notna() & (st["value_so_far"] >= min_value)]
    if supplier:
        d = d[d["supplier_id"] == supplier]
    if stage:
        d = d[d["current_activity"].map(ctx.stage_of) == stage]
    d = d.sort_values("expected_loss_eur", ascending=False)
    total = len(d)
    d = d.head(limit)
    ev = ctx.events[ctx.events["case_id"].isin(d["case_id"]) & (ctx.events["timestamp"] <= t)]
    changes = ev[ev["activity"].str.startswith("Change")].groupby("case_id").size()
    rows = []
    for r in d.to_dict("records"):
        rows.append({
            "case_id": r["case_id"], "supplier_id": r["supplier_id"], "category": r["category"],
            "tier": r["tier"], "status": r["status"],
            "p_breach": _r(r["p_eff"], 3), "p_breach_provenance": "measured" if r["status"] == "already_late" else "experimental",
            "expected_loss_eur": _r(r["expected_loss_eur"], 2), "value_eur": _r(r["value_so_far"], 0),
            "stage": ctx.stage_label.get(ctx.stage_of.get(r["current_activity"]), r["current_activity"]),
            "current_activity": r["current_activity"], "elapsed_hours": _r(r["elapsed_hours"], 0), "idle_hours": _r(r["idle_hours"], 0),
            "target_hours": _r(r["target_hours"], 0),
            "drivers": _drivers(r, st, None, int(changes.get(r["case_id"], 0))),
        })
    return {"as_of": str(t), "total_matching": int(total), "limit": limit,
            "ranking": metric(None, "", "simulated", "p(breach) x order value x assumed breach cost share", t,
                              assumption=_assumption(ctx, t),
                              note="Priority is expected loss, so a small order can never outrank a large one at similar risk."),
            "driver_note": "Drivers are descriptive facts about the case, not model attributions (the GRU model has no SHAP).",
            "rows": rows}


def case_360(ctx: Context, case_id: str, as_of=DEFAULT_AS_OF) -> dict | None:
    t = to_utc(as_of)
    row = ctx.cases[ctx.cases["case_id"] == case_id]
    if row.empty:
        return None
    c = row.iloc[0]
    if c["start_time"] > t:
        return {"case_id": case_id, "status": "not_started", "as_of": str(t),
                "note": "This case has not started at the chosen clock."}
    ev = ctx.events[(ctx.events["case_id"] == case_id) & (ctx.events["timestamp"] <= t)].sort_values(["timestamp", "activity"], kind="stable")
    timeline, prev = [], None
    t0 = ev["timestamp"].iloc[0] if len(ev) else c["start_time"]
    for r in ev.itertuples():
        gap = 0.0 if prev is None else (r.timestamp - prev).total_seconds() / 3600
        timeline.append({"activity": r.activity, "stage": ctx.stage_label.get(ctx.stage_of.get(r.activity), None),
                         "at": r.timestamp.isoformat(), "day": _r((r.timestamp - t0).total_seconds() / 86400, 1), "gap_hours": _r(gap, 1)})
        prev = r.timestamp
    st = ctx.state(t)
    srow = st[st["case_id"] == case_id]
    closed = c["end_time"] <= t
    out = {"case_id": case_id, "as_of": str(t), "supplier_id": c["supplier_id"], "category": c["category"],
           "status": "closed" if closed else (srow.iloc[0]["status"] if len(srow) else "open"),
           "timeline": timeline, "events_seen": len(ev),
           "longest_wait": max(timeline[1:], key=lambda x: x["gap_hours"]) if len(timeline) > 1 else None,
           "model": {"name": MODEL_NAME, "feature_hash": (_json("models/sla_risk_model.meta.json") or {}).get("feature_code_sha256", "")[:12]},
           "policy_sections": [], "policy_note": "Policy lookup is done by the P2 assistant; no static section mapping exists yet.",
           "interventions": []}
    if closed:
        out["outcome"] = {"cycle_hours": _r(c["cycle_time_hours"], 0), "target_hours": _r(ctx.targets.get(c["category"], ctx.targets["default"]), 0),
                          "breached_realistic_target": bool(c["breach_realistic"])}
    elif len(srow):
        r = srow.iloc[0].to_dict()
        sup_hist = None
        out["risk"] = {"p_breach": metric(_r(r["p_eff"], 3), "probability", "measured" if r["status"] == "already_late" else "experimental",
                                          MODEL_NAME, t, note="breach already determined by elapsed time" if r["status"] == "already_late" else
                                          f"early-warning score from the first k={r['k']} events"),
                       "expected_loss_eur": metric(_r(r["expected_loss_eur"], 2), "EUR", "simulated", "p x value x breach cost share", t,
                                                   assumption=_assumption(ctx, t)),
                       "tier": r["tier"], "value_eur": _r(r["value_so_far"], 0), "elapsed_hours": _r(r["elapsed_hours"], 0),
                       "target_hours": _r(r["target_hours"], 0), "idle_hours": _r(r["idle_hours"], 0)}
        chg = int(sum(1 for x in timeline if x["activity"].startswith("Change")))
        out["drivers"] = _drivers(r, st, sup_hist, chg)
        out["driver_note"] = "Descriptive drivers, not model attributions."
        m = ctx.model.meta["models"].get(str(r["k"])) if r.get("k") is not None and not pd.isna(r.get("k")) else None
        if m:
            out["risk"]["model_quality"] = {"k": int(r["k"]), "test_roc_auc": _r(m["test_roc_auc"], 3),
                                            "ci95": [_r(x, 3) for x in m["test_roc_auc_ci95"]], "n_test": m["n_test"],
                                            "base_rate": _r(m["base_rate"], 3)}
    if ctx.ap_df is not None and "case_id" in ctx.ap_df.columns:
        ex = ctx.ap_df[ctx.ap_df["case_id"] == case_id]
        out["ap_exceptions"] = ex.drop(columns=[c for c in ex.columns if c not in ("control_id", "severity", "exposure_eur")], errors="ignore").to_dict("records")
    return out


# ───────────────────────── suppliers ─────────────────────────
def suppliers(ctx: Context, as_of=DEFAULT_AS_OF, sort: str = "ci_lower", min_n: int = 20) -> dict:
    t = to_utc(as_of)
    closed = ctx.decided(t).assign(breach_realistic=lambda d: d["breached_by_clock"])
    st = ctx.state(t)
    open_by = st.groupby("supplier_id").agg(open_cases=("case_id", "size"),
                                            already_late=("status", lambda s: int((s == "already_late").sum())),
                                            expected_loss=("expected_loss_eur", "sum"))
    rows = []
    for sid, g in closed.dropna(subset=["supplier_id"]).groupby("supplier_id"):
        n = len(g)
        if n < min_n:
            continue
        k = int(g["breach_realistic"].sum())
        lo, hi = wilson(k, n)
        gc = g[g["end_time"] <= t]          # cycle times only for cases that have actually ended at the clock
        o = open_by.loc[sid] if sid in open_by.index else None
        rows.append({"supplier_id": sid, "closed_cases": n, "breach_rate": metric(_r(100 * k / n, 1), "%", "measured", "cases past their target window, realistic target", t, n=n,
                                                                                    ci95=[_r(100 * lo, 1), _r(100 * hi, 1)]),
                     "p50_days": _r(gc["cycle_time_hours"].median() / 24, 1) if len(gc) else None, "p75_days": _r(gc["cycle_time_hours"].quantile(.75) / 24, 1) if len(gc) else None,
                     "p90_days": _r(gc["cycle_time_hours"].quantile(.9) / 24, 1) if len(gc) else None,
                     "open_cases": int(o["open_cases"]) if o is not None else 0, "open_already_late": int(o["already_late"]) if o is not None else 0,
                     "open_expected_loss_eur": _r(o["expected_loss"], 0) if o is not None else 0.0, "_lo": lo})
    key = {"ci_lower": lambda r: -r["_lo"], "breach_rate": lambda r: -(r["breach_rate"]["value"] or 0),
           "expected_loss": lambda r: -r["open_expected_loss_eur"], "volume": lambda r: -r["closed_cases"]}.get(sort, lambda r: -r["_lo"])
    rows.sort(key=key)
    for r in rows:
        r.pop("_lo")
    return {"as_of": str(t), "min_n": min_n, "sort": sort, "count": len(rows),
            "note": "Breach rates use cases whose target window has elapsed at the clock (strictly causal; still-open cases count as breached) and are ranked by the Wilson interval lower bound; suppliers with fewer than min_n such cases are not ranked.",
            "rows": rows}


def supplier_360(ctx: Context, supplier_id: str, as_of=DEFAULT_AS_OF) -> dict | None:
    t = to_utc(as_of)
    c = ctx.cases[ctx.cases["supplier_id"] == supplier_id]
    if c.empty:
        return None
    dec = ctx.decided(t)
    closed = dec[dec["supplier_id"] == supplier_id].assign(breach_realistic=lambda d: d["breached_by_clock"])
    st = ctx.state(t)
    op = st[st["supplier_id"] == supplier_id].sort_values("expected_loss_eur", ascending=False)
    n, k = len(closed), int(closed["breach_realistic"].sum())
    lo, hi = wilson(k, n)
    peer = ctx.closed(t)
    tr = closed.assign(month=closed["deadline"].dt.strftime("%Y-%m")).groupby("month").agg(n=("case_id", "size"), b=("breach_realistic", "mean"))
    ended = closed[closed["end_time"] <= t]          # cycle time / value are known only for cases ended at the clock
    vals = ctx.final_value.reindex(ended["case_id"]).fillna(0)
    return {
        "supplier_id": supplier_id, "as_of": str(t),
        "closed_cases": metric(n, "cases", "measured", "cases past their target window at the clock", t, n=n),
        "breach_rate": metric(_r(100 * k / n, 1) if n else None, "%", "measured", "cases past their target window, realistic target", t, n=n,
                              ci95=[_r(100 * lo, 1), _r(100 * hi, 1)] if n else None),
        "value_closed_eur": metric(_r(vals.sum(), 0), "EUR", "measured", "max cumulative net worth per case ended at the clock", t, n=len(ended)),
        "cycle_days": {"p50": _r(ended["cycle_time_hours"].median() / 24, 1) if len(ended) else None, "p75": _r(ended["cycle_time_hours"].quantile(.75) / 24, 1) if len(ended) else None,
                       "p90": _r(ended["cycle_time_hours"].quantile(.9) / 24, 1) if len(ended) else None,
                       "peer_p50": _r(peer["cycle_time_hours"].median() / 24, 1) if len(peer) else None},
        "trend": [{"month": m, "n": int(r.n), "breach_rate": _r(100 * r.b, 1)} for m, r in tr.iterrows() if r.n >= 3],
        "open_cases": [{"case_id": r.case_id, "tier": r.tier, "p_breach": _r(r.p_eff, 3), "expected_loss_eur": _r(r.expected_loss_eur, 2),
                        "value_eur": _r(r.value_so_far, 0), "status": r.status} for r in op.head(15).itertuples()],
        "open_count": int(len(op)),
        "ap_exceptions": [] if ctx.ap_df is None or "supplier_id" not in ctx.ap_df.columns else
        ctx.ap_df[ctx.ap_df["supplier_id"] == supplier_id][["control_id", "severity", "exposure_eur"]].to_dict("records"),
    }


# ───────────────────────── process ─────────────────────────
def process_flow(ctx: Context, as_of=DEFAULT_AS_OF, metric_name: str = "median", group: str = "stage", max_edges: int = 40) -> dict:
    t = to_utc(as_of)
    closed = ctx.closed(t)
    ids = set(closed["case_id"])
    ev = ctx.events[ctx.events["case_id"].isin(ids)][["case_id", "activity", "timestamp"]].sort_values(["case_id", "timestamp"], kind="stable")
    breach = closed.set_index("case_id")["breach_realistic"]
    if group == "stage":
        unmapped = sorted(set(ev["activity"]) - set(ctx.stage_of))
        ev = ev.assign(node=ev["activity"].map(ctx.stage_of))
        label = ctx.stage_label
    else:
        unmapped = []
        ev = ev.assign(node=ev["activity"])
        label = {}
    ev = ev.dropna(subset=["node"])
    # collapse consecutive events in the same node into one visit
    ev["new"] = (ev["node"] != ev.groupby("case_id")["node"].shift()) | (ev["case_id"] != ev["case_id"].shift())
    ev["visit"] = ev.groupby("case_id")["new"].cumsum()
    visits = ev.groupby(["case_id", "visit"]).agg(node=("node", "first"), start=("timestamp", "min"), end=("timestamp", "max"), events=("timestamp", "size")).reset_index()
    visits["dwell_h"] = (visits["end"] - visits["start"]).dt.total_seconds() / 3600
    visits["next_node"] = visits.groupby("case_id")["node"].shift(-1)
    visits["next_start"] = visits.groupby("case_id")["start"].shift(-1)
    visits["wait_h"] = (visits["next_start"] - visits["end"]).dt.total_seconds() / 3600
    tr = visits.dropna(subset=["next_node"]).copy()
    tr["breach"] = tr["case_id"].map(breach)
    breach_total = float(tr.loc[tr["breach"] == 1, "wait_h"].sum()) or 1.0
    edges = []
    for (a, b), g in tr.groupby(["node", "next_node"]):
        gb = g[g["breach"] == 1]
        edges.append({"from": a, "to": b, "cases": int(g["case_id"].nunique()), "transitions": int(len(g)),
                      "median_hours": _r(g["wait_h"].median(), 1), "p75_hours": _r(g["wait_h"].quantile(.75), 1), "p90_hours": _r(g["wait_h"].quantile(.9), 1),
                      "breach_contribution_pct": _r(100 * float(gb["wait_h"].sum()) / breach_total, 1),
                      "breach_rate_pct": _r(100 * float(g["breach"].mean()), 1)})
    edges.sort(key=lambda e: -e["transitions"])
    edges = edges[:max_edges]
    keep = {e["from"] for e in edges} | {e["to"] for e in edges}
    nodes = []
    for node, g in visits.groupby("node"):
        if node in keep:
            nodes.append({"id": node, "label": label.get(node, node), "cases": int(g["case_id"].nunique()), "visits": int(len(g)),
                          "events": int(g["events"].sum()), "median_dwell_hours": _r(g["dwell_h"].median(), 1)})
    nodes.sort(key=lambda n: -n["events"])
    return {"as_of": str(t), "group": group, "metric": metric_name, "closed_cases": int(len(closed)),
            "provenance": "measured", "source": "closed cases at the clock; events grouped into business stages" if group == "stage" else "closed cases at the clock",
            "unmapped_activities": unmapped, "nodes": nodes, "edges": edges,
            "note": "breach_contribution_pct = share of all waiting time of breaching cases spent on this transition."}


def process_variants(ctx: Context, as_of=DEFAULT_AS_OF, top: int = 12) -> dict:
    t = to_utc(as_of)
    closed = ctx.closed(t)
    d = closed.groupby("variant").agg(cases=("case_id", "size"), breach=("breach_realistic", "mean"), median_days=("cycle_time_hours", lambda s: s.median() / 24)).sort_values("cases", ascending=False)
    n = len(closed)
    return {"as_of": str(t), "closed_cases": n, "variants": [
        {"variant": v, "cases": int(r.cases), "share_pct": _r(100 * r.cases / n, 1), "breach_rate_pct": _r(100 * r.breach, 1), "median_days": _r(r.median_days, 1)}
        for v, r in d.head(top).iterrows()]}


# ───────────────────────── risk map ─────────────────────────
def risk_map(ctx: Context, as_of=DEFAULT_AS_OF, limit: int = 800) -> dict:
    t = to_utc(as_of)
    st = ctx.state(t)
    d = st[st["expected_loss_eur"].notna()]
    pts = d.sort_values("expected_loss_eur", ascending=False).head(limit)
    vq = d["value_so_far"].quantile([1 / 3, 2 / 3]).tolist() if len(d) else [0, 0]
    pq = d["p_eff"].quantile([.75, .9]).tolist() if len(d) else [0, 0]
    vt = np.where(d["value_so_far"] >= vq[1], "high", np.where(d["value_so_far"] >= vq[0], "mid", "low"))
    pt = np.where(d["p_eff"] >= pq[1], "high", np.where(d["p_eff"] >= pq[0], "elevated", "standard"))
    mat = pd.crosstab(pd.Series(pt, name="risk"), pd.Series(vt, name="value"))
    return {"as_of": str(t), "points": [{"case_id": r.case_id, "p_breach": _r(r.p_eff, 3), "value_eur": _r(r.value_so_far, 0), "supplier_id": r.supplier_id,
                                         "stage": ctx.stage_label.get(ctx.stage_of.get(r.current_activity), r.current_activity), "tier": r.tier} for r in pts.itertuples()],
            "matrix": {"risk_tiers": ["high", "elevated", "standard"], "value_tiers": ["low", "mid", "high"],
                       "counts": [[int(mat.loc[r, v]) if r in mat.index and v in mat.columns else 0 for v in ["low", "mid", "high"]] for r in ["high", "elevated", "standard"]]},
            "provenance": "experimental", "source": MODEL_NAME}


# ───────────────────────── finance / roi / models / data ─────────────────────────
def finance_controls(ctx: Context, as_of=DEFAULT_AS_OF) -> dict:
    from src.api.product import _finance

    f = _finance(ctx.events, ctx.ap_df, lambda fn, *a: {"available": True, **fn(*a)} if True else None)
    controls = []
    for c in f["control_health"]:
        controls.append({**c, "provenance": "measured" if c["status"] != "not_valid" else "measured",
                         "source": "planted-anomaly evaluation (synthetic injections) + external validation"})
    return {"as_of": str(to_utc(as_of)), "controls": controls, "external_c4": f.get("external_c4"),
            "note": "Recall is measured against planted synthetic anomalies; real exception counts are unlabeled triage."}


def finance_exceptions(ctx: Context, control: str | None = None, min_exposure: float = 0.0) -> dict:
    df = ctx.ap_df
    if df is None or df.empty:
        return {"available": False, "reason": "AP controls have not been run"}
    d = df[df["exposure_eur"].fillna(0) >= min_exposure]
    if control:
        d = d[d["control_id"].str.startswith(control)]
    summary = d.groupby(["control_id", "severity"]).agg(count=("exposure_eur", "size"), exposure_eur=("exposure_eur", "sum")).reset_index()
    vq = d["exposure_eur"].fillna(0).quantile([.5, .9]).tolist() if len(d) else [0, 0]
    return {"total": int(len(d)), "provenance": "measured", "source": "AP controls over the whole log (unlabeled triage, not fraud)",
            "summary": summary.round(2).to_dict("records"), "exposure_cuts_eur": {"p50": _r(vq[0], 0), "p90": _r(vq[1], 0)}}


def finance_working_capital(ctx: Context) -> dict:
    from src.api.product import _finance

    f = _finance(ctx.events, ctx.ap_df, lambda fn, *a: {"available": True, **fn(*a)})
    return {"provenance": "simulated", "source": "net-30 terms assumed; the log has no payment terms", **f["working_capital"]}


def interventions_roi(ctx: Context) -> dict:
    roi = _json("reports/roi_sensitivity.json")
    mvr = _json("reports/model_vs_rules.json")
    cfg = _yaml("config/interventions.yaml")
    return {"logged": metric(0, "interventions", "pending", "analytics.interventions ledger", None, note="No real intervention outcomes recorded."),
            "simulated": {"provenance": "simulated", "assumptions": {"breach_cost": cfg.get("breach_cost"), "capacity_pct": cfg.get("capacity_pct"), "types": cfg.get("types")},
                          "sensitivity_report": "reports/roi_sensitivity.json" if roi else None},
            "model_vs_rules": None if not mvr else {"provenance": "measured", "precision_at_k": mvr.get("precision_at_k"), "n_test": mvr.get("n_test"),
                                                    "verdict": "Model advantage over the order-value rule is not statistically established."}}


def models(ctx: Context) -> dict:
    from src.api.product import _models

    m = _models()
    cal = _json("models/calibration_data.json")
    early = ctx.model.meta
    return {"late_stage_model": m, "calibration": cal,
            "early_warning": {"name": MODEL_NAME, "target": early["target"]["definition"], "trained_at": early["trained_at"],
                              "per_k": {k: {"test_roc_auc": _r(v["test_roc_auc"], 3), "ci95": [_r(x, 3) for x in v["test_roc_auc_ci95"]], "n_test": v["n_test"], "base_rate": _r(v["base_rate"], 3)}
                                        for k, v in early["models"].items()},
                              "note": "Weak signal: AUC well below the late-stage score because only the first k events are known."},
            "governance_checks": [{"check": c, "status": "pass"} for c in (m.get("validation") or [])],
            "provenance": "measured"}


def data_quality(ctx: Context) -> dict:
    from src.api.product import _data_quality
    from src.analytics.sla_analysis import evaluate_sla, load_sla_targets

    ev = evaluate_sla(ctx.cases.drop(columns=["breach_realistic"]), load_sla_targets())
    q = _data_quality(ev, ctx.cases, ctx.events, load_sla_targets())
    q["mode"] = "historical dataset"
    q["freshness_note"] = "Freshness is not alarmed: the log ends in 2019 by construction."
    q["provenance"] = "measured"
    return q


EXPERIMENTS = [
    {"id": "benford", "name": "Benford first-digit screen", "hypothesis": "Benford deviation flags suspicious vendors.", "result": "failed",
     "decision": "Marked not operationally valid", "detail": "Flags ~55% of ordinary external data (null 5%).", "doc": "docs/external-validation.md"},
    {"id": "iforest", "name": "Isolation Forest vs rules (AP)", "hypothesis": "Unsupervised anomaly model beats rule controls.", "result": "failed",
     "decision": "Not deployed", "detail": "Did not beat the rules on planted anomalies.", "doc": "docs/ap-controls-evaluation.md"},
    {"id": "leak", "name": "Supplier-history features", "hypothesis": "Earlier-started cases' outcomes are usable history.", "result": "leaked",
     "decision": "Fixed: completed-before-start only; retrained", "detail": "Look-ahead leak inflated AUC and a rule's small-share advantage.", "doc": "docs/model-vs-rules.md"},
    {"id": "workload", "name": "Supplier workload features", "hypothesis": "Workload features let the model beat the rules.", "result": "tied",
     "decision": "Not adopted", "detail": "No gain at 5-10% treated share.", "doc": "docs/model-vs-rules.md"},
    {"id": "model_vs_order_value", "name": "Model vs order-value rule", "hypothesis": "The model beats a one-feature rule.", "result": "tied",
     "decision": "Advantage not established", "detail": "Paired cluster-bootstrap CIs include 0.", "doc": "docs/model-vs-rules.md"},
    {"id": "calibration", "name": "Isotonic vs Platt calibration", "hypothesis": "Isotonic calibration improves served probabilities.", "result": "resolved",
     "decision": "Held-out Platt kept", "detail": "Isotonic fitted on training rows collapsed AUC.", "doc": "docs/calibration.md"},
    {"id": "c4_external", "name": "Duplicate-invoice control on Online Retail II", "hypothesis": "C4 isolates unusual repeats on out-of-domain data.", "result": "promising",
     "decision": "Kept; proxy label only", "detail": "21x lift vs reversal base rate.", "doc": "docs/external-validation.md"},
]


def evidence(ctx: Context | None = None) -> dict:
    """Headline claims with their status, and the known limitations (also served by the older /app shell)."""
    from src.api.product import _evidence

    e = _evidence()
    return {**e, "provenance": "measured", "source": "docs/ and reports/ in this repository"}


def experiments(ctx: Context | None = None) -> dict:
    return {"experiments": EXPERIMENTS, "provenance": "measured", "note": "Failed, leaked and tied results are listed on purpose."}


LINEAGE_CHAINS = {
    "breach_rate": ["raw event log", "staging.events", "analytics.process_cases", "dbt_marts.fct_cases", "realistic target (early_risk_meta.json)", "/v1/overview"],
    "open_case_risk": ["raw event log", "events <= as_of", "early-warning GRU (ONNX)", "expected loss (config/expected_loss.yaml)", "/v1/queue"],
    "ap_exceptions": ["raw event log", "case attributes", "AP controls C1-C6", "analytics.ap_control_exceptions", "/v1/finance/exceptions"],
}


def lineage(ctx: Context | None = None, metric_name: str | None = None) -> dict:
    from src.api.product import LINEAGE

    return {"graph": LINEAGE, "chains": LINEAGE_CHAINS if not metric_name else {metric_name: LINEAGE_CHAINS.get(metric_name)}}


def briefing(ctx: Context, as_of=DEFAULT_AS_OF) -> dict:
    ov = overview(ctx, as_of)
    k = ov["kpis"]
    q = queue(ctx, as_of, limit=3)
    facts = [
        {"id": "open", "fact": f"{k['open_cases']['value']} cases are open at {ov['as_of'][:10]}.", "metric": "open_cases"},
        {"id": "late", "fact": f"{k['already_late']['value']} open cases are already past their realistic target.", "metric": "already_late"},
        {"id": "risk", "fact": f"{k['at_risk_open']['value']} further open cases have P(breach) >= {ctx.cfg['at_risk_probability']} (experimental model).", "metric": "at_risk_open"},
        {"id": "loss", "fact": f"Simulated expected loss on open cases: EUR {k['expected_loss_eur']['value']} (assumed {int(100 * ctx.cfg['breach_cost_pct_of_value'])}% of order value per breach).", "metric": "expected_loss_eur"},
    ]
    for i, r in enumerate(q["rows"]):
        facts.append({"id": f"top{i + 1}", "fact": f"Highest expected loss: case {r['case_id']} (supplier {r['supplier_id']}, EUR {r['value_eur']} value, p={r['p_breach']}).", "metric": "queue"})
    return {"as_of": ov["as_of"], "facts": facts, "note": "Structured facts only; the P2 assistant turns them into prose and must cite fact ids."}


def search(ctx: Context, q: str, limit: int = 20) -> dict:
    ql = q.strip().lower()
    out = []
    if not ql:
        return {"results": []}
    m = ctx.cases[ctx.cases["case_id"].str.lower().str.contains(ql, regex=False)].head(limit)
    out += [{"type": "case", "id": r.case_id, "label": f"Case {r.case_id}"} for r in m.itertuples()]
    sup = ctx.cases["supplier_id"].dropna().unique()
    out += [{"type": "supplier", "id": s, "label": f"Supplier {s}"} for s in sup if ql in str(s).lower()][:limit]
    out += [{"type": "experiment", "id": e["id"], "label": e["name"]} for e in EXPERIMENTS if ql in e["name"].lower()]
    out += [{"type": "control", "id": c, "label": c} for c in ("C1", "C2", "C3", "C4", "C5", "C6") if ql in c.lower()]
    return {"results": out[:limit]}
