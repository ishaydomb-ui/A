"""The digest: one proactive message, everything needed to order, zero questions.

This is the product's center of gravity after the pivot. The bot's job
is not to drive a cart — the store's own app does that well — but to
open the conversation at the right moment with everything already
thought through: what you're due to buy, what someone at home asked
for, what's genuinely worth grabbing this week, and the text to paste
into the store's quick-order box. The user reads one message, pastes,
filters in the app, done.

Composition rules learned the hard way in this project:
- Zero questions. Anything ambiguous is resolved silently from history
  or left out; a digest that interrogates is a chore, not a service.
- Every number is honest: deals only if genuinely usable and below the
  price actually paid; price-history claims only once there is enough
  history to back them.
- The paste block is a separate bare message (the quick-order box
  matches whatever it's handed).
"""
from __future__ import annotations

import datetime
import logging

from .catalog import find_cheaper_equivalents
from .learn import days_since_last_order, typical_gap_days
from .listbuilder import available_lists, build as build_list
from .radar import find_stockup_deals
from .unitprice import for_product

logger = logging.getLogger(__name__)

# How rare a price has to be, against the item's own recorded history,
# before the digest calls it out. Needs this many days of history first —
# early on the claim would be hollow, so it simply isn't made.
HISTORY_MIN_DAYS = 21

MAX_DEALS = 5
MAX_SWAPS = 3


def compose(storage, store: str = "shufersal") -> tuple[str, str]:
    """Build the digest. Returns (message, paste_block)."""
    now = datetime.datetime.now()
    since = days_since_last_order(storage, store)
    gap = typical_gap_days(storage, store)

    lines: list[str] = [f"🛒 *הגיע זמן קנייה* · {now.strftime('%d.%m')}"]
    if since is not None:
        lines.append(f"_עברו {since:.0f} ימים מההזמנה האחרונה (הקצב שלכם ~{gap:.0f} ימים)._")
    lines.append("")

    # --- the list ---------------------------------------------------------
    rows = storage.list_stock_items(store)
    spec = build_list(next(s for s in available_lists() if s.key == "full"), rows)
    requests = storage.list_pending_adhoc()

    by_department: dict[str, int] = {}
    for row in spec.items:
        by_department[row["department"]] = by_department.get(row["department"], 0) + 1
    departments = " · ".join(f"{name} {count}" for name, count in sorted(by_department.items(), key=lambda p: -p[1]))
    lines.append(f"*הרשימה:* {len(spec.items)} מוצרים ({departments})")

    if requests:
        lines.append("")
        lines.append("*בקשות אישיות:*")
        for item in requests:
            who = f" — 🙋 {item.requested_by}" if item.requested_by else ""
            lines.append(f"• {item.text}{who}")

    # --- worth grabbing ---------------------------------------------------
    deals = find_stockup_deals(storage, store)[:MAX_DEALS]
    if deals:
        lines.append("")
        lines.append("*שווה לאגור השבוע:*")
        for deal in deals:
            mark = "🧺 " if deal.pantryable else ""
            rarity = _rarity_note(storage, deal)
            lines.append(
                f"• {mark}{deal.catalog_name} — *{deal.deal_price:.2f}₪* "
                f"(במקום {deal.shelf_price:.2f}) −{deal.discount * 100:.0f}%{rarity}"
            )

    # --- cheaper than your usual -----------------------------------------
    swaps = _swap_suggestions(storage, rows, store)
    if swaps:
        lines.append("")
        lines.append("*זול יותר מהרגיל שלכם (לפי ₪ ליחידת מידה):*")
        lines += swaps

    lines.append("")
    lines.append("_להעתיק את ההודעה הבאה ל'הזמנה מהירה' באפליקציה, לסנן שם ולסיים._")

    paste = "\n".join(row["product_name"] for row in spec.items)
    if requests:
        paste += "\n" + "\n".join(item.text for item in requests)
    return "\n".join(lines), paste


def _rarity_note(storage, deal) -> str:
    """" · המחיר הכי נמוך שראינו" — only when history can back it."""
    stats = None
    try:
        # The radar's catalog product is keyed by feed item code via name
        # search; use best price seen across history for the same name via
        # a conservative exact-price check.
        results = storage.search_with_deals(deal.catalog_name, limit=1)
        if results:
            stats = storage.price_stats(results[0][0].item_code)
    except Exception:
        logger.debug("rarity lookup failed", exc_info=True)
    if not stats or stats["days"] < HISTORY_MIN_DAYS:
        return ""
    if deal.deal_price <= stats["best"] + 0.01:
        return " · הנמוך שראינו"
    if stats["promo_share"] >= 0.5:
        return " · במבצע רוב הזמן — לא לשלם מלא"
    return ""


def _swap_suggestions(storage, stock_rows, store: str) -> list[str]:
    """Cheaper equivalents for the most-bought items, at most a few.

    Only tier A/B items are checked — a swap suggestion on something
    bought twice a year is noise — and only clear wins are shown.
    """
    suggestions = []
    frequent = [row for row in stock_rows if row["tier"] in ("A", "B")]
    for row in frequent:
        if len(suggestions) >= MAX_SWAPS:
            break
        try:
            reference, cheaper = find_cheaper_equivalents(
                storage, row["product_name"], reference_name=row["product_name"]
            )
        except Exception:
            continue
        if reference is None or not cheaper:
            continue
        product, deal, saving = cheaper[0]
        unit = for_product(product, deal)
        unit_text = f" · {unit.format()}" if unit else ""
        suggestions.append(
            f"• במקום {row['product_name']}: {product.name}{unit_text}  ↓{saving * 100:.0f}%"
        )
    return suggestions
