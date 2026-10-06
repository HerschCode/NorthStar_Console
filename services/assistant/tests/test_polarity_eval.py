import json
from pathlib import Path

import pytest

from scripts.polarity_eval import evaluate, paired_bootstrap_ci


def test_committed_polarity_report_matches_pinned_source_evaluation():
    faith = json.loads(
        Path("data/evaluation/faithfulness_results.json").read_text(encoding="utf-8")
    )
    contexts = json.loads(Path("data/evaluation/p2_gate_contexts.json").read_text(encoding="utf-8"))
    gate = json.loads(Path("reports/gate_labeled_eval.json").read_text(encoding="utf-8"))
    committed = json.loads(Path("reports/polarity_eval.json").read_text(encoding="utf-8"))

    generated = evaluate(faith["results"], contexts["contexts"], gate["chosen_support_recall"])

    assert generated == committed


def test_paired_bootstrap_reports_identical_rates_and_zero_change():
    outcomes = [True, False, True, False]
    result = paired_bootstrap_ci(outcomes, outcomes, n_resamples=200, seed=9)

    assert result["baseline_pass_rate_95_ci"] == result["polarity_pass_rate_95_ci"]
    assert result["paired_change_95_ci"] == [0.0, 0.0]


def test_paired_bootstrap_is_deterministic_and_preserves_pairing():
    baseline = [True, True, False, False]
    polarity = [True, False, False, False]
    first = paired_bootstrap_ci(baseline, polarity, n_resamples=1000, seed=41)
    second = paired_bootstrap_ci(baseline, polarity, n_resamples=1000, seed=41)

    assert first == second
    assert first["paired_change_95_ci"][1] <= 0


@pytest.mark.parametrize(
    ("baseline", "polarity", "n_resamples"),
    [
        ([], [], 100),
        ([True], [], 100),
        ([1], [True], 100),
        ([True], [False], 0),
    ],
)
def test_paired_bootstrap_rejects_invalid_inputs(baseline, polarity, n_resamples):
    with pytest.raises(ValueError):
        paired_bootstrap_ci(baseline, polarity, n_resamples=n_resamples)


def test_committed_report_has_paired_intervals_and_discloses_label_limits():
    report = json.loads(Path("reports/polarity_eval.json").read_text(encoding="utf-8"))

    assert report["bootstrap_method"]["resamples"] == 5000
    assert "labels independent or representative" in report["bootstrap_method"]["limitations"]
    assert report["correct"]["bootstrap_95_ci"]["paired_change_95_ci"] == [-0.156, 0.0]
    assert report["wrong_fact"]["bootstrap_95_ci"]["paired_change_95_ci"] == [-0.219, 0.0]
