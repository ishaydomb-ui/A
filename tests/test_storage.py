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
