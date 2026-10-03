import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
try:
    from sklearn.frozen import FrozenEstimator as _FrozenEstimator
except ImportError:
    _FrozenEstimator = None
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    confusion_matrix,
)

from src.ml.calibration import HeldOutCalibratedClassifier
from src.ml.features import build_features


def time_based_split(cases: pd.DataFrame, time_col: str = "start_time", test_size: float = 0.2):
    """Split forward in time and keep purchase orders on one side of the split.

    Groups crossing the temporal cutoff are excluded so neither train nor test
    contains a purchase order whose cases straddle the boundary.
    """
    ordered = cases.sort_values(time_col)
    cutoff = int(len(ordered) * (1 - test_size))
    if "purchase_order_id" in cases.columns:
        cutoff_time = ordered.iloc[cutoff][time_col]
        group_ids = cases["purchase_order_id"].where(
            cases["purchase_order_id"].notna(), cases["case_id"]
        )
        grouped_times = pd.DataFrame({"group_id": group_ids, "time": cases[time_col]}).groupby(
            "group_id", sort=False
        )["time"].agg(["min", "max"])
        train_groups = grouped_times.index[grouped_times["max"] < cutoff_time]
        test_groups = grouped_times.index[grouped_times["min"] >= cutoff_time]
        if len(train_groups) == 0 or len(test_groups) == 0:
            raise ValueError("Purchase-order temporal split produced an empty train or test set")
        train_idx = cases.loc[group_ids.isin(train_groups)].sort_values(time_col).index
        test_idx = cases.loc[group_ids.isin(test_groups)].sort_values(time_col).index
        return train_idx, test_idx

    train_idx = ordered.index[:cutoff]
    test_idx = ordered.index[cutoff:]
    return train_idx, test_idx


def time_series_group_splits(cases: pd.DataFrame, n_splits: int = 5, time_col: str = "start_time"):
    """Yield expanding temporal folds without splitting a purchase order across sides."""
    if "purchase_order_id" in cases.columns:
        group_ids = cases["purchase_order_id"].astype("string")
        if "case_id" in cases.columns:
            group_ids = group_ids.fillna(cases["case_id"].astype("string"))
    elif "case_id" in cases.columns:
        group_ids = cases["case_id"].astype("string")
    else:
        group_ids = pd.Series(cases.index.astype(str), index=cases.index, dtype="string")

    grouped = pd.DataFrame({"group_id": group_ids, "time": cases[time_col]}).groupby(
        "group_id", sort=False
    )["time"].agg(["min", "max"]).sort_values("min")
    if len(grouped) <= n_splits:
        return

    splitter = TimeSeriesSplit(n_splits=n_splits)
    for train_positions, test_positions in splitter.split(grouped):
        candidate_train = grouped.iloc[train_positions]
        candidate_test = grouped.iloc[test_positions]
        test_start = candidate_test["min"].min()
        train_groups = candidate_train.index[candidate_train["max"] < test_start]
        test_groups = candidate_test.index[candidate_test["min"] >= test_start]
        if len(train_groups) == 0 or len(test_groups) == 0:
            continue
        yield cases.index[group_ids.isin(train_groups)], cases.index[group_ids.isin(test_groups)]


