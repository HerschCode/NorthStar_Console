#!/usr/bin/env bash
# Prove that the policy gate really blocks bad changes, using REAL `terraform plan` output.
#
#   scripts/policy-mutation-tests.sh [name-filter]
#
# 1. Baseline: the unmodified bootstrap and dev roots must produce plans with no violations.
# 2. Mutations: every policies/mutations/*.tfmutation is planned (offline, no credentials) and
#    handed to conftest. The gate must reject it, and the rule the file names must be among the
#    failures. See policies/mutations/README.md.
#
# Needs terraform and conftest on PATH. TF_PLUGIN_DIR (optional) is passed through to
# scripts/offline-plan.sh.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
policies="$repo_root/policies"
filter="${1:-}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

total=0
failed=0
report() { printf '  %-4s %s\n' "$1" "$2"; }
indent() { sed 's/^/        /'; }

echo "== baseline: the real roots must satisfy their own policies =="
for root in bootstrap dev; do
  total=$((total + 1))
  if ! bash "$repo_root/scripts/offline-plan.sh" "$root" "$work/base-$root.json" 2>"$work/base-$root.err"; then
    report FAIL "envs/$root does not plan:"
    indent <"$work/base-$root.err" | tail -n 20
    failed=$((failed + 1))
    continue
  fi
  if out="$(conftest test "$work/base-$root.json" -p "$policies" --no-color 2>&1)"; then
    report PASS "envs/$root: no violations ($(echo "$out" | tail -n 1))"
  else
    report FAIL "envs/$root is rejected by its own policies:"
    echo "$out" | grep -E 'FAIL|WARN' | indent
    failed=$((failed + 1))
  fi
done

echo
echo "== mutations: each deliberately bad change must be blocked =="
for m in "$policies"/mutations/*.tfmutation; do
  name="$(basename "$m" .tfmutation)"
  if [ -n "$filter" ] && [[ "$name" != *"$filter"* ]]; then continue; fi

  root="$(sed -n 's/^# root: *//p' "$m" | head -n 1 | tr -d '\r ')"
  expect="$(sed -n 's/^# expect: *//p' "$m" | head -n 1 | tr -d '\r ')"
  total=$((total + 1))

  plan="$work/$name.json"
  if bash "$repo_root/scripts/offline-plan.sh" "$root" "$plan" "$m" 2>"$work/$name.err"; then
    planned=yes
  else
    planned=no
  fi

  if [ "$expect" = "plan-fails" ]; then
    if [ "$planned" = no ]; then
      report PASS "$name: refused by Terraform itself ($(grep -m1 -E 'Error:' "$work/$name.err" | sed 's/^.*Error: *//'))"
    else
      report FAIL "$name: the plan succeeded but a module guard should have refused it"
      failed=$((failed + 1))
    fi
    continue
  fi

  if [ "$planned" = no ]; then
    report FAIL "$name: the mutation does not plan (fix the mutation file):"
    indent <"$work/$name.err" | tail -n 12
    failed=$((failed + 1))
    continue
  fi

  out="$(conftest test "$plan" -p "$policies" --no-color 2>&1)"
  status=$?
  if [ "$status" -eq 0 ]; then
    report FAIL "$name: NOT BLOCKED (expected $expect)"
    failed=$((failed + 1))
    continue
  fi

  missing=""
  IFS=',' read -r -a ids <<<"$expect"
  for id in "${ids[@]}"; do
    grep -Fq "[$id]" <<<"$out" || missing="$missing $id"
  done

  if [ -z "$missing" ]; then
    report PASS "$name: blocked by $expect"
  else
    report FAIL "$name: blocked, but not by:$missing (expected $expect)"
    echo "$out" | grep -E '^FAIL' | indent | head -n 6
    failed=$((failed + 1))
  fi
done

echo
echo "$((total - failed))/$total checks passed"
[ "$failed" -eq 0 ]
