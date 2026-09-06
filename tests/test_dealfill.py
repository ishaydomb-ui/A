"""Deals the cycle puts in the cart on its own.

The guards matter more than the feature here: this is the one place the
bot spends the household's money on something nobody asked for, so
"pantryable only", "already-bought only" and "not already in the cart"
each get a test of their own.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import dealfill
from grocery_bot.prices import PricedProduct, PromotionItem
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


class PicksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def _stock(self, *items):
        self.storage.replace_stock_items("shufersal", list(items))

    def test_a_deep_discount_on_a_pantry_staple_is_picked(self) -> None:
        self._stock(StockItem("P_1", "מרכך כביסה", 0.05, "טיפוח, תינוקות וניקיון"))
        self.storage.replace_catalog(
            [_product("1", "מרכך כביסה סנו", 20.0)], [_promo("1", 10.0)]
        )
        picks = dealfill.picks_for(self.storage, "shufersal")
        self.assertEqual([p.term for p in picks], ["מרכך כביסה"])
        self.assertIn("-50%", picks[0].label)
        self.assertIn("10.00₪", picks[0].label)

    def test_perishables_are_left_to_the_report(self) -> None:
        """Half-price bananas rot; the whole point is things that keep."""
        self._stock(StockItem("P_2", "בננה", 0.05, "פירות וירקות"))
        self.storage.replace_catalog(
            [_product("1", "בננה", 12.9, weighted=True, uom="1קילוגרם")], [_promo("1", 5.0)]
        )
        self.assertEqual(dealfill.picks_for(self.storage, "shufersal"), [])
        # ...but they are still available when asked for explicitly.
        self.assertEqual(
            len(dealfill.picks_for(self.storage, "shufersal", pantryable_only=False)), 1
        )

    def test_something_already_being_added_is_not_duplicated(self) -> None:
        self._stock(StockItem("P_1", "מרכך כביסה", 0.05, "טיפוח, תינוקות וניקיון"))
        self.storage.replace_catalog(
            [_product("1", "מרכך כביסה סנו", 20.0)], [_promo("1", 10.0)]
        )
        picks = dealfill.picks_for(
            self.storage, "shufersal", skip_terms=["מרכך כביסה"]
        )
        self.assertEqual(picks, [])

    def test_a_chain_with_no_promotions_of_its_own_gets_nothing(self) -> None:
        """Tiv Taam publishes no promo feed. A Shufersal deal added to a
        Tiv Taam cart would be bought at Tiv Taam's undiscounted price."""
        self._stock(StockItem("P_1", "מרכך כביסה", 0.05, "טיפוח, תינוקות וניקיון"))
        self.storage.replace_catalog(
            [_product("1", "מרכך כביסה סנו", 20.0)], [_promo("1", 10.0)]
        )
        self.assertEqual(dealfill.picks_for(self.storage, "tivtaam"), [])

    def test_the_number_added_is_capped(self) -> None:
        self._stock(*[
            StockItem(f"P_{n}", f"מוצר {n}", 0.05, "מזווה ושימורים") for n in range(12)
        ])
        self.storage.replace_catalog(
            [_product(str(n), f"מוצר {n}", 20.0) for n in range(12)],
            [_promo(str(n), 5.0) for n in range(12)],
        )
        picks = dealfill.picks_for(self.storage, "shufersal", limit=3)
        self.assertEqual(len(picks), 3)

    def test_format_names_the_saving_and_says_they_can_be_deleted(self) -> None:
        self._stock(StockItem("P_1", "מרכך כביסה", 0.05, "טיפוח, תינוקות וניקיון"))
        self.storage.replace_catalog(
            [_product("1", "מרכך כביסה סנו", 20.0)], [_promo("1", 10.0)]
        )
        text = dealfill.format_picks(dealfill.picks_for(self.storage, "shufersal"))
        self.assertIn("מחקו", text)
        self.assertIn("10.00₪", text)

    def test_no_picks_formats_to_nothing_rather_than_an_empty_header(self) -> None:
        self.assertEqual(dealfill.format_picks([]), "")


if __name__ == "__main__":
    unittest.main()
