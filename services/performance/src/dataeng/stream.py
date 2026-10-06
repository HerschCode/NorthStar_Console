"""Event streaming with at-least-once delivery and an idempotent consumer, so the effect is exactly-once.

`EventLog` is an append-only, offset-addressed log (a file; the same Transport interface fits a Kafka/Redpanda topic or Pub/Sub, which are
not exercised here). `replay` publishes a historical log in time order, optionally with duplicates and late events to mimic a real feed.
`Consumer` reads from its committed offset, upserts into DuckDB keyed by event identity and commits the offset only after the write, so a crash
between the two re-delivers events and the upsert absorbs them. Case metrics are then recomputed only for the cases a batch touched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Iterator

import duckdb
import pandas as pd


def event_key(ev: dict) -> str:
    """Stable identity of an event: the same business event always hashes the same, whatever the delivery attempt."""
    raw = f'{ev["case_id"]}|{ev["activity"]}|{ev["timestamp"]}|{ev.get("resource", "")}'
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, events: Iterable[dict]) -> int:
        n = 0
        with open(self.path, "a", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, default=str, sort_keys=True) + "\n"); n += 1
        return n

    def read_from(self, offset: int, limit: int | None = None) -> Iterator[tuple[int, dict]]:
        with open(self.path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i < offset:
                    continue
                if limit is not None and i >= offset + limit:
                    return
                yield i, json.loads(line)


def replay(df: pd.DataFrame, log: EventLog, duplicate_rate: float = 0.0, late_rate: float = 0.0, seed: int = 0) -> int:
    """Publish df (columns case_id, activity, timestamp[, resource]) in timestamp order. `duplicate_rate` re-sends events (at-least-once);
    `late_rate` delays events to the end of the stream (out-of-order arrival)."""
    import random
    rnd = random.Random(seed)
    rows = df.sort_values("timestamp").to_dict("records")
    main, late = [], []
    for ev in rows:
        ev = {**ev, "timestamp": str(ev["timestamp"])}
        (late if rnd.random() < late_rate else main).append(ev)
        if rnd.random() < duplicate_rate:
            main.append(dict(ev))
    return log.append(main + late)


class Consumer:
    def __init__(self, log: EventLog, db_path: str | Path, checkpoint: str | Path):
        self.log, self.checkpoint = log, Path(checkpoint)
        self.con = duckdb.connect(str(db_path))
        self.con.execute("""CREATE TABLE IF NOT EXISTS events (
            event_key VARCHAR PRIMARY KEY, case_id VARCHAR, activity VARCHAR, timestamp TIMESTAMP, resource VARCHAR)""")
        self.con.execute("""CREATE TABLE IF NOT EXISTS case_metrics (
            case_id VARCHAR PRIMARY KEY, start_time TIMESTAMP, end_time TIMESTAMP, event_count BIGINT, elapsed_hours DOUBLE)""")

    @property
    def offset(self) -> int:
        return int(self.checkpoint.read_text()) if self.checkpoint.exists() else 0

    def poll(self, batch_size: int = 1000, crash_before_commit: bool = False) -> dict:
        start = self.offset
        batch = list(self.log.read_from(start, batch_size))
        if not batch:
            return {"read": 0, "inserted": 0, "touched_cases": 0, "offset": start}
        before = self.con.execute("SELECT count(*) FROM events").fetchone()[0]
        rows = [(event_key(ev), ev["case_id"], ev["activity"], ev["timestamp"], ev.get("resource")) for _, ev in batch]
        self.con.executemany("INSERT INTO events VALUES (?, ?, ?, CAST(? AS TIMESTAMP), ?) ON CONFLICT (event_key) DO NOTHING", rows)
        touched = sorted({r[1] for r in rows})
        self.con.execute("DELETE FROM case_metrics WHERE case_id IN (SELECT unnest(?))", [touched])
        self.con.execute("""INSERT INTO case_metrics
            SELECT case_id, min(timestamp), max(timestamp), count(*), date_diff('second', min(timestamp), max(timestamp)) / 3600.0
            FROM events WHERE case_id IN (SELECT unnest(?)) GROUP BY case_id""", [touched])
        inserted = self.con.execute("SELECT count(*) FROM events").fetchone()[0] - before
        if crash_before_commit:      # test hook: the write happened, the offset did not move
            raise RuntimeError("simulated crash before offset commit")
        self.checkpoint.write_text(str(batch[-1][0] + 1))
        return {"read": len(batch), "inserted": inserted, "touched_cases": len(touched), "offset": self.offset}

    def drain(self, batch_size: int = 1000) -> dict:
        total = {"read": 0, "inserted": 0}
        while True:
            r = self.poll(batch_size)
            if r["read"] == 0:
                return total
            total["read"] += r["read"]; total["inserted"] += r["inserted"]
