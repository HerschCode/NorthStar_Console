# Early-warning endpoint: `GET /orders/{case_id}/early-risk?k=`

The GRU beat the random forest at the p75 target for k >= 2 ([`sequence-model.md`](sequence-model.md)), so it is now
served, as an **early-warning score with its own accuracy printed on every response**. It does not replace
`/orders/{id}/risk`, which is the late-stage triage score (0.84-0.88 ROC-AUC on realistic targets).

## What it does
Scores a case as of its k-th event (k = 2, 3 or 5) using the first k activities, elapsed and inter-event times,
start hour/day/month, category and a strictly causal supplier history. It predicts **breach of the training-window
per-category p75 cycle-time target** (~27% base rate), not the configured SLA (97% breach, which makes early
prediction meaningless). It refuses (HTTP 409) a case with fewer than k events or one already older than its target
at event k, because that case's label is already determined. Response fields include `breach_probability`
(Platt-calibrated), `test_roc_auc` with a 95% CI, `base_rate`, `n_test` and a `warning` string. Unknown k -> 422,
unknown case -> 404, model files missing -> 503.

## Served artifacts (`models/early_risk/`)
`gru_k{2,3,5}.onnx` (98 KB each) + `early_risk_meta.json` (vocabulary, category list, p75 targets, Platt scaling,
metrics). Each ONNX graph contains a 3-seed GRU ensemble that averages logits internally. Regenerate with
`python -m scripts.export_early_risk` (needs torch and onnx via `requirements-sequence.txt`).

**torch is not a serving dependency.** `requirements.txt` gains only `onnxruntime`; `tests/test_early_risk.py`
runs the app in a fresh interpreter and asserts torch is never imported. Featurization (`src/ml/early_risk.py`) is
numpy-only and is the same code the export script uses for training, checked against the torch-side research code.

## Measured on the served artifact (held-out last 20% by start time)
| k | Test n | Base rate | ROC-AUC [95% CI] | Brier (calibrated) |
|---|---|---|---|---|
| 2 | 580 | 0.250 | 0.673 [0.625, 0.714] | 0.190 |
| 3 | 571 | 0.245 | 0.729 [0.680, 0.772] | 0.174 |
| 5 | 563 | 0.236 | 0.765 [0.718, 0.805] | 0.154 |

*Regenerated 2026-09-28 after excluding 104 zero-duration cases from the labels (was 0.759 / 0.759 / 0.787 on the uncorrected labels, with a different train/test split). k = 2 is now barely above creation-time features.*

Metrics come from running the exported ONNX file through onnxruntime, not from the PyTorch model (the lesson of the
calibration bug in [`calibration.md`](calibration.md)). ONNX-vs-PyTorch logits agree to < 1e-6 (test asserts < 1e-5).
Platt scaling is fitted on the same validation slice used for early stopping, so the Brier scores are slightly
optimistic; Brier is well below the research run's uncalibrated 0.22-0.28.

## Differences from the research script, and limits
- Data comes from the tables the API reads (`staging.events`, `analytics.process_cases`) and events are ordered by
  (timestamp, activity) so ties are deterministic; the research script read the CSV. Numbers therefore differ a
  little (ensemble 0.76/0.76/0.79 here vs seed-mean 0.76/0.74/0.76 there); the ensemble is also better than any
  single seed, so do not read the gain as a new result.
- Supplier history uses the training-window breach rate as its prior (the research script used the full-data rate).
- Weak signal: ROC-AUC 0.76-0.79 with intervals of about +-0.04, one split, no tuning. A case scored at k=3 is
  scored as of its third event using what is known then; replayed historical cases are scored the same way, so this
  is a simulation of early scoring rather than a live stream.
- Cases with the k-th event more than a few thousand hours in are still eligible if under their category's p75
  target; the target hours are returned so the caller can see what "breach" means for that case.
