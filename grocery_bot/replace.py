""""X במקום Y" — a correction, carried out truthfully. Phase 9 (2026-09-17).

Until this module, the replace handler called `adapter.remove_from_cart`,
a method neither adapter has ever had: every replace added X and silently
left Y in the cart, then reported "(את Y לא הצלחתי להסיר)" as if it had
tried. The household read that as flakiness; it was a dead call.

What a replace means here, in order:

1. **Reject Y for the term Y** — an explicit correction is the one thing
   that writes a durable rejection in v1 (see storage.reject_product).
   Done before the add so nothing can pick Y again on the way.
2. **Add X** under its own cart run, through the normal path (identity,
   remembered choice, resolver, breaker, verification).
3. **Remember X as a human choice** for the term X, once its product code
   is known from the add.
4. **Remove Y from the cart** — only if X was actually added, and only
   where the chain has a real per-line remover (Shufersal `remove_item`,
   first 100 lines). Tiv Taam has none; the message says so instead of
   pretending. If X was not added, Y is deliberately left in place.

Every branch is reported as what it is: added/verified, added/unverified,
not added; removed, failed, unsupported, unknown-product.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .models import PlanTerm
from .orchestrator import add_terms_to_cart
from .storage import normalize_term

logger = logging.getLogger(__name__)


@dataclass
class ReplaceOutcome:
    store: str
    old: str
    new: str
    old_code: str = ""
    old_name: str = ""
    added: bool = False
    verification: str = "n/a"
    new_code: str = ""
    new_name: str = ""
    # removed | failed | unsupported | unknown_product | not_attempted
    removed: str = "not_attempted"
    detail: str = ""


def replace_product(storage, factories: dict, store: str, old: str, new: str) -> ReplaceOutcome:
    out = ReplaceOutcome(store=store, old=old, new=new)
    factory = factories.get(store)
    if factory is None:
        out.detail = "no adapter for this store"
        return out

    out.old_code, out.old_name = _old_identity(storage, factory, store, old)
    if out.old_code:
        storage.reject_product(store, old, out.old_code, out.old_name, source="human")

    reports = add_terms_to_cart(storage, {store: factory}, [PlanTerm(new, 1, "replace", old)],
                                trigger="replace")
    report = reports.get(store)
    result = next(iter(report.added), None) if report is not None and report.added else None
    if result is not None:
        out.added = True
        out.verification = getattr(result, "verification", "n/a") or "n/a"
        out.new_code = result.product_code or ""
        out.new_name = result.item_name or new
        if out.new_code:
            storage.remember_choice(store, new, out.new_code, out.new_name, source="human")
    else:
        out.detail = _first_failure(report)
        return out

    if not out.old_code:
        out.removed = "unknown_product"
        return out
    try:
        with factory() as adapter:
            remover = getattr(adapter, "remove_item", None)
            if remover is None:
                out.removed = "unsupported"
            else:
                out.removed = "removed" if remover(out.old_code) else "failed"
    except Exception:  # noqa: BLE001
        logger.exception("Removing %r (%s) from the %s cart failed", old, out.old_code, store)
        out.removed = "failed"
    return out


def format_replace(out: ReplaceOutcome) -> str:
    if not out.added:
        why = f" ({out.detail})" if out.detail else ""
        return f"לא הצלחתי להוסיף את {out.new}{why}; {out.old} נשאר בעגלה."
    head = f"🔄 {out.new_name or out.new} במקום {out.old}."
    if out.verification == "unverified":
        head += " (ההוספה לא אומתה מול העגלה — בדקו שם.)"
    tail = {
        "removed": "",
        "failed": f"\nאת {out.old} לא הצלחתי להסיר — בדקו בעגלה.",
        "unsupported": f"\nב{_store_he(out.store)} אין לי הסרה מהעגלה — הסירו את {out.old} ידנית.",
        "unknown_product": f"\nלא זיהיתי איזו שורה בעגלה היא {out.old} — הסירו ידנית.",
        "not_attempted": "",
    }[out.removed]
    return head + tail


def _old_identity(storage, factory, store: str, old: str) -> tuple[str, str]:
    """Which product in this cart is 'old'? The remembered choice, else the cart line."""
    remembered = storage.preferred_for(store, old)
    if remembered and remembered.get("product_code"):
        return remembered["product_code"], remembered.get("product_name") or old
    words = set(normalize_term(old).split())
    if not words:
        return "", ""
    try:
        with factory() as adapter:
            summary = adapter.cart_summary()
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the %s cart to find %r", store, old)
        return "", ""
    if not summary.get("ok"):
        return "", ""
    for line in summary.get("items") or []:
        name = str(line.get("name") or "")
        if words <= set(normalize_term(name).split()) and line.get("code"):
            return str(line["code"]), name
    return "", ""


def _first_failure(report) -> str:
    if report is None:
        return "אין דוח"
    for bucket in ("not_found", "ambiguous", "errors", "skipped"):
        items = getattr(report, bucket, None) or []
        if items:
            first = items[0]
            return getattr(first, "detail", "") or bucket
    return ""


def _store_he(store: str) -> str:
    return {"tivtaam": "טיב טעם", "shufersal": "שופרסל"}.get(store, store)
