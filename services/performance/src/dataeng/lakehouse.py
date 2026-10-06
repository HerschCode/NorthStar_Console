"""Columnar lakehouse layer: the cleaned event log as month-partitioned Parquet, queried with DuckDB (no server, no cost).

Why: the same analytical queries that run on Postgres can run on files, so an analyst or a CI job needs no database, and the layout is the one
Iceberg/Delta tables sit on. Writes are idempotent: re-exporting a month replaces that month's file, never appends duplicates.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


def export_events(df: pd.DataFrame, root: str | Path, ts_col: str = "timestamp") -> list[Path]:
    """Write `df` to <root>/events/month=YYYY-MM/part.parquet, replacing any existing file for each month it contains."""
    root = Path(root)
    d = df.copy()
    d[ts_col] = pd.to_datetime(d[ts_col], utc=True)
    d["month"] = d[ts_col].dt.strftime("%Y-%m")
    written = []
    for month, part in d.groupby("month"):
        out = root / "events" / f"month={month}"
        out.mkdir(parents=True, exist_ok=True)
        path = out / "part.parquet"
        part.drop(columns="month").to_parquet(path, index=False)   # overwrite = idempotent
        written.append(path)
    return written


def connect(root: str | Path) -> duckdb.DuckDBPyConnection:
    """DuckDB connection with `events` as a view over the partitioned files (partition pruning on `month` works)."""
    con = duckdb.connect()
    glob = str(Path(root) / "events" / "month=*" / "*.parquet")
    con.execute(f"CREATE VIEW events AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")
    return con


def case_durations(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per-case first/last event and elapsed hours, the same shape as analytics.process_cases' timing columns."""
    return con.execute("""
        SELECT case_id, min(timestamp) AS start_time, max(timestamp) AS end_time, count(*) AS event_count,
               date_diff('second', min(timestamp), max(timestamp)) / 3600.0 AS elapsed_hours
        FROM events GROUP BY case_id ORDER BY case_id""").df()


def storage_report(root: str | Path) -> dict:
    files = list(Path(root).rglob("*.parquet"))
    return {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}
