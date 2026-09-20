"""Turn evidence into a per-item need assessment — vNext Phase 1.

For each candidate term the engine says how sure it is the household
needs it (`need_confidence`), how much (`recommended_quantity`, with its
own `quantity_confidence`), why (`reasons` — evidence refs), and what to
do about it (`decision`: auto_include / suggest / ignore, from the bands
in `vnext_config`).

Gordon does not know the pantry. A depletion estimate here is an
INFERENCE built from cadence and recency and is returned, never stored.
The rules the tests pin:

- an explicit need is HIGH whatever the depletion arithmetic says;
- a purchase inside cadence × recent_factor lowers confidence;
- a recurring item past its cadence becomes likely due;
- a waste report lowers the quantity and the quantity confidence;
- an explicit rejection removes that product as a candidate but leaves
  the need itself in place;
- a good promotion on a recurring/pantryable item makes a stock-up
  candidate; a mediocre one creates no demand at all;
- an inferred product choice stays on the assessment only.

Phase 1.5 added, on top:

- four confidences kept apart: need, quantity, product, retailer — a
  need can be certain while the SKU is unresolved, and that is never
  turned into a question about the need;
- cadence strata: a depletion estimate built on a *measured* cadence
  may reach HIGH, a *weak* one at most MEDIUM, a household-level
  *fallback* at most LOW+ — another evidence type can lift the item,
  cadence alone cannot (`vnext_config.cadence_ceiling_*`);
- the semantic resolver decides the product (`vnext_resolver`), and
  stock-up is decided by real economics (`vnext_economics`), not by a
  feed percentage;
- an exception class on every assessment: true_user_decision /
  agent_resolvable / quiet — and "cart state unknown" is a plan-level
  caveat, not an item-level decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .household_evidence import Evidence, EvidenceSet, EvidenceType, Origin, gather
from .vnext_config import DEFAULT, VNextConfig

TRUE_USER_DECISION = "true_user_decision"
AGENT_RESOLVABLE = "agent_resolvable"
QUIET = "quiet"

CADENCE_MEASURED = "measured"
CADENCE_WEAK = "weak"
CADENCE_FALLBACK = "household_fallback"

AUTO_INCLUDE = "auto_include"
SUGGEST = "suggest"
IGNORE = "ignore"

PANTRYABLE_DEPARTMENTS = {
    "טיפוח, תינוקות וניקיון", "מזווה ושימורים", "יבשים ובישול", "קפואים ומזון בסיסי",
}


@dataclass
class CandidateProduct:
    store: str
    product_code: str
    product_name: str
    origin: Origin              # HUMAN_DECLARED (human pref) or INFERENCE (resolver / history)
    source_ref: str = ""

    def to_dict(self) -> dict:
        return {"store": self.store, "product_code": self.product_code,
                "product_name": self.product_name, "origin": self.origin.value,
                "source_ref": self.source_ref}


@dataclass
class NeedAssessment:
    term: str
    need_confidence: float
    recommended_quantity: float
    quantity_confidence: float
    decision: str
    reasons: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    mandatory: bool = False
    stock_up: bool = False
    meal_or_event: str = ""
    inventory_known: bool = False
    candidate_products: list[CandidateProduct] = field(default_factory=list)
    rejected_products: list[str] = field(default_factory=list)
    stores: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    depletion: dict = field(default_factory=dict)   # the inference, made explicit
    display_name: str = ""
    unit: str = ""
    # Phase 1.5
    product_confidence: float = 0.0
    retailer_confidence: float = 0.0
    product_resolution: dict = field(default_factory=dict)
    exception_class: str = QUIET
    exception_reason: str = ""
    cadence_stratum: str = ""
    cadence_capped: bool = False      # depletion alone wanted more than its stratum allows
    stockup_assessment: dict = field(default_factory=dict)
    request_status_estimate: str = ""
    fulfillment_evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "display_name": self.display_name or self.term,
            "need_confidence": round(self.need_confidence, 3),
            "recommended_quantity": self.recommended_quantity,
            "unit": self.unit,
            "quantity_confidence": round(self.quantity_confidence, 3),
            "product_confidence": round(self.product_confidence, 3),
            "retailer_confidence": round(self.retailer_confidence, 3),
            "exception_class": self.exception_class,
            "exception_reason": self.exception_reason,
            "cadence_stratum": self.cadence_stratum,
            "cadence_capped": self.cadence_capped,
            "product_resolution": self.product_resolution,
            "stockup_assessment": self.stockup_assessment,
            "request_status_estimate": self.request_status_estimate,
            "fulfillment_evidence": self.fulfillment_evidence,
            "decision": self.decision,
            "mandatory": self.mandatory,
            "stock_up": self.stock_up,
            "meal_or_event": self.meal_or_event,
            "inventory_known": self.inventory_known,
            "reasons": self.reasons,
            "evidence_refs": self.evidence_refs,
            "candidate_products": [c.to_dict() for c in self.candidate_products],
            "rejected_products": self.rejected_products,
            "stores": self.stores,
            "unresolved": self.unresolved,
            "depletion": self.depletion,
        }


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def decide(confidence: float, config: VNextConfig) -> str:
    if confidence >= config.high_confidence:
        return AUTO_INCLUDE
    if confidence >= config.medium_confidence:
        return SUGGEST
    return IGNORE


def promos_present(by_type: dict) -> bool:
    return bool(by_type.get(EvidenceType.promotion))


def _tier_base(tier: str, config: VNextConfig) -> float:
    return {"A": config.tier_base_confidence_a, "B": config.tier_base_confidence_b,
            "C": config.tier_base_confidence_c}.get(tier or "", 0.0)


def cadence_stratum(cadence: Evidence | None, config: VNextConfig) -> str:
    """measured / weak / household_fallback, from how the cadence was got."""
    if cadence is None or cadence.value is None:
        return CADENCE_FALLBACK
    method = str(cadence.data.get("method", ""))
    observations = int(cadence.data.get("observations") or 0)
    if method.startswith("chain-measured") or method.startswith("median of"):
        return CADENCE_MEASURED if observations >= config.min_cadence_observations else CADENCE_WEAK
    if observations >= 2:
        return CADENCE_WEAK
    return CADENCE_FALLBACK


def cadence_ceiling(stratum: str, config: VNextConfig) -> float:
    return {CADENCE_MEASURED: config.cadence_ceiling_measured,
            CADENCE_WEAK: config.cadence_ceiling_weak}.get(stratum, config.cadence_ceiling_fallback)


def _depletion(cadence: Evidence | None, recency: Evidence | None, tier: str, config: VNextConfig) -> tuple[float, dict]:
    """Confidence that a recurring item has run low, and the inference itself."""
    if cadence is None or cadence.value is None:
        base = _tier_base(tier, config)
        return base, {"method": "tier-only", "note": "no cadence available"}
    if recency is None or recency.value is None:
        base = _tier_base(tier, config) * max(cadence.trust, 0.5)
        return base, {"method": "cadence-without-recency", "cadence_days": cadence.value}
    ratio = recency.value / cadence.value if cadence.value else 0.0
    if ratio < config.recent_factor:
        raw = 0.12 * (ratio / config.recent_factor if config.recent_factor else 1)
        state = "recently bought"
    elif ratio < config.due_factor:
        span = config.due_factor - config.recent_factor
        raw = 0.12 + (ratio - config.recent_factor) / span * (0.70 - 0.12)
        state = "approaching due"
    elif ratio < config.overdue_factor:
        span = config.overdue_factor - config.due_factor
        raw = 0.70 + (ratio - config.due_factor) / span * (0.92 - 0.70)
        state = "likely due"
    elif ratio < config.lapsed_factor:
        raw = 0.92
        state = "overdue"
    else:
        raw = config.lapsed_confidence
        state = "lapsed (probably no longer bought, or bought elsewhere)"
    # A cadence built from thin evidence cannot make Gordon very sure
    # either way: scale toward the tier prior instead of toward zero.
    prior = _tier_base(tier, config)
    confidence = prior + (raw - prior) * cadence.trust
    return _clamp(confidence), {
        "method": "cadence-vs-recency", "cadence_days": cadence.value, "days_since": recency.value,
        "ratio": round(ratio, 2), "state": state, "cadence_trust": round(cadence.trust, 2),
        "is_inference": True,
    }


def _display_name(term: str, evidence: list[Evidence]) -> str:
    """The household's own words first, then the standing-list name, then
    whatever the history calls it. A remembered product name must not
    replace what was asked for ("חלב עמיד" is not "חלב בקרטון 3%")."""
    for e in evidence:
        if e.type == EvidenceType.explicit_need and e.data.get("raw"):
            return str(e.data["raw"]).strip()
    for e in evidence:
        if e.type == EvidenceType.explicit_preference and e.data.get("standing") and e.product_name:
            return e.product_name
    for e in evidence:
        if e.type == EvidenceType.purchase_history and e.product_name:
            return e.product_name
    return next((e.product_name for e in evidence if e.product_name), "") or term


def assess_term(term: str, evidence: list[Evidence], config: VNextConfig = DEFAULT,
                stockup_rules: list[dict] | None = None, storage=None,
                purchases_by_code: dict | None = None,
                reconciliation: dict | None = None) -> NeedAssessment:
    """`storage` enables the semantic resolver and stock-up economics
    (both read-only); without it the assessment falls back to the Phase 1
    candidate list, which the tests for the need rules rely on."""
    by_type: dict[EvidenceType, list[Evidence]] = {}
    for item in evidence:
        by_type.setdefault(item.type, []).append(item)

    refs = [e.source_ref for e in evidence if e.source_ref]
    reasons: list[str] = []
    stores = sorted({e.store for e in evidence if e.store})
    display = _display_name(term, evidence)

    history = by_type.get(EvidenceType.purchase_history, [])
    tier = max((h.data.get("tier", "D") for h in history), default="", key=lambda t: {"A": 4, "B": 3, "C": 2, "D": 1}.get(t, 0))
    department = next((h.data.get("department", "") for h in history if h.data.get("department")), "")
    cadence = max(by_type.get(EvidenceType.purchase_cadence, []), key=lambda e: e.trust, default=None)
    recency = min(by_type.get(EvidenceType.purchase_recency, []), key=lambda e: e.value or 0, default=None)

    confidence, depletion = _depletion(cadence, recency, tier, config)
    stratum = cadence_stratum(cadence, config)
    ceiling = cadence_ceiling(stratum, config)
    capped = False
    if confidence > ceiling:
        # Depletion arithmetic on a thin cadence must not, by itself, put
        # an item in the cart. Other evidence below may still lift it.
        confidence, capped = ceiling, True
    depletion["cadence_stratum"] = stratum
    depletion["confidence_ceiling"] = ceiling
    if history:
        reasons.append(f"bought regularly (tier {tier})")
    if depletion.get("state"):
        reasons.append(f"{depletion['state']}: {depletion['days_since']:.0f}d since purchase vs ~{depletion['cadence_days']:.0f}d cadence"
                       + (f" [{stratum} cadence, capped at {ceiling:.2f}]" if capped else f" [{stratum} cadence]"))

    # quantity: observed median, else standing-list default, else 1
    observed = max(by_type.get(EvidenceType.observed_quantity, []), key=lambda e: e.trust, default=None)
    standing = [e for e in by_type.get(EvidenceType.explicit_preference, []) if e.data.get("standing")]
    unit = (observed.data.get("unit") if observed else "") or ""
    if observed and observed.value:
        quantity, qty_conf = float(observed.value), observed.trust
    elif standing and standing[0].value:
        quantity, qty_conf = float(standing[0].value), 0.6
    else:
        quantity, qty_conf = 1.0, 0.3

    mandatory = False
    if standing:
        confidence = max(confidence, config.standing_list_confidence)
        reasons.append("on the standing list")

    explicit = by_type.get(EvidenceType.explicit_need, [])
    request_status = ""
    fulfillment: list[dict] = []
    if explicit:
        recon = [reconciliation[e.source_ref] for e in explicit
                 if reconciliation and e.source_ref in reconciliation]
        statuses = {r["request_status_estimate"] for r in recon}
        if statuses and statuses <= {"likely_fulfilled"}:
            request_status = "likely_fulfilled"
        elif statuses and "active" in statuses:
            request_status = "active"
        elif statuses:
            request_status = "uncertain"
        else:
            request_status = "active"
        fulfillment = [ev for r in recon for ev in r.get("fulfillment_evidence", [])]
        if request_status == "likely_fulfilled":
            # Already bought after it was asked for: the request row stays
            # untouched (shadow mode), but it is not a need any more.
            reasons.insert(0, "explicitly requested, but a later order line satisfies it — treated as fulfilled")
            confidence = min(confidence, config.medium_confidence - 0.01)
        else:
            # "uncertain" = a later line matched the head noun but left a
            # qualifier unverified: probably bought. Suggest, do not auto-add
            # (buying cherry tomatoes twice in a week is waste, not safety).
            confidence = max(confidence, config.explicit_need_confidence
                             if request_status == "active" else config.high_confidence - 0.01)
            mandatory = True
            quantity = max(quantity if observed else 0, max(float(e.value or 1) for e in explicit)) or 1.0
            qty_conf = max(qty_conf, 0.8)
            reasons.insert(0, "explicitly requested: " + ", ".join(sorted({e.detail for e in explicit}))
                           + (" (a later order may have covered it — uncertain)" if request_status == "uncertain" else ""))

    meal_or_event = ""
    meals = [e for e in by_type.get(EvidenceType.planned_meal, []) if not e.data.get("is_meal")]
    inventory_known = False
    if meals:
        meal_or_event = meals[0].data.get("meal", "")
        likely_have = all(e.data.get("likely_have") for e in meals)
        floor = (config.meal_ingredient_likely_have_confidence if likely_have
                 else config.meal_ingredient_confidence)
        confidence = max(confidence, floor)
        reasons.append(meals[0].detail)
        if likely_have:
            reasons.append("pantry heuristic says probably at home — inventory NOT known")

    # waste dampening
    waste = by_type.get(EvidenceType.waste_report, [])
    if waste:
        rate = _clamp(max(e.value or 0 for e in waste))
        if not mandatory:
            confidence = _clamp(confidence - config.waste_confidence_penalty * rate)
        quantity = max(1.0, round(quantity * (1 - rate * config.waste_quantity_factor), 1))
        qty_conf = _clamp(qty_conf - config.waste_quantity_confidence_penalty)
        reasons.append(f"waste reported ({rate:.0%} thrown away) — quantity reduced")

    # rejections: drop the product, keep the need
    rejected = {(e.store, e.product_code) for e in by_type.get(EvidenceType.explicit_rejection, [])}
    rejected_codes = sorted({code for _, code in rejected})
    if rejected:
        reasons.append("household rejected a specific product for this term")

    candidates: list[CandidateProduct] = []
    for e in by_type.get(EvidenceType.explicit_preference, []):
        if e.product_code and (e.store, e.product_code) not in rejected and not e.data.get("standing"):
            candidates.append(CandidateProduct(e.store, e.product_code, e.product_name, e.origin, e.source_ref))
    for h in history:
        if h.product_code and (h.store, h.product_code) not in rejected and not any(
                c.store == h.store and c.product_code == h.product_code for c in candidates):
            candidates.append(CandidateProduct(h.store, h.product_code, h.product_name, Origin.INFERENCE, h.source_ref))

    # -- product resolution (Phase 1.5): meaning first, evidence second --
    resolution: dict = {}
    product_conf = 0.0
    # The resolver runs catalogue searches; a term that is going to be
    # ignored anyway is not worth them (need confidence does not depend
    # on the product, so the decision is already known here).
    worth_resolving = mandatory or decide(_clamp(max(confidence, config.stockup_confidence if promos_present(by_type) else confidence)), config) != IGNORE
    if storage is not None and worth_resolving:
        from . import vnext_resolver

        res = vnext_resolver.resolve(storage, term, evidence, config, purchases_by_code, raw=display)
        resolution = res.to_dict()
        product_conf = res.confidence
        if res.chosen is not None:
            chosen = CandidateProduct(res.chosen.store, res.chosen.product_code, res.chosen.product_name,
                                      res.chosen.origin, f"resolver:{res.chosen.source}")
            candidates = [chosen] + [c for c in candidates
                                     if not (c.store == chosen.store and c.product_code == chosen.product_code)
                                     and not any(rc.product_code == c.product_code and rc.store == c.store
                                                 and rc.status == vnext_resolver.REJECTED for rc in res.candidates)]
        else:
            candidates = []
        if res.human_flagged:
            reasons.append(res.human_flagged)
    elif candidates:
        product_conf = 0.9 if any(c.origin == Origin.HUMAN_DECLARED for c in candidates) else 0.5

    # -- promotions / stock-up: economics, not a percentage --
    stock_up = False
    stockup_info: dict = {}
    promos = by_type.get(EvidenceType.promotion, [])
    recurring = bool(history) or bool(standing)
    pantryable = department in PANTRYABLE_DEPARTMENTS or any(p.data.get("stockable") for p in promos)
    rule = next((r for r in (stockup_rules or []) if r.get("term") == term), None)
    lapsed = str(depletion.get("state", "")).startswith("lapsed")
    for promo in promos:
        discount = float(promo.value or 0)
        saving = float(promo.data.get("saving") or 0)
        bar = float(rule["min_discount"]) if rule else config.stockup_min_discount
        good = discount >= bar and saving >= config.stockup_min_saving and promo.trust >= config.promotion_trust_readable
        if not good:
            reasons.append(f"promotion noted but below the stock-up bar ({discount:.0%})")
            continue
        if lapsed and not rule:
            reasons.append(f"good promotion ({discount:.0%}) but the item looks lapsed — not stocking up")
            continue
        if not (recurring or pantryable or rule):
            reasons.append(f"promotion ({discount:.0%}) on something the household does not buy — not stocking up")
            continue
        if storage is not None:
            from . import vnext_economics

            eco = vnext_economics.assess(storage, term, promo, evidence, config, rule)
            stockup_info = eco.to_dict()
            if eco.worthwhile:
                stock_up = True
                quantity = max(quantity, float(eco.recommended_units))
                confidence = max(confidence, config.stockup_confidence)
                reasons.append("stock-up: " + "; ".join(eco.reasons))
                break
            reasons.append("promotion not worth stocking up: " + "; ".join(eco.reasons))
        else:
            stock_up = True
            cap = int(rule["max_quantity"]) if rule else config.stockup_quantity
            quantity = max(quantity, float(cap))
            confidence = max(confidence, config.stockup_confidence)
            reasons.append(f"good promotion ({discount:.0%}, saves {saving:.2f}₪): stock-up candidate")
            break

    # -- retailer confidence --
    if candidates:
        stores_with_product = sorted({c.store for c in candidates})
        retailer_conf = 0.9 if len(stores_with_product) == 1 else 0.6
    else:
        stores_with_product, retailer_conf = [], 0.0

    # -- what is still open, and whether it is the household's decision --
    unresolved: list[str] = []
    status = resolution.get("status", "")
    if not candidates:
        if status == "rejected_by_constraints":
            unresolved.append("every known product violates the request — nothing safe to add")
        else:
            unresolved.append("no known product for this term at any chain — retailer resolution needed")
    elif resolution.get("qualifiers_unverified"):
        unresolved.append("unverified: " + ", ".join(resolution["qualifiers_unverified"]))
    if len(stores_with_product) > 1:
        unresolved.append("available at more than one chain — chain choice open")

    subs = resolution.get("substitution_candidates") or []
    exception_class, exception_reason = QUIET, ""
    if mandatory and not candidates:
        if subs:
            exception_class = TRUE_USER_DECISION
            exception_reason = "requested product not found; substitutes differ: " + ", ".join(c["product_name"] for c in subs[:3])
        else:
            exception_class = TRUE_USER_DECISION
            exception_reason = ("requested product cannot be matched safely" if status == "rejected_by_constraints"
                                else "no product found for this request at any chain")
    elif mandatory and candidates and any(q.startswith("brand:") for q in resolution.get("qualifiers_unverified", [])):
        exception_class = TRUE_USER_DECISION
        exception_reason = ("requested brand not found; closest is " + candidates[0].product_name
                            + " — a different brand is the household's call")
    elif stock_up and (quantity >= config.exception_stockup_quantity or
                       float(stockup_info.get("economics", {}).get("deal_price") or 0) * quantity >= config.exception_stockup_spend):
        exception_class = TRUE_USER_DECISION
        exception_reason = f"stock-up commitment: {quantity:g} units"
    elif candidates and product_conf < config.product_confidence_agent_resolvable and mandatory:
        exception_class = AGENT_RESOLVABLE
        exception_reason = "product inferred at low confidence — Gordon picks, household can correct"
    elif candidates and not any(c.origin == Origin.HUMAN_DECLARED for c in candidates):
        exception_class = AGENT_RESOLVABLE
        exception_reason = "product inferred, not human-confirmed — resolved by evidence"

    confidence = _clamp(confidence)
    return NeedAssessment(
        term=term, display_name=display, need_confidence=confidence,
        recommended_quantity=quantity, quantity_confidence=_clamp(qty_conf),
        decision=decide(confidence, config), reasons=reasons, evidence_refs=refs,
        mandatory=mandatory, stock_up=stock_up, meal_or_event=meal_or_event,
        inventory_known=inventory_known, candidate_products=candidates,
        rejected_products=rejected_codes, stores=stores_with_product or stores, unresolved=unresolved,
        depletion=depletion, unit=unit,
        product_confidence=_clamp(product_conf), retailer_confidence=retailer_conf,
        product_resolution=resolution, exception_class=exception_class, exception_reason=exception_reason,
        cadence_stratum=stratum, cadence_capped=capped, stockup_assessment=stockup_info,
        request_status_estimate=request_status, fulfillment_evidence=fulfillment,
    )


def assess_all(storage, config: VNextConfig = DEFAULT, today: date | None = None,
               evidence: EvidenceSet | None = None) -> tuple[list[NeedAssessment], EvidenceSet]:
    """Assess every candidate term. Candidates are the union of explicit
    needs, the standing list, recurring products (tier A–C), meal
    ingredients and promotion terms; tier-D history alone is not a
    candidate (it would be noise) unless something else names the term."""
    from . import vnext_reconcile, vnext_resolver

    evidence = evidence or gather(storage, config, today)
    rules = storage.list_vnext_stockup_rules()
    grouped = evidence.by_term()
    purchases = vnext_resolver.purchases_index(storage)
    reconciliation = {f"adhoc_requests:{r.request_id}": r.to_dict() for r in vnext_reconcile.reconcile_all(storage)}
    out: list[NeedAssessment] = []
    for term, items in grouped.items():
        if not term:
            continue
        kinds = {e.type for e in items}
        tiers = {e.data.get("tier") for e in items if e.type == EvidenceType.purchase_history}
        names_it = kinds & {EvidenceType.explicit_need, EvidenceType.explicit_preference,
                            EvidenceType.planned_meal, EvidenceType.promotion, EvidenceType.waste_report}
        if not names_it and not (tiers & {"A", "B", "C"}):
            continue
        if kinds == {EvidenceType.planned_meal} and all(e.data.get("is_meal") for e in items):
            continue  # the meal row itself is not a need
        out.append(assess_term(term, items, config, rules, storage=storage,
                               purchases_by_code=purchases, reconciliation=reconciliation))
    out.sort(key=lambda a: (not a.mandatory, -a.need_confidence, a.term))
    return out, evidence
