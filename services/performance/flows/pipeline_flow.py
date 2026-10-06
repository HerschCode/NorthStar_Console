"""PLAN.md Phases 3-4: ingest -> validate -> dbt build -> retrain gate (drift/age/volume) -> train -> evaluate -> register (only if
it beats the current deployed model by a configured margin) -> report.

Run locally with one command:
    python -m flows.pipeline_flow

In production this would run on Prefect's own cron deployment schedule -- daily at 07:00 UTC,
one hour after operations-performance's existing GitHub Actions retrain-check.yml cron (06:00
UTC), so a full re-ingest + dbt rebuild + retrain-if-warranted always runs after (not racing)
the simpler daily check that workflow already does. Not deployed as an actual Prefect Cloud/
server schedule here -- that needs a persistent Prefect server or Prefect Cloud account, which
is out of scope for a project that otherwise runs on free-tier, no-server infrastructure
(Render, Neon, GitHub Actions). `prefect deployment build` + a cron schedule string is the real
command that would wire this up against a real Prefect server.
"""
import subprocess
import sys
from pathlib import Path

from prefect import flow, task, get_run_logger
from prefect.exceptions import MissingContextError

REPO_ROOT = Path(__file__).resolve().parent.parent


def _logger():
    """get_run_logger() requires an active Prefect flow/task run context -- calling a task's
    .fn directly (the standard way to unit-test Prefect task logic without spinning up the
    Prefect runtime, used throughout tests/test_pipeline_flow.py) has none, and raises
    MissingContextError. Falls back to a plain stdlib logger outside a real run rather than
    making every task function conditionally skip logging in tests."""
    try:
        return get_run_logger()
    except MissingContextError:
        import logging
        return logging.getLogger("pipeline_flow")


class DataQualityGateFailed(Exception):
    """Raised by validate_task when a DQ threshold is exceeded -- stops the flow before
    dbt_build_task/train_task ever run, since Prefect skips downstream tasks whose upstream
    dependency raised."""


@task(retries=1, retry_delay_seconds=10)
def ingest_task() -> int:
    """Real ingest: scripts/run_pipeline.py's existing, already-tested pipeline (download/load
    -> data-contract check -> clean -> write to staging.events/analytics.process_cases). Not
    re-implemented here -- one source of truth for what "ingest" means in this project.

    Real integration conflict, found by running the flow end to end: run_pipeline.py's
    cleaned.to_sql(..., if_exists="replace") does DROP TABLE staging.events, which fails once
    Phase 2's dbt views exist -- "cannot drop table staging.events because other objects
    depend on it" (dbt's stg_events view, and everything built on top of it). The correct fix
    is sequencing, not avoiding dbt: dbt_build_task runs immediately after this task in the
    same flow, so it's safe to drop the dbt-managed schemas here and let dbt fully recreate
    them right after, rather than trying to make the raw-table replace preserve views it
    doesn't know exist."""
    from sqlalchemy import text
    from src.api.db import get_engine

    with get_engine().begin() as conn:
        for schema in ("dbt_staging", "dbt_intermediate", "dbt_marts"):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))

    logger = _logger()
    from scripts.run_pipeline import run as run_ingest_pipeline

    run_ingest_pipeline()
    from src.api.db import get_engine
    from sqlalchemy import text
    with get_engine().connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM staging.events")).scalar()
    logger.info(f"Ingest complete: {count} events in staging.events")
    return count


