"""Normalised evidence about what the household needs — vNext Phase 1.

Every signal the need engine consults is turned into one `Evidence` row
here, carrying its `origin`:

- FACT           — something a system observed (an order line, a price).
- HUMAN_DECLARED — something a person said (a request, a rejection, a
                   meal, a waste report).
- INFERENCE      — something Gordon derived (a cadence, a "probably low",
                   a likely product for a spoken term).

The distinction is the contract with the rest of vNext: an INFERENCE is
never persisted anywhere, and a HUMAN_DECLARED signal outranks any
amount of inference about the same item. The readers below only read
existing tables through `Storage`'s public accessors; nothing here writes.

Known limits carried explicitly rather than hidden (see the 2026-09-20
UX audit): a cart read is not trusted as inventory (`trust` < 1 and
`data["cart_derived"]`), `adhoc_requests.consumed` is not treated as
proof an item reached a cart, and `preferred_products` rows whose source
is not "human" are INFERENCE-grade, never human preference.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum

from .storage import normalize_term
from .vnext_config import DEFAULT, VNextConfig

CART_CAPABLE = ("shufersal", "tivtaam")


class Origin(str, Enum):
    FACT = "FACT"
    HUMAN_DECLARED = "HUMAN_DECLARED"
    INFERENCE = "INFERENCE"


class EvidenceType(str, Enum):
    explicit_need = "explicit_need"
    explicit_rejection = "explicit_rejection"
    explicit_preference = "explicit_preference"
    purchase_history = "purchase_history"
    purchase_recency = "purchase_recency"
    purchase_cadence = "purchase_cadence"
    observed_quantity = "observed_quantity"
    planned_meal = "planned_meal"
    household_event = "household_event"
    waste_report = "waste_report"
    promotion = "promotion"
    price_history = "price_history"
    external_spend_signal = "external_spend_signal"


@dataclass(frozen=True)
class Evidence:
    type: EvidenceType
    origin: Origin
    term: str                       # normalised need key
    store: str = ""
    product_code: str = ""
    product_name: str = ""
    value: float | None = None      # the numeric payload, meaning depends on type
    detail: str = ""                # one human-readable line
    observed_at: str = ""           # YYYY-MM-DD or ISO
    source_ref: str = ""            # table:key, so a reason can be traced
    trust: float = 1.0              # 0..1, lowered for thin or unreliable sources
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "origin": self.origin.value,
            "term": self.term,
            "store": self.store,
            "product_code": self.product_code,
            "product_name": self.product_name,
            "value": self.value,
            "detail": self.detail,
            "observed_at": self.observed_at,
            "source_ref": self.source_ref,
            "trust": round(self.trust, 3),
            "data": self.data,
        }


@dataclass
class EvidenceSet:
    items: list[Evidence] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    gathered_at: str = ""

    def by_term(self) -> dict[str, list[Evidence]]:
        grouped: dict[str, list[Evidence]] = defaultdict(list)
        for item in self.items:
            grouped[item.term].append(item)
        return dict(grouped)

    def of_type(self, kind: EvidenceType) -> list[Evidence]:
        return [i for i in self.items if i.type == kind]

    def counts(self) -> dict:
        out: dict = defaultdict(int)
        for item in self.items:
            out[item.type.value] += 1
        return dict(out)


def key_for(name: str) -> str:
    return normalize_term(name)


def _today(today: date | None) -> date:
    return today or date.today()


def _parse_day(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:19]).date()
    except ValueError:
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


# -- readers ----------------------------------------------------------------

def explicit_needs(storage) -> list[Evidence]:
    """Pending ad-hoc requests. `consumed=0` means "still on the list"; it
    is NOT evidence about the cart either way (audit gap #2)."""
    out = []
    for req in storage.list_pending_adhoc():
        out.append(Evidence(
            type=EvidenceType.explicit_need, origin=Origin.HUMAN_DECLARED,
            term=key_for(req.text), value=float(req.quantity or 1),
            detail=f"{req.requested_by or 'household'} asked for it",
            observed_at=str(req.created_at or "")[:10],
            source_ref=f"adhoc_requests:{req.id}",
            data={"raw": req.text, "requested_by": req.requested_by, "amount": req.amount,
                  "unit": req.unit, "brand": req.brand, "cart_presence_unknown": True},
        ))
    return out


def standing_list(storage) -> list[Evidence]:
    out = []
    for item in storage.list_active_base_items():
        out.append(Evidence(
            type=EvidenceType.explicit_preference, origin=Origin.HUMAN_DECLARED,
            term=key_for(item.name), value=float(item.default_quantity or 1),
            product_name=item.name, detail="on the household's standing list",
            source_ref=f"base_list_items:{item.id}",
            data={"standing": True, "brand": item.brand, "amount": item.amount,
                  "unit": item.unit, "tags": list(item.tags or [])},
        ))
    return out


def rejections(storage) -> list[Evidence]:
    """Only `source='human'` rows. `order_removed` is reserved in the
    schema and never written today, so there is nothing else to read."""
    out = []
    for row in storage.list_rejections():
        if row.get("source") != "human":
            continue
        out.append(Evidence(
            type=EvidenceType.explicit_rejection, origin=Origin.HUMAN_DECLARED,
            term=key_for(row["term"]), store=row["store"],
            product_code=str(row["product_code"]), product_name=row.get("product_name") or "",
            detail="household said: not this product for this term",
            observed_at=str(row.get("rejected_at") or "")[:10],
            source_ref=f"product_rejections:{row['store']}:{row['term']}:{row['product_code']}",
        ))
    return out


def preferences(storage) -> list[Evidence]:
    """Human-confirmed product choices are HUMAN_DECLARED; everything the
    resolver wrote on its own (`purchase`/`inferred`/`search`) is INFERENCE
    and is labelled so — it is never promoted to a human preference here
    (audit gap: autoresolve turning weak inference durable)."""
    out = []
    for row in storage.list_preferences():
        human = row.get("source") == "human"
        out.append(Evidence(
            type=EvidenceType.explicit_preference,
            origin=Origin.HUMAN_DECLARED if human else Origin.INFERENCE,
            term=key_for(row["term"]), store=row["store"],
            product_code=str(row["product_code"]), product_name=row.get("product_name") or "",
            detail=("household chose this product" if human
                    else f"resolver remembered this product (source={row.get('source')})"),
            source_ref=f"preferred_products:{row['store']}:{row['term']}",
            trust=1.0 if human else 0.5,
            data={"source": row.get("source"), "evidence_count": row.get("evidence_count")},
        ))
    return out


def _tivtaam_line_dates(storage) -> dict[str, list[tuple[date, float | None, str]]]:
    """product_code -> [(order_date, actual_or_ordered_qty, unit)] oldest first."""
    by_code: dict[str, list[tuple[date, float | None, str]]] = defaultdict(list)
    for line in storage.tivtaam_purchase_lines("tivtaam"):
        day = _parse_day(line.get("order_date"))
        if day is None:
            continue
        qty = line.get("actual_quantity")
        if qty is None:
            qty = line.get("ordered_quantity")
        unit = line.get("unit") or ("ק\"ג" if line.get("weightable") else "")
        by_code[str(line.get("product_code"))].append((day, float(qty) if qty is not None else None, unit))
    for code in by_code:
        by_code[code].sort(key=lambda t: t[0])
    return by_code


def purchase_evidence(storage, config: VNextConfig = DEFAULT, today: date | None = None) -> list[Evidence]:
    """History, recency, cadence and observed quantity per recurring product.

    Cadence is measured from real order dates where there are at least
    `min_cadence_observations` (Tiv Taam order lines today); otherwise
    estimated as household_gap / share with lower trust; otherwise the
    household's stated gap with the lowest trust. All three are INFERENCE.
    """
    from . import learn

    now = _today(today)
    household_gap = float(learn.typical_gap_days(storage, learn.ALL_CHAINS))
    tiv_lines = _tivtaam_line_dates(storage)
    out: list[Evidence] = []
    for store in CART_CAPABLE:
        last = storage.last_purchase_dates(store)
        for row in storage.list_stock_items(store):
            code = str(row.get("product_code") or "")
            name = row.get("product_name") or ""
            term = key_for(name)
            share = float(row.get("share") or 0)
            tier = row.get("tier") or "D"
            base = dict(store=store, product_code=code, product_name=name)

            out.append(Evidence(
                type=EvidenceType.purchase_history, origin=Origin.FACT, term=term,
                value=share, detail=f"tier {tier}, in {share:.0%} of {store} orders",
                source_ref=f"stock_items:{store}:{code}", trust=1.0,
                data={"tier": tier, "department": row.get("department") or "",
                      "picked_count": row.get("picked_count"), "skipped_count": row.get("skipped_count"),
                      "barcode": row.get("barcode")},
                **base,
            ))

            dates = [d for d, _, _ in tiv_lines.get(code, [])] if store == "tivtaam" else []
            last_day = last.get(code)
            if dates and (last_day is None or dates[-1] > last_day):
                last_day = dates[-1]
            if last_day is not None:
                out.append(Evidence(
                    type=EvidenceType.purchase_recency, origin=Origin.FACT, term=term,
                    value=float((now - last_day).days), detail=f"last bought {last_day.isoformat()}",
                    observed_at=last_day.isoformat(), source_ref=f"last_purchase:{store}:{code}",
                    **base,
                ))

            cadence, trust, how = _cadence(dates, share, household_gap, row.get("interval_days"), config)
            if cadence is not None:
                out.append(Evidence(
                    type=EvidenceType.purchase_cadence, origin=Origin.INFERENCE, term=term,
                    value=cadence, detail=f"~every {cadence:.0f} days ({how})",
                    source_ref=f"derived:{store}:{code}", trust=trust,
                    data={"method": how, "observations": len(dates)},
                    **base,
                ))

            qty = row.get("default_quantity")
            if store == "tivtaam" and tiv_lines.get(code):
                observed = [q for _, q, _ in tiv_lines[code] if q]
                unit = next((u for _, _, u in tiv_lines[code] if u), "")
                if observed:
                    qty = round(statistics.median(observed), 2)
                    out.append(Evidence(
                        type=EvidenceType.observed_quantity, origin=Origin.FACT, term=term,
                        value=float(qty), detail=f"median of {len(observed)} real order lines"
                                                 + (f" ({unit})" if unit else ""),
                        source_ref=f"tivtaam_order_lines:{code}", trust=min(1.0, 0.5 + 0.1 * len(observed)),
                        data={"observations": len(observed), "unit": unit}, **base,
                    ))
                    continue
            if qty:
                out.append(Evidence(
                    type=EvidenceType.observed_quantity, origin=Origin.FACT, term=term,
                    value=float(qty), detail="stock_items.default_quantity (median of past orders)",
                    source_ref=f"stock_items:{store}:{code}", trust=0.6, **base,
                ))
    return out


def _cadence(dates: list[date], share: float, household_gap: float, measured, config: VNextConfig):
    if measured:
        return float(measured), config.cadence_trust_measured, "chain-measured interval"
    if len(dates) >= config.min_cadence_observations:
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days >= 1]
        if gaps:
            trust = min(config.cadence_trust_measured, 0.5 + 0.07 * len(gaps))
            return float(statistics.median(gaps)), trust, f"median of {len(gaps)} real gaps"
    if 0 < share <= 1:
        return round(household_gap / share, 1), config.cadence_trust_share_estimate, "household gap / share"
    return household_gap, config.cadence_trust_household_fallback, "household gap"


