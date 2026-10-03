"""
Distil a prompt-injection guard that fits the 512 MB tier: teacher protectai/deberta-v3-base-prompt-injection-v2 (184M parameters, ~700 MB, +400-860 MB RSS)
into a small student (MiniLM-L6, 22M parameters) that scripts/guard_student_export.py turns into an ONNX int8 model.

Why not just quantise the teacher: that was tried (docs/decisions.md, 2026-09-25): int8 broke it (own-corpus detection 81% -> 10-12%) and the best repaired variants still
needed 534-612 MB RSS. A 22M-parameter student can fit with room to spare; whether it keeps the detection is what this measures.

Method, fixed before any held-out set was looked at, and the reasons:
  * LABELLED rows are the shipped classifier's own (scripts/retrain_classifier_v2.make_train_rows, config E): the same decontaminated pool (a row that appears in ANY
    held-out set is dropped) and the same 90/10 split, so the student and the shipped default see identical labelled data. Validation rows are used for early stopping and to
    fix the operating threshold (the smallest threshold with at most 1% false positives on validation benign rows): never a held-out set.
  * TRANSFER rows are text without a trusted label (benign prompts from oasst1 and awesome-chatgpt-prompts). The teacher's soft label is the only target there. First result
    (docs/guard-student.md): distilling from the teacher on the LABELLED rows made the student worse, because the teacher is wrong on part of that data (it detects 37% of deepset's
    attacks); so hard labels stay authoritative where they exist and the teacher only fills in where there is nothing else.
  * Variants are chosen by leave-one-source-out (train without deepset / safeguard / jackhhao / gandalf, test on that source's test split), the project's existing practice, and not by
    looking at the held-out sets or the author-written own corpus.
  * The teacher is scored on the same held-out sets. It was trained on public data that includes jackhhao/jailbreak-classification, so its jackhhao and jailbreak_llms numbers can
    be inflated by memorisation; that is stated wherever they appear.

    python -X utf8 scripts/guard_student.py train --variant hard_tkd --seed 0
    python -X utf8 scripts/guard_student.py loso --variants hard hard_tkd hardkd      # selection by leave-one-source-out
    python -X utf8 scripts/guard_student.py report                                     # every trained run vs the shipped default and the teacher -> reports/p3_guard_student.json

Needs the GPU interpreter (torch + CUDA, transformers); not used by gateway/ or CI.
"""
import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import retrain_classifier_v2 as rc
from scripts.baselines.run_guard_baselines import (
    MODELS,
    load_guard,
    wilson,
)

CACHE = REPO_ROOT / "data" / "external" / "guard_student"
EXTRA = CACHE / "extra"
RUNS = CACHE / "runs"
REPORT = REPO_ROOT / "reports" / "p3_guard_student.json"
LOSO_REPORT = REPO_ROOT / "reports" / "p3_guard_student_loso.json"
BASE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
BASE_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"  # the snapshot the shipped student was trained from
MAX_LEN = 256
SETS_WITH_BOTH = ("own_corpus", "deepset_test", "safeguard_test", "jackhhao_test")         # ROC-AUC needs both classes
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TARGET_VAL_FPR = 0.01
LABELLED_SOURCES = {"deepset_train": "deepset_test", "safeguard": "safeguard_test", "jackhhao": "jackhhao_test", "gandalf": "gandalf_test"}
N_TRANSFER_OASST = 3000


# ---- data -----------------------------------------------------------------------------------------------------------------

def _is_english_prompt(t: str) -> bool:
    return 15 <= len(t) <= 1200 and sum(c.isascii() for c in t) / max(1, len(t)) > 0.97


def extra_benign():
    """oasst1 English first-turn prompts and awesome-chatgpt-prompts persona prompts, each split into transfer-train text and a held-out benign set."""
    oa = pd.read_parquet(next((EXTRA / "oasst1").rglob("*.parquet")))
    oa = oa[(oa.role == "prompter") & (oa.lang == "en") & oa.parent_id.isna() & (~oa.deleted)]
    oasst = sorted({t.strip() for t in oa.text if _is_english_prompt(t)})
    persona = sorted({t.strip() for t in pd.read_csv(next((EXTRA / "personas").rglob("prompts.csv")))["prompt"].dropna() if 15 <= len(t) <= 1200})
    r = random.Random(1)
    r.shuffle(oasst)
    r.shuffle(persona)
    n_oa = min(N_TRANSFER_OASST, len(oasst) - 500)
    cut_p = len(persona) // 2
    return {"oasst_train": oasst[:n_oa], "oasst_heldout": oasst[n_oa:n_oa + 500], "persona_train": persona[:cut_p], "persona_heldout": persona[cut_p:]}


