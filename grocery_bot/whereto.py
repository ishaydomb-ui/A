"""Where should this week's shop go?

The household is flexible about which chain they use. What kept them at
one shop was never loyalty — it was having to rebuild the list from
scratch somewhere else. Once building a list is cheap, the right chain is
simply whichever is cheapest this week, and that changes week to week.

So this does *not* ask "is this one deal worth its own delivery". That
framing was wrong, and the household said so: they will happily move the
entire weekly shop to whichever chain has a good enough reason. It asks
the whole question instead — **what does my actual basket cost at each
chain** — and answers with one chain and a number.

Two rules that keep the answer honest:

**Only price what is actually known.** A chain that carries 12 of 30
basket items cannot be compared on total alone; the missing 18 would be
bought there too, at unknown prices. So the comparison is always made on
the *shared* subset, and the coverage is reported alongside, because a
₪30 saving measured over a third of the basket is a much weaker claim
than the same saving over all of it.

**Delivery is part of the price.** A ₪25 saving is not a saving if the
delivery costs ₪30 more, and the household's own chains differ (₪35.90 at
Shufersal, ₪29.90 at Tiv Taam).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .chains import display_name, is_regular

# Published delivery charges, used so a comparison is of the total cost
# of shopping rather than of shelf prices alone. Approximate and easy to
# correct; the household's real Shufersal charge is on every order.
DELIVERY_FEES = {
    "shufersal": 35.90,
    "tivtaam": 29.90,
    "victory": 29.90,
    "ramilevy": 29.00,
    "osherad": 29.00,
    "keshet": 29.00,
    "politzer": 29.00,
    "freshmarket": 29.00,
}

# Below this share of the basket, a chain's total is not a fair
# comparison — it is a different, smaller basket.
MIN_COVERAGE = 0.4

# Moving an entire weekly shop has a real cost in attention even when the
# list is generated. Not worth it to save the price of a coffee.
WORTH_SWITCHING = 20.0


@dataclass
class ChainQuote:
    """What one chain would charge for the shared part of the basket."""

    chain: str
    matched: int
    total_lines: int
    subtotal: float
    baseline_subtotal: float
    missing: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.matched / self.total_lines if self.total_lines else 0.0

    @property
    def comparable(self) -> bool:
        return self.coverage >= MIN_COVERAGE

    @property
    def delivery(self) -> float:
        return DELIVERY_FEES.get(self.chain, 29.00)

    @property
    def saving(self) -> float:
        """Against the baseline chain, on the shared items, after delivery."""
        return round(self.baseline_subtotal - self.subtotal, 2)

    @property
    def saving_with_delivery(self) -> float:
        baseline_delivery = DELIVERY_FEES.get("shufersal", 35.90)
        return round(self.saving + (baseline_delivery - self.delivery), 2)

    @property
    def worth_switching(self) -> bool:
        return self.comparable and self.saving_with_delivery >= WORTH_SWITCHING


def quote_basket(storage, basket, baseline: str = "shufersal", chains=None) -> list[ChainQuote]:
    """Price a basket at every chain with data.

    ``basket`` items need ``barcode``, ``units`` and ``price`` — the last
    being what the baseline chain charges, used both as the baseline and
    as the stand-in for items a chain does not carry.
    """
    items = [i for i in basket if i.get("barcode")]
    if not items:
        return []

    candidates = chains or [c for c in DELIVERY_FEES if c != baseline]
    quotes = []
    for chain in candidates:
        known = storage.latest_store_prices(chain)
        if not known:
            continue
        matched, subtotal, baseline_subtotal, missing = 0, 0.0, 0.0, []
        for item in items:
            units = float(item.get("units") or 1)
            baseline_price = float(item.get("price") or 0)
            row = known.get(str(item["barcode"]))
            if row:
                matched += 1
                subtotal += row["price"] * units
                baseline_subtotal += baseline_price * units
            else:
                missing.append(item.get("name") or str(item["barcode"]))
        quotes.append(
            ChainQuote(
                chain=chain,
                matched=matched,
                total_lines=len(items),
                subtotal=round(subtotal, 2),
                baseline_subtotal=round(baseline_subtotal, 2),
                missing=missing,
            )
        )
    return sorted(quotes, key=lambda q: q.saving_with_delivery, reverse=True)


def best_switch(quotes: list[ChainQuote]) -> ChainQuote | None:
    """The one chain worth moving the whole shop to, if any."""
    worthwhile = [q for q in quotes if q.worth_switching]
    return worthwhile[0] if worthwhile else None


def format_quotes(quotes: list[ChainQuote], limit: int = 5) -> str:
    from .mdtext import escape

    usable = [q for q in quotes if q.comparable]
    if not usable:
        return "אין מספיק חפיפה בין הסלים כדי להשוות רשתות השבוע."

    lines = ["*כמה יעלה אותו סל במקום אחר*", ""]
    for quote in usable[:limit]:
        mark = "🟢" if quote.worth_switching else "▪️"
        regular = "" if is_regular(quote.chain) else " _(רשת שאתם לא קונים בה)_"
        lines.append(
            f"{mark} *{escape(display_name(quote.chain))}*{regular}\n"
            f"   ₪{quote.subtotal:.2f} מול ₪{quote.baseline_subtotal:.2f} "
            f"({quote.matched}/{quote.total_lines} פריטים) → "
            f"*{quote.saving_with_delivery:+.2f} ₪* כולל משלוח"
        )
    best = best_switch(quotes)
    lines.append("")
    if best:
        lines.append(
            f"_שווה לשקול להעביר את כל הקנייה ל{escape(display_name(best.chain))} השבוע._"
        )
    else:
        lines.append("_אין הפרש שמצדיק מעבר השבוע._")
    return "\n".join(lines)
