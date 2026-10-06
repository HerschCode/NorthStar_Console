"""Train the early-warning GRU ensembles (p75 target) and export them to ONNX for the API.

For k in {2, 3, 5}: a 3-seed GRU ensemble (logits averaged INSIDE the exported graph) trained on the first 80% of
cases by start time, early-stopped on the last 15% of that window, Platt-scaled on that same validation slice, and
scored on the untouched last 20%. The metrics written to the metadata are measured by running the EXPORTED ONNX
file through onnxruntime -- i.e. they describe the artifact that is served, not the PyTorch model (lesson from the
2026-09-25 calibration bug, docs/calibration.md). PyTorch-vs-ONNX parity is recorded and asserted in tests.

Data comes from the same tables the API reads (analytics.process_cases, staging.events), events ordered by
(timestamp, activity) so ties are deterministic in training and serving.

Requires torch (requirements-sequence.txt). Serving needs only onnxruntime.
Run: python -m scripts.export_early_risk  -> models/early_risk/{gru_k*.onnx, early_risk_meta.json}
"""
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from dotenv import load_dotenv
from sklearn.metrics import brier_score_loss, roc_auc_score
from torch import nn

from scripts.target_sensitivity import targets_from_training_percentile
from src.api.db import load_cases, load_events
from src.ml import early_risk as er
from src.ml.calibration import _fit_platt
from src.ml.sequence_model import ActivityVocab, SequenceRiskModel, fit

warnings.filterwarnings("ignore")
KS = (2, 3, 5)
SEEDS = (0, 1, 2)
PERCENTILE = 75
OUT_DIR = er.MODEL_DIR


class LogitEnsemble(nn.Module):
    """Mean of member logits -- one ONNX graph per k regardless of the number of seeds."""

    def __init__(self, members):
        super().__init__()
        self.members = nn.ModuleList(members)

    def forward(self, acts, step_feats, static):
        return torch.stack([m(acts, step_feats, static) for m in self.members]).mean(0)


def build_rows(cases, events_by_case, targets, k):
    """Eligible case positions and their model inputs for prefix length k."""
    keep, acts_l, offs_l = [], [], []
    for pos, (cid, cat) in enumerate(zip(cases["case_id"], cases["category"])):
        ev = events_by_case.get(cid)
        if ev is None or len(ev[0]) < k:
            continue
        offs = (ev[1][:k] - ev[1][0]) / np.timedelta64(1, "h")
        if offs[-1] > er.target_hours(targets, cat):
            continue
        keep.append(pos)
        acts_l.append(ev[0][:k])
        offs_l.append(offs)
    return np.array(keep), acts_l, offs_l


def to_tensors(cases, keep_pos, acts_l, offs_l, hist, meta, k):
    A = np.zeros((len(keep_pos), k), dtype=np.int64)
    F = np.zeros((len(keep_pos), k, 2), dtype=np.float32)
    S = np.zeros((len(keep_pos), len(meta["static_columns"])), dtype=np.float32)
    for i, pos in enumerate(keep_pos):
        A[i], F[i] = er.sequence_arrays(acts_l[i], offs_l[i], meta["vocab"], k)
        row = cases.iloc[pos]
        S[i] = er.static_vector(meta, row["start_time"], row["category"], hist.iloc[pos])
    return torch.from_numpy(A), torch.from_numpy(F), torch.from_numpy(S)


def bootstrap_ci(y, p, n=500, seed=0):
    rng = np.random.default_rng(seed)
    v = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) == 2:
            v.append(roc_auc_score(y[i], p[i]))
    return [float(x) for x in np.percentile(v, [2.5, 97.5])]


