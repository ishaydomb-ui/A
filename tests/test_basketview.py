"""The same basket at every chain.

The tests that matter here are the honesty ones: a chain must not look
cheap because it stocks less, and a substitute must not be priced as if
it were the same product. Both of those were real bugs in the first two
runs against the live feeds.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import basketview
from grocery_bot.prices import PricedProduct
from grocery_bot.storage import Storage


def _product(code, name, price):
    return PricedProduct(
        item_code=code, name=name, manufacturer="", price=price,
        unit_of_measure_price=0, unit_of_measure="", quantity="", is_weighted=False,
    )


class BasketComparisonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.replace_catalog(
            [
                _product("1", "חלב 3% קרטון", 8.0),
                _product("2", "קוטג 5% 250 גרם", 8.0),
                _product("3", "אורז בסמטי 1 קג", 20.0),
            ],
            [],
        )
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "a", "name": "חלב 3% קרטון", "price": 6.0,
             "observed_at": "2026-09-06", "source": "feed"},
            {"barcode": "b", "name": "קוטג 5% 250 גרם", "price": 7.0,
             "observed_at": "2026-09-06", "source": "feed"},
            # No rice at all — a real gap.
        ])

    def _basket(self):
        return [
            {"name": "חלב 3% קרטון", "quantity": 1},
            {"name": "קוטג 5% 250 גרם", "quantity": 2},
            {"name": "אורז בסמטי 1 קג", "quantity": 1},
        ]

    def test_a_missing_item_is_marked_not_silently_dropped(self):
        baskets = basketview.price_basket(self.storage, self._basket())
        tt = next(b for b in baskets if b.store == "tivtaam")
        self.assertEqual([l.term for l in tt.missing], ["אורז בסמטי 1 קג"])
        self.assertEqual(tt.lines[-1].mark, "❌")

    def test_the_saving_is_measured_on_the_same_goods_only(self):
        """₪2 on milk + ₪2 on two cottages = ₪4, and the rice — which Tiv
        Taam does not carry — must not appear on either side."""
        baskets = basketview.price_basket(self.storage, self._basket())
        tt = next(b for b in baskets if b.store == "tivtaam")
        self.assertEqual(tt.exact_subtotal, 6.0 + 7.0 * 2)
        self.assertEqual(tt.baseline_subtotal, 8.0 + 8.0 * 2)
        self.assertEqual(tt.saving, 4.0)

    def test_a_chain_that_stocks_less_does_not_win_on_total_alone(self):
        # A chain carrying only the one cheapest line would have the
        # smallest total in the basket and must still not be called
        # cheaper on it.
        self.storage.record_store_prices("politzer", [
            {"barcode": "z", "name": "חלב 3% קרטון", "price": 7.9,
             "observed_at": "2026-09-06", "source": "feed"},
        ])
        baskets = basketview.price_basket(self.storage, self._basket())
        thin = next(b for b in baskets if b.store == "politzer")
        rich = next(b for b in baskets if b.store == "tivtaam")
        self.assertLess(thin.total, rich.total, "the thin chain does have a smaller bill")
        self.assertGreater(rich.saving, thin.saving, "but the real saving is the other way")

    def test_a_substitute_is_shown_but_never_priced_into_the_saving(self):
        """Live example: 'אצבעות גבינה צהובה' matched 'אצבעות שוקולד' at a
        third of the price. Counting that as a discount would report a
        saving on being sold a different product."""
        self.storage.record_store_prices("keshet", [
            {"barcode": "c", "name": "אצבעות שוקולד קרם 50 גר", "price": 2.0,
             "observed_at": "2026-09-06", "source": "feed"},
        ])
        self.storage.replace_catalog(
            [_product("4", "אצבעות גבינה צהובה", 18.0)], []
        )
        baskets = basketview.price_basket(
            self.storage, [{"name": "אצבעות גבינה צהובה", "quantity": 1}]
        )
        keshet = next(b for b in baskets if b.store == "keshet")
        self.assertEqual(len(keshet.substitutes), 1)
        self.assertEqual(keshet.exact, [], "not an exact match")
        self.assertEqual(keshet.saving, 0.0, "a substitute earns no saving")

    def test_delivery_is_part_of_the_bottom_line(self):
        baskets = basketview.price_basket(self.storage, self._basket())
        tt = next(b for b in baskets if b.store == "tivtaam")
        # ₪4 on the shelf plus ₪6 cheaper delivery than Shufersal.
        self.assertAlmostEqual(tt.saving_with_delivery, 10.0, places=2)

    def test_the_report_names_the_match_quality_and_the_caveat(self):
        text = basketview.format_baskets(
            basketview.price_basket(self.storage, self._basket())
        )
        self.assertIn("לפי שם מוצר", text)
        self.assertIn("פריטים זהים", text)

    def test_an_empty_basket_says_so_rather_than_crashing(self):
        self.assertIn("אין לי מספיק", basketview.format_baskets([]))


if __name__ == "__main__":
    unittest.main()
