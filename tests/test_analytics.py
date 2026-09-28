import pandas as pd
import pytest
from src.analytics.cycle_time import stage_durations, stage_summary, cycle_time_percentiles
from src.analytics.bottlenecks import identify_bottlenecks
from src.analytics.sla_analysis import evaluate_sla, sla_summary
from src.analytics.rework import rework_by_case
from src.analytics.supplier_analysis import supplier_scorecard


def sample_events():
    return pd.DataFrame({
        "case_id": ["C1", "C1", "C1", "C2", "C2", "C3", "C3", "C3"],
        "activity": [
            "Created", "Approved", "Approved",  # C1 has a repeated Approved -> rework
            "Created", "Approved",
            "Created", "Approved", "Paid",
        ],
        "timestamp": pd.to_datetime([
            "2024-01-01 09:00", "2024-01-02 09:00", "2024-01-02 20:00",
            "2024-01-05 09:00", "2024-01-06 09:00",
            "2024-01-10 09:00", "2024-01-11 09:00", "2024-01-12 09:00",
        ]),
    })


def sample_cases():
    return pd.DataFrame({
        "case_id": ["C1", "C2", "C3"],
        "cycle_time_hours": [35.0, 24.0, 48.0],
        "category": ["3-way match", "3-way match", "Consignment"],
        "supplier_id": ["S1", "S1", "S2"],
    })


def test_stage_durations_produces_one_row_per_transition():
    stages = stage_durations(sample_events())
    # C1 has 3 events -> 2 transitions, C2 has 2 events -> 1 transition, C3 has 3 -> 2
    assert len(stages) == 5


def test_stage_summary_ranks_by_avg_hours_descending():
    summary = stage_summary(sample_events())
    assert list(summary["avg_hours"]) == sorted(summary["avg_hours"], reverse=True)


def test_cycle_time_percentiles_returns_expected_keys():
    result = cycle_time_percentiles(sample_cases())
    assert set(result.keys()) == {"mean_hours", "median_hours", "p90_hours", "p99_hours", "case_count"}
    assert result["case_count"] == 3


def test_identify_bottlenecks_percentages_sum_near_100():
    # min_case_count=1: this fixture's stages each have only 1-2 cases, well under
    # the real min_case_count=5 default that exists to filter single-case noise on
    # the actual BPI 2019 data -- this test verifies the percentage math, not the
    # sample-size floor, so it explicitly opts out of that floor.
    bottlenecks = identify_bottlenecks(sample_events(), top_n=10, min_case_count=1)
    # top_n=10 with only a few distinct stages should capture ~all of them
    assert bottlenecks["pct_of_total_delay"].sum() == pytest.approx(100.0, rel=0.01)


def test_evaluate_sla_flags_breaches_correctly():
    targets = {"default": 30, "3-way match": 30, "Consignment": 30}
    evaluated = evaluate_sla(sample_cases(), targets)
    # C1: 35h > 30h -> breach. C2: 24h <= 30h -> no breach. C3: 48h > 30h -> breach.
    breaches = evaluated.set_index("case_id")["sla_breach"]
    assert breaches["C1"] == True
    assert breaches["C2"] == False
    assert breaches["C3"] == True


def test_sla_summary_overall_breach_rate():
    targets = {"default": 30}
    evaluated = evaluate_sla(sample_cases(), targets)
    summary = sla_summary(evaluated)
    assert summary.iloc[0]["case_count"] == 3
    assert summary.iloc[0]["breach_count"] == 2


def test_rework_by_case_flags_repeated_activity():
    rework = rework_by_case(sample_events())
    c1 = rework[rework["case_id"] == "C1"].iloc[0]
    c2 = rework[rework["case_id"] == "C2"].iloc[0]
    assert c1["has_rework"] == True  # Approved appears twice
    assert c2["has_rework"] == False


def test_supplier_scorecard_excludes_below_min_volume():
    evaluated = evaluate_sla(sample_cases(), {"default": 30})
    scorecard = supplier_scorecard(evaluated, min_volume=2)
    # S1 has 2 orders (qualifies), S2 has 1 (excluded)
    assert "S1" in scorecard["supplier_id"].values
    assert "S2" not in scorecard["supplier_id"].values


def test_supplier_scorecard_raises_without_supplier_column():
    cases_no_supplier = sample_cases().drop(columns=["supplier_id"])
    with pytest.raises(ValueError):
        supplier_scorecard(cases_no_supplier)


def test_supplier_scorecard_ignores_zero_duration_cases():
    import pandas as pd
    from src.analytics.supplier_analysis import supplier_scorecard

    cases = pd.DataFrame({"case_id": list("abcdefg"), "supplier_id": ["S"] * 5 + ["T"] * 2,
                          "cycle_time_hours": [0, 0, 0, 100, 200, 50, 60], "sla_breach": [0, 0, 0, 1, 1, 0, 1]})
    s = supplier_scorecard(cases, min_volume=2).set_index("supplier_id")
    assert s.loc["S", "order_count"] == 2 and s.loc["S", "avg_cycle_time_hours"] == 150
    assert s.loc["S", "sla_breach_rate"] == 1.0


def test_evaluate_sla_drops_zero_duration_cases_unless_asked_to_keep_them():
    """Regression (2026-09-28): 104 truncated BPI 2019 cases with cycle_time_hours == 0 were labelled
    'not breached' and entered every breach rate and the model's training labels."""
    import pandas as pd
    from src.analytics.sla_analysis import evaluate_sla

    cases = pd.DataFrame({"case_id": ["a", "b", "c"], "category": ["x"] * 3, "cycle_time_hours": [0.0, 10.0, 500.0]})
    ev = evaluate_sla(cases, {"default": 240})
    assert list(ev["case_id"]) == ["b", "c"] and list(ev["sla_breach"]) == [False, True]
    assert len(evaluate_sla(cases, {"default": 240}, keep_unmeasurable=True)) == 3
