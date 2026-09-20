"""Is a grocery cycle worth preparing now? — vNext Phase 1.

Combines the plan's own numbers (likely-depleted essentials, explicit
pending needs, meal demand, savings) with the household's measured
rhythm into one score, with reasons. Read-only; it sends nothing.

Two things it deliberately does not know: whether the live carts already
hold these items (cart reads are not trusted, and shadow mode does not
perform one), and whether a shop happened that no chain has reported
yet. Both are stated in `reasons`, not assumed away.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .shopping_plan import ShoppingPlan, build_plan
from .vnext_config import DEFAULT, VNextConfig

PREPARE_NOW = "prepare_now"
PREPARE_SOON = "prepare_soon"
WAIT = "wait"


@dataclass
class Readiness:
    score: float
    suggested_action: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    as_of: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "schema": "gordon-vnext-readiness/v1",
            "generated_at": self.generated_at,
            "as_of": self.as_of,
            "score": round(self.score, 3),
            "suggested_action": self.suggested_action,
            "confidence": round(self.confidence, 3),
            "reasons": self.reasons,
            "signals": self.signals,
            "sends_messages": False,
        }


def _saturate(n: float, cap: float) -> float:
    return min(1.0, n / cap) if cap else 0.0


def assess(storage, config: VNextConfig | None = None, today: date | None = None,
           plan: ShoppingPlan | None = None) -> Readiness:
    from . import learn

    config = config or DEFAULT
    today = today or date.today()
    plan = plan or build_plan(storage, config, today)

    auto = plan.auto_items
    essentials_due = [i for i in auto if not i.mandatory
                      and i.depletion_inference.get("state") in ("likely due", "overdue")]
    # Phase 1.5: a request a later order already covered is not a reason
    # to shop; an uncertain one counts at a configurable fraction.
    active = plan.active_requests
    uncertain = plan.uncertain_requests
    explicit = active + uncertain
    explicit_weight = len(active) + config.readiness_uncertain_request_weight * len(uncertain)
    meal_items = [i for i in plan.items if i.meal_or_event]
    savings = [i for i in plan.items if i.stock_up]
    basket = len(auto)

    since = learn.days_since_last_order(storage, learn.ALL_CHAINS)
    gap = float(learn.typical_gap_days(storage, learn.ALL_CHAINS))
    cadence_signal = 0.0
    if since is not None and gap:
        cadence_signal = min(1.0, max(0.0, (since - gap * config.recent_factor) / (gap * (config.overdue_factor - config.recent_factor))))

    horizon = (today + timedelta(days=config.meal_lookahead_days)).isoformat()
    upcoming_meals = storage.list_vnext_planned_meals(today.isoformat(), horizon)
    upcoming_events = storage.list_vnext_household_events(today.isoformat(), horizon)

    parts = {
        "essentials": config.readiness_weight_essentials * _saturate(len(essentials_due), config.readiness_essentials_saturation),
        "explicit": config.readiness_weight_explicit * _saturate(explicit_weight, config.readiness_explicit_saturation),
        "cadence": config.readiness_weight_cadence * cadence_signal,
        "meals": config.readiness_weight_meals * (1.0 if (upcoming_meals or upcoming_events) else 0.0),
        "savings": config.readiness_weight_savings * _saturate(len(savings), config.readiness_savings_saturation),
    }
    score = sum(parts.values())
    if basket < config.min_basket_for_cycle and not explicit:
        score *= 0.5

    reasons = []
    if essentials_due:
        reasons.append(f"{len(essentials_due)} recurring items look due by cadence (inference, inventory unknown)")
    fulfilled = plan.summary.get("pending_requests", {}).get("likely_fulfilled", 0)
    if explicit:
        reasons.append(f"{len(active)} explicit requests active" + (f", {len(uncertain)} possibly already bought" if uncertain else "")
                       + (f"; {fulfilled} older ones look already bought and are not counted" if fulfilled else "")
                       + " — cart presence not verified in shadow mode")
    elif fulfilled:
        reasons.append(f"{fulfilled} pending requests look already bought by a later order — none counted")
    if since is not None:
        reasons.append(f"{since:.0f} days since the last recorded order; household rhythm ~{gap:.0f} days")
    else:
        reasons.append("no order history — cadence signal unavailable")
    if upcoming_meals or upcoming_events:
        reasons.append(f"{len(upcoming_meals)} planned meal(s) / {len(upcoming_events)} event(s) within {config.meal_lookahead_days} days")
    if savings:
        reasons.append(f"{len(savings)} stock-up opportunities on things the household buys")
    reasons.append(f"estimated basket: {basket} auto-include items, {len(plan.suggested_items)} to review")
    reasons.append("a shop nobody reported yet would not be visible here until the nightly order sync")
    reasons.append("cart_state=unknown — one cycle-level caveat, not a question per item")

    if score >= config.readiness_prepare_now:
        action = PREPARE_NOW
    elif score >= config.readiness_prepare_soon:
        action = PREPARE_SOON
    else:
        action = WAIT

    # Confidence: how much of the score rests on measured signals.
    measured_share = plan.summary.get("evidence_counts", {}).get("purchase_recency", 0)
    history = plan.summary.get("evidence_counts", {}).get("purchase_history", 0)
    data_quality = min(1.0, (measured_share / history) if history else 0.0)
    confidence = 0.35 + 0.4 * data_quality + (0.25 if since is not None else 0.0)
    if explicit:
        confidence = max(confidence, 0.7)
    # Without a trusted cart read nobody can say these items are not already
    # in a cart; that alone caps how sure a readiness call can be.
    confidence = min(confidence, config.readiness_confidence_cap_cart_unknown)

    return Readiness(
        score=min(1.0, score), suggested_action=action, confidence=min(1.0, confidence),
        reasons=reasons,
        signals={
            "essentials_due": len(essentials_due),
            "explicit_pending": len(explicit),
            "explicit_active": len(active),
            "explicit_uncertain": len(uncertain),
            "explicit_likely_fulfilled": fulfilled,
            "true_user_decisions": len(plan.exceptions),
            "days_since_last_order": None if since is None else round(since, 1),
            "household_gap_days": gap,
            "cadence_signal": round(cadence_signal, 3),
            "upcoming_meals": len(upcoming_meals),
            "upcoming_events": len(upcoming_events),
            "meal_items": len(meal_items),
            "savings_opportunities": len(savings),
            "estimated_basket": basket,
            "to_review": len(plan.suggested_items),
            "score_parts": {k: round(v, 3) for k, v in parts.items()},
            "cart_state_known": False,
        },
        as_of=today.isoformat(),
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )


def format_readiness(r: Readiness) -> str:
    lines = [
        f"vNext readiness — as of {r.as_of} (shadow mode, sends nothing)",
        f"  score {r.score:.2f} → {r.suggested_action}   (confidence {r.confidence:.2f})",
        "  reasons:",
    ]
    lines += [f"    - {x}" for x in r.reasons]
    lines.append("  signals: " + ", ".join(f"{k}={v}" for k, v in r.signals.items() if k != "score_parts"))
    lines.append("  score parts: " + ", ".join(f"{k}={v}" for k, v in r.signals.get("score_parts", {}).items()))
    return "\n".join(lines)
