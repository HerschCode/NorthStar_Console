"""Independent-dataset check: does the "completion-time features carry the skill" finding hold
on a different public event log, and how much can a first-k-events (prefix) model recover?

Data: BPI Challenge 2012 (loan-application process, 4TU.ResearchData, CC terms per the "4TU General
Terms of Use"). NOT this project's own data and NOT its labels: the target is defined here as
"cycle time above the training window's p50/p75" (threshold from the first 80% of cases by start
time only; last 20% held out in time order).

Feature sets (all use only information available at the stated moment):
  creation   -- requested amount, start hour/weekday/month, first activity
  prefix-k   -- the above + only the first k events: elapsed hours at event k, unique activities so
                far, repeats so far, the k-th activity. Excluded: cases with fewer than k events (no
                prefix exists) AND cases whose elapsed time at event k already exceeds the threshold
                (their label is already determined, so scoring them would be a look-up, not a
                prediction). Reported per row, since the surviving population changes with k.
  full-case  -- aggregates over the finished case (event_count, unique, rework, last activity):
                the analogue of P1's completion-time features. Outcome-bearing by construction.

Run: python -m scripts.external_validation_bpi2012
"""
import gzip
import hashlib
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

warnings.filterwarnings("ignore")

URL = "https://data.4tu.nl/file/533f66a4-8911-4ac7-8612-1235d65d1f37/3276db7f-8bee-4f2b-88ee-92dbffb5a893"
MD5 = "74c7ba9aba85bfcb181a22c9d565e5b5"
PATH = Path("data/external/BPI_Challenge_2012.xes.gz")


def ensure_data() -> Path:
    if not PATH.exists():
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_bytes(requests.get(URL, timeout=120).content)
    if hashlib.md5(PATH.read_bytes()).hexdigest() != MD5:
        raise RuntimeError("BPI 2012 checksum mismatch -- refusing to use a corrupted/changed file")
    return PATH


def _ln(tag):
    return tag.rsplit("}", 1)[-1]


def parse_cases(path: Path) -> list[dict]:
    cases = []
    with gzip.open(path, "rb") as f:
        trace = None
        for ev, el in ET.iterparse(f, events=("start", "end")):
            t = _ln(el.tag)
            if ev == "start" and t == "trace":
                trace = {"events": [], "amount": np.nan, "case_id": None}
            elif ev == "end" and t == "event" and trace is not None:
                keys = {c.get("key"): c.get("value") for c in el}
                act = f"{keys.get('concept:name')}+{keys.get('lifecycle:transition')}"
                trace["events"].append((pd.to_datetime(keys["time:timestamp"], utc=True), act))
                el.clear()
            elif ev == "end" and t in ("string", "int", "float") and trace is not None and el.get("key") == "AMOUNT_REQ":
                trace["amount"] = float(el.get("value"))
            elif ev == "end" and t == "string" and trace is not None and el.get("key") == "concept:name" and trace["case_id"] is None and not trace["events"]:
                trace["case_id"] = el.get("value")
            elif ev == "end" and t == "trace":
                trace["events"].sort(key=lambda x: x[0])
                cases.append(trace)
                trace = None
                el.clear()
    return cases


def build_frame(cases: list[dict]) -> pd.DataFrame:
    rows = []
    for c in cases:
        ev = c["events"]
        if len(ev) < 2:
            continue
        times = [e[0] for e in ev]
        acts = [e[1] for e in ev]
        rows.append({
            "case_id": c["case_id"], "start": times[0], "cycle_h": (times[-1] - times[0]).total_seconds() / 3600,
            "amount": c["amount"], "times": times, "acts": acts,
        })
    df = pd.DataFrame(rows)
    df["start"] = pd.to_datetime(df["start"], utc=True)
    df = df.sort_values("start").reset_index(drop=True)
    return df


def base_features(df):
    return pd.DataFrame({
        "amount": df["amount"].fillna(df["amount"].median()),
        "hour": df["start"].dt.hour, "dow": df["start"].dt.dayofweek, "month": df["start"].dt.month,
        "first_act": df["acts"].map(lambda a: a[0]),
    })


def prefix_features(df, k):
    X = base_features(df)
    X["elapsed_h"] = [(t[k - 1] - t[0]).total_seconds() / 3600 if len(t) >= k else np.nan for t in df["times"]]
    X["uniq_prefix"] = [len(set(a[:k])) for a in df["acts"]]
    X["repeats_prefix"] = [k - len(set(a[:k])) if len(a) >= k else np.nan for a in df["acts"]]
    X["kth_act"] = [a[k - 1] if len(a) >= k else "NA" for a in df["acts"]]
    return X


def full_features(df):
    X = base_features(df)
    X["event_count"] = df["acts"].map(len)
    X["uniq"] = df["acts"].map(lambda a: len(set(a)))
    X["rework"] = df["acts"].map(lambda a: len(a) - len(set(a)))
    X["last_act"] = df["acts"].map(lambda a: a[-1])
    return X


def encode(X):
    return pd.get_dummies(X, columns=[c for c in X.columns if X[c].dtype == object]).astype(float)


def evaluate(df, X, label, pct, mask=None):
    n = len(df)
    cut = int(n * 0.8)
    thr = df["cycle_h"].iloc[:cut].quantile(pct / 100)     # threshold from training window only
    y = (df["cycle_h"] > thr).astype(int)
    keep = np.ones(n, bool) if mask is None else mask
    tr = np.where(keep & (np.arange(n) < cut))[0]
    te = np.where(keep & (np.arange(n) >= cut))[0]
    Xe = encode(X)
    out = []
    for name, mk in [("LR", lambda: LogisticRegression(max_iter=3000, class_weight="balanced")),
                     ("RF", lambda: RandomForestClassifier(n_estimators=200, max_depth=8, class_weight="balanced", random_state=42))]:
        m = mk().fit(Xe.iloc[tr].fillna(0), y.iloc[tr])
        p = m.predict_proba(Xe.iloc[te].fillna(0))[:, 1]
        out.append((name, roc_auc_score(y.iloc[te], p), average_precision_score(y.iloc[te], p)))
    base = y.iloc[te].mean()
    print(f"  {label:26s} n_train={len(tr):5d} n_test={len(te):5d} base={base:.3f} | " +
          " | ".join(f"{n} ROC {r:.3f} PR {p:.3f}" for n, r, p in out))
    return out


def main():
    df = build_frame(parse_cases(ensure_data()))
    print(f"cases={len(df)} events={int(df['acts'].map(len).sum())}  median cycle={df['cycle_h'].median():.1f}h "
          f"p75={df['cycle_h'].quantile(.75):.1f}h max={df['cycle_h'].max()/24:.0f}d")
    for pct in (50, 75):
        cut = int(len(df) * 0.8)
        thr = df["cycle_h"].iloc[:cut].quantile(pct / 100)
        print(f"\n== target: cycle time > training-window p{pct} ({thr:.1f}h) ==")
        evaluate(df, base_features(df), "creation-time only", pct)
        for k in (3, 5, 10):
            X = prefix_features(df, k)
            still_open = (df["acts"].map(len) >= k).values & (X["elapsed_h"].fillna(np.inf) <= thr).values
            evaluate(df, X, f"first {k} events, not yet breached", pct, still_open)
        evaluate(df, full_features(df), "full case (completion)", pct)


if __name__ == "__main__":
    main()
