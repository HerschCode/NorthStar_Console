"""Split-conformal prediction for the breach classifier: instead of a bare probability, return a *set* of labels that contains the true label
with probability >= 1 - alpha, provided calibration and new cases are exchangeable. {1} = confident breach, {0} = confident ok,
{0,1} = abstain (send to a human), {} = never happens for alpha < 0.5 here but is reported if it does.

Class-conditional (Mondrian): each class gets its own threshold, so the guarantee holds for breaches and non-breaches separately, which
matters when the classes are imbalanced. The guarantee is marginal and breaks under distribution shift: `coverage_report` is how to see that.
"""
from __future__ import annotations

import numpy as np


def fit(cal_y, cal_p, alpha: float = 0.1) -> dict:
    y, p = np.asarray(cal_y, int), np.asarray(cal_p, float)
    q = {}
    for c in (0, 1):
        scores = (1 - p[y == 1]) if c == 1 else p[y == 0]     # nonconformity of the true class
        n = len(scores)
        if n == 0:
            raise ValueError(f"no calibration examples of class {c}")
        level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
        q[c] = float(np.quantile(scores, level, method="higher"))
    return {"alpha": alpha, "q0": q[0], "q1": q[1], "n": int(len(y))}


def predict_sets(model: dict, p) -> list[tuple[int, ...]]:
    p = np.asarray(p, float)
    out = []
    for v in p:
        s = []
        if v <= model["q0"]:          # class 0 stays in the set if its nonconformity (= p) is within its threshold
            s.append(0)
        if 1 - v <= model["q1"]:
            s.append(1)
        out.append(tuple(s))
    return out


def coverage_report(sets, y) -> dict:
    y = np.asarray(y, int)
    cov = np.array([int(t in s) for s, t in zip(sets, y)])
    size = np.array([len(s) for s in sets])
    return {"coverage": float(cov.mean()), "coverage_breach": float(cov[y == 1].mean()) if (y == 1).any() else None,
            "coverage_ok": float(cov[y == 0].mean()) if (y == 0).any() else None, "abstain_rate": float((size == 2).mean()),
            "empty_rate": float((size == 0).mean()), "n": int(len(y))}
