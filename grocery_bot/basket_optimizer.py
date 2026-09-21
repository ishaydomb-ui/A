"""Basket economics across chains — vNext. Phase 1 declared the seam;
Phase 2b fills it in, read-only.

What it decides: for the draft's included items, which chain each one
should go to, what each chain's basket costs including delivery, and
whether splitting the shop across Tiv Taam and Shufersal is worth two
deliveries. What it never does: touch a cart, or change the existing
per-chain refill behaviour of `/done` — it only feeds the vNext flow.

Prices come from the chains' own feeds already in the DB
(`store_prices` for the portal chains, `catalog_products` for
Shufersal) against the product the semantic resolver chose *per chain*.
An item with no quotable product at a chain simply cannot go there; the
compare screen names it. Delivery fees default to `whereto.DELIVERY_FEES`
and can be overridden in `VNextConfig`; Shufersal's ₪599 gift threshold
is `threshold.DEFAULT_GIFT_THRESHOLD`, reported as a note, never used to
push spend.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .shopping_plan import ShoppingPlan
from .vnext_config import DEFAULT, VNextConfig

logger = logging.getLogger(__name__)

CHAINS = ("tivtaam", "shufersal")


@dataclass(frozen=True)
class ChainEconomics:
    store: str
    delivery_fee: float | None = None       # ₪, None = unknown
    minimum_order: float | None = None      # ₪, None = unknown
    free_delivery_from: float | None = None
    club_benefit_rate: float = 0.0          # e.g. 0.03 TivCoins
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LineQuote:
    term: str
    store: str
    product_code: str = ""
    product_name: str = ""
    unit_price: float | None = None         # ₪ per the comparable unit
    unit: str = ""
    sticker_price: float | None = None
    promo_price: float | None = None
    promo_quality: str = "none"             # none | readable | conditional
    historical_best: float | None = None
    historical_avg: float | None = None
    shelf_life_days: float | None = None
    expected_consumption_days: float | None = None
    available: bool | None = None           # None = not checked (no live read)


@dataclass(frozen=True)
class BasketQuote:
    store: str
    lines: tuple[LineQuote, ...] = ()
    subtotal: float | None = None
    delivery: float | None = None
    total: float | None = None
    coverage: float = 0.0                   # share of plan lines this chain can quote
    stock_up_value: float | None = None


@dataclass(frozen=True)
class OptimizerInput:
    plan: ShoppingPlan
    chains: tuple[ChainEconomics, ...]
    quotes: tuple[BasketQuote, ...] = ()
    split_penalty: float = 0.0              # ₪-equivalent friction of a second order


@dataclass(frozen=True)
class OptimizerResult:
    implemented: bool
    recommended_split: dict = field(default_factory=dict)   # store -> [terms]
    expected_total: float | None = None
    expected_saving_vs_single_chain: float | None = None
    reasons: tuple[str, ...] = ()
    inputs_summary: dict = field(default_factory=dict)


# -- economics per chain --------------------------------------------------------------

def chain_economics(config: VNextConfig = DEFAULT) -> dict[str, ChainEconomics]:
    from .threshold import DEFAULT_GIFT_THRESHOLD

    return {
        "shufersal": ChainEconomics("shufersal", delivery_fee=config.delivery_fee_shufersal,
                                    minimum_order=config.minimum_order_shufersal or None,
                                    notes=(f"מתנה מעל ₪{DEFAULT_GIFT_THRESHOLD:.0f}",)),
        "tivtaam": ChainEconomics("tivtaam", delivery_fee=config.delivery_fee_tivtaam,
                                  minimum_order=config.minimum_order_tivtaam or None,
                                  club_benefit_rate=0.03),
    }


# -- pricing the draft's items ------------------------------------------------------------

def price_items(storage, items: list[dict], config: VNextConfig = DEFAULT) -> None:
    """Fill `item["prices"][store]` from the feeds for every chain product
    the resolver chose. Missing price = None (not 0)."""
    for item in items:
        prices: dict = {}
        for store, prod in (item.get("products") or {}).items():
            prices[store] = _price_at(storage, store, prod.get("product_code", ""), prod.get("product_name", ""))
        item["prices"] = prices


def _price_at(storage, store: str, code: str, name: str) -> float | None:
    try:
        if store == "shufersal":
            row = None
            if code and code.isdigit() and len(code) >= 8:
                row = storage.catalog_price(code)
            if row is None and code:
                row = storage.catalog_price_by_suffix(code.replace("P_", ""))
            if row is None and name:
                item_code = storage.catalog_item_code_for_name(name)
                if item_code:
                    row = storage.catalog_price(item_code)
            if row and row.get("price") is not None:
                return float(row["price"])
            # Shufersal is also on the portal feed for some products.
            if code:
                sp = storage.latest_store_price(store, code)
                if sp and sp.get("price") is not None:
                    return float(sp["price"])
            return None
        if code:
            sp = storage.latest_store_price(store, code)
            if sp and sp.get("price") is not None:
                return float(sp["price"])
        return None
    except Exception:  # noqa: BLE001
        logger.debug("price lookup failed %s %s", store, code, exc_info=True)
        return None


def cheaper_alternatives(storage, items: list[dict], config: VNextConfig = DEFAULT,
                         limit: int = 40) -> list[dict]:
    """Per included item, a materially cheaper comparable product on the
    Shufersal catalogue (the existing per-unit comparison in
    `catalog.find_cheaper_equivalents`, not a new one)."""
    from .catalog import find_cheaper_equivalents

    out: list[dict] = []
    for item in [i for i in items if i.get("included")][:limit]:
        reference_name = ((item.get("products") or {}).get("shufersal") or {}).get("product_name", "")
        if not reference_name:
            continue
        try:
            reference, cheaper = find_cheaper_equivalents(storage, item["term"], 12, reference_name)
        except Exception:  # noqa: BLE001
            logger.debug("cheaper lookup failed for %r", item["term"], exc_info=True)
            continue
        if reference is None or not cheaper:
            continue
        product, deal, fraction = cheaper[0]
        saving = 0.0
        try:
            saving = max(0.0, float(reference.price) - float(deal.discounted_price if deal else product.price))
        except Exception:  # noqa: BLE001
            saving = 0.0
        out.append({"key": item["key"], "store": "shufersal", "product_code": product.item_code,
                    "product_name": product.name, "saving": round(saving, 2),
                    "fraction": round(float(fraction), 2), "accepted": False})
    return out


# -- quoting and the split decision ---------------------------------------------------------

def quotes_for(items: list[dict], chains: dict, config: VNextConfig = DEFAULT) -> dict:
    """Per-chain baskets for the included items plus the assignment and a
    recommendation. Pure: works on the prices already on the items."""
    econ = chain_economics(config)
    enabled = [s for s in CHAINS if chains.get(s, False)]
    included = [i for i in items if i.get("included") and not i.get("removed")]
    per_chain: dict = {}
    for store in enabled:
        lines, missing, promos, subtotal = [], [], 0, 0.0
        for item in included:
            price = (item.get("prices") or {}).get(store)
            prod = (item.get("products") or {}).get(store)
            if price is None:
                missing.append(item["display_name"])
                continue
            qty = float(item.get("quantity") or 1)
            promo = bool((item.get("promo") or {}).get("store") == store)
            line_total = round(price * qty, 2)
            lines.append({"key": item["key"], "name": (prod or {}).get("product_name") or item["display_name"],
                          "price": line_total, "unit_price": price, "promo": promo})
            subtotal += line_total
            promos += int(promo)
        fee = econ[store].delivery_fee or 0.0
        per_chain[store] = {"items": len(lines), "subtotal": round(subtotal, 2), "delivery": fee,
                            "total": round(subtotal + fee, 2) if lines else 0.0, "promos": promos,
                            "missing": missing, "lines": lines,
                            "coverage": (len(lines) / len(included)) if included else 0.0}

    assignment: dict = {}
    recommendation: dict = {}
    if not included or not enabled:
        return {"chains": per_chain, "assignment": assignment, "recommendation": recommendation}

    # Single-chain option: everything one chain can quote + the rest by
    # name search there (unpriced), one delivery.
    single: dict = {}
    for store in enabled:
        q = per_chain[store]
        single[store] = {"total": q["total"], "coverage": q["coverage"], "unpriced": len(q["missing"])}

    if len(enabled) == 1:
        store = enabled[0]
        assignment = {i["key"]: store for i in included}
        recommendation = {"single": store, "total": single[store]["total"],
                          "note": _threshold_note(store, per_chain[store]["subtotal"])}
        return {"chains": per_chain, "assignment": assignment, "recommendation": recommendation}

    # Split option: each item to its cheaper quoted chain; unquotable
    # items follow the chain that has the most of the basket.
    split_assign: dict = {}
    split_sub: dict = {s: 0.0 for s in enabled}
    for item in included:
        prices = {s: (item.get("prices") or {}).get(s) for s in enabled}
        priced = {s: p for s, p in prices.items() if p is not None}
        if not priced:
            continue
        best = min(priced, key=priced.get)
        split_assign[item["key"]] = best
        split_sub[best] += priced[best] * float(item.get("quantity") or 1)
    used = [s for s in enabled if any(v == s for v in split_assign.values())]
    anchor = max(used, key=lambda s: split_sub[s]) if used else enabled[0]
    for item in included:
        split_assign.setdefault(item["key"], anchor)
    split_total = sum(split_sub[s] + (econ[s].delivery_fee or 0.0) for s in used) if used else None
    # Compare like with like: the single-chain total for the chain that
    # quotes everything the split quotes.
    best_single = min(enabled, key=lambda s: (single[s]["unpriced"], single[s]["total"]))
    single_total = single[best_single]["total"]
    saving = round(single_total - split_total, 2) if split_total is not None and single_total else 0.0
    if len(used) > 1 and saving >= config.split_min_saving:
        assignment = split_assign
        recommendation = {"split": {s: sum(1 for v in split_assign.values() if v == s) for s in used},
                          "saving": saving, "total": round(split_total, 2),
                          "note": _threshold_note("shufersal", split_sub.get("shufersal", 0.0))}
    else:
        assignment = {i["key"]: best_single for i in included}
        other = [s for s in enabled if s != best_single]
        recommendation = {"single": best_single, "total": single_total,
                          "saving_vs_other": round(single[other[0]]["total"] - single_total, 2) if other and single[other[0]]["total"] else None,
                          "note": _threshold_note(best_single, per_chain[best_single]["subtotal"])}
        if len(used) > 1 and 0 < saving < config.split_min_saving:
            recommendation["note"] = (recommendation["note"] + "\n" if recommendation["note"] else "") + \
                f"פיצול היה חוסך רק {saving:.0f}₪ — לא שווה משלוח כפול"
    return {"chains": per_chain, "assignment": assignment, "recommendation": recommendation}


def _threshold_note(store: str, subtotal: float) -> str:
    if store != "shufersal" or not subtotal:
        return ""
    from .threshold import DEFAULT_GIFT_THRESHOLD

    gap = DEFAULT_GIFT_THRESHOLD - subtotal
    if 0 < gap <= 60:
        return f"עוד {gap:.0f}₪ בשופרסל ומגיעים למתנה של ₪{DEFAULT_GIFT_THRESHOLD:.0f}"
    return ""


def optimize(inputs: OptimizerInput) -> OptimizerResult:
    """Phase 2b: a real decision from the draft-shaped items in
    `inputs.quotes` (built by `quotes_for`). Read-only; the assignment is
    advice the flow may follow, never a cart action."""
    plan = inputs.plan
    items = [{"key": i.term, "display_name": i.display_name, "quantity": i.quantity, "included": True,
              "products": {}, "prices": {}, "promo": {}} for i in plan.auto_items]
    for q in inputs.quotes:
        for line in q.lines:
            for it in items:
                if it["key"] == line.term:
                    it["prices"][q.store] = line.promo_price if line.promo_price is not None else line.sticker_price
                    it["products"][q.store] = {"product_code": line.product_code, "product_name": line.product_name}
    chains = {c.store: True for c in inputs.chains}
    config = DEFAULT
    result = quotes_for(items, chains, config)
    rec = result["recommendation"]
    split = {}
    for key, store in result["assignment"].items():
        split.setdefault(store, []).append(key)
    return OptimizerResult(
        implemented=True,
        recommended_split=split,
        expected_total=rec.get("total"),
        expected_saving_vs_single_chain=rec.get("saving") if rec.get("split") else 0.0,
        reasons=tuple(x for x in (rec.get("note"),) if x) or ("single chain" if rec.get("single") else "no quotes",),
        inputs_summary={"plan_items": len(plan.items), "chains": list(chains), "quotes": len(inputs.quotes),
                        "split_penalty": inputs.split_penalty},
    )
