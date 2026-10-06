"""One-off: add `feature_baselines` to the DEPLOYED model's models/sla_risk_model.meta.json without
retraining or touching the model itself.

The deployed model was trained (2026-09-19) before src/ml/feature_drift.py existed, so its metadata
has no training-time feature distributions. Retraining just to get them would replace the served
model for a monitoring feature -- and the flow's promotion gate exists precisely to stop unneeded
replacements. Instead this recomputes the baselines from the SAME training rows the model saw:
src/ml/train.py::time_based_split's first 80% of cases by start_time (train_row_count in the
metadata records the size; this checks it still matches before writing anything).

Run: python -m scripts.backfill_feature_baselines [--print-only]
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.api.db import load_cases
from src.ml.feature_drift import compute_baselines, load_drift_config
from src.ml.features import raw_feature_frame
from src.ml.train import time_based_split

META_PATH = Path("models/sla_risk_model.meta.json")


def main(print_only: bool = False) -> int:
    load_dotenv()
    meta = json.loads(META_PATH.read_text())
    evaluated = evaluate_sla(load_cases(), load_sla_targets())
    train_idx, _ = time_based_split(evaluated)
    if len(train_idx) != meta["train_row_count"]:
        print(f"REFUSING: model was trained on {meta['train_row_count']} rows but the current data's "
              f"training split has {len(train_idx)} -- baselines would not describe this model's training set.")
        return 1

    cfg = load_drift_config()
    baselines = compute_baselines(raw_feature_frame(evaluated).loc[train_idx], cfg["n_bins"], cfg["max_categories"])
    print(f"baselines for {len(baselines)} features from {len(train_idx)} training rows")
    if print_only:
        return 0
    meta["feature_baselines"] = baselines
    META_PATH.write_text(json.dumps(meta, indent=2))
    print(f"wrote feature_baselines into {META_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main("--print-only" in sys.argv))
