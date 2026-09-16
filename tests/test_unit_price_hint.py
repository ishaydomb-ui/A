"""The 💰 hint ranks value, not sticker price.

Reviewer finding, 2026-09-11: `_cheapest_index` ranked absolute price, so
a small pack won over the better buy — the household's own mental
arithmetic, done wrongly on their behalf.
"""
import unittest

from grocery_bot.telegram_bot import _cheapest_index


def card(price, unit=None, label=""):
    c = {"name": f"p{price}", "price": str(price)}
    if unit is not None:
        c["unitPrice"] = str(unit)
        c["unitLabel"] = label
    return c


class UnitPriceHintTests(unittest.TestCase):
    def test_the_better_buy_wins_over_the_smaller_pack(self):
        cards = [card(12.90, 25.80, "ק\"ג"), card(19.90, 19.90, "ק\"ג")]
        self.assertEqual(_cheapest_index(cards), 1)

    def test_mixed_units_fall_back_to_absolute_price(self):
        # ₪/kg against ₪/litre share a number and nothing else.
        cards = [card(19.90, 19.90, "ק\"ג"), card(12.90, 25.80, "ליטר")]
        self.assertEqual(_cheapest_index(cards), 1)

    def test_a_blank_label_is_not_a_unit(self):
        cards = [card(19.90, 5.0, ""), card(12.90, 9.0, "")]
        self.assertEqual(_cheapest_index(cards), 1)

    def test_no_unit_prices_at_all_uses_price(self):
        self.assertEqual(_cheapest_index([card(19.90), card(12.90)]), 1)

    def test_a_candidate_without_a_unit_price_does_not_break_the_others(self):
        cards = [card(30.0, 10.0, "ק\"ג"), card(5.0), card(20.0, 8.0, "ק\"ג")]
        self.assertEqual(_cheapest_index(cards), 2)

    def test_unusable_unit_prices_fall_back_rather_than_giving_up(self):
        cards = [card(19.90, 0, "ק\"ג"), card(12.90, 0, "ק\"ג")]
        self.assertEqual(_cheapest_index(cards), 1)

    def test_no_prices_at_all_is_no_hint(self):
        self.assertIsNone(_cheapest_index([{"name": "a"}, {"name": "b"}]))

    def test_empty_is_no_hint(self):
        self.assertIsNone(_cheapest_index([]))


if __name__ == "__main__":
    unittest.main()
