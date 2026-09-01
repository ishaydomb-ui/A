#!/usr/bin/env python3
"""Is the backup actually working? Checked from the outside.

Run hourly by grocery-doctor.timer. Gathers the facts — heartbeat file,
what GitHub actually has, whether the working tree is dirty — and hands
them to grocery_bot.watchdog, which holds the thresholds and the
alert/all-clear logic so both can be tested against a fake clock.

Exit codes: 0 healthy, 1 problems found (and reported).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from grocery_bot import watchdog  # noqa: E402

HEARTBEAT = REPO / "data" / "backup_heartbeat.json"
STATE = REPO / "data" / "backup_doctor_state.json"


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=120
    )
    return result.stdout.strip()


def _read_heartbeat() -> dict:
    try:
        return json.loads(HEARTBEAT.read_text())
    except (OSError, ValueError):
        return {}


def _unpushed_since() -> datetime | None:
    """When the oldest commit missing from origin was made.

    Checked against what origin actually holds, not against whether the
    push command reported success — a push can succeed and still leave
    the branch behind if it went somewhere unexpected.
    """
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return None
    subprocess.run(
        ["git", "fetch", "--quiet", "origin", branch],
        cwd=REPO, capture_output=True, timeout=180,
    )
    stamps = _git(
        "log", f"origin/{branch}..HEAD", "--format=%cI", "--reverse"
    ).splitlines()
    if not stamps:
        return None
    try:
        oldest = datetime.fromisoformat(stamps[0])
    except ValueError:
        return None
    return oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)


def _dirty_since(heartbeat: dict) -> datetime | None:
    """How long the working tree has been dirty.

    auto_push.sh records the first run that saw a dirty tree and clears it
    once the tree is clean again, so this measures the age of the *state*,
    not of any one file.
    """
    if not _git("status", "--porcelain"):
        return None
    stamp = heartbeat.get("dirty_since")
    try:
        parsed = datetime.fromisoformat(str(stamp)) if stamp else None
    except (ValueError, TypeError):
        return None
    if parsed and not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main() -> int:
    heartbeat = _read_heartbeat()
    health = watchdog.check(
        heartbeat=heartbeat,
        unpushed_since=_unpushed_since(),
        dirty_since=_dirty_since(heartbeat),
    )

    action, text = watchdog.transition(watchdog.load_state(STATE), health)
    for problem in health.problems:
        print(f"PROBLEM {problem.key}: {problem.message}")
    if health.ok:
        print("backup healthy")

    if action in ("alert", "all_clear"):
        from notify import send  # noqa: E402 - sibling script

        send(text)
        print(f"sent {action}")

    watchdog.save_state(STATE, health.keys)
    return 1 if health.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
