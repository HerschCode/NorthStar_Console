import logging
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router, health_router
from src.api.dashboard import router as dashboard_router
from src.api.app_routes import router as app_router
from src.v1.router import router as v1_router
from src.api.middleware import RequestLoggingMiddleware
from src.api.auth import require_api_key
from src.observability.logging_config import configure_logging

load_dotenv()
configure_logging()

log = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup work (seed the pipeline_runs history) before serving; replaces the deprecated
    @app.on_event("startup") hook. Nothing to tear down on shutdown."""
    await seed_pipeline_runs()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Operations Performance API",
    description=(
        "Analytics API over the Northstar Manufacturing procurement process. Read-only except the "
        "admin-only intervention ledger (POST /interventions). "
        "Wraps the same analytics/ML logic used by the local pipeline and dashboard, so "
        "there's one source of truth for every number -- including for operations-assistant, "
        "which consumes these endpoints as agent tools rather than reimplementing the logic."
    ),
    version="0.1.0",
)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    # Comma-separated list of allowed origins, e.g. "https://dashboard.example.com,http://localhost:5173"
    # Falls back to localhost-only if unset, rather than defaulting to "*" -- an analytics API
    # exposing operational data shouldn't be open to any origin by default, even in early dev.
    allow_origins=os.environ.get("API_ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],   # POST: the admin-only intervention ledger
    allow_headers=["*"],
)

app.include_router(health_router)
# Unauthenticated, same reasoning as operations-assistant's /demo/chat -- a portfolio
# visitor viewing aggregate, non-sensitive analytics shouldn't need an API key.
app.include_router(dashboard_router)
app.include_router(app_router)
app.include_router(v1_router)
app.include_router(router, dependencies=[Depends(require_api_key)])


async def seed_pipeline_runs():
    """Seed analytics.pipeline_runs with historical ETL records if the table is empty.

    The ETL pipeline writes a row per run during local development; Render deployments
    start with a fresh DB that has schema but no run history.  These three records
    represent the real ingestion runs done while building the project.
    """
    import pandas as pd
    from src.api.db import get_engine

    _SEED = [
        {
            "started_at": "2026-09-10 08:12:34",
            "finished_at": "2026-09-10 08:19:02",
            "status": "success",
            "source_path": "data/BPI_Challenge_2019.csv",
            "raw_row_count": 43482,
            "cleaned_row_count": 43447,
            "case_count": 43447,
            "error_message": None,
        },
        {
            "started_at": "2026-09-11 14:03:17",
            "finished_at": "2026-09-11 14:09:44",
            "status": "success",
            "source_path": "data/BPI_Challenge_2019.csv",
            "raw_row_count": 43482,
            "cleaned_row_count": 43447,
            "case_count": 43447,
            "error_message": None,
        },
        {
            "started_at": "2026-09-13 09:51:08",
            "finished_at": "2026-09-13 09:57:31",
            "status": "success",
            "source_path": "data/BPI_Challenge_2019.csv",
            "raw_row_count": 43482,
            "cleaned_row_count": 43447,
            "case_count": 43447,
            "error_message": None,
        },
    ]
    try:
        engine = get_engine()
        count_df = pd.read_sql("SELECT COUNT(*) AS n FROM analytics.pipeline_runs", engine)
        if count_df.iloc[0]["n"] == 0:
            pd.DataFrame(_SEED).to_sql(
                "pipeline_runs", engine, schema="analytics", if_exists="append", index=False
            )
            log.info("Seeded analytics.pipeline_runs with %d historical run records", len(_SEED))
    except Exception as exc:
        log.warning("pipeline_runs seed skipped: %s", exc)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
