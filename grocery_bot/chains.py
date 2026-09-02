"""Every chain's prices in one table, so a basket can be priced anywhere.

The reframing that shaped this, from the household on 2026-09-01: they
are *flexible about which chain they shop at*. What stopped them jumping
between chains was never loyalty — it was having to rebuild the list from
scratch each time. If building a list is cheap, the right chain is
whichever one is cheapest this week.

That inverts the question. It is not "is this one deal worth a separate
delivery" — the earlier design, and wrong. It is **"where should the
whole shop go this week"**, which can be answered honestly only by
pricing the household's actual basket at every chain at once.

Sources, all keyed by EAN so they merge without name matching:

- **Shufersal** — its own public feed, already in `catalog_products`.
- **Tiv Taam, Victory** — live Self-Point API, no login.
- **Rami Levy, Osher Ad, Keshet, Politzer, Fresh Market** — the shared
  transparency portal.

A feed that is present but stale is rejected rather than used: Yohananof
publishes files that parse perfectly and were last updated in December
2024. Comparing against those is worse than not comparing at all, because
it is wrong with confidence instead of visibly absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .prices import parse_prices
from .publishedprices import MAX_FEED_AGE_DAYS, PORTAL_CHAINS, PublishedPrices

# Display names, and whether the household shops there today. The second
# flag exists because a deal at a chain they already use is a normal
# saving, while a deal at one they do not is a *decision* — and only the
# second needs justifying against the hassle of a new order.
CHAIN_NAMES = {
    "shufersal": ("שופרסל", True),
    "tivtaam": ("טיב טעם", True),
    "victory": ("ויקטורי", True),
    "ramilevy": ("רמי לוי", False),
    "osherad": ("אושר עד", False),
    "keshet": ("קשת טעמים", False),
    "politzer": ("פוליצר", False),
    "freshmarket": ("פרש מרקט", False),
    "yohananof": ("יוחננוף", False),
}


def display_name(chain: str) -> str:
    return CHAIN_NAMES.get(chain, (chain, False))[0]


def is_regular(chain: str) -> bool:
    return CHAIN_NAMES.get(chain, (chain, False))[1]


# Chains this project can actually put something into a cart at. Every
# other chain is price data only: its deals are real and worth knowing,
# but "add it" cannot mean anything there, and saying so up front is the
# difference between useful information and a promise that quietly fails.
CART_CAPABLE = {"shufersal", "tivtaam"}

# Where the household goes to review and pay. Kept here rather than in the
# bot because the hand-off message needs one button per chain it filled,
# and a URL that lives next to the button is a URL that gets forgotten
# when a chain is added — which is how a Tiv Taam cart came to be filled
# and then advertised with a Shufersal link.
CART_URLS = {
    "shufersal": "https://www.shufersal.co.il/online/he/cart/cartsummary",
    # Tiv Taam has no cart *page*: /cart redirects to the homepage, both
    # empty and with items in it (verified 2026-09-02 against the real
    # account). The cart is a side panel opened from the header, so the
    # honest link is the site itself, where the cart bar is the first
    # thing at the top. A /cart link would land the household on the
    # homepage looking for a basket that is one tap away in the header.
    "tivtaam": "https://www.tivtaam.co.il/",
}


def can_fill_cart(chain: str) -> bool:
    return chain in CART_CAPABLE


def cart_url(chain: str) -> str | None:
    return CART_URLS.get(chain)


@dataclass(frozen=True)
class FeedResult:
    chain: str
    products: int
    age_days: int | None
    file_name: str = ""
    skipped_reason: str = ""

    @property
    def used(self) -> bool:
        return not self.skipped_reason


def refresh_portal_chain(
    storage,
    chain: str,
    proxy: str | None = None,
    today: date | None = None,
    max_age_days: int = MAX_FEED_AGE_DAYS,
) -> FeedResult:
    """Pull one chain's newest full price snapshot into store_prices."""
    portal = PublishedPrices(chain, proxy=proxy)
    newest = portal.latest("PriceFull")
    if newest is None:
        return FeedResult(chain, 0, None, skipped_reason="no PriceFull published")

    age = newest.age_days(today)
    if age is None or age > max_age_days:
        return FeedResult(
            chain,
            0,
            age,
            newest.name,
            skipped_reason=f"feed is {age} days old — refusing to price against it",
        )

    products = parse_prices(portal.download_xml(newest))
    observed = (newest.published_on or (today or date.today())).isoformat()
    rows = [
        {
            "barcode": product.item_code,
            "name": product.name,
            "price": product.price,
            "observed_at": observed,
            "source": "feed",
        }
        for product in products
        if product.item_code and product.price
    ]
    if rows:
        storage.record_store_prices(chain, rows)
    return FeedResult(chain, len(rows), age, newest.name)


def refresh_all_portal_chains(
    storage, proxy: str | None = None, today: date | None = None
) -> list[FeedResult]:
    """Refresh every chain on the shared portal, skipping the broken ones.

    One chain failing must never sink the rest: these are third-party
    feeds that go down, go stale, or change their username without
    warning, and a partial picture still answers the question.
    """
    results = []
    for chain in sorted(PORTAL_CHAINS):
        try:
            results.append(refresh_portal_chain(storage, chain, proxy, today))
        except Exception as exc:
            results.append(
                FeedResult(chain, 0, None, skipped_reason=str(exc)[:120])
            )
    return results


def format_refresh(results: list[FeedResult]) -> str:
    lines = []
    for result in sorted(results, key=lambda r: -r.products):
        if result.used:
            lines.append(f"✅ {display_name(result.chain)}: {result.products} מוצרים")
        else:
            lines.append(f"⚠️ {display_name(result.chain)}: {result.skipped_reason}")
    return "\n".join(lines)
