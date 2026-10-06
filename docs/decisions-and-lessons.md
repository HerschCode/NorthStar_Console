# Decisions and lessons

Northstar treats corrected results as part of the engineering record. The
examples below link to the full methods, measurements, and remaining
limitations rather than presenting only the final headline.

## Model calibration must be out of sample

An isotonic calibrator was fitted on the same rows used to train its random
forest. The forest's in-sample scores were extreme, and the calibrator collapsed
the served model to a handful of distinct scores. On the configured test run,
the deployed artifact's ROC-AUC was 0.665 while metadata reported the raw
forest's 0.986. Calibration now uses a temporal holdout inside the training
window; metrics distinguish the served and raw model. The realistic-target
results and limits remain documented.

Details: [calibration bug, fix, and measurements](../services/performance/docs/calibration.md).

## Unmeasurable partial cases are not negative examples

Cases with zero measured duration were previously counted as non-breaches,
although no valid cycle-time outcome existed. Those rows are now excluded from
outcome-based model evaluation. See the P1
[development log](../services/performance/docs/development-log.md) and
[model evaluation notes](../services/performance/docs/evaluation.md).

## High base rates can make impressive metrics uninformative

The configured breach target is highly imbalanced toward breaches. High ROC-AUC
or PR-AUC on that target is not by itself evidence of useful early warning.
Northstar reports more realistic targets and the late-stage nature of its risk
features; it does not relabel the configured target as a deployment-quality
result.

Details: [evaluation and target limitations](../services/performance/docs/evaluation.md).

## RAG evaluation needs correctly ordered evidence pairs

An NLI scorer was initially called with answer and evidence in the wrong
premise/hypothesis order. That invalidated earlier NLI conclusions. The order
was corrected, the results rerun, and the old report retained as retracted
evidence. On the corrected 32-question evaluation, the deployed claim-support
gate remains a trade-off, not a guarantee; the optional polarity check lowers
some wrong-fact passes but also reduces correct-answer passes.

Details: [P2 gate calibration correction](../services/assistant/docs/gate-calibration.md)
and [polarity measurements](../services/assistant/reports/polarity_eval.json).

## Simulated intervention value is not measured causal impact

The public event-log intervention story has no real randomized intervention
outcomes. Ledger ROI is therefore labelled simulated. A deterministic holdout
assignment and an offline sample-size planner are available to prepare a future
trial, but neither is evidence that an action prevented a breach.

Details: [intervention and uplift methods](../services/performance/docs/uplift-method.md).

## Partial traces should disclose upstream failure safely

If P2 cannot return its trace spans, P3 keeps the gateway's own trace evidence
and logs a warning with the trace ID and status. It does not log upstream
response detail. The regression test covers both partial-response behavior and
log redaction:
[`test_trace_lookup_logs_p2_failure_and_keeps_gateway_evidence`](../services/gateway/tests/test_v1_gateway.py).

## Claims discipline

- Mark simulated, measured, and pending values distinctly.
- Keep old or retracted reports for audit, but label them as superseded.
- State dataset size, split method, base rates, and whether labels are
  independent alongside evaluation results.
- Treat cloud diagrams and Terraform as intended/prepared until deployment is
  applied and verified.
