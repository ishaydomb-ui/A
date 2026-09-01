"""Deals worth interrupting someone for.

Distinct from `radar`, which looks for stock-up bargains inside the
household's usual shop. This looks across *every* chain now in the
database — including the five they do not shop at — and asks a harder
question: is this cheap enough that it should change what they do?

Two categories qualify, and they qualify for different reasons:

**Things they buy often.** A deep cut on a weekly product compounds. The
saving is small per unit and large per year.

**Expensive things that keep.** Nappies, formula, toilet paper, cleaning
supplies, baby wipes. These are the items where a one-off order from an
unfamiliar chain genuinely pays, because the saving is tens of shekels
per unit and nothing spoils while it waits. The household has a baby due
in January, which makes this category worth watching from late in the
year rather than now.

The bar is deliberately high. A weekly message listing thirty small
discounts gets muted, and then the one that mattered goes unread too.
"""
from __future__ import annotations

from dataclasses import dataclass

from .chains import display_name, is_regular

# A cut worth a message. Below this it is ordinary price movement and the
# weekly comparison already covers it.
MIN_DISCOUNT = 0.20

# For an expensive keeper, the shekels matter more than the percentage:
# 15% off nappies is worth more than half off a tin of corn.
MIN_ABSOLUTE_SAVING = 12.0

# At most this many deals from one product family. Without it the first
# run returned six lines of nappies in eight: all true, all the same
# decision, and the two other findings pushed off the end of the message.
MAX_PER_FAMILY = 2

# Categories where a deep discount justifies buying ahead, because the
# product does not spoil and the unit price is high. Matched on the
# product name, so kept broad but specific enough not to catch food.
# Written as stems, without the final letter, because Hebrew changes it
# when a word is pluralised: "מגבון" ends in a final nun (ן) while
# "מגבונים" uses the medial form (נ), so the singular is not a substring
# of the plural and a naive match silently misses every packet of wipes.
STOCKABLE_PATTERNS = (
    "חיתול", "פמפרס", "האגיס", "טיטול",
    "מגבונ", "מגבון",
    "סימילאק", "מטרנה", "תמ\"ל", "תמל",
    "נייר טואלט", "מגבת נייר", "טישו",
    "אבקת כביסה", "ג'ל כביסה", "מרכך כביסה", "אקונומיקה",
    "נוזל כלים", "מטהר", "סבון",
    "שמפו", "מרכך שיער", "משחת שיניים", "דאודורנט",
    "מוצץ", "בקבוק לתינוק",
)


def is_stockable(name: str) -> bool:
    """Does this keep indefinitely and cost enough to be worth stocking?"""
    text = name or ""
    return any(pattern in text for pattern in STOCKABLE_PATTERNS)


@dataclass(frozen=True)
class HotDeal:
    barcode: str
    name: str
    chain: str
    price: float
    reference_price: float
    reference_chain: str = "shufersal"
    bought_often: bool = False

    @property
    def saving(self) -> float:
        return round(self.reference_price - self.price, 2)

    @property
    def discount(self) -> float:
        if not self.reference_price:
            return 0.0
        return round(self.saving / self.reference_price, 3)

    @property
    def stockable(self) -> bool:
        return is_stockable(self.name)

    @property
    def worth_reporting(self) -> bool:
        if self.saving <= 0:
            return False
        # An expensive keeper clears on shekels; everything else has to
        # clear on percentage, so a cheap item cannot shout.
        if self.stockable and self.saving >= MIN_ABSOLUTE_SAVING:
            return True
        return self.discount >= MIN_DISCOUNT and self.saving >= 2.0

    @property
    def reason(self) -> str:
        if self.stockable:
            return "לא מתקלקל, שווה לאגור"
        if self.bought_often:
            return "אתם קונים את זה הרבה"
        return "הנחה עמוקה"


def find(storage, chains=None, limit: int = 8) -> list[HotDeal]:
    """Deep discounts across every chain, on things this household cares about.

    The reference price is Shufersal's current shelf price, because it is
    the chain the household actually uses and therefore the price they
    would otherwise pay.
    """
    frequent = {
        row["product_name"]
        for store in ("shufersal", "tivtaam")
        for row in storage.list_stock_items(store)
        if row.get("tier") in ("A", "B")
    }

    from .chains import CHAIN_NAMES

    candidates = chains or [c for c in CHAIN_NAMES if c != "shufersal"]
    deals: list[HotDeal] = []
    for chain in candidates:
        for barcode, row in storage.latest_store_prices(chain).items():
            reference = storage.catalog_price(barcode)
            if not reference or not reference.get("price"):
                continue
            deal = HotDeal(
                barcode=barcode,
                name=reference["name"],
                chain=chain,
                price=row["price"],
                reference_price=reference["price"],
                bought_often=reference["name"] in frequent,
            )
            # Only surface something they buy, or something worth
            # stocking: a bargain on an item they never buy is noise.
            if not (deal.bought_often or deal.stockable):
                continue
            if deal.worth_reporting:
                deals.append(deal)

    deals.sort(key=lambda d: (d.stockable, d.saving), reverse=True)
    return _dedupe(deals)[:limit]


def _family(name: str) -> str:
    """A crude product family, used only to stop one category dominating."""
    for pattern in STOCKABLE_PATTERNS:
        if pattern in (name or ""):
            return pattern
    return " ".join((name or "").split()[:2])


def _dedupe(deals: list[HotDeal]) -> list[HotDeal]:
    """Cheapest chain per product, and no more than a couple per family."""
    best: dict[str, HotDeal] = {}
    for deal in deals:
        current = best.get(deal.barcode)
        if current is None or deal.price < current.price:
            best[deal.barcode] = deal

    ordered = sorted(best.values(), key=lambda d: (d.stockable, d.saving), reverse=True)
    seen: dict[str, int] = {}
    diverse = []
    for deal in ordered:
        family = _family(deal.name)
        if seen.get(family, 0) >= MAX_PER_FAMILY:
            continue
        seen[family] = seen.get(family, 0) + 1
        diverse.append(deal)
    return diverse


def format_deals(deals: list[HotDeal]) -> str:
    from .mdtext import escape

    if not deals:
        return ""
    lines = ["*מבצעים ששווה להסתכל עליהם*", ""]
    for deal in deals:
        where = display_name(deal.chain)
        tag = "" if is_regular(deal.chain) else " ⚡"
        lines.append(
            f"• *{escape(deal.name)}* — ₪{deal.price:.2f} ב{escape(where)}{tag} "
            f"מול ₪{deal.reference_price:.2f} "
            f"_(חיסכון ₪{deal.saving:.2f}, {deal.discount * 100:.0f}% · {deal.reason})_"
        )
    if any(not is_regular(d.chain) for d in deals):
        lines.append("")
        lines.append("_⚡ = רשת שאתם לא קונים בה בדרך כלל_")
    return "\n".join(lines)