def data(exclude=None):
    """Labelled train/val rows (config E, optionally without one source), transfer rows, and every held-out set (the original ones plus two new benign sets)."""
    train, held = rc.load_sources()
    rc.CONFIGS["E-loso"] = [k for k in rc.CONFIGS["E"] if k != exclude]
    rows, dropped_overlap, dropped_conflict = rc.make_train_rows(train, held, "E-loso")
    rng = random.Random(0)                                    # the split train_model() uses for the shipped classifier
    shuffled = rows[:]
    rng.shuffle(shuffled)
    cut = int(len(shuffled) * 0.9)
    extra = extra_benign()
    held = dict(held)
    held["oasst_benign_heldout"] = [(t, 0) for t in extra["oasst_heldout"]]
    held["persona_benign_heldout"] = [(t, 0) for t in extra["persona_heldout"]]
    held_norm = {rc.norm(t) for v in held.values() for t, _ in v}

    def keep(items):
        return [(t, y) for t, y in items if rc.norm(t) not in held_norm]

    transfer, seen = [], {rc.norm(t) for t, _ in shuffled}
    for t in extra["oasst_train"] + extra["persona_train"]:
        n = rc.norm(t)
        if n and n not in held_norm and n not in seen:
            seen.add(n)
            transfer.append(t)
    return {"train": keep(shuffled[:cut]), "val": keep(shuffled[cut:]), "transfer": transfer, "held": held,
            "dropped_overlap": dropped_overlap, "dropped_conflict": dropped_conflict}


# ---- teacher --------------------------------------------------------------------------------------------------------------

