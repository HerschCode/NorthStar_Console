import pandas as pd
from src.transformation.build_process_cases import build_process_cases


def sample_events():
    return pd.DataFrame({
        "case_id": ["C1", "C1", "C1", "C2", "C2"],
        "activity": ["Created", "Approved", "Paid", "Created", "Approved"],
        "timestamp": pd.to_datetime([
            "2024-01-01 09:00", "2024-01-02 09:00", "2024-01-03 09:00",
            "2024-01-05 09:00", "2024-01-05 15:00",
        ]),
        "supplier_id": ["S1", "S1", "S1", "S2", "S2"],
        "purchase_order_id": ["PO1", "PO1", "PO1", "PO2", "PO2"],
        "category": ["3-way match", "3-way match", "3-way match", "Consignment", "Consignment"],
    })


def test_build_process_cases_one_row_per_case():
    cases = build_process_cases(sample_events())
    assert len(cases) == 2
    assert set(cases["case_id"]) == {"C1", "C2"}


def test_build_process_cases_computes_cycle_time_correctly():
    cases = build_process_cases(sample_events())
    c1 = cases[cases["case_id"] == "C1"].iloc[0]
    assert c1["cycle_time_hours"] == 48.0  # exactly 2 days between first and last event


def test_build_process_cases_captures_event_count():
    cases = build_process_cases(sample_events())
    c1 = cases[cases["case_id"] == "C1"].iloc[0]
    c2 = cases[cases["case_id"] == "C2"].iloc[0]
    assert c1["event_count"] == 3
    assert c2["event_count"] == 2


def test_build_process_cases_builds_variant_string():
    cases = build_process_cases(sample_events())
    c1 = cases[cases["case_id"] == "C1"].iloc[0]
    assert c1["variant"] == "Created -> Approved -> Paid"


def test_build_process_cases_variant_frequency_counts_shared_variants():
    # two cases with the identical variant should both show frequency 2
    events = pd.DataFrame({
        "case_id": ["C1", "C1", "C2", "C2"],
        "activity": ["Created", "Approved", "Created", "Approved"],
        "timestamp": pd.to_datetime([
            "2024-01-01 09:00", "2024-01-02 09:00",
            "2024-01-03 09:00", "2024-01-04 09:00",
        ]),
    })
    cases = build_process_cases(events)
    assert (cases["variant_frequency"] == 2).all()


def test_build_process_cases_carries_optional_columns_when_present():
    cases = build_process_cases(sample_events())
    assert "supplier_id" in cases.columns
    assert cases.set_index("case_id")["purchase_order_id"].to_dict() == {"C1": "PO1", "C2": "PO2"}
    assert "category" in cases.columns


def test_build_process_cases_missing_optional_columns_does_not_error():
    events = sample_events().drop(columns=["supplier_id", "category"])
    cases = build_process_cases(events)
    assert "supplier_id" not in cases.columns
    assert len(cases) == 2
