import pandas as pd
import pytest
from src.ingestion.data_contract import check_data_contract, enforce_data_contract


def test_contract_passes_on_well_formed_data():
    df = pd.DataFrame({
        "case_id": ["C1", "C2"],
        "activity": ["Created", "Approved"],
        "timestamp": ["2024-01-01", "2024-01-02"],
    })
    result = check_data_contract(df)
    assert result.passed
    assert result.violations == []


def test_contract_fails_on_missing_required_column():
    df = pd.DataFrame({
        "case_id": ["C1"],
        "timestamp": ["2024-01-01"],
        # 'activity' missing
    })
    result = check_data_contract(df)
    assert not result.passed
    assert any("activity" in v for v in result.violations)


def test_contract_fails_on_empty_dataframe():
    df = pd.DataFrame(columns=["case_id", "activity", "timestamp"])
    result = check_data_contract(df)
    assert not result.passed
    assert any("empty" in v.lower() for v in result.violations)


def test_enforce_data_contract_raises_on_violation():
    df = pd.DataFrame({"case_id": ["C1"]})  # missing activity, timestamp
    with pytest.raises(ValueError, match="Data contract violated"):
        enforce_data_contract(df)


def test_enforce_data_contract_silent_on_valid_data():
    df = pd.DataFrame({
        "case_id": ["C1"],
        "activity": ["Created"],
        "timestamp": ["2024-01-01"],
    })
    enforce_data_contract(df)  # should not raise
