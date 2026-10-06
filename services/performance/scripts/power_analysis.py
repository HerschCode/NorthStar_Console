"""Plan sample size for a randomized intervention with a binary breach outcome."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from src.roi.power import plan_binary_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--holdout-rate", type=float, required=True, help="Expected control/holdout breach rate"
    )
    parser.add_argument(
        "--treatment-rate", type=float, required=True, help="Expected treatment breach rate"
    )
    parser.add_argument("--holdout-share", type=float, default=0.2)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--power", type=float, default=0.8, dest="target_power")
    parser.add_argument("--max-total-n", type=int, default=10_000_000)
    args = parser.parse_args()

    try:
        plan = plan_binary_experiment(
            holdout_rate=args.holdout_rate,
            treatment_rate=args.treatment_rate,
            holdout_share=args.holdout_share,
            alpha=args.alpha,
            target_power=args.target_power,
            max_total_n=args.max_total_n,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({
        "method": "two-sided independent-proportions normal approximation",
        "assumptions": {
            "independent_units": True,
            "binary_outcome": "breached / not breached",
            "effect_scale": "absolute risk difference",
            "cluster_adjustment": False,
            "attrition_adjustment": False,
            "result_is_live_evidence": False,
        },
        "inputs": {
            "holdout_rate": args.holdout_rate,
            "treatment_rate": args.treatment_rate,
            "holdout_share": args.holdout_share,
            "alpha": args.alpha,
            "target_power": args.target_power,
        },
        "plan": asdict(plan),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
