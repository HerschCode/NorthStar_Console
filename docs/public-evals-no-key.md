# Evaluations that need no API key: what ran, on what, and what it says

Everything here ran on this machine with no paid call: data from public sources (pinned and checksummed), a local `qwen2.5:7b-instruct` through Ollama where a model is needed, and P2's own code
unchanged. Rules for reading it: every result file names the model that produced it; **a local-7B result is a pipeline proof, never a headline number and never comparable with a Claude run**; every cell
that needs a key or an account says so ("pending (key)", "blocked") instead of being left out; and negative results stay in. The bugs these evaluations found are listed at the end, with their commits.

## 1. The answer gates on RAGTruth's human labels

RAGTruth (Niu et al., 2024; MIT; [ParticleMedia/RAGTruth](https://github.com/ParticleMedia/RAGTruth), read at commit `c103204b9c`): 2,700 test responses from six models (GPT-4, GPT-3.5, three
Llama-2 sizes, Mistral-7B) to retrieval-augmented QA, data-to-text and summarisation prompts, with span-level hallucination labels by human annotators. A response counts as **hallucinated** here when it has at least
one labelled span (34.9% of them do). Two gates run as deployed, not tuned to this data: the lexical claim-support gate (`src/evaluation/claim_support.py`, r = 0.65) and the NLI baseline
(`cross-encoder/nli-deberta-v3-small`, every sentence needs entailment >= 0.5 against some 200-word window of the source). `python -X utf8 -m scripts.eval_gate_ragtruth` ->
[`reports/gate_ragtruth_nokey.json`](../reports/gate_ragtruth_nokey.json).

| Gate (positive class = hallucinated) | Hallucinated flagged (recall) | Supported responses flagged (false positives) | Precision | Balanced accuracy | ROC-AUC |
|---|---:|---:|---:|---:|---:|
| Flag every response (baseline) | 943/943 (100.0%) | 1757/1757 (100.0%) | 34.9% | 0.50 | n/a |
| Lexical claim-support gate, r = 0.65 (deployed) | 923/943 (97.9%) | 1390/1757 (79.1%) | 39.9% | 0.59 | 0.765 |
| NLI baseline, evidence first (corrected 2026-10-06) | 940/943 (99.7%) | 1699/1757 (96.7%) | 35.6% | 0.52 | 0.623 |
| NLI baseline, answer sentence first (as deployed before; swapped pairs) | 936/943 (99.3%) | 1726/1757 (98.2%) | 35.2% | 0.51 | 0.502 |

ROC-AUC uses each gate's continuous score (lexical: the weakest sentence's content recall, minus 1 when its numbers or key terms fail; NLI: the weakest sentence's best entailment); 0.5 is chance, and
"flag every response" has precision equal to the hallucinated share. The swapped-pairs row is in [`reports/gate_ragtruth_nokey_reversed_pairs.json`](../reports/gate_ragtruth_nokey_reversed_pairs.json).

| Task | Hallucinated | AUC, lexical | AUC, NLI (evidence first) | AUC, NLI (swapped, before) |
|---|---:|---:|---:|---:|
| Data2txt | 579/900 (64.3%) | 0.475 | 0.509 | 0.493 |
| QA | 160/900 (17.8%) | 0.761 | 0.653 | 0.492 |
| Summary | 204/900 (22.7%) | 0.673 | 0.562 | 0.469 |

**What it says.**
- **At its deployed threshold the lexical gate flags almost everything here** (79.1% of supported responses), because these responses are long and multi-sentence and one unsupported sentence flags the
  whole response; its threshold was set on short policy answers. Its *ranking* has real signal (AUC 0.765; QA 0.761, summarisation 0.673), which means a threshold chosen
  for this kind of text would behave very differently from r = 0.65. On data-to-text it is at chance (0.475): the source is a JSON record, so word overlap says little.
- **The NLI baseline is weak on this data even in the right order** (AUC 0.623; answer sentence first, as it was deployed, 0.502). Fixing the pair order recovers some ranking
  ability but not a usable gate: it flags 96.7% of supported responses at its threshold. (On the 32 procurement answers in `docs/gate-calibration.md`, evidence-first NLI does discriminate;
  these are different texts, much longer, and a small cross-encoder over 200-word windows.)
- Caveats: RAGTruth is news, Yelp business data and MS MARCO passages, not procurement policy; a response-level label from span annotations is coarser than the claim-level checks the gates make.

**LLM-AggreFact: blocked, not run.** It is the other public set the plan names. It is gated on Hugging Face (CC BY-ND 4.0; it needs an account that has accepted its terms and an `HF_TOKEN`), which only the
account owner can provide. `scripts/eval_gate_public.py` will run it, with the LLM-judge columns, once that exists; its earlier licence statements (MIT, Apache) and guessed dataset ids were wrong and are fixed.
The LLM-judge columns for RAGTruth are **pending (key)**.

## 2. Retrieval on FinanceBench (a closed-set proxy)

FinanceBench (Islam et al., 2023; CC BY-NC 4.0, non-commercial use only; `PatronusAI/financebench` revision `e04404e3a9`): 150 questions over 84 real filings, each with the evidence page(s) a
person selected. **The corpus here is only those evidence pages** (168 pages, 598 chunks of 1000 characters), not the filings, so every number is optimistic: the real task retrieves from thousands of
pages. P2's own `semantic_search`, `bm25_search` and `hybrid_search` run unchanged in a throw-away Chroma directory. Hit@k = one of the top-k chunks comes from a page the dataset marks as evidence.
`python -X utf8 -m scripts.eval_financebench_retrieval` -> [`reports/financebench_retrieval.json`](../reports/financebench_retrieval.json). Answer accuracy needs a model and a key: **pending (key)**.

| Retriever | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| dense (semantic_search) | 41.3% | 65.3% | 73.3% | 83.3% | 0.546 |
| BM25 (bm25_search) | 16.0% | 25.3% | 29.3% | 36.0% | 0.215 |
| hybrid, RRF (hybrid_search) | 26.0% | 48.0% | 60.7% | 78.7% | 0.411 |

Hit@5 by question type:

| Question type (n) | dense | BM25 | hybrid, RRF |
|---|---:|---:|---:|
| domain-relevant (50) | 62.0% | 32.0% | 56.0% |
| metrics-generated (50) | 80.0% | 4.0% | 56.0% |
| novel-generated (50) | 78.0% | 52.0% | 70.0% |

**What it says.** Dense retrieval is clearly best. BM25 is poor, and it collapses on the 50 templated "metrics" questions. A likely cause (not verified): P2's BM25 splits on whitespace, so punctuation stays
glued to words ("3M?", "(in") and near-identical question text matches the same generic pages. **P2's hybrid fusion is worse than dense alone** at k <= 5 here, the opposite of what it exists for: on this corpus the weak
BM25 list drags the fused ranking down. On P2's own 12-document policy corpus hybrid and dense are within a point of each other (Hit@1 70.8% vs 70.0% in the README), so hybrid never clearly beat dense there either; its
justification in P2 is the cross-encoder reranker on top (77.7%), which this evaluation did not run. Not investigated or fixed here; the first thing to try is a tokeniser that strips punctuation and lowercases.

## 3. Local-model evaluations (`qwen2.5:7b-instruct`, labelled as such in every file)

**Ask, with page context** (`python -X utf8 -m scripts.eval_v1_ask --provider ollama`, 120 questions that should be answered and 10 that should be refused; [`reports/eval_v1_ask_local.json`](../reports/eval_v1_ask_local.json)).
"Grounded" = at least one claim and at least 80% of claims verified against their cited evidence.

| | First run | After the fixes in section 4 (items 1 and 2) |
|---|---:|---:|
| Grounded answers | 34.2% | 66.7% |
| Answers whose claims all cite evidence | 84.2% | 96.7% |
| Refused although it should answer | 18 | 0 |
| Off-topic probes refused | 100.0% | 100.0% |

| Page | Grounded, first run | Grounded, now |
|---|---:|---:|
| case | 0.0% | 83.3% |
| supplier | 63.3% | 63.3% |
| overview | 33.3% | 80.0% |
| finance | 40.0% | 40.0% |

What is left is mostly the model: figures it states that are not in the evidence it cites (the gate checks figures and citations, **not meaning**: a figure-free claim that cites real evidence passes), and policy claims whose wording
drifts from the document. The finance page is the weakest.

**Natural-language filters** (`scripts.eval_v1_nl_filter --model --provider ollama`, 40 phrasings, exact match): **52.5% (21/40)**, against 40/40 for the deterministic rule
parser (written by the same author as the phrasings, so a smoke test). Failures by kind: 4 rejected a valid request, 14 added constraints the user did not ask for, 1 wrong target or values. The dominant failure is a weak model adding constraints nobody asked for
(`stage=order`, `min_n=100`), which silently changes what the user sees; the restatement shown to the user is the safeguard. A request for dates, owners or regions is now refused before the model sees it.

**Briefing traceability** (`scripts.eval_v1_briefing --provider ollama`): the model's brief was used in **13 of 16 runs (81.2%)** over the full fact set and every leave-one-fact-out variant,
twice each; the other 3 failed the check that every figure appear in the facts it cites (3: a figure is not in the facts it cites) and fell back to the labelled template,
which is the check doing its job. 7 of 8 variants gave the same outcome both times: a local run at temperature 0 is not bit-for-bit repeatable.

**AgentDojo** (P3): a 3-pair smoke run through Ollama proves the runner and its budget caps end to end; it measures nothing about the gateway (see `docs/guard-student.md` in the gateway repo).

## 4. Bugs these evaluations found (all fixed, with tests)

1. **Numeric identifiers were read as figures** (`668a66a`). A claim naming case `2000000100_00001` was marked unsupported for a "figure" no evidence contains; every case-page answer failed (0 of 30 grounded). Unit tests had used ids like `C1`.
2. **The scope filter refused the product's own overview questions** (`cf607eb`): 18 refusals of questions like "How many interventions are recorded?" and "What is the simulated expected loss?".
3. **The model path of the NL filter skipped the unsupported-request check, and any word was a valid supplier** (`bb8e8e8`): "orders from German suppliers" became `supplier = German`.
4. **The NLI gate gave the model premise and hypothesis the wrong way round.** It invalidated P2's documented NLI results and the "domain mismatch" explanation; the corrected re-run and what is retracted are in
   [`gate-calibration.md`](gate-calibration.md).

## 5. Not done, and what it needs

| Item | Needs |
|---|---|
| LLM-judge columns for the gate comparison (RAGTruth, LLM-AggreFact) | an LLM key; LLM-AggreFact also the owner's Hugging Face login |
| FinanceBench answer accuracy | a key |
| Agent evaluation v2, a real AgentDojo run, adaptive red-team runs | a key |
| The Ragas-vs-NLI comparison, re-scored evidence first | free (offline), not yet done |
| A gate requiring claim support AND NLI | free, not yet done |
| Retrieval from whole FinanceBench filings instead of evidence pages | the filings (SEC downloads) |
