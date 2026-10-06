"""MLOps loop CLI.   python -m scripts.mlops_cycle demo | status | card <version> | rollback <reason>

`demo` runs the whole loop on synthetic data in a throw-away registry: first model promoted, no-op while stable, drift fires, a worse
challenger is rejected, a better one promoted, then rolled back. It touches no database and is the same code the real cycle uses."""
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier

from src.ml.feature_drift import compute_baselines, compute_feature_drift
from src.mlops import metrics as M
from src.mlops.cycle import rollback, run_cycle
from src.mlops.gate import load_config
from src.mlops.modelcard import render
from src.mlops.registry import ModelRegistry


def _synth(n, shift, seed):
    r = np.random.default_rng(seed)
    X = pd.DataFrame({"a": r.normal(shift, 1, n), "b": r.normal(shift, 1, n), "c": r.integers(0, 5, n)})
    return X, (X["a"] - shift + 0.8 * (X["b"] - shift) + r.normal(0, 1, n) > 0.2).astype(int)


def _trainer(tmp: Path, shift: float, seed: int, noise: bool = False):
    def fn():
        X, y = _synth(2000, shift, seed); Xt, yt = _synth(1000, shift, seed + 100)
        if noise:
            y = pd.Series(np.random.default_rng(seed).integers(0, 2, len(y)))
        model = GradientBoostingClassifier(random_state=0).fit(X, y)
        path = tmp / f"model_{seed}.joblib"; joblib.dump(model, path)
        meta = {"trained_at": datetime.now(timezone.utc).isoformat(), "train_row_count": len(X), "test_row_count": len(Xt),
                "feature_baselines": compute_baselines(X)}
        return str(path), M.evaluate(yt, model.predict_proba(Xt)[:, 1], n_boot=100), meta, f"synthetic-seed{seed}"
    return fn


def demo(root: Path) -> None:
    reg = ModelRegistry(root / "registry"); work = root / "work"; work.mkdir(exist_ok=True)
    step = lambda title, r: print(f"{title:<44} -> {r.action:<12} {'; '.join(r.reasons)[:110]}")
    step("1 first model", run_cycle(reg, "sla", _trainer(work, 0, 1), current_case_count=2000))
    step("2 nothing changed", run_cycle(reg, "sla", _trainer(work, 0, 2), current_case_count=2000))
    X0, _ = _synth(2000, 0, 1); Xd, _ = _synth(500, 3, 7)
    drift = compute_feature_drift(compute_baselines(X0), Xd)
    print(f"   drift report: {drift.status}, alert features: {drift.alert_features}")
    step("3 drift, challenger trained on noise", run_cycle(reg, "sla", _trainer(work, 3, 3, noise=True), current_case_count=2000, feature_drift=drift))
    cfg = {**load_config(), "min_auc_gain": 0.0, "max_auc_drop": 0.05}
    step("4 drift, challenger trained on new data", run_cycle(reg, "sla", _trainer(work, 3, 4), current_case_count=2000, feature_drift=drift, gate_cfg=cfg))
    print("   champion now:", reg.resolve("sla")["version"])
    print("5 rollback ->", rollback(reg, "sla", "demo: production metric regression")["version"])
    print("\n" + render(reg, "sla", reg.resolve("sla")["version"]))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["demo", "status", "card", "rollback"]); ap.add_argument("arg", nargs="?")
    ap.add_argument("--registry", default="models/registry"); ap.add_argument("--name", default="sla_risk")
    a = ap.parse_args()
    if a.cmd == "demo":
        demo(Path(tempfile.mkdtemp(prefix="mlops-demo-")))
        return
    reg = ModelRegistry(a.registry)
    if a.cmd == "status":
        print(json.dumps({"versions": reg.versions(a.name), "champion": reg.resolve(a.name), "challenger": reg.resolve(a.name, "challenger")}, indent=2, default=str))
    elif a.cmd == "card":
        print(render(reg, a.name, a.arg or reg.resolve(a.name)["version"]))
    else:
        print(json.dumps(rollback(reg, a.name, a.arg or "manual"), indent=2))


if __name__ == "__main__":
    main()
