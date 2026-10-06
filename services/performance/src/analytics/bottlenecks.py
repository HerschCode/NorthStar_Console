import pandas as pd
from src.analytics.cycle_time import stage_summary


def identify_bottlenecks(events: pd.DataFrame, top_n: int = 10, min_case_count: int = 5) -> pd.DataFrame:
    """min_case_count guards against a real issue found on the live BPI 2019 data:
    a stage transition that only 1-2 cases ever go through can have a mean delay
    of tens of thousands of hours purely from one real, anomalous case in the
    public dataset (a data-quality property of BPI 2019 itself, not a bug in this
    code) -- ranking by raw mean with no sample-size floor puts that single-case
    noise at the top of "Top Bottlenecks", crowding out the systemic bottlenecks
    that actually affect many cases. pct_of_total_delay is still computed against
    ALL stages (not just the ones meeting the floor), so it keeps meaning "share
    of total measured delay" rather than silently changing denominator when the
    floor is applied -- verified by tests/test_data_correctness.py's golden-dataset
    percentage assertion, which uses a stage well above this floor."""
    summary = stage_summary(events)
    total_hours = summary["avg_hours"].sum()
    summary["pct_of_total_delay"] = (summary["avg_hours"] / total_hours) * 100
    summary = summary[summary["case_count"] >= min_case_count]
    return summary.sort_values("avg_hours", ascending=False).head(top_n).reset_index(drop=True)


def bottlenecks_by_segment(
    events: pd.DataFrame, cases: pd.DataFrame, segment_col: str, top_n: int = 5
) -> pd.DataFrame:
    if segment_col not in cases.columns:
        raise ValueError(f"'{segment_col}' not found in cases dataframe")

    merged = events.merge(cases[["case_id", segment_col]], on="case_id", how="left")
    results = []
    for segment_value, group in merged.groupby(segment_col):
        top = identify_bottlenecks(group, top_n=top_n)
        top[segment_col] = segment_value
        results.append(top)

    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


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

    print("Top bottleneck stages overall:")
    print(identify_bottlenecks(cleaned).to_string(index=False))

    if "category" in cases.columns:
        print("\nBottlenecks by category:")
        print(bottlenecks_by_segment(cleaned, cases, "category").to_string(index=False))
