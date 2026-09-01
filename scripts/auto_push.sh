#!/usr/bin/env bash
# Push to GitHub if there is anything new — run periodically by
# grocery-backup.timer. Cheap by design: a `git fetch` plus a local ref
# comparison, and it only pushes (never force, never touches other
# branches) when there is genuinely something to send. On an idle day
# this does nothing but one small network check.
#
# It also stamps a heartbeat on every successful run, including the
# no-op ones. Without that, a stopped timer and a quiet night are
# indistinguishable: both produce no commits, no failures and no log
# lines. backup_doctor.py reads this file rather than asking systemd
# whether the timer "looks" enabled.
#
# It deliberately does NOT commit uncommitted work. This repository
# pushes to a code host and holds store credentials and live browser
# sessions, so an unattended `git add -A` is one .gitignore gap away from
# publishing them — a gap that genuinely existed on 2026-09-01 between a
# browser-profile directory being created and its ignore rule being
# written. Uncommitted work is reported by the doctor instead.
set -euo pipefail
cd "$(dirname "$0")/.."

HEARTBEAT="data/backup_heartbeat.json"
now() { date -u +%Y-%m-%dT%H:%M:%S+00:00; }

# Preserve when the tree first went dirty, so the doctor measures the age
# of the state rather than resetting it on every run.
dirty_since=""
if [ -n "$(git status --porcelain)" ]; then
    if [ -f "$HEARTBEAT" ]; then
        dirty_since=$(python3 -c "
import json,sys
try:
    print(json.load(open('$HEARTBEAT')).get('dirty_since') or '')
except Exception:
    print('')
" 2>/dev/null || true)
    fi
    [ -z "$dirty_since" ] && dirty_since=$(now)
fi

write_heartbeat() {
    mkdir -p data
    python3 - "$HEARTBEAT" "$(now)" "$dirty_since" "$1" <<'PY'
import json, sys
path, last_run, dirty_since, result = sys.argv[1:5]
json.dump(
    {"last_run": last_run, "dirty_since": dirty_since or None, "result": result},
    open(path, "w"),
    indent=1,
)
PY
}

branch=$(git rev-parse --abbrev-ref HEAD)
git fetch --quiet origin "$branch"

ahead=$(git rev-list --count "origin/${branch}..HEAD" 2>/dev/null || echo 0)
if [ "$ahead" -eq 0 ]; then
    echo "up to date, nothing to push"
    write_heartbeat "noop"
    exit 0
fi

echo "pushing $ahead commit(s) to origin/$branch"
git push origin "$branch"
write_heartbeat "pushed"
