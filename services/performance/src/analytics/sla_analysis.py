import pandas as pd
import yaml
from pathlib import Path


def load_sla_targets(config_path: str | Path = "config/sla.yaml") -> dict:
    path = Path(config_path)
    if not path.exists():
        return {"default": 240}  # 10 days, fallback

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    targets = {}
    for key, val in raw.items():
        if isinstance(val, dict) and "target_days" in val:
            targets[key] = val["target_days"] * 24
    targets.setdefault("default", 240)
    return targets


def evaluate_sla(cases: pd.DataFrame, sla_targets: dict, keep_unmeasurable: bool = False) -> pd.DataFrame:
    """Adds sla_target_hours and sla_breach.

    Cases with no measurable duration (cycle_time_hours <= 0: 96 single-event cases plus 8 whose events share one
    timestamp in the BPI 2019 sample) are DROPPED by default. They are truncated records, not instant orders, and
    labelling them "not breached" put 104 wrong negatives into every breach rate and into the model's training
    labels until 2026-09-28. keep_unmeasurable=True keeps them (breach label then means nothing) for callers that
    score one specific case rather than compute rates or train."""
    cases = cases.copy()
    if not keep_unmeasurable and "cycle_time_hours" in cases.columns:
        cases = cases[cases["cycle_time_hours"].astype(float) > 0]
    default_target = sla_targets.get("default", 240)

    if "category" in cases.columns:
        cases["sla_target_hours"] = (
            cases["category"].map(sla_targets).fillna(default_target)
        )
    else:
        cases["sla_target_hours"] = default_target

    cases["sla_breach"] = cases["cycle_time_hours"] > cases["sla_target_hours"]
    return cases


def sla_summary(evaluated_cases: pd.DataFrame, segment_col: str | None = None) -> pd.DataFrame:
    if segment_col and segment_col in evaluated_cases.columns:
        grouped = evaluated_cases.groupby(segment_col)
    else:
        evaluated_cases = evaluated_cases.assign(_all="all")
        grouped = evaluated_cases.groupby("_all")

    summary = grouped.agg(
        case_count=("sla_breach", "count"),
        breach_count=("sla_breach", "sum"),
    ).reset_index()
    summary["breach_rate_pct"] = (summary["breach_count"] / summary["case_count"]) * 100
    return summary


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

    targets = load_sla_targets()
    evaluated = evaluate_sla(cases, targets)

    print("Overall SLA performance:")
    print(sla_summary(evaluated).to_string(index=False))

    if "category" in evaluated.columns:
        print("\nSLA performance by category:")
        print(sla_summary(evaluated, "category").to_string(index=False))
