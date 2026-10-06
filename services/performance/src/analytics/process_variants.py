import pandas as pd


def variant_summary(cases: pd.DataFrame, min_frequency: int = 1) -> pd.DataFrame:
    """One row per distinct variant: how common it is, and its average performance."""
    if "variant" not in cases.columns:
        raise ValueError("cases dataframe must have a 'variant' column (see build_process_cases)")

    grouped = cases.groupby("variant")
    summary = grouped.agg(
        case_count=("case_id", "count"),
        avg_cycle_time_hours=("cycle_time_hours", "mean"),
        median_cycle_time_hours=("cycle_time_hours", "median"),
    ).reset_index()

    if "sla_breach" in cases.columns:
        breach_rate = grouped["sla_breach"].mean().reset_index(name="sla_breach_rate")
        summary = summary.merge(breach_rate, on="variant")

    summary = summary[summary["case_count"] >= min_frequency]
    return summary.sort_values("case_count", ascending=False).reset_index(drop=True)


def compare_top_variants(cases: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """The N most common variants side by side, for a 'here's how the process actually
    runs vs. how it's supposed to run' view."""
    summary = variant_summary(cases)
    return summary.head(top_n)


def variant_by_segment(cases: pd.DataFrame, segment_col: str) -> pd.DataFrame:
    """Which variants dominate within each business-unit / category / supplier segment."""
    if segment_col not in cases.columns:
        raise ValueError(f"'{segment_col}' not found in cases dataframe")

    counts = (
        cases.groupby([segment_col, "variant"])
        .size()
        .reset_index(name="case_count")
        .sort_values([segment_col, "case_count"], ascending=[True, False])
    )
    return counts


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events
    from src.transformation.build_process_cases import build_process_cases
    from src.analytics.sla_analysis import load_sla_targets, evaluate_sla

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    evaluated = evaluate_sla(cases, load_sla_targets())

    print(f"{evaluated['variant'].nunique()} distinct variants across {len(evaluated)} cases\n")
    print("Top 5 variants:")
    print(compare_top_variants(evaluated).to_string(index=False))
