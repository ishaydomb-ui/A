import tempfile
import unittest
from pathlib import Path

from grocery_bot.storage import Storage


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "test.sqlite3")
        self.storage = Storage(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_add_and_list_base_items(self) -> None:
        self.storage.add_base_list_item("חלב", default_quantity=2)
        self.storage.add_base_list_item("לחם", search_terms={"shufersal": "לחם אחיד"})

        items = self.storage.list_active_base_items()

        self.assertEqual([i.name for i in items], ["חלב", "לחם"])
        self.assertEqual(items[0].default_quantity, 2)
        self.assertEqual(items[1].search_term_for("shufersal"), "לחם אחיד")
        self.assertEqual(items[1].search_term_for("tiv_taam"), "לחם")  # falls back to name

    def test_adhoc_queue_lifecycle(self) -> None:
        req_id = self.storage.add_adhoc_request("סבון כלים", requested_by="ליראן")

        pending = self.storage.list_pending_adhoc()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].text, "סבון כלים")

        self.storage.mark_adhoc_consumed(req_id)
        self.assertEqual(self.storage.list_pending_adhoc(), [])

    def test_ambiguity_lifecycle(self) -> None:
        amb_id = self.storage.save_pending_ambiguity(
            store="shufersal", original_term="טונה", quantity=2, candidates=["טונה א", "טונה ב"]
        )

        pending = self.storage.get_pending_ambiguity(amb_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["candidates"], ["טונה א", "טונה ב"])
        self.assertEqual(len(self.storage.list_pending_ambiguities()), 1)

        self.storage.mark_ambiguity_resolved(amb_id)
        self.assertIsNone(self.storage.get_pending_ambiguity(amb_id))
        self.assertEqual(self.storage.list_pending_ambiguities(), [])

    def test_import_base_list_from_yaml(self) -> None:
        count = self.storage.import_base_list_from_yaml("data/base_list.example.yaml")
        self.assertEqual(count, 3)
        self.assertEqual(len(self.storage.list_active_base_items()), 3)


if __name__ == "__main__":
    unittest.main()


class AmbiguityHygieneTests(unittest.TestCase):
    """Questions must not pile up across runs."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_the_same_term_does_not_stack_up(self) -> None:
        first = self.storage.save_pending_ambiguity("s", "גבינה צהובה", 1, ["a", "b"])
        second = self.storage.save_pending_ambiguity("s", "גבינה צהובה", 1, ["a", "c"])
        self.assertEqual(first, second)
        self.assertEqual(len(self.storage.list_pending_ambiguities()), 1)

    def test_reasking_refreshes_the_options(self) -> None:
        self.storage.save_pending_ambiguity("s", "גבינה", 1, ["ישן"])
        self.storage.save_pending_ambiguity("s", "גבינה", 1, ["חדש"])
        self.assertEqual(self.storage.list_pending_ambiguities()[0]["candidates"], ["חדש"])

    def test_different_terms_are_separate_questions(self) -> None:
        self.storage.save_pending_ambiguity("s", "גבינה", 1, ["a"])
        self.storage.save_pending_ambiguity("s", "קוטג", 1, ["a"])
        self.assertEqual(len(self.storage.list_pending_ambiguities()), 2)

    def test_stale_questions_are_expired(self) -> None:
        import sqlite3

        self.storage.save_pending_ambiguity("s", "ישן", 1, ["a"])
        conn = sqlite3.connect(self.storage._db_path)
        conn.execute("UPDATE pending_ambiguities SET created_at = '2020-01-01T00:00:00+00:00'")
        conn.commit()
        conn.close()
        self.assertEqual(self.storage.expire_stale_ambiguities(6), 1)
        self.assertEqual(self.storage.list_pending_ambiguities(), [])

    def test_fresh_questions_survive_expiry(self) -> None:
        self.storage.save_pending_ambiguity("s", "חדש", 1, ["a"])
        self.storage.expire_stale_ambiguities(6)
        self.assertEqual(len(self.storage.list_pending_ambiguities()), 1)


class StaleAmbiguityIsNotReAskedTests(unittest.TestCase):
    """An unresolved question can outlive its own answer.

    A pending_ambiguities row is only closed when the household taps a
    choice. But the same term can be settled later by a clean resolution,
    a bulk match, or the history import — none of which close the row. On
    2026-09-03 there were 7 open rows from 08-29/30 and six already had a
    remembered product, so the household was queued to be asked again for
    answers the bot was holding. Being asked twice about one thing is the
    small indignity that makes people stop reading a bot.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        from grocery_bot.storage import Storage

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_a_remembered_term_is_filtered_out_of_the_questions_to_ask(self):
        self.storage.save_pending_ambiguity(
            store="shufersal", original_term="קוטג", quantity=1,
            candidates=["קוטג' 5%", "קוטג' 9%"],
        )
        pending = self.storage.list_pending_ambiguities()
        self.assertEqual(len(pending), 1, "row should start unresolved")

        # The answer arrives by another route, which does not close the row.
        self.storage.remember_choice("shufersal", "קוטג", "P_1", "קוטג' 9% שומן")
        still_open = self.storage.list_pending_ambiguities()
        self.assertEqual(len(still_open), 1, "the row itself stays open — that is the trap")

        # The ask path must skip it. This mirrors the filter in
        # telegram_bot._ask_ambiguities.
        askable = [
            p for p in still_open
            if self.storage.preferred_for(p["store"], p["original_term"]) is None
        ]
        self.assertEqual(askable, [], "a question with a known answer must not be asked")

    def test_an_unanswered_term_is_still_asked(self):
        self.storage.save_pending_ambiguity(
            store="shufersal", original_term="מקלות גבינה", quantity=1,
            candidates=["מקלות בורקס", "אצבעות גבינה"],
        )
        pending = self.storage.list_pending_ambiguities()
        askable = [
            p for p in pending
            if self.storage.preferred_for(p["store"], p["original_term"]) is None
        ]
        self.assertEqual(len(askable), 1, "a genuinely open question must survive the filter")


