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
from .unitprice import best_value, describe, for_product

logger = logging.getLogger(__name__)


def refresh_catalog(storage: Storage, store_id: str) -> dict[str, str]:
    """Pull the current snapshot for a branch and replace the stored catalog.

    Keeps the previous promotions when a refresh comes back with none.
    The feed listing is scraped page by page and a page that fails is
    only warned about, so a transient error makes the PromoFull file
    invisible for that run — which used to wipe every promotion while
    still reporting success, leaving /deals quietly answering "no
    promotions" instead of admitting it had no data. Observed in
    practice: 18,456 rows at 06:19, zero at 12:17, both "successful".

    Stale promotions are the lesser evil: they carry their own validity
    dates, so an expired one is filtered at query time anyway.
    """
    products, promotions, source_info = fetch_branch_snapshot(store_id)
    source_info["branch"] = store_id

    if not promotions:
        existing = storage.catalog_meta().get("promo_file", "")
        if existing:
            logger.warning(
                "Price feed: no promotions in this snapshot; keeping the previous set "
                "from %s rather than wiping them.",
                existing,
            )
            storage.replace_products_only(products, source_info)
            return storage.catalog_meta()

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


def format_product_line(
    product: PricedProduct, deal: PromotionItem | None, best_value_mark: bool = False
) -> str:
    """One product line, with the per-kilo/litre price spelled out.

    The household was doing this ratio in their head at the shelf, which
    is exactly where it is easiest to get wrong: a small tub of cottage
    cheese looks cheaper at 3.30₪ than 6.40₪, while costing 33₪/kg
    against 25.60₪/kg.
    """
    line = f"• {product.name} — {_money(product.price)}"
    unit = for_product(product, deal)
    if unit is not None:
        line += f" · {unit.format()}"
    if best_value_mark:
        line += " 🏆"
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
    # 🏆 marks the best price per kilo/litre, not the lowest sticker price.
    best = best_value(results)
    lines += [
        format_product_line(product, deal, best_value_mark=(index == best))
        for index, (product, deal) in enumerate(results)
    ]
    if best is not None:
        lines.append("\n_🏆 = המשתלם ביותר ליחידת מידה._")
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


def _is_comparable(usual: PricedProduct, candidate: PricedProduct) -> bool:
    """Is `candidate` the same *kind* of thing as `usual`?

    Name search alone happily offers banana-flavoured snack rings as a
    substitute for a kilo of fresh bananas — they share a word, so they
    rank together. Selling shape is what actually separates them: fresh
    produce is weighed and priced per kilo, a packaged snack is neither.
    Comparing those two is worse than staying quiet, because a suggestion
    that misses this obviously erodes trust in the ones that don't.
    """
    return (
        usual.is_weighted == candidate.is_weighted
        and usual.unit_of_measure == candidate.unit_of_measure
    )


def find_deals_for_base_list(
    storage: Storage, base_items: list[BaseListItem], per_item: int = 6
) -> list[tuple[BaseListItem, PricedProduct, PromotionItem]]:
    """Genuine promotions on anything in the standing list.

    This is the "brand lock-in" pain point from the project goals: the
    household always buys the same variant, so a better deal on a
    neighbouring brand goes unnoticed. Searching by the item's generic
    name (not the store-specific search term) is deliberate — the whole
    point is to surface alternatives to the usual pick.

    A promotion only counts when it beats what the household actually
    pays. Most "deals" on a nearby product are on a pricier variant —
    organic bananas at 19.86 "reduced" from 20.90 are not a saving to
    someone who buys the 12.90 ones, and listing them turns the deals
    report into an upsell feed nobody trusts.
    """
    found = []
    for item in base_items:
        results = storage.search_with_deals(item.name, limit=per_item)
        if not results:
            continue
        # The closest name match stands in for "the kind of thing meant",
        # so a promoted banana-flavoured snack isn't offered as a deal on
        # fresh bananas. Same guard as find_cycle_alternatives.
        reference = results[0][0]
        best: tuple[PricedProduct, PromotionItem] | None = None
        for product, deal in results:
            if deal is None:
                continue
            if not _is_comparable(reference, product):
                continue
            if deal.discounted_price >= reference.price:
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


def find_cycle_alternatives(
    storage: Storage, item_names: list[str], per_item: int = 8, min_saving: float = 0.05
) -> list[tuple[str, PricedProduct, PricedProduct, PromotionItem]]:
    """Cheaper promoted alternatives to what a cycle just added.

    This is the brand-fixation pain point (goals, #2) made concrete: the
    household reliably buys the same variant, so a promotion on a
    neighbouring brand is invisible. Remembered product choices make that
    *worse*, not better, because the bot now goes straight to the usual
    pick without ever looking sideways — so the memory has to be paired
    with an explicit look at the alternatives.

    Returns (term, what-you-buy, alternative, its-promotion) per item
    where a genuinely cheaper promoted alternative exists.

    Product codes cannot be used to join the two sides: the web store's
    ids (``P_522319``) and the price feed's item codes are different
    namespaces entirely, so matching goes through the name search that
    already backs /price. `min_saving` keeps out noise — a few agorot
    difference is not worth a message.
    """
    suggestions = []
    for term in item_names:
        results = storage.search_with_deals(term, limit=per_item)
        if not results:
            continue
        # The first hit is the closest name match, i.e. the usual pick.
        usual_product, usual_deal = results[0]
        usual_price = usual_deal.discounted_price if usual_deal else usual_product.price

        best: tuple[PricedProduct, PromotionItem] | None = None
        for product, deal in results[1:]:
            if deal is None:
                continue
            if not _is_comparable(usual_product, product):
                continue
            if deal.discounted_price >= usual_price * (1 - min_saving):
                continue
            if best is None or deal.discounted_price < best[1].discounted_price:
                best = (product, deal)
        if best is not None:
            suggestions.append((term, usual_product, best[0], best[1]))
    return suggestions


