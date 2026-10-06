import pandas as pd
from src.cleaning.data_quality import run_data_quality_checks


def clean_events(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    report = run_data_quality_checks(df)

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    before = len(df)
    df = df.dropna(subset=["case_id", "activity", "timestamp"])
    dropped_missing = before - len(df)

    before = len(df)
    df = df.drop_duplicates(subset=["case_id", "activity", "timestamp"])
    dropped_duplicates = before - len(df)

    df = df.sort_values(["case_id", "timestamp"]).reset_index(drop=True)

    if "resource" in df.columns:
        df["resource"] = df["resource"].fillna("UNKNOWN")
    if "supplier_id" in df.columns:
        df["supplier_id"] = df["supplier_id"].fillna("UNKNOWN")
    if "user_id" in df.columns:
        # Source data uses the literal string "NONE" (not a null) for some events' actor --
        # normalize it to the same UNKNOWN bucket as a real missing value, so a
        # segregation-of-duties check never treats two different "no recorded actor" events as
        # the same person (found while building the AP controls, 2026-09-28).
        df["user_id"] = df["user_id"].replace("NONE", pd.NA).fillna("UNKNOWN")

    cleaning_summary = {
        "quality_report": report.as_dict(),
        "dropped_missing_required_fields": dropped_missing,
        "dropped_exact_duplicates": dropped_duplicates,
        "rows_after_cleaning": len(df),
    }
    return df, cleaning_summary


if __name__ == "__main__":
    import os
    import json
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, summary = clean_events(raw)
    print(json.dumps(summary, indent=2, default=str))
