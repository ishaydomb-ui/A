"""vNext Phase 2a: the in-memory catalogue shortlist matches the SQL
helpers' semantics (every word contained, apostrophes folded, shortest
first, limit) at a fraction of the cost, and caches per DB path."""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import vnext_catalogue
from grocery_bot.storage import Storage


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(vnext_catalogue.clear_cache)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.record_store_prices("tivtaam", [
            {"barcode": "1", "name": "קוטג' 5% שומן 250 גרם", "price": 5.9, "observed_at": "2026-09-20"},
            {"barcode": "2", "name": "קוטג׳ 3%", "price": 5.5, "observed_at": "2026-09-20"},
            {"barcode": "3", "name": "משקה בננה 1 ליטר", "price": 6.0, "observed_at": "2026-09-20"},
            {"barcode": "4", "name": "בננה", "price": 4.0, "observed_at": "2026-09-20"},
            {"barcode": "5", "name": "מלפפונים קטנים בחומץ", "price": 9.0, "observed_at": "2026-09-20"},
        ])

    def search(self, q, also=None, limit=12, store="tivtaam"):
        return [r["name"] for r in vnext_catalogue.search(self.storage, store, q, limit, also, 900)]


class ShortlistSearchTests(Base):
    def test_every_word_must_be_contained_shortest_first(self):
        self.assertEqual(self.search("בננה"), ["בננה", "משקה בננה 1 ליטר"])
        self.assertEqual(self.search("בננה", also=["משקה"]), ["משקה בננה 1 ליטר"])

    def test_apostrophe_variants_fold_together(self):
        self.assertEqual(sorted(self.search("קוטג'")), sorted(["קוטג' 5% שומן 250 גרם", "קוטג׳ 3%"]))
        self.assertEqual(sorted(self.search("קוטג")), sorted(["קוטג' 5% שומן 250 גרם", "קוטג׳ 3%"]))

    def test_limit_and_empty_query(self):
        self.assertEqual(len(self.search("קוטג", limit=1)), 1)
        self.assertEqual(self.search(""), [])

    def test_agrees_with_the_sql_helper(self):
        sql = [r["name"] for r in self.storage.search_store_price_names("tivtaam", "קוטג", limit=12)]
        self.assertEqual(sorted(self.search("קוטג")), sorted(sql))

    def test_cache_is_per_db_and_reused(self):
        first = vnext_catalogue.shortlist(self.storage, "tivtaam", 900)
        self.storage.record_store_prices("tivtaam", [{"barcode": "9", "name": "חלב", "price": 1.0, "observed_at": "2026-09-20"}])
        self.assertIs(vnext_catalogue.shortlist(self.storage, "tivtaam", 900), first)
        self.assertEqual(self.search("חלב"), [])
        vnext_catalogue.clear_cache()
        self.assertEqual(self.search("חלב"), ["חלב"])


if __name__ == "__main__":
    unittest.main()
