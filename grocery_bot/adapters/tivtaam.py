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

# **Tiv Taam has no cart page.** Verified 2026-09-02 against the real
# account: https://www.tivtaam.co.il/cart redirects to the homepage — with
# an empty cart *and* with two items in it, so the redirect is not an
# empty-cart behaviour. The cart is a side panel opened from the header
# (`sideNavCtrl.toggleCart()`), and its contents live in the header
# summary whether or not the panel is open. Shufersal's /cart/cartsummary
# has no equivalent here, so anything that wants "the cart" reads the
# header rather than navigating.
CART_URL = SITE

# The header cart summary: "N מוצרים בעגלה", "סך הכל", "₪39.90". Reading
# the element rather than scanning the page body matters — the body says
# "33 מוצרים" and "39 מוצרים" in unrelated category links, and "מוצרים
# בפיקוח" in the footer, any of which a loose regex picks up first.
CART_SUMMARY_SELECTOR = "[ng-click*='toggleCart'], .summary.clean-cart-button"
# The cart header is identified by its own wording, not by position. A
# bare `.first` over the selector above picks whichever candidate comes
# first in the DOM, which is not always the cart — and when it picked
# another one, the count read 0 with a full cart. That made `clear_cart`
# report success on a cart it had not emptied, and made a successful add
# report "the click did not change the cart".
CART_SUMMARY_MARKERS = ("בעגלה", "סך הכל")
# Per-line remove, from the panel's own markup:
# ng-click="...announceCartLineRemoved(line); $root.cart.removeLine(line)"
CART_LINE_REMOVE_SELECTOR = "button.delete.hover-action"
# The panel's broom, ng-click="...sideNavCtrl.clearCart()". Kept for
# reference: clicking it did *not* empty the cart in testing, so
# `clear_cart` removes line by line instead.
CART_CLEAR_SELECTOR = ".clean-cart-icon"
CART_TOGGLE_SELECTOR = ".toggle-cart, .cart-icon"
# A guard on the removal loop, not a cart-size limit. An unbounded
# "until the cart is empty" loop cannot tell "not finished" from "never
# going to finish" — the same trap that once waited 23 hours for a file.
MAX_CART_LINES_TO_CLEAR = 60
# The header count lags the click by a second or two, so an add is
# verified by polling rather than by one read after a fixed sleep.
ADD_VERIFY_POLLS = 6
ADD_VERIFY_POLL_MS = 2000
# Clearing reloads and re-opens the panel between rounds; the list
# re-renders as lines go, and one pass stopped after the first item.
CLEAR_ROUNDS = 5
PANEL_RENDER_POLLS = 6

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
            # Counted on the line elements, never on the header. The
            # header count lags the click and was seen reading 0 on a cart
            # that held an item, which reported real adds as "the click
            # did not change the cart" — on 2026-09-02 two products sat in
            # the cart while both had been reported as failures. Under-
            # reporting an add is the expensive direction: the item gets
            # re-queued and the household is told it never went in.
            before = self._cart_line_count()
            button.first.click(timeout=15000)
            after = before
            for _ in range(ADD_VERIFY_POLLS):
                self._page.wait_for_timeout(ADD_VERIFY_POLL_MS)
                after = self._cart_line_count()
                if after > before:
                    break

            for _ in range(max(0, int(quantity) - 1)):
                try:
                    button.first.click(timeout=8000)
                    self._page.wait_for_timeout(2000)
                except Exception:
                    break
            if after <= before:
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

    def _summary_text(self) -> str | None:
        """The header cart summary's own text, or None if it is not there.

        Scoped to the element on purpose. The old version searched the
        whole page body for `(\\d+)\\s*מוצרים`, which the category nav
        ("33 מוצרים", "39 מוצרים") and the footer ("מוצרים בפיקוח") also
        satisfy — and, worse, returned 0 when it matched nothing at all.
        That turned "I could not read the cart" into "the cart is empty",
        which is the same failure this project has written up twice: a
        check that returns the same answer whether or not the thing is
        true. None now means unreadable and 0 means empty, and the two are
        no longer the same value.
        """
        try:
            nodes = self._page.locator(CART_SUMMARY_SELECTOR)
            best = None
            for index in range(min(nodes.count(), 10)):
                try:
                    text = nodes.nth(index).inner_text(timeout=3_000)
                except Exception:
                    continue
                if not text:
                    continue
                if any(marker in text for marker in CART_SUMMARY_MARKERS):
                    return text
                best = best or text
            # Nothing carried the cart's wording. Returning the first
            # candidate anyway would be the old bug — a number read off
            # some other element — so this is unreadable, not empty.
            return None if best is None else (best if "מוצרים" in best else None)
        except Exception:
            return None

    def _cart_count(self) -> int | None:
        """How many products the cart bar reports, or None if unreadable."""
        import re

        text = self._summary_text()
        if text is None:
            return None
        found = re.search(r"(\d+)\s*מוצרים", text)
        return int(found.group(1)) if found else 0

    def _cart_line_count(self) -> int:
        """How many line elements the cart holds, panel open or shut.

        The authoritative signal, and the one an add is verified against.
        The header count lags a click and was observed reading 0 on a cart
        that held an item — which reported two successful adds as
        failures. A `.product-in-cart` element is the line itself: it is
        present in the DOM whether or not the panel has slid into view, so
        this costs nothing and does not need the panel opened.
        """
        try:
            return self._page.locator(".product-in-cart").count()
        except Exception:
            return 0

    def cart_summary(self) -> dict:
        """Read the cart the household is about to pay for.

        Same contract as the Shufersal adapter so the hand-off renderer
        needs no per-chain branching: {ok, items, total, url}. The total is
        the panel's own "₪39.90 כולל דמי משלוח", which already includes
        delivery — summing what we added would quietly disagree with what
        they are about to pay.

        Line names come from the panel, which must be opened to render
        them; the count and total are in the header regardless, so a cart
        that will not open still yields a usable total rather than
        nothing.

        Never raises, and never touches the checkout control sitting in
        the same panel (`.button.highlight.order` / `.checkout`). Reading
        the total is explicitly allowed; going further into that flow is
        not.
        """
        import re

        try:
            # Load fresh rather than trusting whatever this adapter was
            # last looking at: a summary taken after a `clear_cart`
            # otherwise reported the pre-clear total from a stale DOM.
            self._reopen()
            text = self._summary_text()
            if text is None:
                return {"ok": False, "items": [], "total": None, "url": CART_URL}

            count_match = re.search(r"(\d+)\s*מוצרים", text)
            count = int(count_match.group(1)) if count_match else 0

            total = None
            price_match = re.search(r"₪\s*([\d,]+(?:\.\d+)?)", text)
            if price_match:
                total = float(price_match.group(1).replace(",", ""))

            items = self._cart_line_names()
            if not items and count:
                # The panel would not open, but the header still knows how
                # many lines there are. Say so with placeholders rather
                # than reporting an empty cart with a non-zero total.
                items = [{"name": "", "qty": ""} for _ in range(count)]
            return {"ok": True, "items": items, "total": total, "url": CART_URL}
        except Exception:
            logger.exception("Tiv Taam: could not read the cart")
            return {"ok": False, "items": [], "total": None, "url": CART_URL}

    def _cart_line_names(self) -> list[dict]:
        """Open the cart panel and read its line items. [] if it will not.

        A line renders as `qty | brand | name | size | price`, so the
        product name is the third row of the container's text and the
        quantity is the first. Reading them positionally rather than by
        class because only the container carries a stable class
        (`.product-in-cart`); the rows inside it do not.
        """
        try:
            if not self._open_cart_panel():
                return []
            return self._page.evaluate(
                """() => Array.from(
                    document.querySelectorAll('.product-in-cart')
                ).map(line => {
                    const parts = (line.innerText || '')
                        .split('\\n').map(s => s.trim()).filter(Boolean);
                    return {
                        qty: parts[0] || '',
                        brand: parts[1] || '',
                        name: parts[2] || '',
                        price: parts[4] || '',
                    };
                })"""
            )
        except Exception:
            logger.debug("Tiv Taam: cart panel would not open", exc_info=True)
            return []

    def clear_cart(self) -> bool:
        """Empty the cart, and verify it actually emptied.

        Added 2026-09-02. HANDOFF claimed "search, add, verify, clear" was
        verified against the real account, but no clear method existed
        here — whatever clearing happened that day was done by hand. The
        control is the panel's own broom (`sideNavCtrl.clearCart()`).

        Returns False rather than raising if the cart could not be
        emptied, and never reports success on a click alone: the count is
        read back, because a click that silently does nothing is the
        failure mode this adapter already guards against on the way in.
        """
        try:
            # Each round reloads the site and re-opens the panel before
            # touching anything. That outer reload is what made this work:
            # removing a line re-renders the list, and continuing to click
            # into a half-updated panel silently stopped after the first
            # item while reporting the cart empty.
            for _ in range(CLEAR_ROUNDS):
                self._reopen()
                if not self._page.locator(".product-in-cart").count():
                    return True
                if not self._open_cart_panel():
                    continue

                # Per-line removal rather than the panel's broom
                # (`sideNavCtrl.clearCart()`). The broom was tried first
                # and did not empty the cart; each line's own delete
                # button did. Re-reading the buttons every iteration
                # matters — the list re-renders, so a locator captured up
                # front goes stale.
                for _ in range(MAX_CART_LINES_TO_CLEAR):
                    buttons = self._page.locator(CART_LINE_REMOVE_SELECTOR)
                    if not buttons.count():
                        break
                    try:
                        buttons.first.click(timeout=8_000)
                    except Exception:
                        break
                    self._page.wait_for_timeout(3_500)

            # Verify on the line elements, not on the header count. The
            # header read 0 on a cart that still held an item, which made
            # an earlier version of this method report success on a cart
            # it had not emptied — the worst possible lie for a method
            # whose whole job is leaving the household's cart as it found
            # it. A line element is the item itself.
            self._reopen()
            return not self._page.locator(".product-in-cart").count()
        except Exception:
            logger.exception("Tiv Taam: could not clear the cart")
            return False

    def _open_cart_panel(self) -> bool:
        """Make the cart panel actually render. False if it would not.

        The panel's elements are in the DOM before it slides in, and a
        hidden element has no text and cannot be clicked — so "the line
        exists" is not "the panel is open". Everything that touches a line
        goes through here, because the delete buttons are unclickable
        until this returns True.
        """
        lines = self._page.locator(".product-in-cart")

        def rendered() -> bool:
            try:
                return bool(
                    lines.count() and lines.first.inner_text(timeout=2_000).strip()
                )
            except Exception:
                return False

        if rendered():
            return True
        toggle = self._page.locator(CART_TOGGLE_SELECTOR).first
        if not toggle.count():
            return False
        try:
            # The control *toggles*: clicking it on an already-open panel
            # closes it, which is how a freshly-filled cart came back with
            # the right number of lines and every field blank.
            toggle.click(timeout=10_000)
        except Exception:
            return False
        for _ in range(PANEL_RENDER_POLLS):
            if rendered():
                return True
            self._page.wait_for_timeout(2_000)
        return False

    def _reopen(self) -> None:
        """Load the site fresh, discarding whatever the page last showed.

        `_open()` returns immediately once the site has been visited, so
        every read after an interaction saw a DOM that might not have
        caught up — the single cause behind a summary reporting a
        pre-clear total, a successful add reported as a failure, and a
        clear reporting success on a cart it had not emptied.
        """
        self._opened = False
        self._open()
        self._page.wait_for_timeout(4_000)

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
