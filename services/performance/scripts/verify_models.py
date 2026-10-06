"""Supply-chain gate for model artifacts.  python -m scripts.verify_models [--write]

* models/MANIFEST.sha256 lists the sha256 of every file under models/ (except itself and the registry). CI fails if a file changed without the
  manifest being regenerated in the same commit, so a swapped model is a visible diff.
* Every .joblib is opened with the restricted unpickler (src/mlops/safe_load.py): a pickle that imports anything outside the
  scikit-learn / numpy / scipy / pandas allowlist is rejected without executing it. ONNX files are hashed but not scanned.
Exit code 1 on any problem. `--write` regenerates the manifest (a deliberate act, reviewed in the PR)."""
import sys
from pathlib import Path

from src.mlops.registry import sha256_file
from src.mlops.safe_load import UnsafeModelError, restricted_load

ROOT = Path("models")
MANIFEST = ROOT / "MANIFEST.sha256"


def files():
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and p != MANIFEST and "registry" not in p.parts and p.suffix != ".md")


def main(argv) -> int:
    current = {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in files()}
    if "--write" in argv:
        MANIFEST.write_text("".join(f"{h}  {n}\n" for n, h in sorted(current.items())), encoding="utf-8")
        print(f"wrote {MANIFEST} ({len(current)} files)")
        return 0
    problems = []
    if not MANIFEST.exists():
        problems.append("models/MANIFEST.sha256 is missing (run: python -m scripts.verify_models --write)")
    else:
        listed = dict(line.split("  ", 1)[::-1] for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip())
        for n, h in current.items():
            if n not in listed:
                problems.append(f"{n}: not in the manifest")
            elif listed[n] != h:
                problems.append(f"{n}: sha256 differs from the manifest")
        problems += [f"{n}: listed but missing" for n in listed if n not in current]
    for p in files():
        if p.suffix == ".joblib":
            try:
                restricted_load(p)
            except UnsafeModelError as e:
                problems.append(f"{p}: {e}")
    for line in problems:
        print("FAIL", line)
    print("models verified" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
