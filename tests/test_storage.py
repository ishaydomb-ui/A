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
