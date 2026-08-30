"""The self-learning loop: every placed order teaches the system.

The household places orders in whatever way suits the moment — through
this bot, or entirely by hand in the store's app. Both must count.
Anything that only learns from its own actions decays the moment people
use the store directly, which they always will.

So learning is a nightly *pull from the store's own order history*, not
instrumentation of this bot: new orders are logged (for cadence), the
frequency model is rebuilt (tiers, departments, quantities — user
feedback survives, see replace_stock_items), and every newly seen
product lands in product memory so it never triggers a which-of-these
question.

Cadence is the quiet payoff. The household's own words: "אנחנו זורקים
הרבה, ומצד שני לפעמים המקרר ריק כי פספסנו להזמין בזמן" — both failure
modes are *timing*, not list contents. Knowing the typical gap between
orders lets the bot open the conversation at the right moment instead
of on an arbitrary calendar day.
"""
from __future__ import annotations

import datetime
import logging
import statistics

from .history import fetch_order_history, summarise, seed_product_memory
from .stock import build_from_orders

logger = logging.getLogger(__name__)

# Guard rails for the learned gap: below 4 days the "gap" is really a
# split order; above 21 the data is too sparse to call a rhythm and the
# user said weekly-to-ten-days anyway.
MIN_GAP_DAYS, MAX_GAP_DAYS, DEFAULT_GAP_DAYS = 4, 21, 8


def sync_from_orders(storage, adapter, store: str = "shufersal") -> dict:
    """One learning pass. Returns a small report of what changed."""
    orders = fetch_order_history(adapter._page)

    logged = storage.log_orders(
        [
            {
                "code": order["code"],
                "placed_at": order["created"].isoformat() if order.get("created") else "",
                "item_count": len(order.get("entries") or []),
            }
            for order in orders
            if order.get("code")
        ],
        store=store,
    )

    items = build_from_orders(orders)
    storage.replace_stock_items(store, items)
    remembered = seed_product_memory(storage, summarise(orders), store=store)

    storage.set_state("last_learn_sync", datetime.datetime.now(datetime.timezone.utc).isoformat())
    report = {
        "orders_seen": len(orders),
        "new_orders": logged,
        "stock_items": len(items),
        "remembered": remembered,
    }
    logger.info("Learn sync: %s", report)
    return report


def typical_gap_days(storage, store: str = "shufersal") -> float:
    """The household's ordering rhythm — stated if given, else learned.

    The stated value wins because the learned one is structurally biased
    here: this store's order log only sees *its own* orders, and the
    household splits shopping across two chains, so gaps at one chain
    overstate the real fridge cycle (the data says ~21 days; the
    household says 7-10). When they tell us their rhythm, believe them.
    """
    stated = storage.get_state("target_gap_days")
    if stated:
        try:
            return float(stated)
        except ValueError:
            pass
    dates = []
    for raw in storage.order_dates(store):
        try:
            dates.append(datetime.datetime.fromisoformat(raw))
        except ValueError:
            continue
    if len(dates) < 4:
        return DEFAULT_GAP_DAYS
    dates.sort()
    # Recent behaviour beats ancient history: the household's rhythm
    # changed once before (when a second chain entered), so learn from
    # the last year, not everything ever.
    cutoff = dates[-1] - datetime.timedelta(days=365)
    recent = [d for d in dates if d >= cutoff]
    gaps = [
        (recent[i + 1] - recent[i]).days
        for i in range(len(recent) - 1)
        if (recent[i + 1] - recent[i]).days >= 1
    ]
    if not gaps:
        return DEFAULT_GAP_DAYS
    gap = statistics.median(gaps)
    return float(min(MAX_GAP_DAYS, max(MIN_GAP_DAYS, gap)))


def days_since_last_order(storage, store: str = "shufersal") -> float | None:
    dates = storage.order_dates(store)
    if not dates:
        return None
    try:
        last = datetime.datetime.fromisoformat(max(dates))
    except ValueError:
        return None
    now = datetime.datetime.now(last.tzinfo) if last.tzinfo else datetime.datetime.now()
    return (now - last).total_seconds() / 86400.0


def digest_due(storage, store: str = "shufersal") -> tuple[bool, str]:
    """Should the bot open the conversation now, and why / why not.

    Due when the household has drifted past its own typical gap. Not
    re-sent for the same order cycle: once a digest went out, the next
    one waits for either a new order or three more days — a reminder,
    not a nag.
    """
    since = days_since_last_order(storage, store)
    if since is None:
        return False, "אין עדיין היסטוריית הזמנות"
    gap = typical_gap_days(storage, store)
    if since < gap:
        return False, f"עברו {since:.0f} ימים; הקצב שלכם הוא ~{gap:.0f}"

    last_sent = storage.get_state("last_digest_sent")
    if last_sent:
        try:
            sent = datetime.datetime.fromisoformat(last_sent)
            now = datetime.datetime.now(sent.tzinfo) if sent.tzinfo else datetime.datetime.now()
            if (now - sent).days < 3:
                return False, "דייג'סט כבר נשלח למחזור הזה"
        except ValueError:
            pass
    return True, f"עברו {since:.0f} ימים מההזמנה האחרונה (הקצב שלכם ~{gap:.0f})"
