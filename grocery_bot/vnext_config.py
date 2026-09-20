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
