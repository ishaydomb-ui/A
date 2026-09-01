"""Catch the money that is lost in the last minute before paying.

Two things went wrong on both of the household's last two orders, and
they went wrong together, which is why they are handled together.

**The gift threshold was missed twice running.** Shufersal gives a free
gift at ₪599; the basket qualified at ₪560.79 on 1 September and ₪521.19
on 24 August — short by ₪38.21 and ₪77.81. Both times the shortfall was
smaller than the value of the deals also being left on the table.

**Multi-buy offers sat one unit short.** Five of them on the September
order: 2-for on tuna, ketchup, tinned tomatoes and pasta sauce, 3-for on
pasta. Each second unit is cheaper *per unit* and also pushes the basket
toward the threshold — so the two problems have one answer, and solving
them separately would miss that.

This runs at the only moment it can work: after the cart is built and
before the household is handed over to pay. It never adds anything
itself. Buying something unwanted to reach a threshold is not a saving,
and deciding that is not the bot's call.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .multibuy import MultiBuyOffer, offers_for_items

# Shufersal's online gift threshold. Read from the order payload when
# present — `conditionValue` on a promotionOrderEntry — and used as a
# fallback when composing before an order exists.
DEFAULT_GIFT_THRESHOLD = 599.0

# Chasing a threshold from further away than this means buying things
# nobody wanted. At that distance the honest advice is to ignore it.
MAX_SENSIBLE_SHORTFALL = 120.0


@dataclass
class ThresholdCheck:
    """What the household could still change before paying."""

    basket_total: float
    threshold: float = DEFAULT_GIFT_THRESHOLD
    offers: list[MultiBuyOffer] = field(default_factory=list)
    reward: str = "מתנה לבחירה"
    # How many of each product the basket already holds. Without this the
    # check happily tells the household to buy a second chocolate when
    # two are already in the cart and the 2-for has been applied —
    # verified against the 1 September order, where four of six "missed"
    # offers had in fact been taken.
    quantities: dict = field(default_factory=dict)

    @property
    def shortfall(self) -> float:
        return round(max(0.0, self.threshold - self.basket_total), 2)

    @property
    def qualifies(self) -> bool:
        return self.basket_total >= self.threshold

    @property
    def worth_chasing(self) -> bool:
        return 0 < self.shortfall <= MAX_SENSIBLE_SHORTFALL

    @property
    def upsells(self) -> list[MultiBuyOffer]:
        """Offers still one or more units short of qualifying."""
        return [
            offer
            for offer in self.offers
            if offer.is_upsell
            and offer.worth_taking
            and self.quantities.get(offer.item_code, 1) < offer.min_qty
        ]

    @property
    def closing_offers(self) -> list[MultiBuyOffer]:
        """Upsells that would also carry the basket over the threshold.

        Ordered by how much they save, not by how neatly they close the
        gap: the point is to spend well, not to spend exactly.
        """
        if not self.worth_chasing:
            return []
        return sorted(
            [o for o in self.upsells if o.extra_outlay >= self.shortfall],
            key=lambda o: o.unit_saving,
            reverse=True,
        )

    def combination_to_close(self, max_items: int = 3) -> list[MultiBuyOffer]:
        """The best few upsells that together clear the threshold.

        On the real 1 September order no single offer closed a ₪38.21 gap,
        but tuna and chocolate together did — and both were worth taking
        on their own merits anyway. Saying "none of these is enough" and
        stopping there would have been true and useless.

        Chosen by saving first, so the suggestion is things worth buying
        that happen to close the gap, rather than the tightest fit.
        """
        if not self.worth_chasing:
            return []
        chosen, spend = [], 0.0
        for offer in sorted(self.upsells, key=lambda o: o.unit_saving, reverse=True):
            if len(chosen) >= max_items:
                break
            chosen.append(offer)
            spend += offer.extra_outlay
            if spend >= self.shortfall:
                return chosen
        return []

    @property
    def total_upsell_value(self) -> float:
        return round(sum(o.unit_saving for o in self.upsells), 2)


def check(storage, basket_total: float, item_codes, threshold: float | None = None,
          reward: str = "מתנה לבחירה", quantities: dict | None = None) -> ThresholdCheck:
    """Look at a finished basket for what is still worth changing.

    ``quantities`` maps a product code to how many are already in the
    basket. Omitting it assumes one of each, which is the safe default
    but will over-report on a basket that already took its offers.
    """
    return ThresholdCheck(
        basket_total=round(float(basket_total), 2),
        threshold=float(threshold or DEFAULT_GIFT_THRESHOLD),
        offers=offers_for_items(storage, item_codes),
        reward=reward,
        quantities=dict(quantities or {}),
    )


def threshold_from_order(order: dict) -> tuple[float, float, str] | None:
    """Read a live threshold promotion out of a Shufersal order payload.

    Better than the hard-coded figure whenever it is available: the store
    states the target, the qualifying subtotal and how far off it is, and
    the qualifying subtotal is *not* the basket total — delivery and some
    lines do not count toward it.
    """
    for entry in order.get("entries") or []:
        for promotion in entry.get("promotionOrderEntries") or []:
            if promotion.get("conditionType") != "Amount":
                continue
            value = promotion.get("conditionValue")
            actual = promotion.get("conditionActualValue")
            if value and actual is not None:
                return (
                    float(value),
                    float(actual),
                    promotion.get("promotionMessage") or "מתנה",
                )
    return None


def format_check(result: ThresholdCheck) -> str:
    """The message shown just before the household goes to pay."""
    from .mdtext import escape

    lines = []
    if result.qualifies:
        lines.append(f"✅ *הסל עובר את הסף של ₪{result.threshold:.0f}* — {escape(result.reward)}")
    elif result.worth_chasing:
        lines.append(
            f"⚠️ *חסרים ₪{result.shortfall:.2f} לסף של ₪{result.threshold:.0f}* "
            f"({escape(result.reward)})"
        )
    else:
        lines.append(f"_הסל רחוק מהסף של ₪{result.threshold:.0f} — לא שווה לרדוף אחריו._")

    if result.upsells:
        lines += ["", "*יחידה שנייה שמוזילה את המחיר ליחידה:*"]
        for offer in result.upsells[:5]:
            lines.append(
                f"• *{escape(offer.name)}* — עוד אחד ותשלמו "
                f"₪{offer.unit_price:.2f} במקום ₪{offer.regular_price:.2f} "
                f"_(תוספת ₪{offer.extra_outlay:.2f} היום, חיסכון "
                f"₪{offer.unit_saving:.2f} ליחידה)_"
            )

    closing = result.closing_offers
    if closing:
        lines += [
            "",
            f"_כל אחד מאלה גם יעביר אתכם את הסף של ₪{result.threshold:.0f}._",
        ]

    if not result.qualifies and result.worth_chasing and not closing and result.upsells:
        combination = result.combination_to_close()
        if combination:
            names = " + ".join(escape(o.name) for o in combination)
            spend = sum(o.extra_outlay for o in combination)
            saved = sum(o.unit_saving for o in combination)
            lines += [
                "",
                f"_{names} ביחד — תוספת ₪{spend:.2f}, חיסכון ₪{saved:.2f}, "
                f"וגם עוברים את הסף._",
            ]
        else:
            lines += [
                "",
                f"_אף אחד מהם לבדו לא מספיק לסף — צריך עוד ₪{result.shortfall:.2f}._",
            ]

    lines += ["", "_לא מוסיף כלום מעצמי — ההחלטה שלכם._"]
    return "\n".join(lines)
