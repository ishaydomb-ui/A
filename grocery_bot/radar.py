"""Deals worth stocking up on, even when nothing has run out.

The user's own example: no fabric softener needed this week, but at a
deep enough discount they would buy anyway and shelf it — saving off a
future shop. That is a different question from "deals on what I am
buying today", so it gets its own scan:

- The candidate set is everything the household has EVER bought (the
  full stock table, tier D included), not the current proposal. A
  stock-up deal is by definition on something not currently needed.
- Only exceptional discounts qualify. A 5% shave is noise; the bar is
  set by MIN_DISCOUNT.
- Pantryable departments get flagged, because bananas at half price are
  not stockable and softener is. Perishables still appear — a deep
  discount on something bought weekly is worth knowing — but the 🧺
  mark tells the user which ones can actually sit in a cupboard.

Matching necessarily goes product-name -> catalog search: the web
store's product ids and the price feed's item codes are different
namespaces (verified — no code from one exists in the other), so name
search plus the same comparability guard the deals report uses is the
only join there is.
"""
from __future__ import annotations

from dataclasses import dataclass

from .catalog import _is_comparable
from .storage import Storage

# A deal is only worth a message when it is unusually deep. Below this it
# is ordinary price noise the weekly deals report already covers.
MIN_DISCOUNT = 0.25

# Departments whose products keep: the whole point of a stock-up.
PANTRYABLE_DEPARTMENTS = {
    "טיפוח, תינוקות וניקיון",
    "מזווה ושימורים",
    "יבשים ובישול",
    "קפואים ומזון בסיסי",
}

MAX_RESULTS = 10


@dataclass
class StockUpDeal:
    bought_name: str
    catalog_name: str
    shelf_price: float
    deal_price: float
    description: str
    pantryable: bool

    @property
    def discount(self) -> float:
        return 1 - self.deal_price / self.shelf_price if self.shelf_price else 0.0


def find_stockup_deals(storage: Storage, store: str = "shufersal") -> list[StockUpDeal]:
    """Exceptional deals on anything the household has ever bought."""
    deals: dict[str, StockUpDeal] = {}
    for row in storage.list_stock_items(store):
        results = storage.search_with_deals(row["product_name"], limit=6)
        if not results:
            continue
        reference = results[0][0]
        for product, promo in results:
            if promo is None or not _is_comparable(reference, product):
                continue
            if not product.price or promo.discounted_price <= 0:
                continue
            discount = 1 - promo.discounted_price / product.price
            if discount < MIN_DISCOUNT:
                continue
            # Keep the deepest deal per catalog product, whatever purchase
            # led to it.
            existing = deals.get(product.item_code)
            if existing is not None and existing.discount >= discount:
                continue
            deals[product.item_code] = StockUpDeal(
                bought_name=row["product_name"],
                catalog_name=product.name,
                shelf_price=product.price,
                deal_price=promo.discounted_price,
                description=promo.description,
                pantryable=row["department"] in PANTRYABLE_DEPARTMENTS,
            )
    ranked = sorted(deals.values(), key=lambda deal: -deal.discount)
    # Pantryable first among equals: those are the ones worth buying ahead.
    ranked.sort(key=lambda deal: (not deal.pantryable, -deal.discount))
    return ranked[:MAX_RESULTS]


def format_stockup_deals(deals: list[StockUpDeal], bot_username: str = "") -> str:
    """`bot_username` is accepted and unused; see the note about deep links."""
    if not deals:
        return (
            "אין כרגע מבצעים חריגים (25%+ הנחה) על מוצרים שאתם קונים. "
            "אבדוק שוב אחרי רענון המחירים הבא."
        )
    # Every deal here comes from the Shufersal price feed, so the chain
    # is named once at the top. Without it the list reads as chain-neutral
    # while the cross-chain deals list beside it names a chain per line —
    # and a reader cannot tell which prices these are.
    lines = ["📦 *שווה לאגור בשופרסל* — מבצעים חריגים על דברים שאתם קונים", ""]
    for deal in deals:
        mark = "🧺 " if deal.pantryable else ""
        lines.append(
            f"• {mark}{deal.catalog_name}\n"
            f"   {deal.shelf_price:.2f}₪ ⟵ *{deal.deal_price:.2f}₪* "
            f"(-{deal.discount * 100:.0f}%) — {deal.description}"
        )
    lines += ["", "_🧺 = נשמר בארון; שווה לקנות מראש. כלום לא נוסף לסל אוטומטית._"]
    # A plain command, not a deep link and not a callback button.
    #
    # A t.me deep link tapped from inside the bot's own chat arrives as a
    # bare /start with the payload stripped, so it silently does nothing.
    # A callback button works in principle but added a round trip of
    # plumbing that still had to be debugged in the household's hands.
    # Telegram renders "/chaindeals" as tappable text on its own, which
    # is the whole feature with none of the machinery — and it keeps the
    # long list out of this message, which was the point.
    lines.append("_עוד מבצעים, מכל הרשתות:_ /chaindeals")
    return "\n".join(lines)
