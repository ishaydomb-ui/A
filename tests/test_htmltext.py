"""HTML as the message container, replacing legacy Markdown.

The tests that matter are the ones legacy Markdown could not pass: store
text staying intact *inside* a bold span, and product names surviving
unedited.
"""
import unittest

from grocery_bot import htmltext


class EscapeTests(unittest.TestCase):
    def test_only_the_three_html_characters_are_touched(self):
        self.assertEqual(htmltext.escape("a & b"), "a &amp; b")
        self.assertEqual(htmltext.escape("a < b > c"), "a &lt; b &gt; c")

    def test_the_characters_markdown_feared_pass_through_untouched(self):
        """This is the data fix. mdtext had to rewrite these to survive."""
        for name in ("בירה קרומבאכר 6*330 מ\"ל", "קוטג' 5%", "מוצר_עם_קו", "פסטה [500]"):
            self.assertEqual(htmltext.escape(name), name)

    def test_a_product_name_is_safe_inside_bold(self):
        """The exact failure that killed a real order summary: an
        asterisk inside an entity, which legacy Markdown could not
        escape at all."""
        out = htmltext.bold("עגבניות חתוכות דק 400*3ג")
        self.assertEqual(out, "<b>עגבניות חתוכות דק 400*3ג</b>")
        self.assertEqual(out.count("<b>"), 1)
        self.assertEqual(out.count("</b>"), 1)

    def test_an_ampersand_in_a_name_cannot_break_the_message(self):
        self.assertEqual(htmltext.bold("M&S"), "<b>M&amp;S</b>")

    def test_empty_and_none_are_safe(self):
        self.assertEqual(htmltext.escape(""), "")
        self.assertEqual(htmltext.escape(None), "")
        self.assertEqual(htmltext.bold(None), "<b></b>")


class MarkupTests(unittest.TestCase):
    def test_italic_and_code_escape_their_content(self):
        self.assertEqual(htmltext.italic("a<b"), "<i>a&lt;b</i>")
        self.assertEqual(htmltext.code("x & y"), "<code>x &amp; y</code>")

    def test_a_link_escapes_both_halves(self):
        out = htmltext.link("הסל שלי", "https://x.co/?a=1&b=2")
        self.assertIn('href="https://x.co/?a=1&amp;b=2"', out)
        self.assertIn(">הסל שלי<", out)

    def test_an_expandable_blockquote_is_available(self):
        """Bot API 8.3, already installed — for the digest and the deals
        list, the two messages a phone screen cannot hold."""
        self.assertTrue(
            htmltext.blockquote("שורה", expandable=True).startswith("<blockquote expandable>")
        )
        self.assertTrue(htmltext.blockquote("שורה").startswith("<blockquote>"))


if __name__ == "__main__":
    unittest.main()
