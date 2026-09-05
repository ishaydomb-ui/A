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
    python3 - "$HEARTBEAT" "$(now)" "$dirty_since" "$1" "$db_backup_failing_since" <<'PY'
import json, sys
path, last_run, dirty_since, result, db_backup_failing_since = sys.argv[1:6]
json.dump(
    {
        "last_run": last_run,
        "dirty_since": dirty_since or None,
        "result": result,
        "db_backup_failing_since": db_backup_failing_since or None,
    },
    open(path, "w"),
    indent=1,
)
PY
}

# Off-box copy of the SQLite database. The repo push above protects the
# code, but data/*.sqlite3 is gitignored (it holds live sessions and the
# household's lists), so price_history — ~23k daily price rows that cannot
# be re-derived — lived only on this box until 2026-09-04. This copies it
# to Drive on every run. It uses an ISOLATED remote if one is configured
# (`gdrive-grocery:`, the budget/familyos pattern — a token scoped to this
# project alone), and falls back to the shared `gdrive:` otherwise so the
# data is never left unprotected while that token is being set up. Never
# fatal: a backup that fails must not stop the git push above.
#
# `rclone` is called by full path, not bare — found 2026-09-05 (Arthur,
# usage-audit, from journalctl): systemd --user's default PATH does not
# include ~/bin, so the bare command silently resolved to nothing (exit
# 127, "command not found") on every single timer run, all day, with the
# real error swallowed by `2>&1 >/dev/null` and reported only as
# "(non-fatal)" — a backup that never once succeeded, indistinguishable
# in the log from an occasional hiccup. Two fixes, not one: the path
# (below), and `db_backup_failing_since` in the heartbeat (mirroring
# `dirty_since`) so watchdog.py can catch a *sustained* failure even if
# some future breakage swallows stderr the same way again.
RCLONE="$HOME/bin/rclone"
[ -x "$RCLONE" ] || RCLONE="rclone"  # fall back to PATH if the layout changes

db_backup_failing_since=""
if [ -f "$HEARTBEAT" ]; then
    db_backup_failing_since=$(python3 -c "
import json
try:
    print(json.load(open('$HEARTBEAT')).get('db_backup_failing_since') or '')
except Exception:
    print('')
" 2>/dev/null || true)
fi

backup_db() {
    local db="data/grocery_bot.sqlite3"
    [ -f "$db" ] || return 0
    local remote="gdrive:"
    "$RCLONE" listremotes 2>/dev/null | grep -qx "gdrive-grocery:" && remote="gdrive-grocery:"
    local err
    if err=$("$RCLONE" copy "$db" "${remote}גורדון — גיבוי DB/" 2>&1); then
        echo "db backed up to ${remote}"
        db_backup_failing_since=""
    else
        [ -z "$db_backup_failing_since" ] && db_backup_failing_since=$(now)
        # Real error text now reaches the journal — it used to be
        # discarded, which is exactly why this went unnoticed all day.
        echo "WARN: db backup to ${remote} failed (non-fatal): ${err}" >&2
    fi
}
backup_db || true

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
