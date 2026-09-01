"""Is Shufersal's "הוסף וחסוך" actually worth taking?

The store shows an *Add & save* button beside a cart line whenever a
multi-buy promotion exists — "2 for ₪26", "4 for ₪20". It never says what
the per-unit price becomes, so the household has been accepting or
ignoring these on instinct. This works the arithmetic out.

Three things make it less obvious than it looks:

**The feed's discount_rate cannot be trusted for multi-buy.** Verified on
real rows: "2ב26 טונה בשמן צמחי/קנולה" reports ``discount_rate = 0``
while genuinely saving ₪2.50 a tin, and "4ב20 פסטות" also reports 0 while
halving the price. The rate is only populated for single-unit discounts.
Per-unit price is therefore always computed from ``discounted_price /
min_qty``, never read off the field.

**A promotion needing one unit is not an upsell at all.** ``min_qty = 1``
means the price is already lower at the quantity in the cart; nothing
needs adding, and presenting it as "add & save" would push the household
into buying a second one for no reason.

**Buying two only saves money if both get used.** A multi-buy on tinned
tuna is close to free money; the same offer on yoghurt is how a fridge
fills with things that get thrown away. So every recommendation carries
whether the item keeps, and perishables are ranked below shelf-stable
goods at equal saving rather than being hidden — see the waste-tracking
work for the other half of this.
"""
from __future__ import annotations

from dataclasses import dataclass

from .radar import PANTRYABLE_DEPARTMENTS

# Below this the arithmetic is real but not worth a line in a message.
MIN_UNIT_SAVING = 0.50

# A multi-buy that demands a lot of units is a different decision from
# "take one more" — six tins is a storage question, not a price one.
MAX_SENSIBLE_QTY = 6
# Gift-coupon rows sit in the same table as real promotions and are dated
# years out ("קופון 50ש\"ח מתנה", valid to 2031); they are not price cuts
# and would dominate every result. They are excluded by how far away the
# end date is, not by a hardcoded year — the original `< '2027'` test
# would have quietly stopped finding any promotion at all in 2027.
MAX_PROMOTION_HORIZON_DAYS = 400


def _promotion_horizon(today=None) -> str:
    from datetime import date, timedelta

    return ((today or date.today()) + timedelta(days=MAX_PROMOTION_HORIZON_DAYS)).isoformat()


@dataclass(frozen=True)
class MultiBuyOffer:
    """One "add & save" offer, with the arithmetic actually done."""

    item_code: str
    name: str
    regular_price: float
    promo_total: float
    min_qty: float
    description: str
    ends_at: str = ""
    department: str = ""

    @property
    def unit_price(self) -> float:
        """What each unit costs once the offer is taken."""
        if not self.min_qty:
            return self.promo_total
        return round(self.promo_total / self.min_qty, 2)

    @property
    def unit_saving(self) -> float:
        return round(self.regular_price - self.unit_price, 2)

    @property
    def saving_rate(self) -> float:
        if not self.regular_price:
            return 0.0
        return round(self.unit_saving / self.regular_price, 4)

    @property
    def extra_units(self) -> float:
        """How many more units the household must buy to qualify."""
        return max(0.0, self.min_qty - 1)

    @property
    def extra_outlay(self) -> float:
        """Extra money leaving the account today to take the offer.

        The saving is per unit, but the household still pays more in total
        than it would for one. Reporting only the saving makes every offer
        look free.
        """
        return round(self.promo_total - self.regular_price, 2)

    @property
    def keeps(self) -> bool | None:
        """Does this keep in a cupboard? None when we genuinely don't know.

        The department comes from the household's own purchase history, so
        anything never bought before has none. Treating that absence as
        "perishable" quietly demoted tinned tomatoes below ketchup; an
        unknown is reported as unknown instead of guessed.
        """
        if not self.department:
            return None
        return self.department in PANTRYABLE_DEPARTMENTS

    @property
    def is_upsell(self) -> bool:
        """Does taking this require buying more than the cart holds?"""
        return self.min_qty > 1

    @property
    def worth_taking(self) -> bool:
        return (
            self.unit_saving >= MIN_UNIT_SAVING
            and self.min_qty <= MAX_SENSIBLE_QTY
            and self.unit_price > 0
        )


def offers_for_items(storage, item_codes) -> list[MultiBuyOffer]:
    """Live multi-buy offers for the given catalogue items, best first."""
    offers: list[MultiBuyOffer] = []
    for code in dict.fromkeys(str(c) for c in item_codes):
        product = storage.catalog_price(code)
        if not product:
            continue
        for promo in _promotions_for(storage, code):
            offer = MultiBuyOffer(
                item_code=code,
                name=product["name"],
                regular_price=float(product["price"]),
                promo_total=float(promo["discounted_price"]),
                min_qty=float(promo["min_qty"] or 1),
                description=promo["description"],
                ends_at=promo["ends_at"],
                department=promo.get("department", "") or "",
            )
            if offer.worth_taking:
                offers.append(offer)
    # Money first. Shelf-life only breaks ties: ranking by it ahead of the
    # saving put a ₪0.90 offer above an ₪8.90 one purely because the
    # cheaper item happened to have a known department.
    return sorted(
        offers,
        key=lambda o: (o.unit_saving, o.keeps is True),
        reverse=True,
    )


def _promotions_for(storage, item_code: str) -> list[dict]:
    """Currently-running promotions for one item.

    Long-dated coupon rows ("קופון 50ש\"ח מתנה", valid to 2031) are not
    price promotions and would otherwise dominate every result.
    """
    from contextlib import closing

    with closing(storage._connect()) as conn:  # noqa: SLF001 - storage-internal
        rows = conn.execute(
            "SELECT p.*, COALESCE(s.department, '') AS department "
            "FROM catalog_promotions p "
            "LEFT JOIN stock_items s ON s.product_code = 'P_' || p.item_code "
            "WHERE p.item_code = ? AND p.discounted_price > 0 "
            "  AND p.ends_at != '' AND p.ends_at < ? "
            "  AND p.discounted_price < p.min_qty * ("
            "      SELECT price FROM catalog_products WHERE item_code = p.item_code)",
            (str(item_code), _promotion_horizon()),
        ).fetchall()
    return [dict(row) for row in rows]


def format_offers(offers: list[MultiBuyOffer], limit: int = 8) -> str:
    """A Telegram summary of which "add & save" buttons are worth pressing."""
    from .mdtext import escape

    if not offers:
        return "לא מצאתי מבצעי *הוסף וחסוך* ששווים את זה על מה שבסל."

    lines = ["*הוסף וחסוך — מה באמת משתלם*", ""]
    for offer in offers[:limit]:
        keeps = {True: "🧺", False: "🥬", None: "•"}[offer.keeps]
        lines.append(
            f"{keeps} *{escape(offer.name)}*\n"
            f"   ₪{offer.regular_price:.2f} → *₪{offer.unit_price:.2f}* ליחידה "
            f"(חיסכון ₪{offer.unit_saving:.2f}, {offer.saving_rate * 100:.0f}%)"
        )
        if offer.is_upsell:
            lines.append(
                f"   _צריך לקנות {offer.min_qty:.0f} — תוספת של "
                f"₪{offer.extra_outlay:.2f} היום_"
            )
        else:
            lines.append("   _המחיר כבר מוזל, אין צורך להוסיף יחידות_")
    lines.append("")
    lines.append("_🧺 נשמר במזווה · 🥬 מתקלקל — כדאי רק אם באמת ייאכל · • לא ידוע_")
    return "\n".join(lines)
