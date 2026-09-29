#!/usr/bin/env bash
# Fail if anything private is tracked in this public repo: run data (alerts,
# scores, the Jev cache, snapshots, run state, the tuned question set, the
# dismissed list), generated site pages, local paths or personal addresses.
#
# Runs in CI (.github/workflows/check-public.yml). Locally, extra patterns
# that must not be named here (e.g. the owner's addresses) can be supplied as
# a file of extended regexes, one per line:
#   TOS_WATCH_PRIVATE_PATTERNS=/path/to/patterns scripts/check-public.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

fail=0
bad_paths=$(git ls-files \
  | grep -E '^(alerts|cache|datasets)/|(^|/)(questions|dismissed|state|drafts_state)\.json$|_snapshots/|^site/(alerts|s)/|^site/[^/]+\.html$|^site/(feed|sitemap)\.xml$' \
  | grep -vx 'datasets/build_services_catalog.py' || true)
if [[ -n "$bad_paths" ]]; then
  echo "private or generated files are tracked:" >&2
  echo "$bad_paths" | head -20 >&2
  fail=1
fi

patterns='/home/[a-z]|/Users/[A-Za-z]|@gmail\.com|"(jev_)?scores?"[[:space:]]*:'
if [[ -n "${TOS_WATCH_PRIVATE_PATTERNS:-}" && -f "$TOS_WATCH_PRIVATE_PATTERNS" ]]; then
  while IFS= read -r p; do [[ -n "$p" ]] && patterns+="|$p"; done < "$TOS_WATCH_PRIVATE_PATTERNS"
fi
hits=$(git grep -nIE "$patterns" -- . ':!scripts/check-public.sh' ':!**/package-lock.json' || true)
if [[ -n "$hits" ]]; then
  echo "private-looking content is tracked:" >&2
  echo "$hits" | head -20 >&2
  fail=1
fi

[[ $fail -eq 0 ]] && echo "check-public: clean"
exit $fail
