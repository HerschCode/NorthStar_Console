# Distilled ONNX prompt-injection guard

The student guard is an **opt-in** classifier backend. It is not the default
gateway detector and it does not replace the rules, response checks, or action
firewall. Enable it after measuring it on the traffic and latency budget of your
own deployment; this page says what was measured here and, as importantly, what
was not.

## Run it

Install the optional ONNX Runtime dependency and select the student backend:

```bash
pip install -e ".[student]"
# macOS / Linux
CLASSIFIER_BACKEND=student uvicorn gateway.app:app --port 8000
```

In PowerShell, set `$env:CLASSIFIER_BACKEND = "student"` before running
`uvicorn gateway.app:app --port 8000`.

`CLASSIFIER_BACKEND` is `numpy` (default), `student`, or `both` (the NumPy
classifier first, then the student, block-on-any). `STUDENT_THREADS` sets the
ONNX Runtime thread count (default 1). The checked-in model, tokenizer and
metadata live under [`models/guard_student/`](../models/guard_student/); their
hashes are covered by [`models/MANIFEST.sha256`](../models/MANIFEST.sha256) and
checked before the ONNX file is parsed. CI installs the hash-locked
[`requirements-student.lock`](../requirements-student.lock) so the student's
tests run there; the deployed free-tier image does not include ONNX Runtime and
keeps the default backend.

The runtime is ONNX Runtime plus a pure-Python WordPiece tokenizer
([`gateway/detectors/wordpiece.py`](../gateway/detectors/wordpiece.py)): no
`tokenizers`, `transformers`, `huggingface-hub` or torch. It loads the packaged
Hugging Face `tokenizer.json` and refuses any configuration other than a BERT
uncased normalizer, BERT pre-tokenizer and WordPiece model. Literal text that
looks like a special token (`[SEP]`) is tokenized as ordinary text, never as
model structure.

**The tokenizer is checked against the Hugging Face one, not assumed to match.**
`python scripts/wordpiece_fuzz.py <tokenizer.json>` compares every Unicode code
point, in three contexts each, against the Rust tokenizer: 0 differences under
Python 3.12 / Unicode 15.0. (A Python with older Unicode tables produces
spurious differences; the script prints the Python and Unicode versions it ran
under.) A 473-case
golden file generated from the Rust tokenizer pins this in the test suite.

## How it was trained

- **Student:** `sentence-transformers/all-MiniLM-L6-v2` (22M parameters).
  **Teacher:** `protectai/deberta-v3-base-prompt-injection-v2` (about 700 MB).
- **Labelled rows:** 8,178 prompts from the project's public training sources,
  with every row that also appears in any held-out set removed first.
- **Transfer set:** 3,467 unlabelled benign texts, labelled **only** by the
  teacher's soft labels. Teacher knowledge on text the labelled data never
  covered is the point: it is what carries over to sources the student did not
  train on.
- **Loss (`hardkd_tkd`, alpha 0.3):** on labelled rows, 0.3 x cross-entropy plus
  0.7 x match to the teacher's temperature-2 distribution; on transfer rows, the
  teacher match alone. Four epochs, batch 32, learning rate 5e-5, three seeds.

### Why this recipe: leave-one-source-out

Choosing a recipe on the held-out table you then report is how a number gets
flattered, so the recipe was chosen on **development data**: leave-one-source-out
(LOSO). For each of the four training sources (deepset, safeguard, jackhhao,
gandalf) the student is trained without that source and tested on that source's
test split, and the selection rule, fixed in advance, was the **best mean LOSO
AUC**. Two seeds per variant; mean over the sources:

| Variant | Mean LOSO AUC | Benign FPR at 0.5 (unseen source) | Attack detection at 0.5 (unseen source) |
|---|---:|---:|---:|
| `hard` (labels only, no teacher) | 0.838 | 10.8% | 62.9% |
| `hard_tkd` | 0.838 | 8.2% | 58.3% |
| `hardkd` (alpha 0.5) | 0.878 | 1.4% | 59.0% |
| `hardkd_tkd` (alpha 0.5) | 0.907 | 1.1% | 57.6% |
| **`hardkd_tkd`, alpha 0.3 (shipped)** | **0.913** | **0.4%** | **58.2%** |

