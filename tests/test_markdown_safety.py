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
    """The same trap, in the one message that reports a real shop.

    Live failure on 2026-09-07: a cycle filled the carts at both chains
    and the household was told nothing at all, because the summary
    interpolated raw product names, one of them "עגבניות חתוכות דק
    400*3ג", and the caller sent it with parse_mode="Markdown" and no
    fallback. 349 of this branch's 5,807 products carry that asterisk.
    """

    def _summary(self, *names):
        from grocery_bot.models import CartAddResult, OrderCycleReport
        from grocery_bot.orchestrator import format_report_summary

        report = OrderCycleReport(store="shufersal")
        for name in names:
            report.record(
                CartAddResult(item_name=name, store="shufersal", status="added")
            )
        return format_report_summary({"shufersal": report})

    def test_an_asterisk_in_a_product_name_is_escaped(self):
        text = self._summary("עגבניות חתוכות דק 400*3ג")
        self.assertIn("400\\*3ג", text)

    def test_every_asterisk_left_is_a_deliberate_entity_marker(self):
        text = self._summary("טונה בהירה בשמן 3*80 גרם", "דואלקר 2*75")
        # Strip the escaped ones, then the bold pair around the chain
        # name; nothing unbalanced may remain.
        bare = text.replace("\\*", "")
        self.assertEqual(bare.count("*") % 2, 0, text)

    def test_a_deal_label_is_escaped_too(self):
        from grocery_bot.models import CartAddResult, OrderCycleReport
        from grocery_bot.orchestrator import format_report_summary

        report = OrderCycleReport(store="shufersal")
        result = CartAddResult(
            item_name="טונה 3*80", store="shufersal", status="added"
        )
        result.deal = "-64% · 5.00₪ במקום 13.90₪"
        report.record(result)
        text = format_report_summary({"shufersal": report})
        bare = text.replace("\\*", "")
        self.assertEqual(bare.count("*") % 2, 0, text)


if __name__ == "__main__":
    unittest.main()
