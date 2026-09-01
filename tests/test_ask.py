import tempfile
import unittest
from pathlib import Path

from grocery_bot import ask
from grocery_bot.storage import Storage

FUTURE = "2099-01-01T00:00:00"


class ParseQuestionTest(unittest.TestCase):
    def test_the_reported_phrasing(self):
        self.assertEqual(
            ask.parse_question("האם חרדל ב-20 שקל זה טוב?"), ("חרדל", 20.0)
        )

    def test_shekel_sign_and_decimals(self):
        self.assertEqual(ask.parse_question("שמן זית ב-39.90 ₪"), ("שמן זית", 39.90))

    def test_price_after_the_product(self):
        name, price = ask.parse_question('קטשופ 25 ש"ח')
        self.assertEqual(price, 25.0)
        self.assertEqual(name, "קטשופ")

    def test_question_without_a_price_returns_none(self):
        self.assertEqual(ask.parse_question("כמה עולה חלב"), ("חלב", None))

    def test_trailing_punctuation_is_not_searched_for(self):
        # "טוב?" survived a word-boundary match and was searched as part of
        # the product name.
        name, _ = ask.parse_question("האם קוטג' ב-6 שקל זה טוב?")
        self.assertNotIn("?", name)
        self.assertNotIn("טוב", name)

    def test_empty_input_is_safe(self):
        self.assertEqual(ask.parse_question(""), ("", None))


class VerdictTest(unittest.TestCase):
    def _v(self, quoted, **kw):
        return ask.PriceVerdict(query="x", quoted_price=quoted, name="x", **kw)

    def test_matching_the_cheapest_is_good(self):
        self.assertEqual(self._v(11.90, victory_price=11.90).verdict, "good")

    def test_slightly_over_is_fair(self):
        self.assertEqual(self._v(13.00, victory_price=11.90).verdict, "fair")

    def test_well_over_is_poor(self):
        v = self._v(20.00, shufersal_price=12.90, victory_price=11.90)
        self.assertEqual(v.verdict, "poor")
        self.assertEqual(v.overpay, 8.10)
        self.assertEqual(v.best_source, "ויקטורי")

    def test_a_quote_far_under_everything_is_a_mismatch_not_a_bargain(self):
        # Asked about milk at ₪7.35 the search returned a 2-litre jug at
        # ₪19.60; calling that a great price is confidently wrong.
        v = self._v(7.35, shufersal_price=19.60, tivtaam_price=18.40)
        self.assertEqual(v.verdict, "mismatch")
        self.assertIn("לא אותו מוצר", ask.format_verdict(v))

    def test_promotion_counts_as_the_best_price(self):
        v = self._v(25.0, shufersal_price=18.90, promo_price=14.90, promo_text="מבצע")
        self.assertEqual(v.best_available, 14.90)
        self.assertEqual(v.best_source, "שופרסל במבצע")

    def test_no_price_quoted_gives_no_verdict(self):
        self.assertEqual(self._v(None, shufersal_price=10.0).verdict, "unknown")


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.execute(
                "INSERT INTO catalog_products (item_code, name, price) VALUES (?,?,?)",
                ("7290000000011", "חרדל לחיץ 260 גרם", 12.90),
            )
            conn.commit()

    def test_uses_other_chains_when_available(self):
        class FakeChain:
            def __init__(self, price):
                self.price = price

            def prices_by_barcode(self, codes):
                return {codes[0]: {"price": self.price, "name": "x", "store": "y"}}

        prices = {"tivtaam": FakeChain(12.20), "victory": FakeChain(11.90)}
        v = ask.evaluate(self.storage, "חרדל ב-20 שקל", lambda k: prices[k])
        self.assertEqual(v.tivtaam_price, 12.20)
        self.assertEqual(v.victory_price, 11.90)
        self.assertEqual(v.verdict, "poor")

    def test_an_unreachable_chain_does_not_sink_the_answer(self):
        def boom(_key):
            raise RuntimeError("exit node down")

        v = ask.evaluate(self.storage, "חרדל ב-20 שקל", boom)
        self.assertEqual(v.shufersal_price, 12.90)
        self.assertEqual(v.verdict, "poor")
        self.assertTrue(v.notes)

    def test_unknown_product_says_so(self):
        v = ask.evaluate(self.storage, "קוואקר ב-20 שקל")
        self.assertEqual(v.name, "")
        self.assertIn("לא מצאתי", ask.format_verdict(v))

    def test_thin_history_does_not_claim_a_trend(self):
        v = ask.evaluate(self.storage, "חרדל ב-20 שקל")
        v.history_low, v.history_high = 12.0, 13.0
        text = ask.format_verdict(v, history_days=2)
        self.assertIn("קצרה מכדי", text)
        self.assertNotIn("טווח היסטורי", text)

    def test_enough_history_reports_the_range(self):
        v = ask.evaluate(self.storage, "חרדל ב-20 שקל")
        v.history_low, v.history_high = 12.0, 13.0
        self.assertIn("טווח היסטורי", ask.format_verdict(v, history_days=30))


if __name__ == "__main__":
    unittest.main()
