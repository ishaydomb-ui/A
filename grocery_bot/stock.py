"""What to propose buying, and how confidently.

The household's own method is to add everything they have ever bought
(~300 products) and delete what they don't need this week. That is not a
workaround, it is the right response to an asymmetric cost: a missing
item means no milk until the next delivery, while an unwanted one costs
a single tap. The problem was never the strategy, only that removing 280
items by hand is tedious.

So this does not try to predict what is needed. Two prediction models
were built and both failed against the real history — a time-based one
("milk every 6 days, 8 days elapsed") and an order-gap one ("bought
every 4th order, 6 orders ago"). The second flagged 56 of 89 products as
due. The cause is structural rather than a modelling mistake: the same
household also shops at another chain, so this history cannot see what
was consumed. No model recovers data that was never recorded.

What the history *does* support is confidence. Products separate cleanly
by how reliably they appear in an order:

    A  >= 70%   almost every order      8 products
    B  35-70%   regular                14
    C  15-35%   occasional              67
    D  < 15%    rare, one-offs         216

A, B and C are proposed pre-ticked; D is not proposed at all. Everything
stays visible and removable, which is the user's existing workflow, but
grouped into a handful of departments instead of one 300-item list.

**Learning matters more than the thresholds.** Because the other chain
is invisible here, a product silently dropping out of the history is
ambiguous — it might be bought elsewhere every week. The user's own
ticks are the only unambiguous signal, so repeated removals demote an
item regardless of what the purchase history says.
"""
from __future__ import annotations

import collections
import statistics
from dataclasses import dataclass, field

# Share-of-orders boundaries. Deliberately constants rather than magic
# numbers inline: a second chain will split each product's history across
# two stores, and these will need retuning when it lands.
TIER_A_MIN = 0.70
TIER_B_MIN = 0.35
TIER_C_MIN = 0.15

# How many removals in a row before an item stops being pre-ticked. Low
# on purpose — the user removing something three times running is a much
# stronger signal than an inference drawn from a partial history.
DEMOTE_AFTER_SKIPS = 3

# Category codes group into departments by their leading digit. Taken
# from the store's own taxonomy in the order data rather than invented,
# so it matches how the shop is actually laid out.
DEPARTMENTS = {
    "2": "קפואים ומזון בסיסי",
    "3": "פירות וירקות",
    "4": "טיפוח, תינוקות וניקיון",
    "5": "מוצרי חלב וקירור",
    "6": "מזווה ושימורים",
    "7": "יבשים ובישול",
}
OTHER_DEPARTMENT = "שונות"


@dataclass
class StockItem:
    product_code: str
    product_name: str
    share: float
    department: str
    category: str = ""
    default_quantity: int = 1
    amount: float | None = None
    unit: str = ""
    picked_count: int = 0
    skipped_count: int = 0
    # Days between purchases when the *chain* measured it rather than us.
    # Tiv Taam publishes this per product and counts in-store purchases we
    # cannot see, so it beats anything derived from online orders alone.
    # None means "nobody measured it"; shelflife falls back to 1/share.
    interval_days: float | None = None

    @property
    def tier(self) -> str:
        if self.share >= TIER_A_MIN:
            return "A"
        if self.share >= TIER_B_MIN:
            return "B"
        if self.share >= TIER_C_MIN:
            return "C"
        return "D"

    @property
    def proposed(self) -> bool:
        """Whether to put this on the checklist at all."""
        return self.tier in ("A", "B", "C")

    @property
    def preticked(self) -> bool:
        """Pre-ticked unless the user has repeatedly said otherwise.

        Tier A is included here rather than added silently: the user asked
        to see everything that is going in, so that a wrong one is one tap
        from removal instead of a surprise discovered at checkout.
        """
        if self.skipped_count >= DEMOTE_AFTER_SKIPS and self.skipped_count > self.picked_count:
            return False
        return self.proposed


def department_for(category_code: str) -> str:
    """Which part of the shop a category code belongs to."""
    if category_code and category_code[0] in DEPARTMENTS:
        return DEPARTMENTS[category_code[0]]
    return OTHER_DEPARTMENT


def build_from_orders(orders: list[dict]) -> list[StockItem]:
    """Turn raw order history into per-product confidence and department.

    `orders` is the shape returned by history.fetch_order_history.
    """
    total = len(orders)
    if total == 0:
        return []

    counts: collections.Counter = collections.Counter()
    quantities: dict[str, list[float]] = collections.defaultdict(list)
    names: dict[str, str] = {}
    categories: dict[str, tuple[str, str]] = {}
    methods: dict[str, str] = {}
    package_grams: dict[str, float | None] = {}

    for order in orders:
        seen: set[str] = set()
        for entry in order.get("entries") or []:
            product = entry.get("product") or {}
            code = product.get("code")
            name = product.get("name") or ""
            if not code or "משלוח" in name:
                continue
            names[code] = name
            group = product.get("commercialCategoryGroup") or {}
            categories[code] = (group.get("code", ""), group.get("name", ""))
            methods[code] = (product.get("sellingMethod") or {}).get("code", "")
            package_grams[code] = product.get("weightConversion")
            quantities[code].append(float(entry.get("quantity") or 1))
            if code not in seen:
                counts[code] += 1
                seen.add(code)

    items = []
    for code, count in counts.most_common():
        category_code, category_name = categories.get(code, ("", ""))
        median = statistics.median(quantities[code])
        amount, unit, quantity = _quantity_for(methods.get(code, ""), median, package_grams.get(code))
        items.append(
            StockItem(
                product_code=code,
                product_name=names[code],
                share=count / total,
                department=department_for(category_code),
                category=category_name,
                default_quantity=quantity,
                amount=amount,
                unit=unit,
            )
        )
    return items


def _quantity_for(
    selling_method: str, median_quantity: float, grams_per_package: float | None
) -> tuple[float | None, str, int]:
    """Interpret a median order quantity (see history.py for the rules)."""
    if selling_method == "BY_WEIGHT":
        return round(median_quantity / 1000.0, 3), 'ק"ג', 1
    if selling_method == "BY_PACKAGE":
        packages = (
            max(1, int(round(median_quantity / grams_per_package))) if grams_per_package else 1
        )
        return round(median_quantity / 1000.0, 3), 'ק"ג', packages
    return None, "", max(1, int(round(median_quantity)))


@dataclass
class Department:
    name: str
    items: list[StockItem] = field(default_factory=list)

    @property
    def preticked_count(self) -> int:
        return sum(1 for item in self.items if item.preticked)


def group_by_department(items: list[StockItem]) -> list[Department]:
    """Proposed items, grouped for review, biggest department first."""
    grouped: dict[str, list[StockItem]] = collections.defaultdict(list)
    for item in items:
        if item.proposed:
            grouped[item.department].append(item)
    departments = [
        Department(name=name, items=sorted(rows, key=lambda i: -i.share))
        for name, rows in grouped.items()
    ]
    return sorted(departments, key=lambda d: -len(d.items))