class TivTaamMemorySeedingTests(unittest.TestCase):
    """Seeding a chain's product memory from order history already on disk.

    Shufersal had 309 remembered choices and Tiv Taam zero, although 743
    rows of real Tiv Taam order history were sitting in `store_prices`.
    The import that built Shufersal's memory scrapes that chain's own
    order pages, so Tiv Taam was never seeded — a gap, not a decision.
    Without a memory, every Tiv Taam item goes through the autocomplete
    that returned 4, then 0, then 5, then 1 candidate for one query in a
    single afternoon.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        from grocery_bot.storage import Storage

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _seed_rows(self):
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "111", "name": "קוטג' 5% 250 גרם", "price": 6.7,
             "observed_at": "2026-01-01", "source": "order"},
            {"barcode": "111", "name": "קוטג' 5% 250 גרם", "price": 7.1,
             "observed_at": "2026-06-01", "source": "order"},
            {"barcode": "222", "name": "פיתות במרקם מיוחד", "price": 12.9,
             "observed_at": "2026-05-01", "source": "order"},
        ])

    def test_latest_row_per_barcode_is_what_gets_remembered(self):
        # A product bought repeatedly should be remembered under the name
        # it carried most recently, not its oldest.
        self._seed_rows()
        latest = self.storage.latest_store_prices("tivtaam")
        self.assertEqual(len(latest), 2, "two distinct barcodes, not three rows")
        self.assertEqual(latest["111"]["price"], 7.1)

    def test_seeding_makes_the_product_resolvable_without_the_dropdown(self):
        self._seed_rows()
        for barcode, row in self.storage.latest_store_prices("tivtaam").items():
            self.storage.remember_choice(
                store="tivtaam", term=row["name"],
                product_code=str(barcode), product_name=row["name"],
            )
        got = self.storage.preferred_for("tivtaam", "קוטג' 5% 250 גרם")
        self.assertIsNotNone(got)
        self.assertEqual(got["product_code"], "111")

    def test_seeding_does_not_leak_into_another_chain(self):
        # preferred_products is keyed (store, term); a Tiv Taam memory
        # must not answer for Shufersal, where the same name is a
        # different product code.
        self._seed_rows()
        self.storage.remember_choice("tivtaam", "קוטג' 5% 250 גרם", "111", "קוטג' 5% 250 גרם")
        self.assertIsNone(self.storage.preferred_for("shufersal", "קוטג' 5% 250 גרם"))

    def test_reseeding_refreshes_rather_than_duplicates(self):
        self._seed_rows()
        for _ in range(2):
            self.storage.remember_choice("tivtaam", "פיתות במרקם מיוחד", "222", "פיתות במרקם מיוחד")
        got = self.storage.preferred_for("tivtaam", "פיתות במרקם מיוחד")
        self.assertEqual(got["product_code"], "222")


class LikeWildcardEscapeTests(unittest.TestCase):
    """A literal % in a query must not act as a SQL LIKE wildcard.

    Grocery queries are full of "3%", "5%". A bare `%term%` pattern hands
    that % to SQL, so "חלב 3%" also matched "חלב 36 גרם" (chocolate). The
    fix escapes the user's own wildcards and pairs it with ESCAPE.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        from grocery_bot.storage import Storage

        from grocery_bot.prices import PricedProduct

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.replace_catalog(
            [
                PricedProduct("1", "חלב 3% מהדרין שקית 1 ל", "תנובה", 6.4, 6.4, "1ליטר", "1", False),
                PricedProduct("2", "מטבעות שוקולד חלב 36 גרם", "", 4.9, 136.1, "100 גרם", "36", False),
            ],
            [],
        )

    def test_percent_in_query_is_literal_not_a_wildcard(self):
        names = [p.name for p in self.storage.search_products("חלב 3%")]
        self.assertIn("חלב 3% מהדרין שקית 1 ל", names)
        self.assertNotIn("מטבעות שוקולד חלב 36 גרם", names,
                         "the % must not wildcard-match 'חלב 36'")

    def test_plain_query_still_matches(self):
        names = [p.name for p in self.storage.search_products("חלב")]
        self.assertTrue(any("חלב 3%" in n for n in names))

    def test_helper_escapes_all_three_wildcards(self):
        from grocery_bot.storage import _like_contains
        self.assertEqual(_like_contains("3%"), "%3\\%%")
        self.assertEqual(_like_contains("a_b"), "%a\\_b%")
        self.assertEqual(_like_contains("x\\y"), "%x\\\\y%")


