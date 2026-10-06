"""
scripts/check_and_retrain.py is the actual scheduled job .github/workflows/
retrain-check.yml's cron trigger runs -- these tests mock the DB and training
boundary (no live Postgres/real training run needed for CI) but exercise the
real dispatch logic: does it correctly skip retraining when not needed, and
correctly invoke the real training pipeline when it is.
"""
from unittest.mock import patch, MagicMock

from src.ml.retrain_trigger import RetrainRecommendation



@patch("scripts.check_and_retrain.save_best_model")
@patch("scripts.check_and_retrain.train_models")
@patch("scripts.check_and_retrain.build_features")
@patch("scripts.check_and_retrain.evaluate_sla")
@patch("scripts.check_and_retrain.load_sla_targets")
@patch("scripts.check_and_retrain.check_retrain_needed")
@patch("scripts.check_and_retrain.get_current_case_count")
def test_skips_retraining_when_not_needed(
    mock_case_count, mock_check, mock_targets, mock_eval, mock_features, mock_train, mock_save,
):
    mock_case_count.return_value = 1000
    mock_check.return_value = RetrainRecommendation(
        should_retrain=False, reasons=[], days_since_training=5.0, data_growth_pct=0.02,
    )

    from scripts.check_and_retrain import main
    exit_code = main()

    assert exit_code == 0
    mock_train.assert_not_called()
    mock_save.assert_not_called()


@patch("scripts.check_and_retrain.save_best_model")
@patch("scripts.check_and_retrain.train_models")
@patch("scripts.check_and_retrain.build_features")
@patch("scripts.check_and_retrain.evaluate_sla")
@patch("scripts.check_and_retrain.load_sla_targets")
@patch("scripts.check_and_retrain.load_cases")
@patch("scripts.check_and_retrain.check_retrain_needed")
@patch("scripts.check_and_retrain.get_current_case_count")
def test_retrains_when_needed(
    mock_case_count, mock_check, mock_load_cases, mock_targets, mock_eval,
    mock_features, mock_train, mock_save,
):
    mock_case_count.return_value = 5000
    mock_check.return_value = RetrainRecommendation(
        should_retrain=True,
        reasons=["case volume grew 25.0% since last training"],
        days_since_training=10.0, data_growth_pct=0.25,
    )
    mock_features.return_value = (MagicMock(), MagicMock())
    mock_train.return_value = {"_columns": [], "_split": {}}

    from scripts.check_and_retrain import main
    exit_code = main()

    assert exit_code == 0
    mock_load_cases.assert_called_once()
    mock_train.assert_called_once()
    mock_save.assert_called_once()


@patch("scripts.check_and_retrain.get_engine")
@patch("scripts.check_and_retrain.save_best_model")
@patch("scripts.check_and_retrain.train_models")
@patch("scripts.check_and_retrain.build_features")
@patch("scripts.check_and_retrain.load_evaluated_cases_from_dbt_marts")
@patch("scripts.check_and_retrain.load_cases")
@patch("scripts.check_and_retrain.check_retrain_needed")
@patch("scripts.check_and_retrain.get_current_case_count")
def test_retrains_from_dbt_marts_when_feature_source_env_var_is_set(
    mock_case_count, mock_check, mock_load_cases, mock_dbt_load,
    mock_features, mock_train, mock_save, mock_engine, monkeypatch,
):
    """PLAN.md Phase 2's optional flag: FEATURE_SOURCE=dbt_marts reads fct_cases instead of the
    raw-events path -- load_cases() (the raw-events path) must NOT be called, and
    load_evaluated_cases_from_dbt_marts() must be, verifying the dispatch, not the query result
    (that's verified separately, live, in docs/dbt-project.md)."""
    monkeypatch.setenv("FEATURE_SOURCE", "dbt_marts")
    mock_case_count.return_value = 5000
    mock_check.return_value = RetrainRecommendation(
        should_retrain=True,
        reasons=["case volume grew 25.0% since last training"],
        days_since_training=10.0, data_growth_pct=0.25,
    )
    mock_features.return_value = (MagicMock(), MagicMock())
    mock_train.return_value = {"_columns": [], "_split": {}}

    from scripts.check_and_retrain import main
    exit_code = main()

    assert exit_code == 0
    mock_dbt_load.assert_called_once()
    mock_load_cases.assert_not_called()
    mock_train.assert_called_once()


def test_fails_fast_with_clear_message_when_db_secrets_are_empty(monkeypatch, capsys):
    # Reproduces the scheduled-workflow failure mode: unset Actions secrets arrive as empty strings.
    for k in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        monkeypatch.setenv(k, "")
    from scripts.check_and_retrain import main
    with patch("scripts.check_and_retrain.load_dotenv"), patch("scripts.check_and_retrain.get_current_case_count") as count:
        code = main()
    assert code == 2
    count.assert_not_called()
    err = capsys.readouterr().err
    assert "DB_HOST" in err and "Secrets and variables" in err


def test_unreachable_database_skips_with_a_warning_instead_of_failing(monkeypatch, capsys):
    from sqlalchemy.exc import OperationalError
    from scripts import check_and_retrain as job

    for k in job.REQUIRED_DB_ENV:
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(job, "load_dotenv", lambda: None)

    def boom():
        raise OperationalError("select 1", {}, Exception("quota exceeded"))
    monkeypatch.setattr(job, "get_current_case_count", boom)
    assert job.main() == 0
    assert "::warning" in capsys.readouterr().out
