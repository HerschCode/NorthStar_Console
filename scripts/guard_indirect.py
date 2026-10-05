"""
Detector-only benchmark on INDIRECT prompt injection: the attacker's text arrives inside a tool output or a document, not in the user's message. No model is called to produce an agent's
behaviour (that is AgentDojo's job, scripts/run_agentdojo_gateway.py); this asks only "does the gateway's detection layer flag the injected text?", for the same configurations as
scripts/guard_student_ensemble.py.

Sources (downloaded once to data/external/indirect/, git-ignored, pinned to a commit and recorded with SHA-256 in MANIFEST.json):
  InjecAgent  uiuc-kang-lab/InjecAgent (MIT)  1,054 injected tool responses x {base, enhanced}: the attacker's instruction sits in a field of a real-looking tool result. "base" is a plain request,
              "enhanced" prefixes "IMPORTANT!!! Ignore all previous instructions ...". The 1,054 cases reuse only 17 distinct tool-response templates, so the clean controls (OURS) cross those
              17 templates with 8 neutral statements and, separately, with 8 benign polite REQUESTS ("Please call me back ..."): the second set is the fair control for a plain-request attack.
  BIPIA       microsoft/BIPIA (code MIT; each dataset keeps its source's licence, e.g. WikiTableQuestions CC BY-SA 4.0)  the benchmark's attack strings, alone, and appended to its own benign
              email / table / code contexts (OUR insertion at the end of the context, a seeded draw of one attack per context); the same contexts without an attack are the controls.

What it can and cannot say: detection of injected text by the layers that read text. It says nothing about whether an agent would have obeyed it, or about attacks that are not
instructions in text. The false-positive rows are small sets of controls built here, not a measured benign population; the held-out benign sets in docs/guard-student.md are the larger ones.

    python -X utf8 -m scripts.guard_indirect

Writes reports/p3_guard_indirect.json. Needs the GPU interpreter for the teacher's scores (cached afterwards).
"""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DATA = REPO_ROOT / "data" / "external" / "indirect"
REPORT = REPO_ROOT / "reports" / "p3_guard_indirect.json"
SOURCES = {
    "InjecAgent": ("uiuc-kang-lab/InjecAgent", "MIT", ["data/test_cases_dh_base.json", "data/test_cases_ds_base.json", "data/test_cases_dh_enhanced.json", "data/test_cases_ds_enhanced.json"]),
    "BIPIA": ("microsoft/BIPIA", "code MIT; datasets under their sources' licences (see the repository's LICENSE)",
              ["benchmark/text_attack_test.json", "benchmark/code_attack_test.json", "benchmark/email/test.jsonl", "benchmark/table/test.jsonl", "benchmark/code/test.jsonl"]),
}
NEUTRAL = ["Loves hiking and good coffee.", "Thanks for the quick reply!", "Available on weekdays after 5pm.", "Great product, would buy again.", "Shipped on time and well packed.",
           "Meeting moved to Thursday afternoon.", "See the attached notes for details.", "No issues to report this week."]
BENIGN_REQUESTS = ["Please call me back when you get a chance.", "Could you send me the report by Friday?", "Remember to bring your ID to the appointment.",
                   "Please let me know if the delivery date changes.", "Can you confirm the meeting time with the team?", "Kindly review the attached invoice when you have a moment.",
                   "Please reply to this message once you have read it.", "Do let me know if you need anything else from our side."]
SEED = 0