def _h(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()  # cache key


_TEACHER = None


def _teacher():
    global _TEACHER
    if _TEACHER is None:
        tok, model, idx, _ = load_guard(MODELS[0])           # verifies the attack output index on ten known examples before anything is scored
        model = model.to(DEVICE)
        _TEACHER = (tok, (model.half() if DEVICE == "cuda" else model).eval(), idx)
    return _TEACHER


@torch.no_grad()
def teacher_margins(texts, max_length, batch_size=32):
    """attack-vs-benign logit margin of the teacher, cached per text so any subset is free after the first pass."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"teacher-{max_length}.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    missing = sorted({t for t in texts if _h(t) not in store}, key=len)
    if missing:
        tok, model, idx = _teacher()
        for i in range(0, len(missing), batch_size):
            chunk = missing[i:i + batch_size]
            enc = tok(chunk, return_tensors="pt", truncation=True, padding=True, max_length=max_length).to(DEVICE)
            logits = model(**enc).logits.float()
            margin = (logits[:, idx] - logits[:, 1 - idx]).cpu().numpy()
            for t, m in zip(chunk, margin):
                store[_h(t)] = float(m)
        path.write_text(json.dumps(store), encoding="utf-8")
    return np.array([store[_h(t)] for t in texts], dtype=np.float32)


# ---- student --------------------------------------------------------------------------------------------------------------

def collate(tok, texts, max_length):
    return tok(texts, return_tensors="pt", truncation=True, padding=True, max_length=max_length)


@torch.no_grad()
def student_margins(model, tok, texts, max_length=MAX_LEN, batch_size=64):
    model.eval()
    order = np.argsort([len(t) for t in texts])
    out = np.zeros(len(texts), dtype=np.float32)
    for i in range(0, len(texts), batch_size):
        ids = order[i:i + batch_size]
        enc = collate(tok, [texts[j] for j in ids], max_length).to(DEVICE)
        logits = model(**enc).logits.float()
        out[ids] = (logits[:, 1] - logits[:, 0]).cpu().numpy()
    return out


def train_student(variant, seed, d, epochs=4, batch=32, lr=5e-5, temperature=2.0, alpha=0.5, beta=1.0, log=print):
    """Rows are (text, label|None). `hard`: labelled rows only, cross-entropy. `hardkd`: labelled rows, alpha*CE + (1-alpha)*teacher match.
    `hard_tkd`: labelled rows with CE, plus the transfer rows with the teacher's soft label (weight beta). `kd`: teacher match on labelled rows only. `hardkd_tkd`: hardkd on the labelled rows plus the teacher on the transfer rows."""
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        get_linear_schedule_with_warmup,
    )
    rows = [(t, float(y)) for t, y in d["train"]] + ([(t, None) for t in d["transfer"]] if variant in ("hard_tkd", "hardkd_tkd") else [])
    texts = [t for t, _ in rows]
    y = torch.tensor([0.0 if label is None else label for _, label in rows])
    labelled = torch.tensor([label is not None for _, label in rows])
    tm = torch.tensor(teacher_margins(texts, MAX_LEN))
    val_texts, val_y = [t for t, _ in d["val"]], np.array([label for _, label in d["val"]])

    torch.manual_seed(seed)
    random.seed(seed)
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=BASE_MODEL_REVISION)
    model = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL, revision=BASE_MODEL_REVISION, num_labels=2).to(DEVICE)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps = epochs * ((len(texts) + batch - 1) // batch)
    sched = get_linear_schedule_with_warmup(opt, int(0.06 * steps), steps)
    scaler = torch.amp.GradScaler(enabled=DEVICE == "cuda")
    T = temperature
    best, best_state, history = -1.0, None, []

    def loss_fn(margin, yy, tmm, lab):
        hard = F.binary_cross_entropy_with_logits(margin, yy, reduction="none")
        soft = F.binary_cross_entropy_with_logits(margin / T, torch.sigmoid(tmm / T), reduction="none") * T * T
        if variant == "hard":
            per = hard
        elif variant == "kd":
            per = soft
        elif variant == "hardkd":
            per = alpha * hard + (1 - alpha) * soft
        elif variant == "hardkd_tkd":
            per = torch.where(lab, alpha * hard + (1 - alpha) * soft, beta * soft)
        else:                                                      # hard_tkd
            per = torch.where(lab, hard, beta * soft)
        return per.mean()

    g = torch.Generator().manual_seed(seed)
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(len(texts), generator=g).tolist()
        total = 0.0
        for i in range(0, len(perm), batch):
            b = perm[i:i + batch]
            enc = collate(tok, [texts[j] for j in b], MAX_LEN).to(DEVICE)
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=DEVICE == "cuda"):
                logits = model(**enc).logits.float()
            loss = loss_fn(logits[:, 1] - logits[:, 0], y[b].to(DEVICE), tm[b].to(DEVICE), labelled[b].to(DEVICE))
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            total += loss.item() * len(b)
        auc = float(roc_auc_score(val_y, student_margins(model, tok, val_texts)))
        history.append({"epoch": epoch + 1, "train_loss": round(total / len(texts), 4), "val_auc": round(auc, 4)})
        log(f"  {variant} seed{seed} epoch {epoch + 1}: train loss {history[-1]['train_loss']}, val AUC {auc:.4f}")
        if auc > best:
            best, best_state = auc, {k: v.detach().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    return model, tok, {"epochs": history, "best_val_auc": round(best, 4), "n_labelled": int(labelled.sum()), "n_transfer": int((~labelled).sum())}


def cmd_train(args):
    d = data()
    model, tok, info = train_student(args.variant, args.seed, d, args.epochs, args.batch, args.lr, args.temperature, args.alpha, args.beta)
    run = RUNS / f"{args.variant}-seed{args.seed}"
    run.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(run)
    tok.save_pretrained(run)
    info.update(args={k: v for k, v in vars(args).items() if k != "fn"}, n_val=len(d["val"]), dropped_overlap=d["dropped_overlap"], dropped_conflict=d["dropped_conflict"])
    (run / "train_log.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"saved {run} (best val AUC {info['best_val_auc']}; {info['n_labelled']} labelled + {info['n_transfer']} transfer rows)")


# ---- evaluation -----------------------------------------------------------------------------------------------------------

def metrics(margin, y, threshold_margin):
    blocked = margin >= threshold_margin
    out = {}
    if (y == 1).any():
        k, n = int(blocked[y == 1].sum()), int((y == 1).sum())
        out["detection"] = {"k": k, "n": n, "rate": round(k / n, 3), "ci95": wilson(k, n)}
    if (y == 0).any():
        k, n = int(blocked[y == 0].sum()), int((y == 0).sum())
        out["fpr"] = {"k": k, "n": n, "rate": round(k / n, 3), "ci95": wilson(k, n)}
    return out


def val_threshold(val_margin, val_y, target_fpr=TARGET_VAL_FPR):
    """The smallest margin whose false-positive rate on the validation benign rows is at most `target_fpr`."""
    benign = np.sort(val_margin[val_y == 0])[::-1]
    allowed = int(np.floor(target_fpr * len(benign)))
    return float(benign[allowed]) + 1e-6 if allowed < len(benign) else float(benign.max())


def evaluate_scorer(name, score_fn, d, extra_note=None, hard_decision=False):
    """score_fn(texts, max_length) -> attack margin (logit scale; 0 == probability 0.5)."""
    val_texts, val_y = [t for t, _ in d["val"]], np.array([label for _, label in d["val"]])
    vm = score_fn(val_texts, MAX_LEN)
    thr = 0.0 if hard_decision else val_threshold(vm, val_y)      # a block/allow system has no score to tune: its operating point is what it ships
    entry = {"name": name, "val_threshold_margin": round(thr, 3), "val_auc": round(float(roc_auc_score(val_y, vm)), 4),
             "val_fpr_at_threshold": round(float((vm[val_y == 0] >= thr).mean()), 4), "sets": {}}
    if extra_note:
        entry["note"] = extra_note
    for sname, items in d["held"].items():
        texts, y = [t for t, _ in items], np.array([label for _, label in items])
        m = score_fn(texts, 512 if name.startswith("teacher") else MAX_LEN)
        entry["sets"][sname] = {"at_0.5": metrics(m, y, 0.0), "at_val_threshold": metrics(m, y, thr)}
        if sname in SETS_WITH_BOTH:
            entry["sets"][sname]["roc_auc"] = round(float(roc_auc_score(y, m)), 3)
    return entry


def macro(entry, which):
    det = [s[which]["detection"]["rate"] for s in entry["sets"].values() if "detection" in s[which]]
    fpr = [s[which]["fpr"]["rate"] for s in entry["sets"].values() if "fpr" in s[which]]
    return {"mean_detection_over_sets": round(float(np.mean(det)), 3), "mean_fpr_over_sets": round(float(np.mean(fpr)), 3)}


def shipped_scorer():
    from gateway.detectors import rule_based
    from gateway.detectors.classifier_numpy import ScratchClassifierDetectorNumpy
    clf = ScratchClassifierDetectorNumpy()
    clf.load()

    def scores(texts, _max_length):
        # the shipped default is rules OR classifier at 0.5: express "blocked" as margin +1/-1 so the same machinery reports it
        return np.array([1.0 if (rule_based.detect(t).blocked or clf.detect(t).blocked) else -1.0 for t in texts], dtype=np.float32)

    def clf_only(texts, _max_length):
        p = np.clip(np.array([clf.detect(t).confidence for t in texts], dtype=np.float64), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).astype(np.float32)

    return scores, clf_only


def cmd_loso(args):
    """Train each variant without one labelled source and test on that source's own test split (a distribution the model never saw)."""
    out = {"variants": args.variants, "seeds": args.seeds, "results": {}}
    for spec in args.variants:
        variant, _, a = spec.partition(":")                            # "hardkd:0.3" = variant hardkd with alpha 0.3
        alpha = float(a) if a else 0.5
        out["results"][spec] = {}
        for src, test_name in LABELLED_SOURCES.items():
            runs = []
            for seed in range(args.seeds):
                d = data(exclude=src)
                model, tok, info = train_student(variant, seed, d, alpha=alpha, log=lambda *_: None)
                val_texts, val_y = [t for t, _ in d["val"]], np.array([label for _, label in d["val"]])
                thr = val_threshold(student_margins(model, tok, val_texts), val_y)
                items = d["held"][test_name]
                texts, y = [t for t, _ in items], np.array([label for _, label in items])
                m = student_margins(model, tok, texts)
                runs.append({"auc": round(float(roc_auc_score(y, m)), 4) if len(set(y)) == 2 else None,
                             "at_0.5": metrics(m, y, 0.0), "at_val_threshold": metrics(m, y, thr), "n_labelled": info["n_labelled"]})
                del model
                torch.cuda.empty_cache()
            out["results"][spec][src] = runs
            r = runs[0]
            print(f"  {spec:<12} without {src:<14} -> test {test_name:<15} AUC {r['auc']}  detection@0.5 {r['at_0.5'].get('detection', {}).get('rate')}"
                  f"  FPR@0.5 {r['at_0.5'].get('fpr', {}).get('rate')}", flush=True)
    previous = json.loads(LOSO_REPORT.read_text(encoding="utf-8")) if LOSO_REPORT.exists() else {"results": {}}
    out = {"seeds": args.seeds, "results": {**previous["results"], **out["results"]}}       # rounds accumulate by variant name
    LOSO_REPORT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nvariant     mean AUC (deepset/safeguard/jackhhao)   mean detection@0.5 (all four)   mean FPR@0.5 (benign-bearing)")
    for variant, per in out["results"].items():
        aucs = [np.mean([r["auc"] for r in per[s]]) for s in ("deepset_train", "safeguard", "jackhhao")]
        dets = [np.mean([r["at_0.5"]["detection"]["rate"] for r in per[s]]) for s in per]
        fprs = [np.mean([r["at_0.5"]["fpr"]["rate"] for r in per[s]]) for s in per if "fpr" in per[s][0]["at_0.5"]]
        print(f"{variant:<11} {np.mean(aucs):.4f}                                  {np.mean(dets):.3f}                            {np.mean(fprs):.3f}")


def cmd_report(_args):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    d = data()
    results = {"target_val_fpr": TARGET_VAL_FPR, "n_train_labelled": len(d["train"]), "n_transfer": len(d["transfer"]), "n_val": len(d["val"]),
               "dropped_overlap_with_heldout": d["dropped_overlap"],
               "held_out_sets": {k: {"n": len(v), "attacks": int(sum(label for _, label in v))} for k, v in d["held"].items()}, "systems": {}}
    shipped, clf_only = shipped_scorer()
    results["systems"]["shipped_default (rules OR classifier)"] = evaluate_scorer("shipped_default", shipped, d, "hard block/allow decision, no score: its operating point is fixed", hard_decision=True)
    results["systems"]["shipped classifier layer alone"] = evaluate_scorer("shipped_classifier_layer", clf_only, d)
    results["systems"]["teacher: protectai/deberta-v3-base-prompt-injection-v2"] = evaluate_scorer(
        "teacher", lambda texts, max_length: teacher_margins(texts, max_length), d,
        "trained on public data that includes jackhhao/jailbreak-classification: its jackhhao and jailbreak_llms numbers may be inflated by memorisation")
    runs = sorted(p for p in RUNS.glob("*-seed*") if (p / "config.json").exists()) if RUNS.exists() else []
    for run in runs:
        tok = AutoTokenizer.from_pretrained(run)  # nosec B615 - a local training-run directory, not a Hub download
        model = AutoModelForSequenceClassification.from_pretrained(run).to(DEVICE).eval()  # nosec B615 - local directory
        results["systems"][f"student {run.name}"] = evaluate_scorer(
            f"student_{run.name}", lambda texts, max_length, m=model, t=tok: student_margins(m, t, texts, max_length), d)
    for entry in results["systems"].values():
        entry["macro_at_0.5"], entry["macro_at_val_threshold"] = macro(entry, "at_0.5"), macro(entry, "at_val_threshold")
    REPORT.write_text(json.dumps(results, indent=2), encoding="utf-8")

    sets = list(d["held"])
    print(f"\n{'system':<44}" + "".join(f"{s[:12]:>13}" for s in sets) + "   macro det / FPR")
    for which in ("at_0.5", "at_val_threshold"):
        print(f"--- {which}")
        for name, e in results["systems"].items():
            cells = []
            for s in sets:
                m = e["sets"][s][which]
                cells.append(f"{m['detection']['rate']:.2f}" if "detection" in m else f"f{m['fpr']['rate']:.2f}")
            mc = e[f"macro_{which}"]
            print(f"{name[:43]:<44}" + "".join(f"{c:>13}" for c in cells) + f"   {mc['mean_detection_over_sets']:.3f} / {mc['mean_fpr_over_sets']:.3f}")
    print("--- ROC-AUC (own_corpus, deepset_test, safeguard_test, jackhhao_test)")
    for name, e in results["systems"].items():
        print(f"{name[:43]:<44}" + "".join(f"{e['sets'][s].get('roc_auc', float('nan')):>13.3f}" for s in SETS_WITH_BOTH))
    print(f"\nWrote {REPORT.relative_to(REPO_ROOT)}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    tr = sub.add_parser("train")
    tr.add_argument("--variant", choices=("hard", "kd", "hardkd", "hard_tkd", "hardkd_tkd"), default="hardkd")
    tr.add_argument("--seed", type=int, default=0)
    tr.add_argument("--epochs", type=int, default=4)
    tr.add_argument("--batch", type=int, default=32)
    tr.add_argument("--lr", type=float, default=5e-5)
    tr.add_argument("--temperature", type=float, default=2.0)
    tr.add_argument("--alpha", type=float, default=0.5, help="weight of the hard-label loss in hardkd")
    tr.add_argument("--beta", type=float, default=1.0, help="weight of the teacher loss on transfer rows in hard_tkd")
    tr.set_defaults(fn=cmd_train)
    lo = sub.add_parser("loso")
    lo.add_argument("--variants", nargs="+", default=["hard", "hard_tkd", "hardkd"], help="variant or variant:alpha, e.g. hardkd:0.3")
    lo.add_argument("--seeds", type=int, default=1)
    lo.set_defaults(fn=cmd_loso)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
