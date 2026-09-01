"""The message that goes out when a shop is overdue.

Six days after the last order, if nobody has started filling a cart, both
partners get one message in the group they already talk in. It does three
things at once, deliberately, because three separate messages would be
three chances to be ignored:

1. **Says the shop is due.** Nothing clever — most weeks this is the
   whole content, and that is fine.
2. **Asks what to add**, and expects a reply in ordinary Hebrew. The
   reply is captured by whichever bot receives it, through the same CLI
   the second bot already uses for the list.
3. **Carries anything genuinely worth acting on** — deep discounts on
   what they buy often, and on expensive things that keep, at any chain
   including the five they do not shop at.

It is sent by the household's *other* bot, the one both partners already
share, rather than by the grocery bot: the grocery bot is not in their
group, and asking them to talk to a second assistant to answer a question
is the friction this is meant to remove. So this module composes the text
and the CLI hands it over; delivery belongs to the caller.

Silence is a valid output. If the shop is not due, nothing is produced.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import hotdeals, shelflife

# The household orders roughly weekly to every ten days, in their own
# words. Six days is early enough to act on and late enough not to nag —
# a nudge that arrives while the fridge is still full gets ignored, and
# the next one gets ignored too.
DUE_AFTER_DAYS = 6

# Once nudged, do not nudge again for this long. Being asked daily is how
# a reminder becomes noise.
QUIET_PERIOD_DAYS = 3


@dataclass(frozen=True)
class NudgeDecision:
    """Whether to speak, and what to say."""

    due: bool
    days_since_order: int | None
    text: str = ""
    reason: str = ""


def _as_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except ValueError:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def last_order_date(storage, store: str = "shufersal") -> date | None:
    """When the household last actually ordered, from any chain."""
    from contextlib import closing

    with closing(storage._connect()) as conn:  # noqa: SLF001 - storage-internal
        row = conn.execute(
            "SELECT MAX(placed_at) AS newest FROM order_log"
        ).fetchone()
    logged = _as_date(row["newest"] if row else None)
    # order_log is only as current as its last sync, and the per-product
    # purchase dates are written by a different path, so either can be the
    # stale one. Taking the *later* of the two is what stops the bot
    # nagging about a shop that was already done — checked after the first
    # run reported the last order as 24 August while 1 September sat in
    # the other table.
    dates = [d for d in storage.last_purchase_dates(store).values() if d]
    candidates = [d for d in (logged, max(dates) if dates else None) if d]
    return max(candidates) if candidates else None


def decide(
    storage,
    today: date | None = None,
    last_nudged: date | None = None,
    store: str = "shufersal",
) -> NudgeDecision:
    """Should the household be nudged, and with what?"""
    today = today or datetime.now(timezone.utc).date()
    last_order = last_order_date(storage, store)
    if last_order is None:
        return NudgeDecision(False, None, reason="no order history yet")

    days = (today - last_order).days
    if days < DUE_AFTER_DAYS:
        return NudgeDecision(False, days, reason=f"only {days} days since the last order")

    if last_nudged and (today - last_nudged).days < QUIET_PERIOD_DAYS:
        return NudgeDecision(False, days, reason="already nudged recently")

    return NudgeDecision(True, days, text=compose(storage, days, today, store))


def compose(
    storage, days: int, today: date | None = None, store: str = "shufersal"
) -> str:
    """The message itself."""
    lines = [
        f"🛒 *עברו {days} ימים מההזמנה האחרונה*",
        "",
        "מה להוסיף לקנייה הבאה? אפשר לכתוב פשוט, למשל "
        "_\"חלב, לחם ושתי חבילות פסטה\"_ — ואני אוסיף לרשימה.",
    ]

    due = shelflife.due_now(storage, store, today)
    if due:
        lines += ["", "*כנראה נגמר במזווה:*"]
        lines += [f"• {item.name}" for item in due[:6]]

    deals = hotdeals.find(storage)
    if deals:
        lines += ["", hotdeals.format_deals(deals)]

    return "\n".join(lines)
