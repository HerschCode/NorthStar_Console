#!/usr/bin/env bash
# Regenerate the terraform-docs block (inputs, outputs, resources...) inside every module and
# root-module README, using .terraform-docs.yml. Text outside the BEGIN/END markers is untouched.
#
#   scripts/gen-docs.sh            rewrite the READMEs
#   scripts/gen-docs.sh --check    fail if any README is out of date (used by CI)

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mode_args=()
if [ "${1:-}" = "--check" ]; then
  mode_args=(--output-check)
fi

status=0
for dir in modules/* envs/*; do
  [ -f "$dir/README.md" ] || continue
  if ! terraform-docs --config .terraform-docs.yml "${mode_args[@]}" "$dir"; then
    echo "README out of date: $dir (run scripts/gen-docs.sh)" >&2
    status=1
  fi
done
exit "$status"