def train_models(
    X: pd.DataFrame, y: pd.Series, cases: pd.DataFrame, random_state: int = 42
) -> dict:
    train_idx, test_idx = time_based_split(cases)
    X_train, X_test = X.loc[train_idx], X.loc[test_idx]
    y_train, y_test = y.loc[train_idx], y.loc[test_idx]

    results = {}

    def _register(name, fit_fn):
        base, calibrated, method, info = fit_calibrated(fit_fn, X_train, y_train)
        results[name] = _evaluate(base, X_test, y_test)
        results[name]["model"] = base
        results[name]["calibrated_model"] = calibrated
        results[name]["calibration_method"] = method
        results[name]["calibration_info"] = info
        results[name]["calibrated_eval"] = _evaluate(calibrated, X_test, y_test)
        return base

    _register("logistic_regression",
              lambda X_, y_: LogisticRegression(max_iter=3000, class_weight="balanced").fit(X_, y_))

    forest = _register("random_forest", lambda X_, y_: RandomForestClassifier(
        n_estimators=200, max_depth=8, class_weight="balanced", random_state=random_state).fit(X_, y_))
    results["random_forest"]["feature_importances"] = dict(
        sorted(
            zip(X.columns, forest.feature_importances_),
            key=lambda kv: kv[1],
            reverse=True,
        )
    )

    # Gradient boosting -- a genuinely different algorithm family from the other two
    # (sequential error-correcting ensemble vs. a single linear model vs. a bagged
    # ensemble of independent trees), worth comparing rather than assuming random
    # forest is automatically the right choice. GradientBoostingClassifier doesn't
    # support class_weight directly (unlike the other two) -- sample_weight is the
    # equivalent mechanism, computed manually here for the same balanced effect.
    def _fit_boosting(X_, y_):
        counts = y_.value_counts()
        weight = y_.map({cls: len(y_) / (2 * count) for cls, count in counts.items()})
        return GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.1, random_state=random_state
        ).fit(X_, y_, sample_weight=weight)

    boosting = _register("gradient_boosting", _fit_boosting)
    results["gradient_boosting"]["feature_importances"] = dict(
        sorted(
            zip(X.columns, boosting.feature_importances_),
            key=lambda kv: kv[1],
            reverse=True,
        )
    )

    results["_columns"] = X.columns.tolist()
    # Training-time feature distributions for src/ml/feature_drift.py (PLAN.md Phase 4) -- fit on
    # the TRAINING rows only, on the raw (pre-one-hot) inputs, and saved into the model's
    # .meta.json by save_best_model() so drift is always measured against what THIS model saw.
    try:
        from src.ml.feature_drift import compute_baselines, load_drift_config
        from src.ml.features import raw_feature_frame
        _dcfg = load_drift_config()
        results["_feature_baselines"] = compute_baselines(
            raw_feature_frame(cases).loc[train_idx], _dcfg["n_bins"], _dcfg["max_categories"],
        )
    except Exception as exc:  # a drift-baseline failure must never block training
        print(f"Feature baselines skipped: {exc}")
        results["_feature_baselines"] = None
    results["_split"] = {
        "train_size": len(X_train),
        "test_size": len(X_test),
        "train_period_end": str(cases.loc[train_idx, "start_time"].max()),
        "test_period_start": str(cases.loc[test_idx, "start_time"].min()),
    }
    return results


def calibrate_model(model, X_cal, y_cal, method: str = "sigmoid"):
    """Fits a calibration layer on top of an ALREADY-FITTED model (frozen). X_cal/y_cal must be rows
    the model was NOT trained on: a random forest's predictions on its own training rows are pushed
    toward 0/1, so calibrating on them yields a coarse step function (isotonic) with huge ties -- the
    bug this project shipped until 2026-09-25 (served ROC-AUC 0.665 vs 0.986 raw; docs/calibration.md).
    method: "sigmoid" (Platt; strictly monotonic, so it cannot change ROC-AUC) or "isotonic".
    See src/ml/calibration.py for why this is not sklearn's CalibratedClassifierCV."""
    return HeldOutCalibratedClassifier(model, method).fit(X_cal, y_cal)


CALIBRATION_FRACTION = 0.2


MIN_NEGATIVES_FOR_ISOTONIC = 50


def choose_calibration_method(model, X_cal, y_cal) -> str:
    """Sigmoid unless isotonic wins on Brier score WITHOUT losing ROC-AUC. Judged out-of-sample inside the
    calibration slice with a 2-fold temporal swap (fit the calibrator on one half, score the other, both
    directions), and only when each class has >= MIN_NEGATIVES_FOR_ISOTONIC examples in the slice --
    isotonic is a step function whose steps are estimated from the minority class, and with a few
    negatives it creates ties. (First version of this rule had no minimum and picked isotonic on the
    ~97%-breach target, where it cost 0.075 ROC-AUC on the test window; docs/calibration.md.)"""
    n = len(y_cal)
    half = n // 2
    if min(int((y_cal == 0).sum()), int((y_cal == 1).sum())) < MIN_NEGATIVES_FOR_ISOTONIC:
        return "sigmoid"
    folds = [(slice(0, half), slice(half, n)), (slice(half, n), slice(0, half))]
    tot = {"sigmoid": [0.0, 0.0], "isotonic": [0.0, 0.0]}
    try:
        for fit_s, score_s in folds:
            if y_cal.iloc[fit_s].nunique() < 2 or y_cal.iloc[score_s].nunique() < 2:
                return "sigmoid"
            for m in tot:
                p = calibrate_model(model, X_cal.iloc[fit_s], y_cal.iloc[fit_s], m).predict_proba(X_cal.iloc[score_s])[:, 1]
                tot[m][0] += brier_score_loss(y_cal.iloc[score_s], p)
                tot[m][1] += roc_auc_score(y_cal.iloc[score_s], p)
    except ValueError:
        return "sigmoid"
    iso, sig = tot["isotonic"], tot["sigmoid"]
    return "isotonic" if iso[0] < sig[0] and iso[1] >= sig[1] - 1e-9 else "sigmoid"


