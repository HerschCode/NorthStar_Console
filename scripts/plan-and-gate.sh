#!/usr/bin/env bash
# Plan a root module offline (no credentials) and hold the plan to the policies.
#
#   scripts/plan-and-gate.sh <bootstrap|dev> <plan.json>    # plan.json: absolute, or relative to the repo root
#
# This is the "the real roots must satisfy their own policies" check: the same gate that stops a bad
# pull request, run on the code as it stands.

set -euo pipefail

root="${1:?usage: plan-and-gate.sh <bootstrap|dev> <plan.json>}"
out="${2:?usage: plan-and-gate.sh <bootstrap|dev> <plan.json>}"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

bash scripts/offline-plan.sh "$root" "$out"
conftest test "$out" -p policies --no-color
