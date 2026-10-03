# LLM gate evaluation

**Model:** openai/gpt-oss-20b  
**Rows evaluated:** 80 (27 correct, 27 wrong_fact, 26 off_context)

The lexical gate (`claim_support.py`, `support@0.65`) cannot detect polarity flips
(`included` vs `excluded`, `Yes` vs `No`, `required` vs `optional`).
The LLM judge is intended to assess semantic entailment rather than term overlap.
The evaluation measures whether it catches these cases; it does not assume that it does.

> **Historical results below need to be rerun.** The stored run evaluated 80 of the
> possible 96 rows and used a parser that treated truncated outputs and API failures as
> ordinary UNFAITHFUL decisions. These figures therefore do not measure judge quality.
> The current evaluator retries malformed labels, excludes operational failures from the
> classification metrics, and supports Gemini: `python -m scripts.evaluate_llm_gate
> --provider gemini --model gemini-3.8-flash
> --context-snapshot data/evaluation/p2_gate_contexts.json`
> (requires `GEMINI_API_KEY`). The frozen evidence snapshot covers 32 question IDs.
> The configured account currently receives 403 access denied for `gemini-3.8-flash`;
> resolve Google AI Studio project access before rerunning. Permanent 4xx errors now
> stop immediately instead of consuming retry time/quota. Use `--resume` after access
> is restored; failed rows are retried and each completed row is saved immediately.

## Overall scores

| Gate | Accuracy | F1 | Correct pass rate | Wrong blocked rate |
|---|---|---|---|---|
| lexical | 82.5% | 0.741 | 74.1% | 86.8% |
| llm | 75.0% | 0.412 | 25.9% | 100.0% |
| ensemble | 75.0% | 0.412 | 25.9% | 100.0% |

## Per-type pass rates (lower = gate firing more)

### lexical

| Type | Pass rate | n |
|---|---|---|
| correct | 74.1% | 27 |
| wrong_fact | 25.9% | 27 |
| off_context | 0.0% | 26 |

### llm

| Type | Pass rate | n |
|---|---|---|
| correct | 25.9% | 27 |
| wrong_fact | 0.0% | 27 |
| off_context | 0.0% | 26 |

### ensemble

| Type | Pass rate | n |
|---|---|---|
| correct | 25.9% | 27 |
| wrong_fact | 0.0% | 27 |
| off_context | 0.0% | 26 |

## Disagreements (lexical ≠ LLM judge)

20 rows where the two gates disagree:

**id=2 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] FA
- Answer: The SLA target for 3‑way match category orders is **10 business days**.

**id=4 (wrong_fact)** — expected UNFAITHFUL
- Lexical: PASS (✗)
- LLM: BLOCK (✓) — The answer claims the Finance team performs Manual Credit Review, but the context states it is performed by
- Answer: Manual Credit Review is performed by the Finance team.

**id=5 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: Each escalation level has **2 business days** to act before the case is automatically escalated to t

**id=6 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] FA
- Answer: SLA targets are reviewed **quarterly** by Operations leadership.

**id=8 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: A supplier must have **at least three prior completed orders** to avoid being automatically routed t

**id=11 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: Escalation is triggered when a supplier is unresponsive for **more than 3 business days** during the

**id=13 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: Yes. PO Change Approval time is included in the cycle‑time calculation, even though it falls outside

**id=13 (wrong_fact)** — expected UNFAITHFUL
- Lexical: PASS (✗)
- LLM: BLOCK (✓) — [parse error] 
- Answer: No. PO Change Approval time is included in the cycle‑time calculation, even though it falls outside 

**id=14 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: No. Escalation does not override any approval requirements in the Procurement Policy, including the 

**id=14 (wrong_fact)** — expected UNFAITHFUL
- Lexical: PASS (✗)
- LLM: BLOCK (✓) — [parse error] 
- Answer: Yes. Escalation does not override any approval requirements in the Procurement Policy, including the

**id=15 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: No. Manual Credit Review time is **included** in the cycle‑time measurement; it is not paused or exc

**id=15 (wrong_fact)** — expected UNFAITHFUL
- Lexical: PASS (✗)
- LLM: BLOCK (✓) — [parse error] 
- Answer: Yes. Manual Credit Review time is **included** in the cycle‑time measurement; it is not paused or ex

**id=16 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: When a released Purchase Order is changed, the **entire original approval chain must be re‑run**—the

**id=17 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: Yes. When a supplier disputes an order’s terms after the PO is released, the case is placed on hold 

**id=17 (wrong_fact)** — expected UNFAITHFUL
- Lexical: PASS (✗)
- LLM: BLOCK (✓) — [parse error] 
- Answer: No. When a supplier disputes an order’s terms after the PO is released, the case is placed on hold a

**id=18 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: The only documented exception is when a supplier disputes an order’s terms after the purchase‑order 

**id=18 (wrong_fact)** — expected UNFAITHFUL
- Lexical: PASS (✗)
- LLM: BLOCK (✓) — [parse error] 
- Answer: The only documented exception is when a supplier disputes an order’s terms after the purchase‑order 

**id=19 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: Yes. Even though an emergency purchase can bypass the normal requisition approval, Section 2 states 

**id=19 (wrong_fact)** — expected UNFAITHFUL
- Lexical: PASS (✗)
- LLM: BLOCK (✓) — [parse error] 
- Answer: Yes. Even though an emergency purchase can bypass the normal requisition approval, Section 2 states 

**id=21 (correct)** — expected FAITHFUL
- Lexical: PASS (✓)
- LLM: BLOCK (✗) — [parse error] 
- Answer: The procedure calls for Operations leadership to conduct a specific review of that supplier’s proces

## Design notes

- **Same evaluation set**: compared on the existing labeled set
  (`reports/gate_labeled_eval.json`), not a new holdout. Results measure
  within-sample performance; correct answers were labeled by one author. An independent
  holdout and second annotator would be stronger.
- **Chunks**: freeze exact evidence with
  `python -m scripts.evaluate_llm_gate --freeze-contexts data/evaluation/p2_gate_contexts.json`
  and reuse it with `--context-snapshot data/evaluation/p2_gate_contexts.json`. The snapshot
  records hashes of the labeled answer/question source files. Off-context rows use the
  recorded `partner` question's chunks.
- **Labeling**: `correct` answers were hand-checked against the policy documents
  (same author as this eval — disclosed in `data/evaluation/gate_labels.json`).
- **Polarity flips**: `wrong_fact` rows include mutated numbers, swapped team
  names, and yes/no inversions. Results should be reported by mutation type; no catch
  rate is presumed.
- **Provider failures**: API errors and unparseable/truncated responses are excluded
  from classification metrics and counted separately; they are not successful blocks.
- **Provider selection**: Groq is the default; use `--provider gemini` with a configured
  `GEMINI_API_KEY` to evaluate through Google AI Studio.
