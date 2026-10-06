"""
Phase 27: proves the real pipeline produces the exact numbers hand-derived in
docs/data-correctness-audit.md -- BEFORE writing this test, every value below was
computed with a calculator against data/golden/golden_raw_events.csv, independently
of the code. This is what makes it a correctness audit rather than a regression test:
a regression test would pass even if the implementation and the expected value shared
the same bug, because both were written by looking at the same code. These numbers
were not.
"""
import pytest
from src.ingestion.load_event_log import load_event_log
from src.ingestion.validate_input import validate_event_log
from src.cleaning.clean_events import clean_events
from src.transformation.build_process_cases import build_process_cases
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla, sla_summary
from src.analytics.rework import rework_by_case
from src.analytics.conformance import check_conformance, conformance_report

TEXTBOOK = __import__('pathlib').Path(__file__).parent / 'fixtures' / 'process_textbook.yaml'
from src.analytics.supplier_analysis import supplier_scorecard
from src.analytics.bottlenecks import identify_bottlenecks
from src.analytics.cycle_time import cycle_time_percentiles

GOLDEN_PATH = "data/golden/golden_raw_events.csv"


@pytest.fixture(scope="module")
def golden_pipeline():
    """Runs the golden dataset through the real pipeline once per test module --
    every test below asserts against this same run, matching how a single pipeline
    run would actually produce all these numbers together in production."""
    raw = load_event_log(GOLDEN_PATH)
    validation = validate_event_log(raw)
    assert validation.is_valid, f"golden dataset failed validation: {validation.errors}"

    cleaned, cleaning_summary = clean_events(raw)
    cases = build_process_cases(cleaned)
    evaluated = evaluate_sla(cases, load_sla_targets())

    return {"raw": raw, "cleaned": cleaned, "cases": cases, "evaluated": evaluated}


def test_golden_dataset_loads_all_7_cases(golden_pipeline):
    assert golden_pipeline["cases"]["case_id"].nunique() == 7


def test_cycle_time_percentiles_match_hand_calculation(golden_pipeline):
    result = cycle_time_percentiles(golden_pipeline["cases"])
    assert result["case_count"] == 7
    assert result["mean_hours"] == pytest.approx(114.857, abs=0.01)
    assert result["median_hours"] == pytest.approx(72.0, abs=0.01)
    assert result["p90_hours"] == pytest.approx(256.8, abs=0.01)
    assert result["p99_hours"] == pytest.approx(263.28, abs=0.01)


def test_sla_breach_rate_matches_hand_calculation(golden_pipeline):
    summary = sla_summary(golden_pipeline["evaluated"])
    assert summary.iloc[0]["case_count"] == 7
    assert summary.iloc[0]["breach_count"] == 2
    assert summary.iloc[0]["breach_rate_pct"] == pytest.approx(28.57, abs=0.01)


def test_sla_breach_flags_correct_specific_cases(golden_pipeline):
    evaluated = golden_pipeline["evaluated"].set_index("case_id")
    assert evaluated.loc["GC01", "sla_breach"] == False
    assert evaluated.loc["GC02", "sla_breach"] == True
    assert evaluated.loc["GC03", "sla_breach"] == False
    assert evaluated.loc["GC04", "sla_breach"] == False
    assert evaluated.loc["GC05", "sla_breach"] == False
    assert evaluated.loc["GC06", "sla_breach"] == False
    assert evaluated.loc["GC07", "sla_breach"] == True


def test_rework_matches_hand_calculation(golden_pipeline):
    rework = rework_by_case(golden_pipeline["cleaned"], config_path=TEXTBOOK)
    rework_by_id = rework.set_index("case_id")
    assert rework_by_id.loc["GC03", "has_rework"] == True
    assert rework_by_id.loc["GC04", "has_rework"] == False  # allowed GR repeat, not rework
    assert rework["has_rework"].sum() == 1
    rework_rate = rework["has_rework"].mean() * 100
    assert rework_rate == pytest.approx(14.29, abs=0.01)


def test_conformance_matches_hand_calculation(golden_pipeline):
    conformance = check_conformance(golden_pipeline["cleaned"], config_path=TEXTBOOK)
    report = conformance_report(conformance)
    assert report["total_cases"] == 7
    assert report["conformant_cases"] == 4
    assert report["conformance_rate_pct"] == pytest.approx(57.1, abs=0.1)
    assert report["deviation_breakdown"].get("repeated_activity") == 1
    assert report["deviation_breakdown"].get("skipped_step") == 1
    assert report["deviation_breakdown"].get("unexpected_activity") == 1
    # None of the 7 golden cases are actually out of order (GC03 and GC04 repeat an
    # activity but the underlying sequence is otherwise correct) -- this specifically
    # guards against the Phase 27 bug regressing (a repeated activity, allowed or not,
    # previously always triggered a spurious out_of_order flag on top of whatever
    # else was legitimately wrong, or on its own for an otherwise-fine case like GC04).
    assert report["deviation_breakdown"].get("out_of_order") is None


def test_conformance_flags_correct_specific_cases(golden_pipeline):
    conformance = check_conformance(golden_pipeline["cleaned"], config_path=TEXTBOOK).set_index("case_id")
    assert conformance.loc["GC01", "is_conformant"] == True
    assert conformance.loc["GC02", "is_conformant"] == True
    assert conformance.loc["GC03", "is_conformant"] == False
    assert conformance.loc["GC04", "is_conformant"] == True  # allowed repeat
    assert conformance.loc["GC05", "is_conformant"] == False
    assert conformance.loc["GC06", "is_conformant"] == False
    assert conformance.loc["GC07", "is_conformant"] == True


