"""Prefer the price-controlled version of a staple.

Israel caps the price of a short list of basics — plain milk, ordinary
eggs, standard white cheese, butter, plain bread, salt. The enriched or
premium variant sitting beside it on the shelf is not controlled and is
priced freely, so the gap is often large for a difference the household
does not care about.

Measured on a real basket (2026-09-01): omega-3 eggs at ₪21.90 where
controlled eggs were ₪13.13 — **₪8.77 on one line**, for eggs. The same
basket already bought the controlled milk (₪7.35 carton rather than the
₪9.90 enriched bottle), so this is a nudge, not a rule.

Deliberately advisory. The user's framing was "prefer, not dramatic": a
household may want the enriched product, and silently swapping it would
be exactly the kind of unasked-for substitution that makes an assistant
untrustworthy. This suggests; the person decides.
"""
from __future__ import annotations

from dataclasses import dataclass

# Words marking the *uncontrolled* premium variant of a controlled staple.
# Kept short and specific: a loose match here silently rewrites a shopping
# list, which is worse than missing a saving.
PREMIUM_MARKERS = (
    "אומגה",
    "מועשר",
    "מועשרת",
    "אורגני",
    "אורגנית",
    "חופש",
    "מרעה",
    "פרימיום",
    "ביו",
)

# The staples that are actually under price control, with the search term
# used to find the controlled equivalent in the catalogue.
CONTROLLED_STAPLES = {
    "ביצים": "ביצי משק טריות",
    "ביצי": "ביצי משק טריות",
    "חלב": "חלב בקרטון",
    "גבינה לבנה": "גבינה לבנה 5%",
    "חמאה": "חמאה",
    "לחם": "לחם אחיד",
    "מלח": "מלח שולחן",
}


@dataclass(frozen=True)
class Swap:
    """A cheaper controlled equivalent for something in the basket."""

    original_name: str
    original_price: float
    controlled_name: str
    controlled_price: float

    @property
    def saving(self) -> float:
        return round(self.original_price - self.controlled_price, 2)


def is_premium_variant(name: str) -> bool:
    """Does this name mark the uncontrolled version of a staple?"""
    text = name or ""
    return any(marker in text for marker in PREMIUM_MARKERS)


def _staple_for(name: str) -> str | None:
    for staple, search_term in CONTROLLED_STAPLES.items():
        if staple in (name or ""):
            return search_term
    return None


def suggest_swap(storage, name: str, price: float) -> Swap | None:
    """A controlled equivalent for one item, when there is a real saving.

    Returns None unless the item is a premium variant of a controlled
    staple *and* the controlled version is genuinely cheaper — a swap that
    saves nothing is noise.
    """
    if not is_premium_variant(name):
        return None
    search_term = _staple_for(name)
    if not search_term:
        return None

    for product in storage.search_products(search_term, 6):
        if is_premium_variant(product.name):
            continue
        if product.price and product.price < price:
            return Swap(
                original_name=name,
                original_price=price,
                controlled_name=product.name,
                controlled_price=product.price,
            )
    return None


def review_basket(storage, items) -> list[Swap]:
    """Controlled-price swaps available for a basket.

    ``items`` are dicts with ``name`` and ``price``.
    """
    swaps = []
    for item in items:
        swap = suggest_swap(storage, item.get("name", ""), float(item.get("price") or 0))
        if swap and swap.saving > 0:
            swaps.append(swap)
    return sorted(swaps, key=lambda s: s.saving, reverse=True)


def format_swaps(swaps: list[Swap]) -> str:
    from .mdtext import escape

    if not swaps:
        return ""
    total = sum(s.saving for s in swaps)
    lines = [f"*מוצרים בפיקוח — חיסכון אפשרי ₪{total:.2f}*", ""]
    for swap in swaps:
        lines.append(
            f"• {escape(swap.original_name)} ₪{swap.original_price:.2f} → "
            f"*{escape(swap.controlled_name)}* ₪{swap.controlled_price:.2f} "
            f"_(₪{swap.saving:.2f})_"
        )
    lines.append("")
    lines.append("_הצעה בלבד — אם העדפתם את המוצר המקורי, הוא נשאר._")
    return "\n".join(lines)
