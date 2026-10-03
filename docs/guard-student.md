# Distilled ONNX prompt-injection guard

The student guard is an **experimental, opt-in** classifier backend. It is not
the default gateway detector and it does not replace the rules, response
checks, or action firewall. Enable it only after measuring it on the traffic
and latency budget relevant to your deployment.

## Run it

Install the optional ONNX Runtime dependency and select the student backend:

```bash
pip install -e ".[student]"
# macOS / Linux
CLASSIFIER_BACKEND=student uvicorn gateway.app:app --port 8000
```

In PowerShell, set `$env:CLASSIFIER_BACKEND = "student"` before running
`uvicorn gateway.app:app --port 8000`.

The checked-in model, tokenizer and metadata live under
[`models/guard_student/`](../models/guard_student/); their hashes are covered by
[`models/MANIFEST.sha256`](../models/MANIFEST.sha256). The app's default
backend remains unchanged. The model can also be selected as part of the
`both` ensemble; that runs both the existing NumPy classifier and the student.

The runtime uses ONNX Runtime plus a small pure-Python WordPiece implementation
and loads the packaged Hugging Face `tokenizer.json`. The JSON loader rejects
unsupported tokenizer configurations instead of silently approximating them.
It expects a BERT uncased normalizer, BERT pre-tokenizer, and WordPiece model.
As with the existing implementation, literal text resembling special tokens
is tokenized as ordinary text rather than injected as model structure.

## Model and quantization

The student is `sentence-transformers/all-MiniLM-L6-v2`, trained with hard
teacher knowledge distillation from
`protectai/deberta-v3-base-prompt-injection-v2`. The recorded run used 8,178
labeled rows, 3,467 transfer rows, four epochs, batch size 32, seed 0, and a
256-token input limit. Its validation operating point was selected before
held-out evaluation at no more than 1% validation false positives.

The shipped variant is `int8-matmul-gather-perchannel`. The recorded model-only
comparison reports validation AUC 0.9901 for int8 versus 0.9898 for fp32,
99.67% decision agreement at the fp32 threshold, isolated-process peak RSS of
96.8 MB, and p50/p95 inference latency of 5/26 ms. These are **model-only**
measurements, not full-service or container measurements. See
[`reports/p3_guard_student_onnx.json`](../reports/p3_guard_student_onnx.json)
and the run metadata in [`models/guard_student/meta.json`](../models/guard_student/meta.json).

## Gateway evaluation

The gateway evaluation runs each detector through the gateway's pre-flight
path on the held-out sets listed in
[`reports/p3_guard_student_gateway.json`](../reports/p3_guard_student_gateway.json).
Detection and false-positive rates are macro-averaged over the sets with
attacks and benign examples respectively; this gives each set equal weight,
not each row. It is not a single pooled accuracy score. Per-set counts and
Wilson 95% intervals are in the report. Several source-provided test splits
are in-distribution for the shipped classifier and the student, so the macro
results should not be read as performance on wholly independent deployments.

Recorded results:

| Gateway configuration | Macro attack detection | Macro benign FPR | p50 ensemble latency |
|---|---:|---:|---:|
| Shipped rules + NumPy classifier | 79.1% | 6.5% | 1.801 ms |
| Rules + student at metadata-selected threshold | 71.5% | 2.2% | 29.813 ms |
| Rules + student at probability 0.5 | 79.8% | 2.9% | 29.433 ms |
| Rules + NumPy + student | 81.6% | 6.6% | 14.766 ms |

The 0.5 result is an alternate operating point, **not** the validation-selected
threshold. The student does not dominate the default: at the metadata-selected
threshold it detects fewer attacks, and it adds latency. The combined
configuration increases detection modestly while also increasing false
positives. Treat these as directional evidence from this project's fixed
evaluation harness, not a claim of general superiority. The older comparison
in the README uses a different baseline/evaluation path; consult
[`scripts/guard_student_ensemble.py`](../scripts/guard_student_ensemble.py)
and the report when comparing numbers.

## Not yet established

- No full-service run in a **512 MB-capped container** has been completed.
  The isolated model RSS result does not establish that the complete gateway
  fits that limit.
- An optional AgentDojo 0.1.35 pipeline adapter and runner are now available
  (`gateway/agentdojo.py`, `scripts/run_agentdojo_gateway.py`), but no AgentDojo
  results are claimed until it has been run with a configured model provider.
  It screens tool outputs with the gateway's detector and can optionally run
  the action firewall when supplied an explicit policy and tool-name mapping.
