"""Notice when the backup stops working, including when it fails silently.

A systemd timer that stops does not fail. No unit goes red, no log line
appears, and `auto_push.sh` keeps reporting "up to date, nothing to push"
— which is the shape of success. The backup simply ages out in silence,
which is the worst state a backup can be in: it looks fine right up to
the moment it is needed.

So nothing here trusts the timer's own account of itself. Two conditions
are checked, and they are deliberately separate because they mean
different things:

**A stale heartbeat** means the timer stopped running at all.
`auto_push.sh` stamps a file on *every* successful run, including the
no-op ones, so an idle night and a dead timer stop looking identical. A
rule based on commit times could never tell those apart.

**origin behind local** means runs are happening but pushes are failing —
credentials, network, a rejected ref.

Thresholds are set against this timer's real cadence of 30 minutes, not
copied from the daily familyos backup, whose two-day window would be
ninety-six missed runs here.

Uncommitted work is *reported*, never committed. This repository pushes
to a code host and holds store credentials and live browser sessions; a
`git add -A` running unattended is one .gitignore gap away from
publishing them. That gap existed for real on 2026-09-01, between a
browser profile directory being created and the ignore rule being
written.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The timer fires every 30 minutes, so this is four consecutive misses:
# past coincidence, still well inside a day. The Israeli exit node drops
# routinely and a tighter window would cry wolf.
HEARTBEAT_MAX_AGE = timedelta(hours=2)

# Same reasoning for a push that is not landing.
UNPUSHED_MAX_AGE = timedelta(hours=2)

# Long enough that an ordinary working session never trips it, short
# enough that a day's work is never silently unprotected overnight.
DIRTY_TREE_MAX_AGE = timedelta(hours=12)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Problem:
    """One thing wrong, in a form both a human and the state file can use."""

    key: str
    message: str


@dataclass
class Health:
    """The state of the backup, as observed from the outside."""

    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def keys(self) -> list[str]:
        return sorted(p.key for p in self.problems)


def check(
    heartbeat: dict | None,
    unpushed_since: datetime | None,
    dirty_since: datetime | None,
    now: datetime | None = None,
) -> Health:
    """Evaluate backup health from already-gathered facts.

    Kept free of subprocess and filesystem calls so the thresholds can be
    tested against a fake clock rather than by waiting two hours.
    """
    now = now or _now()
    problems: list[Problem] = []

    last_run = _parse((heartbeat or {}).get("last_run"))
    if last_run is None:
        problems.append(
            Problem("heartbeat_missing", "אין דופק מהגיבוי — הסקריפט כנראה לא רץ מעולם")
        )
    elif now - last_run > HEARTBEAT_MAX_AGE:
        hours = (now - last_run).total_seconds() / 3600
        problems.append(
            Problem(
                "heartbeat_stale",
                f"הגיבוי לא רץ כבר {hours:.0f} שעות — הטיימר כנראה נעצר",
            )
        )

    if unpushed_since and now - unpushed_since > UNPUSHED_MAX_AGE:
        hours = (now - unpushed_since).total_seconds() / 3600
        problems.append(
            Problem(
                "push_failing",
                f"יש קומיטים מקומיים שלא הגיעו ל-GitHub כבר {hours:.0f} שעות",
            )
        )

    if dirty_since and now - dirty_since > DIRTY_TREE_MAX_AGE:
        hours = (now - dirty_since).total_seconds() / 3600
        problems.append(
            Problem(
                "tree_dirty",
                f"יש עבודה לא מקומיטת כבר {hours:.0f} שעות — היא לא מגובה בשום מקום",
            )
        )

    return Health(problems=problems)


def transition(previous_keys: list[str], health: Health) -> tuple[str, str]:
    """What to send, given what was already reported.

    Returns (action, text). ``action`` is one of "alert", "all_clear" or
    "silent". Repeating an alert every hour for a condition already
    reported trains the household to ignore it, and recovering in silence
    leaves a stuck alert looking like an ongoing failure — so a change in
    either direction is announced exactly once.
    """
    previous = sorted(previous_keys or [])
    current = health.keys

    if current and current != previous:
        lines = ["🛑 *בעיה בגיבוי של grocery-automation*", ""]
        lines += [f"• {p.message}" for p in health.problems]
        return "alert", "\n".join(lines)

    if not current and previous:
        return "all_clear", "✅ *הגיבוי חזר לפעול כרגיל* — כל הבעיות נפתרו."

    return "silent", ""


def load_state(path: str | Path) -> list[str]:
    """Conditions reported at the last run."""
    try:
        return list(json.loads(Path(path).read_text()).get("reported", []))
    except (OSError, ValueError, AttributeError):
        # A missing or corrupt state file must not stop the check; the
        # worst case is one duplicate alert.
        return []


def save_state(path: str | Path, keys: list[str]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"reported": sorted(keys), "updated": _now().isoformat()}, indent=1)
    )
