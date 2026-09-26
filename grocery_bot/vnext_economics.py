"""Is a promotion actually worth stocking up on? — vNext Phase 1.5.

Phase 1 called anything ≥25% off on a recurring item a stock-up
candidate, which put fresh peppers and a diaper brand nobody buys in
the list. A discount percentage is one number out of the several that
decide whether buying more now saves money:

- the price itself against its own recorded past (`price_history` for
  Shufersal, `store_prices` snapshots for Tiv Taam) — an item that sits
  at the "promo" price half the time is routinely cheap, not on sale,
  and a reference price well above the recent median is inflated;
- how much of it the household will use before it spoils, from the
  measured cadence and the usual quantity, under a horizon that is a
  week for perishables and two months for shelf-stable goods;
- what was bought recently — a unit already in the house is a unit not
  to buy;
- the absolute money saved on the units it makes sense to buy, not the
  percentage on one.

Everything here is read-only and returns a `StockUpAssessment` with
`worthwhile` plus the arithmetic behind it. Source freshness per chain
is reported, not assumed: see `price_source_freshness`.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .household_evidence import Evidence, EvidenceType
from .vnext_config import DEFAULT, VNextConfig
from .vnext_semantics import BAKERY, DAIRY, MEAT_FISH, PRODUCE, category_of, forms_of


@dataclass
class StockUpAssessment:
    term: str
    store: str
    product_name: str
    worthwhile: bool
    recommended_units: int
    reasons: list[str] = field(default_factory=list)
    economics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"term": self.term, "store": self.store, "product_name": self.product_name,
                "worthwhile": self.worthwhile, "recommended_units": self.recommended_units,
                "reasons": self.reasons, "economics": self.economics}


def is_perishable(name: str) -> bool:
    cat = category_of(name)
    forms = forms_of(name)
    if "frozen" in forms or "canned" in forms or "dried" in forms or "pickled" in forms:
        return False
    return cat in (PRODUCE, DAIRY, BAKERY, MEAT_FISH)


def _price_series(storage, store: str, product_name: str, barcode: str | None, days: int) -> tuple[list[float], list[float | None], str]:
    """(prices, promo_prices, source) over the lookback, oldest first."""
    if store == "shufersal":
        code = storage.catalog_item_code_for_name(product_name)
        if code:
            rows = storage.price_history_series(code, days)
            return ([float(r["price"]) for r in rows], [r.get("promo_price") for r in rows], f"price_history:{code}")
        return [], [], "price_history:none"
    key = barcode
    if not key:
        hits = storage.search_store_price_names(store, product_name, limit=1)
        key = hits[0]["barcode"] if hits and hits[0]["name"] == product_name else None
    if key:
        rows = storage.store_price_series(store, key, days)
        return [float(r["price"]) for r in rows], [None] * len(rows), f"store_prices:{store}:{key}"
    return [], [], f"store_prices:{store}:none"


def price_reference(storage, store: str, name: str, barcode: str | None,
                    shelf: float, deal: float, config: VNextConfig = DEFAULT) -> dict:
    """The history half of `assess`: is `deal` really cheap for this product?

    Shared with `dealfill` (Basics in Order §3, 26.09.2026: the deals the
    bot puts in the cart by itself are judged against the 90-day median,
    not today's shelf price) so the two cannot drift apart.
    """
    prices, _promos, source = _price_series(storage, store, name, barcode, config.economics_lookback_days)
    history_days = len(prices)
    median = statistics.median(prices) if prices else None
    best = min(prices) if prices else None
    at_or_below = sum(1 for p in prices if deal and p <= deal * 1.02)
    routine_share = (at_or_below / history_days) if history_days else None
    inflated = bool(median and shelf and shelf > median * config.economics_inflated_reference_factor)
    # The shelf price is the reference unless it is inflated above the
    # median, in which case the median is. This read `not inflated` until
    # 26.09.2026 — the exact inverse of the reason text in `assess` and of
    # the Phase 1.5 report — so an inflated shelf was *kept* as the
    # reference and manufactured the discount it was meant to discard.
    reference = median if (median and inflated) else shelf
    per_unit_saving = max(0.0, (reference or 0) - deal) if deal else 0.0
    return {
        "source": source,
        "history_days": history_days,
        "median": median,
        "best": best,
        "routine_share": routine_share,
        "routine": bool(routine_share is not None and routine_share >= config.economics_routine_promo_share),
        "inflated": inflated,
        "unusual": bool(best is not None and deal and deal <= best * 1.02
                        and history_days >= config.economics_min_history_days),
        "reference": reference,
        "per_unit_saving": per_unit_saving,
        "real_discount": (per_unit_saving / reference) if reference else 0.0,
    }


def assess(storage, term: str, promo: Evidence, evidence: list[Evidence],
           config: VNextConfig = DEFAULT, rule: dict | None = None) -> StockUpAssessment:
    d = promo.data or {}
    shelf = float(d.get("shelf_price") or 0)
    deal = float(d.get("deal_price") or 0)
    discount = float(promo.value or 0)
    name = promo.product_name or term
    store = promo.store
    reasons: list[str] = []

    ref = price_reference(storage, store, name, d.get("barcode"), shelf, deal, config)
    source, history_days, median = ref["source"], ref["history_days"], ref["median"]
    best, routine_share, inflated = ref["best"], ref["routine_share"], ref["inflated"]
    unusual, reference = ref["unusual"], ref["reference"]
    if inflated:
        reasons.append(f"reference {shelf:.2f}₪ is above the {config.economics_lookback_days}d median {median:.2f}₪ — inflated; using the median")
    if routine_share is not None and routine_share >= config.economics_routine_promo_share:
        reasons.append(f"routine price: at or below {deal:.2f}₪ on {routine_share:.0%} of {history_days} recorded days")
    if history_days < config.economics_min_history_days:
        reasons.append(f"only {history_days} recorded price days — cannot call this unusual")

    per_unit_saving = ref["per_unit_saving"]
    real_discount = ref["real_discount"]

    cadence = max((e for e in evidence if e.type == EvidenceType.purchase_cadence), key=lambda e: e.trust, default=None)
    recency = min((e for e in evidence if e.type == EvidenceType.purchase_recency), key=lambda e: e.value or 0, default=None)
    qty = max((e for e in evidence if e.type == EvidenceType.observed_quantity), key=lambda e: e.trust, default=None)
    per_purchase = float(qty.value) if (qty and qty.value) else 1.0
    perishable = is_perishable(name)
    horizon = config.economics_perishable_horizon_days if perishable else config.economics_stockable_horizon_days
    if cadence and cadence.value:
        consumable = horizon / float(cadence.value) * per_purchase
        cadence_note = f"~{cadence.value:.0f}d cadence ({cadence.data.get('method', '?')})"
    else:
        consumable = per_purchase if not perishable else 0.0
        cadence_note = "no cadence — one purchase's worth at most"
    recent_supply = 0.0
    if cadence and cadence.value and recency and recency.value is not None and recency.value < float(cadence.value) * config.recent_factor:
        recent_supply = per_purchase
        reasons.append(f"bought {recency.value:.0f}d ago — one purchase's worth already at home")
    cap = int(rule["max_quantity"]) if rule else config.economics_max_units
    units = int(round(consumable - recent_supply))
    units = max(0, min(cap, units))
    need_qty = int(d.get("quantity") or 1)
    if units < need_qty:
        reasons.append(f"promotion needs {need_qty}, household would use {units} before spoilage")
    total_saving = per_unit_saving * units
    bar = float(rule["min_discount"]) if rule else config.stockup_min_discount

    worthwhile = (
        units >= 2
        and real_discount >= bar
        and total_saving >= config.economics_min_absolute_saving
        and not (routine_share is not None and routine_share >= config.economics_routine_promo_share)
        and (not perishable or bool(rule))
        and promo.trust >= config.promotion_trust_readable
    )
    if perishable and not rule:
        reasons.append("perishable — stocking up would be waste, not saving")
    if worthwhile:
        reasons.append(f"{units} units at {deal:.2f}₪ vs {reference:.2f}₪ reference saves {total_saving:.2f}₪ ({real_discount:.0%})"
                       + (" — unusually low price" if unusual else ""))
    elif units >= 2 and real_discount < bar:
        reasons.append(f"real discount {real_discount:.0%} vs reference is below the {bar:.0%} bar")
    elif units >= 2 and total_saving < config.economics_min_absolute_saving:
        reasons.append(f"absolute saving {total_saving:.2f}₪ on {units} units is below {config.economics_min_absolute_saving:.0f}₪")

    return StockUpAssessment(
        term=term, store=store, product_name=name, worthwhile=worthwhile, recommended_units=units if worthwhile else 0,
        reasons=reasons,
        economics={
            "shelf_price": shelf, "deal_price": deal, "feed_discount": round(discount, 3),
            "reference_price": round(reference, 2) if reference else None, "reference_inflated": inflated,
            "history_days": history_days, "history_median": round(median, 2) if median else None,
            "history_best": round(best, 2) if best is not None else None, "routine_promo_share": routine_share,
            "unusual_price": unusual, "real_discount": round(real_discount, 3),
            "per_unit_saving": round(per_unit_saving, 2), "units_consumable_before_spoilage": round(consumable, 1),
            "recent_supply_units": recent_supply, "perishable": perishable, "horizon_days": horizon,
            "cadence": cadence_note, "per_purchase_quantity": per_purchase, "total_saving": round(total_saving, 2),
            "price_source": source,
        },
    )


def price_source_freshness(storage) -> dict:
    """How current each chain's price memory is — reported, never assumed."""
    import sqlite3
    from contextlib import closing

    out: dict = {}
    with closing(storage._connect()) as conn:  # noqa: SLF001 - read-only aggregate on our own DB
        row = conn.execute("SELECT MIN(day) a, MAX(day) b, COUNT(DISTINCT day) n, COUNT(DISTINCT item_code) items FROM price_history").fetchone()
        out["shufersal"] = {"source": "price_history (daily snapshot of the transparency feed)",
                            "from": row["a"], "to": row["b"], "days_recorded": row["n"], "items": row["items"]}
        for store in ("tivtaam",):
            row = conn.execute(
                "SELECT MIN(observed_at) a, MAX(observed_at) b, COUNT(DISTINCT observed_at) n, COUNT(DISTINCT barcode) items "
                "FROM store_prices WHERE store = ?", (store,)).fetchone()
            recent = conn.execute(
                "SELECT COUNT(DISTINCT observed_at) n FROM store_prices WHERE store = ? AND observed_at >= date('now', '-30 days')",
                (store,)).fetchone()
            out[store] = {"source": "store_prices (portal feed snapshots + order prices)",
                          "from": row["a"], "to": row["b"], "days_recorded": row["n"], "items": row["items"],
                          "days_in_last_30": recent["n"]}
    return out
