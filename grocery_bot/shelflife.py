"""When does a cupboard item actually need buying again?

Nearly half of a real Shufersal basket — ₪262 of ₪604 on 2026-08-24 —
was shelf-stable: olive oil, rice, soy sauce, tinned corn, couscous,
earplugs. None of it is weekly shopping, and proposing it on the weekly
rhythm is how a cupboard ends up with four bottles of soy sauce while the
household still runs out of milk.

The signal was already in the database and unused. ``stock_items.share``
is the fraction of past orders containing a product, so its reciprocal is
how many orders typically pass between purchases: a share of 0.16 means
roughly every sixth order. Multiplied by the household's real order gap
that becomes a expected interval in days, which can be compared against
when the item was last actually bought.

Two deliberate asymmetries:

**Perishables are exempt.** Milk has a short interval for a different
reason — it is consumed, not stocked — and the weekly rhythm already
handles it. Only pantryable departments are gated on an interval.

**Being early is worse than being late for cheap items, and the reverse
for expensive ones.** Buying a ₪7 tin early wastes ₪7; running out of
olive oil mid-cooking costs a trip. So the reminder leans early for
staples and the caller can widen the window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .radar import PANTRYABLE_DEPARTMENTS

# The household orders roughly weekly (their own words: "weekly or every
# 10 days"), which is the multiplier turning "every Nth order" into days.
DEFAULT_ORDER_GAP_DAYS = 8.0

# One purchase is not a pattern. Below roughly two appearances in the
# ~20 orders Shufersal exposes, the reciprocal is not an interval at all —
# it is the length of the history. Set here after the first run returned a
# "due" list consisting entirely of items bought exactly once, ten months
# apart, all with an identical fabricated 152-day interval.
MIN_RELIABLE_SHARE = 0.10

# How close to the expected date counts as "coming up" rather than "due".
SOON_WINDOW_DAYS = 7

# Past this many intervals, "overdue" stops meaning "needed" and starts
# meaning "no longer bought". Without this the list is dominated by things
# the household abandoned: the first run surfaced baby formula 497 days
# overdue for a household whose children outgrew it, ranked above the
# coffee they actually drink. 117 of 162 items came back "due", which is
# another way of saying the answer was useless.
LAPSED_AFTER_INTERVALS = 3.0

# An absolute backstop on top of the interval rule. Shufersal exposes only
# about twenty past orders, so a product bought once in early 2025 gets a
# long computed interval and stays just inside the lapsed ratio forever.
# Anything not bought within a year is not part of current habits,
# whatever the arithmetic says.
STALE_AFTER_DAYS = 365


@dataclass(frozen=True)
class ShelfItem:
    """A cupboard product and whether it is due to be bought again."""

    product_code: str
    name: str
    department: str
    share: float
    last_purchased: date | None
    order_gap_days: float = DEFAULT_ORDER_GAP_DAYS
    # Published by the chain, where it publishes one. None means nobody
    # measured it and the share-based estimate is used instead.
    measured_interval_days: float | None = None

    @property
    def is_pantryable(self) -> bool:
        return self.department in PANTRYABLE_DEPARTMENTS

    @property
    def expected_interval_days(self) -> float | None:
        """Typical days between purchases, or None when unknowable.

        A figure the chain measured itself always wins: Tiv Taam publishes
        one per product and counts in-store purchases that never reach the
        online history. Our own value is 1/share × the household's order
        gap — a reasonable guess resting on two approximations, and only
        used where nothing better exists.
        """
        if self.measured_interval_days:
            return round(float(self.measured_interval_days), 1)
        if self.share < MIN_RELIABLE_SHARE or self.share > 1:
            return None
        return round((1 / self.share) * self.order_gap_days, 1)

    def days_since(self, today: date | None = None) -> int | None:
        if self.last_purchased is None:
            return None
        return ((today or date.today()) - self.last_purchased).days

    def status(self, today: date | None = None) -> str:
        """due | soon | stocked | lapsed | unknown."""
        interval = self.expected_interval_days
        elapsed = self.days_since(today)
        if interval is None or elapsed is None:
            return "unknown"
        if elapsed >= STALE_AFTER_DAYS or elapsed >= interval * LAPSED_AFTER_INTERVALS:
            return "lapsed"
        if elapsed >= interval:
            return "due"
        if elapsed >= interval - SOON_WINDOW_DAYS:
            return "soon"
        return "stocked"

    def days_until_due(self, today: date | None = None) -> float | None:
        interval = self.expected_interval_days
        elapsed = self.days_since(today)
        if interval is None or elapsed is None:
            return None
        return round(interval - elapsed, 1)


def build_items(storage, store: str = "shufersal", order_gap_days: float = DEFAULT_ORDER_GAP_DAYS):
    """Shelf-stable products the household buys, with their due status."""
    last = storage.last_purchase_dates(store)
    items = []
    for row in storage.list_stock_items(store):
        item = ShelfItem(
            product_code=row["product_code"],
            name=row["product_name"],
            department=row.get("department", "") or "",
            share=float(row.get("share") or 0),
            last_purchased=last.get(row["product_code"]),
            order_gap_days=order_gap_days,
            measured_interval_days=row.get("interval_days"),
        )
        if item.is_pantryable:
            items.append(item)
    return items


def due_now(storage, store: str = "shufersal", today: date | None = None) -> list[ShelfItem]:
    """Cupboard items that have gone long enough to be worth re-buying.

    Excludes lapsed products — see LAPSED_AFTER_INTERVALS.
    """
    items = [i for i in build_items(storage, store) if i.status(today) == "due"]
    # Longest overdue first: those are the ones actually about to run out.
    return sorted(items, key=lambda i: i.days_until_due(today) or 0)


def lapsed(storage, store: str = "shufersal", today: date | None = None) -> list[ShelfItem]:
    """Products the household appears to have stopped buying altogether.

    Worth surfacing separately and rarely — occasionally one is a genuine
    oversight, but proposing them weekly is noise.
    """
    return [i for i in build_items(storage, store) if i.status(today) == "lapsed"]


def not_yet(storage, store: str = "shufersal", today: date | None = None) -> list[ShelfItem]:
    """Cupboard items that should NOT be proposed — bought recently enough.

    The more valuable half of this module: it is what stops a weekly
    proposal from suggesting olive oil bought a fortnight ago.
    """
    return [i for i in build_items(storage, store) if i.status(today) == "stocked"]


def format_due(items: list[ShelfItem], limit: int = 10, today: date | None = None) -> str:
    from .mdtext import escape

    if not items:
        return "אין מוצרי מזווה שהגיע הזמן לחדש."
    lines = ["*מהמזווה — כנראה נגמר*", ""]
    for item in items[:limit]:
        overdue = -(item.days_until_due(today) or 0)
        interval = item.expected_interval_days
        lines.append(
            f"• *{escape(item.name)}* — נקנה לפני {item.days_since(today)} ימים "
            f"(בערך כל {interval:.0f})"
            + (f", באיחור של {overdue:.0f}" if overdue > 0 else "")
        )
    return "\n".join(lines)
