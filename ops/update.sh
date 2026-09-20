#!/usr/bin/env bash
#
# One command to bring a running deployment up to date.
#
#   ./ops/update.sh
#
# Pulls the branch, then does only what the pull actually changed: rebuilds
# the services whose code moved, reloads the catalogue if the workbook moved,
# and says so if nothing did. Safe to run when there is nothing to do — that
# is the point, since it makes the command worth running on a whim rather
# than something to reason about first.
#
# It never touches the bot: that service is deliberately left to be started
# and stopped by hand (see CLAUDE.md and docs/telegram-bot.md).
#
# To have the server keep itself up to date, run it from cron — but read the
# note at the bottom of this file first, because that means anything pushed
# to the branch reaches clinicians without anyone looking at it.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
COMPOSE=(docker compose)
for file in ${COMPOSE_FILES:-docker-compose.local.yml docker-compose.permanent.yml}; do
  [ -f "$file" ] && COMPOSE+=(-f "$file")
done

say() { printf '\n== %s\n' "$*"; }

before="$(git rev-parse HEAD)"
say "Fetching $BRANCH"
# --ff-only: a deployment should never end up with a merge commit nobody
# intended, and a diverged checkout is worth stopping on rather than papering
# over at three in the morning.
git pull --ff-only origin "$BRANCH"
after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
  echo "Already up to date — nothing changed, nothing rebuilt."
  exit 0
fi

changed="$(git diff --name-only "$before" "$after")"
echo "$changed" | sed 's/^/  /'

needs_build=false
needs_data=false
while IFS= read -r path; do
  case "$path" in
    apps/bot/*) ;;                               # never ours to restart
    data/*) needs_data=true ;;
    apps/*|packages/*|ops/*|docker-compose*|package.json|pnpm-lock.yaml) needs_build=true ;;
  esac
done <<< "$changed"

if [ "$needs_build" = true ]; then
  say "Rebuilding the API and client"
  "${COMPOSE[@]}" up -d --build api web
else
  echo "No code changed — skipping the rebuild."
fi

if [ "$needs_data" = true ]; then
  # The newest workbook wins, so publishing a revision is: add it under a new
  # dated name, commit, run this.
  workbook="$(ls -1 data/*.xlsx | sort | tail -n 1)"
  say "Loading $(basename "$workbook")"
  "${COMPOSE[@]}" exec -T api node dist/scripts/load-formulary.js "/formulary/$(basename "$workbook")"
else
  echo "No new workbook — the catalogue is left as it is."
fi

say "Done"
"${COMPOSE[@]}" ps api web

# ---------------------------------------------------------------------------
# Running this automatically
#
#   crontab -e
#   */10 * * * * cd /home/codex/medcat && ./ops/update.sh >> var/update.log 2>&1
#
# Weigh that decision rather than copying it: it means a push to the branch
# reaches clinicians with nobody having looked at the result. That is a
# reasonable trade while the catalogue is a pilot among people who know each
# other, and a poor one once anybody relies on it without being told when it
# changed.
# ---------------------------------------------------------------------------
