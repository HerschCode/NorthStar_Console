"""Measure the polarity check offline on frozen contexts and hand-checked answers.

The deterministic mutations come from scripts.gate_labeled_eval.py. No model or
provider is used. Run with ``python -m scripts.polarity_eval`` to write the
report to reports/polarity_eval.json.
"""

import json
import random
import re
from pathlib import Path

from scripts.gate_labeled_eval import mutate
from src.evaluation.claim_support import sentence_support, split_sentences

BOOTSTRAP_RESAMPLES = 5000
BOOTSTRAP_SEED = 20261006


def passes(answer, chunks, r, use_polarity):
    sents = split_sentences(answer)
    if not sents:
        return False
    for s in sents:
        d = sentence_support(s, chunks)
        if not (d["numbers_supported"] and d["key_terms_supported"] and d["content_recall"] >= r):
            return False
        if use_polarity and d["polarity_conflicts"]:
            return False
    return True


def _percentile(sorted_values, probability):
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def paired_bootstrap_ci(baseline, polarity, n_resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Return 95% percentile intervals for rates and their paired difference.

    Each source question is the resampling unit; baseline and polarity results
    for a question are sampled together to preserve the paired comparison.
    """
    if len(baseline) != len(polarity) or not baseline:
        raise ValueError("baseline and polarity outcomes must be non-empty and equally sized")
    if any(not isinstance(value, bool) for value in (*baseline, *polarity)):
        raise ValueError("bootstrap outcomes must be booleans")
    if not isinstance(n_resamples, int) or isinstance(n_resamples, bool) or n_resamples < 1:
        raise ValueError("n_resamples must be a positive integer")

    rng = random.Random(seed)
    n = len(baseline)
    baseline_rates, polarity_rates, paired_changes = [], [], []
    for _ in range(n_resamples):
        indices = [rng.randrange(n) for _ in range(n)]
        baseline_rate = sum(baseline[index] for index in indices) / n
        polarity_rate = sum(polarity[index] for index in indices) / n
        baseline_rates.append(baseline_rate)
        polarity_rates.append(polarity_rate)
        paired_changes.append(polarity_rate - baseline_rate)

    baseline_rates.sort()
    polarity_rates.sort()
    paired_changes.sort()
    return {
        "baseline_pass_rate_95_ci": [
            round(_percentile(baseline_rates, 0.025), 3),
            round(_percentile(baseline_rates, 0.975), 3),
        ],
        "polarity_pass_rate_95_ci": [
            round(_percentile(polarity_rates, 0.025), 3),
            round(_percentile(polarity_rates, 0.975), 3),
        ],
        "paired_change_95_ci": [
            round(_percentile(paired_changes, 0.025), 3),
            round(_percentile(paired_changes, 0.975), 3),
        ],
    }


def evaluate(res, contexts, support_recall):
    rows = {
        "correct": [],
        "wrong_fact": [],
        "wrong_fact_polarity_word": [],
        "wrong_fact_leading_yes_no": [],
    }
    for x in res:
        a, chunks = x["answer"], contexts[str(x["id"])]
        m = mutate(a)
        is_leading_flip = re.match(r"^(Yes|No)\.", a) and m != a and m[:3] != a[:3]
        has_polarity_term = re.search(r"included|excluded|does not count|counts", m) and m != a
        kind = (
            "wrong_fact_leading_yes_no"
            if is_leading_flip
            else "wrong_fact_polarity_word"
            if has_polarity_term
            else "wrong_fact"
        )
        rows["correct"].append((a, chunks))
        rows["wrong_fact"].append((m, chunks))
        if kind != "wrong_fact":
            rows[kind].append((m, chunks))
    out = {"support_recall": support_recall, "n_answers": len(res)}
    for name, items in rows.items():
        without = [passes(answer, chunks, support_recall, False) for answer, chunks in items]
        with_polarity = [passes(answer, chunks, support_recall, True) for answer, chunks in items]
        bootstrap = paired_bootstrap_ci(without, with_polarity) if items else None
        out[name] = {
            "n": len(items),
            "pass_rate_without_polarity": round(sum(without) / max(1, len(items)), 3),
            "pass_rate_with_polarity": round(sum(with_polarity) / max(1, len(items)), 3),
            "bootstrap_95_ci": bootstrap,
        }
    out["bootstrap_method"] = {
        "method": "paired percentile bootstrap over source questions",
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
        "paired_change": "polarity-enabled pass rate minus baseline pass rate",
        "limitations": (
            "Intervals describe resampling uncertainty in this small, author-labeled set; "
            "they do not make its labels independent or representative."
        ),
    }
    return out


def main():
    with Path("data/evaluation/faithfulness_results.json").open(encoding="utf-8") as source:
        res = json.load(source)["results"]
    with Path("data/evaluation/p2_gate_contexts.json").open(encoding="utf-8") as source:
        contexts = json.load(source)["contexts"]
    with Path("reports/gate_labeled_eval.json").open(encoding="utf-8") as source:
        support_recall = json.load(source)["chosen_support_recall"]

    report = evaluate(res, contexts, support_recall)
    Path("reports/polarity_eval.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
