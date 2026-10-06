"""Metrics a promotion decision needs beyond ROC-AUC: calibration error, ranking quality at the operating point, expected loss avoided."""
from __future__ import annotations

import numpy as np


def _arr(a):
    return np.asarray(a, dtype=float)


def roc_auc(y, p) -> float:
    """Rank-based AUC (Mann-Whitney), ties get half credit. NaN if only one class is present."""
    y, p = _arr(y), _arr(p)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    allv = np.concatenate([pos, neg])[order]
    i = 0
    while i < len(allv):  # average ranks over ties
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def brier(y, p) -> float:
    y, p = _arr(y), _arr(p)
    return float(np.mean((p - y) ** 2))


def ece(y, p, bins: int = 10) -> float:
    """Expected calibration error with equal-width bins: sum over bins of |mean(p) - mean(y)| weighted by bin share."""
    y, p = _arr(y), _arr(p)
    if len(y) == 0:
        return float("nan")
    idx = np.minimum((p * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def precision_at_k(y, p, k: int) -> float:
    y, p = _arr(y), _arr(p)
    k = max(1, min(int(k), len(y)))
    top = np.argsort(-p, kind="mergesort")[:k]
    return float(y[top].mean())


def lift_at_k(y, p, k: int) -> float:
    base = float(_arr(y).mean())
    return float("nan") if base == 0 else precision_at_k(y, p, k) / base


def expected_loss_avoided(y, p, k: int, loss_per_breach: float, cost_per_intervention: float, effect: float) -> float:
    """Net value of intervening on the top-k by score: caught breaches * effect * loss - k * intervention cost.
    `effect` (share of treated breaches prevented) is an ASSUMPTION unless a randomized holdout has measured it."""
    y, p = _arr(y), _arr(p)
    k = max(1, min(int(k), len(y)))
    top = np.argsort(-p, kind="mergesort")[:k]
    return float(y[top].sum() * effect * loss_per_breach - k * cost_per_intervention)


def bootstrap_ci(fn, y, p, n: int = 500, seed: int = 0, alpha: float = 0.05, groups=None) -> tuple[float, float]:
    """Percentile bootstrap CI for fn(y, p). With `groups` (e.g. purchase-order ids) whole groups are resampled, so related rows
    cannot make the interval look tighter than it is."""
    y, p = _arr(y), _arr(p)
    rng = np.random.default_rng(seed)
    vals = []
    if groups is None:
        for _ in range(n):
            i = rng.integers(0, len(y), len(y))
            v = fn(y[i], p[i])
            if not np.isnan(v):
                vals.append(v)
    else:
        groups = np.asarray(groups)
        uniq = np.unique(groups)
        index = {g: np.flatnonzero(groups == g) for g in uniq}
        for _ in range(n):
            pick = rng.choice(uniq, len(uniq))
            i = np.concatenate([index[g] for g in pick])
            v = fn(y[i], p[i])
            if not np.isnan(v):
                vals.append(v)
    lo, hi = np.quantile(vals, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def evaluate(y, p, k_share: float = 0.3, groups=None, n_boot: int = 300) -> dict:
    """The metric block stored in the registry for every candidate."""
    y, p = _arr(y), _arr(p)
    k = max(1, int(round(len(y) * k_share)))
    out = {
        "roc_auc": roc_auc(y, p), "brier": brier(y, p), "ece": ece(y, p),
        "precision_at_k": precision_at_k(y, p, k), "lift_at_k": lift_at_k(y, p, k), "k_share": k_share,
        "base_rate": float(y.mean()), "n_test": int(len(y)), "n_unique_scores": int(len(np.unique(p))),
    }
    lo, hi = bootstrap_ci(roc_auc, y, p, n=n_boot, groups=groups)
    out["roc_auc_ci95"] = [lo, hi]
    return out
