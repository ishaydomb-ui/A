"""Gordon -> Work SAFE grocery context export (2026-09-20).

The minimum-safe export for ChatGPT Work's first pilot, built from the
read-only audit delivered the same day
(gordon_work_context_audit.md/_field_guide.md/_source_map.md). Work will
independently decide what to buy and resolve products against the live
Tiv Taam catalogue itself -- this module's only job is honest factual
memory, so nothing here is a planning conclusion:

- No `due`/`gordon_due`/`due_reason` (shelflife.py) -- gated on a Tiv
  Taam department classification the audit found is 100% broken.
- No department, no stock_items.default_quantity presented as an
  observed quantity, no standing-list/planner output.
- No promotions at all in this first pilot -- hotdeals.py currently
  leaks Shufersal promotions into a Tiv Taam-scoped query (the audit's
  finding #3); Work is expected to check live Tiv Taam promotions
  itself instead.
- Product preferences appear only as `product_hints`, and only when
  `source='human'` (an explicit tap/correction) -- 2026-09-20 (round 2):
  even `source='purchase'` rows were found to include demonstrably wrong
  mappings (a Hebrew description written into the code column), so
  provenance alone is not treated as evidence of correctness. A term
  with no human-confirmed mapping carries no hint at all here; Work
  resolves it against the live catalogue instead of inheriting a guess.

Same hard boundary as work_projection.py/work_planner_snapshot.py:
never import plancontext, never touch an adapter/browser/session file,
only Storage's own public methods, no raw SQL, gated by the same
_SECRET_MARKERS list. See docs/gordon_work_context_schema.md for the
field-by-field contract this module implements.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = "gordon-work-safe-context/v1"
_SECRET_MARKERS = ("password", "cookie", "token", "session", "secret")

# "Prefer the last 8-12 weeks or last ~10 real orders" -- whichever gives
# more signal. A household ordering roughly weekly clears 10 orders well
# inside 12 weeks; a quiet stretch (holiday, a chain tried less often)
# falls back to the last 10 orders regardless of date so Work still gets
# real evidence rather than an empty window.
RECENT_WINDOW_DAYS = 84
MIN_ORDERS = 10


def build_export(storage, store: str) -> dict:
    """The full safe export for one store. Never raises outward -- each
    section degrades to an empty list/dict on its own failure, same
    discipline as work_projection.py/work_planner_snapshot.py."""
    export: dict = {"schema": SCHEMA_VERSION}

    try:
        export["metadata"] = _metadata(storage, store)
    except Exception:  # noqa: BLE001
        export["metadata"] = {"generated_at": datetime.now(timezone.utc).isoformat(), "store": store}

    try:
        export["pending_needs"] = _pending_needs(storage)
    except Exception:  # noqa: BLE001
        export["pending_needs"] = []

    try:
        export["recent_purchase_history"] = _recent_purchase_history(storage, store)
    except Exception:  # noqa: BLE001
        export["recent_purchase_history"] = {"orders": [], "note": "unavailable"}

    try:
        export["observed_purchase_summary"] = _observed_purchase_summary(storage, store)
    except Exception:  # noqa: BLE001
        export["observed_purchase_summary"] = []

    try:
        export["product_hints"] = _product_hints(storage, store)
    except Exception:  # noqa: BLE001
        export["product_hints"] = []

    try:
        export["explicit_rejections"] = _explicit_rejections(storage, store)
    except Exception:  # noqa: BLE001
        export["explicit_rejections"] = []

    try:
        export["repeat_cart_failures"] = _repeat_cart_failures(storage, store)
    except Exception:  # noqa: BLE001
        export["repeat_cart_failures"] = []

    export["retailer_context"] = _retailer_context(store)
    return export


def _metadata(storage, store: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    orders = storage.list_orders(store)
    lines = storage.tivtaam_purchase_lines() if store == "tivtaam" else []
    return {
        "generated_at": now,
        "store": store,
        "schema_doc": "docs/gordon_work_context_schema.md",
        "source_freshness": {
            "order_log_latest": orders[0]["placed_at"] if orders else None,
            "order_log_count": len(orders),
            "tivtaam_order_lines_latest": lines[0]["order_date"] if lines else None,
            "tivtaam_order_lines_count": len(lines),
        },
        "excluded_by_design": [
            "gordon_due", "due_signal", "due_reason", "department",
            "stock_items.default_quantity (presented as observed)",
            "standing-list / planner buy-don't-buy output", "promotions",
            "preferred_products rows where source != 'human' (purchase/"
            "inferred/search -- excluded from product_hints since "
            "2026-09-20 round 2, after finding wrong mappings even at "
            "source='purchase')",
        ],
    }


def _pending_needs(storage) -> list[dict]:
    """Verbatim -- adhoc_requests has no store column; a household need
    is not tied to a chain until Gordon resolves it."""
    return [
        {
            "id": r.id, "text": r.text, "requested_by": r.requested_by,
            "requested_at": r.created_at, "quantity": r.quantity,
            "amount": r.amount, "unit": r.unit, "brand": r.brand,
        }
        for r in storage.list_pending_adhoc()
    ]


def _recent_purchase_history(storage, store: str) -> dict:
    if store != "tivtaam":
        return {
            "orders": [],
            "note": "Per-line purchase evidence (tivtaam_order_lines) is currently Tiv Taam-only.",
        }

    all_orders = storage.list_orders(store)  # newest first
    if not all_orders:
        return {"orders": [], "note": "No orders recorded for this store yet."}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_WINDOW_DAYS)).date().isoformat()
    within_window = [o for o in all_orders if (o["placed_at"] or "")[:10] >= cutoff]
    selected = within_window if len(within_window) >= MIN_ORDERS else all_orders[:MIN_ORDERS]

    orders_out = []
    for order in selected:
        lines = storage.tivtaam_lines_for_order(order["order_code"])
        orders_out.append({
            "order_id": order["order_code"],
            "order_date": (order["placed_at"] or "")[:10],
            "total": order["total"],
            "item_count": order["item_count"],
            "lines": [
                {
                    "product_code": ln["product_code"],
                    "barcode": ln["barcode"] or None,
                    "raw_name": ln["raw_name"],
                    "ordered_quantity": ln["ordered_quantity"],
                    "actual_quantity": ln["actual_quantity"],
                    "weightable": bool(ln["weightable"]),
                    "unit": ln["unit"] or None,
                    "price": ln["price"],
                    "line_total": ln["line_total"],
                    "substituted": bool(ln["substituted"]),
                }
                for ln in lines
            ],
            "line_detail_available": bool(lines),
        })
    return {
        "orders": orders_out,
        "window_days": RECENT_WINDOW_DAYS,
        "min_orders_target": MIN_ORDERS,
    }


def _observed_purchase_summary(storage, store: str) -> list[dict]:
    """Per recurring product: real observed dates/quantities only.

    Deliberately does not use stock_items.share/tier/department or
    shelflife.py's due model -- every number here is derived from actual
    tivtaam_order_lines rows, with the sample size that backs it shown
    alongside so Work can judge confidence itself rather than trust a
    label.
    """
    if store != "tivtaam":
        return []

    last_purchase = storage.last_purchase_dates(store)
    by_code: dict[str, list[dict]] = {}
    for line in storage.tivtaam_purchase_lines():
        by_code.setdefault(line["product_code"], []).append(line)

    out = []
    for code, lines in by_code.items():
        dates = sorted({ln["order_date"] for ln in lines})
        quantities = [ln["actual_quantity"] for ln in lines if ln["actual_quantity"] not in (None, 0)]
        gaps = _day_gaps(dates)
        out.append({
            "product_code": code,
            "barcode": next((ln["barcode"] for ln in lines if ln["barcode"]), None),
            "raw_name": lines[-1]["raw_name"],
            "last_purchase_date": last_purchase.get(code).isoformat() if last_purchase.get(code) else (dates[-1] if dates else None),
            "observed_purchase_dates": dates,
            "observed_purchase_count": len(dates),
            "observed_typical_quantity": {
                "median": round(statistics.median(quantities), 3) if quantities else None,
                "sample_size": len(quantities),
                "unit": ('ק"ג' if any(ln["weightable"] for ln in lines) else None),
                "basis": "DERIVED_FROM_OBSERVED_HISTORY -- median of real tivtaam_order_lines.actual_quantity, not a planner default",
            },
            "observed_purchase_interval_days": {
                "median": round(statistics.median(gaps), 1) if gaps else None,
                "sample_size": len(gaps),
                "basis": "DERIVED_FROM_OBSERVED_HISTORY -- median gap between real observed purchase dates, not shelflife.py's model",
            },
        })
    out.sort(key=lambda r: r["last_purchase_date"] or "", reverse=True)
    return out


def _day_gaps(sorted_dates: list[str]) -> list[float]:
    if len(sorted_dates) < 2:
        return []
    parsed = [datetime.strptime(d, "%Y-%m-%d") for d in sorted_dates]
    return [(b - a).days for a, b in zip(parsed, parsed[1:])]


HUMAN_CONFIRMED_SOURCE = "human"


def _product_hints(storage, store: str) -> list[dict]:
    """Gordon's product preferences, as hints only -- never as ground
    truth, and only when a human explicitly confirmed the mapping.

    2026-09-20 (round 2): restricted to source='human' after the audit
    found demonstrably wrong mappings even at source='purchase' --
    Gordon's second-highest confidence tier, and one this export
    previously still included. Provenance is Gordon's confidence in the
    *choice*; it says nothing about whether the code is machine-
    resolvable, and this export no longer asks Work to make that
    inference itself. `machine_resolvable` stays on each hint as a
    defensive check even on the human-confirmed set: False whenever
    product_code is empty or textually identical to product_name (a
    Hebrew description written into the code column instead of a real
    Tiv Taam product id) -- included for transparency, not used to
    decide inclusion.
    """
    barcode_by_code = {row["product_code"]: row.get("barcode") for row in storage.list_stock_items(store)}
    out = []
    for row in storage.list_preferences(store):
        if row.get("source") != HUMAN_CONFIRMED_SOURCE:
            continue
        code = (row.get("product_code") or "").strip()
        name = (row.get("product_name") or "").strip()
        resolvable = bool(code) and code != name
        out.append({
            "household_term": row["term"],
            "product_display_name": row["product_name"],
            "barcode": barcode_by_code.get(code) or None,
            "stored_product_code": code,
            "provenance": row["source"],
            "evidence_count": row.get("evidence_count"),
            "machine_resolvable": resolvable,
        })
    return out


def _explicit_rejections(storage, store: str) -> list[dict]:
    """Strong constraints -- an explicit human 'not this one' always wins."""
    return [
        {
            "household_term": row["term"],
            "rejected_product_code": row["product_code"],
            "rejected_product_name": row["product_name"],
            "source": row["source"],
            "rejected_at": row["rejected_at"],
            "constraint": "DO_NOT_CHOOSE",
        }
        for row in storage.list_rejections(store)
    ]


def _repeat_cart_failures(storage, store: str) -> list[dict]:
    """Operational evidence (a term that keeps failing to add), not a
    product preference -- Work should read this as 'this term/resolution
    has been unreliable', never as 'don't buy this product'."""
    return [
        {
            "item_name": r["item_name"], "runs": r["runs"],
            "first_failed": r["first_failed"], "last_failed": r["last_failed"],
            "detail": r["detail"],
            "kind": "OPERATIONAL_EVIDENCE_NOT_A_PREFERENCE",
        }
        for r in storage.repeat_failures(min_runs=2, days=180)
        if r["store"] == store
    ]


def _retailer_context(store: str) -> dict:
    from . import chains

    return {
        "store": store,
        "display_name": chains.display_name(store),
        "is_regular_chain": chains.is_regular(store),
        "cart_fill_supported": chains.can_fill_cart(store),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
