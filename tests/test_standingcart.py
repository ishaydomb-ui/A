"""The cart that stays full between shops.

The behaviours worth pinning: it fills from the broad list rather than
the curated one, it never re-adds a consumed request, it can tell what a
person removed, and it does not act on that — Ishay chose reporting over
learning on 2026-09-07, and an automatic demotion would silently stop
buying something the household needs.
"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from grocery_bot import standingcart
from grocery_bot.stock import StockItem
from grocery_bot.storage import Storage


class PlanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.replace_stock_items("shufersal", [
            StockItem("P_1", "חלב 3%", 0.9, "מוצרי חלב וקירור"),
            StockItem("P_2", "אורז בסמטי", 0.08, "מזווה ושימורים"),
        ])
        self.storage.record_last_purchase(
            "shufersal", [("P_1", "2026-09-01"), ("P_2", "2026-08-01")]
        )

    def test_it_fills_from_the_broad_list_not_the_curated_one(self):
        """A rare product (8% of orders) belongs in a standing cart —
        that is the whole difference from the order cycle's base list."""
        plan = standingcart.plan_refill(self.storage, "shufersal")
        self.assertIn("אורז בסמטי", [t for t, _ in plan.terms])

    def test_a_product_not_bought_for_over_a_year_is_left_out(self):
        # A separate store, because record_last_purchase deliberately
        # keeps the newest date it has ever seen — writing an older one
        # over a newer one is exactly what it refuses to do.
        self.storage.replace_stock_items("victory", [
            StockItem("P_9", "אורז ישן", 0.08, "מזווה ושימורים"),
        ])
        self.storage.record_last_purchase("victory", [("P_9", "2024-01-01")])
        plan = standingcart.plan_refill(self.storage, "victory")
        self.assertNotIn("אורז ישן", [t for t, _ in plan.terms])

    def test_a_chain_with_no_history_still_gets_a_full_cart(self):
        """Tiv Taam has no stock table of its own; filling nothing there
        would leave one of the two carts empty every week."""
        self.storage.add_base_list_item("קוטג", default_quantity=2)
        plan = standingcart.plan_refill(self.storage, "tivtaam")
        self.assertEqual(plan.terms, [("קוטג", 2)])

    def test_an_unknown_list_shape_fails_loudly(self):
        with self.assertRaises(KeyError):
            standingcart.plan_refill(self.storage, "shufersal", list_key="nonsense")


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _fill(self, *pairs):
        from grocery_bot.models import CartAddResult, OrderCycleReport

        report = OrderCycleReport(store="shufersal")
        for code, name in pairs:
            report.record(CartAddResult(
                item_name=name, store="shufersal", status="added", product_code=code
            ))
        standingcart.record_manifest(self.storage, {"shufersal": report})

    def test_what_is_gone_from_the_cart_was_removed_by_a_person(self):
        self._fill(("P_1", "חלב 3%"), ("P_2", "קורנפלקס"))
        gone = standingcart.removals(
            self.storage, "shufersal", [{"code": "P_1", "name": "חלב 3%"}]
        )
        self.assertEqual([r["name"] for r in gone], ["קורנפלקס"])

    def test_an_untouched_cart_reports_no_removals(self):
        self._fill(("P_1", "חלב 3%"))
        gone = standingcart.removals(
            self.storage, "shufersal", [{"code": "P_1", "name": "חלב 3%"}]
        )
        self.assertEqual(gone, [])

    def test_a_name_match_counts_even_without_a_code(self):
        # Tiv Taam's cart rows do not always carry a product code.
        self._fill(("", "חלב 3%"))
        gone = standingcart.removals(
            self.storage, "shufersal", [{"code": "", "name": "חלב 3%"}]
        )
        self.assertEqual(gone, [])

    def test_the_report_states_the_fact_and_suggests_nothing(self):
        text = standingcart.format_removal_report(
            [{"name": "קורנפלקס"}, {"name": "קורנפלקס"}, {"name": "חוט דנטלי"}],
            "שופרסל",
        )
        self.assertIn("קורנפלקס ×2", text)
        self.assertIn("לא שיניתי כלום", text)
        # No question, no recommendation: reporting was the explicit choice.
        self.assertNotIn("?", text)

    def test_no_removals_produces_no_message_at_all(self):
        self.assertEqual(standingcart.format_removal_report([], "שופרסל"), "")

    def test_the_cart_contents_come_from_the_manifest(self):
        self._fill(("P_1", "חלב"), ("P_2", "לחם"))
        self.assertEqual(standingcart.cart_contents(self.storage), [("shufersal", 2)])

    def test_marking_a_shop_is_recorded(self):
        standingcart.mark_shopped(self.storage, date(2026, 9, 7))
        self.assertEqual(standingcart.last_shop(self.storage), "2026-09-07")


if __name__ == "__main__":
    unittest.main()
