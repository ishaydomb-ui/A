import tempfile
import unittest
from pathlib import Path

from grocery_bot.nlu import ParsedItem
from grocery_bot.pantry import likely_have, split_ingredients
from grocery_bot.stock import StockItem
from grocery_bot.storage import Storage


class PantryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self.storage.replace_stock_items(
            "shufersal",
            [
                StockItem("A", "קוטג' 5% שומן", 0.9, "מוצרי חלב וקירור"),
                StockItem("B", "טונה בשמן קנולה", 0.05, "מזווה ושימורים"),  # tier D
            ],
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_universal_staples_are_probably_home(self) -> None:
        """One bag of flour lasts months — its absence from online history
        proves nothing."""
        for name in ("קמח", "סוכר", "מלח", "אבקת אפייה", "קינמון"):
            self.assertTrue(likely_have(self.storage, name), name)

    def test_a_tier_a_product_is_probably_home(self) -> None:
        self.assertTrue(likely_have(self.storage, "קוטג"))

    def test_a_rare_purchase_is_not_assumed(self) -> None:
        """Tier D means bought once or twice ever — that is not a pantry."""
        self.assertFalse(likely_have(self.storage, "טונה בשמן קנולה"))

    def test_an_unknown_ingredient_is_marked_missing(self) -> None:
        self.assertFalse(likely_have(self.storage, "תמצית שקדים"))

    def test_split_partitions_without_losing_anything(self) -> None:
        ingredients = [
            ParsedItem(name="קמח"),
            ParsedItem(name="תפוחי עץ"),
            ParsedItem(name="קוטג"),
        ]
        missing, have = split_ingredients(self.storage, ingredients)
        self.assertEqual(len(missing) + len(have), 3)
        self.assertEqual([i.name for i in missing], ["תפוחי עץ"])

    def test_geresh_spelling_does_not_break_matching(self) -> None:
        self.assertTrue(likely_have(self.storage, "קוטג׳"))


if __name__ == "__main__":
    unittest.main()
