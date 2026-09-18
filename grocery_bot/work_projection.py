"""Gordon -> Work context projection (Ishay, 2026-09-18). Pure read, no
browser, no live retailer session, no raw SQLite handed out. Deliberately
does NOT call plancontext._read_carts() or anything Playwright-touching --
that function does a live page-load of the actual cart, exactly the kind
of concurrent live-session touch a Work benchmark must avoid (2026-09-18
review, section D2). This module does not import plancontext at all.

Every field here comes from Gordon's own preference/intent tables, not
from a retailer's page text -- so untrusted.flatten() is not needed on
this path (nothing here is copied from a page the retailer controls); if
a future field ever draws from page-sourced text, it must go through
untrusted.flatten() first, the same way plancontext already does for
_read_carts.

Every read goes through Storage's own public methods -- list_pending_adhoc,
list_preferences, list_rejections, list_stock_items -- never a direct SQL
query from this module. list_preferences/list_rejections already existed
as store-scoped bulk accessors (Phase 8, 2026-09-17) before this file was
written, so no new Storage accessor was needed here; verified by reading
storage.py directly rather than assumed from the spec.
"""
from __future__ import annotations

from datetime import datetime, timezone

_SECRET_MARKERS = ("password", "cookie", "token", "session", "secret")

# hotdeals.find(storage, chains=None) always returns (relevant,
# exceptional) and treats `chains` as a *list* of chain names (iterated
# in scan()), never a single store string. The spec's own stub called
# hotdeals.find(storage, store) as if it returned a flat list and took a
# bare store name; verified against the live function and corrected here.
_MAX_PROMOTIONS = 20


def build_projection(storage, store: str) -> dict:
    """dict with: pending_needs, preferred_products, rejections,
    quantities, promotions, generated_at. No key or value here should
    ever match _SECRET_MARKERS -- enforced by tests/test_work_projection.py,
    not just this docstring.
    """
    projection: dict = {
        "store": store,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        projection["pending_needs"] = [
            {"term": r.text, "quantity": r.quantity}
            for r in storage.list_pending_adhoc()
        ]
    except Exception:  # noqa: BLE001
        projection["pending_needs"] = []

    try:
        projection["preferred_products"] = [
            {
                "term": row.get("term", ""),
                "product_code": row.get("product_code", ""),
                "product_name": row.get("product_name", ""),
                "source": row.get("source", ""),
            }
            for row in storage.list_preferences(store)
        ]
    except Exception:  # noqa: BLE001
        projection["preferred_products"] = []

    try:
        projection["rejections"] = [
            {
                "term": row.get("term", ""),
                "product_code": row.get("product_code", ""),
                "product_name": row.get("product_name", ""),
            }
            for row in storage.list_rejections(store)
        ]
    except Exception:  # noqa: BLE001
        projection["rejections"] = []

    try:
        projection["quantities"] = [
            {
                "product_name": row.get("product_name", ""),
                "tier": row.get("tier", ""),
                "default_quantity": row.get("default_quantity"),
            }
            for row in storage.list_stock_items(store)
        ]
    except Exception:  # noqa: BLE001
        projection["quantities"] = []

    try:
        from . import hotdeals

        relevant, exceptional = hotdeals.find(storage, chains=[store] if store else None)
        deals = [d for d in (relevant + exceptional) if d.stockable]
        projection["promotions"] = [
            {"name": d.name, "discount": d.discount, "chain": d.chain}
            for d in deals
        ][:_MAX_PROMOTIONS]
    except Exception:  # noqa: BLE001
        projection["promotions"] = []

    return projection
