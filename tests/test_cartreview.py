"""The review before opening the store's site (Basics in Order §3, 26.09)."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from grocery_bot import cartreview, standingcart
from grocery_bot.models import CartAddResult, OrderCycleReport, PlanTerm
from grocery_bot.storage import Storage


class CartReviewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _fill(self, at="2026-09-24T10:00:00+00:00"):
        report = OrderCycleReport(store="shufersal")
        report.record(CartAddResult(item_name="חלב", store="shufersal", status="added", product_code="P_1"))
        standingcart.record_manifest(self.storage, {"shufersal": report},
                                     at=datetime.fromisoformat(at))

    def test_nothing_filled_means_no_review(self):
        self.assertEqual(cartreview.build(self.storage), ("", []))
        self.assertFalse(cartreview.due(self.storage))

    def test_once_per_fill(self):
        self._fill()
        text, buttons = cartreview.build(self.storage)
        self.assertIn("שופרסל", text)
        self.assertIn("לא משלמים דרכי", text)
        self.assertTrue(buttons and buttons[0][1].startswith("https://www.shufersal.co.il/"))
        self.assertTrue(cartreview.due(self.storage))
        cartreview.mark_sent(self.storage)
        self.assertFalse(cartreview.due(self.storage))
        self._fill("2026-09-30T10:00:00+00:00")  # a refill earns a new review
        self.assertTrue(cartreview.due(self.storage))

    def _item(self, run_id, term, outcome, when, kind="stock"):
        ids = self.storage.add_run_items(run_id, [PlanTerm(term=term, quantity=1, source_kind=kind, source_id=None)])
        self.storage.update_run_item(next(iter(ids.values())), store="tivtaam", outcome=outcome, attempted_at=when)

    def test_a_term_that_landed_later_is_not_open(self):
        self._fill()
        first = self.storage.start_cart_run("refill")
        self._item(first, "ביצים", "failed_product", "2026-09-24T11:00:00+00:00")
        self._item(first, "חלב", "unverified", "2026-09-24T11:00:00+00:00")
        second = self.storage.start_cart_run("watch_list")
        self._item(second, "ביצים", "verified", "2026-09-25T09:00:00+00:00")
        self._item(second, "יין", "verified", "2026-09-25T09:00:00+00:00", kind="deal")
        text, _ = cartreview.build(self.storage)
        warning = text.split("⚠️", 1)[1]
        self.assertIn("חלב", warning)
        self.assertNotIn("ביצים", warning)
        self.assertIn("יין", text.split("⚠️", 1)[0])  # listed as a deal the bot added


if __name__ == "__main__":
    unittest.main()
