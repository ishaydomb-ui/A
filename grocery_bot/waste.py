"""What the household threw away, and what to do about it.

The only input the bot cannot obtain for itself. Order history shows what
was *bought*; nothing anywhere shows what was *eaten*. An item half of
which is binned every week costs more than any price gap this project has
measured — Tiv Taam being cheaper on 80 of 91 comparable products is
worth a few shekels a line, while a ₪15 bag of herbs bought weekly and
thrown away fortnightly is ₪390 a year.

Design constraints, set with the user 2026-09-01 and load-bearing:

**Reporting waste is a chore and a small confession.** So it costs one
line of free text, whenever they feel like it, parsed by the existing
Hebrew NLU. Never a form, never a checklist.

**Silent weeks are normal** and go unremarked. A bot that nags about
unreported waste gets muted, and then reports nothing forever.

**The tone is factual.** This records a fact about consumption rates. It
does not have an opinion about waste, and must never acquire one.

What it produces: a consumption rate to set against the purchase rate
already known from `shelflife`. Where those two disagree, the quantity is
wrong — and that is a change the bot can actually act on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

# How much of the item was reported wasted. Kept coarse on purpose: a
# person can answer "half" instantly and cannot answer "38%" at all.
FRACTIONS = {
    "all": 1.0,
    "most": 0.75,
    "half": 0.5,
    "some": 0.25,
    "none": 0.0,
}

# Words people actually use, mapped to those fractions.
_HEBREW_FRACTIONS = (
    (("הכל", "כולו", "כולה", "כל ה"), "all"),
    (("רוב", "רובו", "רובה"), "most"),
    (("חצי", "מחצית"), "half"),
    (("קצת", "מעט", "חלק", "שליש", "רבע"), "some"),
    (("כלום", "שום דבר", "לא נזרק"), "none"),
)

# Below this share of purchases wasted, there is nothing worth saying:
# some waste is normal and unavoidable, and flagging it would be nagging.
WASTE_RATE_THRESHOLD = 0.34

# One report is an anecdote — a bad week, guests cancelled, a trip.
MIN_REPORTS_BEFORE_ACTING = 2


def fraction_for(text: str) -> float:
    """How much of an item a phrase describes. Defaults to half.

    Half is the default because it is the least wrong answer when someone
    says "זרקתי חסה" without a quantity: treating it as a whole item
    overstates, treating it as a token amount understates.
    """
    lowered = text or ""
    for words, key in _HEBREW_FRACTIONS:
        if any(word in lowered for word in words):
            return FRACTIONS[key]
    return FRACTIONS["half"]


@dataclass(frozen=True)
class WasteReport:
    """One thing the household threw away."""

    item_name: str
    fraction: float
    reported_on: str
    reported_by: str = ""


@dataclass(frozen=True)
class WastePattern:
    """A product wasted often enough to be worth changing something."""

    item_name: str
    reports: int
    total_fraction: float
    purchases: int

    @property
    def waste_rate(self) -> float:
        """Share of purchased quantity that gets binned."""
        if not self.purchases:
            return 0.0
        return round(self.total_fraction / self.purchases, 3)

    @property
    def actionable(self) -> bool:
        return (
            self.reports >= MIN_REPORTS_BEFORE_ACTING
            and self.waste_rate >= WASTE_RATE_THRESHOLD
        )

    @property
    def suggestion(self) -> str:
        """What to actually change — a quantity, not a lecture."""
        if self.waste_rate >= 0.75:
            return "כנראה לא צריך את זה בכלל בקנייה הקבועה"
        if self.waste_rate >= 0.5:
            return "כדאי חצי כמות, או אריזה קטנה יותר"
        return "כדאי לקנות בתדירות נמוכה יותר"


def record(storage, items, reported_by: str = "", today: date | None = None) -> int:
    """Store waste reports. ``items`` are (name, fraction) pairs."""
    day = (today or datetime.now(timezone.utc).date()).isoformat()
    rows = [
        (name.strip(), float(fraction), day, reported_by)
        for name, fraction in items
        if name and name.strip()
    ]
    if not rows:
        return 0
    return storage.record_waste(rows)


def patterns(storage, store: str = "shufersal") -> list[WastePattern]:
    """Products wasted often enough to justify changing the order."""
    counts = storage.waste_summary()
    stock = {row["product_name"]: row for row in storage.list_stock_items(store)}

    found = []
    for name, (reports, total) in counts.items():
        # Purchases are approximated by report count when the product is
        # not in the stock table: a household reporting waste twice bought
        # it at least twice.
        matched = _match_stock(name, stock)
        purchases = max(reports, int((matched or {}).get("picked_count") or 0) or reports)
        found.append(
            WastePattern(
                item_name=matched["product_name"] if matched else name,
                reports=reports,
                total_fraction=total,
                purchases=purchases,
            )
        )
    return sorted(
        [p for p in found if p.actionable], key=lambda p: p.waste_rate, reverse=True
    )


def _match_stock(name: str, stock: dict) -> dict | None:
    """Loosely match a spoken name to a known product."""
    needle = (name or "").strip()
    if not needle:
        return None
    if needle in stock:
        return stock[needle]
    for product_name, row in stock.items():
        if needle in product_name or product_name in needle:
            return row
    return None


def format_patterns(found: list[WastePattern]) -> str:
    from .mdtext import escape

    if not found:
        return ""
    lines = ["*מה נזרק שוב ושוב*", ""]
    for pattern in found:
        lines.append(
            f"• *{escape(pattern.item_name)}* — נזרק בערך "
            f"{pattern.waste_rate * 100:.0f}% ממה שנקנה "
            f"({pattern.reports} דיווחים)\n   _{pattern.suggestion}_"
        )
    return "\n".join(lines)


def acknowledge(items) -> str:
    """The reply to a waste report. Factual, and never a comment on waste."""
    from .mdtext import escape

    if not items:
        return "לא הבנתי מה נזרק — אפשר לכתוב למשל 'זרקתי חצי חסה ושתי עגבניות'."
    names = ", ".join(escape(name) for name, _ in items)
    return f"רשמתי: {names}. אתחשב בזה בכמויות בקנייה הבאה."