class ApostropheNormalizationTests(unittest.TestCase):
    """Apostrophe/geresh variants must not change what a price search finds.

    Product names are inconsistent about the apostrophe, so "קוטג' 5%"
    (ASCII), "קוטג 5%" (none), and "קוטג׳ 5%" (Hebrew geresh) found 1, 3, 0
    rows respectively — a form-miss masquerading as a real result count.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        from grocery_bot.prices import PricedProduct
        from grocery_bot.storage import Storage

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.replace_catalog(
            [
                PricedProduct("1", "קוטג 5% שומן 250ג טרה", "טרה", 6.7, 26.8, "100 גרם", "250", False),
                PricedProduct("2", "קוטג' 5% 250 גרם בעלז", "בעלז", 6.4, 25.6, "100 גרם", "250", False),
            ],
            [],
        )

    def test_all_apostrophe_forms_return_the_same_set(self):
        counts = {q: len(self.storage.search_products(q))
                  for q in ("קוטג' 5%", "קוטג 5%", "קוטג׳ 5%")}
        self.assertEqual(set(counts.values()), {2}, counts)

    def test_the_fold_helper_drops_the_apostrophe_family(self):
        from grocery_bot.storage import _fold_apostrophes
        self.assertEqual(_fold_apostrophes("קוטג' 5%"), "קוטג 5%")
        self.assertEqual(_fold_apostrophes("קוטג׳ 5%"), "קוטג 5%")


class CrossChainPriceTests(unittest.TestCase):
    """Where is a product cheapest across chains — the canonical answer.

    Shufersal (catalog_products, no barcode) and the other chains
    (store_prices, by barcode) share no key, so this matches by name and
    returns each chain's cheapest hit, honest that sizes/variants differ.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        from grocery_bot.prices import PricedProduct
        from grocery_bot.storage import Storage

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.replace_catalog(
            [PricedProduct("1", "במבה 80 גרם אסם", "אסם", 5.9, 73.8, "100 גרם", "80", False)],
            [],
        )
        # Two observations for one Tiv Taam barcode — only the newest counts.
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "111", "name": "חטיף במבה", "price": 6.0,
             "observed_at": "2026-01-01", "source": "order"},
            {"barcode": "111", "name": "חטיף במבה", "price": 4.3,
             "observed_at": "2026-06-01", "source": "order"},
        ])
        self.storage.record_store_prices("ramilevy", [
            {"barcode": "222", "name": "במבה קלאסי", "price": 4.8,
             "observed_at": "2026-06-01", "source": "feed"},
        ])

    def test_one_row_per_chain_cheapest_first(self):
        rows = self.storage.cross_chain_prices("במבה")
        stores = [r["store"] for r in rows]
        self.assertEqual(set(stores), {"shufersal", "tivtaam", "ramilevy"})
        self.assertEqual(len(stores), len(set(stores)), "one row per chain")
        prices = [r["price"] for r in rows]
        self.assertEqual(prices, sorted(prices), "cheapest first")

    def test_uses_the_newest_observation_per_barcode(self):
        rows = self.storage.cross_chain_prices("במבה")
        tt = next(r for r in rows if r["store"] == "tivtaam")
        self.assertEqual(tt["price"], 4.3, "the June price, not the January one")

    def test_shufersal_carries_a_unit_price_others_do_not(self):
        rows = self.storage.cross_chain_prices("במבה")
        sh = next(r for r in rows if r["store"] == "shufersal")
        self.assertEqual(sh["unit_price"], 73.8)
        tt = next(r for r in rows if r["store"] == "tivtaam")
        self.assertIsNone(tt["unit_price"])

    def test_a_missing_product_is_empty_not_an_error(self):
        self.assertEqual(self.storage.cross_chain_prices("פטריות שיטאקי"), [])

    def test_a_cheap_candy_does_not_beat_a_real_word_boundary_match(self):
        """Reproduces the live bug: 'חלבי' (a candy) is a prefix-only
        substring hit for 'חלב', and used to win purely on being cheaper
        than any actual milk. It must now lose to a genuine whole-word
        match at the same chain, even though it is still cheaper.
        """
        from grocery_bot.prices import PricedProduct

        self.storage.replace_catalog(
            [
                PricedProduct("2", "לימבו פטל חלבי", "שטראוס", 2.0, None, "", "1", False),
                PricedProduct("3", "חלב 3% קרטון", "תנובה", 6.5, 6.5, "1 ליטר", "1", False),
            ],
            [],
        )
        rows = self.storage.cross_chain_prices("חלב")
        shufersal = next(r for r in rows if r["store"] == "shufersal")
        self.assertEqual(shufersal["name"], "חלב 3% קרטון")

    def test_a_pure_substring_with_no_word_boundary_is_excluded_entirely(self):
        """'מחלבה' (dairy plant/creamery) contains 'חלב' mid-word, with no
        boundary on either side — noise, not a weak signal. If it's the
        only candidate at a chain, that chain should be omitted rather
        than shown a coincidental hit.
        """
        self.storage.record_store_prices("victory", [
            {"barcode": "999", "name": "מחלבה טרייה בע\"מ", "price": 1.0,
             "observed_at": "2026-06-01", "source": "feed"},
        ])
        rows = self.storage.cross_chain_prices("חלב")
        self.assertNotIn("victory", [r["store"] for r in rows])