def format_cycle_alternatives(
    suggestions: list[tuple[str, PricedProduct, PricedProduct, PromotionItem]]
) -> str:
    if not suggestions:
        return ""
    lines = ["*חלופות זולות יותר במבצע*"]
    for term, usual, alt, deal in suggestions:
        saving = usual.price - deal.discounted_price
        lines.append(
            f"• במקום {usual.name} ({_money(usual.price)})\n"
            f"   {alt.name} — {_money(deal.discounted_price)} "
            f"(חיסכון {_money(saving)}) — {deal.description}"
        )
    lines.append("\n_זו הצעה בלבד — לא שיניתי כלום בסל._")
    return "\n".join(lines)


# A cheaper equivalent is only worth raising when the gap is real. Below
# this the difference is pack-size rounding and shelf noise, not a
# decision worth making.
MIN_UNIT_SAVING = 0.15


def find_cheaper_equivalents(
    storage: Storage, product_name: str, per_item: int = 12, reference_name: str = ""
) -> tuple[PricedProduct | None, list[tuple[PricedProduct, PromotionItem | None, float]]]:
    """Comparable products with a materially better price per kilo/litre.

    The question this answers is the one the user was doing by hand: a
    branded frozen schnitzel at 71₪/kg sits on the same shelf as a
    comparable one at 28₪/kg, and sticker prices hide that completely
    because the packs are different sizes.

    Comparisons stay inside one selling shape (`_is_comparable`) so a
    per-kilo product is never held up against a per-unit one, and the
    reference is the closest name match — the thing the user meant.

    Returns (reference, [(product, deal, saving_fraction), ...]).
    """
    results = storage.search_with_deals(product_name, limit=per_item)
    if not results:
        return None, []

    # The comparison is only useful against what the household actually
    # buys. Search ranking is a poor stand-in for that: asking about
    # "שמן זית" ranks the shortest name first, which may already be the
    # cheapest item on the shelf and makes the answer trivially "nothing
    # is cheaper". `reference_name` lets the caller pass the remembered
    # product instead, so the question becomes "is there better value
    # than my usual", which is the one being asked.
    reference, reference_deal = results[0]
    if reference_name:
        for product, deal in results:
            if product.name.strip() == reference_name.strip():
                reference, reference_deal = product, deal
                break
    reference_unit = for_product(reference, reference_deal)
    if reference_unit is None:
        return reference, []

    cheaper = []
    for product, deal in results:
        # Skip the reference itself rather than a fixed position: once the
        # reference can be the remembered product, it is not necessarily
        # the first result any more.
        if product.item_code == reference.item_code:
            continue
        # Deliberately looser than `_is_comparable` here. That guard
        # compares raw unit strings, which is right for the deals report
        # (it keeps banana crisps away from bananas) but wrong for this:
        # it also rejects a 750ml bottle against a 3L one, since the feed
        # prices them per 100ml and per litre respectively. Comparing by
        # *dimension* is what makes different pack sizes comparable at
        # all, which is the entire point of the question.
        if product.is_weighted != reference.is_weighted:
            continue
        unit = for_product(product, deal)
        if unit is None or unit.dimension != reference_unit.dimension:
            continue
        saving = 1 - unit.value / reference_unit.value if reference_unit.value else 0
        if saving >= MIN_UNIT_SAVING:
            cheaper.append((product, deal, saving))
    cheaper.sort(key=lambda row: -row[2])
    return reference, cheaper


def format_cheaper_equivalents(
    reference: PricedProduct | None,
    cheaper: list[tuple[PricedProduct, PromotionItem | None, float]],
    query: str,
) -> str:
    if reference is None:
        return f"לא מצאתי '{query}' בקטלוג הסניף."
    reference_unit = for_product(reference)
    reference_line = (
        f"*{reference.name}* — {_money(reference.price)}"
        + (f" · {reference_unit.format()}" if reference_unit else "")
    )
    if not cheaper:
        return (
            f"{reference_line}\n\n"
            "לא מצאתי חלופה דומה שזולה משמעותית ליחידת מידה. "
            "כלומר מה שאתם קונים הוא כבר בחירה טובה."
        )
    lines = [reference_line, "", "*חלופות זולות יותר ליחידת מידה:*"]
    for product, deal, saving in cheaper[:5]:
        unit = for_product(product, deal)
        price = deal.discounted_price if deal else product.price
        maker = f" ({product.manufacturer})" if product.manufacturer.strip("- ") else ""
        lines.append(
            f"• {product.name}{maker} — {_money(price)}"
            + (f" · {unit.format()}" if unit else "")
            + f"  ↓{saving * 100:.0f}%"
        )
    lines += ["", "_ההשוואה לפי ₪ לק\"ג/ליטר, לא לפי מחיר המדבקה. לא שיניתי כלום בסל._"]
    return "\n".join(lines)
