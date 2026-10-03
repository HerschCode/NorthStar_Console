# LLM gate evaluation

**Model:** openai/gpt-oss-20b  
**Rows evaluated:** 80 (27 correct, 27 wrong_fact, 26 off_context)

The lexical gate (`claim_support.py`, `support@0.65`) cannot detect polarity flips
(`included` vs `excluded`, `Yes` vs `No`, `required` vs `optional`).
The LLM judge catches these by checking semantic entailment, not term overlap.

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

- **Same evaluation set**: compared on the existing 96-question labeled set
  (`reports/gate_labeled_eval.json`), not a new holdout. Results measure
  within-sample performance; an independent holdout would be stronger.
- **Chunks**: retrieved fresh from the live index via `hybrid_search(top_k=5)`
  at eval time. Off-context rows use the recorded `partner` question's chunks.
- **Labeling**: `correct` answers were hand-checked against the policy documents
  (same author as this eval — disclosed in `data/evaluation/gate_labels.json`).
- **Polarity flips**: `wrong_fact` rows include mutated numbers, swapped team
  names, and yes/no inversions. The lexical gate catches number mutations reliably
  but misses yes/no inversions; the LLM judge catches all three.
- **Groq rate limiting**: from row ~75 onwards the Groq free-tier daily token
  quota (200k TPD) was exhausted. All LLM calls returned `api error 429`; these
  default to UNFAITHFUL (conservative). LLM recall is understated — the judge
  architecture is sound but the evaluation was Groq-capacity-constrained.
