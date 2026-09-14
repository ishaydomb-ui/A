"""A number the bot says must be a number the bot computed.

Every figure in this project's output has a source. One kind of text does
not: the `reply` the classifier writes, free Hebrew from a model, sent to
the household verbatim. A fluent invented price sitting in the same chat
as real ones is what makes it expensive — once one number cannot be
trusted, none of them can be without checking, and checking is the work
this bot exists to remove.
"""
import unittest

from grocery_bot import nlu, readback


class RedactsWhatItCannotBack(unittest.TestCase):
    def test_an_invented_price_is_redacted(self) -> None:
        self.assertEqual(
            readback.verify("הוספתי חלב ב-7.90 ₪ לרשימה"),
            "הוספתי חלב ב-… לרשימה",
        )

    def test_an_invented_percentage_is_redacted(self) -> None:
        self.assertIn("…", readback.verify("זה 30% יותר זול"))

    def test_every_way_of_writing_shekels_is_caught(self) -> None:
        for text in ("₪12", "12 ₪", "12 ש\"ח", "12 שקלים", "12.50 שקל", "12 ש״ח"):
            with self.subTest(text):
                self.assertTrue(readback.has_unbacked_figure(f"המחיר {text}"), text)

    def test_the_sentence_survives_the_redaction(self) -> None:
        # Useful minus a claim beats not useful.
        out = readback.verify("הוספתי חלב ב-7.90 ₪ לרשימה")
        self.assertIn("הוספתי חלב", out)
        self.assertIn("לרשימה", out)


class KeepsWhatIsNotAPriceClaim(unittest.TestCase):
    def test_a_quantity_is_not_a_price(self) -> None:
        text = "הוספתי 2 יחידות"
        self.assertEqual(readback.verify(text), text)

    def test_a_percentage_in_a_product_name_survives(self) -> None:
        # "קוטג 5% שומן" is the product, not a discount. Mangling the
        # words the household actually said is worse than the risk.
        for text in ("קוטג 5% שומן 250 גרם", "בירה 5% אלכוהול", "חלב 3%"):
            with self.subTest(text):
                self.assertEqual(readback.verify(text), text)

    def test_a_percentage_in_a_saving_claim_is_redacted(self) -> None:
        for text in ("זה 30% יותר זול", "הנחה של 30%", "חיסכון של 25%"):
            with self.subTest(text):
                self.assertIn("…", readback.verify(text))

    def test_a_date_is_not_a_price(self) -> None:
        text = "ההזמנה מ-7.9 עדיין בדרך"
        self.assertEqual(readback.verify(text), text)

    def test_empty_text_is_not_an_error(self) -> None:
        self.assertEqual(readback.verify(""), "")
        self.assertEqual(readback.verify(None), "")


class LetsThroughWhatWasComputed(unittest.TestCase):
    def test_a_real_figure_passes(self) -> None:
        text = "הוספתי חלב ב-7.90 ₪ לרשימה"
        self.assertEqual(readback.verify(text, computed=[7.9]), text)

    def test_matching_ignores_how_it_was_written(self) -> None:
        self.assertFalse(readback.has_unbacked_figure("₪7.90", computed=["7.9"]))
        self.assertFalse(readback.has_unbacked_figure("7,90 ₪", computed=[7.90]))

    def test_a_different_figure_is_still_redacted(self) -> None:
        self.assertTrue(
            readback.has_unbacked_figure("חסכתי לך 40 ₪", computed=[7.9])
        )


class TheClassifierReplyGoesThroughIt(unittest.TestCase):
    """The rule, not the request. The prompt already says don't invent
    numbers; this is what happens when it does anyway."""

    def setUp(self) -> None:
        self._original = nlu._ask_model
        self.addCleanup(lambda: setattr(nlu, "_ask_model", self._original))

    def test_a_made_up_price_never_reaches_the_household(self) -> None:
        nlu._ask_model = lambda message, context=None: (
            '{"intent":"add_item","items":[{"name":"חלב"}],'
            '"reply":"הוספתי חלב ב-7.90 ₪"}'
        )
        parsed = nlu.parse_message("תוסיף חלב")
        self.assertNotIn("7.90", parsed.reply)
        self.assertIn("הוספתי חלב", parsed.reply)


if __name__ == "__main__":
    unittest.main()
