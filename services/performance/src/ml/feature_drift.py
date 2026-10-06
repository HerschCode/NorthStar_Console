"""Per-feature drift (Population Stability Index) between the training-time distribution of each
model input and what the model is being asked to score now.

/observability/prediction-drift only compares the distribution of the model's OUTPUT risk buckets;
this looks at the inputs, which move first (a supplier-mix change shifts `supplier_id` shares long
before it moves the predicted-risk histogram enough to trip a 10pp bucket alert).

Computed on the RAW model inputs (event_count, category, supplier_id, ...), not the 539
one-hot-encoded columns -- PSI on one-hot dummies would report a category's drift 20+ times.

Numeric features: bin edges are the training-set quantiles (so each training bin holds ~equal
mass), stored in the baseline at train time; current values are binned with the SAME edges.
Categorical features: training category shares, keeping the top `max_categories` and pooling the
rest as "__other__"; a category unseen in training also lands in "__other__".

PSI = sum((actual_i - expected_i) * ln(actual_i / expected_i)), each share floored at EPSILON so
an empty bin doesn't produce an infinite term. Conventional reading: <0.1 stable, 0.1-0.25 moderate
shift, >0.25 major shift -- the defaults in config/drift.yaml, not a law of nature.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

EPSILON = 1e-4
OTHER = "__other__"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "drift.yaml"


def load_drift_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


def _is_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def compute_baselines(train_features: pd.DataFrame, n_bins: int = 10, max_categories: int = 30) -> dict:
    """train_features: RAW feature frame (one column per model input, before one-hot encoding)."""
    baselines: dict = {}
    for col in train_features.columns:
        s = train_features[col]
        if _is_numeric(s):
            vals = s.dropna().astype(float)
            if len(vals) == 0 or vals.nunique() < 2:
                continue
            edges = np.unique(np.quantile(vals, np.linspace(0, 1, n_bins + 1)))
            if len(edges) < 3:
                continue
            counts, _ = np.histogram(_clip_to_edges(vals.values, edges), bins=edges)
            baselines[col] = {
                "type": "numeric", "edges": [float(e) for e in edges],
                "shares": [float(c) / counts.sum() for c in counts],
            }
        else:
            shares = s.fillna("UNKNOWN").astype(str).value_counts(normalize=True)
            top = shares.head(max_categories)
            out = {str(k): float(v) for k, v in top.items()}
            rest = 1.0 - sum(out.values())
            if rest > 1e-9:
                out[OTHER] = float(rest)
            baselines[col] = {"type": "categorical", "shares": out}
    return baselines


def _clip_to_edges(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    # Values outside the training range belong in the first/last bin (they ARE a shift signal --
    # mass piling up in an outer bin), not silently dropped by np.histogram.
    return np.clip(values, edges[0], edges[-1])


def psi(expected: list[float], actual: list[float]) -> float:
    total = 0.0
    for e, a in zip(expected, actual):
        e, a = max(e, EPSILON), max(a, EPSILON)
        total += (a - e) * math.log(a / e)
    return total


def _current_shares(baseline: dict, series: pd.Series) -> tuple[list[float], list[float]] | None:
    if baseline["type"] == "numeric":
        vals = series.dropna().astype(float).values
        if len(vals) == 0:
            return None
        edges = np.asarray(baseline["edges"])
        counts, _ = np.histogram(_clip_to_edges(vals, edges), bins=edges)
        return baseline["shares"], [float(c) / counts.sum() for c in counts]
    keys = list(baseline["shares"].keys())
    cur = series.fillna("UNKNOWN").astype(str)
    cur = cur.where(cur.isin(keys), OTHER)
    counts = cur.value_counts(normalize=True)
    if OTHER not in baseline["shares"]:
        keys = keys + [OTHER]
        expected = [baseline["shares"].get(k, 0.0) for k in keys]
    else:
        expected = [baseline["shares"][k] for k in keys]
    return expected, [float(counts.get(k, 0.0)) for k in keys]


@dataclass
class FeatureDrift:
    feature: str
    psi: float
    status: str  # "stable" | "warn" | "alert"


@dataclass
class FeatureDriftReport:
    status: str  # "stable" | "warn" | "alert" | "no_baseline"
    features: list[FeatureDrift] = field(default_factory=list)
    n_current_rows: int = 0
    note: str | None = None

    @property
    def alert_features(self) -> list[str]:
        return [f.feature for f in self.features if f.status == "alert"]


def compute_feature_drift(baselines: dict | None, current_features: pd.DataFrame, config: dict | None = None) -> FeatureDriftReport:
    cfg = config or load_drift_config()
    if not baselines:
        return FeatureDriftReport(
            status="no_baseline", n_current_rows=len(current_features),
            note="model metadata has no feature_baselines -- retrain with current code to populate",
        )
    warn_t, alert_t = cfg["psi_warn"], cfg["psi_alert"]
    results = []
    for col, base in baselines.items():
        if col not in current_features.columns:
            continue
        pair = _current_shares(base, current_features[col])
        if pair is None:
            continue
        value = psi(*pair)
        status = "alert" if value >= alert_t else "warn" if value >= warn_t else "stable"
        results.append(FeatureDrift(col, round(value, 4), status))
    results.sort(key=lambda f: f.psi, reverse=True)
    n_alert = sum(1 for f in results if f.status == "alert")
    n_warn = sum(1 for f in results if f.status == "warn")
    overall = "alert" if n_alert >= cfg["retrain_min_alert_features"] else "warn" if (n_alert or n_warn) else "stable"
    return FeatureDriftReport(status=overall, features=results, n_current_rows=len(current_features))


def current_feature_drift(meta_path: str | Path = "models/sla_risk_model.meta.json", config: dict | None = None) -> FeatureDriftReport:
    """Baselines from the deployed model's metadata vs the most recent `current_window_cases` cases
    in the database. Shared by GET /health/drift/features and the Prefect flow's retrain gate so
    both always agree on what "current" means."""
    import json

    cfg = config or load_drift_config()
    path = Path(meta_path)
    baselines = json.loads(path.read_text()).get("feature_baselines") if path.exists() else None
    if not baselines:
        return compute_feature_drift(None, pd.DataFrame(), cfg)

    from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
    from src.api.db import load_cases
    from src.ml.features import raw_feature_frame

    evaluated = evaluate_sla(load_cases(), load_sla_targets())
    # Derive features over ALL cases (supplier-history features are expanding, so they need the
    # full history), then take the most recent window as the "current" population.
    raw = raw_feature_frame(evaluated)
    recent_idx = evaluated.sort_values("start_time").index[-cfg["current_window_cases"]:]
    return compute_feature_drift(baselines, raw.loc[recent_idx], cfg)
