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
# NOTE: the hyphen matters. "/online/he/myaccount" (no hyphen) soft-404s —
# it still doesn't contain "login" in the URL, so is_session_valid() was
# passing against a 404 page rather than a real account page. Found and
# fixed 2026-08-29 while verifying headless login against a real account.
ACCOUNT_URL = BASE_URL + "/online/he/my-account"
CART_URL = BASE_URL + "/online/he/cart/cartsummary"

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
# On /online/he/cart/cartsummary, each line item is an
# article[data-product-code=...]; the global "ניקוי הסל" link
# (data-miglog-role="cart-remove-overlay-opener") has two DOM copies
# gated by responsive CSS and neither is ever actually clickable — this
# per-item (×) button, scoped to one product's article, is what works.
CART_LINE_ITEM_SELECTOR = 'article[data-product-code="{code}"]'
CART_ITEM_REMOVE_SELECTOR = 'a[data-miglog-role="cart-item-remover"]'
# Results render client-side, so the tiles must be waited for explicitly.
RESULTS_TIMEOUT_MS = 30_000
MAX_CANDIDATES = 5


class ShufersalAdapter(StoreAdapter):
    name = "shufersal"

    def __init__(
        self,
        storage_state_path: str,
        headless: bool = True,
        proxy: str = "",
        username: str = "",
        password: str = "",
    ):
        from playwright.sync_api import sync_playwright  # lazy: only needed here

        if not proxy:
            raise RuntimeError(
                "Shufersal blocks non-Israeli IPs and this server is in France, so a "
                "proxy is required. Set PLAYWRIGHT_PROXY (e.g. socks5://localhost:1055). "
                "Without it the site returns a geo-block page with HTTP 200, which looks "
                "like broken selectors rather than a blocked request."
            )

        self._storage_state_path = storage_state_path
        self._proxy = proxy
        self._headless = headless
        self._username = username
        self._password = password

        state_path = Path(storage_state_path)
        if not state_path.exists():
            # Credentials make the one-time manual login unnecessary: log in
            # now rather than failing and waiting for a human.
            self._login()

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=headless, proxy={"server": proxy}
        )
        self._context = self._browser.new_context(storage_state=str(state_path))
        self._page = self._context.new_page()

    def _login(self, browser=None) -> None:
        """Create a fresh session file from stored credentials.

        `browser` is passed once this adapter has one of its own: a second
        `sync_playwright()` inside a running one raises "Sync API inside
        the asyncio loop", so renewal has to reuse the live browser.
        """
        from ..login import login_and_save_session

        if not self._username or not self._password:
            raise FileNotFoundError(
                f"No saved Shufersal session at {self._storage_state_path} and no "
                "credentials configured to create one. Either set "
                "SHUFERSAL_USERNAME/SHUFERSAL_PASSWORD, or run "
                "scripts/login_helper.py once (see README)."
            )
        Path(self._storage_state_path).parent.mkdir(parents=True, exist_ok=True)
        logger.info("Shufersal: logging in to create a new session")
        login_and_save_session(
            username=self._username,
            password=self._password,
            output_path=self._storage_state_path,
            proxy=self._proxy,
            headless=self._headless,
            browser=browser,
        )

    def ensure_session(self) -> bool:
        """Re-login if the saved session has expired. Returns True if usable.

        This is what keeps the project's "minimum user dependency" promise:
        a session that expires mid-life gets replaced silently rather than
        surfacing as "please log in again" on every cycle. Only a genuine
        credential problem (or an OTP challenge) still needs the user.
        """
        if self.is_session_valid():
            return True
        if not self._username or not self._password:
            logger.warning("Shufersal session expired and no credentials to renew it")
            return False
        try:
            self._login(browser=self._browser)
        except Exception:
            logger.exception("Shufersal: re-login failed")
            return False
        # Swap in the refreshed cookies without tearing down the browser.
        self._context.close()
        self._context = self._browser.new_context(storage_state=self._storage_state_path)
        self._page = self._context.new_page()
        return self.is_session_valid()

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

    def add_specific_product(
        self, product_label: str, quantity: int = 1, product_code: str = "", search_term: str = ""
    ) -> CartAddResult:
        """Add one exact product, previously chosen or remembered.

        Matching prefers `product_code`: names are not unique and the
        chain re-words them, so a remembered choice keyed only on the
        label would quietly start matching a different tub of cottage
        cheese. `search_term` lets a remembered choice be re-found by the
        original query, since searching the full product name sometimes
        returns nothing.
        """
        try:
            cards = self._search(search_term or product_label)
            if product_code:
                for card in cards:
                    if card["code"] == product_code:
                        return self._add(card, card["name"] or product_label, quantity)
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

    def remove_item(self, product_code: str) -> bool:
        """Remove one line item from the real cart by product code.

        Must go through the real cart page — the item isn't necessarily
        addressable from wherever the caller currently is. Returns False
        (never raises) if the item wasn't found or the click didn't
        register, so a caller can decide whether to retry or report it.
        """
        try:
            self._page.goto(CART_URL, wait_until="domcontentloaded", timeout=30_000)
            article = self._page.locator(CART_LINE_ITEM_SELECTOR.format(code=product_code))
            # The cart page renders its line items after an async discount
            # computation ("מחשבים את ההנחות..."); domcontentloaded fires
            # well before that, so the article isn't in the DOM yet without
            # this wait.
            try:
                article.first.wait_for(state="attached", timeout=15_000)
            except Exception:
                return False
            if article.count() == 0:
                return False
            try:
                article.locator(CART_ITEM_REMOVE_SELECTOR).first.click(timeout=10_000)
            except Exception:
                # The click sometimes throws even when it worked: removing
                # the line item shifts the DOM mid-click and Playwright's
                # own stability wait can lose the element. Don't trust the
                # exception either way — check what actually happened.
                logger.info(
                    "Shufersal: remove click for %r raised; verifying actual state", product_code
                )
            self._page.wait_for_timeout(1_000)  # let the removal request settle
            return article.count() == 0
        except Exception:
            logger.exception("Shufersal: failed to remove %r from cart", product_code)
            return False

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
                item_name=name,
                store=self.name,
                status="added",
                quantity=quantity,
                product_code=card.get("code", ""),
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
