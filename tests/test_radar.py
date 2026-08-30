import tempfile
import unittest
from pathlib import Path

from grocery_bot.prices import PricedProduct, PromotionItem
from grocery_bot.radar import find_stockup_deals, format_stockup_deals
from grocery_bot.stock import StockItem
from grocery_bot.storage import Storage

FUTURE, PAST = "2099-01-01T00:00:00", "2000-01-01T00:00:00"


def _product(code, name, price, weighted=False, uom="100 גרם"):
    return PricedProduct(
        item_code=code, name=name, manufacturer="", price=price,
        unit_of_measure_price=0, unit_of_measure=uom, quantity="", is_weighted=weighted,
    )


def _promo(code, price, desc="מבצע"):
    return PromotionItem(
        promotion_id="p" + code, description=desc, item_code=code,
        discounted_price=price, min_qty=1, discount_rate=0, starts_at=PAST, ends_at=FUTURE,
    )


class RadarTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _stock(self, name, department="מזווה ושימורים", share=0.05):
        self.storage.replace_stock_items(
            "shufersal", [StockItem("P_X", name, share, department)]
        )

    def test_a_deep_discount_on_a_rare_item_is_flagged(self) -> None:
        """Tier D is the whole point: stocking up on what is NOT needed now."""
        self._stock("דבש טבעי")
        self.storage.replace_catalog([_product("1", "דבש טבעי לחיץ", 16.9)], [_promo("1", 5.0)])
        deals = find_stockup_deals(self.storage)
        self.assertEqual(len(deals), 1)
        self.assertAlmostEqual(deals[0].discount, 0.70, places=2)

    def test_a_shallow_discount_is_noise(self) -> None:
        self._stock("דבש טבעי")
        self.storage.replace_catalog([_product("1", "דבש טבעי לחיץ", 16.9)], [_promo("1", 15.0)])
        self.assertEqual(find_stockup_deals(self.storage), [])

    def test_unbought_products_are_not_scanned(self) -> None:
        self.storage.replace_catalog([_product("1", "דבש טבעי", 16.9)], [_promo("1", 5.0)])
        self.assertEqual(find_stockup_deals(self.storage), [])

    def test_pantryable_departments_are_marked(self) -> None:
        self._stock("דבש טבעי", department="מזווה ושימורים")
        self.storage.replace_catalog([_product("1", "דבש טבעי", 16.9)], [_promo("1", 5.0)])
        self.assertTrue(find_stockup_deals(self.storage)[0].pantryable)
        self.assertIn("🧺", format_stockup_deals(find_stockup_deals(self.storage)))

    def test_perishable_departments_are_not_marked_pantryable(self) -> None:
        self._stock("בננה", department="פירות וירקות")
        self.storage.replace_catalog(
            [_product("1", "בננה", 12.9, weighted=True, uom="1קילוגרם")], [_promo("1", 6.0)]
        )
        self.assertFalse(find_stockup_deals(self.storage)[0].pantryable)

    def test_lookalike_products_are_excluded(self) -> None:
        """Banana snack rings are not a stock-up deal on bananas."""
        self._stock("בננה", department="פירות וירקות")
        self.storage.replace_catalog(
            [
                _product("1", "בננה", 12.9, weighted=True, uom="1קילוגרם"),
                _product("2", "בננה ציפס", 11.9),
            ],
            [_promo("2", 4.0)],
        )
        self.assertEqual(find_stockup_deals(self.storage), [])

    def test_no_deals_message_is_honest(self) -> None:
        self.assertIn("אין כרגע", format_stockup_deals([]))


if __name__ == "__main__":
    unittest.main()
