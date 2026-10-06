"""One turn of the MLOps loop: drift/schedule check -> train a challenger -> register -> gate against the champion -> promote or reject
-> record everything. `rollback` undoes a promotion. Training and shadow scoring are injected, so the loop is testable without a database."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import numpy as np

from src.ml.retrain_trigger import check_retrain_needed
from .gate import GateDecision, evaluate_gate, load_config
from .registry import ModelRegistry

# train_fn() -> (artifact_path, metrics_dict, meta_dict, data_fingerprint)
TrainFn = Callable[[], tuple[str, dict, dict, str]]


@dataclass
class CycleResult:
    action: str                       # skipped | promoted | rejected | needs_review
    reasons: list[str] = field(default_factory=list)
    candidate: dict | None = None
    champion: dict | None = None
    decision: GateDecision | None = None
    shadow: dict | None = None


def shadow_compare(champion_scores, challenger_scores, top_share: float = 0.3) -> dict:
    """How differently the two models rank the same recent cases: Spearman-style rank correlation and top-k overlap.
    Low overlap on live-like traffic is a reason to look before promoting even when held-out metrics pass."""
    a, b = np.asarray(champion_scores, float), np.asarray(challenger_scores, float)
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    rho = float(np.corrcoef(ra, rb)[0, 1]) if len(a) > 1 and a.std() > 0 and b.std() > 0 else float("nan")
    k = max(1, int(len(a) * top_share))
    overlap = len(set(np.argsort(-a)[:k]) & set(np.argsort(-b)[:k])) / k
    return {"rank_correlation": rho, "top_k_overlap": float(overlap), "n": int(len(a)), "top_share": top_share}


def run_cycle(registry: ModelRegistry, name: str, train_fn: TrainFn, *, current_case_count: int, feature_drift=None,
              force: bool = False, gate_cfg: dict | None = None, now: datetime | None = None,
              shadow_fn: Callable[[str, str], dict] | None = None) -> CycleResult:
    champion = registry.resolve(name, "champion")
    trigger_reasons: list[str] = ["forced"] if force else []
    if champion is None:
        trigger_reasons.append("no champion registered")
    elif not force:
        rec = check_retrain_needed(current_case_count, meta_path=registry.meta_path(name, champion["version"]),
                                   feature_drift=feature_drift, now=now)
        trigger_reasons += rec.reasons
    if not trigger_reasons:
        registry.log("cycle_skipped", name=name, champion=champion["version"] if champion else None)
        return CycleResult("skipped", ["no retrain trigger fired"], champion=champion)

    artifact, metrics, meta, fingerprint = train_fn()
    cand = registry.register(name, artifact, metrics, meta, fingerprint, notes="; ".join(trigger_reasons))
    registry.set_alias(name, "challenger", cand["version"], reason="; ".join(trigger_reasons))

    shadow = None
    if shadow_fn and champion:
        shadow = shadow_compare_from(shadow_fn, registry, name, champion, cand)
    decision = evaluate_gate(metrics, champion["metrics"] if champion else None, gate_cfg or load_config())
    registry.log("gate_decision", name=name, version=cand["version"], passed=decision.passed,
                 needs_review=decision.needs_review, summary=decision.summary(), shadow=shadow)
    if decision.passed:
        registry.set_alias(name, "champion", cand["version"], reason="gate passed: " + "; ".join(decision.checks))
        return CycleResult("promoted", trigger_reasons, cand, champion, decision, shadow)
    return CycleResult("needs_review" if decision.needs_review else "rejected", decision.reasons, cand, champion, decision, shadow)


def shadow_compare_from(shadow_fn, registry, name, champion, cand) -> dict:
    return shadow_fn(str(registry.artifact_path(name, champion["version"])), str(registry.artifact_path(name, cand["version"])))


def rollback(registry: ModelRegistry, name: str, reason: str) -> dict:
    return registry.rollback(name, "champion", reason)
