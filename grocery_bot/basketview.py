"""The same basket, priced at every chain, before anyone pays.

Asked for by Ishay 2026-09-06: show this basket at Tiv Taam before
buying, with anything missing or substituted marked clearly, so one look
says where the shop should go. More chains are coming, so nothing here
is written for two.

**Why this is name-matched, and what that costs.** Shufersal publishes
no barcode — not in its price feed and not in the order history, which
is why `stock_items.barcode` is empty for all 303 products. The portal
chains publish EANs. So there is no shared key between the household's
Shufersal-shaped basket and anyone else's catalogue, and the comparison
has to go through product names.

That is a real limitation and it is shown rather than hidden. Every line
carries how it was matched:

    ✅  the chain sells something whose name *starts* with the item
    🔄  the name appears, but not as the product's own leading words —
        a variant, a different size, possibly a different thing
    ❌  nothing acceptable at that chain

A total is only ever computed over lines that matched, and the coverage
is printed beside it, because "₪40 cheaper" across half the basket is a
much weaker claim than the same number across all of it — the same rule
`whereto` applies to barcode comparisons.

**Delivery counts.** A ₪25 shelf saving is not a saving against a ₪30
dearer delivery, so the fees are part of the bottom line.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .chains import display_name
from .whereto import DELIVERY_FEES

# Below this share of the basket, a total is not a comparison — it is a
# sample. Same threshold as the barcode-based comparison uses.
MIN_COVERAGE = 0.4

# How the three match states are drawn. Rank comes from
# Storage.best_name_match: 0 = the product's name starts with the term,
# 1 = the term appears as a whole word somewhere in it.
MATCH_MARKS = {0: "✅", 1: "🔄"}
MISSING_MARK = "❌"


@dataclass
class BasketLine:
    """One item of the household's basket, at one chain."""

    term: str
    quantity: float = 1
    name: str = ""
    price: float | None = None
    rank: int | None = None

    @property
    def matched(self) -> bool:
        return self.price is not None

    @property
    def substitute(self) -> bool:
        """Matched, but not on the product's own leading words."""
        return self.matched and self.rank == 1

    @property
    def line_total(self) -> float:
        return (self.price or 0.0) * (self.quantity or 1)

    @property
    def mark(self) -> str:
        return MATCH_MARKS.get(self.rank, MISSING_MARK) if self.matched else MISSING_MARK


@dataclass
class ChainBasket:
    """The whole basket as one chain would price it.

    `baseline_subtotal` is what the reference chain charges **for the
    same matched lines** — not for the whole basket. Without that, two
    chains that each carry a different half of the basket get compared
    on totals of two different baskets, which is how a chain that simply
    stocks less looks cheapest. The first run of this made exactly that
    mistake: it announced one chain ₪43 cheaper than another when the
    two had matched 14 and 14 *different* items.
    """

    store: str
    lines: list[BasketLine] = field(default_factory=list)
    baseline_subtotal: float = 0.0
    baseline_store: str = "shufersal"

    @property
    def saving(self) -> float:
        """Positive means this chain is cheaper, on like-for-like only."""
        return round(self.baseline_subtotal - self.exact_subtotal, 2)

    @property
    def saving_with_delivery(self) -> float:
        baseline_delivery = DELIVERY_FEES.get(self.baseline_store, 0.0)
        return round(self.saving + (baseline_delivery - self.delivery), 2)

    @property
    def matched(self) -> list[BasketLine]:
        return [line for line in self.lines if line.matched]

    @property
    def exact(self) -> list[BasketLine]:
        """Lines where the chain sells something actually named this.

        The money claim rests on these alone. Substitutes are shown, and
        they are what makes the coverage figure respectable, but a run
        against the real feeds paired "אצבעות גבינה צהובה" (cheese
        sticks) with "אצבעות שוקולד קרם חלב" (chocolate fingers) and
        "חלב בקרטון 3%" with long-life milk at half the price. Counting
        those toward a saving would report a discount on being sold a
        different product.
        """
        return [line for line in self.lines if line.matched and line.rank == 0]

    @property
    def missing(self) -> list[BasketLine]:
        return [line for line in self.lines if not line.matched]

    @property
    def substitutes(self) -> list[BasketLine]:
        return [line for line in self.lines if line.substitute]

    @property
    def coverage(self) -> float:
        return len(self.matched) / len(self.lines) if self.lines else 0.0

    @property
    def comparable(self) -> bool:
        return self.coverage >= MIN_COVERAGE

    @property
    def subtotal(self) -> float:
        """What the matched lines cost here, substitutes included."""
        return round(sum(line.line_total for line in self.matched), 2)

    @property
    def exact_subtotal(self) -> float:
        return round(sum(line.line_total for line in self.exact), 2)

    @property
    def delivery(self) -> float:
        return DELIVERY_FEES.get(self.store, 0.0)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.delivery, 2)


