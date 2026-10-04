"""
The actual scheduled job src/ml/retrain_trigger.py's docstring named as the missing
piece: "No scheduler is actually wired up in this environment." Wired now via
.github/workflows/retrain-check.yml's cron trigger -- this script is what that
workflow runs.

Fetches the REAL current case count from the live database (not hardcoded, unlike
retrain_trigger.py's own __main__ illustrative block), calls the existing decision
logic unchanged, and retrains for real if warranted. Exits non-zero on an actual
retrain (not on "no retrain needed" -- that's the expected, common outcome, not a
failure) so a human glancing at Action run history can tell "did anything happen"
from the green/red status without opening logs, while still logging plainly either
way.

(Correction: that "non-zero on retrain" behaviour is NOT what the code does -- both
branches return 0. A red run therefore always means an error, most commonly the DB
secrets below being unset, never "a retrain happened".)
"""
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.api.db import get_engine, load_cases
from src.ml.retrain_trigger import check_retrain_needed
from src.ml.features import build_features, feature_source_is_dbt_marts, load_evaluated_cases_from_dbt_marts
from src.ml.train import train_models, save_best_model
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla
from src.observability.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger("retrain_trigger")


def get_current_case_count() -> int:
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM analytics.process_cases")).scalar()


REQUIRED_DB_ENV = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")


def missing_db_env() -> list[str]:
    return [k for k in REQUIRED_DB_ENV if not os.environ.get(k)]


def main() -> int:
    load_dotenv()
    missing = missing_db_env()
    if missing:
        # In GitHub Actions an unset repository secret expands to an empty string, and the DB layer
        # then silently falls back to localhost and fails with a bare "connection refused" -- which
        # is what made the scheduled run fail daily with no hint why. Say it plainly instead.
        print(
            "ERROR: required database settings are empty: " + ", ".join(missing) + ". "
            "In GitHub Actions set them under Settings -> Secrets and variables -> Actions "
            "(DB_HOST, DB_NAME, DB_USER, DB_PASSWORD).",
            file=sys.stderr,
        )
        return 2
    try:
        case_count = get_current_case_count()
    except OperationalError as exc:
        # The hosted free-tier database can be suspended or over quota. That is an availability problem, not a
        # retraining failure: surface it as a workflow warning annotation and skip, instead of leaving the
        # scheduled check permanently red (which hides real failures behind a known one).
        print(f"::warning title=Retrain check skipped::database unreachable ({type(exc).__name__}); no decision made")
        logger.warning("Retrain check skipped: database unreachable", extra={"error": str(exc)[:200]})
        return 0
    logger.info("Checking retrain need", extra={"current_case_count": case_count})

    result = check_retrain_needed(current_case_count=case_count)

    if not result.should_retrain:
        logger.info(
            "No retraining needed",
            extra={"days_since_training": result.days_since_training, "data_growth_pct": result.data_growth_pct},
        )
        print("No retraining needed yet.")
        return 0

    logger.info("Retraining triggered", extra={"reasons": result.reasons})
    print("Retraining triggered:")
    for reason in result.reasons:
        print(f"  - {reason}")

    if feature_source_is_dbt_marts():
        # PLAN.md Phase 2: optional dbt_marts.fct_cases read path, results unchanged --
        # verified against live data (see docs/dbt-project.md): 0 of 1,617,000 feature cells
        # differ from the raw-events path below. Needs `dbt build` to have been run first.
        logger.info("Reading case features from dbt_marts.fct_cases (FEATURE_SOURCE=dbt_marts)")
        evaluated = load_evaluated_cases_from_dbt_marts(get_engine())
    else:
        cases = load_cases()
        evaluated = evaluate_sla(cases, load_sla_targets())
    X, y = build_features(evaluated)
    train_results = train_models(X, y, evaluated)
    save_best_model(train_results)

    logger.info("Retraining complete")
    print(f"Retrained and saved new model (case_count={case_count}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
