"""Serving-side early-warning risk: score a case from its first k events with the GRU ensemble exported to ONNX
(scripts/export_early_risk.py). numpy + onnxruntime only -- torch is NOT needed (and is not in requirements.txt),
so the serving image stays small.

This module also owns the featurization used at TRAINING time by the export script, so a case is turned into model
inputs by exactly one piece of code in both places (tests/test_early_risk.py checks it against the torch-side
research code in src/ml/sequence_model.py).

Target: breach = cycle time above the TRAINING-window per-category p75 (~27% base rate) -- not the configured SLA.
Population: cases with >= k events that are still under that target at event k (otherwise the label is already
determined by elapsed time); anything else is refused, not scored.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "early_risk"
META_NAME = "early_risk_meta.json"
UNK = 1


class EarlyRiskNotApplicable(ValueError):
    """The case cannot be scored at this k (too few events yet, or already past its target)."""


def target_hours(targets: dict, category) -> float:
    return float(targets.get(category, targets["default"]))


def encode_activities(vocab: dict, activities) -> list[int]:
    return [vocab.get(a, UNK) for a in activities]


def sequence_arrays(activities, offsets_h, vocab: dict, k: int):
    """(acts [k] int64, step_feats [k, 2] float32): activity ids, log1p(hours since case start),
    log1p(hours since previous event). Same arithmetic as sequence_model.make_sequence_tensors."""
    t = np.asarray(offsets_h[:k], dtype=float)
    acts = np.asarray(encode_activities(vocab, list(activities)[:k]), dtype=np.int64)
    feats = np.zeros((k, 2), dtype=np.float32)
    feats[:, 0] = np.log1p(np.clip(t, 0, None))
    feats[1:, 1] = np.log1p(np.clip(np.diff(t), 0, None))
    return acts, feats


def static_vector(meta: dict, start_time, category, supplier_hist: float) -> np.ndarray:
    ts = pd.Timestamp(start_time)
    cat = "UNKNOWN" if category is None or (isinstance(category, float) and math.isnan(category)) else str(category)
    onehot = [1.0 if cat == c else 0.0 for c in meta["categories"]]
    return np.asarray([ts.hour / 23.0, ts.dayofweek / 6.0, ts.month / 12.0, float(supplier_hist)] + onehot,
                      dtype=np.float32)


def breach_flags(cases: pd.DataFrame, targets: dict) -> pd.Series:
    tgt = cases["category"].map(lambda c: target_hours(targets, c))
    return (cases["cycle_time_hours"].astype(float) > tgt).astype(int)


def supplier_history(cases: pd.DataFrame, targets: dict, prior: float) -> pd.Series:
    """Per case: breach rate of that supplier's cases that had ALREADY ENDED before this case started
    (strictly causal); `prior` (the training-window breach rate) when there are none."""
    breach = breach_flags(cases, targets)
    out = pd.Series(prior, index=cases.index, dtype=float)
    for _, g in cases.groupby("supplier_id"):
        s, e, y = g["start_time"].values, g["end_time"].values, breach.loc[g.index].values
        for pos, idx in enumerate(g.index):
            done = e <= s[pos]
            if done.any():
                out.loc[idx] = y[done].mean()
    return out


def supplier_history_for_case(cases: pd.DataFrame, case_row, targets: dict, prior: float) -> float:
    """Single-case version of `supplier_history` (used by the endpoint)."""
    same = cases[(cases["supplier_id"] == case_row["supplier_id"]) & (cases["end_time"] <= case_row["start_time"])]
    if same.empty:
        return float(prior)
    return float(breach_flags(same, targets).mean())


class EarlyRiskModel:
    """Loads early_risk_meta.json + one ONNX ensemble per k and scores a prefix."""

    def __init__(self, meta: dict, sessions: dict):
        self.meta, self._sessions = meta, sessions

    @classmethod
    def load(cls, model_dir: str | Path = MODEL_DIR) -> "EarlyRiskModel":
        import onnxruntime as ort

        model_dir = Path(model_dir)
        meta_path = model_dir / META_NAME
        if not meta_path.exists():
            raise FileNotFoundError(f"No early-risk model at {model_dir}; run scripts/export_early_risk.py")
        meta = json.loads(meta_path.read_text())
        sessions = {int(k): ort.InferenceSession(str(model_dir / m["file"]), providers=["CPUExecutionProvider"])
                    for k, m in meta["models"].items()}
        return cls(meta, sessions)

    @property
    def available_k(self) -> list[int]:
        return sorted(self._sessions)

    def logits(self, k: int, acts: np.ndarray, feats: np.ndarray, static: np.ndarray) -> np.ndarray:
        sess = self._sessions[k]
        return sess.run(None, {"acts": acts.astype(np.int64), "step_feats": feats.astype(np.float32),
                               "static": static.astype(np.float32)})[0]

    def probability(self, k: int, acts, feats, static) -> np.ndarray:
        m = self.meta["models"][str(k)]
        z = self.logits(k, acts, feats, static)
        return 1.0 / (1.0 + np.exp(-(m["platt_a"] * z + m["platt_b"])))

    def score_case(self, case_row, events: pd.DataFrame, cases: pd.DataFrame, k: int) -> dict:
        """Score one case as of its k-th event. events: that case's events (case_id, activity, timestamp)."""
        if k not in self._sessions:
            raise ValueError(f"k must be one of {self.available_k}")
        ev = events.sort_values(["timestamp", "activity"], kind="stable")
        if len(ev) < k:
            raise EarlyRiskNotApplicable(f"case has only {len(ev)} events; needs at least {k}")
        ts = pd.to_datetime(ev["timestamp"]).values
        offsets = (ts[:k] - ts[0]) / np.timedelta64(1, "h")
        tgt = target_hours(self.meta["target"]["targets_hours"], case_row["category"])
        if offsets[-1] > tgt:
            raise EarlyRiskNotApplicable(
                f"case was already {offsets[-1]:.0f}h old at event {k}, past its {tgt:.0f}h target -- breach is determined")
        hist = supplier_history_for_case(cases, case_row, self.meta["target"]["targets_hours"],
                                         self.meta["target"]["prior_breach_rate"])
        acts, feats = sequence_arrays(ev["activity"].tolist(), offsets, self.meta["vocab"], k)
        static = static_vector(self.meta, case_row["start_time"], case_row["category"], hist)
        p = float(self.probability(k, acts[None], feats[None], static[None])[0])
        return {"breach_probability": p, "elapsed_hours_at_k": float(offsets[-1]), "target_hours": tgt,
                "supplier_history": hist}
