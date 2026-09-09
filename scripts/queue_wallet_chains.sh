#!/usr/bin/env bash
# Run the wallet chain-list harvest once the branch harvest is done.
#
# The two must not overlap: while the branch harvest is hitting the site
# every few seconds, the SPA stops hydrating and pages come back with an
# empty body — which reads exactly like a broken selector. Observed
# 2026-09-09, and the reason this waits rather than running in parallel.
set -u
until ! systemctl --user is-active --quiet behatsdaa-branches.service; do
  sleep 60
done
sleep 45   # let the site settle before the next run
exec /home/codex/grocery-automation/.venv/bin/python3 \
     /home/codex/grocery-automation/scripts/harvest_wallet_chains.py
