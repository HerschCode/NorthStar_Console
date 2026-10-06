import yaml
from pathlib import Path
import pandas as pd


def _load_allowed_repeats(config_path: str | Path = "config/process.yaml") -> set[str]:
    path = Path(config_path)
    if not path.exists():
        return set()
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return set(raw.get("allowed_repeats", []))


def rework_by_case(events: pd.DataFrame, config_path: str | Path = "config/process.yaml") -> pd.DataFrame:
    """Per case: how many activity repeats occurred that AREN'T explicitly allowed
    (e.g. re-approval after a rejected PO, not a normal multi-line goods receipt).

    Vectorized (Phase 30 rewrite) -- the original implementation looped over every
    case in Python (`for case_id, group in events.groupby("case_id")`), calling
    `.value_counts()` per iteration. Benchmarked at ~978ms for 2,000 cases vs. ~20-50ms
    for sibling functions doing an equivalent amount of work with a single vectorized
    groupby -- an 8-40x difference that's pure Python-loop overhead, not real
    computation. This version does one groupby over the whole DataFrame instead of one
    per case."""
    allowed = _load_allowed_repeats(config_path)

    counts = events.groupby(["case_id", "activity"]).size().reset_index(name="count")
    repeated = counts[(counts["count"] > 1) & (~counts["activity"].isin(allowed))].copy()
    repeated["extra_occurrences"] = repeated["count"] - 1

    all_case_ids = sorted(events["case_id"].unique())  # matches the original's sorted groupby order
    result = pd.DataFrame({"case_id": all_case_ids}).set_index("case_id")

    result["rework_count"] = repeated.groupby("case_id")["extra_occurrences"].sum()
    result["rework_count"] = result["rework_count"].fillna(0).astype(int)

    activities_joined = repeated.groupby("case_id")["activity"].apply(lambda s: ",".join(s))
    result["rework_activities"] = activities_joined
    result["rework_activities"] = result["rework_activities"].where(result["rework_activities"].notna(), None)

    result["has_rework"] = result["rework_count"] > 0
    return result.reset_index()


def rework_impact(cases: pd.DataFrame, rework: pd.DataFrame) -> pd.DataFrame:
    """Does rework actually correlate with longer cycle time / more SLA breaches?"""
    merged = cases.merge(rework[["case_id", "has_rework"]], on="case_id", how="left")
    merged["has_rework"] = merged["has_rework"].fillna(False)

    agg = {"cycle_time_hours": "mean", "case_id": "count"}
    if "sla_breach" in merged.columns:
        agg["sla_breach"] = "mean"

    summary = merged.groupby("has_rework").agg(agg).rename(columns={"case_id": "case_count"})
    return summary.reset_index()


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

    rework = rework_by_case(cleaned)
    print(f"{rework['has_rework'].sum()} of {len(rework)} cases involve rework\n")
    print("Cycle time / SLA breach: rework vs. no rework")
    print(rework_impact(evaluated, rework).to_string(index=False))
