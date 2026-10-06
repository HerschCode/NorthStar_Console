import pandas as pd


def driver_comparison(cases: pd.DataFrame, driver_col: str, metric_col: str = "cycle_time_hours") -> pd.DataFrame:
    """Compare a metric (default: cycle time) across levels of a candidate driver
    (supplier, category, business unit, has_rework, etc). This shows association,
    not causation -- say so when reporting it."""
    if driver_col not in cases.columns:
        raise ValueError(f"'{driver_col}' not found in cases dataframe")
    if metric_col not in cases.columns:
        raise ValueError(f"'{metric_col}' not found in cases dataframe")

    summary = cases.groupby(driver_col)[metric_col].agg(
        avg="mean", median="median", case_count="count"
    ).reset_index()
    return summary.sort_values("avg", ascending=False)


def sla_breach_drivers(evaluated_cases: pd.DataFrame, driver_cols: list[str]) -> pd.DataFrame:
    """For each candidate driver column, breach rate at each level, plus the overall
    breach rate so you can see the lift/deficit at a glance."""
    if "sla_breach" not in evaluated_cases.columns:
        raise ValueError("cases must be SLA-evaluated first (see sla_analysis.evaluate_sla)")

    overall_rate = evaluated_cases["sla_breach"].mean()
    rows = []
    for col in driver_cols:
        if col not in evaluated_cases.columns:
            continue
        grouped = evaluated_cases.groupby(col)["sla_breach"].agg(
            breach_rate="mean", case_count="count"
        ).reset_index()
        grouped["driver"] = col
        grouped = grouped.rename(columns={col: "level"})
        grouped["lift_vs_overall"] = grouped["breach_rate"] - overall_rate
        rows.append(grouped[["driver", "level", "case_count", "breach_rate", "lift_vs_overall"]])

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def correlation_matrix(cases: pd.DataFrame, numeric_cols: list[str]) -> pd.DataFrame:
    """Simple correlation among numeric fields -- a starting point for root-cause
    hypotheses, not a conclusion on its own."""
    available = [c for c in numeric_cols if c in cases.columns]
    if len(available) < 2:
        raise ValueError("Need at least 2 numeric columns present to compute correlations")
    return cases[available].corr()


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

    candidate_drivers = [c for c in ["category", "supplier_id", "variant"] if c in evaluated.columns]
    if candidate_drivers:
        print("SLA breach rate by candidate driver:")
        print(sla_breach_drivers(evaluated, candidate_drivers).to_string(index=False))
