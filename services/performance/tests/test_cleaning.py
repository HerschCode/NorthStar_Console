import pandas as pd
from src.cleaning.clean_events import clean_events
from src.cleaning.data_quality import run_data_quality_checks


def make_raw_events():
    return pd.DataFrame({
        "case_id": ["C1", "C1", "C1", "C2", "C2", None, "C1"],
        "activity": ["Created", "Approved", "Approved", "Created", "Paid", "Created", "Created"],
        "timestamp": [
            "2024-01-01 09:00:00",
            "2024-01-02 10:00:00",
            "2024-01-02 10:00:00",  # exact duplicate
            "2024-01-03 08:00:00",
            "2024-01-05 12:00:00",
            "2024-01-01 09:00:00",  # will be dropped: null case_id
            "2024-01-01 09:00:00",  # duplicate of first row
        ],
    })


def test_clean_events_drops_null_case_ids():
    raw = make_raw_events()
    cleaned, summary = clean_events(raw)
    assert cleaned["case_id"].isna().sum() == 0
    assert summary["dropped_missing_required_fields"] >= 1


def test_clean_events_drops_exact_duplicates():
    raw = make_raw_events()
    cleaned, summary = clean_events(raw)
    dupes = cleaned.duplicated(subset=["case_id", "activity", "timestamp"]).sum()
    assert dupes == 0
    assert summary["dropped_exact_duplicates"] >= 1


def test_clean_events_sorts_by_case_and_timestamp():
    raw = make_raw_events()
    cleaned, _ = clean_events(raw)
    for _, group in cleaned.groupby("case_id"):
        assert group["timestamp"].is_monotonic_increasing


def test_data_quality_report_counts_out_of_order_events():
    df = pd.DataFrame({
        "case_id": ["C1", "C1"],
        "activity": ["Created", "Approved"],
        "timestamp": ["2024-01-05 09:00:00", "2024-01-01 09:00:00"],  # goes backwards
    })
    report = run_data_quality_checks(df)
    assert report.out_of_order_events == 1


def test_clean_events_empty_input_raises_no_error():
    empty = pd.DataFrame(columns=["case_id", "activity", "timestamp"])
    cleaned, summary = clean_events(empty)
    assert len(cleaned) == 0
    assert summary["rows_after_cleaning"] == 0
