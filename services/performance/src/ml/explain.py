"""
Per-prediction explainability for the SLA breach predictor.

Two strategies, both available:

  explain_shap()       — real SHAP TreeExplainer on the underlying Random Forest.
                         Gives signed, per-instance contributions: positive = pushed
                         breach probability UP, negative = pushed it DOWN. Sorted by
                         absolute magnitude so the most influential features come first.
                         This is the right tool; use it.

  explain_prediction() — legacy approximation kept for backward compatibility.
                         Uses global feature importance filtered to active features.
                         Not SHAP — see the note in the function docstring. Prefer
                         explain_shap() for all new code.
"""
import numpy as np
import pandas as pd

from src.ml.explain_shap import _unwrap_for_shap


def explain_shap(
    feature_row: pd.Series,
    model_bundle: dict,
    top_n: int = 5,
) -> list[dict]:
    """SHAP TreeExplainer explanation for one prediction row.

    Extracts the base RandomForest from the CalibratedClassifierCV wrapper,
    runs TreeExplainer on it, and returns the top_n features ranked by absolute
    SHAP value for the breach class (class 1).

    Returns:
        List of dicts with keys:
          feature   — readable feature name
          shap      — signed SHAP value (positive = pushes breach probability up)
          value     — actual feature value for this case
          direction — "↑ risk" or "↓ risk"
    """
    try:
        import shap
    except ImportError:
        return explain_prediction(feature_row, model_bundle.get("feature_importances", {}), top_n)

    calibrated = model_bundle["model"]
    columns = model_bundle["columns"]

    try:
        # handles HeldOutCalibratedClassifier (current), CalibratedClassifierCV and FrozenEstimator
        base_rf = _unwrap_for_shap(calibrated)
        if not hasattr(base_rf, "predict_proba"):
            raise AttributeError("no unwrappable base estimator")
    except (AttributeError, IndexError):
        return explain_prediction(feature_row, model_bundle.get("feature_importances", {}), top_n)

    X = feature_row.reindex(index=columns, fill_value=0).to_frame().T
    explainer = shap.TreeExplainer(base_rf)

    sv = explainer.shap_values(X)
    # Handle SHAP <0.46 (list), >=0.46 Explanation (.values), and plain 3D ndarray
    if isinstance(sv, list):
        shap_breach = np.asarray(sv[1])[0]
    elif hasattr(sv, "values"):
        arr = np.asarray(sv.values)
        shap_breach = arr[0, :, 1] if arr.ndim == 3 else arr[0]
    else:
        arr = np.asarray(sv)
        shap_breach = arr[0, :, 1] if arr.ndim == 3 else arr[0]

    top_idx = np.argsort(np.abs(shap_breach))[::-1][:top_n]
    columns_list = list(columns)
    return [
        {
            "feature": columns_list[int(i)],
            "shap": round(float(shap_breach[i]), 4),
            "value": round(float(X.iloc[0, int(i)]), 4),
            "direction": "↑ risk" if shap_breach[i] > 0 else "↓ risk",
        }
        for i in top_idx
    ]


def explain_shap_batch(
    features: pd.DataFrame,
    model_bundle: dict,
    top_n: int = 5,
) -> list[list[dict]]:
    """Batch SHAP explanation — runs one TreeExplainer call for all rows (fast)."""
    try:
        import shap
    except ImportError:
        fi = model_bundle.get("feature_importances", {})
        return [explain_prediction(features.iloc[i], fi, top_n) for i in range(len(features))]

    calibrated = model_bundle["model"]
    columns = model_bundle["columns"]

    try:
        # handles HeldOutCalibratedClassifier (current), CalibratedClassifierCV and FrozenEstimator
        base_rf = _unwrap_for_shap(calibrated)
        if not hasattr(base_rf, "predict_proba"):
            raise AttributeError("no unwrappable base estimator")
    except (AttributeError, IndexError):
        fi = model_bundle.get("feature_importances", {})
        return [explain_prediction(features.iloc[i], fi, top_n) for i in range(len(features))]

    X = features.reindex(columns=columns, fill_value=0)
    explainer = shap.TreeExplainer(base_rf)
    sv = explainer.shap_values(X)

    if isinstance(sv, list):
        shap_all = np.asarray(sv[1])
    elif hasattr(sv, "values"):
        arr = np.asarray(sv.values)
        shap_all = arr[:, :, 1] if arr.ndim == 3 else arr
    else:
        arr = np.asarray(sv)
        shap_all = arr[:, :, 1] if arr.ndim == 3 else arr

    columns_list = list(columns)
    results = []
    for i in range(len(features)):
        row_shap = shap_all[i]
        top_idx = np.argsort(np.abs(row_shap))[::-1][:top_n]
        results.append([
            {
                "feature": columns_list[int(j)],
                "shap": round(float(row_shap[j]), 4),
                "value": round(float(X.iloc[i, int(j)]), 4),
                "direction": "↑ risk" if row_shap[j] > 0 else "↓ risk",
            }
            for j in top_idx
        ])
    return results


def explain_prediction(
    feature_row: pd.Series, feature_importances: dict, top_n: int = 5
) -> list[dict]:
    """Legacy approximation — kept for backward compatibility.

    Uses global feature importance filtered to active (non-zero) features.
    This is NOT SHAP: it doesn't account for feature interactions, signs, or
    per-instance magnitudes. Use explain_shap() for all new code.
    """
    active_features = feature_row[feature_row != 0].index.tolist()
    ranked = sorted(
        ((f, imp) for f, imp in feature_importances.items() if f in active_features),
        key=lambda kv: kv[1],
        reverse=True,
    )
    return [
        {"feature": f, "importance": round(imp, 4), "value": feature_row[f]}
        for f, imp in ranked[:top_n]
    ]


def explain_batch(
    features: pd.DataFrame, feature_importances: dict, top_n: int = 5
) -> pd.DataFrame:
    """Legacy batch approximation — see explain_prediction() note."""
    explanations = features.apply(
        lambda row: explain_prediction(row, feature_importances, top_n), axis=1
    )
    return pd.DataFrame({"top_factors": explanations})
