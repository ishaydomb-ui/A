"""A long message must cost an extra message, never its content.

`_send_markdown` already existed so that a formatting problem could not
cost the content. Length had the same failure and no such guard: Telegram
refuses anything over 4096 characters, and the plain-text fallback resent
the same over-long text and failed again, so the household saw nothing.

Raised by Nigel (family-budget-automation) 2026-09-16, who hit the same
shape — a large section coming back as a bare "truncated" notice carrying
no content at all.
"""
import unittest

from grocery_bot.telegram_bot import TELEGRAM_LIMIT, _split_for_telegram


class SplittingTests(unittest.TestCase):
    def test_a_short_message_is_left_alone(self):
        self.assertEqual(_split_for_telegram("שלום"), ["שלום"])

    def test_a_long_message_is_split_rather_than_lost(self):
        text = "\n".join(f"• מוצר מספר {i} במחיר 12.90" for i in range(400))
        chunks = _split_for_telegram(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), TELEGRAM_LIMIT)

    def test_nothing_is_dropped(self):
        lines = [f"שורה {i}" for i in range(500)]
        chunks = _split_for_telegram("\n".join(lines))
        rejoined = "\n".join(chunks)
        for line in lines:
            self.assertIn(line, rejoined)

    def test_it_never_cuts_inside_a_line(self):
        # A deals line cut in half is a price with no product.
        lines = [f"• מוצר {i} — 12.90₪ במקום 19.90₪" for i in range(300)]
        for chunk in _split_for_telegram("\n".join(lines)):
            for piece in chunk.split("\n"):
                self.assertTrue(piece == "" or piece in lines, piece)

    def test_one_absurdly_long_line_is_still_delivered(self):
        # A composition bug, but losing part of one line beats losing all.
        text = "א" * (TELEGRAM_LIMIT * 2 + 50)
        chunks = _split_for_telegram(text)
        self.assertEqual(sum(len(c) for c in chunks), len(text))
        for chunk in chunks:
            self.assertLessEqual(len(chunk), TELEGRAM_LIMIT)

    def test_empty_text_is_not_an_error(self):
        self.assertEqual(_split_for_telegram(""), [""])

    def test_a_message_exactly_at_the_limit_is_one_chunk(self):
        self.assertEqual(len(_split_for_telegram("א" * TELEGRAM_LIMIT)), 1)


if __name__ == "__main__":
    unittest.main()
