import pytest

from src.roi.power import plan_binary_experiment, two_proportion_power


def test_power_planner_meets_target_at_smallest_reported_total():
    plan = plan_binary_experiment(0.4, 0.3, holdout_share=0.2, target_power=0.8)
    assert plan.total_n == plan.holdout_n + plan.treatment_n
    assert plan.achieved_power >= 0.8
    assert plan.total_n == 1 or two_proportion_power(
        0.4,
        0.3,
        max(1, round((plan.total_n - 1) * 0.2)),
        (plan.total_n - 1) - max(1, round((plan.total_n - 1) * 0.2)),
    ) < 0.8


def test_smaller_effect_requires_more_observations():
    small = plan_binary_experiment(0.4, 0.35, target_power=0.8)
    large = plan_binary_experiment(0.4, 0.3, target_power=0.8)
    assert small.total_n > large.total_n


def test_larger_holdout_share_changes_allocation_but_not_claimed_effect():
    plan = plan_binary_experiment(0.4, 0.3, holdout_share=0.5)
    assert plan.holdout_n == plan.treatment_n
    assert plan.achieved_power >= 0.8


@pytest.mark.parametrize(
    ("holdout_rate", "treatment_rate", "holdout_n", "treatment_n", "alpha"),
    [
        (-0.1, 0.2, 100, 100, 0.05),
        (0.1, 1.1, 100, 100, 0.05),
        (0.1, 0.2, 0, 100, 0.05),
        (0.1, 0.2, 100, True, 0.05),
        (0.1, 0.2, 100, 100, 1.0),
    ],
)
def test_power_rejects_invalid_inputs(holdout_rate, treatment_rate, holdout_n, treatment_n, alpha):
    with pytest.raises(ValueError):
        two_proportion_power(holdout_rate, treatment_rate, holdout_n, treatment_n, alpha)


@pytest.mark.parametrize(
    ("holdout_rate", "treatment_rate", "kwargs"),
    [
        (0.4, 0.4, {}),
        (0.4, 0.3, {"holdout_share": 0.0}),
        (0.4, 0.3, {"target_power": 0.05}),
        (0.4, 0.3, {"max_total_n": 1}),
        (0.4, 0.39, {"max_total_n": 100}),
    ],
)
def test_planner_rejects_undefined_or_unreachable_designs(holdout_rate, treatment_rate, kwargs):
    with pytest.raises(ValueError):
        plan_binary_experiment(holdout_rate, treatment_rate, **kwargs)


def test_equal_rates_have_approximately_alpha_power():
    assert two_proportion_power(0.4, 0.4, 1000, 1000) == pytest.approx(0.05, abs=0.002)
