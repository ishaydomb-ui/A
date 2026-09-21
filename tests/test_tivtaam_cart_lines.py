"""The Tiv Taam cart panel is read by element, never by text position.

Found 2026-09-21: positional parsing put "יח'" in `name` and "הוסר" in
`price`, so CartGuard never saw an item as present, re-clicked it, and
reported "the click did not change the cart" for 11 items that were all
already in the cart.
"""
import unittest

from grocery_bot.adapters.tivtaam import _cart_lines_from_dom


class CartLinesFromDomTests(unittest.TestCase):
    def test_fields_come_from_their_own_elements(self):
        raw = [{"name": "חלב עמיד 3% 1 ליטר", "qty": "2", "price": "₪6.90",
                "line_id": "sidenav_line_1", "removed": False, "out_of_stock": False}]
        lines = _cart_lines_from_dom(raw)
        self.assertEqual(lines[0]["name"], "חלב עמיד 3% 1 ליטר")
        self.assertEqual(lines[0]["qty"], "2")
        self.assertEqual(lines[0]["price"], "₪6.90")

    def test_removed_lines_are_not_in_the_cart(self):
        raw = [{"name": "שזיף סנטה רוזה", "qty": "0", "price": "הוסר", "removed": True},
               {"name": "סלק אדום מקולף", "qty": "1", "price": "₪7.45", "removed": False}]
        self.assertEqual([l["name"] for l in _cart_lines_from_dom(raw)], ["סלק אדום מקולף"])

    def test_out_of_stock_lines_are_kept_and_flagged(self):
        raw = [{"name": "גזר ארוז", "qty": "1", "price": "", "removed": False, "out_of_stock": True}]
        lines = _cart_lines_from_dom(raw)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0]["out_of_stock"])

    def test_nameless_or_malformed_entries_are_dropped(self):
        self.assertEqual(_cart_lines_from_dom([{"name": "", "qty": "1"}, "junk", None]), [])
