import pandas as pd
import pytest

from src.mlops.splits import assert_isolated, group_temporal_split


def frame():
    rows = []
    for po in range(20):
        for k in range(3):
            rows.append({"po": f"P{po}", "case": f"P{po}-{k}", "t": pd.Timestamp("2024-01-01") + pd.Timedelta(days=po * 3 + k)})
    return pd.DataFrame(rows)


def test_split_keeps_groups_whole_and_future_only():
    tr, te = group_temporal_split(frame(), "po", "t", 0.25)
    assert set(tr.po).isdisjoint(te.po) and len(te.po.unique()) == 5
    assert te.groupby("po").t.min().min() >= tr.groupby("po").t.min().max()


def test_assertion_catches_leaky_split():
    df = frame()
    with pytest.raises(AssertionError, match="both train and test"):
        assert_isolated(df.iloc[:30], df.iloc[20:], "po", "t")        # cases of one PO on both sides
    with pytest.raises(AssertionError, match="temporal"):
        assert_isolated(df[df.po >= "P5"], df[df.po == "P1"], "po", "t")
