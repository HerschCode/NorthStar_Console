"""
Real SHAP-based per-prediction explainability, closing the FUTURE_IMPROVEMENTS.md gap
that src/ml/explain.py's docstring named honestly rather than overclaimed: "this is
not SHAP -- it's a lightweight approximation." That module is kept as-is (global
feature importance filtered to what's active in a row) since it needs no background
dataset and nothing currently depends on this file changing its behavior --
explain_shap.py is additive, the actual SHAP explanation for when a real one is
wanted, not a breaking replacement.

shap.Explainer auto-dispatches to the right underlying algorithm for whichever model
type won training (src/ml/train.py compares logistic regression, random forest, and
gradient boosting) -- TreeExplainer for the tree-based models (exact, fast), the
appropriate one for logistic regression too. This module doesn't hardcode which
explainer to use; it lets shap decide based on the actual fitted estimator.
"""
import pandas as pd


def _unwrap_for_shap(model):
    """SHAP's TreeExplainer needs the raw sklearn estimator, not a
    CalibratedClassifierCV wrapper around it. Unwrap if needed."""
    try:
        if hasattr(model, "base") and hasattr(model, "method"):      # HeldOutCalibratedClassifier
            return model.base
        from sklearn.calibration import CalibratedClassifierCV
        if isinstance(model, CalibratedClassifierCV):
            # calibrated_classifiers_ is a list of (base_estimator, calibrator) pairs.
            # All base estimators are the same fitted model — take the first.
            model = model.calibrated_classifiers_[0].estimator
        # sklearn >= 1.6 wraps an already-fitted base model in FrozenEstimator
        if type(model).__name__ == "FrozenEstimator":
            model = model.estimator
    except Exception:
        pass
    return model


def explain_prediction_shap(
    model, background_data: pd.DataFrame, feature_row: pd.DataFrame, top_n: int = 5
) -> list[dict]:
    """Real per-prediction SHAP values for a single row (as a 1-row DataFrame, not a
    Series, since shap's Explainer expects the same shape as training data). Returns
    the top_n features by |SHAP value| -- magnitude, not just importance filtered by
    presence, so a feature can show up even if this specific row's approximation
    version would have missed it (e.g. a numeric feature whose specific value pushes
    the prediction meaningfully despite not being "the globally most important"
    feature overall)."""
    import shap

    # TreeExplainer needs numeric input; one-hot columns arrive as bool/object from get_dummies
    background_data, feature_row = background_data.astype(float), feature_row.astype(float)
    explainer = shap.Explainer(_unwrap_for_shap(model), background_data)
    shap_values = explainer(feature_row)

    # shap_values.values has shape (1, n_features) for a single row, or
    # (1, n_features, n_classes) for some multi-class/probability outputs -- take the
    # positive-class column when a class dimension exists, since predict_sla_risk's
    # breach_probability is specifically P(breach), not an average across classes.
    values = shap_values.values[0]
    if values.ndim == 2:
        values = values[:, -1]

    ranked = sorted(
        zip(feature_row.columns, values, feature_row.iloc[0]),
        key=lambda t: abs(t[1]),
        reverse=True,
    )

    return [
        {"feature": f, "shap_value": round(float(v), 4), "value": val}
        for f, v, val in ranked[:top_n]
    ]


def explain_batch_shap(
    model, background_data: pd.DataFrame, features: pd.DataFrame, top_n: int = 5
) -> pd.DataFrame:
    """Batch version -- computes the explainer once and reuses it across every row,
    rather than explain_prediction_shap's per-row explainer construction, since
    building a shap.Explainer isn't free and this project's dashboard/API scenarios
    explain many cases at once (e.g. GET /metrics/sla-risk-distribution's batch
    scoring), not one case in isolation."""
    import shap

    background_data, features = background_data.astype(float), features.astype(float)
    explainer = shap.Explainer(_unwrap_for_shap(model), background_data)
    shap_values = explainer(features)

    values = shap_values.values
    if values.ndim == 3:
        values = values[:, :, -1]

    explanations = []
    for i in range(len(features)):
        row_values = values[i]
        row_data = features.iloc[i]
        ranked = sorted(
            zip(features.columns, row_values, row_data),
            key=lambda t: abs(t[1]),
            reverse=True,
        )
        explanations.append([
            {"feature": f, "shap_value": round(float(v), 4), "value": val}
            for f, v, val in ranked[:top_n]
        ])

    return pd.DataFrame({"top_factors_shap": explanations})