def main():
    load_dotenv()
    torch.set_num_threads(1)
    cases = load_cases()
    cases = cases[cases["cycle_time_hours"].astype(float) > 0].sort_values("start_time").reset_index(drop=True)  # measurable only
    events = load_events()
    events = events.sort_values(["case_id", "timestamp", "activity"], kind="stable")
    events_by_case = {cid: (g["activity"].tolist(), g["timestamp"].values) for cid, g in events.groupby("case_id")}
    n, cut = len(cases), int(len(cases) * 0.8)

    targets = targets_from_training_percentile(cases, PERCENTILE)
    breach = er.breach_flags(cases, targets)
    prior = float(breach.iloc[:cut].mean())
    hist = er.supplier_history(cases, targets, prior)
    train_acts = {a for cid in cases["case_id"].iloc[:cut] if cid in events_by_case for a in events_by_case[cid][0]}
    vocab = ActivityVocab(train_acts).index
    categories = sorted(cases["category"].iloc[:cut].fillna("UNKNOWN").astype(str).unique())
    meta = {"vocab": vocab, "categories": categories,
            "static_columns": ["hour", "dow", "month", "supplier_hist"] + [f"cat_{c}" for c in categories],
            "target": {"percentile": PERCENTILE, "targets_hours": targets, "prior_breach_rate": prior,
                       "definition": "cycle time above the training-window per-category p75"},
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "train_window_end": str(cases["start_time"].iloc[cut - 1]), "models": {}}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    import onnxruntime as ort

    for k in KS:
        keep, acts_l, offs_l = build_rows(cases, events_by_case, targets, k)
        y = torch.tensor(breach.values[keep], dtype=torch.float32)
        is_tr = keep < cut
        idx_tr, idx_te = np.where(is_tr)[0], np.where(~is_tr)[0]
        inner = int(len(idx_tr) * 0.85)
        idx_fit, idx_val = idx_tr[:inner], idx_tr[inner:]
        A, F, S = to_tensors(cases, keep, acts_l, offs_l, hist, meta, k)
        sub = lambda ix: (A[ix], F[ix], S[ix], y[ix])
        members = []
        for seed in SEEDS:
            torch.manual_seed(seed)
            m = fit(SequenceRiskModel(len(ActivityVocab(train_acts)), S.shape[1], "gru"), sub(idx_fit), sub(idx_val), seed=seed)
            members.append(m.eval())
        ens = LogitEnsemble(members).eval()
        path = OUT_DIR / f"gru_k{k}.onnx"
        torch.onnx.export(ens, (A[:1], F[:1], S[:1]), str(path), input_names=["acts", "step_feats", "static"],
                          output_names=["logit"], opset_version=17,
                          dynamic_axes={"acts": {0: "batch"}, "step_feats": {0: "batch"}, "static": {0: "batch"},
                                        "logit": {0: "batch"}})
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        run = lambda ix: sess.run(None, {"acts": A[ix].numpy(), "step_feats": F[ix].numpy(), "static": S[ix].numpy()})[0]
        with torch.no_grad():
            parity = float(np.max(np.abs(run(idx_te) - ens(A[idx_te], F[idx_te], S[idx_te]).numpy())))
        a, b = _fit_platt(run(idx_val), y[idx_val].numpy().astype(int))
        z_te, y_te = run(idx_te), y[idx_te].numpy().astype(int)
        p_te = 1.0 / (1.0 + np.exp(-(a * z_te + b)))
        meta["models"][str(k)] = {
            "file": path.name, "platt_a": a, "platt_b": b, "seeds": list(SEEDS),
            "n_train": int(len(idx_fit)), "n_validation": int(len(idx_val)), "n_test": int(len(idx_te)),
            "base_rate": float(y_te.mean()), "test_roc_auc": float(roc_auc_score(y_te, z_te)),
            "test_roc_auc_ci95": bootstrap_ci(y_te, z_te), "test_brier": float(brier_score_loss(y_te, p_te)),
            "onnx_vs_torch_max_abs_logit_diff": parity,
        }
        print(f"k={k}: n_test={len(idx_te)} base={y_te.mean():.3f} ROC-AUC (ONNX) {meta['models'][str(k)]['test_roc_auc']:.3f} "
              f"CI {meta['models'][str(k)]['test_roc_auc_ci95']} Brier {meta['models'][str(k)]['test_brier']:.3f} "
              f"parity {parity:.2e}", flush=True)
    (OUT_DIR / er.META_NAME).write_text(json.dumps(meta, indent=2))
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