def test_supplier_scorecard_matches_hand_calculation(golden_pipeline):
    scorecard = supplier_scorecard(golden_pipeline["evaluated"], min_volume=2).set_index("supplier_id")

    assert scorecard.loc["S1", "order_count"] == 3
    assert scorecard.loc["S1", "avg_cycle_time_hours"] == pytest.approx(128.0, abs=0.01)
    assert scorecard.loc["S1", "sla_breach_rate"] == pytest.approx(0.333, abs=0.001)

    assert scorecard.loc["S2", "order_count"] == 2
    assert scorecard.loc["S2", "avg_cycle_time_hours"] == pytest.approx(66.0, abs=0.01)
    assert scorecard.loc["S2", "sla_breach_rate"] == pytest.approx(0.0, abs=0.001)

    assert scorecard.loc["S3", "order_count"] == 2
    assert scorecard.loc["S3", "avg_cycle_time_hours"] == pytest.approx(144.0, abs=0.01)
    assert scorecard.loc["S3", "sla_breach_rate"] == pytest.approx(0.5, abs=0.001)


def test_supplier_scorecard_identifies_s3_as_worst(golden_pipeline):
    """S3 is worst by BOTH avg cycle time and breach rate -- deliberately unambiguous
    (see docs/data-correctness-audit.md), so this is a genuine business-question
    check, not just a numeric assertion."""
    scorecard = supplier_scorecard(golden_pipeline["evaluated"], min_volume=2)
    worst_by_cycle_time = scorecard.sort_values("avg_cycle_time_hours", ascending=False).iloc[0]
    worst_by_breach_rate = scorecard.sort_values("sla_breach_rate", ascending=False).iloc[0]
    assert worst_by_cycle_time["supplier_id"] == "S3"
    assert worst_by_breach_rate["supplier_id"] == "S3"


def test_bottleneck_identifies_correct_top_stage(golden_pipeline):
    bottlenecks = identify_bottlenecks(golden_pipeline["cleaned"], top_n=10)
    top = bottlenecks.iloc[0]
    assert top["stage"] == "Purchase Requisition -> Approval"
    assert top["avg_hours"] == pytest.approx(41.857, abs=0.01)
    assert top["median_hours"] == pytest.approx(10.0, abs=0.01)
    assert top["case_count"] == 7
    assert top["pct_of_total_delay"] == pytest.approx(24.16, abs=0.1)


def test_bottleneck_mean_median_gap_is_real_not_an_artifact(golden_pipeline):
    """The whole point of docs/analytical-methodology.md's mean-vs-median argument,
    proven concretely: this stage's mean is meaningfully higher than its median
    because of two genuine outlier cases (GC02, GC07), not a calculation quirk."""
    bottlenecks = identify_bottlenecks(golden_pipeline["cleaned"], top_n=10)
    top = bottlenecks.iloc[0]
    assert top["avg_hours"] > 4 * top["median_hours"]


def test_scenario_reduction_matches_hand_calculation(golden_pipeline):
    """Extends the Phase 27 hand-verification discipline to scenario_analysis.py.
    Reducing 'Purchase Requisition -> Approval' by 50% across all 7 golden cases:
    GC01 72-5=67, GC02 264-60=204, GC03 96-12=84, GC04 48-4=44, GC05 36-3=33,
    GC06 36-2.5=33.5, GC07 252-60=192. New mean = 657.5/7 = 93.928...h.
    GC02 (was 264h > 240h) and GC07 (was 252h > 240h) both drop below their 240h
    target and stop breaching; no other case's breach status changes."""
    from src.analytics.scenario_analysis import estimate_stage_reduction_impact
    from src.analytics.sla_analysis import load_sla_targets

    result = estimate_stage_reduction_impact(
        golden_pipeline["cases"], golden_pipeline["cleaned"],
        "Purchase Requisition -> Approval", 0.5, load_sla_targets(),
    )

    assert result["baseline"]["breach_count"] == 2
    assert result["scenario"]["breach_count"] == 0
    assert result["scenario"]["avg_cycle_time_hours"] == pytest.approx(93.93, abs=0.01)
    assert result["cases_that_would_no_longer_breach"] == ["GC02", "GC07"]
    assert result["cases_affected"] == 7  # every golden case has this stage transition


def test_scenario_reduction_rejects_invalid_percentage(golden_pipeline):
    from src.analytics.scenario_analysis import estimate_stage_reduction_impact
    from src.analytics.sla_analysis import load_sla_targets

    with pytest.raises(ValueError, match="reduction_pct"):
        estimate_stage_reduction_impact(
            golden_pipeline["cases"], golden_pipeline["cleaned"],
            "Purchase Requisition -> Approval", 1.5, load_sla_targets(),
        )


def test_scenario_reduction_rejects_unknown_stage(golden_pipeline):
    from src.analytics.scenario_analysis import estimate_stage_reduction_impact
    from src.analytics.sla_analysis import load_sla_targets

    with pytest.raises(ValueError, match="not found"):
        estimate_stage_reduction_impact(
            golden_pipeline["cases"], golden_pipeline["cleaned"],
            "Nonexistent Stage -> Also Fake", 0.5, load_sla_targets(),
        )


def test_production_expected_sequence_uses_activity_names_that_exist_in_the_log():
    """Regression: config/process.yaml once listed textbook names absent from BPI 2019, making conformance 0% by
    construction. Every expected activity must occur in the real sample log."""
    import os
    import yaml
    import pandas as pd

    path = os.environ.get("RAW_EVENT_LOG_PATH", "data/raw/bpi2019_events.csv")
    if not os.path.exists(path):
        import pytest
        pytest.skip("raw event log not present")
    names = set(pd.read_csv(path, usecols=["concept:name"])["concept:name"])
    expected = yaml.safe_load(open("config/process.yaml"))["expected_sequence"]
    assert set(expected) <= names, set(expected) - names