def price_basket(
    storage, items, chains: list[str] | None = None, baseline: str = "shufersal"
) -> list[ChainBasket]:
    """Price one basket at every chain we hold prices for.

    `items` are dicts with `name` and optionally `quantity`. Every chain
    is scored against `baseline` on the lines they both carry, so the
    ranking answers "cheaper for the same goods" rather than "stocks
    less of what you asked for".
    """
    if chains is None:
        chains = sorted(set(storage.priced_stores()) | {baseline})

    wanted = [
        {"name": (item.get("name") or "").strip(), "quantity": float(item.get("quantity") or 1)}
        for item in items
        if (item.get("name") or "").strip()
    ]

    # The baseline's own price per term, looked up once.
    reference: dict[str, float] = {}
    for item in wanted:
        hit = storage.best_name_match(baseline, item["name"])
        if hit:
            reference[item["name"]] = hit["price"]

    baskets = []
    for store in chains:
        basket = ChainBasket(store=store, baseline_store=baseline)
        for item in wanted:
            hit = storage.best_name_match(store, item["name"])
            basket.lines.append(
                BasketLine(
                    term=item["name"],
                    quantity=item["quantity"],
                    name=(hit or {}).get("name", ""),
                    price=(hit or {}).get("price"),
                    rank=(hit or {}).get("rank"),
                )
            )
        # Both sides of the comparison are the same lines: the ones this
        # chain matched exactly *and* the baseline also sells. A
        # substitute is shown to the household but never priced into a
        # saving, and neither is a line the baseline itself lacks.
        basket.baseline_subtotal = round(
            sum(
                reference[line.term] * (line.quantity or 1)
                for line in basket.exact
                if line.term in reference
            ),
            2,
        )
        if basket.matched:
            baskets.append(basket)

    # Biggest genuine saving first, comparable chains ahead of thin ones.
    # The baseline itself stays in the list as the thing being compared to.
    baskets.sort(key=lambda b: (not b.comparable, -b.saving_with_delivery))
    return baskets


def format_baskets(
    baskets: list[ChainBasket], limit: int = 4, always_show: tuple[str, ...] = ("shufersal", "tivtaam")
) -> str:
    """A phone-sized answer to 'where should this shop go'.

    `always_show` chains are never cut by `limit`: those are the ones a
    cart can actually be filled at, so leaving them out to make room for
    a chain the household cannot order from would answer a question
    nobody asked.
    """
    if not baskets:
        return "אין לי מספיק מחירים כדי להשוות סל בין רשתות."

    shown = baskets[:limit] + [
        b for b in baskets[limit:] if b.store in always_show
    ]
    baseline_store = baskets[0].baseline_store
    lines: list[str] = ["🧺 *הסל שלכם בכל רשת*", ""]

    for basket in shown:
        head = f"*{display_name(basket.store)}* — {basket.total:.2f}₪"
        if basket.delivery:
            head += f" _(כולל משלוח {basket.delivery:.2f}₪)_"
        lines.append(head)
        detail = (
            f"   {len(basket.matched)}/{len(basket.lines)} פריטים "
            f"({basket.coverage*100:.0f}% מהסל)"
        )
        if basket.substitutes:
            detail += f" · 🔄 {len(basket.substitutes)} תחליפים"
        if basket.missing:
            detail += f" · ❌ {len(basket.missing)} חסרים"
        lines.append(detail)
        if basket.store != baseline_store and basket.exact:
            # Always against the same goods, never against a different
            # chain's differently-sized basket, and never counting a
            # substitute as if it were the same product.
            direction = "זולה" if basket.saving_with_delivery > 0 else "יקרה"
            lines.append(
                f"   _{direction} ב-{abs(basket.saving_with_delivery):.2f}₪ "
                f"מ{display_name(baseline_store)} על {len(basket.exact)} פריטים זהים_"
            )
        if not basket.comparable:
            lines.append("   _כיסוי נמוך מדי — לא באמת בר-השוואה._")
        lines.append("")

    best = next(
        (b for b in baskets if b.comparable and b.store != baseline_store
         and b.saving_with_delivery >= 1 and len(b.exact) >= 5),
        None,
    )
    if best is not None:
        lines.append(
            f"➡️ *{display_name(best.store)}* יוצאת זולה יותר ב-"
            f"{best.saving_with_delivery:.2f}₪ (כולל משלוח), על "
            f"{len(best.exact)} פריטים זהים."
        )
    else:
        lines.append(f"➡️ אין סיבה מספקת לעזוב את {display_name(baseline_store)} השבוע.")

    lines.append("")
    lines.append(
        "_ההשוואה לפי שם מוצר, לא לפי ברקוד (שופרסל לא מפרסמת ברקודים), "
        "ורק על מה שנמצא בשתי הרשתות. 🔄 = התאמה חלקית, בדקו לבד._"
    )
    return "\n".join(lines)


def format_missing(basket: ChainBasket, limit: int = 15) -> str:
    """What one chain does not carry, for when the headline isn't enough."""
    if not basket.missing and not basket.substitutes:
        return f"{display_name(basket.store)}: הכול נמצא, בלי תחליפים."
    lines = [f"*{display_name(basket.store)}* — מה לא זהה:", ""]
    for line in basket.substitutes[:limit]:
        lines.append(f"🔄 {line.term} → {line.name} ({line.price:.2f}₪)")
    for line in basket.missing[:limit]:
        lines.append(f"❌ {line.term} — לא נמצא")
    return "\n".join(lines)
