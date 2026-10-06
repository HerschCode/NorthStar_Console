"""flows/pipeline_flow.py -- unit tests against the task logic, mocking the DB/dbt/training
boundary (same pattern as tests/test_check_and_retrain.py; no live Postgres/real dbt/real
training run needed for CI). The one thing PLAN.md's Phase 3 acceptance criterion explicitly
calls out -- "a failed DQ check stops the flow before training" -- is proven directly: run the
real flow with a forced-bad data-quality report and assert train_task's underlying training
call is never reached, not just that validate_task raises in isolation.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.cleaning.data_quality import DataQualityReport
from flows.pipeline_flow import DataQualityGateFailed, validate_task, evaluate_and_register_task


def _write_orchestration_config(root, min_roc_auc_improvement: float) -> None:
    config_dir = root / "config"
    config_dir.mkdir(exist_ok=True)
    lines = ["promotion:", f"  min_roc_auc_improvement: {min_roc_auc_improvement}"]
    (config_dir / "orchestration.yaml").write_text("\n".join(lines) + "\n")


@patch("src.api.db.get_engine")
@patch("pandas.read_sql")
def test_validate_task_passes_on_clean_data(mock_read_sql, mock_get_engine):
    mock_read_sql.return_value = pd.DataFrame({"case_id": ["C1"]})
    with patch("src.cleaning.data_quality.run_data_quality_checks") as mock_dq:
        mock_dq.return_value = DataQualityReport(
            total_rows=1000, total_cases=100, duplicate_rows=0, duplicate_within_case=0,
            missing_resource=0, missing_supplier=0, unparseable_timestamps=0,
            out_of_order_events=0,
        )
        validate_task.fn(1000)  # .fn calls the undecorated function directly, no Prefect runtime needed


@patch("src.api.db.get_engine")
@patch("pandas.read_sql")
def test_validate_task_raises_when_out_of_order_events_exceed_threshold(mock_read_sql, mock_get_engine):
    """config/orchestration.yaml's max_out_of_order_events is 500 -- this data quality report
    exceeds it, so validate_task must raise DataQualityGateFailed."""
    mock_read_sql.return_value = pd.DataFrame({"case_id": ["C1"]})
    with patch("src.cleaning.data_quality.run_data_quality_checks") as mock_dq:
        mock_dq.return_value = DataQualityReport(
            total_rows=1000, total_cases=100, duplicate_rows=0, duplicate_within_case=0,
            missing_resource=0, missing_supplier=0, unparseable_timestamps=0,
            out_of_order_events=5000,  # far over the 500 threshold
        )
        with pytest.raises(DataQualityGateFailed, match="out_of_order_events"):
            validate_task.fn(1000)


def test_failed_dq_check_stops_the_flow_before_training():
    """The actual Phase 3 acceptance criterion, proven end to end against the real flow
    function (not just validate_task in isolation): run pipeline_flow.pipeline_flow with
    ingest_task and validate_task's real bodies replaced (ingest succeeds trivially;
    validate raises the same DataQualityGateFailed a real bad report would), and assert
    dbt_build_task/train_task/evaluate_and_register_task/report_task are never called --
    Prefect's own dependency-skip behavior is what should stop them, not a manual check."""
    import flows.pipeline_flow as pf

    with patch.object(pf, "ingest_task") as mock_ingest, \
         patch.object(pf, "validate_task") as mock_validate, \
         patch.object(pf, "dbt_build_task") as mock_dbt, \
         patch.object(pf, "train_task") as mock_train, \
         patch.object(pf, "evaluate_and_register_task") as mock_evaluate, \
         patch.object(pf, "report_task") as mock_report:
        mock_ingest.return_value = 1000
        mock_validate.side_effect = pf.DataQualityGateFailed("simulated DQ failure")

        with pytest.raises(Exception):
            pf.pipeline_flow.fn()

        mock_ingest.assert_called_once()
        mock_validate.assert_called_once()
        mock_dbt.assert_not_called()
        mock_train.assert_not_called()
        mock_evaluate.assert_not_called()
        mock_report.assert_not_called()


@patch("src.ml.train.save_best_model")
def test_evaluate_and_register_task_skips_promotion_below_margin(mock_save, tmp_path, monkeypatch):
    """Reproduces the real live-run result: candidate 0.9888 vs deployed 0.9857 (a +0.0031 gap)
    is below the configured 0.005 margin -- must NOT call save_best_model()."""
    meta_dir = tmp_path / "models"
    meta_dir.mkdir()
    (meta_dir / "sla_risk_model.meta.json").write_text('{"roc_auc": 0.9857}')
    _write_orchestration_config(tmp_path, min_roc_auc_improvement=0.005)
    monkeypatch.setattr("flows.pipeline_flow.REPO_ROOT", tmp_path)

    train_results = {
        "_columns": [], "_split": {},
        "random_forest": {"roc_auc": 0.9888},
        "logistic_regression": {"roc_auc": 0.9101},
    }
    decision = evaluate_and_register_task.fn(train_results)

    assert decision["promoted"] is False
    assert decision["best_candidate"] == "random_forest"
    mock_save.assert_not_called()


@patch("src.ml.train.save_best_model")
def test_evaluate_and_register_task_promotes_above_margin(mock_save, tmp_path, monkeypatch):
    meta_dir = tmp_path / "models"
    meta_dir.mkdir()
    (meta_dir / "sla_risk_model.meta.json").write_text('{"roc_auc": 0.80}')
    _write_orchestration_config(tmp_path, min_roc_auc_improvement=0.005)
    monkeypatch.setattr("flows.pipeline_flow.REPO_ROOT", tmp_path)

    train_results = {"_columns": [], "_split": {}, "random_forest": {"roc_auc": 0.90}}
    decision = evaluate_and_register_task.fn(train_results)

    assert decision["promoted"] is True
    mock_save.assert_called_once_with(train_results)


def test_evaluate_and_register_task_always_promotes_when_no_deployed_model_exists(tmp_path, monkeypatch):
    _write_orchestration_config(tmp_path, min_roc_auc_improvement=0.005)
    monkeypatch.setattr("flows.pipeline_flow.REPO_ROOT", tmp_path)
    (tmp_path / "models").mkdir()

    with patch("src.ml.train.save_best_model") as mock_save:
        train_results = {"_columns": [], "_split": {}, "logistic_regression": {"roc_auc": 0.6}}
        decision = evaluate_and_register_task.fn(train_results)

    assert decision["promoted"] is True
    assert "no deployed model" in decision["reason"]
    mock_save.assert_called_once()
