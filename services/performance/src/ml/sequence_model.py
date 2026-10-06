"""PLAN.md Phase 5: a small GRU/LSTM over the first k events of a case, to compare against the
prefix random forest on the SAME rows (scripts/sequence_model_bpi2019.py).

Input per case: the first k activities (embedded) + per-step log(1 + hours since case start) and
log(1 + hours since previous event); static features (category, start hour/day/month, causal
supplier history) are concatenated to the final hidden state. Not deployed -- see
docs/sequence-model.md for whether it earns that.
"""
from __future__ import annotations

import time

import numpy as np
import torch
from torch import nn

PAD = 0


class ActivityVocab:
    """Activity -> integer id from TRAINING cases only; unseen activities map to UNK (1)."""

    def __init__(self, activities):
        self.index = {a: i + 2 for i, a in enumerate(sorted(set(activities)))}

    def __len__(self):
        return len(self.index) + 2

    def encode(self, seq):
        return [self.index.get(a, 1) for a in seq]


def make_sequence_tensors(prefixes, times, vocab, k):
    """prefixes: list of activity lists (len >= k, truncated to k); times: list of hour-offset
    arrays (hours since case start, same length). Returns (act_ids [n,k], step_feats [n,k,2])."""
    n = len(prefixes)
    acts = np.zeros((n, k), dtype=np.int64)
    feats = np.zeros((n, k, 2), dtype=np.float32)
    for i, (a, t) in enumerate(zip(prefixes, times)):
        a, t = a[:k], np.asarray(t[:k], dtype=float)
        acts[i] = vocab.encode(a)
        feats[i, :, 0] = np.log1p(np.clip(t, 0, None))
        feats[i, 1:, 1] = np.log1p(np.clip(np.diff(t), 0, None))
    return torch.from_numpy(acts), torch.from_numpy(feats)


class SequenceRiskModel(nn.Module):
    def __init__(self, vocab_size, static_dim, cell="gru", emb_dim=16, hidden=32, dropout=0.2):
        super().__init__()
        rnn = {"gru": nn.GRU, "lstm": nn.LSTM}[cell]
        self.cell = cell
        self.emb = nn.Embedding(vocab_size, emb_dim, padding_idx=PAD)
        self.rnn = rnn(emb_dim + 2, hidden, batch_first=True)
        self.head = nn.Sequential(
            nn.Linear(hidden + static_dim, 32), nn.ReLU(), nn.Dropout(dropout), nn.Linear(32, 1))

    def forward(self, acts, step_feats, static):
        out, _ = self.rnn(torch.cat([self.emb(acts), step_feats], dim=-1))
        return self.head(torch.cat([out[:, -1], static], dim=-1)).squeeze(-1)


def fit(model, train, val, epochs=40, lr=3e-3, batch_size=128, patience=6, seed=0):
    """train/val = (acts, feats, static, y) tensors. Early stopping on validation loss; restores
    the best weights. Class imbalance handled with pos_weight from the training labels."""
    torch.manual_seed(seed)
    ya = train[3]
    pos_weight = ((1 - ya).sum() / ya.sum().clamp(min=1)).clamp(0.2, 5.0)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    n = len(ya)
    best, best_state, bad = float("inf"), None, 0
    for _ in range(epochs):
        model.train()
        perm = torch.randperm(n)
        for s in range(0, n, batch_size):
            b = perm[s:s + batch_size]
            opt.zero_grad()
            loss_fn(model(train[0][b], train[1][b], train[2][b]), ya[b]).backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            v = loss_fn(model(val[0], val[1], val[2]), val[3]).item()
        if v < best - 1e-4:
            best, bad = v, 0
            best_state = {k_: t.clone() for k_, t in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    if best_state:
        model.load_state_dict(best_state)
    return model


def predict_proba(model, acts, feats, static):
    model.eval()
    with torch.no_grad():
        return torch.sigmoid(model(acts, feats, static)).numpy()


def cpu_latency_ms(predict_one, n_calls=200, warmup=20):
    """Median and p95 wall time (ms) of predict_one() -- a zero-arg callable scoring ONE case."""
    for _ in range(warmup):
        predict_one()
    ts = []
    for _ in range(n_calls):
        t0 = time.perf_counter()
        predict_one()
        ts.append((time.perf_counter() - t0) * 1000)
    return float(np.median(ts)), float(np.percentile(ts, 95))