@task
def validate_task(_ingest_result: int) -> None:
    """Real DQ check (src/cleaning/data_quality.py, the same module scripts/run_pipeline.py's
    own data-quality reporting uses) against the events staging table THE INGEST STEP JUST
    WROTE -- a distinct step from ingest's own internal contract validation, with its own
    configured thresholds (config/orchestration.yaml) and its own failure mode: raises
    DataQualityGateFailed, which stops dbt_build_task/train_task from ever running (Prefect
    skips downstream tasks when their upstream dependency task raises)."""
    logger = _logger()
    import pandas as pd
    import yaml
    from src.api.db import get_engine
    from src.cleaning.data_quality import run_data_quality_checks

    df = pd.read_sql("SELECT * FROM staging.events", get_engine())
    report = run_data_quality_checks(df)
    logger.info(f"Data quality report: {report.as_dict()}")

    cfg = yaml.safe_load((REPO_ROOT / "config" / "orchestration.yaml").read_text())["data_quality"]
    missing_resource_pct = 100.0 * report.missing_resource / max(report.total_rows, 1)

    failures = []
    if report.duplicate_rows > cfg["max_duplicate_rows"]:
        failures.append(f"duplicate_rows {report.duplicate_rows} > {cfg['max_duplicate_rows']}")
    if report.out_of_order_events > cfg["max_out_of_order_events"]:
        failures.append(f"out_of_order_events {report.out_of_order_events} > {cfg['max_out_of_order_events']}")
    if missing_resource_pct > cfg["max_missing_resource_pct"]:
        failures.append(f"missing_resource_pct {missing_resource_pct:.2f} > {cfg['max_missing_resource_pct']}")

    if failures:
        raise DataQualityGateFailed("Data quality gate failed: " + "; ".join(failures))
    logger.info("Data quality gate passed")


@task
def dbt_build_task(_validate_result: None) -> str:
    """Runs the real `dbt build` (Phase 2) as a subprocess -- same command
    docs/dbt-project.md documents for manual reproduction, run here as part of the flow instead
    of by hand. Needs the same DB_* env vars every other script in this project uses, already
    present in the flow's own process environment (not re-exported here)."""
    logger = _logger()
    dbt_cmd = "from dbt.cli.main import cli; cli(['build', '--profiles-dir', '.', '--project-dir', '.'])"
    result = subprocess.run(
        [sys.executable, "-c", dbt_cmd],
        cwd=REPO_ROOT / "dbt", capture_output=True, text=True,
    )
    logger.info(result.stdout[-3000:])
    if result.returncode != 0:
        logger.warning(result.stderr[-2000:])
        raise RuntimeError(f"dbt build failed with exit code {result.returncode}")
    return "dbt build passed"


@task
def retrain_gate_task(_dbt_result: str, force: bool = False) -> dict:
    """PLAN.md Phase 4: only retrain when src/ml/retrain_trigger.py says so -- time since
    training, case-volume growth, or feature drift (per-input PSI vs the deployed model's
    training baselines, src/ml/feature_drift.py). `force` bypasses the check. Runs AFTER ingest
    + dbt so drift is measured on the freshly loaded data."""
    logger = _logger()
    from src.api.db import load_cases
    from src.ml.feature_drift import current_feature_drift
    from src.ml.retrain_trigger import check_retrain_needed

    try:
        drift = current_feature_drift()
    except Exception as exc:  # drift is a signal, never a reason to break the flow
        logger.warning(f"feature drift unavailable: {exc}")
        drift = None
    rec = check_retrain_needed(current_case_count=len(load_cases()), feature_drift=drift)
    retrain = force or rec.should_retrain
    reasons = (["forced by caller"] if force else []) + rec.reasons
    logger.info(f"Retrain gate: {'RETRAIN' if retrain else 'SKIP'} -- {reasons or 'no trigger fired'}")
    return {"retrain": retrain, "reasons": reasons,
            "feature_drift_status": drift.status if drift else None}


@task
def train_task(_dbt_result: str) -> dict:
    """Trains all 3 candidate models on the current data -- src/ml/train.py's existing
    train_models(), not re-implemented. Does NOT call save_best_model() here: that's
    evaluate_and_register_task's decision, gated on beating the deployed model."""
    logger = _logger()
    from src.api.db import get_engine, load_cases
    from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
    from src.ml.features import build_features, feature_source_is_dbt_marts, load_evaluated_cases_from_dbt_marts
    from src.ml.train import train_models

    if feature_source_is_dbt_marts():
        evaluated = load_evaluated_cases_from_dbt_marts(get_engine())
    else:
        evaluated = evaluate_sla(load_cases(), load_sla_targets())

    X, y = build_features(evaluated)
    results = train_models(X, y, evaluated)
    best_name = max((k for k in results if not k.startswith("_")), key=lambda k: results[k]["roc_auc"])
    logger.info(f"Trained candidates: {[(k, round(results[k]['roc_auc'], 4)) for k in results if not k.startswith('_')]}")
    logger.info(f"Best candidate: {best_name} (ROC-AUC {results[best_name]['roc_auc']:.4f})")
    return results


