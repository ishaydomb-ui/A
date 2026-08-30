"""Price per kilo/litre, so "which is actually cheaper" stops being mental arithmetic.

The household currently works this out in their head at the shelf. The
feed already publishes a unit price for every product (100% coverage on
this branch), but in six different units — `100 גרם`, `1קילוגרם`,
`100 מיליליטר`, `1ליטר`, `יחידות`, `מטרים` — so the published numbers are
not comparable to each other as they stand. A 250g tub priced per 100g
and a kilo bag priced per kilo differ by a factor of ten before anything
real is compared.

So everything is normalised to one base per dimension (₪/kg, ₪/litre,
₪/unit, ₪/m) and comparisons only happen *within* a dimension. Comparing
₪/kg against ₪/litre would produce a confident, meaningless answer.

Promotions need care too: the published unit price is derived from the
shelf price, so a discounted product keeps its full-price ratio in the
feed. The effective unit price is scaled by the actual discount, or a
half-price item would look like no bargain at all.
"""
from __future__ import annotations

from dataclasses import dataclass

# unit_of_measure -> (multiplier to reach the base unit, base label, dimension)
_UNITS = {
    "100 גרם": (10.0, 'ק"ג', "weight"),
    "1קילוגרם": (1.0, 'ק"ג', "weight"),
    "100 מיליליטר": (10.0, "ליטר", "volume"),
    "1ליטר": (1.0, "ליטר", "volume"),
    "יחידות": (1.0, "יחידה", "unit"),
    "מטרים": (1.0, "מטר", "length"),
}


@dataclass(frozen=True)
class UnitPrice:
    value: float
    base_label: str
    dimension: str

    def format(self) -> str:
        return f'{self.value:,.2f}₪ ל{self.base_label}'


def unit_price(
    unit_of_measure_price: float,
    unit_of_measure: str,
    shelf_price: float | None = None,
    effective_price: float | None = None,
) -> UnitPrice | None:
    """Normalise a product's published unit price to its base unit.

    Pass `shelf_price` and `effective_price` to reflect a promotion: the
    feed's unit price always describes the full price, so a product at
    half price would otherwise still advertise its undiscounted ratio.
    """
    conversion = _UNITS.get((unit_of_measure or "").strip())
    if conversion is None or not unit_of_measure_price:
        return None
    multiplier, label, dimension = conversion
    value = unit_of_measure_price * multiplier
    if shelf_price and effective_price and shelf_price > 0:
        value *= effective_price / shelf_price
    return UnitPrice(value=value, base_label=label, dimension=dimension)


def for_product(product, deal=None) -> UnitPrice | None:
    """Unit price of a catalog product, discounted if a deal applies."""
    return unit_price(
        getattr(product, "unit_of_measure_price", 0),
        getattr(product, "unit_of_measure", ""),
        shelf_price=getattr(product, "price", None),
        effective_price=getattr(deal, "discounted_price", None) if deal else None,
    )


def best_value(entries: list[tuple]) -> int | None:
    """Index of the best ₪/base among comparable entries, or None.

    `entries` is a list of (product, deal) pairs. Returns None unless at
    least two entries share a dimension — with one product, or a mix of
    weight and volume, "best value" is not a meaningful claim.
    """
    priced = []
    for index, (product, deal) in enumerate(entries):
        computed = for_product(product, deal)
        if computed is not None:
            priced.append((index, computed))
    if len(priced) < 2:
        return None

    dimensions = {computed.dimension for _, computed in priced}
    if len(dimensions) > 1:
        # Keep the majority dimension rather than comparing kilos to
        # litres; a mixed result would be arithmetic without meaning.
        counts = {d: sum(1 for _, c in priced if c.dimension == d) for d in dimensions}
        winner = max(counts, key=counts.get)
        priced = [(i, c) for i, c in priced if c.dimension == winner]
        if len(priced) < 2:
            return None

    return min(priced, key=lambda pair: pair[1].value)[0]


def describe(product, deal=None) -> str:
    """" — 2.56₪ לק"ג" style suffix, or nothing when not comparable."""
    computed = for_product(product, deal)
    return f" · {computed.format()}" if computed else ""
