import numpy as np
import pytest

from src.roi.causal import (
    auuc, difference_in_means, fit_t_learner, fit_x_learner, oracle_curve, qini_curve)


def _rct(n=6000, seed=0):
    """Randomized data with a KNOWN heterogeneous effect: the action helps only when x0 > 0 (tau = 0.4)."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 4))
    t = rng.integers(0, 2, n)
    p0 = np.full(n, 0.5)
    tau = np.where(X[:, 0] > 0, 0.4, 0.0)
    u = rng.random(n)
    y = np.where(t == 1, u < p0 - tau, u < p0).astype(int)
    return X, y, t, tau


def test_difference_in_means_recovers_average_effect_with_ci():
    X, y, t, tau = _rct()
    est, (lo, hi) = difference_in_means(y, t)
    assert lo < tau.mean() < hi
    assert abs(est - tau.mean()) < 0.03


@pytest.mark.parametrize("fit", [fit_t_learner, fit_x_learner])
def test_learners_recover_the_planted_heterogeneous_effect(fit):
    X, y, t, tau = _rct(seed=1)
    Xte, _, _, tau_te = _rct(n=3000, seed=2)
    tau_hat = fit(X, y, t, seed=0)(Xte)
    assert np.corrcoef(tau_hat, tau_te)[0, 1] > 0.7
    assert abs(tau_hat.mean() - tau_te.mean()) < 0.05          # ATE bias within 5 points
    assert tau_hat[Xte[:, 0] > 0].mean() > tau_hat[Xte[:, 0] <= 0].mean() + 0.2


def test_ranking_by_learner_beats_random_and_a_wrong_ranking_on_oracle_qini():
    X, y, t, tau = _rct(seed=3)
    Xte, yte, tte, tau_te = _rct(n=3000, seed=4)
    score = fit_x_learner(X, y, t)(Xte)
    good = auuc(*oracle_curve(score, tau_te))
    wrong = auuc(*oracle_curve(-Xte[:, 0], tau_te))            # ranks the non-responders first
    rnd = auuc(*oracle_curve(np.random.default_rng(0).random(len(tau_te)), tau_te))
    assert good > 0 > wrong and good > abs(rnd) + 20


def test_observed_qini_agrees_in_sign_with_the_oracle():
    Xte, yte, tte, tau_te = _rct(n=6000, seed=5)
    fr, gain, rnd = qini_curve(Xte[:, 0], yte, tte)              # ranks responders first (x0 high)
    assert auuc(fr, gain, rnd) > 20
    fr, gain, rnd = qini_curve(-Xte[:, 0], yte, tte)
    assert auuc(fr, gain, rnd) < -20


def test_oracle_curve_endpoints_are_zero_and_total_true_effect():
    tau = np.array([0.3, 0.1, -0.1, 0.2])
    fr, gain, rnd = oracle_curve(tau, tau)
    assert gain[0] == 0 and gain[-1] == pytest.approx(tau.sum())
    assert auuc(fr, gain, rnd) > 0                                # best ordering beats the diagonal


def test_committed_semi_synthetic_validation_meets_its_pre_registered_acceptance_criteria():
    import json
    from pathlib import Path

    d = json.loads(Path("reports/uplift_validation.json").read_text())
    for name, sc in d["scenarios"].items():
        assert all(sc["acceptance"].values()), (name, sc["acceptance"])
        s = sc["summary"]
        assert abs(s["x_learner_ate"]["mean"] - s["ate_true"]["mean"]) <= 0.02
        assert s["highest_risk_oracle_auuc"]["mean"] < max(s["x_learner_oracle_auuc"]["mean"], s["t_learner_oracle_auuc"]["mean"])
