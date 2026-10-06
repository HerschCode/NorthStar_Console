"""Runs every window-function query in sql/analysis/ against the live database and prints the
first 5 rows of each -- the reproduction path docs/sql-window-functions.md points to. Read-only
(SELECT statements only); does not touch schema or data.

Files can contain more than one statement (e.g. variant_ranking.sql: top-10-by-volume and
top-10-by-breach-rate are two separate SELECTs, since a window function's own result can't be
filtered in the WHERE/HAVING of the same SELECT it's computed in -- Postgres has no QUALIFY
clause). split_sql_statements is the same comment-aware splitter scripts/setup_database.py
uses, reused rather than re-implemented so a fix in one place fixes both.
"""
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

from scripts.setup_database import get_engine, split_sql_statements

FILES = [
    "waiting_time_lag.sql",
    "variant_ranking.sql",
    "rolling_sla_breach_rate.sql",
    "monthly_cohort_breach_trend.sql",
    "supplier_quartiles.sql",
    "slowest_stage_per_case.sql",
    "running_event_count.sql",
    "category_relative_cycle_time.sql",
    "rework_detection.sql",
    "bottlenecks.sql",
]


def main():
    load_dotenv()
    engine = get_engine()
    sql_dir = Path(__file__).resolve().parent.parent / "sql" / "analysis"

    with engine.connect() as conn:
        for name in FILES:
            sql = (sql_dir / name).read_text()
            statements = split_sql_statements(sql)
            for i, stmt in enumerate(statements):
                rows = conn.execute(text(stmt)).fetchall()
                label = name if len(statements) == 1 else f"{name} [statement {i + 1}/{len(statements)}]"
                print(f"\n=== {label} ({len(rows)} rows) ===")
                for r in rows[:5]:
                    print("  ", tuple(r))


if __name__ == "__main__":
    main()
