"""
Export the distilled student (scripts/guard_student.py) to ONNX, quantise it to int8, and measure what quantisation costs: file size, memory in a fresh process, single-example
latency, and how far the decisions move from the fp32 PyTorch model on the validation rows and on every held-out set.

The teacher could not be quantised without breaking it (docs/decisions.md, 2026-09-25); the question here is whether a 22M-parameter BERT-style student survives int8, which usually
works (it has no DeBERTa-style relative-position attention) but is measured, not assumed. A variant that breaks the model is reported as broken. The variant to ship is chosen by
rule on the VALIDATION rows only: the smallest model (sizes within 1 MB count as equal, then the higher validation agreement wins) whose validation ROC-AUC is within 0.003 of fp32 and whose validation
decisions at the fp32 operating threshold agree on at least 99% of rows. Held-out sets are reported for every variant but do not drive the choice.

    python -X utf8 -m scripts.guard_student_export --run hard_tkd-seed0 [--ship]

`--ship` copies the chosen int8 model, its tokenizer and a meta.json (operating threshold, input length, provenance) to models/guard_student/ for gateway/detectors/student_onnx.py.
Writes reports/p3_guard_student_onnx.json. ONNX files go to data/external/guard_student/onnx/<run>/ (gitignored). Needs the GPU interpreter's torch/transformers for the export.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import roc_auc_score

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import guard_student as gs

REPORT = REPO_ROOT / "reports" / "p3_guard_student_onnx.json"
SHIP_DIR = REPO_ROOT / "models" / "guard_student"
LATENCY_SAMPLE = 150
MAX_AUC_DROP = 0.003
MIN_VAL_AGREEMENT = 0.99
MAX_EXPORT_DRIFT = 0.05            # logits; fp32 ONNX on CPU vs fp32 PyTorch on GPU differ by ~1e-4 when the export is right


def export_fp32(model, tok, path: Path):
    # Exported from a padded two-sentence example so the trace contains the attention-mask path. (This is hygiene, not a fix: an earlier version of this comment blamed the export for
    # wrong scores on padded batches; the cause was the tokenizer padding silently, see OrtStudent.)
    enc = tok(["a short one", "a considerably longer example text with a good many more tokens in it than the first one"], return_tensors="pt", padding=True)
    model.config.return_dict = True
    torch.onnx.export(
        model, (enc["input_ids"], enc["attention_mask"]), str(path),
        input_names=["input_ids", "attention_mask"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "b", 1: "s"}, "attention_mask": {0: "b", 1: "s"}, "logits": {0: "b"}},
        opset_version=17, do_constant_folding=True, dynamo=False,
    )


def quantize(src: Path, dst: Path, op_types, per_channel=False):
    from onnxruntime.quantization import QuantType, quantize_dynamic
    quantize_dynamic(str(src), str(dst), weight_type=QuantType.QInt8, op_types_to_quantize=op_types, per_channel=per_channel)


class OrtStudent:
    """The model exactly as the gateway runs it: onnxruntime plus the pure-Python WordPiece tokenizer (gateway/detectors/wordpiece.py), never the `tokenizers` package."""

    def __init__(self, path: Path, vocab_txt: Path, threads=1, arena=True):
        import onnxruntime as ort

        from gateway.detectors.wordpiece import WordPieceTokenizer
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.enable_cpu_mem_arena = arena
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.tok = WordPieceTokenizer.from_file(vocab_txt)

    def margins(self, texts, batch_size=32):
        order = np.argsort([len(t) for t in texts])
        out = np.zeros(len(texts), dtype=np.float32)
        for i in range(0, len(texts), batch_size):
            ids_ = order[i:i + batch_size]
            encs = [self.tok.encode(texts[j], gs.MAX_LEN) for j in ids_]
            width = max(len(e) for e in encs)
            ids = np.zeros((len(encs), width), dtype=np.int64)
            mask = np.zeros((len(encs), width), dtype=np.int64)
            for j, e in enumerate(encs):
                ids[j, :len(e)] = e
                mask[j, :len(e)] = 1
            logits = self.sess.run(None, {"input_ids": ids, "attention_mask": mask})[0]
            out[ids_] = logits[:, 1] - logits[:, 0]
        return out


CHILD = r"""
import json, sys, time
import numpy as np, psutil
cfg = json.loads(sys.argv[1])
p = psutil.Process()
import onnxruntime as ort
sys.path.insert(0, cfg["repo"])
from gateway.detectors.wordpiece import WordPieceTokenizer
base = p.memory_info().rss
so = ort.SessionOptions()
so.intra_op_num_threads = cfg["threads"]
so.enable_cpu_mem_arena = cfg["arena"]
sess = ort.InferenceSession(cfg["model"], so, providers=["CPUExecutionProvider"])
tok = WordPieceTokenizer.from_file(cfg["tokenizer"])
texts = json.load(open(cfg["texts"], encoding="utf-8"))
def run(t):
    ids = np.array([tok.encode(t, cfg["max_len"])], dtype=np.int64)
    sess.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids)})
