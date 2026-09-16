"""Close the gap between "added to the list" and "in the cart".

Set by Ishay 2026-09-16 21:40, relayed verbatim through Miri: *"באיזה שלב
גורדון מכניס את זה ממש לעגלה עצמה? תוודאי מולו שהמסלול סגור עד הסוף. הוא
צריך לקבל הודעה כשמשהו נוסף לרשימה ולהכניס ישר לעגלה או משהו אחר
שתציעו."*

**The gap was real and already costing.** `grocery_bot.cli add-item` —
the seam the household's *other* bot writes through — inserts a row into
`adhoc_requests` and does nothing else. Nothing watches that table, so an
item reached a cart only when someone ran `/start_order` or a deferred
cycle happened to fire. Measured 2026-09-16: Liran added **20 items in
three minutes** through Miri and they sat untouched; `חלב עמיד`, added by
Ishay on 09-11, had been pending **five days**.

Three options were on the table (Miri's framing). This is (b), and the
reasons for rejecting the others are the numbers, not taste:

- **(a) add each item to the cart as it arrives.** A cart add costs ~30s
  of real browser against a real account. Liran's twenty items arrived
  over three minutes, so this would mean twenty sessions racing each
  other for ten minutes of work — and twenty notifications, which is the
  same flood that made 80 pending questions the worst friction ever
  measured here.
- **(c) a cycle at a fixed hour.** Gives up the immediacy that the whole
  instruction is about.
- **(b) debounce, then one run.** A burst becomes one cart run and one
  message. Twenty items cost one session, not twenty.

So: poll, announce new items **once per burst**, and when the burst has
been quiet long enough, run a single cart pass for everything pending.

Two guards that are not optional:

- **A cooldown.** A cycle consumes a request only when some store
  actually managed it, which is correct — a network blip must not
  silently delete something nobody will think to re-send. But it means a
  permanently failing item stays pending forever, and without a cooldown
  this would drive a browser cycle every quiet period, all night.
- **Nothing runs while the exit node is down or a cycle is in flight.**
  Handled by the caller, which already owns both facts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

SEEN_KEY = "listwatch_last_seen_id"
CHANGED_KEY = "listwatch_last_change_at"
RAN_KEY = "listwatch_last_run_at"

# How long a burst must be quiet before the cart run starts. Liran's
# twenty items spanned three minutes, so this has to comfortably exceed a
# person adding things as they think of them — and the cart is not needed
# for days, so waiting costs nothing while running twice costs a session.
QUIET_MINUTES = 12

# Never run again within this window unless something new arrived. The
# guard against an item that can never succeed keeping the browser busy
# all night; `/start_order` remains available for an immediate run.
COOLDOWN_HOURS = 6


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


def _read_time(storage, key: str):
    raw = storage.get_state(key)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def assess(storage, now=None) -> tuple:
    """(action, items) — what the watcher should do this tick.

    Actions: ``announce`` (new items nobody has been told about),
    ``run`` (the burst has settled, put them in the cart), or ``wait``
    with a reason. Pure apart from reads, so the decision can be tested
    without a browser, a chat or a clock.
    """
    now = _now(now)
    try:
        pending = list(storage.list_pending_adhoc())
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the pending list")
        return "wait", []

    seen = 0
    try:
        seen = int(storage.get_state(SEEN_KEY) or 0)
    except ValueError:
        seen = 0

    fresh = [item for item in pending if int(getattr(item, "id", 0) or 0) > seen]
    if fresh:
        return "announce", fresh
    if not pending:
        return "wait", []

    changed = _read_time(storage, CHANGED_KEY)
    if changed and now - changed < timedelta(minutes=QUIET_MINUTES):
        return "wait", []

    ran = _read_time(storage, RAN_KEY)
    if ran and now - ran < timedelta(hours=COOLDOWN_HOURS):
        # Something pending could not be added and will not succeed by
        # being retried every twelve minutes. Left for the next cycle.
        return "wait", []

    return "run", pending


def note_announced(storage, items, now=None) -> None:
    """Remember that these were announced, and restart the quiet clock."""
    if not items:
        return
    highest = max(int(getattr(item, "id", 0) or 0) for item in items)
    storage.set_state(SEEN_KEY, str(highest))
    storage.set_state(CHANGED_KEY, _now(now).isoformat())


def note_ran(storage, now=None) -> None:
    storage.set_state(RAN_KEY, _now(now).isoformat())


# Older than this and "just added" is not true. The first tick after this
# module ships will meet a backlog — 21 items on 2026-09-16, one of them
# pending since 09-11 — and announcing a five-day-old request as new is
# the kind of small false note that makes the whole message untrustworthy.
BACKLOG_AFTER_HOURS = 2


def _is_backlog(items, now=None) -> bool:
    now = _now(now)
    for item in items:
        raw = getattr(item, "created_at", "") or ""
        try:
            created = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if not created.tzinfo:
            created = created.replace(tzinfo=timezone.utc)
        if now - created > timedelta(hours=BACKLOG_AFTER_HOURS):
            return True
    return False


def format_added(items, now=None) -> str:
    """One message for a whole burst, never one per item."""
    if not items:
        return ""
    who = sorted({(getattr(i, "requested_by", "") or "").strip() for i in items} - {""})
    names = [(getattr(i, "text", "") or "").strip() for i in items]
    names = [n for n in names if n]
    if _is_backlog(items, now):
        # Says what is true: these were waiting, not just typed.
        lead = (
            "פריט אחד ממתין ברשימה" if len(names) == 1
            else f"{len(names)} פריטים ממתינים ברשימה"
        )
    else:
        lead = "נוסף לרשימה" if len(names) == 1 else f"נוספו {len(names)} פריטים לרשימה"
    if who:
        lead += " (" + ", ".join(who) + ")"
    lines = [f"📝 *{lead}*", ""]
    lines += [f"• {name}" for name in names[:20]]
    if len(names) > 20:
        lines.append(f"_ועוד {len(names) - 20}._")
    lines += ["", f"_אכניס אותם לעגלה בעוד ~{QUIET_MINUTES} דקות, אם לא יתווספו עוד._"]
    return "\n".join(lines)
