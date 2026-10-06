import os
import time
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.ingestion.load_event_log import load_event_log
from src.ingestion.data_contract import enforce_data_contract
from src.ingestion.validate_input import validate_event_log
from src.cleaning.clean_events import clean_events
from src.transformation.build_process_cases import build_process_cases
from src.observability.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger("pipeline")


def get_engine():
    sslmode = os.environ.get("DB_SSLMODE", "prefer")
    url = (
        f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
        f"?sslmode={sslmode}"
    )
    return create_engine(url)


def _start_run(engine, source_path: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO analytics.pipeline_runs (started_at, status, source_path)
                VALUES (:started_at, 'running', :source_path)
                RETURNING run_id
                """
            ),
            {"started_at": datetime.utcnow(), "source_path": source_path},
        )
        return result.scalar_one()


def _finish_run(engine, run_id: int, **fields):
    fields["finished_at"] = datetime.utcnow()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE analytics.pipeline_runs SET {set_clause} WHERE run_id = :run_id"),
            {**fields, "run_id": run_id},
        )


def run():
    load_dotenv()
    engine = get_engine()
    source_path = os.environ["RAW_EVENT_LOG_PATH"]
    run_id = _start_run(engine, source_path)
    pipeline_start = time.monotonic()

    def step(msg, **extra):
        logger.info(msg, extra={"run_id": run_id, **extra})

    try:
        step("Loading raw event log", stage="load", source_path=source_path)
        t0 = time.monotonic()
        raw = load_event_log(source_path)
        step("Raw event log loaded", stage="load", duration_seconds=round(time.monotonic() - t0, 2), row_count=len(raw))

        step("Enforcing data contract", stage="contract")
        enforce_data_contract(raw)

        step("Validating content", stage="validate")
        result = validate_event_log(raw)
        if not result.is_valid:
            raise RuntimeError(f"Validation failed: {result.errors}")
        for w in result.warnings:
            logger.warning(w, extra={"run_id": run_id, "stage": "validate"})

        step("Cleaning", stage="clean")
        t0 = time.monotonic()
        cleaned, summary = clean_events(raw)
        step(
            "Cleaning complete", stage="clean",
            duration_seconds=round(time.monotonic() - t0, 2),
            dropped_missing=summary["dropped_missing_required_fields"],
            dropped_duplicates=summary["dropped_exact_duplicates"],
            rows_after=summary["rows_after_cleaning"],
        )

        step("Building process cases and loading to Postgres", stage="load_db")
        t0 = time.monotonic()
        cases = build_process_cases(cleaned)

        with engine.begin() as conn:
            cleaned.to_sql("events", conn, schema="staging", if_exists="replace", index=False)
            # pandas' if_exists="replace" does DROP TABLE then CREATE -- which fails on a
            # real database once sql/schema/001_create_tables.sql's FK from
            # analytics.sla_predictions to process_cases actually exists (only surfaced
            # once this ran against a real Postgres with the full schema applied, not
            # against the mocked DB layer every test uses). CASCADE explicitly here so a
            # full pipeline reload also clears out now-stale derived predictions, which
            # src/ml/predict.py is expected to regenerate against the new case set anyway
            # -- consistent with the existing "replace per run" idempotency model, just
            # extended to the table that depends on the one being replaced.
            conn.execute(text("DROP TABLE IF EXISTS analytics.process_cases CASCADE"))
            cases.to_sql("process_cases", conn, schema="analytics", if_exists="replace", index=False)
        step("Load to Postgres complete", stage="load_db", duration_seconds=round(time.monotonic() - t0, 2))

        total_duration = round(time.monotonic() - pipeline_start, 2)
        _finish_run(
            engine, run_id, status="success",
            raw_row_count=len(raw), cleaned_row_count=len(cleaned), case_count=len(cases),
        )
        step(
            "Pipeline run complete", stage="done", status="success",
            total_duration_seconds=total_duration, event_count=len(cleaned), case_count=len(cases),
        )

    except Exception as exc:
        _finish_run(engine, run_id, status="failed", error_message=str(exc))
        logger.error(
            "Pipeline run failed", extra={
                "run_id": run_id, "stage": "failed",
                "total_duration_seconds": round(time.monotonic() - pipeline_start, 2),
            }, exc_info=True,
        )
        raise


if __name__ == "__main__":
    run()
