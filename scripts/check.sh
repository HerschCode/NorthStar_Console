#!/usr/bin/env bash
# Run every check that CI runs, locally, with no cloud credentials.
#
#   scripts/install-tools.sh && export PATH="$PWD/.tools/bin:$PATH"
#   scripts/check.sh            # everything
#   scripts/check.sh quick      # skip the slow mutation tests (about 90 s)
#
# 1 fmt  2 validate  3 module unit tests  4 tflint  5 checkov  6 trivy (IaC + secrets)
# 7 policy unit tests  8 policy mutation tests (real plans)  9 generated docs up to date
#
# Environment:
#   TF_PLUGIN_DIR  optional local provider mirror (terraform init -plugin-dir), avoids the registry.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
quick="${1:-}"

failures=()
step() { # <name> <command...>
  local name="$1"; shift
  echo; echo "=== $name ==="
  if "$@"; then echo "--- ok: $name"; else echo "--- FAILED: $name"; failures+=("$name"); fi
}

init_args=(-backend=false -input=false -no-color)
if [ -n "${TF_PLUGIN_DIR:-}" ]; then
  init_args+=("-plugin-dir=${TF_PLUGIN_DIR}")
  unset TF_PLUGIN_CACHE_DIR
fi

each_dir() { # <function> <dir...>: run a function in every directory, fail if any fails
  local fn="$1" rc=0; shift
  for d in "$@"; do "$fn" "$d" || rc=1; done
  return "$rc"
}

validate_dir() {
  ( cd "$1" && terraform init "${init_args[@]}" >/dev/null 2>&1 && terraform validate -no-color >/dev/null ) \
    && echo "  ok   $1" || { echo "  FAIL $1"; ( cd "$1" && terraform validate -no-color ) || true; return 1; }
}

test_dir() {
  [ -d "$1/tests" ] || return 0
  ( cd "$1" && terraform init "${init_args[@]}" >/dev/null 2>&1 && terraform test -no-color 2>&1 | tail -n 1 ) \
    | sed "s|^|  $1: |"
  ( cd "$1" && terraform test -no-color >/dev/null 2>&1 )
}

run_checkov() {
  if command -v checkov >/dev/null 2>&1; then checkov "$@"
  elif command -v python3 >/dev/null 2>&1; then python3 -m checkov.main "$@"
  else python -m checkov.main "$@"; fi
}

py() { if command -v python3 >/dev/null 2>&1; then python3 "$@"; else python "$@"; fi; }

# The identity map in docs/cloud-security.md is generated from the plans; it must match them.
identity_map_current() {
  local tmp rc; tmp="$(mktemp -d)"
  bash scripts/offline-plan.sh bootstrap "$tmp/bootstrap.json" \
    && bash scripts/offline-plan.sh dev "$tmp/dev.json" \
    && py scripts/identity-map.py "$tmp/bootstrap.json" "$tmp/dev.json" --check docs/cloud-security.md
  rc=$?; rm -rf "$tmp"; return "$rc"
}

step "terraform fmt"            terraform fmt -check -recursive -no-color
step "terraform validate"       each_dir validate_dir modules/* envs/*
step "module unit tests"        each_dir test_dir modules/*
step "tflint"                   bash -c 'tflint --init >/dev/null && tflint --recursive --no-color'
step "checkov"                  run_checkov --config-file .checkov.yaml
step "trivy IaC"                trivy config . --skip-check-update --quiet
step "trivy secrets"            trivy fs --scanners secret --skip-check-update --quiet .
step "rego format"              conftest fmt policies --check
step "policy unit tests"        conftest verify -p policies --no-color
if [ "$quick" = "quick" ]; then
  echo; echo "=== policy mutation tests: skipped (quick) ==="
else
  step "policy mutation tests"  bash scripts/policy-mutation-tests.sh
fi
step "generated docs"           bash scripts/gen-docs.sh --check
step "policy rules documented"  bash scripts/check-rule-docs.sh
step "identity map current"     identity_map_current

echo
if [ "${#failures[@]}" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "FAILED: ${failures[*]}"
  exit 1
fi