[`reports/p3_guard_student_loso.json`](../reports/p3_guard_student_loso.json)
has every variant and fold. Read it for what it says: distillation roughly
**halves the false-alarm rate on unseen sources and raises ranking quality**
(AUC +0.075 over labels-only), but on an unseen source it does **not** detect
more at 0.5. It detects slightly fewer (58% vs 63%) while raising almost no
false alarms. It is a more cautious detector, not a more sensitive one.

**Order of evidence (disclosure).** The operating point, probability 0.5, was
chosen from these LOSO folds, but I had already seen the held-out table for an
earlier interim model when I chose it. The stricter alternative (the threshold
that gives at most 1% false positives on in-distribution validation rows,
margin 7.06) is recorded in `meta.json` beside the shipped one and not used: on
unseen sources 0.5 already gives about 1% false positives, and the stricter
threshold costs about nine points of detection.

## Model, quantization and footprint

The shipped file is `int8-matmul-gather-perchannel` (dynamic int8), the smallest
variant whose validation AUC is within 0.003 of fp32 and whose decisions agree
on at least 99% of validation rows. Parity gates in the export script: ONNX fp32
against the torch model, maximum margin difference 0.0000; the tokenizer
against Hugging Face on 16,755 project texts.

| | fp32 | shipped int8 |
|---|---:|---:|
| File size | 86.8 MB | 22.1 MB |
| Validation ROC-AUC | 0.9865 | 0.9863 |
| Decision agreement with fp32 at its threshold | n/a | 99.6% |
| Process RSS (1 thread, peak, model-only process) | n/a | 92 MB |
| p50 / p95 latency, 1 thread, one request at a time | n/a | 4.7 / 26 ms |
| p50 / p95 latency, 4 threads | n/a | 2.7 / 10 ms |

These are **model-only** measurements in an isolated process (see
[`reports/p3_guard_student_onnx.json`](../reports/p3_guard_student_onnx.json)).
The gateway-level latency and the memory-cap result are below.

## Gateway evaluation

Each configuration runs through the gateway's real pre-flight path (PII
redaction, normalisation, rules with cipher readings, then the classifier layer,
block-on-any) on the held-out sets in
[`reports/p3_guard_student_gateway.json`](../reports/p3_guard_student_gateway.json).
Detection and false-positive rates are **macro-averaged** over the sets that have
attacks and benign examples respectively, so every set weighs the same, not every
row. Per-set counts and Wilson 95% intervals are in the report; with sets of 17
to 244 rows, **differences of a few points between configurations are inside the
intervals**.

| Gateway configuration | Macro attack detection | Macro benign FPR | p50 detection latency |
|---|---:|---:|---:|
| Rules only | 16.2% | 0.7% | 0.34 ms |
| Shipped default: rules + NumPy classifier | 79.1% | 6.5% | 0.41 ms |
| **Rules + student at 0.5 (the shipped operating point)** | **75.6%** | **4.1%** | 4.2 ms |
| Rules + student at the stricter validation threshold | 59.0% | 2.1% | 4.4 ms |
| Rules + NumPy + student (`both`) | 83.7% | 7.4% | 3.3 ms |
| Rules OR ProtectAI teacher (700 MB, cached scores) | 79.3% | 4.4% | n/a |

Latency is the median over 300 single requests, one at a time, one thread, with
no other job running; it is the whole detection ensemble, not the model alone,
and it is not comparable across machines. (An earlier version of this page
reported 29.8 ms: that was taken while a training job shared the CPU.)

What the table says, and does not:

- **The student does not beat the default on detection.** Alone it finds 3.5
  points fewer attacks (75.6% vs 79.1%) with 2.4 points fewer false alarms
  (4.1% vs 6.5%). It is a different point on the same trade-off, not a
  dominating one.
- **Next to the 700 MB teacher** it is close: about the same macro false-alarm
  rate (4.1% vs 4.4%) and 3.7 points lower detection (75.6% vs 79.3%), at 22 MB.
  The rates are not matched, so this is not a matched-FPR comparison.
- **On attacks it never trained on, the teacher is clearly ahead.** On the
  project's own corpus (78 attacks written for this project and held out of
  every training set) the student detects 74.4%, ten points more than the NumPy
  classifier (64.1%) and eleven and a half points fewer than the teacher
  (85.9%). That corpus was written by the same author as the rules, so the rules'
  own 35.9% on it is flattered, not the classifiers'.
