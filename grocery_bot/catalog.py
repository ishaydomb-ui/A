"""Wiring between the public price feed and stored state.

Kept separate from both `prices` (pure feed access and parsing) and
`storage` (pure persistence) because storage already imports the feed's
dataclasses — putting the refresh in either would make the two import
each other.
"""
from __future__ import annotations

import logging

from .models import AdHocRequest, BaseListItem
from .prices import PricedProduct, PromotionItem, fetch_branch_snapshot
from .storage import Storage

logger = logging.getLogger(__name__)


def refresh_catalog(storage: Storage, store_id: str) -> dict[str, str]:
    """Pull the current snapshot for a branch and replace the stored catalog."""
    products, promotions, source_info = fetch_branch_snapshot(store_id)
    source_info["branch"] = store_id
    storage.replace_catalog(products, promotions, source_info)
    logger.info(
        "Catalog refreshed for branch %s: %d products, %d promotion rows",
        store_id,
        len(products),
        len(promotions),
    )
    return storage.catalog_meta()


def _money(value: float) -> str:
    return f"{value:,.2f}₪".replace(".00₪", "₪")


def format_product_line(product: PricedProduct, deal: PromotionItem | None) -> str:
    line = f"• {product.name} — {_money(product.price)}"
    if product.is_weighted and product.unit_of_measure_price:
        line += f" ({_money(product.unit_of_measure_price)}/{product.unit_of_measure})"
    if deal is not None:
        saving = (1 - deal.discounted_price / product.price) * 100 if product.price else 0
        line += f"\n   🏷️ {_money(deal.discounted_price)} (-{saving:.0f}%)"
        if deal.min_qty > 1:
            line += f" בקניית {deal.min_qty:.0f}"
        line += f" — {deal.description}"
    return line


def format_search_answer(
    query: str, results: list[tuple[PricedProduct, PromotionItem | None]]
) -> str:
    if not results:
        return (
            f"לא נמצא '{query}' בקטלוג הסניף.\n"
            "אפשר לנסות ניסוח אחר (למשל 'קוטג' במקום 'קוטג׳ תנובה')."
        )
    lines = [f"*{query}*"]
    lines += [format_product_line(product, deal) for product, deal in results]
    return "\n".join(lines)


def format_full_list(
    base_items: list[BaseListItem], adhoc_items: list[AdHocRequest]
) -> str:
    """The whole current shopping picture, in one message.

    Both halves are shown together because that is the question people
    actually ask ("what's on the list?") — the split between standing
    items and this-week's additions is an implementation detail, so it's
    a heading rather than two separate commands.
    """
    if not base_items and not adhoc_items:
        return "הרשימה ריקה כרגע. פשוט תכתבו לי מה צריך ואוסיף."

    lines: list[str] = []
    if base_items:
        lines.append(f"*רשימת הבסיס* ({len(base_items)})")
        lines += [f"• {item.describe()}" for item in base_items]
    if adhoc_items:
        if lines:
            lines.append("")
        lines.append(f"*נוסף לפעם הבאה* ({len(adhoc_items)})")
        lines += [
            f"• {item.describe()}"
            + (f"  _{item.requested_by}_" if item.requested_by else "")
            for item in adhoc_items
        ]
    return "\n".join(lines)


def find_deals_for_base_list(
    storage: Storage, base_items: list[BaseListItem], per_item: int = 6
) -> list[tuple[BaseListItem, PricedProduct, PromotionItem]]:
    """Genuine promotions on anything in the standing list.

    This is the "brand lock-in" pain point from the project goals: the
    household always buys the same variant, so a better deal on a
    neighbouring brand goes unnoticed. Searching by the item's generic
    name (not the store-specific search term) is deliberate — the whole
    point is to surface alternatives to the usual pick.
    """
    found = []
    for item in base_items:
        best: tuple[PricedProduct, PromotionItem] | None = None
        for product, deal in storage.search_with_deals(item.name, limit=per_item):
            if deal is None:
                continue
            if best is None or deal.discounted_price < best[1].discounted_price:
                best = (product, deal)
        if best is not None:
            found.append((item, best[0], best[1]))
    return found


def format_deals_report(
    deals: list[tuple[BaseListItem, PricedProduct, PromotionItem]]
) -> str:
    if not deals:
        return "אין כרגע מבצעים אמיתיים על פריטי רשימת הבסיס בסניף הזה."
    lines = ["*מבצעים על פריטי רשימת הבסיס*"]
    for item, product, deal in deals:
        saving = (1 - deal.discounted_price / product.price) * 100 if product.price else 0
        lines.append(
            f"• {item.name} → {product.name}\n"
            f"   {_money(product.price)} ⟵ {_money(deal.discounted_price)} (-{saving:.0f}%)"
            f" — {deal.description}"
        )
    return "\n".join(lines)
