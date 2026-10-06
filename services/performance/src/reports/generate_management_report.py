"""
Turns raw analytics into the Finding -> Evidence -> Impact -> Recommendation shape
every earlier planning doc promised and no fresher project actually ships. This is
deliberately template-driven, not LLM-generated -- the numbers are exact and
traceable to a specific query, which matters more here than prose quality.
"""
from dataclasses import dataclass
import pandas as pd

from src.reports.generate_summary import build_executive_summary
from src.analytics.bottlenecks import identify_bottlenecks
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla
from src.analytics.root_causes import sla_breach_drivers
from src.analytics.rework import rework_by_case, rework_impact
from src.analytics.conformance import check_conformance, conformance_report


@dataclass
class Finding:
    title: str
    evidence: str
    impact: str
    recommendation: str
    priority: str  # HIGH | MEDIUM | LOW


def _bottleneck_finding(events: pd.DataFrame) -> Finding | None:
    top = identify_bottlenecks(events, top_n=1)
    if top.empty:
        return None
    row = top.iloc[0]
    return Finding(
        title=f"'{row['stage']}' is the largest process bottleneck",
        evidence=(
            f"Average {row['avg_hours']:.1f}h (median {row['median_hours']:.1f}h, "
            f"p90 {row['p90_hours']:.1f}h) across {int(row['case_count'])} cases, "
            f"accounting for {row['pct_of_total_delay']:.1f}% of total average stage delay."
        ),
        impact=(
            "This single stage transition is the primary driver of overall cycle time -- "
            "reducing it has more leverage than optimizing any other stage."
        ),
        recommendation=(
            "Investigate why cases wait at this specific transition (resourcing, approval "
            "thresholds, batching) before optimizing any other stage."
        ),
        priority="HIGH" if row["pct_of_total_delay"] > 25 else "MEDIUM",
    )


def _rework_finding(events: pd.DataFrame, cases: pd.DataFrame) -> Finding | None:
    rework = rework_by_case(events)
    if rework.empty or not rework["has_rework"].any():
        return None

    impact_df = rework_impact(cases, rework)
    if len(impact_df) < 2:
        return None

    with_rework = impact_df[impact_df["has_rework"] == True]
    without_rework = impact_df[impact_df["has_rework"] == False]
    if with_rework.empty or without_rework.empty:
        return None

    ct_with = with_rework.iloc[0]["cycle_time_hours"]
    ct_without = without_rework.iloc[0]["cycle_time_hours"]
    pct_slower = ((ct_with - ct_without) / ct_without) * 100 if ct_without else 0
    rework_rate = rework["has_rework"].mean() * 100

    return Finding(
        title="Rework materially slows down affected cases",
        evidence=(
            f"{rework_rate:.1f}% of cases involve rework. Cases with rework average "
            f"{ct_with:.1f}h vs {ct_without:.1f}h without -- {pct_slower:.0f}% slower."
        ),
        impact="Rework is a compounding cost: it delays the affected case and consumes capacity that would otherwise process other cases.",
        recommendation="Identify the specific activities driving repeat occurrences and address the root cause (e.g. approval criteria that trigger frequent rejection) rather than just monitoring the symptom.",
        priority="HIGH" if rework_rate > 15 else "MEDIUM",
    )


def _conformance_finding(events: pd.DataFrame) -> Finding | None:
    try:
        conformance = check_conformance(events)
    except FileNotFoundError:
        return None

    report = conformance_report(conformance)
    if report["total_cases"] == 0:
        return None

    non_conformant_pct = 100 - report["conformance_rate_pct"]
    top_deviation = max(report["deviation_breakdown"].items(), key=lambda kv: kv[1], default=(None, 0))

    return Finding(
        title=f"{non_conformant_pct:.1f}% of cases deviate from the expected process",
        evidence=(
            f"Conformance rate: {report['conformance_rate_pct']}%. "
            f"Most common deviation type: '{top_deviation[0]}' ({top_deviation[1]} occurrences)."
        ),
        impact="Deviations from the expected process are often where delay, rework, and SLA risk concentrate -- they're a leading indicator, not just a compliance metric.",
        recommendation=f"Prioritize investigating the '{top_deviation[0]}' deviation type specifically, since it's the most frequent.",
        priority="HIGH" if non_conformant_pct > 30 else "MEDIUM",
    )


def _sla_driver_finding(cases: pd.DataFrame) -> Finding | None:
    evaluated = evaluate_sla(cases, load_sla_targets())
    candidate_drivers = [c for c in ["category", "supplier_id", "variant"] if c in evaluated.columns]
    if not candidate_drivers:
        return None

    drivers = sla_breach_drivers(evaluated, candidate_drivers)
    if drivers.empty:
        return None

    drivers = drivers[drivers["case_count"] >= 3]  # avoid noisy tiny segments
    if drivers.empty:
        return None

    worst = drivers.sort_values("lift_vs_overall", ascending=False).iloc[0]

    return Finding(
        title=f"SLA breach rate is elevated for {worst['driver']} = '{worst['level']}'",
        evidence=(
            f"{worst['breach_rate']*100:.1f}% breach rate ({int(worst['case_count'])} cases), "
            f"{worst['lift_vs_overall']*100:+.1f}pp vs. the overall rate. "
            f"This is an association, not a proven cause -- see docs/analytical-methodology.md."
        ),
        impact="This segment is disproportionately responsible for SLA misses and is a reasonable place to target an intervention first.",
        recommendation=f"Review process handling specifically for {worst['driver']} = '{worst['level']}' before making process-wide changes.",
        priority="MEDIUM",
    )


def build_management_report(events: pd.DataFrame, cases: pd.DataFrame) -> dict:
    summary = build_executive_summary(events, cases)

    finding_builders = [
        lambda: _bottleneck_finding(events),
        lambda: _rework_finding(events, cases),
        lambda: _conformance_finding(events),
        lambda: _sla_driver_finding(cases),
    ]
    findings = [f() for f in finding_builders]
    findings = [f for f in findings if f is not None]
    findings.sort(key=lambda f: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[f.priority])

    return {
        "executive_summary": summary,
        "findings": findings,
    }


def render_markdown(report: dict) -> str:
    s = report["executive_summary"]
    lines = [
        "# Operations Performance -- Management Report",
        "",
        "## Executive Summary",
        f"- Cases analyzed: {s.total_cases}",
        f"- Average cycle time: {s.avg_cycle_time_hours}h",
        f"- SLA breach rate: {s.sla_breach_rate_pct}%",
        f"- Rework rate: {s.rework_rate_pct}%",
        f"- Top bottleneck: {s.top_bottleneck_stage} ({s.top_bottleneck_pct_of_delay}% of total delay)"
        if s.top_bottleneck_stage else "- Top bottleneck: n/a",
        "",
        "## Findings",
    ]
    for i, f in enumerate(report["findings"], 1):
        lines += [
            f"### {i}. {f.title} [{f.priority}]",
            f"**Evidence:** {f.evidence}",
            f"**Impact:** {f.impact}",
            f"**Recommendation:** {f.recommendation}",
            "",
        ]
    return "\n".join(lines)


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

    report = build_management_report(cleaned, cases)
    print(render_markdown(report))
