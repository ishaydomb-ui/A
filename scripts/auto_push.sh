#!/usr/bin/env bash
# Push to GitHub if there is anything new — run periodically by
# grocery-backup.timer. Cheap by design: a `git fetch` plus a local ref
# comparison, and it only pushes (never force, never touches other
# branches) when there is genuinely something to send. On an idle day
# this does nothing but one small network check.
set -euo pipefail
cd "$(dirname "$0")/.."

branch=$(git rev-parse --abbrev-ref HEAD)
git fetch --quiet origin "$branch"

ahead=$(git rev-list --count "origin/${branch}..HEAD" 2>/dev/null || echo 0)
if [ "$ahead" -eq 0 ]; then
    echo "up to date, nothing to push"
    exit 0
fi

echo "pushing $ahead commit(s) to origin/$branch"
git push origin "$branch"
