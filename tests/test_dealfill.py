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


class NovelDealTests(unittest.TestCase):
    """Deep discounts on things never bought before.

    Every guard here was added after running the real feeds and reading
    what they actually proposed — see the constants' own comments.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def _catalog(self, *pairs):
        self.storage.replace_catalog([p for p, _ in pairs], [r for _, r in pairs])

    def test_a_cheap_deep_discount_on_something_new_is_picked(self) -> None:
        self._catalog((_product("1", "משחת שיניים טוטאל", 19.9), _promo("1", 5.0)))
        picks = dealfill.picks_for(self.storage, "shufersal")
        self.assertEqual([p.catalog_name for p in picks], ["משחת שיניים טוטאל"])
        self.assertFalse(picks[0].familiar)

    def test_an_expensive_novel_item_is_refused(self) -> None:
        """A ₪169 serving spoon at 75% off is a real deal and still not
        something to drop into a grocery cart unasked."""
        self._catalog((_product("1", "כף חלוקה נירוסטה", 669.0), _promo("1", 169.0)))
        self.assertEqual(dealfill.picks_for(self.storage, "shufersal"), [])

    def test_an_implausible_discount_is_treated_as_a_feed_artefact(self) -> None:
        # "₪30 instead of ₪179" on a weighed cheese is a per-kilo shelf
        # price against a per-unit promotion, not an 83% saving.
        self._catalog((_product("1", "גאודה עיזים", 29.0), _promo("1", 2.0)))
        self.assertEqual(dealfill.picks_for(self.storage, "shufersal"), [])

    def test_items_sold_by_weight_are_skipped_entirely(self) -> None:
        self._catalog((_product("1", "פסטרמה ברביקיו משקל", 25.0), _promo("1", 10.0)))
        self.assertEqual(dealfill.picks_for(self.storage, "shufersal"), [])

    def test_a_novel_perishable_is_left_alone(self) -> None:
        self._catalog((_product("1", "גבינה צהובה פרוסה", 20.0), _promo("1", 6.0)))
        self.assertEqual(dealfill.picks_for(self.storage, "shufersal"), [])

    def test_novel_picks_can_be_switched_off_without_losing_familiar_ones(self) -> None:
        self.storage.replace_stock_items(
            "shufersal", [StockItem("P_1", "מרכך כביסה", 0.05, "טיפוח, תינוקות וניקיון")]
        )
        self._catalog(
            (_product("1", "מרכך כביסה סנו", 20.0), _promo("1", 10.0)),
            (_product("2", "משחת שיניים טוטאל", 19.9), _promo("2", 5.0)),
        )
        picks = dealfill.picks_for(self.storage, "shufersal", novel_limit=0)
        self.assertTrue(all(p.familiar for p in picks))
        self.assertIn("מרכך כביסה", [p.term for p in picks])


class BarcodeChainDealTests(unittest.TestCase):
    """Tiv Taam: prices and promotions share the manufacturer's EAN, so a
    deal joins to a shelf price with no name matching in the path."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self.storage.record_store_prices("tivtaam", [
            # bought before -> familiar
            {"barcode": "111", "name": "ברוקולי קפוא 800 גר", "price": 24.9,
             "observed_at": "2026-08-01", "source": "order"},
            # feed-only -> novel
            {"barcode": "222", "name": "ציפס קלאסי 1 קג", "price": 17.9,
             "observed_at": "2026-09-06", "source": "feed"},
        ])
        self.storage.replace_store_promotions("tivtaam", [
            {"barcode": "111", "promotion_id": "a", "description": "50%",
             "discounted_price": 12.45, "min_qty": 1,
             "starts_at": "2000-01-01T00:00:00", "ends_at": "2099-01-01T00:00:00",
             "observed_at": "2026-09-06"},
            {"barcode": "222", "promotion_id": "b", "description": "מבצע",
             "discounted_price": 5.0, "min_qty": 1,
             "starts_at": "2000-01-01T00:00:00", "ends_at": "2099-01-01T00:00:00",
             "observed_at": "2026-09-06"},
        ])

    def test_a_bought_product_on_promotion_is_familiar(self) -> None:
        picks = dealfill.picks_for(self.storage, "tivtaam")
        familiar = [p for p in picks if p.familiar]
        self.assertEqual([p.catalog_name for p in familiar], ["ברוקולי קפוא 800 גר"])

    def test_a_feed_only_product_is_novel(self) -> None:
        picks = dealfill.picks_for(self.storage, "tivtaam")
        novel = [p for p in picks if not p.familiar]
        self.assertEqual([p.catalog_name for p in novel], ["ציפס קלאסי 1 קג"])

    def test_an_expired_promotion_is_not_a_deal(self) -> None:
        self.storage.replace_store_promotions("tivtaam", [
            {"barcode": "111", "promotion_id": "a", "description": "old",
             "discounted_price": 12.45, "min_qty": 1,
             "starts_at": "2000-01-01T00:00:00", "ends_at": "2001-01-01T00:00:00",
             "observed_at": "2026-09-06"},
        ])
        self.assertEqual(dealfill.picks_for(self.storage, "tivtaam"), [])

    def test_a_promotion_dearer_than_the_shelf_is_not_a_saving(self) -> None:
        # Multi-buy totals ("2 for ₪80") arrive in this field routinely.
        self.storage.replace_store_promotions("tivtaam", [
            {"barcode": "111", "promotion_id": "a", "description": "2 ב-80",
             "discounted_price": 80.0, "min_qty": 2,
             "starts_at": "2000-01-01T00:00:00", "ends_at": "2099-01-01T00:00:00",
             "observed_at": "2026-09-06"},
        ])
        self.assertEqual(
            [p for p in dealfill.picks_for(self.storage, "tivtaam") if p.familiar], []
        )


if __name__ == "__main__":
    unittest.main()