def fit_calibrated(fit_fn, X_train, y_train, method: str = "auto", cal_fraction: float = CALIBRATION_FRACTION):
    """Temporal calibration split of the TRAINING window (rows must be in time order): the base model
    is fitted on the earliest (1 - cal_fraction) and the calibration layer on the latest cal_fraction.
    The test window is never touched. Returns (base_model, calibrated_model, method_used, info)."""
    cut = int(len(y_train) * (1 - cal_fraction))
    Xf, yf, Xc, yc = X_train.iloc[:cut], y_train.iloc[:cut], X_train.iloc[cut:], y_train.iloc[cut:]
    base = fit_fn(Xf, yf)
    info = {"fit_rows": len(yf), "calibration_rows": len(yc)}
    if yc.nunique() < 2:            # nothing to calibrate against; serve the raw model, say so
        return base, base, "none", info
    try:
        used = choose_calibration_method(base, Xc, yc) if method == "auto" else method
        return base, calibrate_model(base, Xc, yc, used), used, info
    except ValueError as exc:      # e.g. a Platt slope <= 0 would invert the ranking; never serve that
        info["calibration_error"] = str(exc)
        return base, base, "none", info


def _evaluate(model, X_test, y_test) -> dict:
    preds = model.predict(X_test)
    probs = model.predict_proba(X_test)[:, 1]

    # calibration_curve bins the test set into n_bins probability buckets and
    # computes the observed breach rate per bucket. fraction_of_positives[i]
    # is the actual proportion of breaches among cases the model scored in
    # bucket i. mean_predicted_value[i] is the mean predicted probability in
    # that bucket. A perfectly calibrated model has them equal.
    frac_pos, mean_pred = calibration_curve(y_test, probs, n_bins=10, strategy="quantile")

    return {
        "precision": precision_score(y_test, preds, zero_division=0),
        "recall": recall_score(y_test, preds, zero_division=0),
        "f1": f1_score(y_test, preds, zero_division=0),
        "roc_auc": roc_auc_score(y_test, probs),
        "pr_auc": average_precision_score(y_test, probs),
        "brier_score": brier_score_loss(y_test, probs),
        "confusion_matrix": confusion_matrix(y_test, preds).tolist(),
        # reliability diagram data — stored in bundle so API / dashboard can
        # surface it without re-computing at inference time
        "calibration_curve": {
            "fraction_of_positives": frac_pos.tolist(),
            "mean_predicted_value": mean_pred.tolist(),
        },
        # raw test-set probabilities — needed by save_best_model to compute the
        # training-time risk distribution baseline for /observability/prediction-drift
        "probs": probs.tolist(),
        "n_unique_scores": int(len(np.unique(probs))),
    }


def cross_validate_time_series(
    X: pd.DataFrame, y: pd.Series, cases: pd.DataFrame, model_name: str = "random_forest",
    n_splits: int = 5, random_state: int = 42,
) -> dict:
    """
    Cross-validation using sklearn's TimeSeriesSplit, NOT standard KFold -- standard
    k-fold shuffles (or at least doesn't respect chronological order across folds),
    which would reintroduce exactly the temporal leakage time_based_split() exists to
    avoid for the single train/test split. TimeSeriesSplit instead creates n_splits
    expanding-window folds: fold 1 trains on the earliest chunk and tests on the next
    chunk, fold 2 trains on both of those and tests on the chunk after, and so on --
    every fold's test set is chronologically after everything in its training set.

    Reports per-fold ROC-AUC plus mean/std -- the std matters as much as the mean: a
    model whose performance swings wildly across time windows is a real risk signal
    a single train/test split's single number can't show.
    """
    model_factories = {
        "logistic_regression": lambda: LogisticRegression(max_iter=3000, class_weight="balanced"),
        "random_forest": lambda: RandomForestClassifier(
            n_estimators=200, max_depth=8, class_weight="balanced", random_state=random_state
        ),
    }
    if model_name not in model_factories:
        raise ValueError(f"cross_validate_time_series only supports {list(model_factories)} (gradient_boosting needs per-fold sample_weight handling, not added yet)")

    fold_scores = []

    for train_idx, test_idx in time_series_group_splits(cases, n_splits=n_splits):
        X_train_fold, X_test_fold = X.loc[train_idx], X.loc[test_idx]
        y_train_fold, y_test_fold = y.loc[train_idx], y.loc[test_idx]

        if y_train_fold.nunique() < 2 or y_test_fold.nunique() < 2:
            continue  # a fold with only one class present can't compute ROC-AUC meaningfully -- skip rather than crash

        model = model_factories[model_name]()
        model.fit(X_train_fold, y_train_fold)
        probs = model.predict_proba(X_test_fold)[:, 1]
        fold_scores.append(roc_auc_score(y_test_fold, probs))

    if not fold_scores:
        return {"model_name": model_name, "n_splits_requested": n_splits, "n_folds_scored": 0, "fold_roc_auc": [], "mean_roc_auc": None, "std_roc_auc": None}

    return {
        "model_name": model_name,
        "n_splits_requested": n_splits,
        "n_folds_scored": len(fold_scores),
        "fold_roc_auc": [round(s, 4) for s in fold_scores],
        "mean_roc_auc": round(float(np.mean(fold_scores)), 4),
        "std_roc_auc": round(float(np.std(fold_scores)), 4),
    }


