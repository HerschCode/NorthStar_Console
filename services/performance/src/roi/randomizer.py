"""Deterministic treat / holdout assignment for the intervention experiment.

assign(case_id, experiment_id, holdout_share) hashes "experiment_id:case_id" with SHA-256 and maps the first 8
bytes to a uniform number in [0, 1); the case is holdout when that number is below holdout_share.

Properties that matter for causal inference (asserted in tests/test_randomizer.py):
- deterministic: the same case in the same experiment always gets the same arm, so a retry of POST /interventions
  can never flip a case from holdout to treat;
- independent of anything about the case (risk, supplier, ...) -- only the id and the experiment id enter, so
  assignment is unrelated to outcomes and a difference between arms is attributable to the treatment;
- balanced: over many ids the holdout fraction is close to holdout_share;
- a new experiment id re-randomizes (assignments across experiments are uncorrelated).
"""
from __future__ import annotations

import hashlib

TREAT = "treat"
HOLDOUT = "holdout"


def uniform_draw(case_id: str, experiment_id: str) -> float:
    digest = hashlib.sha256(f"{experiment_id}:{case_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def assign(case_id: str, experiment_id: str, holdout_share: float) -> str:
    if not 0.0 <= holdout_share <= 1.0:
        raise ValueError("holdout_share must be between 0 and 1")
    return HOLDOUT if uniform_draw(case_id, experiment_id) < holdout_share else TREAT
