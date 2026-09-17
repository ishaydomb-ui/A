"""UNKNOWN != FALSE, and "did not throw" != "verified". Phase 2 (2026-09-17).

Every case here is a state the live system was observed to get wrong:
Shufersal reported "added" on nothing but a click that did not throw;
Tiv Taam's line counter returned 0 from an exception handler and that 0
was the `before` in add verification; both carts returned ok=True with
zero lines beside a non-zero total; and an unreadable cart let the guard
allow a duplicate add. The adapters cannot be constructed without a
browser, so each is built with `__new__` and given a stub page.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot.adapters.tivtaam import TivTaamAdapter
from grocery_bot.adapters.shufersal import ShufersalAdapter
from grocery_bot.models import CartAddResult, OrderCycleReport
from grocery_bot.orchestrator import CartGuard, _outcome_for
from grocery_bot.storage import Storage


class _Locator:
    def __init__(self, count=0, raise_on_count=False):
        self._count = count
        self._raise = raise_on_count
        self.first = self

    def count(self):
        if self._raise:
            raise RuntimeError("page closed")
        return self._count

    def click(self, **kw):
        return None

    def inner_text(self, **kw):
        return ""


class _Page:
    def __init__(self, line_count=0, raise_on_count=False):
        self._line = _Locator(line_count, raise_on_count)

    def locator(self, selector):
        return self._line

    def wait_for_timeout(self, ms):
        return None


def _tivtaam(page) -> TivTaamAdapter:
    ad = TivTaamAdapter.__new__(TivTaamAdapter)
    ad._page = page
    return ad


class OutcomeRuleTests(unittest.TestCase):
    """The single rule at the orchestrator boundary."""

    def test_added_without_evidence_is_unverified_not_verified(self):
        r = CartAddResult(item_name="חלב", store="s", status="added")
        self.assertEqual(_outcome_for(r)[0], "unverified")

    def test_added_with_evidence_is_verified(self):
        r = CartAddResult(item_name="חלב", store="s", status="added", verification="verified")
        self.assertEqual(_outcome_for(r)[0], "verified")

    def test_an_explicitly_unverified_add_stays_unverified(self):
        r = CartAddResult(item_name="חלב", store="s", status="added", verification="unverified")
        self.assertEqual(_outcome_for(r)[0], "unverified")

    def test_an_ambiguous_click_is_not_a_product_failure(self):
        # "the click did not change the cart" — lag or a dead button.
        r = CartAddResult(item_name="חלב", store="s", status="error",
                          detail="the click did not change the cart", failure_kind="ambiguous")
        outcome, kind, _ = _outcome_for(r)
        self.assertEqual((outcome, kind), ("unverified", "ambiguous"))

    def test_the_adapters_classification_wins_over_the_text(self):
        r = CartAddResult(item_name="חלב", store="s", status="error",
                          detail="something odd", failure_kind="infrastructure")
        self.assertEqual(_outcome_for(r)[0], "failed_infra")


class TivTaamCountTests(unittest.TestCase):
    def test_a_failed_count_read_is_none_not_zero(self):
        ad = _tivtaam(_Page(raise_on_count=True))
        self.assertIsNone(ad._cart_line_count())

    def test_a_real_zero_is_still_zero(self):
        ad = _tivtaam(_Page(line_count=0))
        self.assertEqual(ad._cart_line_count(), 0)

    def test_a_header_without_a_count_is_unknown(self):
        ad = _tivtaam(_Page())
        ad._summary_text = lambda: "₪39.90 כולל דמי משלוח"
        self.assertIsNone(ad._cart_count())


class TivTaamAddVerificationTests(unittest.TestCase):
    """Two signals must agree; either one missing is unverified."""

    def _add(self, counts, names):
        ad = _tivtaam(_Page())
        seq = iter(counts)
        last = counts[-1]
        # The adapter polls more times than the script has values; after
        # the script ends the count simply stays where it was. Letting the
        # iterator run out raised StopIteration inside the adapter's
        # outer handler and read as a generic error — a fixture bug that
        # looked like a verification bug.
        ad._cart_line_count = lambda: next(seq, last)
        ad._cart_line_names = lambda: names
        row = _Page(line_count=1)  # a row whose add button exists
        return ad._add_row(row, "חלב 3%", 1)

    def test_count_delta_and_name_present_is_verified(self):
        r = self._add([2, 3, 3, 3, 3, 3], [{"name": "חלב 3% קרטון"}])
        self.assertEqual((r.status, r.verification), ("added", "verified"))

    def test_count_delta_but_panel_unreadable_is_unverified(self):
        r = self._add([2, 3, 3, 3, 3, 3], [])
        self.assertEqual((r.status, r.verification), ("added", "unverified"))

    def test_count_delta_but_name_absent_is_unverified(self):
        r = self._add([2, 3, 3, 3, 3, 3], [{"name": "משהו אחר"}])
        self.assertEqual((r.status, r.verification), ("added", "unverified"))

    def test_an_unreadable_before_count_is_unverified_not_a_success(self):
        # The old code turned before=0 (from an exception) into "added".
        r = self._add([None, 3, 3, 3, 3, 3], [{"name": "חלב 3% קרטון"}])
        self.assertEqual((r.status, r.verification), ("added", "unverified"))

    def test_no_delta_is_ambiguous_not_a_product_fact(self):
        r = self._add([2, 2, 2, 2, 2, 2], [])
        self.assertEqual((r.status, r.failure_kind), ("error", "ambiguous"))


class CartReadTruthTests(unittest.TestCase):
    def test_tivtaam_lines_empty_with_total_and_no_count_is_a_failed_read(self):
        ad = _tivtaam(_Page())
        ad._reopen = lambda: None
        ad._summary_text = lambda: "₪452.10 כולל דמי משלוח"   # no "N מוצרים"
        ad._cart_line_names = lambda: []
        s = ad.cart_summary()
        self.assertFalse(s["ok"])
        self.assertEqual(s.get("read"), "failed")

    def test_tivtaam_all_signals_zero_is_a_verified_empty_cart(self):
        ad = _tivtaam(_Page())
        ad._reopen = lambda: None
        ad._summary_text = lambda: "0 מוצרים ₪0.00"
        ad._cart_line_names = lambda: []
        s = ad.cart_summary()
        self.assertTrue(s["ok"])
        self.assertEqual(s["items"], [])

    def test_shufersal_zero_lines_with_a_total_is_a_failed_read(self):
        # Observed live 2026-09-17: 0 lines, ₪1,831.83, ok=True.
        ad = ShufersalAdapter.__new__(ShufersalAdapter)

        class P:
            def goto(self, *a, **k): return None
            def wait_for_selector(self, *a, **k): raise TimeoutError()
            def eval_on_selector_all(self, *a, **k): return []
            def locator(self, sel): return _Locator(0)
            def inner_text(self, sel): return "סה\"כ לתשלום 1,831.83 ₪"
        ad._page = P()
        s = ad.cart_summary()
        self.assertFalse(s["ok"])
        self.assertEqual(s.get("read"), "failed")


class GuardOnUnreadableCartTests(unittest.TestCase):
    """An unreadable cart must not silently allow a duplicate add."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        from grocery_bot import standingcart

        report = OrderCycleReport(store="tivtaam")
        report.record(CartAddResult(item_name="עגבניות שרי במלח", store="tivtaam",
                                    status="added", product_code="4711"))
        standingcart.record_manifest(self.storage, {"tivtaam": report})

    class _Unreadable:
        def cart_summary(self):
            return {"ok": False, "items": [], "total": 452.1, "read": "failed"}

    def test_what_we_last_added_is_blocked_when_the_cart_is_unreadable(self):
        guard = CartGuard.read(self.storage, self._Unreadable(), "tivtaam")
        self.assertFalse(guard.readable)
        self.assertEqual(guard.blocks(name="עגבניות שרי במלח"), "כבר בעגלה")
        self.assertEqual(guard.blocks(code="4711"), "כבר בעגלה")

    def test_a_new_item_still_proceeds(self):
        # Not a hard block on the whole fill.
        guard = CartGuard.read(self.storage, self._Unreadable(), "tivtaam")
        self.assertEqual(guard.blocks(name="בצל ירוק"), "")

    def test_a_readable_cart_is_unchanged(self):
        class Readable:
            def cart_summary(self):
                return {"ok": True, "items": [{"code": "1", "name": "חלב"}], "total": 7.9}
        guard = CartGuard.read(self.storage, Readable(), "tivtaam")
        self.assertTrue(guard.readable)
        self.assertEqual(guard.blocks(name="חלב"), "כבר בעגלה")


if __name__ == "__main__":
    unittest.main()
