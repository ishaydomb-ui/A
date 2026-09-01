"""Fill a Tiv Taam cart. Never pay for it.

Why this exists: the household's 7% loadable-card discount and their
TivCoins both apply here and not at Shufersal, so a basket that could go
to Tiv Taam is worth more there. Until now this project could read Tiv
Taam's prices and account but could only fill a Shufersal cart, which
meant "add it" always meant Shufersal — including for a deal spotted
here.

Browser-driven rather than API-driven, deliberately. The add-to-cart call
was captured from the site's own traffic and is a plain POST:

    POST /v2/retailers/1062/branches/924/carts/<cartId>?appId=4&loyalty=254
    {"lines":[{"quantity":1,"soldBy":null,"retailerProductId":N,"type":1}], ...}

but reproducing it needs a cart id that is created and rotated by the
site, and the stored bearer token is rejected on that route where the
browser's own session is not. Chasing that would buy speed and cost
reliability on the one operation in this project that touches real money.

**The hard rule, restated because this is the file where it matters:**
this adds to a cart and stops. There is no checkout method here and there
must never be one. Self-Point exposes `_checkout`; it is not called, not
wrapped, and not reachable from anything below.
"""
from __future__ import annotations

import logging
import os

from ..models import CartAddResult
from .base import StoreAdapter

logger = logging.getLogger(__name__)

SITE = "https://www.tivtaam.co.il/"
CART_URL = "https://www.tivtaam.co.il/cart"

# Cookie consent sits over the page and silently eats the first click on
# anything, which reads as "the add button did nothing".
CONSENT_LABELS = ("קבל את כל", "אישור", "סגור")
CONSENT_DIALOG = ".cookie-wall"
CONSENT_ATTEMPTS = 4

# A tile carries its name and its own add button; the two are matched by
# index rather than by walking the DOM, because the markup nests
# differently for offers, carousels and search results.
# The search results are the autocomplete dropdown, and each row carries
# both its own name and its own add button. Scoping to the row removes
# index arithmetic entirely — which mattered: the flat lists were 27 names
# against 26 buttons, because a product with no add button (out of stock,
# or already in the cart) shifts every later pairing by one, and clicking
# "the button at index n" then adds a different product.
ROW_SELECTOR = ".autocomplete-product-row"
ROW_NAME_SELECTOR = '[class*="name"]'
ADD_SELECTOR = ".add-to-cart"
# The placeholder is set by the Angular app after load, so matching on it
# is a race; the input type is present from the start.
SEARCH_SELECTOR = 'input[type="search"]'


# Two results that both look right mean the household should choose. More
# than this and the search term was too vague to act on at all.
MAX_CANDIDATES = 6

# Results arrive asynchronously and the page is never empty while they do,
# so the wait is on relevance rather than on time.
SEARCH_POLLS = 8
SEARCH_POLL_MS = 2500


