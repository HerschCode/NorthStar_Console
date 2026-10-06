import math

import pytest

from src.roi.randomizer import HOLDOUT, TREAT, assign, uniform_draw


def test_assignment_is_deterministic():
    first = [assign(f"case-{i}", "exp-1", 0.2) for i in range(500)]
    again = [assign(f"case-{i}", "exp-1", 0.2) for i in range(500)]
    assert first == again


def test_holdout_share_is_balanced_within_binomial_error():
    n, share = 20000, 0.2
    holdout = sum(assign(f"C{i}", "exp-1", share) == HOLDOUT for i in range(n))
    sd = math.sqrt(n * share * (1 - share))
    assert abs(holdout - n * share) < 4 * sd, holdout


@pytest.mark.parametrize("share", [0.05, 0.5, 0.8])
def test_balance_holds_across_shares(share):
    n = 10000
    holdout = sum(assign(f"PO-{i:06d}", "exp-x", share) == HOLDOUT for i in range(n))
    assert abs(holdout / n - share) < 0.02


def test_boundary_shares():
    ids = [f"c{i}" for i in range(200)]
    assert all(assign(c, "e", 0.0) == TREAT for c in ids)
    assert all(assign(c, "e", 1.0) == HOLDOUT for c in ids)
    with pytest.raises(ValueError):
        assign("c", "e", 1.5)


def test_new_experiment_id_rerandomizes():
    a = [assign(f"c{i}", "exp-A", 0.5) for i in range(4000)]
    b = [assign(f"c{i}", "exp-B", 0.5) for i in range(4000)]
    agree = sum(x == y for x, y in zip(a, b)) / len(a)
    assert 0.45 < agree < 0.55          # independent draws agree ~50% of the time at a 50% share


def test_assignment_is_independent_of_case_attributes():
    """The draw uses only (experiment id, case id): ids that look alike are not assigned alike."""
    draws = [uniform_draw(f"200000000{i}_00001", "exp-1") for i in range(2000)]
    # consecutive ids: lag-1 correlation of the draws is ~0
    m = sum(draws) / len(draws)
    num = sum((draws[i] - m) * (draws[i + 1] - m) for i in range(len(draws) - 1))
    den = sum((d - m) ** 2 for d in draws)
    assert abs(num / den) < 0.06
    assert all(0 <= d < 1 for d in draws)
