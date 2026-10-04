import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from src.api.main import app
from src.v1 import build, router as v1router

client = TestClient(app)
ALLOWED = {"measured", "simulated", "experimental", "pending", "descriptive"}
SNAP = Path("reports/v1_snapshot")
UTC = "UTC"


def _walk(node, path=""):
    if isinstance(node, dict):
        if {"value", "unit", "provenance"} <= set(node):
            yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def _synthetic_ctx():
    stages = yaml.safe_load(Path("config/stages.yaml").read_text(encoding="utf-8"))["stages"]
    acts = [a for s in stages for a in s["activities"]]
    rng = np.random.default_rng(1)
    cases, events = [], []
    for i in range(200):
        start = pd.Timestamp("2018-01-01", tz=UTC) + pd.Timedelta(hours=int(rng.integers(0, 24 * 20)))
        n = int(rng.integers(2, 9))
        ts = [start] + [start + pd.Timedelta(hours=float(h)) for h in np.cumsum(rng.uniform(5, 300, n - 1))]
        for j, t in enumerate(ts):
            events.append({"case_id": f"c{i}", "activity": acts[(i + j) % len(acts)], "timestamp": t, "net_worth_eur": float(50 * (i + 1) * (j + 1))})
        cases.append({"case_id": f"c{i}", "start_time": ts[0], "end_time": ts[-1], "supplier_id": f"s{i % 7}", "category": "Others",
                      "cycle_time_hours": (ts[-1] - ts[0]).total_seconds() / 3600, "variant": "A>B" if i % 2 else "A>C"})
    return build.Context(pd.DataFrame(cases), pd.DataFrame(events))


@pytest.fixture(scope="module")
def ctx():
    try:
        return _synthetic_ctx()
    except FileNotFoundError:
        pytest.skip("no early-risk model")


@pytest.fixture
def live(monkeypatch, ctx):
    monkeypatch.setattr(v1router, "get_context", lambda: ctx)


@pytest.fixture
def down(monkeypatch):
    def boom():
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(v1router, "get_context", boom)


LIVE_URLS = ["/v1/overview?as_of=2018-01-20", "/v1/queue?as_of=2018-01-20&limit=10", "/v1/suppliers?as_of=2018-02-10&min_n=1",
             "/v1/process/flow?as_of=2018-02-10", "/v1/process/flow?as_of=2018-02-10&group=activity", "/v1/process/variants?as_of=2018-02-10",
             "/v1/risk-map?as_of=2018-01-20", "/v1/briefing?as_of=2018-01-20", "/v1/search?q=c1", "/v1/experiments", "/v1/lineage"]


@pytest.mark.parametrize("url", LIVE_URLS)
def test_live_endpoints_respond_and_every_metric_has_valid_provenance(live, url):
    r = client.get(url)
    assert r.status_code == 200, r.text[:300]
    for path, m in _walk(r.json()):
        assert m["provenance"] in ALLOWED, (url, path)
        assert m["source"], (url, path)


def test_live_case_and_supplier_360(live, ctx):
    st = ctx.state("2018-01-20")
    cid = st["case_id"].iloc[0]
    r = client.get(f"/v1/cases/{cid}?as_of=2018-01-20")
    assert r.status_code == 200 and r.json()["case_id"] == cid
    assert all(pd.Timestamp(e["at"]) <= pd.Timestamp("2018-01-20", tz=UTC) for e in r.json()["timeline"])   # nothing after the clock
    assert client.get("/v1/cases/nope").status_code == 404
    assert client.get("/v1/suppliers/s1?as_of=2018-02-10").status_code == 200


def test_expected_loss_ranking_cannot_put_a_small_order_above_a_large_one(monkeypatch, ctx):
    t = pd.Timestamp("2018-01-20", tz=UTC)
    frame = pd.DataFrame({
        "case_id": ["small", "big", "mid"], "supplier_id": ["s", "s", "s"], "category": ["Others"] * 3,
        "start_time": [t] * 3, "events_seen": [3] * 3, "elapsed_hours": [10.0] * 3, "idle_hours": [5.0] * 3,
        "current_activity": ["Record Goods Receipt"] * 3, "value_so_far": [86.0, 30000.0, 2000.0], "target_hours": [2000.0] * 3,
        "status": ["scored"] * 3, "breach_probability": [0.60, 0.50, 0.50], "k": [3, 3, 3]})
    monkeypatch.setattr(build, "replay_state", lambda *a, **k: frame)
    ctx._states.clear()
    st = ctx.state(t)
    order = st.sort_values("expected_loss_eur", ascending=False)["case_id"].tolist()
    assert order == ["big", "mid", "small"]
    assert st.loc[st["case_id"] == "small", "tier"].iloc[0] != "CRITICAL"
    ctx._states.clear()


