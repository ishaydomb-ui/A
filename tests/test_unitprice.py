import tempfile
import unittest
from pathlib import Path

from grocery_bot.catalog import find_cheaper_equivalents, format_cheaper_equivalents
from grocery_bot.prices import PricedProduct, PromotionItem
from grocery_bot.storage import Storage
from grocery_bot.unitprice import best_value, for_product, unit_price

FUTURE, PAST = "2099-01-01T00:00:00", "2000-01-01T00:00:00"


def _p(code, name, price, uom_price, uom, weighted=False, maker=""):
    return PricedProduct(
        item_code=code, name=name, manufacturer=maker, price=price,
        unit_of_measure_price=uom_price, unit_of_measure=uom, quantity="",
        is_weighted=weighted,
    )


def _promo(code, price):
    return PromotionItem(
        promotion_id="p" + code, description="מבצע", item_code=code,
        discounted_price=price, min_qty=1, discount_rate=0, starts_at=PAST, ends_at=FUTURE,
    )


class NormalisationTests(unittest.TestCase):
    def test_per_100g_becomes_per_kilo(self) -> None:
        self.assertEqual(unit_price(2.56, "100 גרם").value, 25.6)

    def test_per_kilo_stays_per_kilo(self) -> None:
        self.assertEqual(unit_price(25.6, "1קילוגרם").value, 25.6)

    def test_per_100ml_becomes_per_litre(self) -> None:
        self.assertEqual(unit_price(1.33, "100 מיליליטר").value, 13.3)

    def test_weight_and_volume_are_different_dimensions(self) -> None:
        self.assertNotEqual(
            unit_price(1, "100 גרם").dimension, unit_price(1, "1ליטר").dimension
        )

    def test_an_unknown_unit_gives_nothing(self) -> None:
        self.assertIsNone(unit_price(5.0, "חבילות"))

    def test_a_promotion_scales_the_unit_price(self) -> None:
        """The feed's ratio describes the shelf price, not the offer."""
        result = unit_price(2.0, "100 גרם", shelf_price=10.0, effective_price=5.0)
        self.assertEqual(result.value, 10.0)


class BestValueTests(unittest.TestCase):
    def test_picks_the_lowest_per_kilo_not_the_lowest_sticker(self) -> None:
        """A small tub looks cheap and costs more per kilo."""
        entries = [
            (_p("1", "קוטג 250ג", 6.40, 2.56, "100 גרם"), None),
            (_p("2", "קוטג בקטנה 100ג", 3.30, 3.30, "100 גרם"), None),
        ]
        self.assertEqual(best_value(entries), 0)

    def test_a_single_product_has_no_best_value(self) -> None:
        self.assertIsNone(best_value([(_p("1", "x", 5, 5, "100 גרם"), None)]))

    def test_mixed_dimensions_do_not_produce_a_nonsense_winner(self) -> None:
        entries = [
            (_p("1", "a", 5, 5, "100 גרם"), None),
            (_p("2", "b", 5, 0.05, "מטרים"), None),
        ]
        self.assertIsNone(best_value(entries))


class CheaperEquivalentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_finds_a_cheaper_equivalent_across_pack_sizes(self) -> None:
        """A 3L bottle is priced per litre and a 750ml one per 100ml.

        Comparing the raw unit strings would call them incomparable,
        which would miss the entire point of the question.
        """
        self.storage.replace_catalog(
            [
                _p("1", "שמן זית 3 ליטר", 129.90, 43.30, "1ליטר"),
                _p("2", "שמן זית 750 מל", 10.00, 1.333, "100 מיליליטר"),
            ],
            [],
        )
        reference, cheaper = find_cheaper_equivalents(
            self.storage, "שמן זית", reference_name="שמן זית 3 ליטר"
        )
        self.assertEqual(reference.item_code, "1")
        self.assertEqual(len(cheaper), 1)
        self.assertGreater(cheaper[0][2], 0.5)

    def test_the_reference_defaults_to_the_closest_name_match(self) -> None:
        self.storage.replace_catalog(
            [_p("1", "שמן זית", 20.0, 2.0, "100 מיליליטר")], []
        )
        reference, _ = find_cheaper_equivalents(self.storage, "שמן זית")
        self.assertEqual(reference.item_code, "1")

    def test_a_marginal_difference_is_not_reported(self) -> None:
        self.storage.replace_catalog(
            [
                _p("1", "קוטג א", 6.40, 2.56, "100 גרם"),
                _p("2", "קוטג ב", 6.20, 2.48, "100 גרם"),
            ],
            [],
        )
        _, cheaper = find_cheaper_equivalents(self.storage, "קוטג")
        self.assertEqual(cheaper, [])

    def test_says_so_when_the_usual_choice_is_already_good(self) -> None:
        self.storage.replace_catalog([_p("1", "קוטג", 6.40, 2.56, "100 גרם")], [])
        reference, cheaper = find_cheaper_equivalents(self.storage, "קוטג")
        self.assertIn("כבר בחירה טובה", format_cheaper_equivalents(reference, cheaper, "קוטג"))

    def test_weighted_and_packaged_are_never_compared(self) -> None:
        self.storage.replace_catalog(
            [
                _p("1", "בננה", 12.9, 12.9, "1קילוגרם", weighted=True),
                _p("2", "בננה ציפס", 4.0, 0.4, "100 גרם"),
            ],
            [],
        )
        _, cheaper = find_cheaper_equivalents(self.storage, "בננה")
        self.assertEqual(cheaper, [])

    def test_a_missing_product_is_reported_plainly(self) -> None:
        self.storage.replace_catalog([], [])
        reference, cheaper = find_cheaper_equivalents(self.storage, "משהו")
        self.assertIn("לא מצאתי", format_cheaper_equivalents(reference, cheaper, "משהו"))

    def test_a_promoted_alternative_is_judged_on_its_offer_price(self) -> None:
        self.storage.replace_catalog(
            [
                _p("1", "קוטג א", 6.40, 2.56, "100 גרם"),
                _p("2", "קוטג ב", 6.40, 2.56, "100 גרם"),
            ],
            [_promo("2", 3.20)],
        )
        _, cheaper = find_cheaper_equivalents(self.storage, "קוטג")
        self.assertEqual(len(cheaper), 1)
        self.assertAlmostEqual(cheaper[0][2], 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
