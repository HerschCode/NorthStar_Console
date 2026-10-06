"""
What each classifier backend actually does inside the gateway: the real pre-flight path (PII redaction, normalisation, rules with cipher readings, then the classifier layer),
driven by the held-out sets, with block-on-any exactly as shipped. This is the table that answers "does the student improve the gateway", as opposed to guard_student.py's
"is the student a good classifier".

Configurations:
  rules only                      GATEWAY_LITE (the rule layer and its cipher readings)
  shipped                         rules + the numpy classifier at 0.5 (the default)
  rules + student (selected)      rules + the student threshold recorded in meta.json
  rules + student (0.5)           the same weights at a probability threshold of 0.5
  rules + student (validation)    the threshold fixed to at most 1% false positives on validation rows
  rules + numpy + student         CLASSIFIER_BACKEND=both
  rules OR teacher                rules, or ProtectAI deberta-v3-base at 0.5 (the 700 MB model this work is measured against; a decision combined with ours, as in docs/guard-baselines.md)

Every system is scored on every held-out set with Wilson 95% intervals, per-set and macro-averaged (so the 2,060-row safeguard test does not decide the headline), plus single-request p50 latency of
the detection ensemble. A caution that applies to all rows: the training sources' own test splits (deepset, safeguard, jackhhao, gandalf) are in-distribution for the shipped classifier and the
student alike; the own corpus, jbb_benign, the short and in-domain sets and the two new benign sets are not, and docs/guard-student.md says which is which.

    python -X utf8 -m scripts.guard_student_ensemble [--student-dir data/external/guard_student/onnx/<run>/shippable]

Writes reports/p3_guard_student_gateway.json. Needs the GPU interpreter only for the cached teacher scores.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

REPORT = REPO_ROOT / "reports" / "p3_guard_student_gateway.json"
LATENCY_SAMPLE = 300


def build(backend: str, lite: bool = False):
    from gateway import middleware
    middleware.CLASSIFIER_BACKEND = backend
    middleware.LITE_MODE = lite
    mw = middleware.GatewayMiddleware()
    from gateway.logging_schema import GatewayLogger
    mw.logger = GatewayLogger(REPO_ROOT / "logs" / "ensemble_eval.jsonl")
    return mw


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--student-dir", help="a staged export (student.int8.onnx, tokenizer.json, meta.json); default models/guard_student")
    args = ap.parse_args()
    if args.student_dir:
        os.environ["STUDENT_MODEL_DIR"] = str(Path(args.student_dir).resolve())

    from gateway.pii import scan_and_redact
    from gateway.text_normalizer import normalize
    from scripts import guard_student as gs

    held = gs.data()["held"]
    sets = {k: ([t for t, _ in v], np.array([y for _, y in v])) for k, v in held.items()}
    prepared = {k: [normalize(scan_and_redact(t).redacted_text) for t in texts] for k, (texts, _) in sets.items()}

    rules_only = build("numpy", lite=True)
    shipped = build("numpy")
    student = build("student")                                                    # operating point recorded in meta.json
    at_half = build("student")
    at_half.student_detector.threshold = 0.5
    conservative = build("student")
    meta = json.loads((Path(os.environ.get("STUDENT_MODEL_DIR") or REPO_ROOT / "models" / "guard_student") / "meta.json").read_text(encoding="utf-8"))
    conservative_margin = meta.get("conservative_threshold_margin", meta["threshold_margin"])
    conservative.student_detector.threshold = 1.0 / (1.0 + float(np.exp(-conservative_margin)))
    both = build("both")
    configs = {"rules only": rules_only, "shipped (rules + numpy classifier)": shipped,
               "rules + student (selected)": student, "rules + student (0.5)": at_half,
               "rules + student (validation)": conservative, "rules + numpy + student": both}

    def run(mw, set_name):
        return np.array([bool(mw._run_injection_ensemble(w, f"ens-{set_name}-{i}")[0]) for i, w in enumerate(prepared[set_name])])

    results = {"student_dir": os.environ.get("STUDENT_MODEL_DIR", "models/guard_student"), "held_out_sets": {k: {"n": len(v[0]), "attacks": int(v[1].sum())} for k, v in sets.items()}, "systems": {}}
    rule_flags = {k: run(rules_only, k) for k in sets}
    all_flags = {name: {k: run(mw, k) for k in sets} for name, mw in configs.items()}
    teacher_flags = {k: rule_flags[k] | (gs.teacher_margins(sets[k][0], 512) >= 0.0) for k in sets}
    all_flags["rules OR teacher (ProtectAI deberta-v3-base)"] = teacher_flags

    pool = [w for k in sets for w in prepared[k]]
    sample = [pool[i] for i in np.random.default_rng(0).choice(len(pool), LATENCY_SAMPLE, replace=False)]
    latency = {}
    for name, mw in configs.items():
        lat = []
        for i, w in enumerate(sample):
            t0 = time.perf_counter()
            mw._run_injection_ensemble(w, f"lat-{i}")
            lat.append((time.perf_counter() - t0) * 1000)
        latency[name] = round(float(np.median(lat)), 3)

    for name, per_set in all_flags.items():
        entry = {"p50_ensemble_latency_ms": latency.get(name), "sets": {k: gs.metrics(np.where(per_set[k], 1.0, -1.0), sets[k][1], 0.0) for k in sets}}
        det = [m["detection"]["rate"] for m in entry["sets"].values() if "detection" in m]
        fpr = [m["fpr"]["rate"] for m in entry["sets"].values() if "fpr" in m]
        entry["macro"] = {"mean_detection_over_sets": round(float(np.mean(det)), 3), "mean_fpr_over_sets": round(float(np.mean(fpr)), 3)}
        results["systems"][name] = entry
    REPORT.write_text(json.dumps(results, indent=2), encoding="utf-8")

    names = list(sets)
    print(f"\n{'system':<46}" + "".join(f"{n[:12]:>13}" for n in names) + "   macro det / FPR   p50 ms")
    for name, e in results["systems"].items():
        cells = []
        for n in names:
            m = e["sets"][n]
            cells.append(f"{m['detection']['rate']:.2f}" if "detection" in m else f"f{m['fpr']['rate']:.3f}")
        mc = e["macro"]
        print(f"{name[:45]:<46}" + "".join(f"{c:>13}" for c in cells) + f"   {mc['mean_detection_over_sets']:.3f} / {mc['mean_fpr_over_sets']:.3f}   {e['p50_ensemble_latency_ms']}")
    print(f"\nWrote {REPORT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