@task
def evaluate_and_register_task(train_results: dict) -> dict:
    """The actual gate PLAN.md Phase 3 asks for: register the candidate only if it beats the
    CURRENTLY DEPLOYED model (models/sla_risk_model.meta.json's roc_auc -- there is no real
    MLflow "Production" stage in active use in this project; the deployed .joblib file is what
    actually serves predictions, so that's the honest comparison target) by at least
    config/orchestration.yaml's min_roc_auc_improvement, on the SAME held-out temporal split
    both were measured on."""
    logger = _logger()
    import json
    import yaml
    from src.ml.train import save_best_model

    served = lambda r: r.get("calibrated_eval", r)["roc_auc"]   # compare the model that would be SERVED
    best_name = max((k for k in train_results if not k.startswith("_")), key=lambda k: served(train_results[k]))
    candidate_roc_auc = served(train_results[best_name])

    meta_path = REPO_ROOT / "models" / "sla_risk_model.meta.json"
    deployed_roc_auc = None
    if meta_path.exists():
        deployed_roc_auc = json.loads(meta_path.read_text()).get("roc_auc")

    margin = yaml.safe_load((REPO_ROOT / "config" / "orchestration.yaml").read_text())["promotion"]["min_roc_auc_improvement"]

    if deployed_roc_auc is None:
        decision, reason = True, "no deployed model exists yet"
    elif candidate_roc_auc - deployed_roc_auc >= margin:
        decision, reason = True, f"candidate {candidate_roc_auc:.4f} beats deployed {deployed_roc_auc:.4f} by >= {margin}"
    else:
        decision, reason = False, f"candidate {candidate_roc_auc:.4f} does not beat deployed {deployed_roc_auc:.4f} by >= {margin}"

    logger.info(f"Promotion decision: {'PROMOTE' if decision else 'KEEP DEPLOYED'} -- {reason}")
    if decision:
        save_best_model(train_results)
        logger.info(f"Registered {best_name} as the deployed model")

    return {
        "promoted": decision, "reason": reason, "best_candidate": best_name,
        "candidate_roc_auc": candidate_roc_auc, "deployed_roc_auc_before": deployed_roc_auc,
    }


@task
def report_task(decision: dict) -> None:
    """Writes a short, real summary of this run -- what was trained, what was decided, why --
    to reports/ (gitignored, same pattern as models/*.joblib being the one deliberate
    exception rather than every generated artifact living in git)."""
    import json
    from datetime import datetime, timezone

    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    report = {"ran_at": datetime.now(timezone.utc).isoformat(), **decision}
    out_path = reports_dir / "pipeline_flow_last_run.json"
    out_path.write_text(json.dumps(report, indent=2))
    _logger().info(f"Report written to {out_path}")


@flow(name="operations-performance-pipeline")
def pipeline_flow(force_retrain: bool = False):
    ingest_result = ingest_task()
    validate_result = validate_task(ingest_result)
    dbt_result = dbt_build_task(validate_result)
    gate = retrain_gate_task(dbt_result, force_retrain)
    if not gate["retrain"]:
        decision = {"promoted": False, "retrained": False, "reason": "retrain gate: no trigger fired", "gate": gate}
        report_task(decision)
        return decision
    train_results = train_task(dbt_result)
    decision = {**evaluate_and_register_task(train_results), "retrained": True, "gate": gate}
    report_task(decision)
    return decision


if __name__ == "__main__":
    pipeline_flow()
