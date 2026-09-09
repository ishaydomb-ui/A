#!/usr/bin/env python3
"""Alert only when a failure persists — not on the first blip.

Every grocery unit points its `OnFailure=` at the notifier, which sent a
Telegram message the instant anything failed once. That was the right
instinct and the wrong threshold. Two real examples from 2026-09-08,
both of which reached Ishay's phone and neither of which needed him:

- `grocery-prices` timed out reading one listing page of Shufersal's
  feed, reported "No PriceFull file published for branch 9", and pulled
  all seven chains successfully on its next run.
- `grocery-backup` hit `ssh: connect to github.com port 22: Connection
  timed out` on the push. The database backup inside that same run had
  already succeeded, and the next 30 runs all passed.

A timer that retries every 30 minutes does not need a person for a
network blip it will fix itself. What a person needs to know is that
something is *staying* broken.

So this gate counts how many times the unit has actually failed in a
recent window, straight from the journal, and stays silent below the
threshold. A permanent breakage still alerts — one run later than
before, which for a 30-minute timer is 30 minutes.

The bot itself is the exception and alerts on the first failure: it is
user-facing, nothing retries it, and a household member typing into a
dead bot gets silence with no explanation.
"""
from __future__ import annotations

import os
import subprocess
import sys

# How many failures inside the window before a person is told.
DEFAULT_THRESHOLD = 2
WINDOW = "6 hours ago"

# Units where one failure already matters. Nothing retries these, and
# their failure is visible to the household rather than to a log.
IMMEDIATE = {"grocery-bot.service"}


def failures_in_window(unit: str, window: str = WINDOW) -> int:
    """How many times systemd has recorded this unit failing recently."""
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", unit, "--since", window, "--no-pager"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        # If we cannot read the journal we cannot judge, so we speak
        # rather than swallow — a missed alert is worse than a spare one.
        return DEFAULT_THRESHOLD
    return out.count("Failed with result")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: alert_gate.py <unit> [message...]", file=sys.stderr)
        return 2
    unit = sys.argv[1]
    if not unit.endswith(".service"):
        unit += ".service"
    message = " ".join(sys.argv[2:]).strip() or (
        f"🛑 *{unit}* נכשל שוב בשרת. בדקו עם: systemctl --user status {unit}"
    )

    threshold = 1 if unit in IMMEDIATE else int(
        os.environ.get("ALERT_THRESHOLD", DEFAULT_THRESHOLD)
    )
    seen = failures_in_window(unit)
    if seen < threshold:
        # Logged, not sent: the journal keeps the evidence that this
        # happened at all, so a quiet gate never hides a real pattern
        # from anyone reading back.
        print(
            f"{unit}: {seen} failure(s) in {WINDOW}, below threshold {threshold} "
            "— not alerting",
            flush=True,
        )
        return 0

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from notify import send  # noqa: E402  - after the quiet path, so a

    # silent gate never depends on the notifier importing cleanly.
    print(f"{unit}: {seen} failure(s) in {WINDOW} — alerting", flush=True)
    return 0 if send(message) else 1


if __name__ == "__main__":
    raise SystemExit(main())
