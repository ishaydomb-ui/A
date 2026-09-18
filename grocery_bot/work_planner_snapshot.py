"""Gordon -> Work PLANNER snapshot (Ishay, 2026-09-18). A second, richer,
pure-read export -- separate from work_projection.py, which stays exactly
as it is. That module answers "what does Gordon already intend to do";
this one answers "what does Gordon *know*", so Work can plan independently
and the two conclusions can be compared (Gordon-planner-then-Work-executor
vs. Gordon-data-then-Work-planner-and-executor).

Same boundary as work_projection.py, verified the same way: no
plancontext import, no adapter/browser/Playwright object touched, every
read through Storage's own public methods (adding exactly one new
accessor, storage.recent_order_prices, where none existed for that
specific field -- see its own docstring), never a raw SQL string here.

Nothing here writes anything back. It does not touch preferred_products,
product_rejections, stock_items, or any planner state -- read-only in
both code path and effect.

Scope: "staples" are stock_items rows tiered A/B/C for the requested
store -- the same relevance cut digest.py and propose_cycle already use
elsewhere in this codebase for "products worth a household's attention",
not an arbitrary new filter. Tier D exists in the data but is treated the
same way it already is everywhere else: too rarely bought to be part of
current habits.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

_SECRET_MARKERS = ("password", "cookie", "token", "session", "secret")
RELEVANT_TIERS = ("A", "B", "C")
MAX_RECENT_PRICES = 5


def build_snapshot(storage, store: str) -> dict:
    """The full planner snapshot for one store. Never raises outward --
    each section degrades to an empty list/dict on its own failure, same
    discipline as work_projection.build_projection."""
    snapshot: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "store": store,
    }

    try:
        snapshot["household_needs"] = _household_needs(storage, store)
    except Exception:  # noqa: BLE001
        snapshot["household_needs"] = []

    try:
        snapshot["staples"] = _staples(storage, store)
    except Exception:  # noqa: BLE001
        snapshot["staples"] = []

    try:
        snapshot["promotions"] = _promotions(storage, store)
    except Exception:  # noqa: BLE001
        snapshot["promotions"] = []

    snapshot["retailer_context"] = _retailer_context(store)

    snapshot["data_quality"] = {
        "preference_source_labels": ["explicit", "purchase", "inferred", "search"],
        "notes": [
            "typical_quantity is Gordon's planner default (stock_items."
            "default_quantity/amount/unit), not a measured historical "
            "quantity -- no table records how much was actually bought "
            "per order line, only per-day prices and order-level dates.",
            "due_signal/due_reason apply only to pantryable ('shelf-"
            "stable') departments (shelflife.py). A perishable staple is "
            "refilled every standing-list cycle rather than interval-"
            "tracked, and is labelled accordingly rather than given a "
            "fabricated due/not-due answer.",
            "accepted_substitutions is not a concept Gordon currently "
            "stores anywhere (product_rejections records only explicit "
            "'not this one' corrections, never an approved swap) -- "
            "always an empty list here, not a silent omission.",
            "recent_prices only appears where store_prices has a row "
            "with source='order' (a real synced order line), never the "
            "'feed' rows (the public price-transparency scrape), so an "
            "empty list can mean 'never bought through a source Gordon "
            "has synced' rather than 'never bought'.",
        ],
    }
    return snapshot


def _household_needs(storage, store: str) -> list[dict]:
    needs = []
    for r in storage.list_pending_adhoc():
        preferred = storage.preferred_for(store, r.text)
        needs.append({
            "term": r.text,
            "requested_quantity": r.quantity,
            "requested_by": r.requested_by,
            "requested_at": r.created_at,
            "preferred_product": _preferred_block(preferred) if preferred else None,
        })
    return needs


def _preferred_block(row: dict) -> dict:
    return {
        "product_code": row.get("product_code", ""),
        "product_name": row.get("product_name", ""),
        "source": row.get("source", ""),  # explicit(human) | purchase | inferred | search
        "evidence_count": row.get("evidence_count"),
    }


def _staples(storage, store: str) -> list[dict]:
    from . import shelflife

    # Loaded once, indexed by term/store, rather than one query per staple
    # -- the same shape list_preferences/list_rejections already return.
    preferences = {row["term"]: row for row in storage.list_preferences(store)}
    rejections: dict[str, list] = {}
    for row in storage.list_rejections(store):
        rejections.setdefault(row["term"], []).append(row)
    last_purchase = storage.last_purchase_dates(store)
    shelf_by_code = {item.product_code: item for item in shelflife.build_items(storage, store)}

    today = date.today()
    out = []
    for row in storage.list_stock_items(store):
        if row.get("tier") not in RELEVANT_TIERS:
            continue
        term = row["product_name"]
        code = row["product_code"]
        barcode = str(row.get("barcode") or "")

        pref = preferences.get(term)
        shelf = shelf_by_code.get(code)
        last_date = last_purchase.get(code)

        if shelf is not None:
            due_signal = shelf.status(today)  # due | soon | stocked | lapsed | unknown
            due_reason = _due_reason(shelf, today)
            typical_interval_days = shelf.expected_interval_days
            last_purchase_days_ago = shelf.days_since(today)
        else:
            due_signal = "not_modeled"
            due_reason = ("not a shelf-stable department in Gordon's own "
                          "classification; refilled every standing-list "
                          "cycle, not interval-tracked")
            typical_interval_days = None
            last_purchase_days_ago = (today - last_date).days if last_date else None

        out.append({
            "term": term,
            "known_product": {
                "product_code": code, "barcode": barcode or None,
                "department": row.get("department", ""), "tier": row.get("tier", ""),
            },
            "preferred_product": _preferred_block(pref) if pref else None,
            "rejections": [
                {"product_code": r["product_code"], "product_name": r["product_name"], "source": r["source"]}
                for r in rejections.get(term, [])
            ],
            "accepted_substitutions": [],  # see data_quality.notes -- not modeled anywhere in Gordon
            "last_purchase_date": last_date.isoformat() if last_date else None,
            "last_purchase_days_ago": last_purchase_days_ago,
            "recent_prices": storage.recent_order_prices(store, barcode, MAX_RECENT_PRICES) if barcode else [],
            "typical_interval_days": typical_interval_days,
            "typical_quantity": {
                "quantity": row.get("default_quantity"),
                "amount": row.get("amount"), "unit": row.get("unit", ""),
                "basis": "planner_default",  # not a measured historical quantity -- see data_quality.notes
            },
            "gordon_due": due_signal in ("due", "lapsed"),
            "due_signal": due_signal,
            "due_reason": due_reason,
        })
    return out


def _due_reason(shelf, today) -> str:
    interval = shelf.expected_interval_days
    elapsed = shelf.days_since(today)
    if interval is None or elapsed is None:
        return "not enough purchase history to estimate an interval"
    basis = "the chain's own measured interval" if getattr(shelf, "measured_interval_days", None) else \
        "Gordon's own share-based estimate (1/share x household order gap)"
    return f"{elapsed}d since last purchase vs an expected ~{interval:.0f}d ({basis})"


def _promotions(storage, store: str) -> list[dict]:
    from . import hotdeals

    relevant, exceptional = hotdeals.find(storage, chains=[store] if store else None)
    live = storage.live_store_promotions(store)  # barcode -> row with starts_at/ends_at/observed_at
    out = []
    for deal, bucket in [(d, "relevant") for d in relevant] + [(d, "exceptional") for d in exceptional]:
        validity = live.get(deal.barcode)
        out.append({
            "product_name": deal.name,
            "store": deal.chain,
            "normal_price": deal.reference_price,
            "promo_price": deal.price,
            "discount_fraction": deal.discount,
            "saving": deal.saving,
            "starts_at": validity.get("starts_at") if validity else None,
            "ends_at": validity.get("ends_at") if validity else None,
            "observed_at": validity.get("observed_at") if validity else None,
            # Gordon's own existing signal for "worth acting on" -- see hotdeals.py:
            # `relevant` = on what this household already buys; `exceptional` =
            # a remarkable discount on something novel; `stockable` = a
            # shelf-stable category worth buying ahead of need.
            "gordon_bucket": bucket,
            "gordon_stockable": deal.stockable,
        })
    return out


def _retailer_context(store: str) -> dict:
    from . import chains

    return {
        "store": store,
        "display_name": chains.display_name(store),
        "is_regular_chain": chains.is_regular(store),
        "cart_fill_supported": chains.can_fill_cart(store),
    }
