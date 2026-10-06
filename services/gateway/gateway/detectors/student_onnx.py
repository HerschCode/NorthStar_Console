"""
Distilled prompt-injection guard, served with ONNX Runtime int8 (no torch, no transformers).

A 22M-parameter MiniLM student, fine-tuned on the same labelled rows as the numpy classifier plus teacher-labelled benign transfer text (scripts/guard_student.py, docs/guard-student.md),
exported and quantised by scripts/guard_student_export.py. It replaces nothing by default: gateway/middleware.py loads it only when CLASSIFIER_BACKEND is `student` or `both`.

What it needs: `onnxruntime` (imported lazily, so a deployment that never enables this layer does not load it) and the int8 model, metadata, and either
`vocab.txt` or a supported `tokenizer.json` under models/guard_student/. Each artifact is checked against models/MANIFEST.sha256 before it is parsed:
an ONNX file is a protobuf, and a parser fed an unchecked file is attack surface.

The operating point comes from meta.json: probability 0.5, chosen on the leave-one-source-out folds (docs/guard-student.md says in what order the evidence was seen). The stricter threshold fixed
at at most 1% validation false positives is recorded beside it, not used. `detect` takes a probability threshold like the other detectors, so the adaptive multiplier (a suspicious session gets
an easier threshold) applies unchanged.

Inputs longer than `max_length` tokens are cut: the model sees the start of a long prompt only. That is measured, not hidden (docs/guard-student.md, the jailbreak_llms rows).
"""
import json
import math
import os
import time
from pathlib import Path

import numpy as np

from gateway.detectors.classifier_numpy import DetectionResult

# STUDENT_MODEL_DIR is for evaluating a staged export before it is shipped (scripts/guard_student_ensemble.py); a path outside models/ is not covered by the integrity manifest.
STUDENT_DIR = Path(os.environ.get("STUDENT_MODEL_DIR") or Path(__file__).resolve().parents[2] / "models" / "guard_student")
THREADS = int(os.environ.get("STUDENT_THREADS", "1"))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class StudentOnnxDetector:
    name = "student_guard"

    def __init__(self, path: Path = STUDENT_DIR):
        self.path = path
        self.session = None
        self.tokenizer = None
        self.max_length = 256
        self.meta: dict = {}
        self.threshold = 0.5                       # probability; set from meta.json at load

    def load(self):
        from gateway import model_integrity
        artifacts = ("student.int8.onnx", "meta.json")
        for name in artifacts:
            model_integrity.verify(self.path / name)
        vocab_path = self.path / "vocab.txt"
        tokenizer_path = self.path / "tokenizer.json"
        if vocab_path.is_file():
            model_integrity.verify(vocab_path)
        elif tokenizer_path.is_file():
            model_integrity.verify(tokenizer_path)
        else:
            raise FileNotFoundError(f"student tokenizer not found in {self.path}: expected vocab.txt or tokenizer.json")
        import onnxruntime as ort

        from gateway.detectors.wordpiece import WordPieceTokenizer
        self.meta = json.loads((self.path / "meta.json").read_text(encoding="utf-8"))
        so = ort.SessionOptions()
        so.intra_op_num_threads = THREADS
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(self.path / "student.int8.onnx"), so, providers=["CPUExecutionProvider"])
        self.tokenizer = (
            WordPieceTokenizer.from_file(vocab_path)
            if vocab_path.is_file()
            else WordPieceTokenizer.from_tokenizer_json(tokenizer_path)
        )
        self.max_length = int(self.meta["max_length"])
        self.threshold = _sigmoid(float(self.meta["threshold_margin"]))
        self.score("warm-up")                      # the first call allocates; do it before the first request

    def score(self, text: str) -> float:
        """Attack-vs-benign logit margin (0 means probability 0.5)."""
        ids = np.array([self.tokenizer.encode(text, self.max_length)], dtype=np.int64)         # one sequence per call: every position is a real token
        logits = self.session.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids)})[0][0]
        return float(logits[1] - logits[0])

    def detect(self, text: str, threshold: float | None = None) -> DetectionResult:
        t0 = time.perf_counter()
        margin = self.score(text)
        prob = _sigmoid(margin)
        limit = self.threshold if threshold is None else threshold
        blocked = prob >= limit
        return DetectionResult(
            blocked=blocked, layer=self.name, confidence=round(prob, 4), matched_pattern_id="STUDENT-GUARD" if blocked else None,
            latency_ms=(time.perf_counter() - t0) * 1000, details={"margin": round(margin, 3), "threshold": round(limit, 4)})
