from dataclasses import dataclass, field
import pandas as pd

REQUIRED_COLUMNS = ["case_id", "activity", "timestamp"]


@dataclass
class ValidationResult:
    is_valid: bool
    row_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_event_log(df: pd.DataFrame) -> ValidationResult:
    errors = []
    warnings = []

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {missing_cols}")
        return ValidationResult(is_valid=False, row_count=len(df), errors=errors)

    null_case_ids = df["case_id"].isna().sum()
    if null_case_ids > 0:
        errors.append(f"{null_case_ids} rows have a null case_id")

    null_activities = df["activity"].isna().sum()
    if null_activities > 0:
        errors.append(f"{null_activities} rows have a null activity")

    parsed_ts = pd.to_datetime(df["timestamp"], errors="coerce")
    bad_timestamps = parsed_ts.isna().sum()
    if bad_timestamps > 0:
        warnings.append(f"{bad_timestamps} rows have an unparseable timestamp")

    empty_cases = df.groupby("case_id").size()
    single_event_cases = (empty_cases == 1).sum()
    if single_event_cases > 0:
        warnings.append(f"{single_event_cases} cases contain only a single event")

    is_valid = len(errors) == 0
    return ValidationResult(
        is_valid=is_valid,
        row_count=len(df),
        errors=errors,
        warnings=warnings,
    )


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log

    load_dotenv()
    events = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    result = validate_event_log(events)
    print(f"Valid: {result.is_valid} | Rows: {result.row_count:,}")
    for e in result.errors:
        print(f"  ERROR: {e}")
    for w in result.warnings:
        print(f"  WARNING: {w}")
