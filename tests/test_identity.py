"""Barcode-first identity — Phase 6. Resolve before the browser guesses.

Measured basis: 83% of the Tiv Taam standing plan resolves by a feed
barcode; 7-digit codes are retailer-local produce codes and never cross
chains; pack variants with different barcodes stay different products.
"""
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from grocery_bot import identity
from grocery_bot.models import CartAddResult, PlanTerm
from grocery_bot.orchestrator import add_terms_to_cart
from grocery_bot.stock import StockItem
from grocery_bot.storage import Storage


class _Adapter:
    name = "tivtaam"

    def __init__(self):
        self.specific = []
        self.searched = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def add_specific_product(self, label, quantity=1, product_code="", search_term=""):
        self.specific.append((label, product_code, search_term))
        return CartAddResult(item_name=label, store=self.name, status="added", quantity=quantity,
                             product_code=product_code, verification="verified")

    def search_and_add(self, term, quantity=1):
        self.searched.append(term)
        return CartAddResult(item_name=term, store=self.name, status="ambiguous",
                             candidates=["א", "ב"])


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        # Never the live Self-Point API from a test; SiteNameTests opt in.
        patcher = mock.patch("grocery_bot.identity.site_product", return_value=None)
        self.site = patcher.start()
        self.addCleanup(patcher.stop)

    def _feed(self, store, barcode, name, price=9.9):
        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO store_prices (store, barcode, name, price, observed_at, source)"
                " VALUES (?, ?, ?, ?, '2026-09-17', 'feed')", (store, barcode, name, price))
            conn.commit()

    def _stock(self, store, code, name, barcode):
        item = StockItem(code, name, 0.5, "שונות")
        self.storage.replace_stock_items(store, [item])
        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute("UPDATE stock_items SET barcode=? WHERE store=? AND product_code=?",
                         (barcode, store, code))
            conn.commit()


class ResolveTests(Base):
    def test_a_stock_need_with_a_feed_barcode_resolves_to_the_feed_product(self):
        self._stock("tivtaam", "19314408", "פלפל אדום", "9913002")
        self._feed("tivtaam", "9913002", "פלפל אדום", 8.9)
        ident = identity.resolve(self.storage, "tivtaam", PlanTerm("פלפל אדום", 1, "stock", "19314408"))
        self.assertEqual((ident.basis, ident.name, ident.product_code), ("barcode", "פלפל אדום", "19314408"))

    def test_a_feed_name_that_differs_from_the_stock_name_is_the_canonical_one(self):
        # The order line said "טבעפרוסט תרד 800 גרם"; the feed calls it "טבעפרוסט תרד".
        self._stock("tivtaam", "555", "טבעפרוסט תרד 800 גרם", "7290000000001")
        self._feed("tivtaam", "7290000000001", "טבעפרוסט תרד", 11.9)
        ident = identity.resolve(self.storage, "tivtaam", PlanTerm("טבעפרוסט תרד 800 גרם", 1, "stock", "555"))
        self.assertEqual(ident.name, "טבעפרוסט תרד")

    def test_no_barcode_falls_back_to_the_retailer_code(self):
        self._stock("shufersal", "P_4127329", "קוטג' 5% שומן", "")
        ident = identity.resolve(self.storage, "shufersal", PlanTerm("קוטג' 5% שומן", 1, "stock", "P_4127329"))
        self.assertEqual(ident.basis, "stock_code")
        self.assertEqual(ident.product_code, "P_4127329")

    def test_a_non_stock_need_is_left_to_the_name_path(self):
        self.assertIsNone(identity.resolve(self.storage, "tivtaam", PlanTerm("חלב", 1, "adhoc", "7")))
        self.assertIsNone(identity.resolve(self.storage, "tivtaam", PlanTerm("חלב", 1)))

    def test_an_unknown_stock_code_is_none(self):
        self.assertIsNone(identity.resolve(self.storage, "tivtaam", PlanTerm("x", 1, "stock", "nope")))

    def test_the_flag_turns_it_off(self):
        self._stock("tivtaam", "1", "חלב", "7290000000002")
        self._feed("tivtaam", "7290000000002", "חלב")
        with mock.patch.dict("os.environ", {"GORDON_IDENTITY": "name"}):
            self.assertIsNone(identity.resolve(self.storage, "tivtaam", PlanTerm("חלב", 1, "stock", "1")))


class SiteNameTests(Base):
    """Tiv Taam's site names its products differently from its feed (7/39 agree, 26.09)."""

    def test_the_sites_own_name_and_id_win(self):
        self._stock("tivtaam", "10297185", "שמן חמניות 3ל", "7290015888301")
        self._feed("tivtaam", "7290015888301", "שמן חמניות 3ל")
        self.site.return_value = {"id": "10297185", "name": "שוקחה שמן חמניות מזוכך 3 ליטר"}
        ident = identity.resolve(self.storage, "tivtaam", PlanTerm("שמן", 1, "stock", "10297185"))
        self.assertEqual((ident.basis, ident.name), ("barcode_site", "שוקחה שמן חמניות מזוכך 3 ליטר"))

    def test_an_api_failure_keeps_the_feed_name(self):
        self._stock("tivtaam", "1", "חלב", "7290000000002")
        self._feed("tivtaam", "7290000000002", "חלב 1%")
        self.site.return_value = None
        ident = identity.resolve(self.storage, "tivtaam", PlanTerm("חלב", 1, "stock", "1"))
        self.assertEqual((ident.basis, ident.name), ("barcode", "חלב 1%"))

    def test_site_product_is_only_for_self_point_chains_and_never_raises(self):
        mock.patch.stopall()
        self.assertIsNone(identity.site_product("shufersal", "7290000000002"))
        with mock.patch("grocery_bot.adapters.selfpoint.SelfPointPrices", side_effect=RuntimeError("no proxy")):
            identity._SITE_CACHE.clear()
            self.assertIsNone(identity.site_product("tivtaam", "7290000000003"))


