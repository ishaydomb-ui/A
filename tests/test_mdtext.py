import unittest

from grocery_bot.cartview import render_final
from grocery_bot.mdtext import escape
from grocery_bot.models import CartAddResult


class EscapeTests(unittest.TestCase):
    def test_the_multipack_asterisk_is_escaped(self) -> None:
        """The exact shape that broke a real digest.

        Telegram read the '*' as bold-start, found no pair, and rejected
        the entire message — the user got nothing at all.
        """
        self.assertEqual(escape('בירה 6*330 מ"ל'), 'בירה 6\\*330 מ"ל')

    def test_underscores_and_brackets_too(self) -> None:
        self.assertEqual(escape("a_b [c]"), "a\\_b \\[c\\]")

    def test_ordinary_names_are_untouched(self) -> None:
        self.assertEqual(escape("קוטג' 5% שומן"), "קוטג' 5% שומן")

    def test_empty_is_safe(self) -> None:
        self.assertEqual(escape(""), "")
        self.assertEqual(escape(None), "")


class RenderingTests(unittest.TestCase):
    def test_a_cart_row_escapes_a_hazardous_product_name(self) -> None:
        result = CartAddResult(
            item_name='ניילון נצמד 30ס"מ*30 מטר', store="s", status="added", price=10.0
        )
        self.assertIn("\\*", render_final([result], None))

    def test_markdown_balance_is_preserved(self) -> None:
        """Unescaped asterisks must not outnumber the intentional ones."""
        result = CartAddResult(
            item_name="מארז 5*20 גרם", store="s", status="added", price=10.0
        )
        text = render_final([result], None)
        unescaped = text.replace("\\*", "")
        self.assertEqual(unescaped.count("*") % 2, 0)


if __name__ == "__main__":
    unittest.main()