- **`both` has the best detection (83.7%)** because the two classifiers miss
  different things, at the cost of the NumPy classifier's false alarms
  (7.4% macro).
- **Where each set comes from.** The deepset, safeguard, jackhhao and gandalf
  test splits are in-distribution for the NumPy classifier and the student (their
  training splits were used); `own_corpus`, `jbb_benign`, `jbllms_clean` and the
  short, in-domain, OASST and persona benign sets were never trained on. I did not
  check the teacher's training data for overlap with these public sets
  (jackhhao and jailbreak_llms in particular), so its numbers on them may be
  flattered.
- **`deepset_test`** is the student's weakest set (26.7% detection against
  48.3% for the NumPy classifier and 36.7% for the teacher). It has only 60
  attacks (a wide interval) and is partly non-English; I did not investigate why
  the student is weaker there.
- **Long prompts are cut.** The student reads the first 256 tokens only.
  `jbllms_clean`, long jailbreak prompts, is a set where it trails the NumPy
  classifier (82.0% vs 87.7%), which is consistent with that limit; I have not
  isolated it.

Reproduce with:

```bash
python -X utf8 -m scripts.guard_student_ensemble
```

## Does it fit in 512 MB?

A real `docker run -m 512m` has not been possible (no Docker daemon on the
development machine), so this is a **substitute with different accounting**:
[`scripts/measure_memory_cap.py`](../scripts/measure_memory_cap.py) starts the
real app (`uvicorn gateway.app:app`, full mode, the locked torch-free serving set
on Python 3.12, `MODEL_INTEGRITY=enforce`) inside a Windows job object that caps
its **committed** memory, and drives it with 400 requests (held-out benign and
attack texts, five 19,980-character prompts just under the request limit, eight
concurrent clients). The cap is then lowered until the server fails, which is the
control that shows the cap actually bites. Results
([`reports/p3_guard_student_memory.json`](../reports/p3_guard_student_memory.json)):

| `CLASSIFIER_BACKEND` | Peak committed | Peak working set | Survived every cap down to | Failed at |
|---|---:|---:|---:|---:|
| `numpy` (default) | 63 MB | 76 MB | 64 MB (marginal) | none tried below |
| `student` | 138 MB | 162 MB | 144 MB | 128 MB |
| `both` | 140 MB | 155 MB | 144 MB | 128 MB |

With the student enabled the whole gateway needs about 140 MB committed and
160 MB resident, roughly a third of 512 MB. The numpy figure sits right at its
cap: it passed at 64 MB in one sweep and failed there in an earlier one, so read
it as "about 63 MB", not as a margin.

What this does **not** show:

- A Linux cgroup counts resident memory plus page cache and kills the process; a
  Windows commit limit fails allocations. Commit is usually at least the working
  set, so passing is a reasonable sign, not proof. `docker run -m 512m` on the
  Render image remains the real test.
- BLAS is pinned to one thread (`OPENBLAS_NUM_THREADS=1`) to model a small host.
  This machine has 20 cores, and unpinned OpenBLAS reserves a buffer per thread,
  which a Windows commit cap counts: the unpinned default interpreter showed
  about 660 MB committed right after importing the app, for 64 MB resident.
- 400 requests is a short run, and requests per second on this machine say
  nothing about a small host.


## Not yet established

- **A real `docker run -m 512m` of the deployed image.** There is no Docker
  daemon on the development machine; the Windows job-object test above is a
  substitute with different accounting.
- **A matched-false-positive comparison** against Llama Prompt Guard 2 and other
  current detectors on an independently authored set. The comparison above is
  against the teacher only, at one operating point each.
- **AgentDojo and the adaptive red-team numbers with the student enabled.** The
  AgentDojo runner below is built, but no AgentDojo result is claimed: running it
  needs a model provider key, which the account owner sets. The
  default backend stays `numpy` for that reason as well: switching it would
  change what the published red-team numbers measured.
- **The student has not been through the adaptive attacker.** A stronger-than-
  public attacker who knows the model is ONNX MiniLM can optimise against it; the
  rules and the action firewall are the layers that do not depend on it.


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
