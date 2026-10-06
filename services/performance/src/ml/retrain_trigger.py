"""
Retraining trigger logic -- real decision logic a scheduled job (cron, GitHub Actions
cron trigger, Cloud Scheduler) would call, checked here rather than assuming "run
train.py every night" is the right cadence. No scheduler is actually wired up in this
environment (that needs real infrastructure this project doesn't have running) -- this
module is the decision function such a scheduler would call, fully real and testable
on its own.

Two independent triggers, either one sufficient:
1. Time-based: more than `max_days_since_training` days have passed since the current
   model was trained (docs/ml-model.md's "when to retrain" named this as a real
   trigger; this is that logic, implemented rather than left as prose).
2. Volume-based: the number of cases available to train on has grown by more than
   `min_new_data_pct` since the model was last trained -- a model trained on last
   month's data volume may be missing a meaningful fraction of recent process
   behavior once enough new cases have accumulated.
3. Feature drift (PLAN.md Phase 4): at least config/drift.yaml's `retrain_min_alert_features`
   model inputs have a PSI >= `psi_alert` against their training-time baseline
   (src/ml/feature_drift.py). Optional -- pass the FeatureDriftReport in; None skips it.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RetrainRecommendation:
    should_retrain: bool
    reasons: list[str]
    days_since_training: float | None
    data_growth_pct: float | None


def load_model_metadata(meta_path: str | Path = "models/sla_risk_model.meta.json") -> dict | None:
    path = Path(meta_path)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def check_retrain_needed(
    current_case_count: int,
    meta_path: str | Path = "models/sla_risk_model.meta.json",
    max_days_since_training: int = 30,
    min_new_data_pct: float = 0.20,
    now: datetime | None = None,
    feature_drift=None,
) -> RetrainRecommendation:
    metadata = load_model_metadata(meta_path)
    now = now or datetime.now(timezone.utc)

    if metadata is None:
        return RetrainRecommendation(
            should_retrain=True, reasons=["no trained model found -- initial training needed"],
            days_since_training=None, data_growth_pct=None,
        )

    reasons = []

    trained_at = datetime.fromisoformat(metadata["trained_at"])
    days_since_training = (now - trained_at).total_seconds() / 86400
    if days_since_training > max_days_since_training:
        reasons.append(
            f"{days_since_training:.1f} days since last training, exceeds the "
            f"{max_days_since_training}-day threshold"
        )

    trained_row_count = metadata["train_row_count"] + metadata["test_row_count"]
    data_growth_pct = (
        (current_case_count - trained_row_count) / trained_row_count
        if trained_row_count > 0 else 0.0
    )
    if data_growth_pct > min_new_data_pct:
        reasons.append(
            f"case volume grew {data_growth_pct:.1%} since last training "
            f"({trained_row_count} -> {current_case_count}), exceeds the "
            f"{min_new_data_pct:.0%} threshold"
        )

    if feature_drift is not None and feature_drift.status == "alert":
        top = ", ".join(f"{f.feature} (PSI {f.psi:.2f})" for f in feature_drift.features if f.status == "alert")
        reasons.append(f"feature drift: {len(feature_drift.alert_features)} inputs past the PSI alert threshold -- {top}")

    return RetrainRecommendation(
        should_retrain=len(reasons) > 0, reasons=reasons,
        days_since_training=round(days_since_training, 1), data_growth_pct=round(data_growth_pct, 4),
    )


if __name__ == "__main__":
    # Illustrative standalone run -- a real scheduled job would fetch current_case_count
    # from GET /observability/pipeline-runs or a direct DB count, not hardcode it.
    result = check_retrain_needed(current_case_count=1500)
    print(result)
    if result.should_retrain:
        print("\nRetraining recommended:")
        for reason in result.reasons:
            print(f"  - {reason}")
    else:
        print("\nNo retraining needed yet.")
