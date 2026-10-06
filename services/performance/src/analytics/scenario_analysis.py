"""
What-if / scenario analysis: estimate the historical impact of a hypothetical process
change, using real historical data rather than a synthetic model. Deliberately
narrow -- one well-implemented scenario type (stage-duration reduction) rather than a
speculative framework for arbitrary hypotheticals, matching the original spec's own
instruction to "clearly label these as scenario estimates, not guaranteed
predictions" (FEATURES.md Tier 3).

Reuses stage_durations() and evaluate_sla() rather than recomputing anything --
same "one source of truth" discipline as the rest of this project.
"""
import pandas as pd

from src.analytics.cycle_time import stage_durations
from src.analytics.sla_analysis import evaluate_sla


def estimate_stage_reduction_impact(
    cases: pd.DataFrame, events: pd.DataFrame, stage: str, reduction_pct: float, sla_targets: dict
) -> dict:
    """
    Estimates the impact of reducing a specific stage transition's duration by
    `reduction_pct` (e.g. 0.5 for "what if Approval took 50% less time"), applied
    uniformly to every case that has that stage transition. This is an estimate
    from historical data, not a guarantee -- it assumes the reduction applies equally
    to every case and that nothing else about the process changes, neither of which
    a real intervention would necessarily achieve. Report findings accordingly:
    "historical data suggests..." not "this will save...".
    """
    if not 0 < reduction_pct <= 1:
        raise ValueError("reduction_pct must be between 0 (exclusive) and 1 (inclusive), e.g. 0.5 for 50%")

    stage_durs = stage_durations(events)
    stage_durs = stage_durs[stage_durs["stage"] == stage]
    if stage_durs.empty:
        raise ValueError(f"Stage '{stage}' not found in the event data -- check spelling against actual activity names")

    per_case_reduction = stage_durs.groupby("case_id")["duration_hours"].sum() * reduction_pct

    scenario_cases = cases.copy()
    scenario_cases["cycle_time_hours"] = (
        scenario_cases["cycle_time_hours"] - scenario_cases["case_id"].map(per_case_reduction).fillna(0)
    ).clip(lower=0)  # cycle time can't go negative even in a hypothetical

    baseline_evaluated = evaluate_sla(cases, sla_targets)
    scenario_evaluated = evaluate_sla(scenario_cases, sla_targets)

    cases_no_longer_breaching = sorted(
        set(baseline_evaluated.loc[baseline_evaluated["sla_breach"], "case_id"])
        - set(scenario_evaluated.loc[scenario_evaluated["sla_breach"], "case_id"])
    )

    return {
        "stage": stage,
        "reduction_pct": reduction_pct,
        "cases_affected": int((per_case_reduction > 0).sum()),
        "baseline": {
            "avg_cycle_time_hours": round(float(baseline_evaluated["cycle_time_hours"].mean()), 2),
            "breach_count": int(baseline_evaluated["sla_breach"].sum()),
            "breach_rate_pct": round(float(baseline_evaluated["sla_breach"].mean()) * 100, 2),
        },
        "scenario": {
            "avg_cycle_time_hours": round(float(scenario_evaluated["cycle_time_hours"].mean()), 2),
            "breach_count": int(scenario_evaluated["sla_breach"].sum()),
            "breach_rate_pct": round(float(scenario_evaluated["sla_breach"].mean()) * 100, 2),
        },
        "cases_that_would_no_longer_breach": cases_no_longer_breaching,
    }


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events
    from src.transformation.build_process_cases import build_process_cases
    from src.analytics.sla_analysis import load_sla_targets
    from src.analytics.bottlenecks import identify_bottlenecks

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)

    top_bottleneck = identify_bottlenecks(cleaned, top_n=1).iloc[0]["stage"]
    result = estimate_stage_reduction_impact(cases, cleaned, top_bottleneck, 0.5, load_sla_targets())
    print(f"Scenario: reduce '{top_bottleneck}' duration by 50%")
    print(f"  Baseline: {result['baseline']}")
    print(f"  Scenario: {result['scenario']}")
    print(f"  Cases that would no longer breach: {result['cases_that_would_no_longer_breach']}")
