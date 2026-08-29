import unittest

from grocery_bot.disambiguate import describe_card, resolve


def _card(name, code, price="6.10", size="250 גרם", brand="תנובה"):
    return {"name": name, "code": code, "price": price, "size": size, "brand": brand}


class ResolveTests(unittest.TestCase):
    def test_single_result_needs_no_question(self) -> None:
        r = resolve("קוטג'", [_card("קוטג' 5%", "P_1")], set())
        self.assertTrue(r.resolved)

    def test_exact_name_wins(self) -> None:
        cards = [_card("קמח לבן בהיר", "P_1"), _card("קמח לבן", "P_2")]
        r = resolve("קמח לבן", cards, set())
        self.assertEqual(r.card["code"], "P_2")
        self.assertEqual(r.reason, "exact_name")

    def test_previously_bought_product_wins(self) -> None:
        """The household already decided this once; re-asking is the bug."""
        cards = [_card("תפוח עץ גאלה", "P_1"), _card("תפוח עץ סמיט", "P_2")]
        r = resolve("תפוחי עץ", cards, {"תפוח עץ סמיט"})
        self.assertEqual(r.card["code"], "P_2")
        self.assertEqual(r.reason, "history")

    def test_two_previously_bought_products_must_be_asked(self) -> None:
        cards = [_card("ביצים L", "P_1"), _card("ביצים XL", "P_2")]
        r = resolve("ביצים", cards, {"ביצים L", "ביצים XL"})
        self.assertFalse(r.resolved)

    def test_nothing_known_is_asked(self) -> None:
        cards = [_card("גבינה בולגרית 5%", "P_1"), _card("גבינה בולגרית 16%", "P_2")]
        self.assertFalse(resolve("גבינה בולגרית", cards, set()).resolved)

    def test_duplicate_names_are_settled_by_the_remembered_code(self) -> None:
        """Three tiles all named "קוטג' 5% שומן" differ only by brand.

        The name proves nothing here, so only the code can decide.
        """
        cards = [
            _card("קוטג' 5% שומן", "P_A", brand="תנובה"),
            _card("קוטג' 5% שומן", "P_B", brand="שטראוס"),
            _card("קוטג' 5% שומן", "P_C", brand="טרה"),
        ]
        r = resolve("קוטג' 5% שומן", cards, {"קוטג' 5% שומן"}, {"P_B"})
        self.assertEqual(r.card["code"], "P_B")
        self.assertEqual(r.reason, "history")

    def test_duplicate_names_without_a_known_code_are_asked(self) -> None:
        """Knowing only the name cannot choose between three brands."""
        cards = [
            _card("קוטג' 5% שומן", "P_A", brand="תנובה"),
            _card("קוטג' 5% שומן", "P_B", brand="שטראוס"),
            _card("קוטג' 5% שומן", "P_C", brand="טרה"),
        ]
        self.assertFalse(resolve("קוטג' 5% שומן", cards, {"קוטג' 5% שומן"}).resolved)

    def test_geresh_spelling_does_not_block_a_match(self) -> None:
        """קוטג׳ / קוטג' / קוטג are the same word to a person."""
        cards = [_card("קוטג 5%", "P_1"), _card("קוטג' 9%", "P_2")]
        r = resolve("קוטג׳ 5%", cards, set())
        self.assertEqual(r.card["code"], "P_1")

    def test_no_results_is_not_a_resolution(self) -> None:
        self.assertFalse(resolve("משהו", [], set()).resolved)

    def test_cheapest_is_never_guessed(self) -> None:
        """A silent substitution costs more trust than one question."""
        cards = [_card("קוטג' 5%", "P_1", price="6.10"), _card("קוטג' בקטנה", "P_2", price="3.20")]
        self.assertFalse(resolve("קוטג", cards, set()).resolved)


class DescribeTests(unittest.TestCase):
    def test_includes_brand_size_and_price(self) -> None:
        line = describe_card(_card("קוטג' 5% שומן", "P_1"))
        self.assertIn("תנובה", line)
        self.assertIn("250 גרם", line)
        self.assertIn("6.10", line)

    def test_survives_missing_fields(self) -> None:
        line = describe_card({"name": "חלב", "code": "P_1"})
        self.assertEqual(line, "חלב")

    def test_survives_unparsable_price(self) -> None:
        line = describe_card({"name": "חלב", "price": "לא ידוע", "size": "", "brand": ""})
        self.assertEqual(line, "חלב")


if __name__ == "__main__":
    unittest.main()
