"""Shufersal cart automation via Playwright.

Selectors here were corrected against the live site on 2026-08-29 (see
"verified" notes below), replacing the original guesses. Two of those
guesses happened to be right — `.miglog-prod` and `js-add-to-cart` — but
the product-name selector and the `data-testid` variants were not, and
would have failed silently.

**Network:** both Shufersal and Tiv Taam refuse non-Israeli IPs, and the
server runs in France, so every browser here must go through the SOCKS5
proxy in `Config.playwright_proxy` (Tailscale userspace mode, exiting via
a device at home in Israel). Without it every page is a geo-block
placeholder that still returns HTTP 200 — so a missing proxy looks like
"the site changed its markup", not like a network problem. The adapter
therefore refuses to run without one rather than failing confusingly.

Design choices that are deliberate:
- Product identity comes from the card's `data-*` attributes rather than
  scraped text. The card carries `data-product-name`, `data-product-code`
  and `data-product-price`, which are stable and exact, while the visible
  text is wrapped in nested markup and varies by layout.
- Quantity is set on the quantity field, not by clicking "add" N times —
  clicking N times races the site's own cart updates.
- Never raises out of the public methods: one broken item must not abort
  a whole shopping cycle, so failures come back as CartAddResult(status=
  "error").

**Not yet verified:** adding to the cart requires a logged-in session.
Search, card parsing and candidate extraction were all confirmed against
the live site; the add/update click path was written from the real markup
but has not been executed with a real account, and `data-product-
purchasable` reads "false" for anonymous visitors.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from .base import StoreAdapter
from ..models import CartAddResult

logger = logging.getLogger(__name__)

BASE_URL = "https://www.shufersal.co.il"
SEARCH_URL_TEMPLATE = BASE_URL + "/online/he/search?text={query}"
ACCOUNT_URL = BASE_URL + "/online/he/myaccount"

# --- verified against the live site, 2026-08-29 -------------------------
# Each product tile is <li class="SEARCH tileBlock miglog-prod ...">
PRODUCT_CARD_SELECTOR = "li.miglog-prod"
# The tile's own attributes; far more reliable than reading nested text.
NAME_ATTRIBUTE = "data-product-name"
CODE_ATTRIBUTE = "data-product-code"
PRICE_ATTRIBUTE = "data-product-price"
# <button class="btn js-add-to-cart js-enable-btn miglog-btn-add">
ADD_TO_CART_SELECTOR = "button.js-add-to-cart"
# bootstrap-touchspin field holding the chosen quantity
QUANTITY_INPUT_SELECTOR = "input.js-qty-selector-input"
# Results render client-side, so the tiles must be waited for explicitly.
RESULTS_TIMEOUT_MS = 30_000
MAX_CANDIDATES = 5


class ShufersalAdapter(StoreAdapter):
    name = "shufersal"

    def __init__(self, storage_state_path: str, headless: bool = True, proxy: str = ""):
        from playwright.sync_api import sync_playwright  # lazy: only needed here

        state_path = Path(storage_state_path)
        if not state_path.exists():
            raise FileNotFoundError(
                f"No saved Shufersal session at {storage_state_path}. "
                "Run scripts/login_helper.py once first (see README)."
            )
        if not proxy:
            raise RuntimeError(
                "Shufersal blocks non-Israeli IPs and this server is in France, so a "
                "proxy is required. Set PLAYWRIGHT_PROXY (e.g. socks5://localhost:1055). "
                "Without it the site returns a geo-block page with HTTP 200, which looks "
                "like broken selectors rather than a blocked request."
            )

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=headless, proxy={"server": proxy}
        )
        self._context = self._browser.new_context(storage_state=str(state_path))
        self._page = self._context.new_page()

    def is_session_valid(self) -> bool:
        try:
            self._page.goto(ACCOUNT_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            logger.exception("Shufersal: failed to load account page")
            return False
        return "login" not in self._page.url.lower()

    def search_and_add(self, term: str, quantity: int = 1) -> CartAddResult:
        try:
            cards = self._search(term)
        except Exception as exc:  # noqa: BLE001 - never abort the whole cycle
            logger.exception("Shufersal: search failed for %r", term)
            return CartAddResult(
                item_name=term, store=self.name, status="error", detail=str(exc), quantity=quantity
            )

        count = len(cards)
        if count == 0:
            return CartAddResult(item_name=term, store=self.name, status="not_found", quantity=quantity)
        if count > 1:
            return CartAddResult(
                item_name=term,
                store=self.name,
                status="ambiguous",
                candidates=[c["name"] for c in cards[:MAX_CANDIDATES]],
                quantity=quantity,
            )
        return self._add(cards[0], term, quantity)

    def add_specific_product(self, product_label: str, quantity: int = 1) -> CartAddResult:
        """Add the exact product chosen from a previous ambiguous result."""
        try:
            cards = self._search(product_label)
            for card in cards:
                if card["name"] == product_label:
                    return self._add(card, product_label, quantity)
            return CartAddResult(
                item_name=product_label, store=self.name, status="not_found", quantity=quantity
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Shufersal: add_specific_product failed for %r", product_label)
            return CartAddResult(
                item_name=product_label,
                store=self.name,
                status="error",
                detail=str(exc),
                quantity=quantity,
            )

    def close(self) -> None:
        try:
            self._context.close()
            self._browser.close()
        finally:
            self._playwright.stop()

    # -- helpers -----------------------------------------------------------

    def _search(self, term: str) -> list[dict]:
        """Run a search and return one dict per product tile.

        `quote` matters: search terms are Hebrew, and an unencoded query
        string silently returns the wrong results.
        """
        self._page.goto(
            SEARCH_URL_TEMPLATE.format(query=quote(term)),
            wait_until="domcontentloaded",
            timeout=RESULTS_TIMEOUT_MS,
        )
        try:
            self._page.wait_for_selector(PRODUCT_CARD_SELECTOR, timeout=RESULTS_TIMEOUT_MS)
        except Exception:
            # A genuinely empty result set is normal, not an error.
            logger.info("Shufersal: no product tiles rendered for %r", term)
            return []
        return self._page.eval_on_selector_all(
            PRODUCT_CARD_SELECTOR,
            """els => els.map((e, i) => ({
                index: i,
                name: e.getAttribute('data-product-name') || '',
                code: e.getAttribute('data-product-code') || '',
                price: e.getAttribute('data-product-price') || '',
                purchasable: e.getAttribute('data-product-purchasable') === 'true',
            }))""",
        )

    def _add(self, card: dict, term: str, quantity: int) -> CartAddResult:
        name = card.get("name") or term
        try:
            tile = self._page.locator(PRODUCT_CARD_SELECTOR).nth(card["index"])
            if quantity > 1:
                self._set_quantity(tile, quantity)
            tile.locator(ADD_TO_CART_SELECTOR).first.click(timeout=15_000)
            self._page.wait_for_timeout(1_000)  # let the cart request settle
            return CartAddResult(
                item_name=name, store=self.name, status="added", quantity=quantity
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Shufersal: failed to add %r to cart", name)
            return CartAddResult(
                item_name=name, store=self.name, status="error", detail=str(exc), quantity=quantity
            )

    @staticmethod
    def _set_quantity(tile, quantity: int) -> None:
        """Set the tile's quantity field, firing the events the site listens for.

        The field is a bootstrap-touchspin input wired to JS handlers, so
        assigning `.value` alone leaves the site's own state on 1.
        """
        field = tile.locator(QUANTITY_INPUT_SELECTOR).first
        field.evaluate(
            """(el, qty) => {
                el.value = String(qty);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            quantity,
        )
