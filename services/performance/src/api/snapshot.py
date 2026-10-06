"""Committed dashboard snapshot, served only when the database is unreachable.

The hosted dashboard reads a free-tier Postgres (Neon) that can be suspended or over quota; without this the
page is empty exactly when someone is looking at it. The snapshot is built from the same section functions as
the live page (scripts/build_dashboard_snapshot.py) and is always labelled as a snapshot with its build date.
"""
import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "reports" / "dashboard_snapshot.json"


def load_snapshot(path: Path = SNAPSHOT_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = data.get("snapshot") or {}
    meta["live"] = False
    data["snapshot"] = meta
    return data
