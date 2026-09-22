"""'מתי עשינו קניות פעם אחרונה?' has a real answer (2026-09-22).

It used to fall through to unclear and the loop pass invented a command.
"""
import tempfile
import unittest

from grocery_bot import hybrid, nlu, planner
from grocery_bot.storage import Storage
from grocery_bot.telegram_bot import last_orders_text


class LastOrdersTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.storage = Storage(self._tmp.name)

    def test_intent_tool_and_mapping_exist(self):
        self.assertIn("last_orders", nlu.INTENTS)
        self.assertIn("last_orders", planner.TOOLS)
        self.assertEqual(hybrid.TOOL_TO_INTENT["last_orders"], "last_orders")
        self.assertNotIn("last_orders", hybrid.CART_TOOLS)

    def test_no_history_says_so_per_chain(self):
        text = last_orders_text(self.storage)
        self.assertIn("שופרסל", text)
        self.assertIn("טיב טעם", text)
        self.assertIn("אין הזמנה בהיסטוריה", text)

    def test_reported_shop_newer_than_history_is_labelled(self):
        from grocery_bot import standingcart
        standingcart.mark_shopped(self.storage, store="tivtaam")
        text = last_orders_text(self.storage)
        self.assertIn("דיווחת על קנייה", text)
        self.assertIn("היום", text)


if __name__ == "__main__":
    unittest.main()
