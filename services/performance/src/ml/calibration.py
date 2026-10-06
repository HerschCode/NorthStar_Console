"""Post-hoc probability calibration fitted on a HELD-OUT slice, not on the model's own training rows.

Why a custom wrapper instead of sklearn's CalibratedClassifierCV(FrozenEstimator(model)):
- CalibratedClassifierCV cross-fits the calibrator across 5 folds of the calibration slice and demands
  >= 5 members in each class. This project's configured SLA target is ~97% breach, so a calibration slice
  can hold only a handful of non-breaches; the wrapper here fits ONE calibrator on the whole slice and
  works with as few as one example of each class.
- Sigmoid (Platt) is strictly monotonic in the model score, so it cannot change ROC-AUC. That property is
  asserted in tests/test_calibration.py.

Background (docs/calibration.md): until 2026-09-25 the served model was an isotonic calibrator fitted on the
forest's own TRAINING rows. A forest scores its training rows near 0/1, so isotonic became a coarse step
function with many tied scores and the served ROC-AUC fell to 0.665 while the raw forest scored 0.986.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from sklearn.isotonic import IsotonicRegression


def _fit_platt(score: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Platt scaling p = 1 / (1 + exp(-(a * score + b))) with Platt's smoothed targets, so a slice with
    very few examples of a class does not drive the slope to infinity."""
    n_pos, n_neg = int(y.sum()), int(len(y) - y.sum())
    t = np.where(y == 1, (n_pos + 1.0) / (n_pos + 2.0), 1.0 / (n_neg + 2.0))

    def loss(w):
        z = w[0] * score + w[1]
        return float(np.sum(np.logaddexp(0, z) - t * z))      # cross-entropy against smoothed targets

    prior = np.log((n_pos + 1.0) / (n_neg + 1.0))
    res = minimize(loss, x0=np.array([1.0, prior]), method="L-BFGS-B")
    return float(res.x[0]), float(res.x[1])


class HeldOutCalibratedClassifier:
    """Wraps an ALREADY-FITTED binary classifier and maps its P(class 1) through a calibrator fitted on
    rows the classifier never saw. Exposes predict / predict_proba like an sklearn classifier."""

    def __init__(self, base, method: str = "sigmoid"):
        if method not in ("sigmoid", "isotonic"):
            raise ValueError("method must be 'sigmoid' or 'isotonic'")
        self.base = base
        self.method = method

    @property
    def classes_(self):
        return self.base.classes_

    def _raw(self, X) -> np.ndarray:
        return self.base.predict_proba(X)[:, 1]

    def fit(self, X_cal, y_cal):
        y = np.asarray(y_cal).astype(int)
        if len(set(y)) < 2:
            raise ValueError("calibration slice needs both classes")
        s = self._raw(X_cal)
        if self.method == "sigmoid":
            self.a_, self.b_ = _fit_platt(s, y)
            if self.a_ <= 0:      # would invert the ranking; refuse rather than serve a reversed score
                raise ValueError(f"Platt slope {self.a_:.3f} <= 0 on the calibration slice")
        else:
            self.iso_ = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(s, y)
        return self

    def _map(self, s: np.ndarray) -> np.ndarray:
        if self.method == "sigmoid":
            return 1.0 / (1.0 + np.exp(-(self.a_ * s + self.b_)))
        return self.iso_.predict(s)

    def predict_proba(self, X) -> np.ndarray:
        p = self._map(self._raw(X))
        return np.column_stack([1.0 - p, p])

    def predict(self, X) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)
