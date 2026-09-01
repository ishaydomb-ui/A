import tempfile
import unittest
from datetime import date
from pathlib import Path

from grocery_bot import whereto
from grocery_bot.compare import units_bought
from grocery_bot.publishedprices import PortalFile
from grocery_bot.storage import Storage


class QuantityUnitTest(unittest.TestCase):
    """The most expensive arithmetic mistake available in this codebase."""

    def _entry(self, method, quantity):
        return {"product": {"sellingMethod": {"code": method}}, "quantity": quantity}

    def test_counted_items_are_a_count(self):
        self.assertEqual(units_bought(self._entry("BY_UNIT", 3)), 3)

    def test_weighed_items_arrive_in_grams(self):
        self.assertEqual(units_bought(self._entry("BY_WEIGHT", 500)), 0.5)

    def test_by_package_is_also_grams(self):
        # The one that gets forgotten. Reading 1000g of grapes as 1000
        # packets turned a ₪19.90 line into ₪19,900 and a ₪575 basket
        # into ₪28,187.
        self.assertEqual(units_bought(self._entry("BY_PACKAGE", 1000)), 1.0)

    def test_missing_method_is_treated_as_a_count(self):
        self.assertEqual(units_bought({"product": {}, "quantity": 2}), 2)

    def test_selling_method_as_a_bare_string(self):
        entry = {"product": {"sellingMethod": "BY_WEIGHT"}, "quantity": 250}
        self.assertEqual(units_bought(entry), 0.25)


class QuoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        self.storage.record_store_prices(
            "ramilevy",
            [
                {"barcode": "A", "name": "a", "price": 8.0, "observed_at": "2026-09-01"},
                {"barcode": "B", "name": "b", "price": 18.0, "observed_at": "2026-09-01"},
            ],
        )

    def _basket(self):
        return [
            {"barcode": "A", "name": "a", "units": 1, "price": 10.0},
            {"barcode": "B", "name": "b", "units": 1, "price": 20.0},
        ]

    def test_prices_the_shared_subset(self):
        quote = whereto.quote_basket(self.storage, self._basket(), chains=["ramilevy"])[0]
        self.assertEqual(quote.matched, 2)
        self.assertEqual(quote.subtotal, 26.0)
        self.assertEqual(quote.baseline_subtotal, 30.0)
        self.assertEqual(quote.saving, 4.0)

    def test_unstocked_items_do_not_count_toward_either_side(self):
        # Otherwise a chain looks cheap simply by not carrying things.
        basket = self._basket() + [{"barcode": "Z", "name": "z", "units": 1, "price": 99.0}]
        quote = whereto.quote_basket(self.storage, basket, chains=["ramilevy"])[0]
        self.assertEqual(quote.matched, 2)
        self.assertEqual(quote.baseline_subtotal, 30.0)
        self.assertIn("z", quote.missing)

    def test_delivery_difference_is_part_of_the_saving(self):
        quote = whereto.quote_basket(self.storage, self._basket(), chains=["ramilevy"])[0]
        # ₪4 on the shelf plus ₪6.90 cheaper delivery than Shufersal.
        self.assertAlmostEqual(quote.saving_with_delivery, 10.90, places=2)

    def test_thin_coverage_is_not_comparable(self):
        basket = self._basket() + [
            {"barcode": str(n), "name": "x", "units": 1, "price": 5.0} for n in range(10)
        ]
        quote = whereto.quote_basket(self.storage, basket, chains=["ramilevy"])[0]
        self.assertLess(quote.coverage, whereto.MIN_COVERAGE)
        self.assertFalse(quote.comparable)
        self.assertFalse(quote.worth_switching)

    def test_a_trivial_saving_is_not_worth_moving_a_shop(self):
        self.storage.record_store_prices(
            "keshet",
            [{"barcode": "A", "name": "a", "price": 9.95, "observed_at": "2026-09-01"},
             {"barcode": "B", "name": "b", "price": 19.95, "observed_at": "2026-09-01"}],
        )
        quote = whereto.quote_basket(self.storage, self._basket(), chains=["keshet"])[0]
        self.assertTrue(quote.comparable)
        self.assertFalse(quote.worth_switching)

    def test_chains_with_no_data_are_skipped_not_guessed(self):
        self.assertEqual(
            whereto.quote_basket(self.storage, self._basket(), chains=["osherad"]), []
        )

    def test_best_switch_picks_the_biggest_worthwhile_move(self):
        self.storage.record_store_prices(
            "osherad",
            [{"barcode": "A", "name": "a", "price": 1.0, "observed_at": "2026-09-01"},
             {"barcode": "B", "name": "b", "price": 1.0, "observed_at": "2026-09-01"}],
        )
        quotes = whereto.quote_basket(
            self.storage, self._basket(), chains=["ramilevy", "osherad"]
        )
        self.assertEqual(whereto.best_switch(quotes).chain, "osherad")

    def test_no_worthwhile_move_says_so(self):
        quotes = whereto.quote_basket(self.storage, self._basket(), chains=["ramilevy"])
        quotes[0].subtotal = quotes[0].baseline_subtotal
        self.assertIn("אין הפרש", whereto.format_quotes(quotes))


class FeedFreshnessTest(unittest.TestCase):
    def test_date_comes_from_the_filename_not_the_upload_time(self):
        # Re-uploading an unchanged file refreshes its modification time,
        # which would make a two-year-old snapshot look like today's.
        file = PortalFile("PriceFull7290-001-034-20260901-1405.gz", 1, "09/01/2026 14:10")
        self.assertEqual(file.published_on, date(2026, 9, 1))

    def test_a_long_dead_feed_reports_its_real_age(self):
        # Yohananof, observed 2026-09-01: parses perfectly, last updated
        # December 2024.
        file = PortalFile("PriceFull7290803800003-7999-202412271528.gz", 1, "")
        self.assertEqual(file.age_days(date(2026, 9, 1)), 613)

    def test_a_nameless_date_is_unknown_rather_than_assumed_fresh(self):
        self.assertIsNone(PortalFile("weird.gz", 1, "").age_days(date(2026, 9, 1)))

    def test_branch_id_is_read_from_the_filename(self):
        self.assertEqual(
            PortalFile("PriceFull7290058140886-001-737-20260901-120005.gz", 1, "").branch_id,
            "737",
        )


if __name__ == "__main__":
    unittest.main()
