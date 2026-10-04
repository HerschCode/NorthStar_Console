import numpy as np
import pandas as pd
import pytest

from src.replay.clock import open_cases, replay_state

UTC = "UTC"


def _model():
    from src.ml.early_risk import EarlyRiskModel
    try:
        return EarlyRiskModel.load()
    except FileNotFoundError:
        pytest.skip("no early-risk model")


def _log(vocab_acts):
    rng = np.random.default_rng(0)
    cases, events = [], []
    for i in range(60):
        start = pd.Timestamp("2018-01-01", tz=UTC) + pd.Timedelta(hours=int(rng.integers(0, 24 * 40)))
        n = int(rng.integers(1, 8))
        ts = [start + pd.Timedelta(hours=float(h)) for h in np.cumsum(rng.uniform(1, 90, n))]
        ts[0] = start
        for j, t in enumerate(ts):
            events.append({"case_id": f"c{i}", "activity": vocab_acts[j % len(vocab_acts)], "timestamp": t,
                           "net_worth_eur": 100.0 * (j + 1) * (i + 1)})
        cases.append({"case_id": f"c{i}", "start_time": ts[0], "end_time": ts[-1], "supplier_id": f"s{i % 5}",
                      "category": "cat", "cycle_time_hours": (ts[-1] - ts[0]).total_seconds() / 3600})
    return pd.DataFrame(cases), pd.DataFrame(events)


def test_open_cases_are_started_and_not_yet_ended():
    cases = pd.DataFrame({"case_id": ["a", "b", "c"],
                          "start_time": pd.to_datetime(["2018-01-01", "2018-01-10", "2018-01-01"], utc=True),
                          "end_time": pd.to_datetime(["2018-01-05", "2018-02-01", "2018-03-01"], utc=True)})
    assert set(open_cases(cases, "2018-01-15")["case_id"]) == {"c", "b"}
    assert set(open_cases(cases, "2018-01-02")["case_id"]) == {"a", "c"}


def test_replay_state_never_reads_events_after_as_of():
    """Property: scores from the full log equal scores from a log truncated at as_of."""
    model = _model()
    acts = [a for a in model.meta["vocab"] if not a.startswith("<")][:6]
    cases, events = _log(acts)
    for as_of in ("2018-01-12", "2018-01-25", "2018-02-08"):
        t = pd.Timestamp(as_of, tz=UTC)
        full = replay_state(cases, events, t, model)
        trunc = replay_state(cases, events[events["timestamp"] <= t], t, model)
        pd.testing.assert_frame_equal(full, trunc)
        assert (full["elapsed_hours"] >= 0).all()


def test_statuses_follow_the_rules():
    model = _model()
    acts = [a for a in model.meta["vocab"] if not a.startswith("<")][:6]
    cases, events = _log(acts)
    st = replay_state(cases, events, "2018-01-20", model)
    assert set(st["status"]) <= {"too_early", "already_late", "scored"}
    late = st[st["status"] == "already_late"]
    assert (late["elapsed_hours"] > late["target_hours"]).all()
    assert st.loc[st["status"] == "scored", "breach_probability"].between(0, 1).all()
    assert st.loc[st["status"] != "scored", "breach_probability"].isna().all()
