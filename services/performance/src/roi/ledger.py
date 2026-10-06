"""Phase 6: intervention ledger validation and the ROI arithmetic behind GET /roi/summary.

Everything here is pure (no DB) so it is unit-testable; src/api/routes.py and
scripts/simulate_interventions.py do the I/O. The policy in config/interventions.yaml is a set of
ASSUMPTIONS -- see docs/uplift-method.md for why this is a simulation, not measured uplift.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "interventions.yaml"
SIMULATION_LABEL = ("SIMULATION: effect sizes and costs are assumptions from config/interventions.yaml, "
                    "not measured uplift. The separate `experiment` block reports measured uplift, but only once randomized "
                    "holdout outcomes exist.")


class InterventionValidationError(ValueError):
    pass


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


def validate_intervention(payload: dict, policy: dict) -> dict:
    """Returns the normalised row to insert or raises InterventionValidationError."""
    types = policy["types"]
    itype = payload.get("intervention_type") or policy["default_type"]
    if itype not in types:
        raise InterventionValidationError(f"unknown intervention_type '{itype}'; valid: {sorted(types)}")
    if not str(payload.get("case_id") or "").strip():
        raise InterventionValidationError("case_id is required")
    risk = payload.get("risk_at_intervention")
    if risk is None or isinstance(risk, bool) or not 0 <= float(risk) <= 1:
        raise InterventionValidationError("risk_at_intervention must be a number between 0 and 1")
    cost = types[itype]["cost"] if payload.get("cost") is None else payload["cost"]
    if isinstance(cost, bool) or float(cost) < 0:
        raise InterventionValidationError("cost must be >= 0")
    return {"case_id": str(payload["case_id"]).strip(), "intervention_type": itype,
            "risk_at_intervention": float(risk), "cost": float(cost),
            "breached_after": payload.get("breached_after"), "notes": payload.get("notes")}


def assign_case(case_id: str, policy: dict) -> tuple[str, str]:
    """(assignment, experiment_id) for a case under the configured randomized experiment."""
    from src.roi.randomizer import assign

    exp = policy["experiment"]
    return assign(case_id, exp["id"], exp["holdout_share"]), exp["id"]


def _arm_stats(rows: list[dict]) -> dict:
    known = [r for r in rows if r.get("breached_after") is not None]
    n_b = sum(1 for r in known if r["breached_after"])
    return {"cases": len(rows), "outcomes_known": len(known),
            "breach_rate": round(n_b / len(known), 4) if known else None}


def experiment_summary(rows: list[dict], policy: dict) -> dict:
    """Measured uplift from the randomized arms: breach rate of holdout minus breach rate of treated (a
    positive number = the action reduced breaches), with a 95% CI. Only reported with >= 30 known outcomes in
    each arm; before that the honest answer is 'not enough data'. Rows without an experiment_id (simulated
    replays, pre-experiment rows) are excluded -- they were not randomized."""
    exp = policy.get("experiment")
    if not exp:
        return None
    mine = [r for r in rows if r.get("experiment_id") == exp["id"] and not r.get("is_simulated")]
    treat = [r for r in mine if r.get("assignment", "treat") == "treat"]
    hold = [r for r in mine if r.get("assignment") == "holdout"]
    t, h = _arm_stats(treat), _arm_stats(hold)
    out = {"experiment_id": exp["id"], "holdout_share": exp["holdout_share"], "treated": t, "holdout": h,
           "measured_uplift": None, "note": "Randomized experiment; needs >= 30 known outcomes per arm."}
    if t["outcomes_known"] >= 30 and h["outcomes_known"] >= 30:
        import math

        n_t, n_h = t["outcomes_known"], h["outcomes_known"]
        d = h["breach_rate"] - t["breach_rate"]
        se = math.sqrt(t["breach_rate"] * (1 - t["breach_rate"]) / n_t + h["breach_rate"] * (1 - h["breach_rate"]) / n_h)
        out["measured_uplift"] = {"breach_reduction": round(d, 4), "ci95": [round(d - 1.96 * se, 4), round(d + 1.96 * se, 4)]}
    return out


def _bucket(rows: list[dict], policy: dict) -> dict:
    """ROI for one group of ledger rows. Avoided breaches use the policy's assumed relative effect:
    by model risk (sum of risk * effect) and, where outcomes are known, by observed breaches
    (count of breached treated cases * effect -- the counterfactual is still assumed)."""
    n = len(rows)
    cost = sum(float(r["cost"]) for r in rows)
    by_risk = sum(float(r["risk_at_intervention"]) * policy["types"].get(r["intervention_type"], {"effect": 0})["effect"]
                  for r in rows)
    known = [r for r in rows if r.get("breached_after") is not None]
    by_outcome = sum(policy["types"].get(r["intervention_type"], {"effect": 0})["effect"]
                     for r in known if r["breached_after"])
    value = lambda avoided: avoided * policy["breach_cost"]
    return {
        "interventions": n, "total_cost": round(cost, 2),
        "avoided_breaches_by_model_risk": round(by_risk, 2),
        "net_value_by_model_risk": round(value(by_risk) - cost, 2),
        "outcomes_known": len(known),
        "avoided_breaches_by_observed_outcomes": round(by_outcome, 2),
        "net_value_by_observed_outcomes": round(value(by_outcome) - sum(float(r["cost"]) for r in known), 2),
    }


def break_even_effect(rows: list[dict], policy: dict) -> float | None:
    """Uniform relative effect at which model-risk net value is zero: cost / (breach_cost * sum(risk))."""
    total_risk = sum(float(r["risk_at_intervention"]) for r in rows)
    if not rows or total_risk == 0:
        return None
    return round(sum(float(r["cost"]) for r in rows) / (policy["breach_cost"] * total_risk), 4)


def roi_summary(rows: list[dict], policy: dict) -> dict:
    # holdout rows are cases deliberately NOT treated: they carry no cost or benefit, only outcomes
    treated = [r for r in rows if r.get("assignment", "treat") == "treat"]
    sim = [r for r in treated if r.get("is_simulated")]
    real = [r for r in treated if not r.get("is_simulated")]
    return {
        "label": SIMULATION_LABEL,
        "assumptions": {"breach_cost": policy["breach_cost"], "capacity_pct": policy["capacity_pct"],
                        "types": policy["types"]},
        "simulated": _bucket(sim, policy),
        "logged": _bucket(real, policy),
        "break_even_effect_simulated": break_even_effect(sim, policy),
        "experiment": experiment_summary(rows, policy),
    }


SENSITIVITY_PATH = Path(__file__).resolve().parents[2] / "reports" / "roi_sensitivity.json"


def load_sensitivity(path: str | Path = SENSITIVITY_PATH) -> dict | None:
    """The committed output of scripts/roi_sensitivity.py (treated share x effect x breach cost grid,
    model vs random vs rule-based targeting), or None if it has not been generated. Two scenarios are
    always both present so the degenerate configured target is never shown without the p75 one."""
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    # the *_uncalibrated scenarios keep only their summary here (full grids stay in the JSON file)
    scenarios = {k: ({kk: vv for kk, vv in v.items() if kk != "grid"} if k.endswith("_uncalibrated") else v)
                 for k, v in data["scenarios"].items()}
    return {"label": data["label"], "cost_per_treatment": data["cost_per_treatment"],
            "strategies_not_run": data["strategies_not_run"], "strategies_history": data.get("strategies_history", {}),
            "scenarios": scenarios, "regenerate_with": "python -m scripts.roi_sensitivity"}
