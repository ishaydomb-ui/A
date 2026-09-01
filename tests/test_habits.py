import tempfile
import unittest
from pathlib import Path

from grocery_bot import habits
from grocery_bot.storage import Storage


class RateArithmeticTest(unittest.TestCase):
    """The mistake that would quietly halve every split product."""

    def _habit(self, intervals):
        return habits.Habit(
            name="x", intervals={k: [v] for k, v in dict(intervals).items()}
        )

    def test_two_listings_of_one_product_at_one_chain_both_count(self):
        # Tiv Taam lists cottage cheese under two product ids, at 35 days
        # and at 6. Keeping only the last threw away half the evidence.
        habit = habits.Habit(name="x", intervals={"tivtaam": [35.0, 6.0]})
        self.assertAlmostEqual(habit.interval_days, 5.1, places=1)

    def test_rates_add_rather_than_intervals_averaging(self):
        # Every 40 days at one shop and every 60 at another is every 24 —
        # not every 50. Averaging understates consumption by half.
        self.assertEqual(self._habit({"a": 40, "b": 60}).interval_days, 24.0)

    def test_a_single_chain_keeps_its_own_interval(self):
        self.assertEqual(self._habit({"a": 42}).interval_days, 42.0)

    def test_no_measured_interval_is_unknown_not_zero(self):
        self.assertIsNone(self._habit({}).interval_days)

    def test_three_chains_compound(self):
        self.assertAlmostEqual(
            self._habit({"a": 30, "b": 30, "c": 30}).interval_days, 10.0, places=1
        )


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.execute(
                "INSERT INTO catalog_products (item_code, name, price) VALUES "
                "('7290004131074', 'חלב בקרטון 3%', 7.35)"
            )
            conn.executemany(
                "INSERT INTO stock_items (store, product_code, product_name, "
                "department, share, interval_days, barcode) VALUES (?,?,?,?,?,?,?)",
                [
                    # Shufersal stores no barcode; it is derived from the
                    # sku suffix against the catalogue. Tiv Taam records one
                    # during the smart-list sync — except for loose produce,
                    # which no chain barcodes.
                    ("shufersal", "P_4131074", "חלב בקרטון 3%", "מוצרי בסיס", 0.5, 40.0, None),
                    ("tivtaam", "9001", "חלב 3% קרטון מהדרין", "מוצרי בסיס", 0.6, 60.0,
                     "7290004131074"),
                    ("shufersal", "P_9", "פלפל אדום", "פירות וירקות", 0.4, None, None),
                    ("tivtaam", "9002", "פלפל אדום", "פירות וירקות", 0.9, 57.0, None),
                ],
            )
            conn.commit()

    def test_the_same_product_at_two_chains_becomes_one(self):
        # Milk joins on the barcode even though the two chains name it
        # completely differently.
        merged = habits.build(self.storage)
        milk = [h for h in merged if h.barcode == "7290004131074"]
        self.assertEqual(len(milk), 1)
        self.assertTrue(milk[0].split)
        self.assertEqual(sorted(milk[0].chains), ["shufersal", "tivtaam"])

    def test_a_split_product_gets_the_combined_interval(self):
        milk = [h for h in habits.build(self.storage) if h.barcode][0]
        self.assertEqual(milk.interval_days, 24.0)

    def test_produce_falls_back_to_the_name(self):
        # Tiv Taam publishes no barcode for weighable goods, so the only
        # join available is the name.
        pepper = [h for h in habits.build(self.storage) if "פלפל" in h.name][0]
        self.assertTrue(pepper.split)
        self.assertEqual(pepper.matched_by, "name")

    def test_the_matching_method_is_reported_not_hidden(self):
        methods = {h.matched_by for h in habits.build(self.storage)}
        self.assertTrue(methods <= {"barcode", "name"})

    def test_split_helper_returns_only_multi_chain_products(self):
        self.assertTrue(all(h.split for h in habits.split_across_chains(self.storage)))

    def test_empty_history_says_so_rather_than_pretending(self):
        empty = Storage(str(Path(self.tmp.name) / "empty.sqlite3"))
        self.assertIn("אין עדיין", habits.format_habits(habits.build(empty)))


class SuffixJoinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def test_finds_the_ean_from_a_store_sku(self):
        with self.storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO catalog_products (item_code, name, price) VALUES "
                "('7290004131074', 'חלב', 7.35)"
            )
            conn.commit()
        found = self.storage.catalog_price_by_suffix("4131074")
        self.assertEqual(found["item_code"], "7290004131074")

    def test_an_ambiguous_suffix_is_refused_not_guessed(self):
        # Two products sharing a suffix would silently price the wrong one.
        with self.storage._connect() as conn:  # noqa: SLF001
            conn.executemany(
                "INSERT INTO catalog_products (item_code, name, price) VALUES (?,?,?)",
                [("7290004131074", "a", 1.0), ("1234004131074", "b", 2.0)],
            )
            conn.commit()
        self.assertIsNone(self.storage.catalog_price_by_suffix("4131074"))

    def test_a_non_numeric_code_is_rejected(self):
        self.assertIsNone(self.storage.catalog_price_by_suffix("abc"))


if __name__ == "__main__":
    unittest.main()
