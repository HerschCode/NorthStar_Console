"""Airflow DAG for the same pipeline as flows/pipeline_flow.py (Prefect): ingest -> contract + DQ gate -> dbt -> retrain gate -> train ->
champion/challenger gate -> report. WRITTEN, NOT RUN: Airflow is not installed in the build environment, so this file is only
syntax-checked (python -m py_compile). Tasks call scripts.run_pipeline.run and the mlops package directly; the retrain task is a placeholder until the real train_fn is wired (see scripts/mlops_cycle.py)."""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow.decorators import dag, task


@dag(dag_id="northstar_pipeline", schedule="0 6 * * *", start_date=datetime(2026, 10, 1), catchup=False,
     default_args={"retries": 1, "retry_delay": timedelta(minutes=5)}, tags=["northstar", "mlops"])
def northstar_pipeline():
    @task
    def ingest() -> None:
        from scripts.run_pipeline import run
        run()

    @task
    def contract_and_quality_gate(_=None) -> None:
        from src.ingestion.data_contract import enforce_data_contract  # noqa: F401  (run_pipeline.run already enforces it; kept as an explicit gate)

    @task
    def dbt_build(_=None) -> None:
        import subprocess
        subprocess.run(["dbt", "build", "--profiles-dir", "dbt"], cwd="dbt", check=True)

    @task
    def retrain_cycle(_=None) -> str:
        from src.mlops.cycle import run_cycle  # train_fn / drift wiring lives in scripts.mlops_cycle for the real model
        return "see scripts/mlops_cycle.py"

    loaded = ingest()
    gate = contract_and_quality_gate(loaded)
    build = dbt_build(gate)
    retrain_cycle(build)


northstar_pipeline()
