import tempfile
import unittest
from pathlib import Path

from grocery_bot.catalog import find_cycle_alternatives, format_cycle_alternatives
from grocery_bot.prices import PricedProduct, PromotionItem
from grocery_bot.storage import Storage

FAR_FUTURE = "2099-01-01T00:00:00"
PAST = "2000-01-01T00:00:00"


def _product(code, name, price, weighted=True, uom="1קילוגרם"):
    return PricedProduct(
        item_code=code, name=name, manufacturer="", price=price,
        unit_of_measure_price=0, unit_of_measure=uom, quantity="",
        is_weighted=weighted,
    )


def _promo(code, price, desc="מבצע"):
    return PromotionItem(
        promotion_id="p" + code, description=desc, item_code=code,
        discounted_price=price, min_qty=1, discount_rate=0,
        starts_at=PAST, ends_at=FAR_FUTURE,
    )


class AlternativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_suggests_a_cheaper_promoted_substitute(self) -> None:
        self.storage.replace_catalog(
            [_product("1", "בצל יבש", 5.9), _product("2", "בצל יבש אדום", 7.9)],
            [_promo("2", 4.9)],
        )
        found = find_cycle_alternatives(self.storage, ["בצל יבש"])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][2].name, "בצל יבש אדום")

    def test_ignores_a_different_kind_of_product(self) -> None:
        """The banana trap: snack rings are not a substitute for fruit.

        They share the word and so rank together in a name search, but
        one is sold by the kilo and the other by the packet.
        """
        self.storage.replace_catalog(
            [
                _product("1", "בננה", 12.9),
                _product("2", "בננה ציפס בקופסא", 11.9, weighted=False, uom="100 גרם"),
            ],
            [_promo("2", 4.75)],
        )
        self.assertEqual(find_cycle_alternatives(self.storage, ["בננה"]), [])

    def test_ignores_a_saving_too_small_to_mention(self) -> None:
        self.storage.replace_catalog(
            [_product("1", "בצל יבש", 5.9), _product("2", "בצל יבש אדום", 6.0)],
            [_promo("2", 5.85)],
        )
        self.assertEqual(find_cycle_alternatives(self.storage, ["בצל יבש"]), [])

    def test_ignores_a_more_expensive_promoted_item(self) -> None:
        self.storage.replace_catalog(
            [_product("1", "בצל יבש", 5.9), _product("2", "בצל יבש אורגני", 12.9)],
            [_promo("2", 12.26)],
        )
        self.assertEqual(find_cycle_alternatives(self.storage, ["בצל יבש"]), [])

    def test_no_catalog_match_is_quiet(self) -> None:
        self.storage.replace_catalog([], [])
        self.assertEqual(find_cycle_alternatives(self.storage, ["משהו"]), [])

    def test_message_says_nothing_was_changed(self) -> None:
        """It is a suggestion after the fact, not an approval gate."""
        self.storage.replace_catalog(
            [_product("1", "בצל יבש", 5.9), _product("2", "בצל יבש אדום", 7.9)],
            [_promo("2", 4.9)],
        )
        text = format_cycle_alternatives(find_cycle_alternatives(self.storage, ["בצל יבש"]))
        self.assertIn("לא שיניתי", text)

    def test_empty_suggestions_produce_no_message(self) -> None:
        self.assertEqual(format_cycle_alternatives([]), "")


if __name__ == "__main__":
    unittest.main()
