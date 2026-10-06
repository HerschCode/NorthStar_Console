"""
Fail loudly, not silently, if the source data's shape changes underneath us.
This is deliberately separate from validate_input.py: validate_input checks the
CONTENT of a given run's data (nulls, bad timestamps). This checks the SCHEMA
CONTRACT itself -- did the source stop providing a column we depend on, or start
sending a type we don't expect. A schema-contract failure means "don't even try
to process this," whereas a validate_input warning means "process it, but flag
what's wrong."
"""
from dataclasses import dataclass, field
import pandas as pd

EXPECTED_SCHEMA = {
    "case_id": "string",
    "activity": "string",
    "timestamp": "string",  # still a string at this point; parsed downstream
}

OPTIONAL_SCHEMA = {
    "resource": "string",
    "purchase_order_id": "string",
    "item_id": "string",
    "category": "string",
    "supplier_id": "string",
}


@dataclass
class ContractCheckResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def _check_string_column(df: pd.DataFrame, col: str) -> str | None:
    """Checks that a column holds string-like data, tolerant of pandas' object/'string'/'str'
    dtype variants across versions (pandas 3.0 changed the default string dtype from
    'object' to 'str', which broke a strict equality check here -- is_string_dtype is the
    version-stable way to ask 'is this text' rather than pinning to one dtype's name)."""
    if not pd.api.types.is_string_dtype(df[col]) and not pd.api.types.is_object_dtype(df[col]):
        return f"Column '{col}' has dtype '{df[col].dtype}', expected string-like data"
    return None


def check_data_contract(df: pd.DataFrame) -> ContractCheckResult:
    violations = []

    for col in EXPECTED_SCHEMA:
        if col not in df.columns:
            violations.append(f"Required column '{col}' is missing entirely")
            continue
        issue = _check_string_column(df, col)
        if issue:
            violations.append(issue)

    for col in OPTIONAL_SCHEMA:
        if col in df.columns:
            issue = _check_string_column(df, col)
            if issue:
                violations.append(f"Optional: {issue}")

    if len(df) == 0:
        violations.append("Source data is empty (0 rows) -- refusing to process")

    return ContractCheckResult(passed=len(violations) == 0, violations=violations)


def enforce_data_contract(df: pd.DataFrame) -> None:
    """Raises if the contract is broken. Call this before anything else touches the data."""
    result = check_data_contract(df)
    if not result.passed:
        raise ValueError(
            "Data contract violated -- refusing to run the pipeline:\n  "
            + "\n  ".join(result.violations)
        )
