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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .household_evidence import Evidence, EvidenceSet, EvidenceType, Origin, gather
from .vnext_config import DEFAULT, VNextConfig

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

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "display_name": self.display_name or self.term,
            "need_confidence": round(self.need_confidence, 3),
            "recommended_quantity": self.recommended_quantity,
            "unit": self.unit,
            "quantity_confidence": round(self.quantity_confidence, 3),
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


def _tier_base(tier: str, config: VNextConfig) -> float:
    return {"A": config.tier_base_confidence_a, "B": config.tier_base_confidence_b,
            "C": config.tier_base_confidence_c}.get(tier or "", 0.0)


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
                stockup_rules: list[dict] | None = None) -> NeedAssessment:
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
    if history:
        reasons.append(f"bought regularly (tier {tier})")
    if depletion.get("state"):
        reasons.append(f"{depletion['state']}: {depletion['days_since']:.0f}d since purchase vs ~{depletion['cadence_days']:.0f}d cadence")

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
    if explicit:
        confidence = max(confidence, config.explicit_need_confidence)
        mandatory = True
        quantity = max(quantity if observed else 0, max(float(e.value or 1) for e in explicit)) or 1.0
        qty_conf = max(qty_conf, 0.8)
        reasons.insert(0, "explicitly requested: " + ", ".join(sorted({e.detail for e in explicit})))

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
        if e.product_code and (e.store, e.product_code) not in rejected:
            candidates.append(CandidateProduct(e.store, e.product_code, e.product_name, e.origin, e.source_ref))
    for h in history:
        if h.product_code and (h.store, h.product_code) not in rejected and not any(
                c.store == h.store and c.product_code == h.product_code for c in candidates):
            candidates.append(CandidateProduct(h.store, h.product_code, h.product_name, Origin.INFERENCE, h.source_ref))

    # promotions / stock-up
    stock_up = False
    promos = by_type.get(EvidenceType.promotion, [])
    recurring = bool(history) or bool(standing)
    pantryable = department in PANTRYABLE_DEPARTMENTS or any(p.data.get("stockable") for p in promos)
    rule = next((r for r in (stockup_rules or []) if r.get("term") == term), None)
    for promo in promos:
        discount = float(promo.value or 0)
        saving = float(promo.data.get("saving") or 0)
        bar = float(rule["min_discount"]) if rule else config.stockup_min_discount
        good = discount >= bar and saving >= config.stockup_min_saving and promo.trust >= config.promotion_trust_readable
        lapsed = str(depletion.get("state", "")).startswith("lapsed")
        if good and lapsed and not rule:
            reasons.append(f"good promotion ({discount:.0%}) but the item looks lapsed — not stocking up")
            continue
        if good and (recurring or pantryable or rule):
            stock_up = True
            cap = int(rule["max_quantity"]) if rule else config.stockup_quantity
            quantity = max(quantity, float(cap))
            confidence = max(confidence, config.stockup_confidence)
            reasons.append(f"good promotion ({discount:.0%}, saves {saving:.2f}₪): stock-up candidate")
            break
        # a mediocre promotion is noted but changes nothing
        reasons.append(f"promotion noted but below the stock-up bar ({discount:.0%})")

    unresolved: list[str] = []
    if not candidates:
        unresolved.append("no known product for this term at any chain — retailer resolution needed")
    elif not any(c.origin == Origin.HUMAN_DECLARED for c in candidates):
        unresolved.append("product choice is inferred, not human-confirmed")
    if explicit:
        unresolved.append("may already be in a cart — cart presence is unknown in shadow mode")
    if len(stores) > 1:
        unresolved.append("bought at more than one chain — chain choice open")

    confidence = _clamp(confidence)
    return NeedAssessment(
        term=term, display_name=display, need_confidence=confidence,
        recommended_quantity=quantity, quantity_confidence=_clamp(qty_conf),
        decision=decide(confidence, config), reasons=reasons, evidence_refs=refs,
        mandatory=mandatory, stock_up=stock_up, meal_or_event=meal_or_event,
        inventory_known=inventory_known, candidate_products=candidates,
        rejected_products=rejected_codes, stores=stores, unresolved=unresolved,
        depletion=depletion, unit=unit,
    )


def assess_all(storage, config: VNextConfig = DEFAULT, today: date | None = None,
               evidence: EvidenceSet | None = None) -> tuple[list[NeedAssessment], EvidenceSet]:
    """Assess every candidate term. Candidates are the union of explicit
    needs, the standing list, recurring products (tier A–C), meal
    ingredients and promotion terms; tier-D history alone is not a
    candidate (it would be noise) unless something else names the term."""
    evidence = evidence or gather(storage, config, today)
    rules = storage.list_vnext_stockup_rules()
    grouped = evidence.by_term()
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
        out.append(assess_term(term, items, config, rules))
    out.sort(key=lambda a: (not a.mandatory, -a.need_confidence, a.term))
    return out, evidence
