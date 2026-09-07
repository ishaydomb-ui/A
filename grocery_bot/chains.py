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
- **Victory** — live Self-Point API, no login.
- **Tiv Taam, Rami Levy, Osher Ad, Keshet, Politzer, Fresh Market** — the
  shared transparency portal. Tiv Taam was recorded here for a week as
  having no public feed at all; it does, on that same portal, and its
  promotions come with it (see publishedprices.PORTAL_CHAINS).

A feed that is present but stale is rejected rather than used: Yohananof
publishes files that parse perfectly and were last updated in December
2024. Comparing against those is worse than not comparing at all, because
it is wrong with confidence instead of visibly absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import logging

from .prices import parse_prices, parse_promotions
from .publishedprices import (
    MAX_FEED_AGE_DAYS,
    PORTAL_BRANCHES,
    PORTAL_CHAINS,
    PublishedPrices,
)

logger = logging.getLogger(__name__)

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
    # Shufersal opens in the browser, and that is not a choice we get to
    # make: checked 2026-09-07, `shufersal.co.il/.well-known/
    # apple-app-site-association` serves an HTML page rather than the
    # JSON iOS requires, so the site has no Universal Links and no link
    # can hand a tap to their app. A custom scheme is not an option
    # either — Telegram only accepts http/https/tg:// in a button URL.
    "shufersal": "https://www.shufersal.co.il/online/he/cart/cartsummary",
    # Tiv Taam has no cart *page*: /cart redirects to the homepage, both
    # empty and with items in it (verified 2026-09-02 against the real
    # account). The cart is a side panel opened from the header, so the
    # honest link is the site itself, where the cart bar is the first
    # thing at the top.
    #
    # `?in_app=1` opens the Tiv Taam app instead of Safari. Their AASA
    # (checked 2026-09-07) declares appID HKTXU3DYP4.com.selfpoint.apps.
    # TivTaam matching any path carrying an `in_app` query parameter, so
    # this is their own documented way in rather than a guess. It stays
    # harmless in a desktop browser, which simply ignores the parameter.
    "tivtaam": "https://www.tivtaam.co.il/?in_app=1",
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
    promotions: int = 0

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
    """Pull one chain's newest full price snapshot into store_prices.

    Where the chain publishes promotions too, those are pulled into
    store_promotions in the same pass — they are keyed on the same
    barcode as the prices, so a deal can be joined to a shelf price with
    no name matching anywhere in the path.
    """
    portal = PublishedPrices(chain, proxy=proxy)
    branch = PORTAL_BRANCHES.get(chain, "")
    newest = portal.latest("PriceFull", branch_id=branch)
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

    # Promotions, where this chain publishes them. A failure here must not
    # lose the prices already read: a chain with prices and no promos is
    # useful, a chain with neither is not.
    promo_file = portal.latest("PromoFull", branch_id=branch)
    promotions = 0
    if promo_file is not None and (promo_file.age_days(today) or 0) <= max_age_days:
        try:
            parsed = parse_promotions(portal.download_xml(promo_file))
            promo_rows = [
                {
                    "barcode": promo.item_code,
                    "promotion_id": promo.promotion_id,
                    "description": promo.description,
                    "discounted_price": promo.discounted_price,
                    "min_qty": promo.min_qty,
                    "starts_at": promo.starts_at,
                    "ends_at": promo.ends_at,
                    "observed_at": observed,
                }
                for promo in parsed
                if promo.item_code and promo.discounted_price > 0
            ]
            if promo_rows:
                storage.replace_store_promotions(chain, promo_rows)
                promotions = len(promo_rows)
        except Exception as exc:  # noqa: BLE001 - a promo failure is not a price failure
            logger.warning("%s: promotions failed (%s); prices kept", chain, exc)

    return FeedResult(chain, len(rows), age, newest.name, promotions=promotions)


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
            line = f"✅ {display_name(result.chain)}: {result.products} מוצרים"
            if result.promotions:
                line += f", {result.promotions} מבצעים"
            lines.append(line)
        else:
            lines.append(f"⚠️ {display_name(result.chain)}: {result.skipped_reason}")
    return "\n".join(lines)