run(texts[0])
loaded = p.memory_info().rss
lat, peak = [], loaded
for t in texts:
    t0 = time.perf_counter(); run(t); lat.append((time.perf_counter() - t0) * 1000)
    peak = max(peak, p.memory_info().rss)
print(json.dumps({"rss_after_imports_mb": base / 2**20, "rss_after_load_mb": loaded / 2**20, "rss_peak_mb": peak / 2**20,
                  "p50_ms": float(np.median(lat)), "p95_ms": float(np.percentile(lat, 95))}))
"""


def child_runtime(model: Path, tokenizer: Path, texts_file: Path, threads: int, arena: bool) -> dict:
    cfg = json.dumps({"model": str(model), "tokenizer": str(tokenizer), "texts": str(texts_file), "max_len": gs.MAX_LEN, "threads": threads, "arena": arena, "repo": str(REPO_ROOT)})
    out = subprocess.check_output([sys.executable, "-c", CHILD, cfg], text=True)          # nosec B603 - fixed argv, no shell
    return {k: round(v, 1) for k, v in json.loads(out.strip().splitlines()[-1]).items()}


def write_shippable(dest: Path, vocab_txt: Path, run: str, run_dir: Path, variant: str, thr: float, fp32_auc: float, int8_auc: float):
    """vocab.txt (the serving tokenizer is gateway/detectors/wordpiece.py) and meta.json next to student.int8.onnx."""
    shutil.copy2(vocab_txt, dest / "vocab.txt")
    train_log = json.loads((run_dir / "train_log.json").read_text(encoding="utf-8"))
    meta = {"run": run, "variant": variant, "max_length": gs.MAX_LEN, "threshold_margin": 0.0,
            "threshold_selection": "probability 0.5, the convention of every detector layer here. Chosen over the stricter threshold below on the leave-one-source-out folds (development data): for the "
                                   "teacher-distilled variants 0.5 already gives about 1% false positives on a source never seen in training, and the stricter threshold only costs ~9 points of detection",
            "conservative_threshold_margin": round(thr, 3),
            "conservative_threshold_selection": f"smallest margin with <= {gs.TARGET_VAL_FPR:.0%} false positives on the in-distribution validation rows",
            "base_model": gs.BASE_MODEL, "teacher": "protectai/deberta-v3-base-prompt-injection-v2", "train": train_log["args"], "n_labelled": train_log["n_labelled"],
            "n_transfer": train_log["n_transfer"], "fp32_val_auc": round(fp32_auc, 4), "int8_val_auc": int8_auc,
            "tokenizer": "BERT WordPiece, uncased, pure Python (gateway/detectors/wordpiece.py); identical ids to the Hugging Face tokenizer on every project text that contains no special-token literal"}
    (dest / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def main():
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="hard_tkd-seed0")
    ap.add_argument("--ship", action="store_true", help="copy the chosen int8 model to models/guard_student/")
    args = ap.parse_args()
    run_dir = gs.RUNS / args.run
    out_dir = gs.CACHE / "onnx" / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(run_dir)  # nosec B615 - a local training-run directory, not a Hub download
    model = AutoModelForSequenceClassification.from_pretrained(run_dir).eval()  # nosec B615 - local directory
    tok.save_pretrained(out_dir)
    tokenizer_json = out_dir / "tokenizer.json"
    vocab = json.loads(tokenizer_json.read_text(encoding="utf-8"))["model"]["vocab"]
    assert sorted(vocab.values()) == list(range(len(vocab))), "the vocabulary ids are not contiguous"
    vocab_txt = out_dir / "vocab.txt"
    vocab_txt.write_text("\n".join(sorted(vocab, key=vocab.get)) + "\n", encoding="utf-8")
    fp32 = out_dir / "student_fp32.onnx"
    export_fp32(model, tok, fp32)
    variants = {"fp32-ort": fp32}
    for name, kw in (("int8-matmul", {"op_types": ["MatMul"]}), ("int8-matmul-perchannel", {"op_types": ["MatMul"], "per_channel": True}),
                     ("int8-matmul-gather", {"op_types": ["MatMul", "Gather"]}), ("int8-matmul-gather-perchannel", {"op_types": ["MatMul", "Gather"], "per_channel": True})):
        dst = out_dir / f"student_{name}.onnx"
        try:
            quantize(fp32, dst, **kw)
            variants[name] = dst
        except Exception as exc:  # noqa: BLE001 -- a variant that fails to build is reported, not hidden
            variants[name] = ("FAILED", f"{type(exc).__name__}: {str(exc)[:200]}")

    d = gs.data()
    val_texts, val_y = [t for t, _ in d["val"]], np.array([label for _, label in d["val"]])
    # Tokenizer parity gate: the serving tokenizer (pure Python) must produce the Hugging Face tokenizer's ids for every text this project has. Texts that read like a special token
    # ("[SEP]") are excluded on purpose: the reference tokenizer turns them into the real token, the serving one does not (that is a safety choice, see wordpiece.py).
    from gateway.detectors.wordpiece import WordPieceTokenizer
    mine = WordPieceTokenizer.from_file(vocab_txt)
    every = [t for t, _ in d["train"]] + val_texts + d["transfer"] + [t for v in d["held"].values() for t, _ in v]
    specials = ("[CLS]", "[SEP]", "[PAD]", "[MASK]", "[UNK]")
    checked = [t for t in every if not any(sp in t for sp in specials)]
    differ = [t for t in checked if mine.encode(t, gs.MAX_LEN) != tok(t, truncation=True, max_length=gs.MAX_LEN)["input_ids"]]
    print(f"tokenizer parity gate: {len(checked) - len(differ)} of {len(checked)} texts get identical ids ({len(every) - len(checked)} skipped: they contain a special-token literal)")
    if differ:
        sys.exit(f"the serving tokenizer disagrees with the reference on {len(differ)} texts, e.g. {differ[0][:80]!r}")
    torch_ref = lambda texts: gs.student_margins(model.to(gs.DEVICE), tok, texts)
    ref_val = torch_ref(val_texts)
    # Parity gate: the fp32 ONNX model must reproduce the PyTorch model before anything else about it is believed. Padded batches (the validation set is scored in batches) and single
    # examples (how the gateway calls it) are both checked.
    exported = OrtStudent(fp32, vocab_txt)
    drift = float(np.abs(exported.margins(val_texts) - ref_val).max())
    single = float(max(abs(exported.margins([t])[0] - ref_val[i]) for i, t in enumerate(val_texts[:40])))
    print(f"parity gate: fp32 ONNX vs PyTorch, max |margin difference| {drift:.4f} (batched, validation rows), {single:.4f} (single examples)")
    if drift > MAX_EXPORT_DRIFT or single > MAX_EXPORT_DRIFT:
        sys.exit(f"the fp32 ONNX export does not reproduce the PyTorch model (limit {MAX_EXPORT_DRIFT}); fix the export before measuring anything")
    thr = gs.val_threshold(ref_val, val_y)
    ref_val_auc = float(roc_auc_score(val_y, ref_val))
    ref_held = {k: torch_ref([t for t, _ in v]) for k, v in d["held"].items()}
    held_y = {k: np.array([label for _, label in v]) for k, v in d["held"].items()}

    pool = [t for v in d["held"].values() for t, _ in v]
    rng = np.random.default_rng(0)
    sample = [pool[i] for i in rng.choice(len(pool), LATENCY_SAMPLE, replace=False)]
    texts_file = out_dir / "latency_sample.json"
    texts_file.write_text(json.dumps(sample), encoding="utf-8")

    results = {"run": args.run, "fp32_threshold_margin": round(thr, 3), "fp32_val_auc": round(ref_val_auc, 4),
               "reference_torch_fp32": {"sets": {k: {"at_0.5": gs.metrics(ref_held[k], held_y[k], 0.0), "at_val_threshold": gs.metrics(ref_held[k], held_y[k], thr)} for k in ref_held}},
               "selection_rule": f"smallest file (sizes within 1 MB tie; then higher validation agreement) with validation ROC-AUC within {MAX_AUC_DROP} of fp32 and >= {MIN_VAL_AGREEMENT:.0%} validation decision agreement at the fp32 threshold",
               "variants": {}}
    for name, path in variants.items():
        if isinstance(path, tuple):
            results["variants"][name] = {"status": "failed", "reason": path[1]}
            print(f"{name}: FAILED {path[1]}")
            continue
        s = OrtStudent(path, vocab_txt)
        vm = s.margins(val_texts)
        entry = {"status": "ok", "size_mb": round(path.stat().st_size / 2**20, 1),
                 "val_auc": round(float(roc_auc_score(val_y, vm)), 4), "val_decision_agreement_at_threshold": round(float(((vm >= thr) == (ref_val >= thr)).mean()), 4),
                 "val_max_abs_margin_diff": round(float(np.abs(vm - ref_val).max()), 3), "sets": {}}
        for k in d["held"]:
            m = s.margins([t for t, _ in d["held"][k]])
            entry["sets"][k] = {"at_0.5": gs.metrics(m, held_y[k], 0.0), "at_val_threshold": gs.metrics(m, held_y[k], thr),
                                "decision_agreement_at_0.5": round(float(((m >= 0) == (ref_held[k] >= 0)).mean()), 4)}
            if k in gs.SETS_WITH_BOTH:
                entry["sets"][k]["roc_auc"] = round(float(roc_auc_score(held_y[k], m)), 3)
        entry["runtime_1thread"] = child_runtime(path, vocab_txt, texts_file, threads=1, arena=True)
        entry["runtime_1thread_no_arena"] = child_runtime(path, vocab_txt, texts_file, threads=1, arena=False)
        entry["runtime_4threads"] = child_runtime(path, vocab_txt, texts_file, threads=4, arena=True)
        results["variants"][name] = entry
        print(f"{name:<30} {entry['size_mb']:>6} MB  val AUC {entry['val_auc']}  val agreement {entry['val_decision_agreement_at_threshold']}  "
              f"RSS(1 thread) {entry['runtime_1thread']['rss_peak_mb']} MB  p50 {entry['runtime_1thread']['p50_ms']} ms", flush=True)

    ok = {n: e for n, e in results["variants"].items() if e.get("status") == "ok" and n != "fp32-ort"}
    eligible = {n: e for n, e in ok.items() if e["val_auc"] >= ref_val_auc - MAX_AUC_DROP and e["val_decision_agreement_at_threshold"] >= MIN_VAL_AGREEMENT}
    chosen = min(eligible, key=lambda n: (round(eligible[n]["size_mb"]), -eligible[n]["val_decision_agreement_at_threshold"], eligible[n]["size_mb"])) if eligible else None
    results["chosen_variant"] = chosen
    REPORT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nchosen by rule: {chosen}  (eligible: {sorted(eligible)}; broken or too lossy: {sorted(set(ok) - set(eligible))})")
    print(f"Wrote {REPORT.relative_to(REPO_ROOT)}")

    if chosen:
        stage_dir = out_dir / "shippable"
        stage_dir.mkdir(exist_ok=True)
        shutil.copy2(variants[chosen], stage_dir / "student.int8.onnx")
        write_shippable(stage_dir, vocab_txt, args.run, run_dir, chosen, thr, ref_val_auc, ok[chosen]["val_auc"])
    if args.ship:
        if not chosen:
            sys.exit("no variant met the selection rule; nothing shipped")
        SHIP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(variants[chosen], SHIP_DIR / "student.int8.onnx")
        write_shippable(SHIP_DIR, vocab_txt, args.run, run_dir, chosen, thr, ref_val_auc, ok[chosen]["val_auc"])
        print(f"shipped {chosen} -> {SHIP_DIR.relative_to(REPO_ROOT)} (add it to models/MANIFEST.sha256)")


if __name__ == "__main__":
    main()
