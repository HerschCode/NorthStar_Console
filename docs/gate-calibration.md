# Faithfulness Gate Calibration

**Scripts:** `scripts/calibrate_gate.py`, `scripts/gate_labeled_eval.py`, `scripts/analyze_hard_negatives.py`  
**Data:** `data/evaluation/faithfulness_results.json`  
**Output:** `data/evaluation/gate_calibration_results.json`, `data/evaluation/hard_negatives_analysis.json`, `reports/gate_labeled_eval.json`

---

## Current gate: claim-support (deployed)

The gate is `src/evaluation/claim_support.py` — a deterministic lexical check, no model.
Activate with `GATE_METHOD=support` (default); restore old NLI gate with `GATE_METHOD=nli`.

**What it checks per answer sentence:**
- Every number appears in the retrieved chunks with the same unit nearby
- Every key term (mid-sentence capitalised name/team/role, frequency word) appears
- ≥ 65% content-word recall against the chunks (`SUPPORT_MIN_RECALL`, default 0.65)
- Reports *why* it blocked: missing number / missing key term / low word recall

**Why the NLI gate was replaced:** see [Archived: NLI gate analysis](#archived-nli-gate-analysis) below.

---

## Labeled evaluation results (2026-09-26)

Reproduce: `python -m scripts.gate_labeled_eval` (raw rows: `reports/gate_labeled_eval.json`)

**Labeled set:** 96 answers across 32 questions × 3 conditions:
- Correct answers (LLM-generated, hand-verified against `data/documents/*.md`)
- Same answers with one fact mutated deterministically (number, team, cadence, yes/no)
- Correct answers scored against chunks retrieved for a *different* question (off-context)

The threshold parameter (`r`) was tuned on odd-id questions only; the table below is held-out even ids only.

| Gate | Correct passed | Wrong fact passed | Off-context passed |
|---|---|---|---|
| NLI, t = 0.05 (was deployed) | 18.8% | 18.8% | 18.8% |
| NLI, t = 0.5 | 18.8% | 18.8% | 18.8% |
| **Claim support, r = 0.65 (now default)** | **68.8%** | **18.8%** | **6.2%** |
| No gate | 100% | 100% | 100% |

On all 32 questions: claim-support passes 68.8% correct / 21.9% wrong-fact / 3.1% off-context.  
NLI (t=0.05): 37.5% correct / 31.2% wrong-fact / 21.9% off-context — indiscriminate.

**Key numbers at a glance:**

| What | Number |
|---|---|
| Gate coverage (correct answers that pass) | **68.8%** at r=0.65 |
| Wrong-fact pass rate (false negatives) | **18.8%–21.9%** |
| Off-context pass rate (false positives) | **3.1%–6.2%** |
| NLI coverage (was deployed) | 18.8% — same as random |

**What it still gets wrong.** All wrong facts that pass are polarity flips (Yes↔No,
included↔excluded) — a lexical check cannot see sign changes. The correct answers
that are blocked are mostly long paraphrased or multi-step answers whose wording drifts
from the documents (content recall < 0.65). 16 questions per held-out cell is small;
treat differences under ~15 points as noise.

**Labels are by the project author (not an independent annotator).** Spot-check
`data/evaluation/gate_labels.json` against the source documents to verify.

---

## Gate coverage in production

34.4% of in-domain answers pass the claim-support gate at r=0.65 (11/32 in the original
calibration set, which used NLI — the claim-support coverage of 68.8% is on the labeled
eval set). Of answers the gate blocks:

- 61.9% had an NLI contradiction signal (`nli_signal_rate_of_blocked`)
- 38.1% had no NLI signal at all (`blocked_neutral`): gate fires correctly but only on
  the lexical check, not on an explicit contradiction

**Gate coverage alone** (`gate_coverage_in_domain = 0.344`) is a non-circular metric:
it measures what fraction of in-domain answers the gate lets through, without assuming
blocked answers are wrong.

---

## Recommendations

1. **Use r=0.65** (current default) for the claim-support gate — chosen on held-out data.
2. **Monitor wrong-fact pass rate on polarity questions** — Yes/No and included/excluded
   are the gate's systematic blind spot. A second-pass negation check would close this.
3. **Get independent labels** — current labels are author-verified, which limits the
   credibility of precision/recall numbers. Hand-checking 10 gate decisions is enough
   to establish a baseline.

---

## Reproduction

```bash
# Labeled evaluation: claim-support vs NLI on 96 labeled answers
python -m scripts.gate_labeled_eval

# Calibration: NLI threshold vs coverage (historical, NLI only)
python -m scripts.calibrate_gate

# Hard-negative subtype analysis (historical, NLI only)
python -m scripts.analyze_hard_negatives
```

Both calibration scripts are offline (no API calls, no torch reloading — faithfulness
scores are pre-computed in `faithfulness_results.json`).

---

## Archived: NLI gate analysis

> **This section is superseded.** The NLI gate (`GATE_METHOD=nli`) was the original
> deployment. Three problems found in the 2026-09-26 re-evaluation caused it to be
> replaced by the claim-support gate:
>
> 1. **The "hallucinations" were correct answers.** All 32 in-domain answers were
>    hand-verified against `data/documents/*.md` (`data/evaluation/gate_labels.json`):
>    all 32 are correct. The hard-negative analysis labelled them as hallucinations using
>    NLI contradiction scores — circular, since it could only confirm the NLI model.
>    NLI gives correct procurement sentences contradiction probabilities near 1.
>
> 2. **The OOD test never exercised NLI.** All 35 SQuAD answers in
>    `ood_abstention_results.json` are empty strings. The "100% OOD rejection" came
>    entirely from the empty-answer rule, not the NLI gate.
>
> 3. **"Before every RAG answer" was not true.** The gate lives in `grounded_answer()`;
>    the live `/demo/chat` agent calls `run_agent()`, which does not call `grounded_answer()`.

### NLI calibration table (historical)

| Threshold | Coverage (in-domain) | OOD rejection |
|:---------:|:-------------------:|:-------------:|
| 0.00 | 100.0% | 0.0% |
| **0.05** | **37.5%** | **100.0%** |
| 0.50 | 34.4% | 100.0% |
| 1.00 | 28.1% | 100.0% |

OOD rejection is 100% at all thresholds above 0 — but this is because the OOD answers
were all empty strings, not because NLI discriminated out-of-domain questions.

### NLI hard-negative subtypes (historical)

| Type | n | % | Gate rate | Mean entailment | Mean contradiction |
|---|:---:|:---:|:---:|:---:|:---:|
| correct | 5 | 15.6% | 0.0% | 0.864 | 0.083 |
| ambiguous | 7 | 21.9% | 14.3% | 0.917 | 0.972 |
| halluc_caught | 13 | 40.6% | 100.0% | 0.011 | 0.928 |
| blocked_neutral | 7 | 21.9% | 100.0% | 0.009 | 0.042 |

These types were defined by NLI scores and scored against NLI-derived labels — circular.
`halluc_caught` answers are correct procurement answers; the NLI model contradicts them
because it was trained on MNLI/SNLI, not procurement text. Kept for the record only.
