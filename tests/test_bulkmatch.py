import unittest

from grocery_bot.adapters.shufersal import _plausible_match


class PlausibleMatchTests(unittest.TestCase):
    """The store's bulk endpoint is a recommender, not an exact matcher.

    Asked for a deliberately nonsense product it confidently returned
    milk, so an unguarded result would silently put the wrong thing in
    a real cart.
    """

    def test_an_unrelated_recommendation_is_rejected(self) -> None:
        self.assertFalse(_plausible_match("מוצר שלא קיים בכלל", "חלב בקרטון 3% שומן"))

    def test_a_longer_brand_variant_is_accepted(self) -> None:
        self.assertTrue(_plausible_match("קוטג' 5% שומן", "קוטג' 5% שומן תנובה"))

    def test_an_exact_name_is_accepted(self) -> None:
        self.assertTrue(_plausible_match("בננה", "בננה"))

    def test_a_shorter_generic_is_accepted(self) -> None:
        self.assertTrue(_plausible_match("שמן זית כתית מעולה", "שמן זית"))

    def test_punctuation_does_not_block_a_match(self) -> None:
        self.assertTrue(_plausible_match('חלב 3% בקרטון', "חלב בקרטון 3% שומן"))

    def test_an_empty_term_is_not_rejected(self) -> None:
        self.assertTrue(_plausible_match("", "משהו"))


if __name__ == "__main__":
    unittest.main()