def _metric_block(ev: dict) -> dict:
    return {"roc_auc": round(ev["roc_auc"], 4), "pr_auc": round(ev.get("pr_auc", 0), 4),
            "brier_score": round(ev.get("brier_score", 0), 4), "n_unique_scores": ev.get("n_unique_scores"),
            "n_scores": len(ev.get("probs", []))}


def save_best_model(results: dict, out_path: str | Path = "models/sla_risk_model.joblib"):
    import json
    from datetime import datetime, timezone

    # "Best" = highest ROC-AUC of the model actually SERVED (the calibrated one), not the raw forest.
    best_name = max(
        (k for k in results if not k.startswith("_")),
        key=lambda k: results[k].get("calibrated_eval", results[k])["roc_auc"],
    )
    best = results[best_name]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Save calibrated model as the primary serving artifact. The raw model is
    # also stored for comparison / debugging, but the API serves probabilities
    # from the calibrated wrapper so P(breach)=0.7 means ~70% of similarly
    # scored cases actually breach, not just a ranking score.
    joblib.dump(
        {
            "model": best.get("calibrated_model", best["model"]),
            "uncalibrated_model": best["model"],
            "columns": results["_columns"],
            "name": best_name,
            "feature_importances": best.get("feature_importances"),
            "calibration_curve": best.get("calibrated_eval", best).get("calibration_curve"),
            "brier_score": best.get("calibrated_eval", best).get("brier_score"),
            "calibration_method": best.get("calibration_method"),
        },
        out_path,
    )

    # Sidecar metadata, separate from the joblib bundle -- src/ml/retrain_trigger.py
    # reads this to decide whether retraining is due, without needing to unpickle
    # the (potentially large) model itself just to check a timestamp and a row count.
    # Compute test-set risk distribution using the same probability thresholds
    # predict.py uses at inference time (LOW < 0.3 <= MEDIUM < 0.6 <= HIGH), so
    # /observability/prediction-drift can compare production distributions against
    # this training-time baseline without having to re-run the model.
    best_eval = best.get("calibrated_eval", best)
    test_probs = best_eval.get("probs", [])
    if len(test_probs):
        import numpy as np
        arr = np.array(test_probs)
        train_risk_dist = {
            "LOW": int((arr < 0.3).sum()),
            "MEDIUM": int(((arr >= 0.3) & (arr < 0.6)).sum()),
            "HIGH": int((arr >= 0.6).sum()),
        }
        train_breach_rate = round(float(arr.mean()), 4)
    else:
        train_risk_dist = None
        train_breach_rate = None

    meta_path = out_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps({
        "model_name": best_name,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        # pickles are bound to the sklearn version that wrote them; recorded so a mismatch fails with a clear message
        "sklearn_version": __import__("sklearn").__version__,
        "train_row_count": results["_split"]["train_size"],
        "test_row_count": results["_split"]["test_size"],
        # roc_auc is the SERVED (calibrated) model's -- until 2026-09-25 this recorded the raw forest's
        # and hid a served-vs-raw ranking gap; both are now recorded separately below.
        "roc_auc": round(best_eval["roc_auc"], 4),
        "calibration_method": best.get("calibration_method"),
        "fit_row_count": (best.get("calibration_info") or {}).get("fit_rows"),
        "calibration_row_count": (best.get("calibration_info") or {}).get("calibration_rows"),
        "served_model": _metric_block(best_eval),
        "raw_model": _metric_block(best),
        "train_risk_distribution": train_risk_dist,
        "train_breach_rate": train_breach_rate,
        "feature_baselines": results.get("_feature_baselines"),
    }, indent=2))

    print(f"Saved best model ({best_name}, served ROC-AUC={best_eval['roc_auc']:.3f}, "
          f"raw ROC-AUC={best['roc_auc']:.3f}) to {out_path}")
    return best_name


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events
    from src.transformation.build_process_cases import build_process_cases
    from src.analytics.sla_analysis import load_sla_targets, evaluate_sla

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    evaluated = evaluate_sla(cases, load_sla_targets())

    X, y = build_features(evaluated)
    results = train_models(X, y, evaluated)

    print(f"Split: {results['_split']}\n")
    print(f"{'Model':<25} {'Prec':>6} {'Rec':>6} {'F1':>6} {'ROC-AUC':>8} {'PR-AUC':>7} {'Brier':>7}  (raw → calibrated)")
    print("-" * 90)
    for name in ["logistic_regression", "random_forest", "gradient_boosting"]:
        r = results[name]
        cal = r.get("calibrated_eval", r)
        print(
            f"{name:<25} {r['precision']:>6.3f} {r['recall']:>6.3f} {r['f1']:>6.3f} "
            f"{r['roc_auc']:>8.3f} {r.get('pr_auc', 0):>7.3f} {r.get('brier_score', 0):>7.4f}"
            f"  → Brier {cal.get('brier_score', 0):.4f}"
        )

    print("\nTop features (random forest):")
    for feat, importance in list(results["random_forest"]["feature_importances"].items())[:5]:
        print(f"  {feat}: {importance:.3f}")

    print("\nTime-series cross-validation (random forest, 5 folds):")
    cv = cross_validate_time_series(X, y, evaluated, model_name="random_forest")
    print(f"  {cv}")

    best_name = save_best_model(results)

    # MLflow model registry — non-fatal, skipped gracefully if mlflow is not
    # installed or no tracking server is configured (MLFLOW_TRACKING_URI unset
    # defaults to a local ./mlruns directory, which still gives a local audit trail).
    try:
        import mlflow
        import mlflow.sklearn

        mlflow.set_experiment("sla_risk_predictor")
        best = results[best_name]
        with mlflow.start_run(run_name=f"train_{best_name}"):
            mlflow.log_params({
                "model_type": best_name,
                "calibration_method": best.get("calibration_method"),
                "train_rows": results["_split"]["train_size"],
                "test_rows": results["_split"]["test_size"],
            })
            mlflow.log_metrics({
                "roc_auc": round(best["roc_auc"], 4),
                "precision": round(best["precision"], 4),
                "recall": round(best["recall"], 4),
                "f1": round(best["f1"], 4),
                "brier_score": round(best.get("brier_score", 0), 4),
            })
            served_eval = best.get("calibrated_eval", best)
            mlflow.log_metrics({"served_roc_auc": round(served_eval["roc_auc"], 4),
                                "served_brier_score": round(served_eval["brier_score"], 4)})
            # Log the fitted sklearn estimator (MLflow's default serializer rejects our custom calibration
            # wrapper as an untrusted type); the calibrator itself is two numbers (Platt a, b) or an isotonic
            # step function, recorded in the served bundle and .meta.json.
            cal = best.get("calibrated_model")
            if cal is not None and hasattr(cal, "a_"):
                mlflow.log_params({"platt_a": round(cal.a_, 4), "platt_b": round(cal.b_, 4)})
            mlflow.sklearn.log_model(
                best["model"],
                artifact_path="model",
                registered_model_name="sla_risk_predictor",
            )
            client = mlflow.tracking.MlflowClient()
            versions = client.search_model_versions("name='sla_risk_predictor'")
            if versions:
                latest = max(versions, key=lambda v: int(v.version))
                client.transition_model_version_stage(
                    name="sla_risk_predictor",
                    version=latest.version,
                    stage="Staging",
                    archive_existing_versions=True,
                )
                print(f"Registered sla_risk_predictor v{latest.version} → Staging")
    except Exception as mlflow_exc:
        print(f"MLflow registry skipped: {mlflow_exc}")