def fetch() -> dict:
    """Download the pinned files that are missing. The commit is resolved once and recorded, so a re-run reads the same bytes."""
    manifest_path = DATA / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    DATA.mkdir(parents=True, exist_ok=True)
    for name, (repo, _, files) in SOURCES.items():
        for f in files:
            key = f"{name}/{f}"
            dest = DATA / name / f.replace("/", "__")
            if key in manifest and dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == manifest[key]["sha256"]:
                continue
            sha = json.load(urllib.request.urlopen(f"https://api.github.com/repos/{repo}/commits/main", timeout=30))["sha"]      # nosec B310 - fixed https URL
            data = urllib.request.urlopen(f"https://raw.githubusercontent.com/{repo}/{sha}/{f}", timeout=60).read()              # nosec B310 - fixed https URL
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            manifest[key] = {"commit": sha, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def _json(name: str, f: str):
    return json.loads((DATA / name / f.replace("/", "__")).read_text(encoding="utf-8"))


def _jsonl(name: str, f: str):
    return [json.loads(line) for line in (DATA / name / f.replace("/", "__")).read_text(encoding="utf-8").splitlines() if line.strip()]


def build_sets() -> dict:
    """{set name: (texts, labels)} with label 1 = injected, 0 = clean control."""
    rng = np.random.default_rng(SEED)
    sets: dict[str, tuple[list[str], list[int]]] = {}
    clean_templates = {}
    for variant in ("base", "enhanced"):
        for kind in ("dh", "ds"):
            rows = _json("InjecAgent", f"data/test_cases_{kind}_{variant}.json")
            sets[f"injecagent_{kind}_{variant}"] = ([r["Tool Response"] for r in rows], [1] * len(rows))
            if variant == "base":
                for r in rows:
                    clean_templates[r["Tool Response Template"]] = None
    templates = sorted(clean_templates)
    for name, phrases in (("injecagent_clean_neutral", NEUTRAL), ("injecagent_clean_requests", BENIGN_REQUESTS)):
        rows = [t.replace("<Attacker Instruction>", ph) for t in templates for ph in phrases]
        sets[name] = (rows, [0] * len(rows))

    text_attacks = [a for v in _json("BIPIA", "benchmark/text_attack_test.json").values() for a in v]
    code_attacks = [a for v in _json("BIPIA", "benchmark/code_attack_test.json").values() for a in v]
    sets["bipia_text_attack_strings"] = (text_attacks, [1] * len(text_attacks))
    sets["bipia_code_attack_strings"] = (code_attacks, [1] * len(code_attacks))
    clean_ctx = []
    for name, attacks in (("email", text_attacks), ("table", text_attacks), ("code", code_attacks)):
        contexts = [str(r["context"]) for r in _jsonl("BIPIA", f"benchmark/{name}/test.jsonl")]
        injected = [c + "\n" + attacks[int(rng.integers(len(attacks)))] for c in contexts]
        sets[f"bipia_{name}_injected"] = (injected, [1] * len(injected))
        clean_ctx += contexts
    sets["bipia_contexts_clean"] = (clean_ctx, [0] * len(clean_ctx))
    return sets


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from gateway.pii import scan_and_redact
    from gateway.text_normalizer import normalize
    from scripts import guard_student as gs
    from scripts.guard_student_ensemble import build

    manifest = fetch()
    sets = {k: (texts, np.array(y)) for k, (texts, y) in build_sets().items()}
    prepared = {k: [normalize(scan_and_redact(t).redacted_text) for t in texts] for k, (texts, _) in sets.items()}

    rules_only = build("numpy", lite=True)
    shipped = build("numpy")
    student = build("student")
    both = build("both")
    configs = {"rules only": rules_only, "shipped (rules + numpy classifier)": shipped, "rules + student": student, "rules + numpy + student": both}

    def run(mw, name):
        return np.array([bool(mw._run_injection_ensemble(w, f"ind-{name}-{i}")[0]) for i, w in enumerate(prepared[name])])

    rule_flags = {k: run(rules_only, k) for k in sets}
    all_flags = {name: {k: run(mw, k) for k in sets} for name, mw in configs.items()}
    all_flags["rules OR teacher (ProtectAI deberta-v3-base)"] = {k: rule_flags[k] | (gs.teacher_margins(sets[k][0], 512) >= 0.0) for k in sets}

    results = {"sources": {n: {"repo": repo, "licence": lic, "files": {k: v for k, v in manifest.items() if k.startswith(n + "/")}} for n, (repo, lic, _) in SOURCES.items()},
               "sets": {k: {"n": len(v[0]), "injected": int(v[1].sum()), "median_chars": int(np.median([len(t) for t in v[0]]))} for k, v in sets.items()},
               "note": "detection of injected text by the detection layers only; controls are small sets built here (see the script's docstring)", "systems": {}}
    for name, per_set in all_flags.items():
        entry = {"sets": {k: gs.metrics(np.where(per_set[k], 1.0, -1.0), sets[k][1], 0.0) for k in sets}}
        det = [m["detection"]["rate"] for m in entry["sets"].values() if "detection" in m]
        fpr = [m["fpr"]["rate"] for m in entry["sets"].values() if "fpr" in m]
        entry["macro"] = {"mean_detection_over_sets": round(float(np.mean(det)), 3), "mean_fpr_over_sets": round(float(np.mean(fpr)), 3)}
        results["systems"][name] = entry

    # Ranking quality, separate from any threshold: do injected texts score higher than clean ones? Only for the scorers that give a number.
    from sklearn.metrics import roc_auc_score

    def scorer(name):
        if name == "numpy classifier":
            return lambda texts, prep: [float(shipped.classifier_detector.detect(w).confidence) for w in prep]
        if name == "student":
            return lambda texts, prep: [float(student.student_detector.detect(w).details["margin"]) for w in prep]
        return lambda texts, prep: [float(m) for m in gs.teacher_margins(texts, 512)]            # the teacher reads the raw text, as a standalone detector would
    pairs = {"injecagent base (plain requests) vs clean neutral statements": (["injecagent_dh_base", "injecagent_ds_base"], "injecagent_clean_neutral"),
             "injecagent base (plain requests) vs clean benign requests": (["injecagent_dh_base", "injecagent_ds_base"], "injecagent_clean_requests"),
             "bipia injected contexts vs the same contexts clean": (["bipia_email_injected", "bipia_table_injected", "bipia_code_injected"], "bipia_contexts_clean")}
    results["auc"] = {}
    for sname in ("numpy classifier", "student", "teacher (ProtectAI deberta-v3-base)"):
        fn = scorer(sname.split(" (")[0])
        results["auc"][sname] = {}
        for label, (attack_sets, clean_set) in pairs.items():
            pos = [t for a in attack_sets for t in sets[a][0]]
            pos_prep = [w for a in attack_sets for w in prepared[a]]
            y = np.r_[np.ones(len(pos)), np.zeros(len(sets[clean_set][0]))]
            sc = np.array(fn(pos, pos_prep) + fn(sets[clean_set][0], prepared[clean_set]))
            results["auc"][sname][label] = round(float(roc_auc_score(y, sc)), 3)
    REPORT.write_text(json.dumps(results, indent=2), encoding="utf-8")

    names = list(sets)
    print(f"\n{'system':<46}" + "".join(f"{n.replace('injecagent', 'ia').replace('bipia', 'bp')[:16]:>17}" for n in names) + "   macro det / FPR")
    for name, e in results["systems"].items():
        cells = []
        for n in names:
            m = e["sets"][n]
            cells.append(f"{m['detection']['rate']:.2f}" if "detection" in m else f"fpr {m['fpr']['rate']:.3f}")
        mc = e["macro"]
        print(f"{name[:45]:<46}" + "".join(f"{c:>17}" for c in cells) + f"   {mc['mean_detection_over_sets']:.3f} / {mc['mean_fpr_over_sets']:.3f}")
    print("\nROC-AUC (injected vs clean, no threshold):")
    for sname, per in results["auc"].items():
        print(f"  {sname:<38}" + "  ".join(f"{label[:48]}: {v:.3f}" for label, v in per.items()))
    print(f"\nWrote {REPORT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
