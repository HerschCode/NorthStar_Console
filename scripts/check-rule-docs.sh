#!/usr/bin/env bash
# Fail if a policy rule exists in policies/*.rego but is not documented in docs/cloud-security.md.
# Rule IDs look like [IAM-001] inside the deny/warn messages; the document lists each one in
# backticks in the policy table. This keeps the write-up honest when someone adds a rule.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

status=0
ids="$(grep -ohE '\[(IAM|PUB|UNK|RUN|SEC|GCS|BQ|WIF|KEY|COST)-[0-9]{3}\]' policies/*.rego | tr -d '[]' | sort -u)"
[ -n "$ids" ] || { echo "no policy rule IDs found in policies/*.rego (did the message format change?)" >&2; exit 1; }

for id in $ids; do
  if ! grep -qF "\`$id\`" docs/cloud-security.md; then
    echo "policy rule $id is not documented in docs/cloud-security.md" >&2
    status=1
  fi
done

[ "$status" -eq 0 ] && echo "all $(echo "$ids" | wc -l | tr -d ' ') policy rules are documented"
exit "$status"
