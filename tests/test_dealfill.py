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

    def test_a_two_for_one_price_is_not_a_single_unit_price(self) -> None:
        # Live 2026-09-11: "2ב5 פתיתים ללא גלוטן350 אסם", shelf ₪13.90.
        # ₪5 buys two; one still costs ₪13.90, so the 64% never happens.
        self._catalog((_product("1", "פתיתים ללא גלוטן", 13.9),
                       _promo("1", 5.0, desc="2ב5 פתיתים ללא גלוטן350 אסם")))
        self.assertEqual(dealfill.picks_for(self.storage, "shufersal"), [])

    def test_a_leading_price_is_a_price_and_not_a_quantity(self) -> None:
        # Shufersal writes single-unit deals as "19.90 מרק בצל/פטריות".
        # Reading that leading number as a quantity would refuse most of
        # the feed's real deals.
        self._catalog((_product("1", "מרק פטריות קנור", 19.9),
                       _promo("1", 9.9, desc="9.90 מרק בצל/פטריות 400 גרם")))
        picks = dealfill.picks_for(self.storage, "shufersal")
        self.assertEqual([p.catalog_name for p in picks], ["מרק פטריות קנור"])

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

    def _promo(self, description, min_qty=1.0) -> None:
        self.storage.replace_store_promotions("tivtaam", [
            {"barcode": "111", "promotion_id": "a", "description": description,
             "discounted_price": 12.45, "min_qty": min_qty,
             "starts_at": "2000-01-01T00:00:00", "ends_at": "2099-01-01T00:00:00",
             "observed_at": "2026-09-06"},
        ])

    def test_a_second_unit_promotion_is_not_a_saving_on_one_unit(self) -> None:
        # "השני ב 50%" prices the *second* bag. Buying one costs the full
        # shelf price, so the 50% the feed implies never happens.
        self._promo("מבצע השני ב 50%")
        self.assertEqual(dealfill.picks_for(self.storage, "tivtaam"), [])

    def test_min_qty_cannot_be_trusted_to_flag_those(self) -> None:
        # The live feed had 1,530 multi-buy promotions carrying min_qty=1,
        # which is why the wording is checked and not just the number.
        self._promo("קנה 2 המבורגרים בל ציפס ב 5 ש\"ח")
        self.assertEqual(dealfill.picks_for(self.storage, "tivtaam"), [])

    def test_min_qty_above_one_is_refused_whatever_the_wording_says(self) -> None:
        self._promo("מבצע", min_qty=2.0)
        self.assertEqual(dealfill.picks_for(self.storage, "tivtaam"), [])

    def test_a_plain_single_unit_promotion_is_still_a_deal(self) -> None:
        # The guard must not swallow ordinary price promotions, which are
        # most of the feed.
        self._promo("ברוקולי ב- 12.45")
        picks = dealfill.picks_for(self.storage, "tivtaam")
        self.assertEqual([p.catalog_name for p in picks], ["ברוקולי קפוא 800 גר"])

    def _perishable_bought_before(self) -> None:
        """The live case: a previously-bought perishable, deeply discounted.

        "בצק פריך מלוח" at 41% off was in the cart on 2026-09-11 because
        the perishable guard only ran for unfamiliar products.
        """
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "333", "name": "בצק פריך מלוח 500 גר", "price": 22.9,
             "observed_at": "2026-08-01", "source": "order"},
        ])
        self.storage.replace_store_promotions("tivtaam", [
            {"barcode": "333", "promotion_id": "c", "description": "41%",
             "discounted_price": 13.5, "min_qty": 1,
             "starts_at": "2000-01-01T00:00:00", "ends_at": "2099-01-01T00:00:00",
             "observed_at": "2026-09-06"},
        ])

    def test_a_perishable_bought_before_is_still_left_to_the_report(self) -> None:
        # Having bought it once does not mean a surprise second one gets
        # eaten in time. Same rule as the Shufersal path, which gates its
        # familiar picks on the department taxonomy.
        self._perishable_bought_before()
        picks = dealfill.picks_for(self.storage, "tivtaam")
        self.assertEqual([p.catalog_name for p in picks if p.familiar], [])

    def test_asking_for_perishables_still_returns_the_familiar_one(self) -> None:
        # pantryable_only=False is the caller saying "include them".
        self._perishable_bought_before()
        picks = dealfill.picks_for(self.storage, "tivtaam", pantryable_only=False)
        self.assertEqual(
            [p.catalog_name for p in picks if p.familiar], ["בצק פריך מלוח 500 גר"]
        )

    def test_a_novel_perishable_is_refused_even_when_perishables_are_asked_for(self) -> None:
        # Never bought, no department data, name-only evidence: the one
        # case where the guard is not the caller's to lift.
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "444", "name": "יוגורט תות 150 גר", "price": 6.9,
             "observed_at": "2026-09-06", "source": "feed"},
        ])
        self.storage.replace_store_promotions("tivtaam", [
            {"barcode": "444", "promotion_id": "d", "description": "מבצע",
             "discounted_price": 2.5, "min_qty": 1,
             "starts_at": "2000-01-01T00:00:00", "ends_at": "2099-01-01T00:00:00",
             "observed_at": "2026-09-06"},
        ])
        picks = dealfill.picks_for(self.storage, "tivtaam", pantryable_only=False)
        self.assertEqual([p.catalog_name for p in picks if not p.familiar], [])


