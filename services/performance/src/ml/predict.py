import joblib
import pandas as pd
from pathlib import Path


def load_model(model_path: str | Path = "models/sla_risk_model.joblib") -> dict:
    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(f"No trained model found at {path}. Run src/ml/train.py first.")
    return joblib.load(path)


def predict_sla_risk(features: pd.DataFrame, model_bundle: dict) -> pd.DataFrame:
    model = model_bundle["model"]
    expected_columns = model_bundle["columns"]

    aligned = features.reindex(columns=expected_columns, fill_value=0)
    probabilities = model.predict_proba(aligned)[:, 1]

    result = pd.DataFrame({"breach_probability": probabilities})
    result["risk_level"] = pd.cut(
        result["breach_probability"],
        bins=[-0.01, 0.3, 0.6, 1.0],
        labels=["LOW", "MEDIUM", "HIGH"],
    )
    return result


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events
    from src.transformation.build_process_cases import build_process_cases
    from src.analytics.sla_analysis import load_sla_targets, evaluate_sla
    from src.ml.features import build_features

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    evaluated = evaluate_sla(cases, load_sla_targets())

    X, _ = build_features(evaluated)
    bundle = load_model()
    predictions = predict_sla_risk(X, bundle)

    output = pd.concat([evaluated[["case_id"]].reset_index(drop=True), predictions], axis=1)
    print(output.sort_values("breach_probability", ascending=False).head(10).to_string(index=False))
