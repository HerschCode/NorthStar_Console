"""Regression tests for the calibration bug found 2026-09-25: the served isotonic calibrator was fitted on the
forest's own training rows, so served ROC-AUC (0.665) fell far below the raw forest's (0.986)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score

from src.ml.calibration import HeldOutCalibratedClassifier
from src.ml.train import calibrate_model, choose_calibration_method, fit_calibrated

try:
    from sklearn.frozen import FrozenEstimator
except ImportError:  # pragma: no cover
    FrozenEstimator = None


def _data(n=3000, pos_rate=0.97, seed=0):
    """Noisy, time-ordered data where the label is only partly predictable, with a given positive rate."""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, 6)), columns=list("abcdef"))
    logit = 1.5 * X["a"] + 1.0 * X["b"] + rng.normal(scale=1.0, size=n)
    thr = np.quantile(logit, 1 - pos_rate)
    y = pd.Series((logit > thr).astype(int))
    return X, y


def _forest(X, y):
    return RandomForestClassifier(n_estimators=100, max_depth=8, class_weight="balanced", random_state=0).fit(X, y)


@pytest.mark.parametrize("pos_rate", [0.97, 0.5, 0.27])
def test_served_model_ranks_within_0_01_of_raw(pos_rate):
    """The requirement: served ROC-AUC within 0.01 of the raw model's, on the held-out window."""
    X, y = _data(pos_rate=pos_rate)
    cut = int(len(y) * 0.8)
    Xtr, ytr, Xte, yte = X.iloc[:cut], y.iloc[:cut], X.iloc[cut:], y.iloc[cut:]
    base, served, method, _ = fit_calibrated(_forest, Xtr, ytr)
    raw_auc = roc_auc_score(yte, base.predict_proba(Xte)[:, 1])
    served_auc = roc_auc_score(yte, served.predict_proba(Xte)[:, 1])
    assert abs(served_auc - raw_auc) <= 0.01, (method, raw_auc, served_auc)


def test_sigmoid_is_strictly_monotonic_so_roc_auc_equals_raw_exactly():
    X, y = _data()
    cut = int(len(y) * 0.8)
    base = _forest(X.iloc[:cut], y.iloc[:cut])
    cal = calibrate_model(base, X.iloc[2000:cut], y.iloc[2000:cut], "sigmoid")
    Xte, yte = X.iloc[cut:], y.iloc[cut:]
    raw, out = base.predict_proba(Xte)[:, 1], cal.predict_proba(Xte)[:, 1]
    assert roc_auc_score(yte, out) == pytest.approx(roc_auc_score(yte, raw), abs=1e-12)
    # strictly monotonic: no new ties, and the order of any two distinct raw scores is preserved
    assert len(np.unique(out)) == len(np.unique(raw))
    order = np.argsort(raw, kind="stable")
    assert np.all(np.diff(out[order]) >= 0)


@pytest.mark.skipif(FrozenEstimator is None, reason="needs sklearn >= 1.6")
def test_old_in_sample_isotonic_is_the_bug_it_loses_ranking_and_creates_ties():
    X, y = _data()
    cut = int(len(y) * 0.8)
    Xtr, ytr, Xte, yte = X.iloc[:cut], y.iloc[:cut], X.iloc[cut:], y.iloc[cut:]
    forest = _forest(Xtr, ytr)
    old = CalibratedClassifierCV(FrozenEstimator(forest), method="isotonic").fit(Xtr, ytr)   # fitted on train rows
    raw, bad = forest.predict_proba(Xte)[:, 1], old.predict_proba(Xte)[:, 1]
    assert len(np.unique(bad)) < 0.5 * len(np.unique(raw))          # coarse step function -> heavy ties
    assert roc_auc_score(yte, bad) < roc_auc_score(yte, raw) - 0.02  # and worse ranking


def test_calibration_uses_only_the_latest_slice_of_the_training_window():
    X, y = _data(n=1000)
    seen = {}

    def spy_fit(Xf, yf):
        seen["fit_last"] = Xf.index.max()
        return _forest(Xf, yf)

    base, cal, _, info = fit_calibrated(spy_fit, X, y, cal_fraction=0.2)
    assert info == {"fit_rows": 800, "calibration_rows": 200}
    assert seen["fit_last"] == 799            # forest never saw rows 800+, which the calibrator uses


