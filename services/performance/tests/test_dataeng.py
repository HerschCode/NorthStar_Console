import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

import numpy as np
import pandas as pd
import pytest

from src.dataeng import lakehouse
from src.dataeng.stream import Consumer, EventLog, replay


def events(n_cases=60, seed=0):
    r = np.random.default_rng(seed)
    rows = []
    for c in range(n_cases):
        t = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=int(r.integers(0, 90)))
        for a in ["Create PO", "Approve", "Goods Receipt", "Invoice"][: int(r.integers(2, 5))]:
            t += pd.Timedelta(hours=float(r.integers(1, 72)))
            rows.append({"case_id": f"C{c}", "activity": a, "timestamp": t, "resource": f"u{c % 5}"})
    return pd.DataFrame(rows)


def test_lakehouse_partitions_idempotent_and_matches_pandas(tmp_path):
    df = events()
    files = lakehouse.export_events(df, tmp_path)
    assert len(files) >= 3
    first = lakehouse.storage_report(tmp_path)
    lakehouse.export_events(df, tmp_path)                       # re-export: same bytes, no duplicates
    assert lakehouse.storage_report(tmp_path)["files"] == first["files"]
    con = lakehouse.connect(tmp_path)
    assert con.execute("select count(*) from events").fetchone()[0] == len(df)
    got = lakehouse.case_durations(con).set_index("case_id")
    exp = df.groupby("case_id")["timestamp"].agg(["min", "max", "count"])
    assert (got["event_count"] == exp["count"]).all()
    assert np.allclose(got["elapsed_hours"], (exp["max"] - exp["min"]).dt.total_seconds() / 3600)
    pruned = con.execute("select count(*) from events where month = '2024-02'").fetchone()[0]
    assert 0 < pruned < len(df)


def test_consumer_is_idempotent_under_duplicates_late_events_and_crash(tmp_path):
    df = events()
    log = EventLog(tmp_path / "log.jsonl")
    published = replay(df, log, duplicate_rate=0.2, late_rate=0.1, seed=3)
    assert published > len(df)                                  # duplicates were really sent
    c = Consumer(log, tmp_path / "db.duckdb", tmp_path / "offset")
    with pytest.raises(RuntimeError):
        c.poll(batch_size=40, crash_before_commit=True)         # crash after the write, before the offset commit
    assert c.offset == 0
    c2 = Consumer(log, tmp_path / "db.duckdb", tmp_path / "offset")  # restart: re-reads the same batch
    c2.drain(batch_size=40)
    assert c2.offset == published
    assert c2.con.execute("select count(*) from events").fetchone()[0] == len(df)  # exactly-once effect
    exp = df.groupby("case_id")["timestamp"].agg(["min", "max", "count"])
    got = c2.con.execute("select * from case_metrics order by case_id").df().set_index("case_id")
    assert len(got) == len(exp) and (got["event_count"] == exp["count"]).all()


def test_incremental_recompute_touches_only_new_cases(tmp_path):
    df = events(n_cases=30)
    log = EventLog(tmp_path / "l.jsonl"); c = Consumer(log, tmp_path / "d.duckdb", tmp_path / "o")
    replay(df, log); c.drain()
    extra = pd.DataFrame([{"case_id": "C0", "activity": "Late Invoice", "timestamp": pd.Timestamp("2025-01-01", tz="UTC"), "resource": "u0"}])
    replay(extra, log)
    r = c.poll()
    assert r["read"] == 1 and r["touched_cases"] == 1 and r["inserted"] == 1
