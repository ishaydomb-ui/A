"""Tiv Taam's own answer to "what does this household usually buy".

The chain already computes this and will hand it over: 94 products for
this household, each with `purchaseFrequencyDays` and `ordersNumber`.
Two things make it better than anything derived here:

**It counts in-store purchases.** Order history only shows what was
bought online. A household that buys milk online and cheese in the shop
looks, to `stock.build_from_orders`, like a household that does not buy
cheese.

**The interval is measured, not inferred.** Our own estimate is
`1/share × the household's order gap`, which is a reasonable guess built
on two approximations. Tiv Taam publishes the real figure per product, so
`shelflife` prefers it and falls back to the estimate only where it is
missing.

The smart list carries no barcode for loose produce — weighable items are
identified by a retailer product id instead — so it is stored under the
Tiv Taam store key and joined by id, not merged into Shufersal's rows.
"""
from __future__ import annotations

from .disambiguate import _normalise
from .stock import DEPARTMENTS, OTHER_DEPARTMENT, StockItem

# Departments are not on the smart-list payload, so they are inferred
# from the product family name. Deliberately coarse: the department is
# used to decide "does this keep in a cupboard", not to lay out a shop.
_FAMILY_DEPARTMENT_HINTS = (
    ("פירות", "פירות וירקות"),
    ("ירק", "פירות וירקות"),
    ("עגבני", "פירות וירקות"),
    ("פלפל", "פירות וירקות"),
    ("חסה", "פירות וירקות"),
    ("חלב", "מוצרי חלב וקירור"),
    ("גבינ", "מוצרי חלב וקירור"),
    ("יוגורט", "מוצרי חלב וקירור"),
    ("ביצ", "מוצרי חלב וקירור"),
    ("בשר", "קפואים ומזון בסיסי"),
    ("עוף", "קפואים ומזון בסיסי"),
    ("דג", "קפואים ומזון בסיסי"),
    ("קפוא", "קפואים ומזון בסיסי"),
    ("שימור", "מזווה ושימורים"),
    ("רוטב", "מזווה ושימורים"),
    ("ממרח", "מזווה ושימורים"),
    ("פסטה", "יבשים ובישול"),
    ("אורז", "יבשים ובישול"),
    ("קמח", "יבשים ובישול"),
    ("שמן", "יבשים ובישול"),
    ("ניקוי", "טיפוח, תינוקות וניקיון"),
    ("נייר", "טיפוח, תינוקות וניקיון"),
    ("תינוק", "טיפוח, תינוקות וניקיון"),
    ("היגיינ", "טיפוח, תינוקות וניקיון"),
)


def _name_of(product: dict) -> str:
    hebrew = (product.get("names") or {}).get("1") or {}
    return hebrew.get("short") or product.get("localName") or ""


def _family_name(product: dict) -> str:
    family = product.get("family") or {}
    names = family.get("names") or {}
    first = names.get("1") or {}
    return first.get("name") or ""


def department_for(product: dict) -> str:
    """Best guess at a department, from the product's family name."""
    haystack = f"{_family_name(product)} {_name_of(product)}"
    for hint, department in _FAMILY_DEPARTMENT_HINTS:
        if hint in haystack:
            return department
    return OTHER_DEPARTMENT


def to_stock_items(payload: dict) -> list[StockItem]:
    """Convert a smart-list payload into stock items for the Tiv Taam store.

    ``share`` is derived from ordersNumber against the busiest product,
    because the payload gives no order total. That makes it a *relative*
    confidence, which is exactly what the tier thresholds need, and it
    never claims a household bought something in more orders than exist.
    """
    items = payload.get("items") or []
    if not items:
        return []

    busiest = max((i.get("ordersNumber") or 0) for i in items) or 1
    result = []
    for entry in items:
        product = entry.get("product") or {}
        name = _name_of(product)
        code = product.get("id") or entry.get("retailerProductId")
        if not name or not code:
            continue
        interval = entry.get("purchaseFrequencyDays")
        result.append(
            StockItem(
                product_code=str(code),
                product_name=name,
                share=min(1.0, (entry.get("ordersNumber") or 0) / busiest),
                department=department_for(product),
                category=_family_name(product),
                default_quantity=1,
                interval_days=float(interval) if interval else None,
            )
        )
    return result


def _attach_barcodes(storage, items, store: str) -> int:
    """Fill in barcodes by matching names within this chain's own data.

    The obvious route does not work: querying the products endpoint by
    internal id returns a different field projection that omits
    localBarcode entirely, and its externalId is a short internal number
    rather than an EAN. Verified against the live API — milk comes back
    with externalId 46411, not 7290004131074.

    So the barcode comes from prices already recorded for this same chain,
    matched on the product name. Name matching *within one chain* is safe
    in a way that cross-chain name matching is not, because both sides are
    the same catalogue writing the same string.
    """
    known = {
        _normalise(row["name"]): barcode
        for barcode, row in storage.latest_store_prices(store).items()
        if row.get("name")
    }
    if not known:
        return 0
    filled = 0
    for item in items:
        barcode = known.get(_normalise(item.product_name))
        if barcode:
            item.barcode = barcode
            filled += 1
    return filled


def sync(api, storage, prices=None, store: str = "tivtaam") -> int:
    """Refresh the Tiv Taam stock rows from the chain's own smart list.

    ``prices`` is accepted and unused; barcodes are resolved from data
    this chain has already given us. See :func:`_attach_barcodes`.
    """
    items = to_stock_items(api.smart_list())
    if not items:
        return 0
    _attach_barcodes(storage, items, store)
    storage.replace_stock_items(store, items)
    return len(items)