def test_sigmoid_is_chosen_when_there_are_too_few_negatives_for_isotonic():
    X, y = _data(pos_rate=0.99)
    base = _forest(X.iloc[:2000], y.iloc[:2000])
    assert choose_calibration_method(base, X.iloc[2000:2400], y.iloc[2000:2400]) == "sigmoid"


def test_calibrator_works_with_a_handful_of_negatives_and_rejects_single_class():
    X, y = _data(pos_rate=0.99, n=1200)
    base = _forest(X.iloc[:800], y.iloc[:800])
    Xc, yc = X.iloc[800:], y.iloc[800:].copy()
    yc.iloc[:] = 1
    lowest = np.argsort(base.predict_proba(Xc)[:, 1])[:2]
    yc.iloc[lowest] = 0                          # only two negatives, and they are the lowest-scored rows
    p = HeldOutCalibratedClassifier(base, "sigmoid").fit(Xc, yc).predict_proba(Xc)[:, 1]
    assert ((p >= 0) & (p <= 1)).all()
    with pytest.raises(ValueError, match="both classes"):
        HeldOutCalibratedClassifier(base, "sigmoid").fit(Xc, pd.Series(np.ones(len(Xc), dtype=int)))


def test_deployed_model_meta_records_served_and_raw_metrics_separately():
    meta_path = Path("models/sla_risk_model.meta.json")
    meta = json.loads(meta_path.read_text())
    assert {"served_model", "raw_model", "calibration_method"} <= set(meta)
    assert meta["roc_auc"] == meta["served_model"]["roc_auc"]
    assert abs(meta["served_model"]["roc_auc"] - meta["raw_model"]["roc_auc"]) <= 0.01


def test_deployed_bundle_is_the_held_out_calibrator():
    import joblib
    bundle = joblib.load("models/sla_risk_model.joblib")
    assert isinstance(bundle["model"], HeldOutCalibratedClassifier)
    assert bundle["calibration_method"] == bundle["model"].method


def test_fit_calibrated_falls_back_to_raw_and_says_so_when_calibration_would_invert_ranking():
    X, y = _data(n=1000)
    y_flipped_tail = y.copy()
    y_flipped_tail.iloc[800:] = 1 - y_flipped_tail.iloc[800:]      # calibration slice contradicts the fit slice
    base, served, method, info = fit_calibrated(_forest, X, y_flipped_tail)
    assert method == "none" and served is base and "slope" in info["calibration_error"]


def test_runtime_sklearn_matches_the_version_the_deployed_model_was_pickled_with():
    """Regression (2026-10-03): after the resample, gradient boosting won model selection and its pickle
    embeds sklearn-internal loss classes, so loading it on a different sklearn raised an obscure
    "No module named '_loss'" in CI (and would have in production). requirements.txt now pins sklearn;
    this fails with a readable message if the pin and the committed artifact ever drift apart."""
    import json
    import sklearn

    meta = json.loads(Path("models/sla_risk_model.meta.json").read_text())
    assert meta.get("sklearn_version") == sklearn.__version__, (
        f"models/sla_risk_model.joblib was trained with scikit-learn {meta.get('sklearn_version')} "
        f"but {sklearn.__version__} is installed -- retrain, or fix the pin in requirements.txt")


def test_deployed_model_was_trained_with_the_current_feature_code():
    """Fails when src/ml/features.py changes without a retrain (the served model would see features
    computed differently from how it was trained -- this happened on 2026-10-03 after the look-ahead fix)."""
    import json
    from pathlib import Path
    from src.ml.features import feature_code_hash

    meta = json.loads(Path("models/sla_risk_model.meta.json").read_text(encoding="utf-8"))
    assert meta.get("feature_code_sha256") == feature_code_hash(), (
        "src/ml/features.py changed since models/sla_risk_model.joblib was trained -- run `python -m src.ml.train` "
        "and commit the regenerated model and meta.json")
