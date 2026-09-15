"""Read the household's own Tiv Taam order history.

The gap this closes, in one sentence: a Tiv Taam shop was invisible end
to end. `order_log` was written only by the Shufersal reader in
`history.py`, so nothing at this chain could ever move the `shopped →
confirmed` step, feed the cadence the nudge counts from, or say what was
deleted before paying. On 2026-09-15 that reached the household — the
reminder announced "8 days since your last order" two days after Ishay
shopped Tiv Taam and said so. See `GOALS.md`.

It turned out to be wiring, not building. `TivTaamApi.orders()` and
`.order()` were written on 2026-08-31 alongside the rest of the
Self-Point client and never called by anything: 40 orders back to 2020,
with full line items, sitting unread. What follows is the normalising
layer between that payload and this project's tables, because the
payload is not safe to store as it arrives.

**What is deliberately dropped.** The order payload carries the
household's street address, entrance code, floor, apartment, phone
number, email, and the first and last names of the picker and the
delivery driver. None of it is needed to know what was bought, so none
of it is kept and none of it is logged — `summarise_order` names the
five fields that survive, and that whitelist is the boundary. Card data
is already removed upstream by `tivtaam_api.strip_payment`; this module
never touches a payment field at all.

**Three traps that cost time here**, each found in the real 09-12 order:

1. **Weights are kilograms, not grams.** Shufersal's `BY_WEIGHT` sends
   500 for half a kilo; Tiv Taam sends `quantity: 0.5` with
   `isWeightable`. Reading one convention as the other is a factor of
   1000 in either direction.
2. **A substitution is two lines, not one.** The original comes back
   with `status: 5` and `actualQuantity: 0`; the replacement is a
   separate line carrying `substituteId`. Counting both double-counts
   the item, and counting the `status: 5` line alone records something
   that never arrived.
3. **Names contain embedded newlines.** "טבעפרוסט תרד 800 גרם\\n" is the
   real product name in the feed. Unstripped it breaks a Telegram
   message the same way the asterisks in Shufersal names did.

And a fourth that was already known from Shufersal: the delivery fee
arrives as an ordinary line item ("משלוח אינטרנט", ₪29.90), so without a
filter it looks like a product the household buys every single time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

STORE = "tivtaam"

# Israel is UTC+2/+3. `timePlaced` is UTC with a Z; Shufersal's reader
# writes naive local time, and `order_log` mixes both stores in one
# MAX(placed_at). Storing one in UTC and the other in local would put
# an order on the wrong calendar day for three hours a day, which is
# exactly long enough to be missed and exactly wrong enough to matter to
# a day counter.
_ISRAEL_OFFSET_SUMMER = timedelta(hours=3)

# Not a product, however much the line item looks like one.
NON_PRODUCT_MARKERS = ("משלוח",)

# `status` on a line. 5 is the original half of a substitution: ordered,
# never delivered, superseded by a separate line.
STATUS_NOT_DELIVERED = 5


def _clean_name(value) -> str:
    """Product names arrive with embedded newlines and stray whitespace."""
    return " ".join(str(value or "").split())


def _is_product(name: str) -> bool:
    return bool(name) and not any(mark in name for mark in NON_PRODUCT_MARKERS)


def _placed_at(value) -> str:
    """UTC `timePlaced` as naive Israel local time, matching Shufersal."""
    raw = str(value or "")
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("tivtaam: unparseable timePlaced %r; kept verbatim", raw)
        return raw[:19]
    if parsed.tzinfo is None:
        return parsed.replace(microsecond=0).isoformat()
    local = parsed.astimezone(timezone.utc) + _ISRAEL_OFFSET_SUMMER
    return local.replace(tzinfo=None, microsecond=0).isoformat()


def summarise_order(order: dict) -> dict:
    """One order reduced to the five fields this project stores.

    The whitelist *is* the privacy boundary — see the module docstring
    for what the payload also contains. Adding a field here is a decision
    about what the database holds about the household's home, not a
    convenience.
    """
    return {
        "code": str(order.get("id") or ""),
        "placed_at": _placed_at(order.get("timePlaced")),
        "total": order.get("totalAmount"),
        "item_count": order.get("itemsCount"),
        "discount": order.get("totalDiscount"),
    }


def order_lines(order: dict) -> list[dict]:
    """The products actually delivered, one entry each.

    Weights stay in kilograms, the unit the chain itself uses — converting
    to Shufersal's grams here would hide the difference in a place nobody
    would look for it again. `quantity` is what was ordered and
    `actual_quantity` what was picked; for a weighed item those differ on
    almost every line (0.5 kg of bananas came back as 0.332).
    """
    lines = []
    for raw in order.get("lines") or []:
        if raw.get("status") == STATUS_NOT_DELIVERED:
            continue  # the original half of a substitution; it never arrived
        name = _clean_name(raw.get("name"))
        if not _is_product(name):
            continue
        actual = raw.get("actualQuantity")
        if actual in (None, 0):
            continue  # ordered, not delivered, and not marked as such
        lines.append({
            "code": str(raw.get("productId") or ""),
            "barcode": str(raw.get("barcode") or ""),
            "name": name,
            "quantity": raw.get("quantity"),
            "actual_quantity": actual,
            "weighable": bool(raw.get("isWeightable")),
            "price": raw.get("price"),
            "total": raw.get("totalPrice"),
            "substituted": raw.get("substituteId") is not None,
        })
    return lines


def fetch_orders(api, size: int = 100) -> list[dict]:
    """Order summaries, newest first. Raises nothing the caller can fix."""
    payload = api.orders(size=size)
    orders = (payload or {}).get("orders") or []
    return [summarise_order(order) for order in orders if order.get("id")]


def sync(storage, api, size: int = 100) -> dict:
    """Write any new Tiv Taam orders into `order_log`.

    Returns what happened rather than logging it, so the caller decides
    whether it is worth telling anyone. `log_orders` is idempotent and
    also advances `shopped → confirmed` for this chain, which is the step
    that could never fire for Tiv Taam before this function existed.
    """
    orders = fetch_orders(api, size=size)
    added = storage.log_orders(orders, store=STORE)
    newest = max((o["placed_at"] for o in orders if o["placed_at"]), default="")
    return {"seen": len(orders), "added": added, "newest": newest}


def record_purchases(storage, api, order_code: str) -> int:
    """Remember when each product in one order was last bought.

    Separate from `sync` because it costs a request per order, and the
    cadence counter only needs the summaries. Worth calling for a fresh
    order: it is what lets the second chain answer "when did we last buy
    this" at all.
    """
    detail = api.order(int(order_code))
    day = _placed_at(detail.get("timePlaced"))[:10]
    if not day:
        return 0
    entries = [(line["code"], day) for line in order_lines(detail) if line["code"]]
    if not entries:
        return 0
    return storage.record_last_purchase(STORE, entries)
