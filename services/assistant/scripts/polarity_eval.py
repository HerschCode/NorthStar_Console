"""Does the polarity check help? Offline, no model, no key: frozen retrieval contexts + the 32 hand-checked answers + the deterministic
mutations from scripts/gate_labeled_eval.py. Compares the claim-support gate with and without polarity, at the chosen recall threshold.
Run: python -m scripts.polarity_eval  -> reports/polarity_eval.json"""
import json
import re
from pathlib import Path

from scripts.gate_labeled_eval import mutate
from src.evaluation.claim_support import split_sentences, sentence_support


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


def main():
    res = json.load(open("data/evaluation/faithfulness_results.json", encoding="utf-8"))["results"]
    ctx = json.load(open("data/evaluation/p2_gate_contexts.json", encoding="utf-8"))["contexts"]
    r = json.load(open("reports/gate_labeled_eval.json", encoding="utf-8"))["chosen_support_recall"]
    rows = {"correct": [], "wrong_fact": [], "wrong_fact_polarity_word": [], "wrong_fact_leading_yes_no": []}
    for x in res:
        a, chunks = x["answer"], ctx[str(x["id"])]
        m = mutate(a)
        kind = "wrong_fact_leading_yes_no" if re.match(r"^(Yes|No)\.", a) and m != a and m[:3] != a[:3] else (
            "wrong_fact_polarity_word" if re.search(r"included|excluded|does not count|counts", m) and m != a else "wrong_fact")
        rows["correct"].append((a, chunks))
        rows["wrong_fact"].append((m, chunks))
        if kind != "wrong_fact":
            rows[kind].append((m, chunks))
    out = {"support_recall": r, "n_answers": len(res)}
    for name, items in rows.items():
        out[name] = {"n": len(items), **{k: round(sum(passes(a, c, r, p) for a, c in items) / max(1, len(items)), 3)
                                         for k, p in (("pass_rate_without_polarity", False), ("pass_rate_with_polarity", True))}}
    Path("reports/polarity_eval.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
