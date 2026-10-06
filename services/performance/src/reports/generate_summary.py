"""
Executive-summary numbers for a given period/segment -- the same shape the dashboard's
'Executive Overview' page and the management report both pull from, so the two never
drift out of sync with each other.
"""
from dataclasses import dataclass, asdict
import pandas as pd

from src.analytics.cycle_time import cycle_time_percentiles
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla, sla_summary
from src.analytics.bottlenecks import identify_bottlenecks
from src.analytics.rework import rework_by_case


@dataclass
class ExecutiveSummary:
    period_start: str | None
    period_end: str | None
    segment: str | None
    total_cases: int
    avg_cycle_time_hours: float
    sla_breach_rate_pct: float
    rework_rate_pct: float
    top_bottleneck_stage: str | None
    top_bottleneck_pct_of_delay: float | None


def filter_period(
    cases: pd.DataFrame, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """Parameterized by date range so the report isn't a single static snapshot --
    this is what makes it a tool someone would actually rerun weekly, not a one-off."""
    df = cases.copy()
    if start:
        df = df[df["start_time"] >= pd.Timestamp(start)]
    if end:
        df = df[df["start_time"] <= pd.Timestamp(end)]
    return df


def filter_segment(cases: pd.DataFrame, segment_col: str | None, segment_value: str | None) -> pd.DataFrame:
    if segment_col and segment_value and segment_col in cases.columns:
        return cases[cases[segment_col] == segment_value]
    return cases


def build_executive_summary(
    events: pd.DataFrame,
    cases: pd.DataFrame,
    start: str | None = None,
    end: str | None = None,
    segment_col: str | None = None,
    segment_value: str | None = None,
) -> ExecutiveSummary:
    scoped_cases = filter_period(cases, start, end)
    scoped_cases = filter_segment(scoped_cases, segment_col, segment_value)

    if scoped_cases.empty:
        return ExecutiveSummary(
            period_start=start, period_end=end, segment=segment_value,
            total_cases=0, avg_cycle_time_hours=0.0, sla_breach_rate_pct=0.0,
            rework_rate_pct=0.0, top_bottleneck_stage=None, top_bottleneck_pct_of_delay=None,
        )

    case_ids = set(scoped_cases["case_id"])
    scoped_events = events[events["case_id"].isin(case_ids)]

    ct = cycle_time_percentiles(scoped_cases)
    evaluated = evaluate_sla(scoped_cases, load_sla_targets())
    sla = sla_summary(evaluated).iloc[0]

    rework = rework_by_case(scoped_events)
    rework_rate = (rework["has_rework"].mean() * 100) if not rework.empty else 0.0

    bottlenecks = identify_bottlenecks(scoped_events, top_n=1)
    top_stage = bottlenecks.iloc[0]["stage"] if not bottlenecks.empty else None
    top_pct = float(bottlenecks.iloc[0]["pct_of_total_delay"]) if not bottlenecks.empty else None

    return ExecutiveSummary(
        period_start=start,
        period_end=end,
        segment=segment_value,
        total_cases=int(ct["case_count"]),
        avg_cycle_time_hours=round(ct["mean_hours"], 1),
        sla_breach_rate_pct=round(float(sla["breach_rate_pct"]), 1),
        rework_rate_pct=round(rework_rate, 1),
        top_bottleneck_stage=top_stage,
        top_bottleneck_pct_of_delay=round(top_pct, 1) if top_pct is not None else None,
    )


if __name__ == "__main__":
    import os
    import json
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events
    from src.transformation.build_process_cases import build_process_cases

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)

    summary = build_executive_summary(cleaned, cases)
    print(json.dumps(asdict(summary), indent=2))
