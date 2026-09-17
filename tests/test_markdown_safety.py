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


class HotdealsHtmlTest(unittest.TestCase):
    """hotdeals.py moved to HTML 2026-09-17 (Markdown->HTML migration, part 2).

    Replaces the old balanced-asterisk test: under legacy Markdown the
    goal was to keep `*`/`_` counts even so an entity never leaked past
    the end of a line; under HTML those characters carry no meaning at
    all, so the goal inverts exactly as it did for the order summary
    (see OrderSummaryTest) — a name survives unchanged, & < > are
    escaped, and every <b>/<i> it opens closes.
    """

    def _deal(self, name):
        return hotdeals.HotDeal(
            barcode="1", name=name, chain="shufersal",
            price=10.0, reference_price=20.0,
        )

    def _tags_balanced(self, text):
        return all(text.count(f"<{t}>") == text.count(f"</{t}>") for t in ("b", "i"))

    def test_a_multipack_name_survives_unchanged(self):
        text = hotdeals.format_deals([self._deal("משחת שיניים דואלקר 2*75")], [])
        self.assertIn("2*75", text)
        self.assertNotIn("2×75", text, "no silent rewrite of the name")
        self.assertTrue(self._tags_balanced(text), text)

    def test_several_asterisks_in_one_name(self):
        text = hotdeals.format_deals([self._deal("בירה 6*330 מ\"ל 2*4")], [])
        self.assertIn("6*330", text)
        self.assertIn("2*4", text)
        self.assertTrue(self._tags_balanced(text), text)

    def test_underscores_in_a_name_survive_too(self):
        text = hotdeals.format_deals([self._deal("מוצר_עם_קו_תחתון")], [])
        self.assertIn("מוצר_עם_קו_תחתון", text)
        self.assertTrue(self._tags_balanced(text), text)

    def test_html_metacharacters_are_escaped(self):
        text = hotdeals.format_deals([self._deal("M&S <ביסקוויט>")], [])
        self.assertIn("M&amp;S", text)
        self.assertIn("&lt;ביסקוויט&gt;", text)
        self.assertNotIn("M&S <", text)

    def test_both_sections_stay_balanced(self):
        text = hotdeals.format_deals(
            [self._deal("א 2*75")], [self._deal("ב 6*330")]
        )
        self.assertTrue(self._tags_balanced(text), text)

    def test_the_extended_list_too(self):
        text = hotdeals.format_extended([self._deal("ג 3*100 & נוסף")])
        self.assertIn("3*100", text)
        self.assertIn("&amp;", text)
        self.assertTrue(self._tags_balanced(text), text)


class StockupDealsHtmlTest(unittest.TestCase):
    """radar.format_stockup_deals — moved to HTML the same day as hotdeals.

    catalog_name/description were interpolated with no escaping at all
    even under legacy Markdown (a latent gap the old tests never caught,
    since neither field happened to carry a `*` in the fixtures used).
    Fixed as part of this migration, not left as a second silent-failure
    class under the new parser.
    """

    def _deal(self, name="דבש טבעי", desc="מבצע"):
        from grocery_bot.radar import StockUpDeal

        return StockUpDeal(
            bought_name=name, catalog_name=name, shelf_price=16.9, deal_price=10.0,
            description=desc, pantryable=True,
        )

    def test_html_metacharacters_in_the_name_are_escaped(self):
        from grocery_bot.radar import format_stockup_deals

        text = format_stockup_deals([self._deal(name="M&S <דבש>")])
        self.assertIn("M&amp;S", text)
        self.assertIn("&lt;דבש&gt;", text)

    def test_html_metacharacters_in_the_description_are_escaped(self):
        from grocery_bot.radar import format_stockup_deals

        text = format_stockup_deals([self._deal(desc="מבצע 1<2")])
        self.assertIn("1&lt;2", text)

    def test_tags_are_balanced(self):
        from grocery_bot.radar import format_stockup_deals

        text = format_stockup_deals([self._deal()])
        for tag in ("b", "i"):
            self.assertEqual(text.count(f"<{tag}>"), text.count(f"</{tag}>"))


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

    def test_every_html_metacharacter_is_escaped(self):
        """Nigel's precondition, and the reason it is not optional: the
        overlap between what the old escape handled (_ * ` [ ]) and what
        HTML needs (& < >) is **zero**. Swapping parse_mode without
        swapping the escape implementation in the same commit would
        trade one silent-failure class for another, and "M&S ביסקוויט"
        would have broken a message exactly as "6*330" did."""
        text = self._summary("M&S ביסקוויט", "מוצר <מיוחד>")
        self.assertIn("M&amp;S", text)
        self.assertIn("&lt;מיוחד&gt;", text)
        self.assertNotIn("M&S", text)

    def test_the_plain_text_fallback_restores_the_original(self):
        """"The message arrived less pretty" rather than "the message was
        lost" — the failure mode that would have saved the silent
        cross-chain deals button."""
        import re as _re

        text = self._summary("M&S ביסקוויט", "עגבניות 400*3ג")
        plain = _re.sub(r"<[^>]+>", "", text)
        plain = plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        self.assertIn("M&S ביסקוויט", plain)
        self.assertIn("400*3ג", plain)
        self.assertNotIn("<b>", plain)

    def test_a_deal_label_travels_as_italic_not_underscores(self):
        text = self._summary("טונה 3*80", deal="-64% · 5.00₪")
        self.assertIn("<i>-64% · 5.00₪</i>", text)


if __name__ == "__main__":
    unittest.main()
