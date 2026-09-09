"""Store text that is safe *inside* a Markdown entity, not just beside it.

The cross-chain deals button ran its handler and then sent nothing: the
message contained `משחת שיניים דואלקר 2*75`, escaped to `2\\*75` and placed
inside `*bold*`. Telegram's legacy Markdown has no backslash escape, so
that asterisk closed the bold early, the next opened an entity that never
closed, and the whole message was rejected —

    BadRequest: can't find end of the entity starting at byte offset 1247

Which looks, from the outside, exactly like a button that does nothing.
"""
import unittest

from grocery_bot import hotdeals
from grocery_bot.mdtext import escape, safe_name


class SafeNameTest(unittest.TestCase):
    def test_asterisk_becomes_a_multiplication_sign(self):
        # In Israeli product names it *is* a multiplication sign: "2*75"
        # means two of 75ml. The replacement reads better than the escape.
        self.assertEqual(safe_name("דואלקר 2*75"), "דואלקר 2×75")

    def test_no_backslashes_survive(self):
        # A backslash is meaningless in legacy Markdown and renders raw.
        self.assertNotIn("\\", safe_name("6*330 מ\"ל"))

    def test_other_entity_characters_are_neutralised(self):
        for char in ("_", "`", "[", "]"):
            self.assertNotIn(char, safe_name(f"מוצר{char}כלשהו"))

    def test_empty_is_safe(self):
        self.assertEqual(safe_name(""), "")
        self.assertEqual(safe_name(None), "")

    def test_escape_is_still_there_for_plain_text(self):
        # escape() remains correct beside an entity, where a stray
        # backslash is ugly but not fatal. safe_name is for inside one.
        self.assertIn("\\*", escape("2*75"))


class BoldedNamesStayBalancedTest(unittest.TestCase):
    """Every entity a deals message opens must close."""

    def _deal(self, name):
        return hotdeals.HotDeal(
            barcode="1", name=name, chain="shufersal",
            price=10.0, reference_price=20.0,
        )

    def _balanced(self, text):
        return all(
            line.count("*") % 2 == 0 and line.count("_") % 2 == 0
            for line in text.splitlines()
        )

    def test_a_multipack_name_does_not_break_the_message(self):
        text = hotdeals.format_deals([self._deal("משחת שיניים דואלקר 2*75")], [])
        self.assertTrue(self._balanced(text), text)

    def test_several_asterisks_in_one_name(self):
        text = hotdeals.format_deals([self._deal("בירה 6*330 מ\"ל 2*4")], [])
        self.assertTrue(self._balanced(text), text)

    def test_underscores_in_a_name(self):
        text = hotdeals.format_deals([self._deal("מוצר_עם_קו_תחתון")], [])
        self.assertTrue(self._balanced(text), text)

    def test_both_sections_stay_balanced(self):
        text = hotdeals.format_deals(
            [self._deal("א 2*75")], [self._deal("ב 6*330")]
        )
        self.assertTrue(self._balanced(text), text)

    def test_the_extended_list_too(self):
        text = hotdeals.format_extended([self._deal("ג 3*100")])
        self.assertTrue(self._balanced(text), text)


class OrderSummaryTest(unittest.TestCase):
    """The order summary now travels as HTML, and the contract inverts.

    Under legacy Markdown the goal was to *escape* the asterisk in
    "עגבניות חתוכות דק 400*3ג" — a product name 349 of this branch's
    items share the shape of — because it could not be made safe inside
    an entity. Under HTML the asterisk has no meaning, so the goal is
    the opposite: it must reach the household **unchanged**. What needs
    escaping instead is &, < and >.

    Live cost of getting this wrong under Markdown, 2026-09-07: both
    carts filled, the summary rejected, and the 39 disambiguation
    questions that ran after it never sent.
    """

    def _summary(self, *names, deal=None):
        from grocery_bot.models import CartAddResult, OrderCycleReport
        from grocery_bot.orchestrator import format_report_summary

        report = OrderCycleReport(store="shufersal")
        for name in names:
            result = CartAddResult(
                item_name=name, store="shufersal", status="added"
            )
            if deal:
                result.deal = deal
            report.record(result)
        return format_report_summary({"shufersal": report})

    def test_an_asterisk_in_a_product_name_survives_untouched(self):
        text = self._summary("עגבניות חתוכות דק 400*3ג")
        self.assertIn("400*3ג", text)
        self.assertNotIn("400\\*3ג", text, "no backslash escaping in HTML")
        self.assertNotIn("400×3ג", text, "and no silent rewrite of the name")

    def test_html_special_characters_are_escaped(self):
        text = self._summary("M&S ביסקוויט")
        self.assertIn("M&amp;S", text)

    def test_tags_are_balanced(self):
        text = self._summary("טונה 3*80", deal="-64% · 5.00₪")
        for tag in ("b", "i"):
            self.assertEqual(
                text.count(f"<{tag}>"), text.count(f"</{tag}>"), f"unbalanced <{tag}>"
            )

    def test_a_deal_label_travels_as_italic_not_underscores(self):
        text = self._summary("טונה 3*80", deal="-64% · 5.00₪")
        self.assertIn("<i>-64% · 5.00₪</i>", text)


if __name__ == "__main__":
    unittest.main()
