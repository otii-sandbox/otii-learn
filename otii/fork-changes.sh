#!/usr/bin/env bash
# What otii changed in LearnHouse, compared with the LearnHouse version we are on.
#   otii/fork-changes.sh           files otii added, and LearnHouse files otii edited
#   otii/fork-changes.sh --diff    the full edits to LearnHouse's own files
set -euo pipefail
cd "$(dirname "$0")/.."
base=$(git merge-base HEAD upstream/main)
echo "LearnHouse base: $(git log -1 --format='%h %cd' --date=short "$base")"
echo "Added by otii:"
git diff --name-only --diff-filter=A "$base" HEAD | sed 's/^/  /'
echo "LearnHouse files edited by otii (the only places an update can conflict):"
git diff --stat --diff-filter=M "$base" HEAD | sed 's/^/  /'
[ "${1:-}" = "--diff" ] && git diff --diff-filter=M "$base" HEAD
exit 0
