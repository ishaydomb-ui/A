#!/usr/bin/env bash
# Restart the bot and prove it actually restarted.
#
# `systemctl is-active` says the service is running. It does NOT say it is
# running the code you just wrote — an old process satisfies it perfectly.
# That exact confusion shipped a change to the user that was never live:
# the restart command sat in a compound that timed out, is-active reported
# "active", and the report said "bot restarted" while a 30-minute-old
# process kept serving.
#
# So this compares the main PID before and after, and fails loudly if it
# did not change.
set -euo pipefail
UNIT=${1:-grocery-bot.service}

before=$(systemctl --user show "$UNIT" -p MainPID --value)
systemctl --user restart "$UNIT"
sleep 8
after=$(systemctl --user show "$UNIT" -p MainPID --value)

if [ "$before" = "$after" ] || [ "$after" = "0" ]; then
    echo "RESTART FAILED: pid $before -> $after" >&2
    systemctl --user status "$UNIT" --no-pager | head -12 >&2
    exit 1
fi
echo "$UNIT restarted: pid $before -> $after"
systemctl --user show "$UNIT" -p ActiveEnterTimestamp --value
