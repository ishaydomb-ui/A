"""The cart-pause guard, exercised at all six insertion points in
orchestrator._add_one (Work MVP, 2026-09-18; see cartpause.py and
orchestrator.py's cartpause.is_paused checks).

_add_one has seven mutation call sites per the spec's own count
(orchestrator.py:818, 851, 877, 890, 948, 997, 1001); the last two share
one guard (they are the same logical attempt -- a primary call and its
TypeError fallback with no yield point between them), so there are six
`cartpause.is_paused` checks in the source covering all seven.

Four are reached directly from a fresh call (preferred / identity /
prematched / plain search-and-add fallback) and are tested here with
realistic inputs. The remaining two (the "candidate cards resolve to one
product" branch and the "autoresolve decides" branch) are only reached
*after* search_and_add has already been called and returned ambiguous --
meaning in real operation the plain-fallback guard fires first and those
two are unreachable while paused. They are tested here anyway, by
un-pausing only for the search_and_add call and re-pausing for the
follow-up pick, so the guard's own presence at each site is verified
independently of reachability.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grocery_bot import cartpause
from grocery_bot.identity import Identity
from grocery_bot.models import CartAddResult
from grocery_bot.orchestrator import _add_one
from grocery_bot.storage import Storage


class _Adapter:
    """Records every mutation call; the test fails loudly if one happens
    while paused, since that is exactly the guard's one job."""

    name = "shufersal"

    def __init__(self, search_result=None):
        self.add_specific_product_calls = []
        self.search_and_add_calls = []
        self._search_result = search_result

    def add_specific_product(self, name, quantity=1, **kw):
        self.add_specific_product_calls.append((name, quantity, kw))
        return CartAddResult(item_name=name, store=self.name, status="added",
                             product_code=kw.get("product_code", ""))

    def search_and_add(self, term, quantity=1):
        self.search_and_add_calls.append((term, quantity))
        if self._search_result is not None:
            return self._search_result
        return CartAddResult(item_name=term, store=self.name, status="added")

    @property
    def calls(self):
        return len(self.add_specific_product_calls) + len(self.search_and_add_calls)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        cartpause.set_paused(self.storage, "shufersal", True)


class PlainFallbackGuardTests(Base):
    """orchestrator.py:890 (`result = adapter.search_and_add(term, quantity)`)."""

    def test_paused_blocks_the_plain_search_path_and_reports_skipped(self):
        ad = _Adapter()
        result = _add_one(self.storage, ad, "shufersal", "חלב", 1)
        self.assertEqual(ad.calls, 0)
        self.assertEqual(result.status, "skipped")
        self.assertIn("paused", result.detail)

    def test_unpaused_reaches_the_adapter_normally(self):
        cartpause.set_paused(self.storage, "shufersal", False)
        ad = _Adapter()
        result = _add_one(self.storage, ad, "shufersal", "חלב", 1)
        self.assertEqual(len(ad.search_and_add_calls), 1)
        self.assertEqual(result.status, "added")

    def test_a_different_stores_pause_does_not_block_this_one(self):
        cartpause.set_paused(self.storage, "shufersal", False)
        cartpause.set_paused(self.storage, "tivtaam", True)
        ad = _Adapter()
        result = _add_one(self.storage, ad, "shufersal", "חלב", 1)
        self.assertEqual(len(ad.search_and_add_calls), 1)
        self.assertEqual(result.status, "added")


class PreferredChoiceGuardTests(Base):
    """orchestrator.py:818 (`adapter.add_specific_product` under a remembered choice)."""

    def test_paused_blocks_even_a_remembered_product(self):
        self.storage.remember_choice("shufersal", "חלב", "P_1", "חלב תנובה", source="human")
        ad = _Adapter()
        result = _add_one(self.storage, ad, "shufersal", "חלב", 1)
        self.assertEqual(ad.calls, 0)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.product_code, "P_1")


