"""Leakage-safe evaluation splits: whole groups (e.g. purchase orders) on one side of a temporal cut, and an assertion that proves it."""
from __future__ import annotations

import pandas as pd


def group_temporal_split(df: pd.DataFrame, group_col: str, time_col: str, test_fraction: float = 0.2):
    """Order groups by their first timestamp; the latest `test_fraction` of groups form the test set. No group is split, and every test group
    starts at or after every train group, so neither group-level nor temporal leakage can occur."""
    first = df.groupby(group_col)[time_col].min().sort_values(kind="mergesort")
    n_test = max(1, int(round(len(first) * test_fraction)))
    test_groups = set(first.index[-n_test:])
    test = df[df[group_col].isin(test_groups)]
    train = df[~df[group_col].isin(test_groups)]
    assert_isolated(train, test, group_col, time_col)
    return train, test


def assert_isolated(train: pd.DataFrame, test: pd.DataFrame, group_col: str, time_col: str | None = None) -> None:
    shared = set(train[group_col]) & set(test[group_col])
    if shared:
        raise AssertionError(f"{len(shared)} {group_col} values appear in both train and test, e.g. {sorted(shared)[:3]}")
    if time_col is not None and len(train) and len(test):
        first_test = test.groupby(group_col)[time_col].min().min()
        last_train_start = train.groupby(group_col)[time_col].min().max()
        if first_test < last_train_start:
            raise AssertionError("a test group starts before the latest-starting train group (temporal leakage)")
