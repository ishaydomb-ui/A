"""Derive the base list and product memory from real Shufersal order history.

The starting `data/base_list.yaml` was a generated placeholder that had
nothing to do with what this household actually buys. The account itself
holds the real answer: past orders, with line items, through JSON
endpoints the site's own pages call.

Two things come out of that history:

1. **The base list** — products bought in a large share of orders. This is
   the project's "רשימת הבסיס" derived from evidence instead of memory,
   which is pain point #1 (no inventory tracking, everything from memory).

2. **Product memory** — the exact product code behind each name. Without
   it, searching "קוטג'" returns ~20 tiles and every item turns into an
   ambiguity question; with it the bot goes straight to the product the
   user actually buys. It is deliberately *not* a lock: it records the
   baseline so the promotions engine has something to compare
   alternatives against (pain point #2, brand fixation).

**A caveat that matters when reading this data** (2026-08-29): the
household's ordering pattern changed. 2025 has 15 orders at 6-20 day
intervals; 2026 has 4, spread 27-134 days apart, because they started
splitting the shop with Tiv Taam. So 2026 alone is far too thin to
compute "what we always buy" from -- one product appears in all four
orders. 2025 is the honest picture of a full Shufersal shop, which is
why `year` exists as a filter rather than always using everything.

Quantities need care, and the discriminator is `sellingMethod`, never the
unit (an item can be BY_UNIT with unit KG -- a 1kg bag of rice is
quantity 1, not 1000):

- ``BY_UNIT``    -- quantity is a plain count.
- ``BY_WEIGHT``  -- quantity is **grams** (500 = half a kilo).
- ``BY_PACKAGE`` -- quantity is **grams**, and one package weighs
  ``weightConversion`` grams, so packages = quantity / weightConversion.

Getting this wrong is not a rounding error: reading a BY_PACKAGE 1300g
bag of carrots as a count orders 1300 bags.
"""
from __future__ import annotations

import collections
import datetime
import logging
import statistics
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ORDERS_URL = "https://www.shufersal.co.il/online/he/my-account/orders"
ORDER_DETAIL_URL = "https://www.shufersal.co.il/online/he/my-account/orders/{code}"

# Delivery/service fees come back as ordinary line items on every order,
# so they look like the most-purchased "product" in the account.
NON_PRODUCT_MARKERS = ("משלוח",)


@dataclass
class ProductHistory:
    """How often one product was bought, and in what quantity."""

    product_code: str
    name: str
    order_count: int
    total_orders: int
    median_quantity: float
    selling_method: str
    grams_per_package: float | None = None
    last_bought: datetime.date | None = None

    @property
    def share(self) -> float:
        return self.order_count / self.total_orders if self.total_orders else 0.0

    @property
    def by_weight(self) -> bool:
        """True when `median_quantity` is grams rather than a count."""
        return self.selling_method in ("BY_WEIGHT", "BY_PACKAGE")

    @property
    def amount_and_unit(self) -> tuple[float | None, str]:
        """Quantity expressed for humans: kg for weighed items, else a count."""
        if self.by_weight:
            return round(self.median_quantity / 1000.0, 3), 'ק"ג'
        return None, ""

    @property
    def default_quantity(self) -> int:
        """How many of the thing to order.

        Loose weighed produce is a single "add" carrying an amount. A
        pre-packed item is a whole number of packages, worked out from the
        package weight -- 1300g of 1300g-per-bag carrots is one bag, not
        1300 of anything.
        """
        if self.selling_method == "BY_WEIGHT":
            return 1
        if self.selling_method == "BY_PACKAGE":
            if self.grams_per_package:
                return max(1, int(round(self.median_quantity / self.grams_per_package)))
            return 1
        return max(1, int(round(self.median_quantity)))


@dataclass
class OrderHistory:
    orders_analysed: int = 0
    products: list[ProductHistory] = field(default_factory=list)

    def frequent(self, min_share: float) -> list[ProductHistory]:
        return [p for p in self.products if p.share >= min_share]