class ChainLocalityTests(Base):
    def test_a_seven_digit_code_is_chain_local(self):
        self.assertTrue(identity.is_chain_local("9913002"))
        self.assertFalse(identity.is_global("9913002"))

    def test_an_ean13_is_global(self):
        self.assertTrue(identity.is_global("7290000046020"))
        self.assertFalse(identity.is_chain_local("7290000046020"))

    def test_a_variable_weight_prefix_is_chain_local(self):
        self.assertTrue(identity.is_chain_local("2001234567890"))

    def test_a_plu_never_crosses_chains(self):
        self._feed("politzer", "9913002", "פלפל אדום")
        self.assertIsNone(identity.resolve_cross_chain(self.storage, "9913002", "פלפל אדום", "politzer"))

    def test_an_ean_crosses_when_the_names_agree(self):
        self._feed("politzer", "7290000046020", "גבינה צהובה גלבוע 22%")
        ident = identity.resolve_cross_chain(self.storage, "7290000046020", "גבינה צהובה גלבוע 22% - בפיקוח", "politzer")
        self.assertEqual(ident.basis, "cross_chain")
        self.assertEqual(ident.store, "politzer")

    def test_an_ean_is_refused_when_the_names_share_nothing(self):
        # The 29-of-522 case: one barcode, two unrelated-looking names.
        self._feed("ramilevy", "7290110564360", "החומוס סדרה מובחרת 400")
        self.assertIsNone(identity.resolve_cross_chain(self.storage, "7290110564360", "שקיות אשפה", "ramilevy"))

    def test_pack_variants_stay_distinct(self):
        self._stock("tivtaam", "a", "שמן חמניות 3ל", "7290015888301")
        self._feed("tivtaam", "7290015888301", "שמן חמניות 3 ליטר")
        self._feed("tivtaam", "7290015888196", "שמן חמניות 1 ליטר")
        ident = identity.resolve(self.storage, "tivtaam", PlanTerm("שמן חמניות 3ל", 1, "stock", "a"))
        self.assertEqual(ident.barcode, "7290015888301")
        self.assertIn("3 ליטר", ident.name)


class FillPathTests(Base):
    def test_a_barcode_need_bypasses_search_entirely(self):
        self._stock("tivtaam", "19314408", "פלפל אדום", "9913002")
        self._feed("tivtaam", "9913002", "פלפל אדום", 8.9)
        ad = _Adapter()
        run_id = self.storage.start_cart_run("manual")
        add_terms_to_cart(self.storage, {"tivtaam": lambda: ad},
                          [PlanTerm("פלפל אדום", 1, "stock", "19314408")], run_id=run_id)
        self.assertEqual(ad.searched, [])                                  # no browser guess
        self.assertEqual(ad.specific, [("פלפל אדום", "19314408", "פלפל אדום")])
        item = self.storage.run_items_for(run_id)[0]
        self.assertEqual(item["outcome"], "verified")
        self.assertEqual(item["product_code"], "19314408")
        self.assertEqual(self.storage.list_pending_ambiguities(), [])      # no question

    def test_a_need_without_identity_still_takes_the_name_path(self):
        ad = _Adapter()
        add_terms_to_cart(self.storage, {"tivtaam": lambda: ad}, [PlanTerm("בצל ירוק", 1, "adhoc", "3")])
        self.assertEqual(ad.searched, ["בצל ירוק"])

    def test_a_remembered_human_choice_outranks_identity(self):
        self._stock("tivtaam", "19314408", "פלפל אדום", "9913002")
        self._feed("tivtaam", "9913002", "פלפל אדום")
        self.storage.remember_choice("tivtaam", "פלפל אדום", "OTHER", "פלפל אדום אורגני")
        ad = _Adapter()
        add_terms_to_cart(self.storage, {"tivtaam": lambda: ad},
                          [PlanTerm("פלפל אדום", 1, "stock", "19314408")])
        self.assertEqual(ad.specific[0][1], "OTHER")

    def test_an_identity_miss_falls_through_to_the_name_path(self):
        self._stock("tivtaam", "19314408", "פלפל אדום", "9913002")
        self._feed("tivtaam", "9913002", "פלפל אדום")

        class Missing(_Adapter):
            def add_specific_product(self, label, quantity=1, product_code="", search_term=""):
                self.specific.append((label, product_code, search_term))
                return CartAddResult(item_name=label, store=self.name, status="not_found")
        ad = Missing()
        add_terms_to_cart(self.storage, {"tivtaam": lambda: ad},
                          [PlanTerm("פלפל אדום", 1, "stock", "19314408")])
        self.assertTrue(ad.specific)
        self.assertEqual(ad.searched, ["פלפל אדום"])


if __name__ == "__main__":
    unittest.main()
