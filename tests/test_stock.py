import tempfile
import unittest
from pathlib import Path

from grocery_bot.checklist import render_department, render_summary
from grocery_bot.stock import (
    DEMOTE_AFTER_SKIPS,
    StockItem,
    build_from_orders,
    department_for,
    group_by_department,
)
from grocery_bot.storage import Storage


def _entry(code, name, cat_code="300", cat_name="ירקות", qty=1, method="BY_UNIT", wc=None):
    return {
        "quantity": qty,
        "product": {
            "code": code, "name": name,
            "sellingMethod": {"code": method}, "weightConversion": wc,
            "commercialCategoryGroup": {"code": cat_code, "name": cat_name},
        },
    }


def _orders(*entry_lists):
    return [{"entries": list(e)} for e in entry_lists]


class TierTests(unittest.TestCase):
    def test_share_decides_the_tier(self) -> None:
        for share, expected in ((0.95, "A"), (0.5, "B"), (0.2, "C"), (0.05, "D")):
            item = StockItem("P", "x", share, "d")
            self.assertEqual(item.tier, expected, share)

    def test_rare_items_are_not_proposed(self) -> None:
        self.assertFalse(StockItem("P", "x", 0.05, "d").proposed)

    def test_tier_a_is_preticked_not_silent(self) -> None:
        """The user asked to see everything going in, not have it slip by."""
        self.assertTrue(StockItem("P", "x", 0.95, "d").preticked)

    def test_repeated_removals_stop_an_item_being_preticked(self) -> None:
        """The only signal the history cannot give, since it cannot see
        what was bought at the other chain."""
        item = StockItem("P", "x", 0.95, "d", skipped_count=DEMOTE_AFTER_SKIPS, picked_count=0)
        self.assertFalse(item.preticked)

    def test_an_item_kept_more_often_than_removed_stays_preticked(self) -> None:
        item = StockItem("P", "x", 0.9, "d", skipped_count=DEMOTE_AFTER_SKIPS, picked_count=9)
        self.assertTrue(item.preticked)


class BuildTests(unittest.TestCase):
    def test_share_is_fraction_of_orders(self) -> None:
        items = build_from_orders(
            _orders([_entry("A", "קוטג'")], [_entry("A", "קוטג'"), _entry("B", "חלב")])
        )
        by_code = {i.product_code: i for i in items}
        self.assertEqual(by_code["A"].share, 1.0)
        self.assertEqual(by_code["B"].share, 0.5)

    def test_weighted_quantities_become_kilos(self) -> None:
        items = build_from_orders(_orders([_entry("A", "פלפל", qty=500, method="BY_WEIGHT")]))
        self.assertEqual(items[0].amount, 0.5)
        self.assertEqual(items[0].default_quantity, 1)

    def test_packages_are_counted_not_summed_in_grams(self) -> None:
        items = build_from_orders(
            _orders([_entry("A", "כרוב", qty=2000, method="BY_PACKAGE", wc=1000.0)])
        )
        self.assertEqual(items[0].default_quantity, 2)

    def test_delivery_lines_are_excluded(self) -> None:
        items = build_from_orders(_orders([_entry("D", "משלוח שופרסל"), _entry("A", "קוטג'")]))
        self.assertEqual([i.product_name for i in items], ["קוטג'"])

    def test_empty_history_is_handled(self) -> None:
        self.assertEqual(build_from_orders([]), [])


class DepartmentTests(unittest.TestCase):
    def test_code_prefix_maps_to_a_department(self) -> None:
        self.assertEqual(department_for("306"), "פירות וירקות")
        self.assertEqual(department_for("534"), "מוצרי חלב וקירור")

    def test_unknown_codes_fall_back(self) -> None:
        self.assertEqual(department_for(""), "שונות")
        self.assertEqual(department_for("999"), "שונות")

    def test_grouping_excludes_unproposed_items(self) -> None:
        items = [StockItem("A", "x", 0.9, "פירות וירקות"), StockItem("B", "y", 0.02, "פירות וירקות")]
        groups = group_by_department(items)
        self.assertEqual(len(groups[0].items), 1)


class ChecklistRenderTests(unittest.TestCase):
    def _rows(self):
        return [
            {"product_name": "פלפל אדום", "selected": True, "amount": 0.5, "unit": 'ק"ג'},
            {"product_name": "קוטג'", "selected": False, "quantity": 2},
        ]

    def test_shows_ticks_and_counts(self) -> None:
        text = render_department("פירות", self._rows(), 1, 3)
        self.assertIn("✅", text)
        self.assertIn("⬜", text)
        self.assertIn("(1/2)", text)

    def test_shows_weight_where_relevant(self) -> None:
        self.assertIn('0.5 ק"ג', render_department("פירות", self._rows(), 1, 3))

    def test_summary_totals_across_departments(self) -> None:
        text = render_summary([("פירות", self._rows()), ("חלב", self._rows())])
        self.assertIn("2 מתוך 4", text)


class ProposalStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self.items = [
            {"store": "s", "product_code": "A", "product_name": "פלפל",
             "department": "פירות", "quantity": 1, "selected": True},
            {"store": "s", "product_code": "B", "product_name": "קוטג'",
             "department": "חלב", "quantity": 2, "selected": True},
        ]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_toggle_flips_one_item(self) -> None:
        pid = self.storage.create_proposal(1, self.items)
        self.assertFalse(self.storage.toggle_proposal_item(pid, "A"))
        rows = {r["product_code"]: r for r in self.storage.proposal_items(pid)}
        self.assertEqual(rows["A"]["selected"], 0)
        self.assertEqual(rows["B"]["selected"], 1)

    def test_bulk_clear_affects_only_its_department(self) -> None:
        pid = self.storage.create_proposal(1, self.items)
        self.storage.set_department_selection(pid, "פירות", False)
        rows = {r["product_code"]: r for r in self.storage.proposal_items(pid)}
        self.assertEqual(rows["A"]["selected"], 0)
        self.assertEqual(rows["B"]["selected"], 1)

    def test_a_new_proposal_supersedes_the_open_one(self) -> None:
        self.storage.create_proposal(1, self.items)
        second = self.storage.create_proposal(1, self.items)
        self.assertEqual(self.storage.open_proposal()["id"], second)

    def test_feedback_is_recorded_per_product(self) -> None:
        self.storage.replace_stock_items("s", [StockItem("A", "פלפל", 0.9, "פירות")])
        self.storage.record_stock_feedback("s", picked=[], skipped=["A"])
        row = self.storage.list_stock_items("s")[0]
        self.assertEqual(row["skipped_count"], 1)


if __name__ == "__main__":
    unittest.main()
