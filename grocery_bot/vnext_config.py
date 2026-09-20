"""Tunables for the vNext decision layer, in one place.

Every band, multiplier and cutoff the vNext engines consult lives here as
a dataclass field with an env override (`GORDON_VNEXT_<FIELD>`), so a
threshold can be moved for a shadow run without touching engine code and
without a magic number hiding inline where nobody will find it later.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields

ENV_PREFIX = "GORDON_VNEXT_"


@dataclass(frozen=True)
class VNextConfig:
    # -- need confidence bands -------------------------------------------
    # >= high  -> auto_include; >= medium -> suggest; below -> ignore.
    high_confidence: float = 0.75
    medium_confidence: float = 0.45
    # A MEDIUM item is put in front of a person only when it is materially
    # relevant: within this margin of HIGH, a stock-up, a meal item, or an
    # explicit need whose product is unknown. The rest stay quiet.
    exception_margin: float = 0.15

    # -- cadence / recency --------------------------------------------------
    # A purchase within cadence * recent_factor days counts as "just bought".
    recent_factor: float = 0.5
    # elapsed >= cadence * due_factor -> likely due; >= overdue_factor -> overdue.
    due_factor: float = 1.0
    overdue_factor: float = 1.6
    # Past this many cadences the likelier story is "no longer bought" or
    # "bought elsewhere", not "very overdue" — same rule shelflife.py uses.
    lapsed_factor: float = 3.0
    lapsed_confidence: float = 0.35
    # Per-item cadence from real order dates needs this many observations.
    min_cadence_observations: int = 3
    # Confidence ceilings by how the cadence was obtained.
    cadence_trust_measured: float = 0.85
    cadence_trust_share_estimate: float = 0.45
    cadence_trust_household_fallback: float = 0.25
    # Base confidence for a recurring item whose recency is unknown.
    tier_base_confidence_a: float = 0.55
    tier_base_confidence_b: float = 0.40
    tier_base_confidence_c: float = 0.28

    # -- explicit signals -----------------------------------------------------
    explicit_need_confidence: float = 0.95
    standing_list_confidence: float = 0.60
    meal_ingredient_confidence: float = 0.70
    # An ingredient the pantry heuristic says is probably at home.
    meal_ingredient_likely_have_confidence: float = 0.35
    meal_lookahead_days: int = 7

    # -- waste ------------------------------------------------------------------
    # quantity *= 1 - waste_rate * waste_quantity_factor (floored at 1)
    waste_quantity_factor: float = 0.6
    waste_confidence_penalty: float = 0.15
    waste_quantity_confidence_penalty: float = 0.30

    # -- promotions / stock-up ----------------------------------------------
    stockup_min_discount: float = 0.25
    stockup_min_saving: float = 3.0
    stockup_quantity: int = 2
    stockup_confidence: float = 0.50
    # A promotion's own confidence in being genuinely good, when the feed
    # arithmetic is readable but the condition wording is not.
    promotion_trust_readable: float = 0.8
    promotion_trust_conditional: float = 0.4

    # -- cadence strata (Phase 1.5) -----------------------------------------
    # How a cadence was obtained caps what depletion alone may claim:
    #   measured  (>= min_cadence_observations real gaps) -> up to HIGH
    #   weak      (fewer real gaps than that, but >= 2 dates) -> at most MEDIUM
    #   fallback  (household gap / share, or the household gap) -> at most LOW+
    # Another evidence type (explicit need, standing list, meal) can still
    # lift the item above the cap; cadence by itself cannot.
    cadence_ceiling_measured: float = 1.0
    cadence_ceiling_weak: float = 0.70
    cadence_ceiling_fallback: float = 0.44

    # -- product resolution (Phase 1.5) -------------------------------------
    resolver_exact_base: float = 0.85
    resolver_acceptable_base: float = 0.62
    resolver_unverified_penalty: float = 0.10
    resolver_evidence_bonus_per_purchase: float = 0.03
    resolver_evidence_bonus_cap: float = 0.12
    resolver_catalogue_candidates: int = 12
    # An unresolved product on an explicit request is a real question only
    # when there is no acceptable candidate and no substitution to offer.
    product_confidence_agent_resolvable: float = 0.55

    # -- exceptions (Phase 1.5) ---------------------------------------------
    # Stock-up above this many units, or this much money, is a commitment
    # the household should be asked about rather than made for them.
    exception_stockup_quantity: int = 4
    exception_stockup_spend: float = 60.0
    # Weight of an "uncertain" pending request in readiness (an "active"
    # one counts 1.0, a "likely fulfilled" one 0).
    readiness_uncertain_request_weight: float = 0.5

    # -- stock-up economics (Phase 1.5) ---------------------------------------
    # A promotion whose price the item sat at on more than this share of
    # recorded days is routine, not an opportunity.
    economics_routine_promo_share: float = 0.50
    # A reference price this far above the 90-day median is inflated.
    economics_inflated_reference_factor: float = 1.15
    economics_lookback_days: int = 90
    # Units the household will use before spoilage: cadence-derived
    # consumption over this many days for stockable items.
    economics_stockable_horizon_days: int = 60
    economics_perishable_horizon_days: int = 7
    economics_max_units: int = 6
    economics_min_absolute_saving: float = 5.0
    # A price-per-unit history shorter than this cannot call a discount
    # "unusual" — the reference simply isn't known.
    economics_min_history_days: int = 14

    # -- readiness ----------------------------------------------------------
    readiness_prepare_now: float = 0.60
    readiness_prepare_soon: float = 0.35
    readiness_weight_essentials: float = 0.35
    readiness_weight_explicit: float = 0.25
    readiness_weight_cadence: float = 0.25
    readiness_weight_meals: float = 0.10
    readiness_weight_savings: float = 0.05
    # Explicit pending needs that already make a cycle worthwhile on their own.
    readiness_explicit_saturation: int = 6
    readiness_essentials_saturation: int = 8
    readiness_savings_saturation: int = 3
    # Plan items below this quantity of auto-includes are "too small to bother".
    min_basket_for_cycle: int = 5
    # Cart contents are never read in shadow mode (and the audit found the
    # reads unreliable anyway), so readiness confidence cannot exceed this.
    readiness_confidence_cap_cart_unknown: float = 0.75

    @classmethod
    def from_env(cls, env: dict | None = None) -> "VNextConfig":
        source = os.environ if env is None else env
        overrides = {}
        for f in fields(cls):
            raw = source.get(ENV_PREFIX + f.name.upper())
            if raw is None or raw == "":
                continue
            try:
                overrides[f.name] = _cast(f.default, raw)
            except (TypeError, ValueError):
                continue
        return cls(**overrides)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _cast(default, raw: str):
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


DEFAULT = VNextConfig()
