"""Price the same basket at both chains, on the barcode.

Why this module exists at all
-----------------------------
Neither chain will tell the household what the other one charges, and it
is the only question worth automating: filling a cart saves ten minutes,
but knowing which shop is ₪60 cheaper this week is money, every week.

The join is the manufacturer's EAN barcode, not the product name. Israeli
grocery names are written differently by every chain — the same carton is
``חלב בקרטון 3% שומן 1 ל`` at Shufersal and ``חלב 3% קרטון מהדרין`` at
Tiv Taam — so name matching would produce confident nonsense. Both chains
publish the same EAN (7290004131074 here), which makes the comparison
exact or absent, never approximate.

What this deliberately does not do
----------------------------------
It does not split a basket across two chains. Two deliveries (₪29.90 at
Tiv Taam alone), two time slots and two carts to approve is a worse chore
than the one it replaces, to chase a few tens of shekels. The output is
one sentence — which chain is cheaper this week, and where the difference
comes from — and the household picks a shop.

Honesty about staleness
-----------------------
Tiv Taam has no public price feed (checked: `prices.tivtaam.co.il` does
not exist, and it is absent from the usual publisher portals), so its
prices are *observed* — mostly from what the household actually paid on a
past order. A July price compared against a live Shufersal price is not a
saving, it is a guess, so every comparison carries the date its Tiv Taam
side was observed and the caller is expected to show it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

# A line that is not a product: delivery, service fees, deposits. These
# carry internal codes rather than EANs and would otherwise look like
# unmatched groceries.
_NON_PRODUCT_BARCODES = {"9966"}

# Shufersal's feed keys on the EAN, and so does Tiv Taam's order line —
# but only for packaged goods. Loose produce carries a short internal
# code (`4412470` for apples) that means nothing at the other chain.
# Comparing those would silently match unrelated items.
_MIN_EAN_LENGTH = 8


def is_comparable_barcode(barcode: str | int | None) -> bool:
    """Is this a manufacturer EAN, rather than a chain-internal code?"""
    code = str(barcode or "").strip()
    if not code.isdigit() or code in _NON_PRODUCT_BARCODES:
        return False
    return len(code) >= _MIN_EAN_LENGTH


@dataclass(frozen=True)
class LineComparison:
    """One product priced at both chains."""

    barcode: str
    name: str
    quantity: float
    tivtaam_price: float | None
    shufersal_price: float | None
    tivtaam_observed_at: str | None = None

    @property
    def comparable(self) -> bool:
        return self.tivtaam_price is not None and self.shufersal_price is not None

    @property
    def delta(self) -> float | None:
        """Shufersal minus Tiv Taam, per unit. Positive means Tiv Taam wins."""
        if not self.comparable:
            return None
        return round(self.shufersal_price - self.tivtaam_price, 2)

    @property
    def line_delta(self) -> float | None:
        """The same difference across the quantity actually bought."""
        if self.delta is None:
            return None
        return round(self.delta * (self.quantity or 1), 2)

    @property
    def cheaper_at(self) -> str | None:
        if self.delta is None or abs(self.delta) < 0.01:
            return None
        return "tivtaam" if self.delta > 0 else "shufersal"


@dataclass(frozen=True)
class BasketComparison:
    """A whole basket priced at both chains."""

    lines: list[LineComparison]
    basket_date: str | None = None

    @property
    def compared(self) -> list[LineComparison]:
        return [line for line in self.lines if line.comparable]

    @property
    def unmatched(self) -> list[LineComparison]:
        return [line for line in self.lines if not line.comparable]

    @property
    def tivtaam_total(self) -> float:
        return round(
            sum(l.tivtaam_price * (l.quantity or 1) for l in self.compared), 2
        )

    @property
    def shufersal_total(self) -> float:
        return round(
            sum(l.shufersal_price * (l.quantity or 1) for l in self.compared), 2
        )

    @property
    def difference(self) -> float:
        """Shufersal total minus Tiv Taam total. Positive means Tiv Taam wins."""
        return round(self.shufersal_total - self.tivtaam_total, 2)

    @property
    def cheaper_chain(self) -> str | None:
        if abs(self.difference) < 0.01:
            return None
        return "tivtaam" if self.difference > 0 else "shufersal"

    @property
    def coverage(self) -> float:
        """Share of basket lines that could be priced at both chains."""
        return len(self.compared) / len(self.lines) if self.lines else 0.0

    def biggest_gaps(self, limit: int = 5) -> list[LineComparison]:
        """The lines that actually drive the difference, largest first."""
        return sorted(
            self.compared,
            key=lambda l: abs(l.line_delta or 0),
            reverse=True,
        )[:limit]


def lines_from_tivtaam_order(order: dict) -> list[dict]:
    """Extract comparable product lines from a Tiv Taam order payload.

    Quantity comes from ``actualQuantity`` — what was really supplied, not
    what was asked for. A line supplied at zero (out of stock) is dropped:
    it was never bought, and pricing it would inflate both baskets.
    """
    rows = []
    for line in order.get("lines") or []:
        barcode = str(line.get("barcode") or "")
        if not is_comparable_barcode(barcode):
            continue
        quantity = line.get("actualQuantity")
        price = line.get("price")
        if not quantity or not price:
            continue
        rows.append(
            {
                "barcode": barcode,
                "name": line.get("name") or "",
                "price": float(price),
                "quantity": float(quantity),
            }
        )
    return rows


def order_date(order: dict) -> str:
    placed = order.get("timePlaced") or ""
    return placed[:10] or date.today().isoformat()


def ingest_tivtaam_order(storage, order: dict) -> int:
    """Record what the household paid per barcode on one Tiv Taam order."""
    observed_at = order_date(order)
    rows = [
        {
            "barcode": row["barcode"],
            "name": row["name"],
            "price": row["price"],
            "observed_at": observed_at,
            "source": "order",
        }
        for row in lines_from_tivtaam_order(order)
    ]
    if not rows:
        return 0
    return storage.record_store_prices("tivtaam", rows)


def compare_basket(storage, basket: Iterable[dict], basket_date: str | None = None):
    """Price a basket at both chains.

    ``basket`` items need ``barcode``, ``name`` and ``quantity``; a
    ``price`` is used as the Tiv Taam side when present, otherwise the
    newest observed price for that barcode is looked up.
    """
    lines = []
    for item in basket:
        barcode = str(item["barcode"])
        tivtaam_price = item.get("price")
        observed_at = basket_date
        if tivtaam_price is None:
            seen = storage.latest_store_price("tivtaam", barcode)
            if seen:
                tivtaam_price = seen["price"]
                observed_at = seen["observed_at"]
        shufersal = storage.catalog_price(barcode)
        lines.append(
            LineComparison(
                barcode=barcode,
                name=item.get("name") or (shufersal or {}).get("name", ""),
                quantity=float(item.get("quantity") or 1),
                tivtaam_price=float(tivtaam_price) if tivtaam_price else None,
                shufersal_price=shufersal["price"] if shufersal else None,
                tivtaam_observed_at=observed_at,
            )
        )
    return BasketComparison(lines=lines, basket_date=basket_date)


def staleness_days(comparison: BasketComparison, today: date | None = None) -> int | None:
    """How old the Tiv Taam side of this comparison is, in days.

    Returned so the caller can say so out loud. A comparison against
    two-month-old prices may still be directionally right, but it is not a
    quote, and presenting it as one would be dishonest.
    """
    dates = [l.tivtaam_observed_at for l in comparison.compared if l.tivtaam_observed_at]
    if not dates:
        return None
    newest = max(dates)
    try:
        observed = datetime.strptime(newest, "%Y-%m-%d").date()
    except ValueError:
        return None
    return ((today or date.today()) - observed).days

# Selling methods whose quantity arrives in grams rather than as a count.
# This is the single most expensive mistake available in this codebase:
# reading 1000 grams of grapes as 1000 packets turned a ₪19.90 line into
# ₪19,900 and a ₪575 basket into ₪28,187. BY_PACKAGE is the one that gets
# forgotten — it is not BY_WEIGHT, but it is still grams.
GRAM_SELLING_METHODS = {"BY_WEIGHT", "BY_PACKAGE"}


def selling_method(product: dict) -> str:
    """The selling method code, whatever shape the payload uses."""
    method = product.get("sellingMethod")
    if isinstance(method, dict):
        return method.get("code", "") or ""
    return str(method or "")


def units_bought(entry: dict) -> float:
    """How many priced units a line represents.

    For a counted product that is the quantity. For anything sold by
    weight or by package it is grams converted to kilograms, because the
    price on those lines is per kilogram.
    """
    product = entry.get("product") or {}
    raw = float(entry.get("quantity") or 1)
    if selling_method(product) in GRAM_SELLING_METHODS:
        return raw / 1000.0
    return raw