- The gateway evaluation is not a substitute for an independently authored
  external test set or a matched-false-positive comparison against every
  current commercial/open detector.

Reproduce the gateway comparison with:

```bash
python -m scripts.guard_student_ensemble
```

## AgentDojo integration

The integration is an optional benchmark dependency, not a serving dependency.
Install it with:

```bash
pip install -e ".[agentdojo]"
```

Set the environment variables required by the chosen AgentDojo model provider,
then run a focused suite before attempting the full suite:

```bash
python -m scripts.run_agentdojo_gateway --suite workspace \
  --attack tool_knowledge --model gpt-4o-2024-05-13 \
  --max-pairs 6 --dry-run
```

The runner samples at most six user-task/injection-task pairs by default using
a recorded seed. `--smoke-test` further limits the run to at most three pairs.
`--dry-run` validates the suite, attack, pair selection and budgets, prints a
plan, and makes no model calls. Supply exact repeated `--user-task` and
`--injection-task` selectors when you want a specific subset. Task IDs must
exist in the selected suite/version.

For a free local smoke test, install Ollama and pull the requested model:

```powershell
ollama pull qwen2.5:7b-instruct
python -m scripts.run_agentdojo_gateway --provider ollama --smoke-test `
  --suite workspace --attack tool_knowledge --dry-run
python -m scripts.run_agentdojo_gateway --provider ollama --smoke-test `
  --suite workspace --attack tool_knowledge
```

The runner uses Ollama's OpenAI-compatible endpoint at
`http://localhost:11434/v1`; override it with `--ollama-base-url`. It defaults
to model `qwen2.5:7b-instruct`; override with `--model-id`. Run Ollama locally
before the non-dry-run command. The dry run does not contact Ollama or verify
that the requested model is installed.

The script records the suite and benchmark version, model, sampled task pairs,
security rate, attack-success rate, utility, detector block counts, model-call
count and AgentDojo run logs. `--compare-baseline` runs the same sampled pairs
without gateway defenses as a separate arm, so the call and optional cost
limits apply independently to each arm.

Every run is limited to 100 LLM invocations per arm by default. Set
`--max-llm-calls` to a lower bound appropriate for your provider. Cloud spend
can also be bounded by supplying both `--max-cost-usd` and
`--estimated-cost-per-llm-call-usd`; the runner reserves that amount before
each call and stops before exceeding the configured estimate. Choose a
conservative per-call ceiling for the model's input/output limits. This is a
client-side reservation, **not** an exact billing meter or a provider-enforced
spending cap; pair it with provider quotas/budgets. In compare mode the
configured spend cap applies per arm, so total planned spend may be twice the
per-arm value. The call ceiling wraps AgentDojo's LLM pipeline invocations;
provider SDK retries may create additional network attempts, so also set
provider-side quotas and disable retries in production configurations where
strict request accounting is required.

This adapter screens each new batch of tool outputs before they are returned
to the agent. Detected text is replaced with a fixed omission marker; non-text
content and benign text are preserved. The evaluator itself is AgentDojo's
`run_task_with_injection_tasks` and
`run_task_without_injection_tasks` APIs, so the runner can evaluate only the
sampled pairs and their corresponding no-injection utility tasks. A full
tool-authorization comparison can be enabled with
`--firewall-policy path/to/policy.yaml` and an optional
`--tool-map path/to/tool-map.json`. The JSON map is an object from AgentDojo
function names to gateway policy tool names. Every unmapped or unconfigured
action is denied. Approval-required writes are held and never executed by the
benchmark adapter, so expect utility to decrease on tasks requiring writes.
Mark data-bearing AgentDojo tools as `output_trust: untrusted` in the policy
when their outputs may contain injected content; taint checks rely on that
policy classification.
The action-policy results are specific to the policy and mapping supplied;
they are not implied by the detector-only run.

Example detector plus action-policy run:

```bash
python -m scripts.run_agentdojo_gateway --suite workspace \
  --attack tool_knowledge --model gpt-4o-2024-05-13 \
  --firewall-policy config/your-agentdojo-policy.yaml \
  --tool-map config/your-agentdojo-tool-map.json \
  --compare-baseline
```

Do not use an allow-by-default policy: the gateway rejects such policies.
Review the generated action audit JSONL and approval database under the
specified run log directory alongside the benchmark report.
