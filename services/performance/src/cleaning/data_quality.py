from dataclasses import dataclass, field
import pandas as pd


@dataclass
class DataQualityReport:
    total_rows: int
    total_cases: int
    duplicate_rows: int
    duplicate_within_case: int
    missing_resource: int
    missing_supplier: int
    unparseable_timestamps: int
    out_of_order_events: int
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__


def _count_out_of_order(df: pd.DataFrame) -> int:
    """Detects timestamps that arrive out of chronological order WITHIN the data as
    given -- deliberately does not sort first. Sorting before diffing would silently
    fix the very problem we're trying to detect and always report zero."""
    df = df.dropna(subset=["timestamp"])
    diffs = df.groupby("case_id")["timestamp"].diff()
    return int((diffs.dt.total_seconds() < 0).sum())


def run_data_quality_checks(df: pd.DataFrame) -> DataQualityReport:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    duplicate_rows = int(df.duplicated().sum())
    duplicate_within_case = int(
        df.duplicated(subset=["case_id", "activity", "timestamp"]).sum()
    )
    missing_resource = int(df["resource"].isna().sum()) if "resource" in df else 0
    missing_supplier = (
        int(df["supplier_id"].isna().sum()) if "supplier_id" in df else 0
    )
    unparseable_timestamps = int(df["timestamp"].isna().sum())
    out_of_order = _count_out_of_order(df)

    notes = []
    if duplicate_rows / max(len(df), 1) > 0.05:
        notes.append("Duplicate rate exceeds 5% -- investigate ingestion source before proceeding")
    if unparseable_timestamps > 0:
        notes.append("Rows with unparseable timestamps will be excluded from cycle-time calcs")

    return DataQualityReport(
        total_rows=len(df),
        total_cases=df["case_id"].nunique(),
        duplicate_rows=duplicate_rows,
        duplicate_within_case=duplicate_within_case,
        missing_resource=missing_resource,
        missing_supplier=missing_supplier,
        unparseable_timestamps=unparseable_timestamps,
        out_of_order_events=out_of_order,
        notes=notes,
    )
