"""Normal-approximation power planning for a two-arm binary-outcome experiment."""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist


@dataclass(frozen=True)
class PowerPlan:
    holdout_n: int
    treatment_n: int
    total_n: int
    achieved_power: float


def _probability(value: float, name: str) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a finite probability in [0, 1]")
    return value


def _arm_sizes(total_n: int, holdout_share: float) -> tuple[int, int]:
    holdout_n = min(total_n - 1, max(1, round(total_n * holdout_share)))
    return holdout_n, total_n - holdout_n


def two_proportion_power(
    holdout_rate: float,
    treatment_rate: float,
    holdout_n: int,
    treatment_n: int,
    alpha: float = 0.05,
) -> float:
    """Approximate two-sided power for a difference in independent proportions.

    Uses a pooled null standard error for the rejection boundary and the
    unpooled standard error under the alternative. This is a planning
    approximation, not a replacement for an analysis registered in advance.
    """
    holdout_rate = _probability(holdout_rate, "holdout_rate")
    treatment_rate = _probability(treatment_rate, "treatment_rate")
    if isinstance(holdout_n, bool) or not isinstance(holdout_n, int) or holdout_n < 1:
        raise ValueError("holdout_n must be a positive integer")
    if isinstance(treatment_n, bool) or not isinstance(treatment_n, int) or treatment_n < 1:
        raise ValueError("treatment_n must be a positive integer")
    if not math.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between 0 and 1")

    pooled_rate = (
        holdout_rate * holdout_n + treatment_rate * treatment_n
    ) / (holdout_n + treatment_n)
    null_se = math.sqrt(
        pooled_rate * (1.0 - pooled_rate) * (1.0 / holdout_n + 1.0 / treatment_n)
    )
    alternative_se = math.sqrt(
        holdout_rate * (1.0 - holdout_rate) / holdout_n
        + treatment_rate * (1.0 - treatment_rate) / treatment_n
    )
    effect = holdout_rate - treatment_rate

    if alternative_se == 0.0:
        return float(abs(effect) > 0.0)
    if null_se == 0.0:
        return 1.0 if effect != 0.0 else alpha

    critical_value = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    boundary = critical_value * null_se
    distribution = NormalDist(mu=effect, sigma=alternative_se)
    power = distribution.cdf(-boundary) + 1.0 - distribution.cdf(boundary)
    return min(1.0, max(0.0, power))


def plan_binary_experiment(
    holdout_rate: float,
    treatment_rate: float,
    holdout_share: float = 0.2,
    alpha: float = 0.05,
    target_power: float = 0.8,
    max_total_n: int = 10_000_000,
) -> PowerPlan:
    """Find the smallest planned sample reaching target approximate power.

    The treatment effect is supplied as an absolute change in breach rate.
    A zero effect has no finite sample size for target power above alpha.
    """
    holdout_rate = _probability(holdout_rate, "holdout_rate")
    treatment_rate = _probability(treatment_rate, "treatment_rate")
    if not math.isfinite(holdout_share) or not 0.0 < holdout_share < 1.0:
        raise ValueError("holdout_share must be between 0 and 1, exclusive")
    if not math.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between 0 and 1")
    if not math.isfinite(target_power) or not alpha < target_power < 1.0:
        raise ValueError("target_power must be greater than alpha and less than 1")
    if isinstance(max_total_n, bool) or not isinstance(max_total_n, int) or max_total_n < 2:
        raise ValueError("max_total_n must be an integer of at least 2")
    if holdout_rate == treatment_rate:
        raise ValueError("a non-zero treatment effect is required for finite sample size")

    def plan_at(total_n: int) -> PowerPlan:
        holdout_n, treatment_n = _arm_sizes(total_n, holdout_share)
        return PowerPlan(
            holdout_n=holdout_n,
            treatment_n=treatment_n,
            total_n=total_n,
            achieved_power=two_proportion_power(
                holdout_rate, treatment_rate, holdout_n, treatment_n, alpha
            ),
        )

    lower, upper = 2, 2
    while upper < max_total_n and plan_at(upper).achieved_power < target_power:
        lower = upper + 1
        upper = min(max_total_n, upper * 2)

    if plan_at(upper).achieved_power < target_power:
        raise ValueError(f"target power not reached by max_total_n={max_total_n}")

    while lower < upper:
        middle = (lower + upper) // 2
        if plan_at(middle).achieved_power >= target_power:
            upper = middle
        else:
            lower = middle + 1
    return plan_at(lower)
