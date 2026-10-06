"""Uplift (heterogeneous treatment effect) estimators and evaluation, validated on semi-synthetic data in
scripts/uplift_validation.py. Everything is in terms of *benefit* = reduction in breach probability
(tau = P(breach | no treatment) - P(breach | treatment)); positive tau means the action helps.

Estimators (own implementation on scikit-learn; econml / causalml are not required):
- T-learner: one outcome model per arm; tau_hat(x) = mu0(x) - mu1(x).
- X-learner (Kunzel et al. 2019): impute each unit's counterfactual with the other arm's model, regress the
  imputed effects per arm, blend by the propensity (constant here: randomized assignment).
Evaluation: Qini curve / AUUC from observed outcomes, and an oracle curve from the planted true effect.
"""
from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor


def _rf_clf(seed):
    return RandomForestClassifier(n_estimators=100, min_samples_leaf=20, n_jobs=1, random_state=seed)


def _rf_reg(seed):
    return RandomForestRegressor(n_estimators=100, min_samples_leaf=20, n_jobs=1, random_state=seed)


def _p1(model, X):
    """P(class 1); constant if a training arm happened to contain a single class."""
    if len(model.classes_) == 1:
        return np.full(len(X), float(model.classes_[0]))
    return model.predict_proba(X)[:, 1]


def fit_t_learner(X, y, t, seed=0):
    m0 = _rf_clf(seed).fit(X[t == 0], y[t == 0])
    m1 = _rf_clf(seed + 1).fit(X[t == 1], y[t == 1])
    return lambda Xn: _p1(m0, Xn) - _p1(m1, Xn)


def fit_x_learner(X, y, t, seed=0):
    m0 = _rf_clf(seed).fit(X[t == 0], y[t == 0])
    m1 = _rf_clf(seed + 1).fit(X[t == 1], y[t == 1])
    d1 = _p1(m0, X[t == 1]) - y[t == 1]          # treated: imputed control breach prob - observed breach
    d0 = y[t == 0] - _p1(m1, X[t == 0])          # control: observed breach - imputed treated breach prob
    g1 = _rf_reg(seed + 2).fit(X[t == 1], d1)
    g0 = _rf_reg(seed + 3).fit(X[t == 0], d0)
    e = float(t.mean())                          # propensity (randomized, constant)
    return lambda Xn: (1 - e) * g1.predict(Xn) + e * g0.predict(Xn)


def qini_curve(score, y, t, n_points=101):
    """Observed-outcome Qini curve. Rank by score (high = expected to benefit most); at each top fraction
    the gain is (avoided-breach rate among treated - avoided-breach rate among controls) * n_in_prefix.
    Returns (fractions, gain, random_baseline_gain)."""
    order = np.argsort(-score, kind="stable")
    y_s, t_s = np.asarray(y)[order], np.asarray(t)[order]
    n = len(y_s)
    fr, gain = np.linspace(0, 1, n_points), np.zeros(n_points)
    saved = 1 - y_s                                # benefit outcome: breach avoided
    cs_t, cs_c = np.cumsum(saved * (t_s == 1)), np.cumsum(saved * (t_s == 0))
    n_t, n_c = np.cumsum(t_s == 1), np.cumsum(t_s == 0)
    for i, f in enumerate(fr):
        k = int(round(f * n))
        if k == 0 or n_t[k - 1] == 0 or n_c[k - 1] == 0:
            continue
        gain[i] = (cs_t[k - 1] / n_t[k - 1] - cs_c[k - 1] / n_c[k - 1]) * k
    return fr, gain, fr * gain[-1]


def oracle_curve(score, true_tau, n_points=101):
    """Cumulative TRUE benefit (expected breaches avoided) if the top fraction by `score` is treated.
    Only computable on simulated data, where the planted effect is known."""
    order = np.argsort(-score, kind="stable")
    cum = np.concatenate([[0.0], np.cumsum(np.asarray(true_tau)[order])])
    n = len(order)
    fr = np.linspace(0, 1, n_points)
    gain = np.interp(fr * n, np.arange(n + 1), cum)
    return fr, gain, fr * cum[-1]


def auuc(fr, gain, random_gain):
    """Area between a gain curve and the random-targeting diagonal (Qini coefficient); positive = better
    than random. Trapezoid rule on the fraction axis, in expected-cases-avoided units."""
    d = gain - random_gain
    return float(np.sum((d[1:] + d[:-1]) / 2 * np.diff(fr)))


def difference_in_means(y, t):
    """Observed average benefit of treatment: breach rate(control) - breach rate(treated), with a 95% CI."""
    y, t = np.asarray(y, dtype=float), np.asarray(t)
    a, b = y[t == 0], y[t == 1]
    est = float(a.mean() - b.mean())
    se = float(np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))
    return est, (est - 1.96 * se, est + 1.96 * se)
