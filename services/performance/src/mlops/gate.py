"""Champion/challenger promotion gate. Pure function of two metric blocks plus a config, so CI, the retrain cycle and a notebook all
make the same decision and the reasons are readable."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "model_gate.yaml"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


@dataclass
class GateDecision:
    passed: bool
    needs_review: bool = False
    reasons: list[str] = field(default_factory=list)   # why it failed or needs review
    checks: list[str] = field(default_factory=list)    # what passed

    def summary(self) -> str:
        head = "PROMOTE" if self.passed else ("REVIEW" if self.needs_review else "REJECT")
        return head + ": " + "; ".join(self.reasons or self.checks)


def evaluate_gate(candidate: dict, champion: dict | None, cfg: dict | None = None) -> GateDecision:
    cfg = cfg or load_config()
    fail: list[str] = []
    ok: list[str] = []
    review: list[str] = []

    if candidate.get("n_test", 0) < cfg["min_test_rows"]:
        fail.append(f"only {candidate.get('n_test', 0)} held-out rows (< {cfg['min_test_rows']})")
    lo, hi = cfg["degenerate_base_rate"]
    br = candidate.get("base_rate")
    if br is not None and not (lo <= br <= hi):
        fail.append(f"target base rate {br:.1%} is outside {lo:.0%}-{hi:.0%}: metrics are not informative")
    if candidate.get("n_unique_scores", 10**9) < cfg["min_unique_scores"]:
        fail.append(f"only {candidate.get('n_unique_scores')} distinct scores (< {cfg['min_unique_scores']})")
    if candidate.get("ece", 0.0) > cfg["max_ece_absolute"]:
        fail.append(f"calibration error {candidate['ece']:.3f} exceeds the absolute cap {cfg['max_ece_absolute']}")

    if champion is None:
        if candidate["roc_auc"] < cfg["first_model_min_auc"]:
            fail.append(f"first model ROC-AUC {candidate['roc_auc']:.3f} is below the floor {cfg['first_model_min_auc']}")
        else:
            ok.append(f"no champion yet and ROC-AUC {candidate['roc_auc']:.3f} clears the floor")
    else:
        gain = candidate["roc_auc"] - champion["roc_auc"]
        if cfg["min_auc_gain"] > 0:
            (ok if gain >= cfg["min_auc_gain"] else fail).append(f"ROC-AUC change {gain:+.4f} (needs >= {cfg['min_auc_gain']})")
        else:
            (ok if gain >= -cfg["max_auc_drop"] else fail).append(f"ROC-AUC change {gain:+.4f} (may drop at most {cfg['max_auc_drop']})")
        if gain > cfg["suspicious_auc_jump"]:
            review.append(f"ROC-AUC jumped {gain:+.3f}: check for leakage or a changed label before promoting")
        db = candidate["brier"] - champion["brier"]
        (ok if db <= cfg["max_brier_worsening"] else fail).append(f"Brier change {db:+.4f} (may worsen at most {cfg['max_brier_worsening']})")
        de = candidate["ece"] - champion["ece"]
        (ok if de <= cfg["max_ece_worsening"] else fail).append(f"ECE change {de:+.4f} (may worsen at most {cfg['max_ece_worsening']})")

    if fail:
        return GateDecision(False, False, fail + review, ok)
    if review:
        return GateDecision(False, True, review, ok)
    return GateDecision(True, False, [], ok)
