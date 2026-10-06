import pandas as pd


def supplier_scorecard(evaluated_cases: pd.DataFrame, min_volume: int = 5) -> pd.DataFrame:
    """Supplier-level performance. min_volume prevents a supplier with 1-2 orders
    from ranking as 'best' or 'worst' purely by chance."""
    if "supplier_id" not in evaluated_cases.columns:
        raise ValueError("cases dataframe has no supplier_id column")

    # Cases with zero measured cycle time (96 single-event cases plus 8 whose events share one timestamp in the
    # BPI 2019 sample) have no measurable duration -- they are truncated cases, not instant ones. Counting them
    # made vendorID_0358 (94 such cases) look like the fastest, never-breaching supplier. Found 2026-09-26.
    if "cycle_time_hours" in evaluated_cases.columns:
        evaluated_cases = evaluated_cases[evaluated_cases["cycle_time_hours"] > 0]

    agg = {
        "case_id": "count",
        "cycle_time_hours": ["mean", "std"],
    }
    if "sla_breach" in evaluated_cases.columns:
        agg["sla_breach"] = "mean"

    grouped = evaluated_cases.groupby("supplier_id").agg(agg)
    grouped.columns = ["_".join(c).strip("_") for c in grouped.columns]
    grouped = grouped.rename(columns={
        "case_id_count": "order_count",
        "cycle_time_hours_mean": "avg_cycle_time_hours",
        "cycle_time_hours_std": "cycle_time_std_hours",
        "sla_breach_mean": "sla_breach_rate",
    })

    grouped = grouped.reset_index()
    qualified = grouped[grouped["order_count"] >= min_volume].copy()
    qualified["rank_by_cycle_time"] = qualified["avg_cycle_time_hours"].rank()
    return qualified.sort_values("avg_cycle_time_hours")


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

    if "supplier_id" in evaluated.columns:
        print("Supplier scorecard (min 5 orders):")
        print(supplier_scorecard(evaluated).to_string(index=False))
    else:
        print("No supplier_id column present in this dataset export.")
