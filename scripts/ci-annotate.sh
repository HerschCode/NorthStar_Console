#!/usr/bin/env bash
# Run a command; if it fails inside GitHub Actions, repeat the tail of its output as an error
# annotation.
#
#   scripts/ci-annotate.sh "<title>" <command> [args...]
#
# GitHub hides raw job logs from signed-out visitors, even on a public repository, but annotations
# are visible to everyone: on the run summary, on the pull request's Checks tab and through the API.
# So a failing check explains itself to someone reading it without an account.
#
# The command's output still goes to the normal log, and its exit status is preserved.

set -uo pipefail

title="${1:?usage: ci-annotate.sh <title> <command> [args...]}"
shift

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

"$@" 2>&1 | tee "$log"
status="${PIPESTATUS[0]}"

if [ "$status" -ne 0 ] && [ -n "${GITHUB_ACTIONS:-}" ]; then
  # Workflow-command escaping: % -> %25, newline -> %0A. Keep the tail, where the error is.
  message="$(tail -c 3500 "$log" | tr -d '\r' | sed -e 's/%/%25/g' | sed -e ':a' -e 'N' -e '$!ba' -e 's/\n/%0A/g')"
  echo "::error title=${title}::${message}"
fi

exit "$status"
