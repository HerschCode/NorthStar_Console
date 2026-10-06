import json
from datetime import datetime, timedelta, timezone
import pytest

from src.ml.retrain_trigger import check_retrain_needed, load_model_metadata


def write_meta(path, trained_at: datetime, train_rows: int, test_rows: int):
    path.write_text(json.dumps({
        "model_name": "random_forest", "trained_at": trained_at.isoformat(),
        "train_row_count": train_rows, "test_row_count": test_rows, "roc_auc": 0.85,
    }))


def test_no_metadata_file_recommends_initial_training(tmp_path):
    result = check_retrain_needed(current_case_count=100, meta_path=tmp_path / "missing.json")
    assert result.should_retrain is True
    assert "initial training" in result.reasons[0]


def test_recent_training_small_growth_does_not_trigger(tmp_path):
    meta_path = tmp_path / "model.meta.json"
    now = datetime(2024, 6, 15, tzinfo=timezone.utc)
    write_meta(meta_path, now - timedelta(days=5), train_rows=800, test_rows=200)  # 1000 total

    result = check_retrain_needed(
        current_case_count=1050,  # 5% growth, under 20% threshold
        meta_path=meta_path, now=now,
    )
    assert result.should_retrain is False
    assert result.days_since_training == 5.0
    assert result.data_growth_pct == pytest.approx(0.05, abs=0.001)


def test_time_threshold_alone_triggers_retraining(tmp_path):
    meta_path = tmp_path / "model.meta.json"
    now = datetime(2024, 6, 15, tzinfo=timezone.utc)
    write_meta(meta_path, now - timedelta(days=45), train_rows=800, test_rows=200)  # well past 30-day default

    result = check_retrain_needed(current_case_count=1000, meta_path=meta_path, now=now)  # 0% growth
    assert result.should_retrain is True
    assert any("days since" in r for r in result.reasons)
    assert not any("case volume" in r for r in result.reasons)  # only the time reason should fire


def test_volume_threshold_alone_triggers_retraining(tmp_path):
    meta_path = tmp_path / "model.meta.json"
    now = datetime(2024, 6, 15, tzinfo=timezone.utc)
    write_meta(meta_path, now - timedelta(days=2), train_rows=800, test_rows=200)  # 1000 total, recent

    result = check_retrain_needed(current_case_count=1300, meta_path=meta_path, now=now)  # exactly 30% growth
    assert result.should_retrain is True
    assert any("case volume" in r for r in result.reasons)
    assert not any("days since" in r for r in result.reasons)  # only the volume reason should fire
    assert result.data_growth_pct == pytest.approx(0.30, abs=0.001)


def test_both_thresholds_exceeded_reports_both_reasons(tmp_path):
    meta_path = tmp_path / "model.meta.json"
    now = datetime(2024, 6, 15, tzinfo=timezone.utc)
    write_meta(meta_path, now - timedelta(days=60), train_rows=800, test_rows=200)

    result = check_retrain_needed(current_case_count=2000, meta_path=meta_path, now=now)  # 150% growth, 60 days
    assert result.should_retrain is True
    assert len(result.reasons) == 2


def test_custom_thresholds_are_respected(tmp_path):
    meta_path = tmp_path / "model.meta.json"
    now = datetime(2024, 6, 15, tzinfo=timezone.utc)
    write_meta(meta_path, now - timedelta(days=10), train_rows=1000, test_rows=0)

    # 10 days would trigger with a 7-day threshold, wouldn't with the 30-day default
    result = check_retrain_needed(current_case_count=1000, meta_path=meta_path, now=now, max_days_since_training=7)
    assert result.should_retrain is True


def test_load_model_metadata_returns_none_when_missing(tmp_path):
    assert load_model_metadata(tmp_path / "nope.json") is None
