"""Basket economics across chains — vNext Phase 1: interface and models only.

Nothing here runs against a retailer or changes which chain a cart goes
to. The dataclasses fix the vocabulary a later phase will fill in:

- delivery / minimum-order cost per chain;
- per-unit price (₪/kg, ₪/l, ₪/unit) rather than sticker price;
- promotion quality (readable arithmetic vs. worded condition);
- historical price (is today's price actually good for *this* item);
- stock-up economics (saving × expected consumption before spoilage);
- shelf life / expected consumption horizon;
- split-order friction (two deliveries, two minimums, two reviews).

`optimize()` returns a result with `implemented=False` and the inputs it
would have used, so callers can wire the seam today and get an honest
"not decided" rather than a silent default.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .shopping_plan import ShoppingPlan


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
    available: bool | None = None           # None = not checked (no live read in shadow mode)


@dataclass(frozen=True)
class BasketQuote:
    store: str
    lines: tuple[LineQuote, ...] = ()
    subtotal: float | None = None
    delivery: float | None = None
    total: float | None = None
    coverage: float = 0.0                   # share of plan lines this chain can quote
    stock_up_value: float | None = None     # saving the stock-up lines are expected to realise


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


def optimize(inputs: OptimizerInput) -> OptimizerResult:
    """Phase 1: a declared seam, not a decision. Never alters live retailer
    selection; Phase 2+ fills this in behind the same signature."""
    return OptimizerResult(
        implemented=False,
        reasons=("basket optimisation is not implemented in Phase 1; "
                 "existing per-chain refill behaviour is unchanged",),
        inputs_summary={
            "plan_items": len(inputs.plan.items),
            "chains": [c.store for c in inputs.chains],
            "quotes": len(inputs.quotes),
            "split_penalty": inputs.split_penalty,
        },
    )
