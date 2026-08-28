"""Best-effort Shufersal cart automation via Playwright.

IMPORTANT — written without access to a live Shufersal account or session,
so the URL and CSS selectors below are best-guess placeholders based on
typical grocery-site markup, not verified against the real site. They will
need correcting after one real supervised run (headless=False, or dumping
page.content() on failure) — see README "Tuning the Shufersal adapter" for
how to do that without needing to babysit every subsequent run.

Design choices that ARE deliberate, not placeholders:
- One browser context per call to `search_and_add` and one per call to
  `add_specific_product`, opened from a saved `storage_state` file rather
  than a fresh login. This keeps the user out of the loop entirely for
  routine cart updates (the whole point per the project goals) and avoids
  holding a browser open between the infrequent (weekly-ish) cycles.
- Never raises out of search_and_add/add_specific_product: a broken
  selector or a network hiccup on one item must not abort the rest of the
  cycle, so failures come back as a CartAddResult(status="error").
"""
from __future__ import annotations

import logging
from pathlib import Path

from .base import StoreAdapter
from ..models import CartAddResult

logger = logging.getLogger(__name__)

BASE_URL = "https://www.shufersal.co.il"
SEARCH_URL_TEMPLATE = BASE_URL + "/online/he/search?text={query}"
ACCOUNT_URL = BASE_URL + "/online/he/my-account"

# Best-guess selectors — see module docstring. Kept as constants so a live
# tuning pass touches one place.
PRODUCT_CARD_SELECTOR = "[data-testid='product-item'], .miglog-prod"
PRODUCT_NAME_SELECTOR = ".miglog-prod-name, [data-testid='product-name']"
ADD_TO_CART_SELECTOR = "button:has-text('הוספה לסל'), button.js-add-to-cart"
MAX_CANDIDATES = 5


class ShufersalAdapter(StoreAdapter):
    name = "shufersal"

    def __init__(self, storage_state_path: str, headless: bool = True):
        from playwright.sync_api import sync_playwright  # lazy: only needed here

        state_path = Path(storage_state_path)
        if not state_path.exists():
            raise FileNotFoundError(
                f"No saved Shufersal session at {storage_state_path}. "
                "Run scripts/login_helper.py once first (see README)."
            )
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._context = self._browser.new_context(storage_state=str(state_path))
        self._page = self._context.new_page()

    def is_session_valid(self) -> bool:
        try:
            self._page.goto(ACCOUNT_URL, wait_until="domcontentloaded", timeout=15_000)
        except Exception:
            logger.exception("Shufersal: failed to load account page")
            return False
        return "login" not in self._page.url.lower()

    def search_and_add(self, term: str, quantity: int = 1) -> CartAddResult:
        try:
            self._page.goto(
                SEARCH_URL_TEMPLATE.format(query=term), wait_until="domcontentloaded", timeout=20_000
            )
            cards = self._page.locator(PRODUCT_CARD_SELECTOR)
            count = cards.count()
        except Exception as exc:  # noqa: BLE001 - never abort the whole cycle
            logger.exception("Shufersal: search failed for %r", term)
            return CartAddResult(item_name=term, store=self.name, status="error", detail=str(exc), quantity=quantity)

        if count == 0:
            return CartAddResult(item_name=term, store=self.name, status="not_found", quantity=quantity)

        if count > 1:
            candidates = self._extract_candidate_labels(cards, min(count, MAX_CANDIDATES))
            return CartAddResult(
                item_name=term, store=self.name, status="ambiguous", candidates=candidates, quantity=quantity
            )

        return self._add_card(cards.first, term, quantity)

    def add_specific_product(self, product_label: str, quantity: int = 1) -> CartAddResult:
        """Re-search by the exact label from a previous ambiguous result and add it."""
        try:
            self._page.goto(
                SEARCH_URL_TEMPLATE.format(query=product_label), wait_until="domcontentloaded", timeout=20_000
            )
            cards = self._page.locator(PRODUCT_CARD_SELECTOR)
            for i in range(cards.count()):
                card = cards.nth(i)
                label = self._card_label(card)
                if label == product_label:
                    return self._add_card(card, product_label, quantity)
            return CartAddResult(item_name=product_label, store=self.name, status="not_found", quantity=quantity)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Shufersal: add_specific_product failed for %r", product_label)
            return CartAddResult(
                item_name=product_label, store=self.name, status="error", detail=str(exc), quantity=quantity
            )

    def close(self) -> None:
        try:
            self._context.close()
            self._browser.close()
        finally:
            self._playwright.stop()

    # -- helpers -----------------------------------------------------------

    def _add_card(self, card, term: str, quantity: int) -> CartAddResult:
        try:
            add_button = card.locator(ADD_TO_CART_SELECTOR).first
            for _ in range(quantity):
                add_button.click()
            return CartAddResult(item_name=term, store=self.name, status="added", quantity=quantity)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Shufersal: failed to click add-to-cart for %r", term)
            return CartAddResult(item_name=term, store=self.name, status="error", detail=str(exc), quantity=quantity)

    def _extract_candidate_labels(self, cards, limit: int) -> list[str]:
        return [self._card_label(cards.nth(i)) for i in range(limit)]

    @staticmethod
    def _card_label(card) -> str:
        try:
            text = card.locator(PRODUCT_NAME_SELECTOR).first.inner_text()
        except Exception:  # noqa: BLE001
            text = card.inner_text()
        return (text or "").strip().splitlines()[0]