def waste(storage) -> list[Evidence]:
    out = []
    summary = storage.waste_summary()
    for name, (reports, total) in summary.items():
        out.append(Evidence(
            type=EvidenceType.waste_report, origin=Origin.HUMAN_DECLARED, term=key_for(name),
            product_name=name, value=float(total) / reports if reports else 0.0,
            detail=f"{reports} waste report(s), avg fraction {total / reports:.0%}" if reports else "",
            source_ref=f"waste_reports:{name}", data={"reports": reports, "total_fraction": total},
        ))
    return out


_PROMO_CACHE: dict = {}


def promotions(storage, config: VNextConfig = DEFAULT) -> list[Evidence]:
    """Promotions on things the household buys, from the existing pickers.

    `dealfill.picks_for` is a pure read and already applies the
    multi-buy-arithmetic guard (2026-09-11 incident). `hotdeals.find` is
    also used, filtered by `store` here because `_promotion_deals()`
    ignores its `chains` argument (known bug, hotdeals.py).

    Phase 2a: cached per process for `config.promotions_cache_ttl_seconds`.
    The pickers cost ~13 s (283 `fold()`-UDF searches) and the feeds they
    read change once a day, so a `/plan` right after a `/readiness`
    should not pay for them twice. Keyed by DB path so tests never share.
    """
    import time

    key = (getattr(storage, "_db_path", "") or repr(storage), config.promotions_cache_ttl_seconds)
    hit = _PROMO_CACHE.get(key)
    if hit is not None and time.monotonic() - hit[0] <= config.promotions_cache_ttl_seconds:
        return list(hit[1])
    out = _promotions_uncached(storage, config)
    _PROMO_CACHE[key] = (time.monotonic(), list(out))
    return out


