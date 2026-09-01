import tempfile
import unittest
from pathlib import Path

from grocery_bot import multibuy
from grocery_bot.storage import Storage

FUTURE = "2026-09-13T02:59:00.000"
COUPON_END = "2031-01-01T00:00:00.000"


class MultiBuyArithmeticTest(unittest.TestCase):
    def _offer(self, regular, total, qty, department=""):
        return multibuy.MultiBuyOffer(
            item_code="1", name="x", regular_price=regular, promo_total=total,
            min_qty=qty, description="d", department=department,
        )

    def test_two_for_twenty_six(self):
        offer = self._offer(15.50, 26.0, 2)
        self.assertEqual(offer.unit_price, 13.00)
        self.assertEqual(offer.unit_saving, 2.50)

    def test_four_for_twenty(self):
        offer = self._offer(7.90, 20.0, 4)
        self.assertEqual(offer.unit_price, 5.00)
        self.assertEqual(offer.unit_saving, 2.90)

    def test_extra_outlay_is_reported_not_just_the_saving(self):
        # Saving ₪3.90 a tin still means ₪11.10 more leaves the account
        # today. Reporting only the saving makes every offer look free.
        offer = self._offer(18.90, 30.0, 2)
        self.assertEqual(offer.unit_saving, 3.90)
        self.assertEqual(offer.extra_outlay, 11.10)
        self.assertEqual(offer.extra_units, 1)

    def test_single_unit_promo_is_not_an_upsell(self):
        offer = self._offer(18.90, 10.0, 1)
        self.assertFalse(offer.is_upsell)
        self.assertEqual(offer.extra_units, 0)
        self.assertEqual(offer.unit_price, 10.00)

    def test_trivial_saving_is_not_worth_reporting(self):
        self.assertFalse(self._offer(10.0, 9.80, 1).worth_taking)

    def test_bulk_beyond_a_sensible_quantity_is_rejected(self):
        # Twelve tins is a storage decision, not a price one.
        self.assertFalse(self._offer(10.0, 60.0, 12).worth_taking)

    def test_unknown_shelf_life_is_unknown_not_perishable(self):
        # The department comes from purchase history, so a never-bought
        # item has none; calling that "perishable" demoted tinned tomatoes
        # below ketchup.
        self.assertIsNone(self._offer(10.0, 5.0, 1).keeps)
        self.assertTrue(self._offer(10.0, 5.0, 1, "מזווה ושימורים").keeps)
        self.assertFalse(self._offer(10.0, 5.0, 1, "פירות וירקות").keeps)


class OffersFromCatalogueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.executemany(
                "INSERT INTO catalog_products (item_code, name, price) VALUES (?,?,?)",
                [("A", "עגבניות חתוכות דק", 18.90), ("B", "קטשופ", 11.90)],
            )
            conn.executemany(
                "INSERT INTO catalog_promotions "
                "(promotion_id, description, item_code, discounted_price, min_qty, "
                " discount_rate, starts_at, ends_at) VALUES (?,?,?,?,?,?,?,?)",
                [
                    ("1", "10 עגבניות חתוכות דק", "A", 10.0, 1.0, 47.0, "", FUTURE),
                    ("2", "11 קטשופ", "B", 11.0, 1.0, 8.0, "", FUTURE),
                    # A gift-coupon row, valid for years and not a price cut.
                    ("3", "קופון 50 מתנה", "A", 18.90, 1.0, 0.0, "", COUPON_END),
                ],
            )
            conn.commit()

    def test_gift_coupon_rows_are_excluded(self):
        offers = multibuy.offers_for_items(self.storage, ["A"])
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].unit_price, 10.00)

    def test_bigger_saving_ranks_first(self):
        offers = multibuy.offers_for_items(self.storage, ["B", "A"])
        self.assertEqual([o.item_code for o in offers], ["A", "B"])

    def test_unknown_item_code_is_skipped(self):
        self.assertEqual(multibuy.offers_for_items(self.storage, ["ZZZ"]), [])

    def test_duplicate_codes_are_not_double_counted(self):
        self.assertEqual(len(multibuy.offers_for_items(self.storage, ["A", "A"])), 1)

    def test_message_escapes_asterisks_in_product_names(self):
        # "400*3ג" breaks Telegram Markdown for the whole message.
        offer = multibuy.MultiBuyOffer(
            item_code="A", name="עגבניות חתוכות דק 400*3ג", regular_price=18.90,
            promo_total=10.0, min_qty=1.0, description="d",
        )
        self.assertIn(r"400\*3", multibuy.format_offers([offer]))

    def test_empty_result_says_so_plainly(self):
        self.assertIn("לא מצאתי", multibuy.format_offers([]))


if __name__ == "__main__":
    unittest.main()
