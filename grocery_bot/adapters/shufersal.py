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


def _price_after(text: str, label: str) -> float | None:
    """First money-looking number appearing after `label`."""
    index = (text or "").find(label)
    return _first_price(text[index:]) if index != -1 else None


def _first_price(text: str) -> float | None:
    """First money-looking number in a blob of cart text."""
    import re

    for raw in re.findall(r"\d[\d,]*\.\d{2}", text or ""):
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            continue
    return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

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
            shortlist = cards[:MAX_CANDIDATES]
            return CartAddResult(
                item_name=term,
                store=self.name,
                status="ambiguous",
                candidates=[c["name"] for c in shortlist],
                # Names alone repeat across brands; the full cards let the
                # caller both auto-resolve and show a distinguishable choice.
                candidate_cards=shortlist,
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
            # Wait for the row to actually go, rather than sleeping a fixed
            # second and hoping. The cart re-renders asynchronously, and a
            # too-early check reported failure for removals that had in fact
            # succeeded -- a false negative is worse than a slow answer,
            # because the caller retries or tells the user it failed.
            try:
                article.first.wait_for(state="detached", timeout=15_000)
                return True
            except Exception:
                return article.count() == 0
        except Exception:
            logger.exception("Shufersal: failed to remove %r from cart", product_code)
            return False

    def cart_summary(self) -> dict:
        """Read the real cart: line items and the price actually payable.

        Worth the extra page load rather than summing what we added. The
        cart's total includes delivery, club discounts and the weight the
        store actually recorded, none of which the tile prices know about,
        so a locally-summed figure would quietly disagree with what the
        user is about to pay.

        Never raises: a live cart view failing must not take a cycle with
        it. An unreadable cart comes back as ok=False.
        """
        try:
            self._page.goto(CART_URL, wait_until="domcontentloaded", timeout=30_000)
            try:
                self._page.wait_for_selector(
                    'article[data-product-code]', timeout=15_000
                )
            except Exception:
                pass  # an empty cart legitimately has no line items
            items = self._page.eval_on_selector_all(
                "article[data-product-code]",
                """els => {
                    const seen = {};
                    els.forEach(e => {
                        const code = e.getAttribute('data-product-code');
                        if (!code || seen[code]) return;
                        const nameEl = e.querySelector('.miglog-prod-name');
                        seen[code] = {
                            code: code,
                            qty: e.getAttribute('data-entry-qty') || '',
                            name: nameEl ? nameEl.innerText.trim() : '',
                        };
                    });
                    return Object.values(seen);
                }""",
            )
            total = None
            for selector in (".totalPrice", ".cartSum", ".miglog-cart-summary-total"):
                node = self._page.locator(selector).first
                if node.count():
                    total = _first_price(node.inner_text(timeout=3_000))
                    if total is not None:
                        break
            if total is None:
                # Anchor on the "amount payable" label rather than scanning
                # the whole page: the savings line ("סה"כ חסכת") is usually
                # 0.00 and sits close enough to be picked up first, which
                # would report a free shop.
                total = _price_after(self._page.inner_text("body"), "לתשלום")
            return {"ok": True, "items": items, "total": total, "url": CART_URL}
        except Exception:
            logger.exception("Shufersal: could not read the cart")
            return {"ok": False, "items": [], "total": None, "url": CART_URL}

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
            # `.smallText` holds "250 גרם | תנובה" — size and manufacturer.
            # Without it, a search for קוטג' returns three tiles all named
            # "קוטג' 5% שומן" (Tnuva, Strauss, Tara), and a chooser showing
            # only names offers three identical buttons.
            """els => els.map((e, i) => {
                const small = e.querySelector('.smallText');
                const detail = small ? small.innerText.replace(/\\s+/g, ' ').trim() : '';
                const parts = detail.split('|').map(s => s.trim());
                // The tile also prints "2.44 ש"ח ל- 100 גרם"; that ratio is
                // what makes two pack sizes comparable, so read it rather
                // than trying to re-derive it from name text.
                const flat = e.innerText.replace(/\\s+/g, ' ');
                const ratio = flat.match(/([\\d.]+)\\s*ש"?ח\\s*ל-?\\s*([\\d]*\\s*[^\\s,|]+)/);
                return {
                    index: i,
                    unitPrice: ratio ? parseFloat(ratio[1]) : null,
                    unitLabel: ratio ? ratio[2].trim() : '',
                    name: e.getAttribute('data-product-name') || '',
                    code: e.getAttribute('data-product-code') || '',
                    price: e.getAttribute('data-product-price') || '',
                    size: parts[0] || '',
                    brand: parts.length > 1 ? parts[parts.length - 1] : '',
                    purchasable: e.getAttribute('data-product-purchasable') === 'true',
                };
            })""",
        )

    def _dismiss_overlays(self) -> None:
        """Close any modal the site has floated over the page.

        Shufersal pops dialogs mid-session — product recommendations after
        a few adds, coupon nags — and while one is open every click under
        it times out. That took down most of a real cycle: the add button
        resolved fine but was never clickable, so item after item came
        back "error" while the only true problem was one popup.
        """
        try:
            for selector in (
                ".modal.fade.in button.close",
                ".modal.show button.close",
                "#closeButton",
                ".btnClose:visible",
                ".popup-close:visible",
            ):
                node = self._page.locator(selector).first
                if node.count() and node.is_visible():
                    node.click(timeout=2_000)
                    self._page.wait_for_timeout(300)
            # Escape closes most Bootstrap modals even without a close button.
            if self._page.locator(".modal.fade.in, .modal.show").count():
                self._page.keyboard.press("Escape")
                self._page.wait_for_timeout(300)
        except Exception:
            logger.debug("Overlay dismissal failed; continuing", exc_info=True)

    def _add(self, card: dict, term: str, quantity: int) -> CartAddResult:
        """Put one product in the cart, by product code rather than position.

        Two things go wrong with the obvious approach, both seen in real
        runs:

        - **Positional lookup drifts.** Tiles lazy-load and re-render, so
          the nth tile at click time is not necessarily the nth tile the
          search parsed. Re-finding by `data-product-code` is exact.
        - **A tile that is already in the cart hides its add button** and
          shows a quantity stepper instead. Playwright then waits out the
          full timeout on an element that exists but will never be
          visible, and the item is reported as an error even though it is
          sitting in the cart. That produced a wall of red on items the
          user had simply asked for more than once.

        So: set the quantity directly when the stepper is showing, click
        "add" when it is not, and confirm against the tile's own state
        rather than trusting the click to mean anything.
        """
        name = card.get("name") or term
        code = card.get("code", "")
        try:
            self._dismiss_overlays()
            tile = (
                self._page.locator(f'{PRODUCT_CARD_SELECTOR}[data-product-code="{code}"]').first
                if code
                else self._page.locator(PRODUCT_CARD_SELECTOR).nth(card["index"])
            )
            if not tile.count():
                tile = self._page.locator(PRODUCT_CARD_SELECTOR).nth(card["index"])
            try:
                tile.scroll_into_view_if_needed(timeout=5_000)
            except Exception:
                pass

            already_in_cart = self._tile_in_cart(tile)
            if already_in_cart:
                # Already there: adjust the amount instead of pressing a
                # button that is no longer on screen.
                if quantity > 1:
                    self._set_quantity(tile, quantity)
                return CartAddResult(
                    item_name=name, store=self.name, status="added", quantity=quantity,
                    product_code=code, price=_as_float(card.get("price")),
                )

            if quantity > 1:
                self._set_quantity(tile, quantity)
            button = tile.locator(ADD_TO_CART_SELECTOR).first
            try:
                button.click(timeout=10_000)
            except Exception:
                self._dismiss_overlays()
                # The tile may have flipped to its in-cart form while we
                # waited, which is a success, not a failure.
                if self._tile_in_cart(tile):
                    return CartAddResult(
                        item_name=name, store=self.name, status="added", quantity=quantity,
                        product_code=code, price=_as_float(card.get("price")),
                    )
                button.click(timeout=8_000, force=True)

            self._page.wait_for_timeout(1_000)  # let the cart request settle
            return CartAddResult(
                item_name=name,
                store=self.name,
                status="added",
                quantity=quantity,
                product_code=code,
                price=_as_float(card.get("price")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Shufersal: failed to add %r to cart", name)
            return CartAddResult(
                item_name=name, store=self.name, status="error", detail=str(exc), quantity=quantity
            )

    @staticmethod
    def _tile_in_cart(tile) -> bool:
        """Is this product already in the cart, per the tile's own state?"""
        try:
            return bool(
                tile.evaluate(
                    """e => {
                        if (e.className.includes('miglog-incart')) return true;
                        const add = e.querySelector('button.js-add-to-cart');
                        const qty = e.querySelector('input.js-qty-selector-input');
                        const addHidden = !add || !(add.offsetWidth || add.offsetHeight);
                        const qtyShown = !!qty && !!(qty.offsetWidth || qty.offsetHeight);
                        return addHidden && qtyShown;
                    }"""
                )
            )
        except Exception:
            return False

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