class IdentityGuardTests(Base):
    """orchestrator.py:851 (`adapter.add_specific_product` under a resolved identity)."""

    def test_paused_blocks_a_barcode_resolved_identity(self):
        ad = _Adapter()
        ident = Identity(store="shufersal", product_code="P_2", name="חלב תנובה 3%", basis="barcode")
        result = _add_one(self.storage, ad, "shufersal", "חלב", 1, identity=ident)
        self.assertEqual(ad.calls, 0)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.product_code, "P_2")


class PrematchedGuardTests(Base):
    """orchestrator.py:877 (`adapter.add_specific_product` under a bulk-match hit)."""

    def test_paused_blocks_a_bulk_matched_hit(self):
        ad = _Adapter()
        hit = {"code": "P_3", "name": "חלב תנובה", "in_stock": True}
        result = _add_one(self.storage, ad, "shufersal", "חלב", 1, prematched={"חלב": hit})
        self.assertEqual(ad.calls, 0)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.product_code, "P_3")


class ResolvedCardGuardTests(Base):
    """orchestrator.py:948 (single ambiguous candidate resolves automatically).

    Only reached after search_and_add already ran, so is_paused is
    un-paused for that first check and re-paused for the follow-up pick.
    """

    def test_paused_blocks_the_pick_even_though_the_search_itself_ran(self):
        ambiguous = CartAddResult(
            item_name="קוטג", store="shufersal", status="ambiguous",
            candidate_cards=[{"name": "קוטג 5%", "code": "P_4"}],
        )
        ad = _Adapter(search_result=ambiguous)
        with mock.patch("grocery_bot.orchestrator.cartpause.is_paused", side_effect=[False, True]):
            result = _add_one(self.storage, ad, "shufersal", "קוטג", 1)
        self.assertEqual(len(ad.search_and_add_calls), 1, "the search itself was allowed to run")
        self.assertEqual(ad.add_specific_product_calls, [], "but the pick on top of it was blocked")
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.product_code, "P_4")


class AutoresolveGuardTests(Base):
    """orchestrator.py:997/1001 (autoresolve.decide picks a name-only candidate).

    Same reachability shape as the card-resolve branch above: only
    entered after an ambiguous, card-less search_and_add result.
    """

    def test_paused_blocks_the_autoresolved_pick(self):
        from grocery_bot.autoresolve import Decision

        ambiguous = CartAddResult(
            item_name="חלב", store="shufersal", status="ambiguous",
            candidates=["חלב תנובה 3%", "חלב תנובה 1%"],
        )
        ad = _Adapter(search_result=ambiguous)
        decision = Decision(ambiguity_id=0, store="shufersal", term="חלב",
                            index=0, name="חלב תנובה 3%", basis="history", code="P_5")
        with mock.patch("grocery_bot.orchestrator.cartpause.is_paused", side_effect=[False, True]), \
             mock.patch("grocery_bot.autoresolve.decide", return_value=decision):
            result = _add_one(self.storage, ad, "shufersal", "חלב", 1)
        self.assertEqual(len(ad.search_and_add_calls), 1)
        self.assertEqual(ad.add_specific_product_calls, [])
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.product_code, "P_5")


class EveryGuardIsPresentTests(unittest.TestCase):
    """A structural regression check: six cartpause.is_paused checks must
    remain in orchestrator.py, covering the spec's seven call sites."""

    def test_six_guard_checks_exist_in_the_source(self):
        import inspect

        from grocery_bot import orchestrator

        source = inspect.getsource(orchestrator._add_one)
        self.assertEqual(
            source.count("cartpause.is_paused("), 6,
            "expected one guard each before the preferred/identity/prematched/"
            "plain-search sites, plus one before the card-resolve pick and one "
            "before the autoresolve pick (which covers both its try and except "
            "call sites) -- six checks covering all seven original call sites",
        )


if __name__ == "__main__":
    unittest.main()
