import tempfile
import unittest
from datetime import date
from pathlib import Path

from grocery_bot import pricecontrol, shelflife
from grocery_bot.storage import Storage

TODAY = date(2026, 9, 1)


def _item(share, last, department="מזווה ושימורים", name="x"):
    return shelflife.ShelfItem(
        product_code="P_1", name=name, department=department,
        share=share, last_purchased=last,
    )


class IntervalTest(unittest.TestCase):
    def test_share_becomes_an_interval(self):
        # Bought in a fifth of orders, orders ~8 days apart -> ~40 days.
        self.assertEqual(_item(0.2, TODAY).expected_interval_days, 40.0)

    def test_one_off_purchase_has_no_interval(self):
        # A single appearance in ~20 orders is the length of the history,
        # not a rhythm. The first run invented a 152-day interval for a
        # whole page of such items.
        self.assertIsNone(_item(0.05, TODAY).expected_interval_days)

    def test_impossible_share_is_rejected(self):
        self.assertIsNone(_item(1.4, TODAY).expected_interval_days)


class StatusTest(unittest.TestCase):
    def test_just_bought_is_stocked(self):
        item = _item(0.2, TODAY)
        self.assertEqual(item.status(TODAY), "stocked")
        self.assertEqual(item.days_until_due(TODAY), 40.0)

    def test_near_the_interval_is_soon(self):
        self.assertEqual(_item(0.2, date(2026, 7, 28)).status(TODAY), "soon")

    def test_past_the_interval_is_due(self):
        self.assertEqual(_item(0.2, date(2026, 7, 10)).status(TODAY), "due")

    def test_long_past_is_lapsed_not_due(self):
        # Baby formula 497 days overdue is not "needed", it is outgrown.
        self.assertEqual(_item(0.2, date(2025, 1, 1)).status(TODAY), "lapsed")

    def test_over_a_year_is_lapsed_whatever_the_arithmetic(self):
        # A long interval could otherwise keep a two-year-old purchase
        # inside the lapsed ratio forever.
        self.assertEqual(_item(0.11, date(2025, 6, 1)).status(TODAY), "lapsed")

    def test_never_bought_is_unknown(self):
        self.assertEqual(_item(0.2, None).status(TODAY), "unknown")

    def test_perishables_are_not_tracked_here(self):
        self.assertFalse(_item(0.2, TODAY, department="פירות וירקות").is_pantryable)


class BuildFromStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.executemany(
                "INSERT INTO stock_items (store, product_code, product_name, "
                "department, share) VALUES (?,?,?,?,?)",
                [
                    ("shufersal", "P_OIL", "שמן זית", "יבשים ובישול", 0.2),
                    ("shufersal", "P_CUKE", "מלפפון", "פירות וירקות", 0.9),
                ],
            )
            conn.commit()
        self.storage.record_last_purchase("shufersal", [("P_OIL", "2026-09-01")])

    def test_only_pantryable_items_are_tracked(self):
        names = [i.name for i in shelflife.build_items(self.storage)]
        self.assertEqual(names, ["שמן זית"])

    def test_recently_bought_item_is_not_proposed(self):
        self.assertEqual(
            [i.name for i in shelflife.not_yet(self.storage, today=TODAY)], ["שמן זית"]
        )
        self.assertEqual(shelflife.due_now(self.storage, today=TODAY), [])

    def test_last_purchase_keeps_the_newest_date(self):
        self.storage.record_last_purchase("shufersal", [("P_OIL", "2026-01-01")])
        self.assertEqual(
            self.storage.last_purchase_dates("shufersal")["P_OIL"], date(2026, 9, 1)
        )


class PriceControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.executemany(
                "INSERT INTO catalog_products (item_code, name, price) VALUES (?,?,?)",
                [
                    ("1", "12 ביצי משק טריות M לסר", 13.13),
                    ("2", "ביצים אומגה L שופרסל 6יח", 17.50),
                    ("3", "חלב בקרטון 3% שומן 1 ל", 7.35),
                ],
            )
            conn.commit()

    def test_the_reported_case(self):
        swap = pricecontrol.suggest_swap(self.storage, "ביצי אומגה 3 M", 21.90)
        self.assertIsNotNone(swap)
        self.assertEqual(swap.saving, 8.77)

    def test_a_controlled_item_needs_no_swap(self):
        self.assertIsNone(
            pricecontrol.suggest_swap(self.storage, "חלב בקרטון 3% שומן", 7.35)
        )

    def test_no_swap_when_the_controlled_one_is_not_cheaper(self):
        self.assertIsNone(pricecontrol.suggest_swap(self.storage, "ביצי אומגה 3 M", 10.0))

    def test_non_staples_are_left_alone(self):
        # "אורגני" marks a premium variant, but yoghurt is not controlled.
        self.assertIsNone(pricecontrol.suggest_swap(self.storage, "יוגורט אורגני", 12.0))

    def test_message_says_it_is_only_a_suggestion(self):
        swaps = pricecontrol.review_basket(
            self.storage, [{"name": "ביצי אומגה 3 M", "price": 21.90}]
        )
        self.assertIn("הצעה בלבד", pricecontrol.format_swaps(swaps))


if __name__ == "__main__":
    unittest.main()
