#!/usr/bin/env bash
# Regenerate the hash-pinned lock files (Linux / Python 3.12: what CI and the Docker images run).
# Needs uv:  pip install uv        Review the diff: a lock change is a supply-chain change.
set -euo pipefail
cd "$(dirname "$0")/.."
for name in requirements-render requirements requirements-ci requirements-student; do
  lock="${name}.lock"; [ "$name" = "requirements" ] && lock="requirements.lock"
  # The student lock is installed ALONGSIDE the render lock (--no-deps), so it is compiled constrained to it: a package in both (numpy) must have the same version. Constraining
  # requirements-ci the same way was tried and rejected: it forced older semgrep, pip-audit and OpenTelemetry versions to fit the render lock's FastAPI/pydantic pins.
  constraint=(); [ "$name" = "requirements-student" ] && constraint=(-c requirements-render.lock)
  uv pip compile "${name}.txt" "${constraint[@]}" --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
     --generate-hashes --no-header --annotation-style line -o "$lock"
done
echo "locks regenerated; verify with: pip download --no-deps --require-hashes -r requirements-render.lock -d /tmp/w (Linux, Python 3.12)"
