"""The one canonical ShoppingPlan — vNext Phase 1.

A plan is what Gordon *would* put in the carts, with every item carrying
its evidence, reason, confidence and open decisions. Building one reads
Gordon's tables and nothing else: no adapter, no browser, no cart, no
write. `build_plan` is safe to run beside the live bot at any time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime

from .household_evidence import EvidenceSet, Origin
from .need_engine import AUTO_INCLUDE, IGNORE, SUGGEST, NeedAssessment, assess_all
from .vnext_config import DEFAULT, VNextConfig

SCHEMA = "gordon-vnext-shopping-plan/v1"


@dataclass
class PlanItem:
    term: str                                   # normalised need
    display_name: str
    source_evidence: list[str]                  # evidence refs
    reason: str
    confidence: float
    quantity: float
    quantity_confidence: float
    unit: str
    mandatory: bool
    stock_up: bool
    meal_or_event: str
    candidate_retailers: list[str]
    unresolved_decisions: list[str]
    decision: str                               # auto_include | suggest
    inferred_product: dict | None = None        # in-memory only, never persisted
    human_product: dict | None = None
    inventory_known: bool = False
    depletion_inference: dict = field(default_factory=dict)
    origin_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "display_name": self.display_name,
            "decision": self.decision,
            "mandatory": self.mandatory,
            "stock_up": self.stock_up,
            "confidence": round(self.confidence, 3),
            "quantity": self.quantity,
            "unit": self.unit,
            "quantity_confidence": round(self.quantity_confidence, 3),
            "reason": self.reason,
            "meal_or_event": self.meal_or_event,
            "candidate_retailers": self.candidate_retailers,
            "human_product": self.human_product,
            "inferred_product": self.inferred_product,
            "inventory_known": self.inventory_known,
            "unresolved_decisions": self.unresolved_decisions,
            "depletion_inference": self.depletion_inference,
            "source_evidence": self.source_evidence,
            "origin_summary": self.origin_summary,
        }


@dataclass
class ShoppingPlan:
    generated_at: str
    as_of: str
    items: list[PlanItem]
    ignored: list[dict]                          # term + confidence, for the record
    summary: dict
    caveats: list[str]
    config: dict
    schema: str = SCHEMA
    mutates_cart: bool = False                   # always False — a plan is a proposal

    @property
    def auto_items(self) -> list[PlanItem]:
        return [i for i in self.items if i.decision == AUTO_INCLUDE]

    @property
    def suggested_items(self) -> list[PlanItem]:
        return [i for i in self.items if i.decision == SUGGEST]

    @property
    def exceptions(self) -> list[PlanItem]:
        """Items a person genuinely has to decide on — the exception view.

        Not every MEDIUM item: only the materially relevant ones (see
        `VNextConfig.exception_margin`), plus any explicit need with no
        known product at any chain. The other suggestions stay in the
        plan JSON for review without being pushed at anyone.
        """
        margin = float(self.config.get("exception_margin", 0.15))
        high = float(self.config.get("high_confidence", 0.75))
        out = []
        for i in self.items:
            unknown_product = any("no known product" in u for u in i.unresolved_decisions)
            if i.decision == AUTO_INCLUDE and i.mandatory and unknown_product:
                out.append(i)
            elif i.decision == SUGGEST and (
                i.stock_up or i.meal_or_event or i.confidence >= high - margin
            ):
                out.append(i)
        return out

    @property
    def quiet_suggestions(self) -> list[PlanItem]:
        shown = {i.term for i in self.exceptions}
        return [i for i in self.suggested_items if i.term not in shown]

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "as_of": self.as_of,
            "mutates_cart": self.mutates_cart,
            "summary": self.summary,
            "caveats": self.caveats,
            "items": [i.to_dict() for i in self.items],
            "ignored": self.ignored,
            "config": self.config,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _item_from(assessment: NeedAssessment, evidence_by_ref: dict) -> PlanItem:
    human = next((c for c in assessment.candidate_products if c.origin == Origin.HUMAN_DECLARED), None)
    inferred = next((c for c in assessment.candidate_products if c.origin == Origin.INFERENCE), None)
    origins: dict = {}
    for ref in assessment.evidence_refs:
        ev = evidence_by_ref.get(ref)
        if ev is not None:
            origins[ev.origin.value] = origins.get(ev.origin.value, 0) + 1
    return PlanItem(
        term=assessment.term,
        display_name=assessment.display_name or assessment.term,
        source_evidence=assessment.evidence_refs,
        reason="; ".join(assessment.reasons),
        confidence=assessment.need_confidence,
        quantity=assessment.recommended_quantity,
        quantity_confidence=assessment.quantity_confidence,
        unit=assessment.unit,
        mandatory=assessment.mandatory,
        stock_up=assessment.stock_up,
        meal_or_event=assessment.meal_or_event,
        candidate_retailers=assessment.stores,
        unresolved_decisions=assessment.unresolved,
        decision=assessment.decision,
        inferred_product=inferred.to_dict() if inferred and not human else None,
        human_product=human.to_dict() if human else None,
        inventory_known=assessment.inventory_known,
        depletion_inference=assessment.depletion,
        origin_summary=origins,
    )


def build_plan(storage, config: VNextConfig | None = None, today: date | None = None,
               evidence: EvidenceSet | None = None) -> ShoppingPlan:
    config = config or DEFAULT
    today = today or date.today()
    assessments, evidence = assess_all(storage, config, today, evidence)
    by_ref = {e.source_ref: e for e in evidence.items if e.source_ref}

    items = [_item_from(a, by_ref) for a in assessments if a.decision != IGNORE]
    ignored = [{"term": a.term, "confidence": round(a.need_confidence, 3)}
               for a in assessments if a.decision == IGNORE]

    auto = [i for i in items if i.decision == AUTO_INCLUDE]
    summary = {
        "total_items": len(items),
        "auto_include": len(auto),
        "suggest": len([i for i in items if i.decision == SUGGEST]),
        "ignored": len(ignored),
        "explicit_requests": len([i for i in items if i.mandatory]),
        "routine": len([i for i in auto if not i.mandatory and not i.stock_up and not i.meal_or_event]),
        "meal_related": len([i for i in items if i.meal_or_event]),
        "stock_up": len([i for i in items if i.stock_up]),
        "with_human_product": len([i for i in items if i.human_product]),
        "with_inferred_product_only": len([i for i in items if i.inferred_product and not i.human_product]),
        "unresolved_products": len([i for i in items if not i.human_product and not i.inferred_product]),
        "evidence_counts": evidence.counts(),
    }
    plan = ShoppingPlan(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        as_of=today.isoformat(),
        items=items,
        ignored=ignored,
        summary=summary,
        caveats=list(evidence.caveats),
        config=config.to_dict(),
    )
    summary["decisions_needed"] = len(plan.exceptions)
    summary["quiet_suggestions"] = len(plan.quiet_suggestions)
    return plan


def format_plan(plan: ShoppingPlan, limit: int = 60) -> str:
    """Human-readable, for the CLI. Not a Telegram message."""
    s = plan.summary
    lines = [
        f"vNext shopping plan — as of {plan.as_of} (shadow mode, mutates_cart={plan.mutates_cart})",
        f"  {s['total_items']} items: {s['auto_include']} auto-include, {s['suggest']} suggested, "
        f"{s['ignored']} ignored",
        f"  explicit {s['explicit_requests']} · routine {s['routine']} · meal {s['meal_related']} · "
        f"stock-up {s['stock_up']} · decisions needed {s['decisions_needed']} "
        f"(+{s['quiet_suggestions']} quiet suggestions, not pushed)",
        f"  product choice: human {s['with_human_product']} · inferred-only {s['with_inferred_product_only']} · "
        f"unresolved {s['unresolved_products']}",
        "",
    ]
    for item in plan.items[:limit]:
        flags = "".join([
            "!" if item.mandatory else "",
            "📦" if item.stock_up else "",
            "🍽" if item.meal_or_event else "",
        ])
        product = (item.human_product or item.inferred_product or {}).get("product_name", "")
        tag = "H" if item.human_product else ("i" if item.inferred_product else "?")
        lines.append(
            f"  [{item.decision:12}] {item.confidence:.2f}  x{item.quantity:g}{item.unit} ({item.quantity_confidence:.1f})  "
            f"{item.display_name} {flags}  -> {tag}:{product or '—'}  @{','.join(item.candidate_retailers) or '?'}"
        )
        lines.append(f"      {item.reason}")
        for u in item.unresolved_decisions:
            lines.append(f"      ? {u}")
    if len(plan.items) > limit:
        lines.append(f"  … {len(plan.items) - limit} more")
    lines.append("")
    lines.append("caveats:")
    lines += [f"  - {c}" for c in plan.caveats]
    return "\n".join(lines)
