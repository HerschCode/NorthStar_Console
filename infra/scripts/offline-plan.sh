#!/usr/bin/env bash
# Produce a Terraform plan for one root module WITHOUT any cloud credentials, and write it as JSON
# for the policy checks (conftest).
#
#   scripts/offline-plan.sh <bootstrap|dev> [output.json] [extra.tf ...]
#
# How it works without credentials:
#   * the plan is computed against an EMPTY state, so every resource is a "create" and the provider
#     never has to read anything from GCP;
#   * the remote GCS backend is swapped for a local one in a throw-away copy of the repository
#     (backend_override.tf), so nothing touches the real state bucket;
#   * the provider is given a dummy access token so it configures itself; no API is called.
#
# Extra *.tf files (used by the policy mutation tests) are copied next to the root module before
# planning. The real working tree is never modified.
#
# Environment:
#   TF_PLUGIN_DIR  optional local provider mirror (terraform init -plugin-dir). Use a Windows-style
#                  path (C:/...) on Git Bash. Avoids a registry round trip.

set -euo pipefail

env_name="${1:?usage: offline-plan.sh <bootstrap|dev> [output.json] [extra.tf ...]}"
out="${2:-plan.json}"
extra_files=("${@:3}")

invoke_dir="$PWD"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -d "$repo_root/envs/$env_name" ] || { echo "no such root module: envs/$env_name" >&2; exit 2; }

case "$out" in
  /*|[A-Za-z]:*) out_abs="$out" ;;
  *) out_abs="$invoke_dir/$out" ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Copy sources only: no state, no provider caches, no per-module lock files.
mkdir -p "$work/envs"
cp -R "$repo_root/modules" "$work/modules"
cp -R "$repo_root/envs/$env_name" "$work/envs/$env_name"
find "$work" -name '.terraform' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$work" -name '*.tfstate*' -delete 2>/dev/null || true
find "$work/modules" -name '.terraform.lock.hcl' -delete 2>/dev/null || true

cd "$work/envs/$env_name"

# Local backend instead of the real GCS one. Override files replace the backend block wholesale.
cat > backend_override.tf <<'EOF'
terraform {
  backend "local" {}
}
EOF

# NAME.tfmutation is added as a new file; NAME.override.tfmutation becomes a Terraform override file,
# which merges into (rather than adds to) the resources and module calls it names.
for f in "${extra_files[@]}"; do
  [ -n "$f" ] || continue
  case "$f" in /*|[A-Za-z]:*) src="$f" ;; *) src="$invoke_dir/$f" ;; esac
  base="$(basename "$f")"
  base="${base%.tfmutation}"
  base="${base%.tf}"
  case "$base" in
    *.override) dest="zz_${base%.override}_override.tf" ;;
    *)          dest="zz_${base}.tf" ;;
  esac
  cp "$src" "./$dest"
done

# Any non-empty value works: with an empty state the provider is never asked to call an API.
export GOOGLE_OAUTH_ACCESS_TOKEN="offline-dummy-token"
export CHECKPOINT_DISABLE=1
export TF_IN_AUTOMATION=1

init_args=(-input=false -no-color)
if [ -n "${TF_PLUGIN_DIR:-}" ]; then
  init_args+=("-plugin-dir=${TF_PLUGIN_DIR}")
  unset TF_PLUGIN_CACHE_DIR
fi

terraform init "${init_args[@]}" >init.log 2>&1 || { cat init.log >&2; exit 1; }

tfvars=()
[ -f offline.tfvars ] && tfvars=(-var-file=offline.tfvars)

terraform plan -input=false -no-color -lock=false -refresh=false "${tfvars[@]}" -out=tfplan.bin >plan.log 2>&1 \
  || { cat plan.log >&2; exit 1; }

terraform show -json tfplan.bin >"$out_abs"
echo "offline plan envs/$env_name: $(grep -E '^Plan:' plan.log || echo 'no changes') -> $out_abs" >&2
