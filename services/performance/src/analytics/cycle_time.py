import pandas as pd


def stage_durations(events: pd.DataFrame) -> pd.DataFrame:
    events = events.sort_values(["case_id", "timestamp"]).copy()
    events["next_timestamp"] = events.groupby("case_id")["timestamp"].shift(-1)
    events["next_activity"] = events.groupby("case_id")["activity"].shift(-1)

    stages = events.dropna(subset=["next_timestamp"]).copy()
    stages["duration_hours"] = (
        stages["next_timestamp"] - stages["timestamp"]
    ).dt.total_seconds() / 3600

    stages["stage"] = stages["activity"] + " -> " + stages["next_activity"]
    return stages[["case_id", "stage", "duration_hours"]]


def stage_summary(events: pd.DataFrame) -> pd.DataFrame:
    stages = stage_durations(events)
    summary = stages.groupby("stage")["duration_hours"].agg(
        avg_hours="mean",
        median_hours="median",
        p90_hours=lambda s: s.quantile(0.9),
        case_count="count",
    ).reset_index()
    return summary.sort_values("avg_hours", ascending=False)


def cycle_time_percentiles(cases: pd.DataFrame) -> dict:
    ct = cases["cycle_time_hours"]
    return {
        "mean_hours": float(ct.mean()),
        "median_hours": float(ct.median()),
        "p90_hours": float(ct.quantile(0.9)),
        "p99_hours": float(ct.quantile(0.99)),
        "case_count": int(len(cases)),
    }


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events
    from src.transformation.build_process_cases import build_process_cases

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)

    print("Overall cycle time:")
    print(cycle_time_percentiles(cases))

    print("\nSlowest stages:")
    print(stage_summary(cleaned).head(10).to_string(index=False))
