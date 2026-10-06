"""Runs every file in sql/analysis/ against a real, seeded, isolated Postgres database -- the
"seeded test DB" Phase 1's acceptance criteria calls for.

No local Postgres or Docker is available in this environment (checked: no psql/pg_ctl on PATH,
Docker Desktop's daemon not running), so this seeds a throwaway DATABASE on the same Neon
project the app already uses (confirmed the app's own DB_USER has CREATE DATABASE privilege),
rather than skip the requirement or fake it against a SQLite/mock substitute that couldn't run
this project's actual Postgres-dialect SQL (RANGE window frames, PERCENTILE_CONT, FILTER,
correlated subqueries -- none of which SQLite supports).

A separate DATABASE, not just a separate schema, is required here: every file in sql/analysis/
hardcodes "staging.events" / "analytics.process_cases" / "analytics.sla_rules" -- and those
schema names already exist in the real, live application database on this same server. Creating
a second database (same server, real "staging"/"analytics" schema names inside it, matching
production exactly) is what avoids colliding with production data while still running the SQL
files completely unmodified. The database is created fresh and dropped in a `finally` block
whether the test session passes or fails.

Opt-in, not run by default: needs real DB_* credentials this repo's CI does not have configured,
and CREATE DATABASE privilege beyond what a locked-down read-only role would have. Run
explicitly:

    RUN_LIVE_SQL_TESTS=1 python -m pytest tests/test_sql_analysis_live.py -v
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from scripts.setup_database import split_sql_statements

load_dotenv()

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SQL_TESTS") != "1",
    reason="opt-in live-database test -- set RUN_LIVE_SQL_TESTS=1 and real DB_* creds (with "
           "CREATE DATABASE privilege) to run",
)

TEST_DB_NAME = "sql_analysis_test"
SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "analysis"
T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _admin_url(dbname: str) -> str:
    sslmode = os.environ.get("DB_SSLMODE", "prefer")
    return (
        f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{dbname}?sslmode={sslmode}"
    )


def _terminate_and_drop(admin_conn) -> None:
    # A previous run's own connection to this database can still be alive on Neon's pooler for
    # a moment after the client disconnects -- found live: a fresh run's own SETUP-time cleanup
    # DROP hit "is being accessed by other users" left over from the PRIOR run, not just at
    # teardown as first assumed. Terminate any other backend before every DROP, not only the
    # one in the finally block.
    admin_conn.execute(text(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = :db AND pid <> pg_backend_pid()"
    ), {"db": TEST_DB_NAME})
    admin_conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}"))


@pytest.fixture(scope="module")
def seeded_engine():
    admin = create_engine(_admin_url(os.environ["DB_NAME"]), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        _terminate_and_drop(c)
        c.execute(text(f"CREATE DATABASE {TEST_DB_NAME}"))
    admin.dispose()

    engine = create_engine(_admin_url(TEST_DB_NAME))
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA staging"))
        conn.execute(text("CREATE SCHEMA analytics"))
        conn.execute(text("""
            CREATE TABLE staging.events (
                case_id TEXT, activity TEXT, timestamp TIMESTAMPTZ,
                resource TEXT, purchase_order_id TEXT, item_id TEXT,
                category TEXT, supplier_id TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE analytics.process_cases (
                case_id TEXT, first_activity TEXT, last_activity TEXT, event_count BIGINT,
                start_time TIMESTAMPTZ, end_time TIMESTAMPTZ, cycle_time_hours DOUBLE PRECISION,
                supplier_id TEXT, category TEXT, variant TEXT, variant_frequency BIGINT
            )
        """))
        conn.execute(text("CREATE TABLE analytics.sla_rules (category TEXT, target_hours NUMERIC)"))

        # Two cases, hand-designed so the answers are known:
        #  - C1: "Create" -> "Approve" -> "Approve" (repeated -> rework), 48h cycle time
        #  - C2: "Create" -> "Pay" (no repeat -> no rework), 10h cycle time
        # Category "Widgets" targets 24h, so C1 breaches and C2 doesn't.
        events = [
            ("C1", "Create", T0, "Widgets"),
            ("C1", "Approve", T0 + timedelta(hours=10), "Widgets"),
            ("C1", "Approve", T0 + timedelta(hours=48), "Widgets"),
            ("C2", "Create", T0 + timedelta(days=5), "Widgets"),
            ("C2", "Pay", T0 + timedelta(days=5, hours=10), "Widgets"),
        ]
        for case_id, activity, ts, category in events:
            conn.execute(
                text("INSERT INTO staging.events (case_id, activity, timestamp, category) "
                     "VALUES (:c, :a, :t, :cat)"),
                {"c": case_id, "a": activity, "t": ts, "cat": category},
            )
        cases = [
            ("C1", "Create", "Approve", 3, T0, T0 + timedelta(hours=48), 48.0, "S1", "Widgets", "Create->Approve->Approve", 1),
            ("C2", "Create", "Pay", 2, T0 + timedelta(days=5), T0 + timedelta(days=5, hours=10), 10.0, "S1", "Widgets", "Create->Pay", 1),
        ]
        cols = ["case_id", "first_activity", "last_activity", "event_count", "start_time",
                "end_time", "cycle_time_hours", "supplier_id", "category", "variant", "variant_frequency"]
        for row in cases:
            conn.execute(text(
                f"INSERT INTO analytics.process_cases ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})"
            ), dict(zip(cols, row)))
        conn.execute(text("INSERT INTO analytics.sla_rules (category, target_hours) VALUES ('Widgets', 24)"))

    try:
        yield engine
    finally:
        # engine.dispose() only closes connections sitting idle in SQLAlchemy's own pool --
        # Postgres/Neon can take a moment to fully release a backend after the client-side
        # socket closes, and DROP DATABASE fails with "is being accessed by other users" if it
        # races that (found live -- see _terminate_and_drop's docstring for the setup-time
        # version of the same race).
        engine.dispose()
        admin = create_engine(_admin_url(os.environ["DB_NAME"]), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            _terminate_and_drop(c)
        admin.dispose()


ALL_ANALYSIS_FILES = sorted(p.name for p in SQL_DIR.glob("*.sql"))


@pytest.mark.parametrize("filename", ALL_ANALYSIS_FILES)
def test_query_runs_against_seeded_database(seeded_engine, filename):
    """Every file in sql/analysis/ must execute without error against real Postgres, seeded
    with real (if tiny) data -- catches exactly the class of bug found manually while writing
    these queries (COUNT(DISTINCT...) OVER, ROUND(double,int), QUALIFY not existing in
    Postgres) automatically on every future change, instead of relying on a human re-running
    scripts/run_sql_analysis.py by hand."""
    sql = (SQL_DIR / filename).read_text()
    statements = split_sql_statements(sql)
    with seeded_engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def test_rework_detection_correctly_flags_the_seeded_repeated_activity(seeded_engine):
    """Value-correctness check, not just 'it ran': C1 has Approve twice (rework), C2 doesn't --
    rework_detection.sql must report exactly 1 case with rework and 1 without, with C1's known
    48h cycle time attributed to the has_rework=true row."""
    sql = (SQL_DIR / "rework_detection.sql").read_text()
    with seeded_engine.connect() as conn:
        rows = {r[0]: r for r in conn.execute(text(sql)).fetchall()}
    assert rows[True].case_count == 1
    assert rows[True].avg_cycle_hours == 48.0
    assert rows[False].case_count == 1
    assert rows[False].avg_cycle_hours == 10.0


def test_waiting_time_lag_excludes_first_events_and_respects_min_count(seeded_engine):
    """waiting_time_lag.sql has its own HAVING COUNT(*) >= 5 floor (same min-sample-size
    principle as bottlenecks.sql), so this seeds 5 extra "Approve" events specifically to
    clear it -- otherwise every activity in the tiny base fixture (1-2 occurrences each) would
    be filtered out and an assertion of "no rows" would pass without testing anything real.
    "Create" must never appear (LAG is NULL for a case's first event, excluded by the query's
    own WHERE wait_hours IS NOT NULL); "Approve" must appear now that it clears the floor."""
    with seeded_engine.begin() as conn:
        for i in range(5):
            case_id = f"C_extra_{i}"
            conn.execute(text(
                "INSERT INTO staging.events (case_id, activity, timestamp, category) VALUES "
                "(:c, 'Create', :t0, 'Widgets'), (:c, 'Approve', :t1, 'Widgets')"
            ), {"c": case_id, "t0": T0 + timedelta(days=10 + i), "t1": T0 + timedelta(days=10 + i, hours=6)})

    sql = (SQL_DIR / "waiting_time_lag.sql").read_text()
    with seeded_engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    activities = {r.activity for r in rows}
    assert "Create" not in activities
    assert "Approve" in activities
