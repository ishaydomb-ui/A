"""Deals the cycle puts *in the cart* on its own, not just reports.

Set by Ishay 2026-09-06, from how he actually shops: he fills the cart
with "really everything" and then deletes what is already at home,
because deleting a line from a cart takes seconds while spotting a deal
by hand does not happen at all. A deal that only ever arrives as a
Telegram message is a deal that needs a second action to act on — and
the measured record says those second actions do not happen: the ₪599
gift threshold was missed on both of the two most recent real orders
(by ₪38.21 and ₪77.81), *after* the checking feature was already live.

So the bar here is deliberately not "would he definitely want this".
It is "would he rather delete it than never have seen it", which for a
stockpileable product on a deep discount is nearly always yes.

Two guards keep that from turning into waste, which is the failure this
project cares about more than a missed saving:

**Pantryable only, by default.** Softener at 40% off sits in a cupboard;
half-price bananas rot. `radar` already classifies this by department,
and perishables are left to the report rather than the cart.

**Only things the household actually buys.** The candidate set is the
stock table — products with real purchase history — never the catalogue
at large. A deep discount on something never once bought is not a
saving, it is a new habit nobody asked for.

Shufersal only, and that is a data limit rather than a preference.
Promotions live in `catalog_promotions`, which is Shufersal's own
price-transparency feed; Tiv Taam publishes no such feed at all (its
`store_prices` rows are prices observed on past orders, some years old).
A Shufersal deal added to a Tiv Taam cart would simply be bought at Tiv
Taam's own undiscounted price — the saving is not transferable, so the
cart it goes into has to be the chain that published it.
"""
from __future__ import annotations

from dataclasses import dataclass

from .disambiguate import _normalise
from .radar import find_stockup_deals

# Chains whose own promotions we actually hold. See the module docstring:
# this is about where the discount is real, not about which carts we can
# fill (we can fill both).
DEAL_CAPABLE_STORES = {"shufersal"}

# A ceiling on how much a cycle may add by itself. The workflow tolerates
# deleting a few lines; it does not tolerate a cart that has to be read
# in full to find the things actually wanted.
MAX_DEAL_ITEMS = 8


@dataclass(frozen=True)
class DealPick:
    """One deal worth adding to the cart without being asked."""

    term: str
    catalog_name: str
    shelf_price: float
    deal_price: float
    discount: float
    description: str
    quantity: int = 1

    @property
    def label(self) -> str:
        """Why this is in the cart, short enough to scan in a list."""
        saved = self.shelf_price - self.deal_price
        return (
            f"-{round(self.discount * 100)}% · {self.deal_price:.2f}₪ "
            f"במקום {self.shelf_price:.2f}₪ (חיסכון {saved:.2f}₪)"
        )


def picks_for(
    storage,
    store: str,
    skip_terms: list[str] | None = None,
    limit: int = MAX_DEAL_ITEMS,
    pantryable_only: bool = True,
) -> list[DealPick]:
    """Deals to add to `store`'s cart beyond the list the user asked for.

    `skip_terms` are what the cycle is already adding (standing list and
    ad-hoc requests). A deal on something already going in would arrive
    as a duplicate line, which reads like a bug in a cart the household
    is about to scan quickly.
    """
    if store not in DEAL_CAPABLE_STORES:
        return []

    skip = {_normalise(t) for t in (skip_terms or []) if t}

    picks: list[DealPick] = []
    for deal in find_stockup_deals(storage, store):
        if pantryable_only and not deal.pantryable:
            continue
        # Search on the name the household's own purchase produced, not
        # the catalogue's — that is the term the cart matcher and the
        # product memory are both already keyed on.
        term = deal.bought_name
        folded = _normalise(term)
        if any(folded == s or folded in s or s in folded for s in skip):
            continue
        picks.append(
            DealPick(
                term=term,
                catalog_name=deal.catalog_name,
                shelf_price=deal.shelf_price,
                deal_price=deal.deal_price,
                discount=deal.discount,
                description=deal.description,
            )
        )
        if len(picks) >= limit:
            break
    return picks


def format_picks(picks: list[DealPick]) -> str:
    """The deal block for the end-of-cycle summary.

    Kept separate from the added/not-found buckets on purpose: these are
    the lines the household did not ask for, so they are the lines most
    likely to be deleted, and they need to be findable in one glance
    rather than mixed into a list of forty.
    """
    if not picks:
        return ""
    lines = ["🏷️ *נוספו בגלל מבצע חריג* — מחקו מה שלא צריך:"]
    total = 0.0
    for pick in picks:
        total += pick.shelf_price - pick.deal_price
        lines.append(f"• {pick.catalog_name}\n   _{pick.label}_")
    lines.append("")
    lines.append(f"_סה\"כ חיסכון אם נשארים: {total:.2f}₪_")
    return "\n".join(lines)
