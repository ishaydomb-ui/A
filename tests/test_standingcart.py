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



class MonthlyReportTests(unittest.TestCase):
    """The removal note exists to be *sent*, which needs a caller.

    Built and left unwired on 2026-09-07 — the exact shape flagged to
    the audit project that same morning (a tested function nothing
    calls). It is driven from /done, the one moment the answer is
    knowable, and surfaces monthly because Ishay shops weekly and asked
    not to be shown the same four products every time.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_removals_accumulate_across_shops(self):
        standingcart.log_removals(
            self.storage, "shufersal", [{"name": "קורנפלקס"}], date(2026, 9, 7)
        )
        total = standingcart.log_removals(
            self.storage, "shufersal", [{"name": "קורנפלקס"}], date(2026, 9, 14)
        )
        self.assertEqual(total, 2)

    def test_nothing_removed_logs_nothing(self):
        self.assertEqual(standingcart.log_removals(self.storage, "shufersal", []), 0)

    def test_the_first_report_is_due_as_soon_as_there_is_anything(self):
        standingcart.log_removals(self.storage, "shufersal", [{"name": "קורנפלקס"}])
        self.assertTrue(standingcart.removal_report_due(self.storage))

    def test_it_does_not_repeat_within_the_month(self):
        standingcart.log_removals(
            self.storage, "shufersal", [{"name": "קורנפלקס"}], date(2026, 9, 7)
        )
        first = standingcart.due_removal_report(self.storage, date(2026, 9, 7))
        self.assertIn("קורנפלקס", first)
        standingcart.log_removals(
            self.storage, "shufersal", [{"name": "חוט דנטלי"}], date(2026, 9, 14)
        )
        self.assertEqual(standingcart.due_removal_report(self.storage, date(2026, 9, 14)), "")

    def test_it_comes_back_after_a_month(self):
        standingcart.log_removals(
            self.storage, "shufersal", [{"name": "קורנפלקס"}], date(2026, 9, 7)
        )
        standingcart.due_removal_report(self.storage, date(2026, 9, 7))
        standingcart.log_removals(
            self.storage, "shufersal", [{"name": "חוט דנטלי"}], date(2026, 10, 8)
        )
        later = standingcart.due_removal_report(self.storage, date(2026, 10, 8))
        self.assertIn("חוט דנטלי", later)
        self.assertNotIn("קורנפלקס", later, "the reported batch was cleared")

    def test_an_empty_log_produces_no_message(self):
        self.assertEqual(standingcart.due_removal_report(self.storage), "")

if __name__ == "__main__":
    unittest.main()


class UnannouncedShopTests(unittest.TestCase):
    """The backstop for a shop nobody mentioned.

    From 2026-09-07: a real order was placed, the bot was told in plain
    words, and the cart still sat empty for a day. Free text now handles
    being told; this handles not being told at all.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _order(self, code, placed_at):
        self.storage.log_orders(
            [{"code": code, "placed_at": placed_at, "item_count": 40}], store="shufersal"
        )

    def test_an_order_newer_than_the_last_refill_is_a_shop(self):
        standingcart.mark_shopped(self.storage, date(2026, 9, 1))
        self._order("A1", "2026-09-07T06:15:00")
        self.assertEqual(
            standingcart.shop_detected_since_refill(self.storage)[:10], "2026-09-07"
        )

    def test_an_order_already_followed_by_a_refill_is_not_re_detected(self):
        # Otherwise every nightly run would refill the same cart again.
        self._order("A1", "2026-09-07T06:15:00")
        standingcart.mark_shopped(self.storage, date(2026, 9, 8))
        self.assertEqual(standingcart.shop_detected_since_refill(self.storage), "")

    def test_an_order_on_the_same_day_as_the_refill_is_not_re_detected(self):
        self._order("A1", "2026-09-08T06:15:00")
        standingcart.mark_shopped(self.storage, date(2026, 9, 8))
        self.assertEqual(standingcart.shop_detected_since_refill(self.storage), "")

    def test_no_orders_at_all_is_silence_not_a_shop(self):
        self.assertEqual(standingcart.shop_detected_since_refill(self.storage), "")

    def test_a_first_ever_order_with_no_refill_history_counts(self):
        self._order("A1", "2026-09-07T06:15:00")
        self.assertTrue(standingcart.shop_detected_since_refill(self.storage))


class DealTaggingTests(unittest.TestCase):
    """A refill must record *why* an item is in the cart.

    Ishay, 2026-09-09: "which items were added because of a deal?"
    /lastdeals was empty after a refill that had added six, because the
    standing-cart path fills through add_terms_to_cart, which knows the
    terms but not why each one is on the list. The answer had to be dug
    out of a log file.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _plan_and_report(self):
        from grocery_bot.dealfill import DealPick
        from grocery_bot.models import CartAddResult, OrderCycleReport

        plan = standingcart.RefillPlan(
            store="shufersal",
            terms=[("חלב", 1), ("דבש טבעי לחיץ", 1)],
            deals=[DealPick(term="דבש טבעי לחיץ", catalog_name="דבש טבעי לחיץ 250 גרם",
                            shelf_price=16.90, deal_price=5.00, discount=0.70,
                            description="מבצע")],
        )
        report = OrderCycleReport(store="shufersal")
        for name in ("חלב", "דבש טבעי לחיץ"):
            report.record(CartAddResult(item_name=name, store="shufersal", status="added"))
        return plan, report

    def test_only_the_promotion_driven_line_is_tagged(self):
        plan, report = self._plan_and_report()
        self.assertEqual(standingcart.tag_deal_results(report, plan), 1)
        tagged = [r for r in report.added if r.deal]
        self.assertEqual([r.item_name for r in tagged], ["דבש טבעי לחיץ"])
        self.assertIn("-70%", tagged[0].deal)

    def test_the_household_s_usual_items_stay_untagged(self):
        plan, report = self._plan_and_report()
        standingcart.tag_deal_results(report, plan)
        milk = next(r for r in report.added if r.item_name == "חלב")
        self.assertEqual(milk.deal, "")

    def test_a_refill_with_no_deals_tags_nothing(self):
        from grocery_bot.models import CartAddResult, OrderCycleReport

        plan = standingcart.RefillPlan(store="shufersal", terms=[("חלב", 1)], deals=[])
        report = OrderCycleReport(store="shufersal")
        report.record(CartAddResult(item_name="חלב", store="shufersal", status="added"))
        self.assertEqual(standingcart.tag_deal_results(report, plan), 0)

    def test_tagged_results_reach_the_stored_record(self):
        """The end of the chain: what /lastdeals reads."""
        import json

        from grocery_bot.orchestrator import record_deals

        plan, report = self._plan_and_report()
        standingcart.tag_deal_results(report, plan)
        record_deals(self.storage, {"shufersal": report})
        stored = json.loads(self.storage.get_state("last_deal_picks"))
        self.assertEqual([p["name"] for p in stored["picks"]], ["דבש טבעי לחיץ"])