def _promotions_uncached(storage, config: VNextConfig) -> list[Evidence]:
    from . import dealfill, hotdeals

    out: list[Evidence] = []
    for store in CART_CAPABLE:
        try:
            picks = dealfill.picks_for(storage, store, pantryable_only=False)
        except Exception as exc:  # noqa: BLE001 - a feed hiccup must not sink the plan
            out.append(Evidence(type=EvidenceType.promotion, origin=Origin.FACT, term="",
                                store=store, detail=f"promotion read failed: {exc}", trust=0.0))
            continue
        for pick in picks:
            saving = (pick.shelf_price - pick.deal_price) * max(1, pick.quantity)
            readable = pick.quantity >= 1 and pick.discount > 0
            out.append(Evidence(
                type=EvidenceType.promotion, origin=Origin.FACT, term=key_for(pick.term),
                store=store, product_name=pick.catalog_name, value=float(pick.discount),
                detail=pick.label, source_ref=f"dealfill:{store}:{pick.term}",
                trust=config.promotion_trust_readable if readable else config.promotion_trust_conditional,
                data={"shelf_price": pick.shelf_price, "deal_price": pick.deal_price,
                      "quantity": pick.quantity, "saving": round(saving, 2),
                      "familiar": pick.familiar, "description": pick.description},
            ))
    try:
        relevant, _exceptional = hotdeals.find(storage)
    except Exception:  # noqa: BLE001
        relevant = []
    for deal in relevant:
        if deal.chain not in CART_CAPABLE:
            continue
        out.append(Evidence(
            type=EvidenceType.promotion, origin=Origin.FACT, term=key_for(deal.name),
            store=deal.chain, product_name=deal.name, value=float(deal.discount),
            detail=f"{deal.reason}: {deal.price:.2f}₪ vs {deal.reference_price:.2f}₪",
            source_ref=f"hotdeals:{deal.chain}:{deal.barcode}", trust=config.promotion_trust_readable,
            data={"shelf_price": deal.reference_price, "deal_price": deal.price, "quantity": 1,
                  "saving": deal.saving, "familiar": deal.bought_often, "barcode": deal.barcode,
                  "stockable": deal.stockable},
        ))
    return out


