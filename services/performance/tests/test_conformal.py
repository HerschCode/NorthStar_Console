import numpy as np

from src.mlops import conformal as C


def draw(n, shift=0.0, seed=0):
    r = np.random.default_rng(seed)
    y = r.integers(0, 2, n)
    logit = (y * 2 - 1) * 1.0 + r.normal(0, 1.5, n) + shift   # a noisy scorer
    return y, 1 / (1 + np.exp(-logit))


def test_marginal_and_class_conditional_coverage_hold_when_exchangeable():
    alpha = 0.1
    cy, cp = draw(4000, seed=1)
    m = C.fit(cy, cp, alpha)
    ty, tp = draw(20000, seed=2)
    rep = C.coverage_report(C.predict_sets(m, tp), ty)
    assert rep["coverage"] >= 1 - alpha - 0.015
    assert rep["coverage_breach"] >= 1 - alpha - 0.03 and rep["coverage_ok"] >= 1 - alpha - 0.03
    assert 0 < rep["abstain_rate"] < 1                      # it abstains sometimes, but not always


def test_guarantee_visibly_breaks_under_shift():
    cy, cp = draw(4000, seed=1)
    m = C.fit(cy, cp, 0.1)
    ty, tp = draw(20000, shift=1.5, seed=3)                  # scores drift upward: the calibration no longer describes the stream
    assert C.coverage_report(C.predict_sets(m, tp), ty)["coverage"] < 0.88


def test_confident_cases_get_singletons():
    cy, cp = draw(4000, seed=1)
    m = C.fit(cy, cp, 0.1)
    assert C.predict_sets(m, [0.999])[0] == (1,) and C.predict_sets(m, [0.001])[0] == (0,)