class TivTaamAdapter(StoreAdapter):
    """Search Tiv Taam and put things in the real cart. No payment surface."""

    name = "tivtaam"

    def __init__(
        self,
        storage_state_path: str,
        headless: bool = True,
        proxy: str = "",
        **_ignored,
    ):
        from playwright.sync_api import sync_playwright

        if not proxy:
            # The geo-block returns a plausible page with HTTP 200, so
            # without the Israeli exit this fails like broken selectors.
            raise RuntimeError(
                "Tiv Taam geo-blocks this server; PLAYWRIGHT_PROXY is required. "
                "Without it the site answers 200 with a block page."
            )
        if not os.path.exists(storage_state_path):
            raise RuntimeError(
                f"no Tiv Taam session at {storage_state_path} — run "
                "scripts/selfpoint_login.py tivtaam"
            )

        # A persistent profile rather than a fresh context per run. The
        # cookie wall is the reason: a fresh context has not accepted
        # cookies, so the overlay renders on every page and silently eats
        # the click on the search box — reported by Playwright as the box
        # being "visible, enabled and stable" and then timing out. The
        # profile written by scripts/selfpoint_login.py has already
        # accepted, and carries the login besides.
        profile = os.path.join(os.path.dirname(storage_state_path), "tivtaam_profile")
        self._playwright = sync_playwright().start()
        self._browser = None
        if os.path.isdir(profile):
            self._context = self._playwright.chromium.launch_persistent_context(
                profile, headless=headless, proxy={"server": proxy},
                args=["--no-sandbox"], viewport={"width": 1280, "height": 900},
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        else:
            self._browser = self._playwright.chromium.launch(
                headless=headless, proxy={"server": proxy}, args=["--no-sandbox"]
            )
            self._context = self._browser.new_context(
                storage_state=storage_state_path, viewport={"width": 1280, "height": 900}
            )
            self._page = self._context.new_page()
        self._opened = False

    # -- session ----------------------------------------------------------

    def _open(self) -> None:
        if self._opened:
            return
        self._page.goto(SITE, wait_until="domcontentloaded", timeout=90000)
        self._page.wait_for_timeout(4000)
        self._dismiss_consent()
        self._opened = True

    def _dismiss_consent(self) -> None:
        """Close the cookie wall, and confirm it actually closed.

        It renders after the page and swallows pointer events for
        everything beneath it, so a click on the search box reports
        "element is visible, enabled and stable" and then times out
        against an invisible overlay. Firing a click at a label and
        hoping is not enough — the dialog is polled for and its
        disappearance is checked.
        """
        wall = self._page.locator(CONSENT_DIALOG)
        for _ in range(CONSENT_ATTEMPTS):
            try:
                if not wall.count() or not wall.first.is_visible():
                    return
            except Exception:
                return
            for label in CONSENT_LABELS:
                try:
                    wall.first.get_by_text(label, exact=False).first.click(timeout=2500)
                    break
                except Exception:
                    continue
            self._page.wait_for_timeout(1500)
        # Still there: remove it rather than fail every later click. The
        # household has already accepted cookies in this session; the
        # overlay is a rendering artefact of a fresh context.
        try:
            self._page.evaluate(
                "() => document.querySelectorAll('.dialog-wrapper, .cookie-wall')"
                ".forEach(e => e.remove())"
            )
        except Exception:
            logger.warning("cookie wall could not be dismissed or removed")

    def is_session_valid(self) -> bool:
        """Checked on content, never on a URL.

        A logged-out Tiv Taam serves the same pages, so the only reliable
        signal is the account's own name in the header.
        """
        try:
            self._open()
            body = self._page.locator("body").inner_text()
            return "התנתקות" in body or "היי" in body or "האזור האישי" in body
        except Exception:
            logger.exception("Tiv Taam session check failed")
            return False

    # -- searching --------------------------------------------------------

    def _rows(self, term: str):
        """Search rows matching a term, as (row locator, product name).

        Typing is enough; the dropdown opens on input and pressing Enter
        navigates away from it. Waits on results that relate to the term
        rather than on a fixed delay, because the page behind the dropdown
        is already full of named products and an early read returns the
        household's usual shopping list as though it were a search result.
        """
        self._open()
        self._dismiss_consent()
        box = self._page.locator(SEARCH_SELECTOR).first
        box.wait_for(state="visible", timeout=30000)
        box.click(timeout=15000)
        box.fill(term, timeout=15000)

        needle = term.strip()[:4]
        for _ in range(SEARCH_POLLS):
            self._page.wait_for_timeout(SEARCH_POLL_MS)
            rows = self._page.locator(ROW_SELECTOR)
            found = []
            for index in range(rows.count()):
                row = rows.nth(index)
                label = row.locator(ROW_NAME_SELECTOR).first
                if not label.count():
                    continue
                name = label.inner_text().strip()
                if needle in name:
                    found.append((row, name))
            if found:
                return found
        return []

    def search_and_add(self, term: str, quantity: int = 1) -> CartAddResult:
        try:
            rows = self._rows(term)
        except Exception as exc:
            logger.exception("Tiv Taam search failed for %r", term)
            return CartAddResult(
                item_name=term, store=self.name, status="error", detail=str(exc)[:200]
            )

        if not rows:
            return CartAddResult(item_name=term, store=self.name, status="not_found")
        if len(rows) > 1:
            # Real alternatives are the household's choice. Taking the
            # first silently is how a cart fills with the wrong fat
            # percentage.
            return CartAddResult(
                item_name=term,
                store=self.name,
                status="ambiguous",
                candidates=[name for _, name in rows][:MAX_CANDIDATES],
                quantity=quantity,
            )
        row, name = rows[0]
        return self._add_row(row, name, quantity)

    def add_specific_product(
        self,
        product_label: str,
        quantity: int = 1,
        product_code: str = "",
        search_term: str = "",
    ) -> CartAddResult:
        """Add one named product, after the household has chosen it."""
        try:
            rows = self._rows(search_term or product_label)
        except Exception as exc:
            return CartAddResult(
                item_name=product_label, store=self.name, status="error",
                detail=str(exc)[:200],
            )
        for row, name in rows:
            if name == product_label:
                return self._add_row(row, name, quantity)
        return CartAddResult(
            item_name=product_label, store=self.name, status="not_found"
        )

    def _add_row(self, row, name: str, quantity: int) -> CartAddResult:
        """Click the add button belonging to this row, then verify the cart.

        The click is never trusted on its own: the cart count is read
        before and after, because a click that silently does nothing is a
        failure this project has already hit at the other chain.
        """
        try:
            button = row.locator(ADD_SELECTOR)
            if not button.count():
                # No control on the row at all — the product exists but
                # cannot be bought right now. That is a real answer.
                return CartAddResult(
                    item_name=name, store=self.name, status="not_found",
                    detail="no add control on the row — probably out of stock",
                )
            before = self._cart_count()
            button.first.click(timeout=15000)
            self._page.wait_for_timeout(4000)

            for _ in range(max(0, int(quantity) - 1)):
                try:
                    button.first.click(timeout=8000)
                    self._page.wait_for_timeout(2000)
                except Exception:
                    break

            after = self._cart_count()
            if before is not None and after is not None and after <= before:
                return CartAddResult(
                    item_name=name, store=self.name, status="error",
                    detail="the click did not change the cart",
                )
            return CartAddResult(
                item_name=name, store=self.name, status="added", quantity=quantity
            )
        except Exception as exc:
            logger.exception("Tiv Taam add failed for %r", name)
            return CartAddResult(
                item_name=name, store=self.name, status="error", detail=str(exc)[:200]
            )

    def _cart_count(self) -> int | None:
        """How many products the cart bar reports, or None if unreadable."""
        import re

        try:
            found = re.search(r"(\d+)\s*מוצרים", self._page.locator("body").inner_text())
            return int(found.group(1)) if found else 0
        except Exception:
            return None

    # -- teardown ---------------------------------------------------------

    def close(self) -> None:
        shutdowns = [self._context.close]
        if self._browser is not None:
            shutdowns.append(self._browser.close)
        shutdowns.append(self._playwright.stop)
        for shut in shutdowns:
            try:
                shut()
            except Exception:
                pass
