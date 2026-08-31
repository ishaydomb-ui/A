import tempfile
import unittest
from pathlib import Path

from grocery_bot.storage import Storage

# The product from the reported failure: visible in the proposal the user
# was reading, on neither typed list, so "תוריד סימילאק" answered
# "not on the list" three times in a row.
SIMILAC = "סימילאק גולד שלב 3"
CODE = "P_5391523057943"


class SuppressLearnedStockItemTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.execute(
                "INSERT INTO stock_items (store, product_code, product_name, tier, "
                "picked_count) VALUES (?, ?, ?, ?, ?)",
                ("shufersal", CODE, SIMILAC, "C", 7),
            )
            conn.commit()

    def _row(self):
        return self.storage.list_stock_items("shufersal")[0]

    def test_partial_name_finds_the_learned_product(self):
        # The user says "סימילאק", the stock row says "סימילאק גולד שלב 3".
        self.assertEqual(self.storage.suppress_stock_item_by_name("סימילאק"), SIMILAC)

    def test_longer_phrase_than_the_stored_name_still_matches(self):
        self.assertEqual(
            self.storage.suppress_stock_item_by_name("סימילאק גולד שלב 3 בבקשה"),
            SIMILAC,
        )

    def test_suppression_outweighs_a_long_buying_history(self):
        self.storage.suppress_stock_item_by_name("סימילאק")
        row = self._row()
        self.assertGreater(row["skipped_count"], row["picked_count"])
        # Past picks must not keep voting for an item the user just refused.
        self.assertEqual(row["picked_count"], 0)

    def test_row_is_kept_not_deleted(self):
        # The nightly sync rebuilds this table from real order history, so
        # a deleted row would quietly come back; a skip count survives.
        self.storage.suppress_stock_item_by_name("סימילאק")
        self.assertEqual(len(self.storage.list_stock_items("shufersal")), 1)

    def test_unknown_product_reports_nothing_rather_than_guessing(self):
        self.assertIsNone(self.storage.suppress_stock_item_by_name("קוואקר"))

    def test_blank_name_matches_nothing(self):
        # A bare "תוריד" must not wipe an arbitrary product.
        self.assertIsNone(self.storage.suppress_stock_item_by_name("   "))
        self.assertEqual(self._row()["skipped_count"], 0)

    def test_shortest_match_wins_when_several_could_apply(self):
        with self.storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO stock_items (store, product_code, product_name) "
                "VALUES (?, ?, ?)",
                ("shufersal", "P_2", "סימילאק"),
            )
            conn.commit()
        self.assertEqual(self.storage.suppress_stock_item_by_name("סימילאק"), "סימילאק")

    def test_other_store_is_untouched(self):
        with self.storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO stock_items (store, product_code, product_name) "
                "VALUES (?, ?, ?)",
                ("tivtaam", CODE, SIMILAC),
            )
            conn.commit()
        self.storage.suppress_stock_item_by_name("סימילאק", store="shufersal")
        self.assertEqual(self.storage.list_stock_items("tivtaam")[0]["skipped_count"], 0)


if __name__ == "__main__":
    unittest.main()