def price_history(storage, item_codes: list[str]) -> list[Evidence]:
    """How today's Shufersal price sits against its own recorded past."""
    out = []
    for code in item_codes:
        stats = storage.price_stats(code)
        if not stats:
            continue
        out.append(Evidence(
            type=EvidenceType.price_history, origin=Origin.FACT, term="", store="shufersal",
            product_code=code, value=float(stats["avg"] or 0),
            detail=f"{stats['days']} days recorded, best {stats['best']:.2f}, promo share {stats['promo_share']:.0%}",
            source_ref=f"price_history:{code}", data=stats,
        ))
    return out


def planned_meals(storage, config: VNextConfig = DEFAULT, today: date | None = None) -> list[Evidence]:
    """A declared meal is HUMAN_DECLARED; each ingredient need derived from
    it is INFERENCE with `known_inventory=False` — Gordon does not know the
    pantry, it only guesses via `pantry.likely_have`."""
    from . import pantry

    now = _today(today)
    horizon = now + timedelta(days=config.meal_lookahead_days)
    out: list[Evidence] = []
    for meal in storage.list_vnext_planned_meals(now.isoformat(), horizon.isoformat()):
        out.append(Evidence(
            type=EvidenceType.planned_meal, origin=Origin.HUMAN_DECLARED, term=key_for(meal["meal"]),
            value=float(meal.get("servings") or 0), detail=f"{meal['meal']} on {meal['on_date']}",
            observed_at=meal["on_date"], source_ref=f"vnext_planned_meals:{meal['id']}",
            data={"meal": meal["meal"], "on_date": meal["on_date"], "is_meal": True,
                  "ingredients": list(meal.get("ingredients") or [])},
        ))
        for ingredient in meal.get("ingredients") or []:
            likely = bool(pantry.likely_have(storage, ingredient))
            out.append(Evidence(
                type=EvidenceType.planned_meal, origin=Origin.INFERENCE, term=key_for(ingredient),
                product_name=ingredient, value=1.0,
                detail=f"needed for {meal['meal']} ({meal['on_date']})"
                       + (" — probably already at home" if likely else ""),
                observed_at=meal["on_date"], source_ref=f"vnext_planned_meals:{meal['id']}",
                trust=0.6, data={"meal": meal["meal"], "on_date": meal["on_date"],
                                 "known_inventory": False, "likely_have": likely},
            ))
    return out