class PerishableWordTests(unittest.TestCase):
    """The keyword guard reads a product name, so it has to read Hebrew
    words rather than substrings. Every case here was found in the live
    Tiv Taam feed on 2026-09-11."""

    def test_a_stem_does_not_match_the_middle_of_a_word(self) -> None:
        for pantry in ("אטריות אורז", "רוטב טריאקי", "כפפות ניטריל",
                       "פטריות שמפיניון פרוסות", "דגני בוקר"):
            with self.subTest(pantry):
                self.assertFalse(dealfill._looks_perishable(pantry))

    def test_the_real_perishables_still_match(self) -> None:
        for fresh in ("סלמון טרי", "חלב 3%", "גבינת קוטג", "לחם אחיד",
                      "בצק פריך מלוח 900 גר מעדנות", "עגבניות שרי",
                      "פירות יער קפואים", "דג מושט"):
            with self.subTest(fresh):
                self.assertTrue(dealfill._looks_perishable(fresh))

    def test_a_prefix_letter_does_not_hide_a_perishable(self) -> None:
        self.assertTrue(dealfill._looks_perishable("מארז ובשר טחון"))


class ConditionNoteTests(unittest.TestCase):
    """Conditions the feeds state only in words. The price is real; the
    saving is real *if* the condition holds, and only a person can say."""

    def test_a_minimum_basket_condition_is_named_on_the_line(self) -> None:
        pick = dealfill.DealPick(
            term="דבש", catalog_name="דבש טבעי לחיץ 250 גרם",
            shelf_price=16.9, deal_price=5.0, discount=0.70,
            description="5 דבש טבעי לחיץ 250 גרם-מות150",
        )
        self.assertIn("מותנה בקנייה מעל 150₪", pick.label)

    def test_a_club_price_is_named_on_the_line(self) -> None:
        # Kept rather than filtered: the household is in TivCoins. That
        # was never checked against the account, so it is said out loud.
        pick = dealfill.DealPick(
            term="יין", catalog_name="יין מבעבע", shelf_price=66.9,
            deal_price=49.9, discount=0.25, description="יין 49.90 - מועדון",
        )
        self.assertIn("מחיר מועדון", pick.label)

    def test_an_ordinary_deal_says_nothing_extra(self) -> None:
        pick = dealfill.DealPick(
            term="אורז", catalog_name="אורז בסמטי", shelf_price=14.9,
            deal_price=10.0, discount=0.33, description="10 אורז בסמטי גונאם 1 ק\"ג",
        )
        self.assertNotIn("·  ", pick.label)
        self.assertTrue(pick.label.endswith("₪)"))


class MultiBuyOfferTests(unittest.TestCase):
    """Refused from the cart, reported anyway — see multi_buy_offers."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "111", "name": "ברוקולי קפוא 800 גר", "price": 24.9,
             "observed_at": "2026-08-01", "source": "order"},
            {"barcode": "222", "name": "ציפס קלאסי 1 קג", "price": 17.9,
             "observed_at": "2026-09-06", "source": "feed"},
        ])
        self.storage.replace_store_promotions("tivtaam", [
            {"barcode": "111", "promotion_id": "a", "description": "מבצע השני ב 50%",
             "discounted_price": 12.45, "min_qty": 1,
             "starts_at": PAST, "ends_at": FUTURE, "observed_at": "2026-09-06"},
            {"barcode": "222", "promotion_id": "b", "description": "קנה 2 ציפס",
             "discounted_price": 5.0, "min_qty": 1,
             "starts_at": PAST, "ends_at": FUTURE, "observed_at": "2026-09-06"},
        ])

    def test_a_second_unit_deal_on_a_usual_product_is_reported(self) -> None:
        offers = dealfill.multi_buy_offers(self.storage, "tivtaam")
        self.assertEqual([o.name for o in offers], ["ברוקולי קפוא 800 גר"])

    def test_a_multi_buy_on_a_stranger_is_not_reported(self) -> None:
        # Two units of something never bought is two units of a guess.
        offers = dealfill.multi_buy_offers(self.storage, "tivtaam")
        self.assertNotIn("ציפס קלאסי 1 קג", [o.name for o in offers])

    def test_the_promotion_wording_is_passed_through_not_a_saving(self) -> None:
        # The two chains mean opposite things by discounted_price, so a
        # single computed saving would be wrong half the time.
        text = dealfill.format_multi_buy_offers(
            dealfill.multi_buy_offers(self.storage, "tivtaam")
        )
        self.assertIn("מבצע השני ב 50%", text)
        self.assertIn("24.90", text)
        self.assertNotIn("חיסכון", text)

    def test_nothing_to_report_formats_to_nothing(self) -> None:
        self.assertEqual(dealfill.format_multi_buy_offers([]), "")


if __name__ == "__main__":
    unittest.main()
