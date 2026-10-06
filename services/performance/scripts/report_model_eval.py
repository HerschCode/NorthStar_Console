"""
Read the committed model bundle and sidecar metadata, write reports/p1_model_eval.json.

Offline -- no database or API keys needed. Both files are committed:
  models/sla_risk_model.joblib   (brier_score, feature_count, calibration_curve)
  models/sla_risk_model.meta.json (roc_auc, trained_at, row counts)

To regenerate the model from live data: python -m src.ml.train (requires .env).
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import joblib


def main():
    meta_path = REPO_ROOT / "models" / "sla_risk_model.meta.json"
    bundle_path = REPO_ROOT / "models" / "sla_risk_model.joblib"

    if not meta_path.exists() or not bundle_path.exists():
        print("Model not found. Run: python -m src.ml.train  (requires .env with DB credentials)")
        sys.exit(1)

    meta = json.loads(meta_path.read_text())
    bundle = joblib.load(bundle_path)

    brier = bundle.get("brier_score")
    report = {
        "model": meta["model_name"],
        "trained_at": meta["trained_at"],
        "roc_auc": meta["roc_auc"],
        "brier_score": round(brier, 4) if brier is not None else None,
        "feature_count": len(bundle.get("columns", [])),
        "train_rows": meta["train_row_count"],
        "test_rows": meta["test_row_count"],
    }

    out = REPO_ROOT / "reports" / "p1_model_eval.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
