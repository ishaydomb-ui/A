#!/usr/bin/env bash
# Put the running bot on the current code — but only when that is true
# and safe.
#
# Why this exists: a Python process reads its source once, at start. On
# 2026-09-10 `grocery-bot.service` had been running since the previous
# afternoon, so the thinking loop, its 30s cap and the send fallback
# were all committed, pushed, tested — and absent from the bot Ishay
# actually talks to. `git push` saves code; it does not replace running
# software. Nothing announced the gap.
#
# Ishay's decision, 2026-09-10: keep it current, and accept a moment of
# unavailability. This narrows that moment to the cases that earn it.
#
#   1. If no tracked code is newer than the running process, do nothing.
#      An anchor that changes only HANDOFF.md should not drop the bot.
#   2. If a cart cycle is in flight, refuse. A cycle drives a real
#      browser against a real account and can run 10-40s; killing it
#      mid-way leaves the household's cart half-filled with no report,
#      which is a worse outcome than being one commit behind. Detected
#      by the browser child process, because the cycle owns one and an
#      idle bot has none.
#
# Exit: 0 restarted or already current, 3 deferred (cycle running).
set -u
cd "$(dirname "$0")/.." || exit 1
UNIT=grocery-bot.service

started_raw=$(systemctl --user show "$UNIT" -p ActiveEnterTimestamp --value 2>/dev/null)
if [ -z "$started_raw" ] || ! systemctl --user is-active --quiet "$UNIT"; then
  echo "bot is not running — starting it"
  systemctl --user start "$UNIT" && echo "started"
  exit 0
fi
started=$(date -d "$started_raw" +%s)

# Newest mtime among the files the bot actually loads.
newest=0
while IFS= read -r f; do
  m=$(stat -c %Y "$f" 2>/dev/null || echo 0)
  [ "$m" -gt "$newest" ] && newest=$m
done < <(git ls-files 'grocery_bot/*.py' 'scripts/*.py' 2>/dev/null)

if [ "$newest" -le "$started" ]; then
  echo "already current (running since $(date -d "@$started" '+%H:%M:%S'), no newer code)"
  exit 0
fi

pid=$(systemctl --user show "$UNIT" -p MainPID --value)
if [ -n "$pid" ] && pgrep -P "$pid" >/dev/null 2>&1; then
  echo "DEFERRED: a cycle looks active (child processes under $pid)."
  echo "  Nothing was restarted. Re-run when the cycle reports done."
  pgrep -P "$pid" -a | head -3
  exit 3
fi

echo "restarting: code from $(date -d "@$newest" '+%H:%M:%S') > bot from $(date -d "@$started" '+%H:%M:%S')"
systemctl --user restart "$UNIT" || exit 1
sleep 8
if systemctl --user is-active --quiet "$UNIT"; then
  echo "restarted, now running $(systemctl --user show "$UNIT" -p MainPID --value)"
  journalctl --user -u "$UNIT" --since "1 min ago" -p warning --no-pager -o cat 2>/dev/null | tail -3
else
  echo "RESTART FAILED — the bot is down. Last lines:"
  journalctl --user -u "$UNIT" -n 15 --no-pager -o cat
  exit 1
fi