def fetch_order_history(page, year: int | None = None, limit: int | None = None) -> list[dict]:
    """Pull past orders with their line items, via the site's own JSON API.

    Takes an existing logged-in Playwright page rather than opening its
    own: order history is only visible to an authenticated session, and
    the adapter already owns one.
    """
    response = page.request.get(ORDERS_URL, timeout=60_000)
    if response.status != 200:
        raise RuntimeError(f"Could not list orders (HTTP {response.status})")
    listing = response.json()

    orders = list(listing.get("closedOrders") or []) + list(listing.get("activeOrders") or [])
    detailed: list[dict] = []
    for order in orders:
        created = _parse_created(order.get("createdString"))
        if year is not None and (created is None or created.year != year):
            continue
        code = order.get("code")
        if not code:
            continue
        detail = page.request.get(ORDER_DETAIL_URL.format(code=code), timeout=60_000)
        if detail.status != 200:
            logger.warning("Skipping order %s (HTTP %s)", code, detail.status)
            continue
        detailed.append({"code": code, "created": created, "entries": detail.json().get("entries") or []})
        if limit is not None and len(detailed) >= limit:
            break
    return detailed


def summarise(orders: list[dict]) -> OrderHistory:
    """Turn raw orders into per-product purchase frequency."""
    total = len(orders)
    if total == 0:
        return OrderHistory()

    counts: collections.Counter = collections.Counter()
    quantities: dict[str, list[float]] = collections.defaultdict(list)
    names: dict[str, str] = {}
    methods: dict[str, str] = {}
    package_grams: dict[str, float | None] = {}
    last_seen: dict[str, datetime.date] = {}

    for order in orders:
        seen_in_this_order: set[str] = set()
        created = order.get("created")
        for entry in order.get("entries") or []:
            product = entry.get("product") or {}
            code = product.get("code")
            name = product.get("name") or ""
            if not code or any(marker in name for marker in NON_PRODUCT_MARKERS):
                continue
            names[code] = name
            methods[code] = (product.get("sellingMethod") or {}).get("code", "")
            package_grams[code] = product.get("weightConversion")
            quantities[code].append(float(entry.get("quantity") or 1))
            if code not in seen_in_this_order:
                counts[code] += 1
                seen_in_this_order.add(code)
            if created is not None:
                previous = last_seen.get(code)
                if previous is None or created.date() > previous:
                    last_seen[code] = created.date()

    products = [
        ProductHistory(
            product_code=code,
            name=names[code],
            order_count=count,
            total_orders=total,
            median_quantity=statistics.median(quantities[code]),
            selling_method=methods.get(code, ""),
            grams_per_package=package_grams.get(code),
            last_bought=last_seen.get(code),
        )
        for code, count in counts.most_common()
    ]
    return OrderHistory(orders_analysed=total, products=products)


def import_base_list(
    storage,
    history: OrderHistory,
    min_share: float,
    store: str = "shufersal",
    replace: bool = True,
) -> int:
    """Rebuild the base list from products bought in `min_share` of orders.

    Replaces by default: re-running after new orders arrive should refresh
    the list, not append a second copy of every item.

    Also records each product as the remembered choice for its own name,
    so a cycle resolves it directly instead of asking which of ~20 search
    results was meant.
    """
    chosen = history.frequent(min_share)
    if replace:
        storage.deactivate_all_base_items()
    for item in chosen:
        amount, unit = item.amount_and_unit
        storage.add_base_list_item(
            name=item.name,
            default_quantity=item.default_quantity,
            amount=amount,
            unit=unit,
        )
        storage.remember_choice(
            store=store,
            term=item.name,
            product_code=item.product_code,
            product_name=item.name,
        )
    return len(chosen)


def seed_product_memory(storage, history: OrderHistory, store: str = "shufersal") -> int:
    """Remember the exact product behind every name ever ordered.

    Covers ad-hoc requests too, not just base-list items: asking for
    something bought once two years ago still resolves to the right
    product rather than to a wall of search results.
    """
    for item in history.products:
        storage.remember_choice(
            store=store,
            term=item.name,
            product_code=item.product_code,
            product_name=item.name,
        )
    return len(history.products)


def _parse_created(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y/%m/%d %H:%M")
    except ValueError:
        return None
