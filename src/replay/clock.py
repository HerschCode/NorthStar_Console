"""Replay clock: an honest "open cases at time T" view over a CLOSED historical event log.

Every case in the BPI 2019 sample has finished, so scoring "at risk" on whole cases shows the model its own outcome. The
clock instead picks an `as_of` instant and exposes only what was knowable then:

- open cases   = started at or before `as_of` and not ended by it;
- their events = events with timestamp <= `as_of` (nothing later is ever read);
- risk         = the early-warning GRU ensemble (src/ml/early_risk.py) on the first k events seen so far, k in {2, 3, 5},
                 for the realistic target (training-window per-category p75), never the configured SLA;
- a case already older than its target is `already_late` (breach is determined by elapsed time, not predicted);
- a case with fewer than 2 events is `too_early` (no score).

DEFAULT_AS_OF (2018-04-16): nearly every case in the sample STARTED in January 2018 (the sampling window) and the
realistic targets are ~100 days, so the clock only becomes informative from March. On 2018-04-16 there are 3,089 open
cases: 2,517 young or mid-flight and scorable, 544 already past their target, and 3,670 cases whose target window has
elapsed (24% of them breached, matching the p75 definition). Earlier clocks have almost no decided cases (22 on
2018-01-22), later ones are a long-tail backlog (1,186 open on 2018-05-14, 86% already late).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.ml.early_risk import EarlyRiskModel, sequence_arrays, static_vector, supplier_history, target_hours

DEFAULT_AS_OF = pd.Timestamp("2018-04-16", tz="UTC")


def to_utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def open_cases(cases: pd.DataFrame, as_of) -> pd.DataFrame:
    t = to_utc(as_of)
    return cases[(cases["start_time"] <= t) & (cases["end_time"] > t)]


def replay_state(cases: pd.DataFrame, events: pd.DataFrame, as_of=DEFAULT_AS_OF,
                 model: EarlyRiskModel | None = None) -> pd.DataFrame:
    """One row per open case at `as_of` (see module docstring). Columns: case_id, supplier_id, category, start_time,
    events_seen, elapsed_hours, idle_hours, current_activity, value_so_far, target_hours, status, breach_probability, k."""
    t = to_utc(as_of)
    model = model or EarlyRiskModel.load()
    meta = model.meta
    targets = meta["target"]["targets_hours"]
    prior = meta["target"]["prior_breach_rate"]
    op = open_cases(cases, t)
    if op.empty:
        return pd.DataFrame(columns=["case_id", "status"])
    ev = events[events["case_id"].isin(op["case_id"]) & (events["timestamp"] <= t)]
    ev = ev.sort_values(["case_id", "timestamp", "activity"], kind="stable")
    g = ev.groupby("case_id")
    agg = pd.DataFrame({"events_seen": g.size(), "last_ts": g["timestamp"].max(),
                        "current_activity": g["activity"].last()})
    agg["value_so_far"] = g["net_worth_eur"].max() if "net_worth_eur" in ev.columns else np.nan
    df = op.merge(agg, left_on="case_id", right_index=True, how="left").reset_index(drop=True)
    df["events_seen"] = df["events_seen"].fillna(0).astype(int)
    df["elapsed_hours"] = (t - df["start_time"]).dt.total_seconds() / 3600.0
    df["idle_hours"] = (t - df["last_ts"]).dt.total_seconds() / 3600.0
    df["target_hours"] = df["category"].map(lambda c: target_hours(targets, c))
    df["status"] = np.where(df["events_seen"] < 2, "too_early",
                            np.where(df["elapsed_hours"] > df["target_hours"], "already_late", "scored"))
    df["breach_probability"] = np.nan
    df["k"] = pd.array([pd.NA] * len(df), dtype="Int64")

    scored_idx = list(df.index[df["status"] == "scored"])
    if scored_idx:
        hist = supplier_history(cases, targets, prior)
        case_pos = {cid: i for cid, i in zip(cases["case_id"], cases.index)}
        by_case = {cid: grp for cid, grp in ev.groupby("case_id")}
        buckets: dict[int, list] = {}
        for i in scored_idx:
            usable = [k for k in model.available_k if k <= int(df.at[i, "events_seen"])]
            if usable:
                buckets.setdefault(max(usable), []).append(i)
            else:
                df.at[i, "status"] = "too_early"
        for k, idxs in buckets.items():
            A, F, S = [], [], []
            for i in idxs:
                grp = by_case[df.at[i, "case_id"]]
                ts = pd.to_datetime(grp["timestamp"]).values
                offsets = (ts[:k] - ts[0]) / np.timedelta64(1, "h")
                acts, feats = sequence_arrays(grp["activity"].tolist(), offsets, meta["vocab"], k)
                A.append(acts)
                F.append(feats)
                S.append(static_vector(meta, df.at[i, "start_time"], df.at[i, "category"],
                                       float(hist.loc[case_pos[df.at[i, "case_id"]]])))
            p = model.probability(k, np.stack(A), np.stack(F), np.stack(S))
            df.loc[idxs, "breach_probability"] = np.asarray(p, dtype=float)
            df.loc[idxs, "k"] = k
    cols = ["case_id", "supplier_id", "category", "start_time", "events_seen", "elapsed_hours", "idle_hours",
            "current_activity", "value_so_far", "target_hours", "status", "breach_probability", "k"]
    return df[[c for c in cols if c in df.columns]]