def household_events(storage, config: VNextConfig = DEFAULT, today: date | None = None) -> list[Evidence]:
    now = _today(today)
    horizon = now + timedelta(days=config.meal_lookahead_days)
    out = []
    for event in storage.list_vnext_household_events(now.isoformat(), horizon.isoformat()):
        out.append(Evidence(
            type=EvidenceType.household_event, origin=Origin.HUMAN_DECLARED, term=key_for(event["name"]),
            value=float(event.get("extra_people") or 0), detail=f"{event['name']} on {event['on_date']}",
            observed_at=event["on_date"], source_ref=f"vnext_household_events:{event['id']}",
            data={"note": event.get("note") or "", "on_date": event["on_date"]},
        ))
    return out


def external_spend(storage) -> list[Evidence]:
    """No source exists yet (Phase 1). The evidence type is defined so a
    later budget-project feed can slot in without changing the engines."""
    return []


def stockup_rules(storage) -> list[dict]:
    return storage.list_vnext_stockup_rules()


def gather(storage, config: VNextConfig = DEFAULT, today: date | None = None) -> EvidenceSet:
    """Everything the need engine consults, in one read-only pass."""
    evidence = EvidenceSet(gathered_at=datetime.now().isoformat(timespec="seconds"))
    evidence.items += explicit_needs(storage)
    evidence.items += standing_list(storage)
    evidence.items += rejections(storage)
    evidence.items += preferences(storage)
    evidence.items += purchase_evidence(storage, config, today)
    evidence.items += waste(storage)
    evidence.items += promotions(storage, config)
    evidence.items += planned_meals(storage, config, today)
    evidence.items += household_events(storage, config, today)
    evidence.items += external_spend(storage)

    if not evidence.of_type(EvidenceType.waste_report):
        evidence.caveats.append("no waste reports on record — waste dampening is inactive")
    if not [e for e in evidence.of_type(EvidenceType.explicit_preference) if e.origin == Origin.HUMAN_DECLARED and e.product_code]:
        evidence.caveats.append("no human-confirmed product preferences — every product choice is inference")
    if not evidence.of_type(EvidenceType.planned_meal):
        evidence.caveats.append("no planned meals declared — meal demand is inactive")
    evidence.caveats.append("live cart contents are NOT read in shadow mode; 'already in cart' is unknown for every item")
    evidence.caveats.append("no external spend signal source exists yet")
    return evidence