class RecentOrderPricesTests(unittest.TestCase):
    """storage.recent_order_prices -- added 2026-09-18 for the Work
    planner snapshot. Only source='order' rows count as a real price
    paid; source='feed' (the public price-transparency scrape) is a
    shelf price observed on some day, not evidence of a purchase.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_only_order_sourced_rows_are_returned(self):
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "1", "name": "חלב", "price": 6.9, "observed_at": "2026-09-01", "source": "feed"},
            {"barcode": "1", "name": "חלב", "price": 7.2, "observed_at": "2026-08-15", "source": "order"},
        ])
        rows = self.storage.recent_order_prices("tivtaam", "1")
        self.assertEqual(rows, [{"date": "2026-08-15", "price": 7.2}])

    def test_newest_first_and_limited(self):
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "1", "name": "חלב", "price": p, "observed_at": d, "source": "order"}
            for d, p in [("2026-07-01", 6.5), ("2026-08-01", 6.7), ("2026-09-01", 6.9)]
        ])
        rows = self.storage.recent_order_prices("tivtaam", "1", limit=2)
        self.assertEqual([r["date"] for r in rows], ["2026-09-01", "2026-08-01"])

    def test_a_different_store_or_barcode_never_leaks_in(self):
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "1", "name": "חלב", "price": 6.9, "observed_at": "2026-09-01", "source": "order"},
        ])
        self.assertEqual(self.storage.recent_order_prices("shufersal", "1"), [])
        self.assertEqual(self.storage.recent_order_prices("tivtaam", "2"), [])

    def test_no_data_is_an_empty_list_not_an_error(self):
        self.assertEqual(self.storage.recent_order_prices("tivtaam", "999"), [])


class TivtaamOrderLinesTests(unittest.TestCase):
    """storage.record_tivtaam_order_lines / tivtaam_purchase_lines* --
    added 2026-09-20 so real ordered-vs-delivered quantity, weightable and
    line price survive past the moment tivtaamhistory.order_lines() parses
    them, instead of being discarded before any INSERT (the audit's
    finding: nothing in this schema used to store this)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _line(self, **kw):
        base = {
            "code": "16323094", "barcode": "693493231749", "name": "חסה לאליק הידרופונית",
            "quantity": 1, "actual_quantity": 1, "weighable": False,
            "price": 14.9, "total": 14.9, "substituted": False,
        }
        base.update(kw)
        return base

    def test_ordered_and_actual_quantity_are_preserved_separately(self):
        self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [
            self._line(code="2", name="בננות", quantity=0.5, actual_quantity=0.332, weighable=True),
        ])
        lines = self.storage.tivtaam_purchase_lines_for("2")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["ordered_quantity"], 0.5)
        self.assertEqual(lines[0]["actual_quantity"], 0.332)
        self.assertNotEqual(lines[0]["ordered_quantity"], lines[0]["actual_quantity"])

    def test_weightable_flag_and_kg_unit_survive_for_a_weighed_product(self):
        self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [
            self._line(code="2", name="בננות", quantity=0.5, actual_quantity=0.332, weighable=True),
        ])
        row = self.storage.tivtaam_purchase_lines_for("2")[0]
        self.assertEqual(row["weightable"], 1)
        self.assertEqual(row["unit"], 'ק"ג')

    def test_non_weighed_product_has_no_kg_unit(self):
        self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [self._line()])
        row = self.storage.tivtaam_purchase_lines_for("16323094")[0]
        self.assertEqual(row["weightable"], 0)
        self.assertEqual(row["unit"], "")

    def test_price_and_barcode_are_kept(self):
        self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [self._line()])
        row = self.storage.tivtaam_purchase_lines_for("16323094")[0]
        self.assertEqual(row["price"], 14.9)
        self.assertEqual(row["barcode"], "693493231749")
        self.assertEqual(row["order_date"], "2026-09-12")

    def test_lines_without_a_product_code_are_skipped(self):
        n = self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [self._line(code="")])
        self.assertEqual(n, 0)
        self.assertEqual(self.storage.tivtaam_purchase_lines(), [])

    def test_recording_the_same_order_twice_does_not_duplicate(self):
        for _ in range(2):
            self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [self._line()])
        self.assertEqual(len(self.storage.tivtaam_purchase_lines_for("16323094")), 1)

    def test_order_lines_recorded_flag(self):
        self.assertFalse(self.storage.tivtaam_order_lines_recorded("O1"))
        self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [self._line()])
        self.assertTrue(self.storage.tivtaam_order_lines_recorded("O1"))
        self.assertFalse(self.storage.tivtaam_order_lines_recorded("O2"))

    def test_lines_for_order_scoped_to_that_order(self):
        self.storage.record_tivtaam_order_lines("O1", "2026-09-12", [self._line(code="1"), self._line(code="2")])
        self.storage.record_tivtaam_order_lines("O2", "2026-09-13", [self._line(code="3")])
        self.assertEqual(
            sorted(r["product_code"] for r in self.storage.tivtaam_lines_for_order("O1")),
            ["1", "2"],
        )
        self.assertEqual([r["product_code"] for r in self.storage.tivtaam_lines_for_order("O2")], ["3"])
        self.assertEqual(self.storage.tivtaam_lines_for_order("MISSING"), [])

    def test_since_days_filters_by_order_date(self):
        self.storage.record_tivtaam_order_lines("OLD", "2020-01-01", [self._line(code="1")])
        self.storage.record_tivtaam_order_lines("NEW", "2026-09-19", [self._line(code="2")])
        recent = self.storage.tivtaam_purchase_lines(since_days=30)
        self.assertEqual([r["product_code"] for r in recent], ["2"])
        everything = self.storage.tivtaam_purchase_lines()
        self.assertEqual(len(everything), 2)


class ListOrdersTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.log_orders([
            {"code": "A", "placed_at": "2026-08-01T10:00:00", "total": 100.0, "item_count": 10},
            {"code": "B", "placed_at": "2026-09-01T10:00:00", "total": 200.0, "item_count": 20},
        ], store="tivtaam")
        self.storage.log_orders([
            {"code": "C", "placed_at": "2026-09-05T10:00:00", "total": 50.0, "item_count": 5},
        ], store="shufersal")

    def test_newest_first_and_store_scoped(self):
        orders = self.storage.list_orders("tivtaam")
        self.assertEqual([o["order_code"] for o in orders], ["B", "A"])

    def test_limit(self):
        orders = self.storage.list_orders("tivtaam", limit=1)
        self.assertEqual([o["order_code"] for o in orders], ["B"])

    def test_other_store_not_included(self):
        orders = self.storage.list_orders("tivtaam")
        self.assertNotIn("C", [o["order_code"] for o in orders])


if __name__ == "__main__":
    unittest.main()