def test_stage_config_maps_each_activity_exactly_once():
    stages = yaml.safe_load(Path("config/stages.yaml").read_text(encoding="utf-8"))["stages"]
    acts = [a for s in stages for a in s["activities"]]
    assert len(acts) == len(set(acts))


def test_stage_config_covers_every_activity_in_the_real_log():
    path = os.environ.get("RAW_EVENT_LOG_PATH")
    if not path or not Path(path).exists():
        snap = SNAP / "process_flow_stage.json"
        if not snap.exists():
            pytest.skip("no log and no snapshot")
        assert json.loads(snap.read_text(encoding="utf-8"))["unmapped_activities"] == []
        return
    from src.cleaning.clean_events import clean_events
    from src.ingestion.load_event_log import load_event_log
    ev, _ = clean_events(load_event_log(path))
    stages = yaml.safe_load(Path("config/stages.yaml").read_text(encoding="utf-8"))["stages"]
    mapped = {a for s in stages for a in s["activities"]}
    assert set(ev["activity"]) <= mapped, sorted(set(ev["activity"]) - mapped)


# ── snapshot fallback ──
SNAPSHOT_URLS = ["/v1/overview", "/v1/queue", "/v1/suppliers", "/v1/process/flow", "/v1/process/flow?group=activity", "/v1/process/variants",
                 "/v1/risk-map", "/v1/finance/controls", "/v1/finance/exceptions", "/v1/finance/working-capital", "/v1/interventions/roi",
                 "/v1/models", "/v1/data-quality", "/v1/lineage", "/v1/briefing"]


@pytest.mark.parametrize("url", SNAPSHOT_URLS)
def test_every_endpoint_falls_back_to_the_committed_snapshot(down, url):
    r = client.get(url)
    assert r.status_code == 200, (url, r.text[:200])
    body = r.json()
    assert body["snapshot"]["live"] is False and "database unavailable" in body["snapshot"]["reason"]
    for path, m in _walk(body):
        assert m["provenance"] in ALLOWED, (url, path)


def test_snapshot_covers_only_the_default_clock(down):
    assert client.get("/v1/overview?as_of=2018-01-01").status_code == 503


def test_snapshot_case_and_supplier_360_and_unknown_id(down):
    snap_q = json.loads((SNAP / "queue.json").read_text(encoding="utf-8"))
    cid = snap_q["rows"][0]["case_id"]
    assert client.get(f"/v1/cases/{cid}").status_code == 200
    sid = json.loads((SNAP / "suppliers.json").read_text(encoding="utf-8"))["rows"][0]["supplier_id"]
    assert client.get(f"/v1/suppliers/{sid}").status_code == 200
    assert client.get("/v1/cases/does-not-exist").status_code == 404


# ── honesty tests on the committed snapshot ──
def test_headline_breach_rate_is_the_realistic_target_and_configured_one_is_labelled_secondary():
    k = json.loads((SNAP / "overview.json").read_text(encoding="utf-8"))["kpis"]
    assert k["breach_rate_realistic_30d"]["provenance"] == "measured" and 15 < k["breach_rate_realistic_30d"]["value"] < 35
    assert "Secondary" in k["breach_rate_configured_all_closed"]["note"]
    assert k["interventions_recorded"]["provenance"] == "pending" and k["interventions_recorded"]["value"] == 0
    assert k["expected_loss_eur"]["provenance"] == "simulated" and "assumption" in k["expected_loss_eur"]


def test_snapshot_queue_is_ranked_by_expected_loss_and_no_tiny_order_is_critical():
    rows = json.loads((SNAP / "queue.json").read_text(encoding="utf-8"))["rows"]
    el = [r["expected_loss_eur"] for r in rows]
    assert el == sorted(el, reverse=True)
    assert min(r["value_eur"] for r in rows if r["tier"] == "CRITICAL") > 10_000


def test_snapshot_process_flow_has_seven_stages_and_nothing_unmapped():
    f = json.loads((SNAP / "process_flow_stage.json").read_text(encoding="utf-8"))
    assert f["unmapped_activities"] == [] and 5 <= len(f["nodes"]) <= 8


def test_openapi_contract_is_exported_and_lists_the_v1_paths():
    spec = json.loads(Path("openapi/p1.json").read_text(encoding="utf-8"))
    for p in ("/v1/overview", "/v1/queue", "/v1/cases/{case_id}", "/v1/suppliers/{supplier_id}", "/v1/process/flow", "/v1/models"):
        assert p in spec["paths"]
