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

from . import cardreminder, hotdeals, shelflife

# The household orders roughly weekly to every ten days, in their own
# words. Early enough to act on, late enough not to nag — a nudge that
# arrives while the fridge is still full gets ignored, and the next one
# gets ignored too.
#
# Moved 6 -> 5 by Ishay, 2026-09-07, after the first time it mattered.
# His last order was 09-01, so at six days the nudge only became due on
# the morning of 09-07 — and its first waking-hours slot was 10:00
# Israel time, by which point he had already shopped unprompted at 08:00.
# At six days the reminder can only ever fire on the one day he is most
# likely to have acted on his own; five gives it a day of margin.
DUE_AFTER_DAYS = 5

# Once nudged, do not nudge again for this long. Being asked daily is how
# a reminder becomes noise.
QUIET_PERIOD_DAYS = 3

# Deliberately no reply marker. One was built for the delivering bot to
# match replies against, then removed: that bot routes replies by intent
# instead, so nothing consumed it and it was a visible hashtag in a
# family group serving no purpose. Routing by intent is also better —
# a reply works whether or not a nudge was ever sent, with no state
# linking the two.


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
    bot_username: str = "",
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

    return NudgeDecision(
        True, days, text=compose(storage, days, today, store, bot_username)
    )


def compose(
    storage,
    days: int,
    today: date | None = None,
    store: str = "shufersal",
    bot_username: str = "",
) -> str:
    """The message itself."""
    # "The cart is already ready", chosen by Ishay 2026-09-07 from three
    # drafts. It leads with the state of the cart rather than with a
    # question, because with the standing cart there is nothing to
    # decide — the work left is reviewing and deleting. Note there are
    # no buttons: this message is delivered by Miri into the family
    # group, and a button there would need that project to change. Plain
    # store links work in any message and needed nothing from anyone.
    from . import standingcart

    from .chains import display_name

    filled = standingcart.cart_contents(storage)
    if filled:
        lines = [f"🛒 *עברו {days} ימים — העגלה כבר מוכנה*", ""]
        parts = [f"{count} פריטים ב{display_name(key)}" for key, count in filled]
        lines.append("מילאתי " + " ו-".join(parts) + " לפי מה שאתם קונים בדרך כלל.")
    else:
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

    # The card question rides here rather than having its own channel:
    # it must be asked *before* the shop, and this is the message that
    # already arrives then.
    prompt = cardreminder.decide(storage, today)
    if prompt.should_ask:
        lines += ["", prompt.text]

    relevant, exceptional = hotdeals.find(storage)
    text = hotdeals.format_deals(relevant, exceptional)
    if text:
        lines += ["", text]
        # A link rather than more lines: it costs nothing when they are
        # not interested, and nothing found has to be discarded. A
        # Telegram deep link needs no hosting and opens straight onto the
        # long list.
        extra = len(hotdeals.find_extended(storage))
        if extra and bot_username:
            lines += [
                "",
                f"[עוד {extra} מבצעים](https://t.me/{bot_username}?start=alldeals)",
            ]

    if filled:
        from .chains import cart_url

        lines += ["", "*מה שנשאר: לעבור, להוריד מה שלא צריך, לשלם.*", ""]
        for store_key, _ in filled:
            url = cart_url(store_key)
            if url:
                lines.append(f"[🛒 פתיחת הסל ב{display_name(store_key)}]({url})")

    return "\n".join(lines)
