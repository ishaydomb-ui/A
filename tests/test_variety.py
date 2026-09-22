"""Variety requests: a category is a request for suggestions, like a recipe.

2026-09-22: "הרבה ירקות ופירות שאנחנו לא אוכלים בדרך כלל" was filed as two
items and frozen soup mix, frozen mixed vegetables and a dried-fruit
tray reached the real carts. Now: suggestions to tick; nothing is added
until the household chooses; the cart engine is never reached.
"""
import asyncio
import tempfile
import unittest
from datetime import date
from unittest import mock

from grocery_bot import hybrid, nlu, planner, variety, vnext_catalogue
from grocery_bot.config import Config
from grocery_bot.storage import Storage
from grocery_bot.telegram_bot import GroceryBot
from grocery_bot.vnext_config import VNextConfig

TODAY = date(2026, 9, 22)


def _config() -> Config:
    return Config(
        telegram_bot_token="t", allowed_telegram_user_ids=[], db_path=":memory:",
        shufersal_storage_state_path="x.json", tivtaam_storage_state_path="y.json",
        enabled_stores=["tivtaam", "shufersal"],
    )


def _storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    vnext_catalogue.clear_cache()
    return Storage(tmp.name)


def _seed_catalogue(storage: Storage) -> None:
    rows = [
        ("1", "לפת", 6.9), ("2", "אספרגוס", 19.9), ("3", "כרוב אדום", 7.9), ("4", "גזר ארוז", 4.5),
        ("5", "עגבניות שרי", 9.9), ("6", "לקט ירקות מעורבים 800 ג'", 14.9), ("7", "פירות יבשים מגש", 39.9),
        ("8", "משקה בננה 1 ליטר", 8.9), ("9", "סלק מבושל בואקום", 10.9), ("10", "מלפפונים קטנים בחומץ", 12.9),
        ("11", "דניאלה בננה 88 גרם", 4.2), ("12", "פריגת תפוזים 1.5 ליטר", 7.9), ("13", "קיווי", 12.9),
        ("14", "תחליב רחצה יוגורט וניל", 15.9), ("15", "עוף שלם טרי", 29.9),
    ]
    storage.record_store_prices("tivtaam", [
        {"barcode": b, "name": n, "price": p, "observed_at": "2026-09-20T10:00:00", "source": "feed"}
        for b, n, p in rows
    ])


def _seed_history(storage: Storage) -> None:
    # Carrots and cherry tomatoes bought recently (packaged/plain variants);
    # asparagus bought long ago.
    storage.record_tivtaam_order_lines("o1", "2026-09-17", [
        {"code": "4", "barcode": "4", "name": "גזר ארוז", "quantity": 1, "actual_quantity": 1,
         "weightable": False, "unit": "", "price": 4.5, "line_total": 4.5, "substituted": False},
        {"code": "5", "barcode": "5", "name": "עגבניות שרי", "quantity": 1, "actual_quantity": 1,
         "weightable": False, "unit": "", "price": 9.9, "line_total": 9.9, "substituted": False},
    ])
    storage.record_tivtaam_order_lines("o0", "2026-03-01", [
        {"code": "2", "barcode": "2", "name": "אספרגוס", "quantity": 1, "actual_quantity": 1,
         "weightable": False, "unit": "", "price": 19.9, "line_total": 19.9, "substituted": False},
    ])


class IntentPlumbingTests(unittest.TestCase):
    def test_intent_tool_and_mapping_exist(self):
        self.assertIn("variety_request", nlu.INTENTS)
        self.assertIn("suggest_variety", planner.TOOLS)
        self.assertEqual(hybrid.TOOL_TO_INTENT["suggest_variety"], "variety_request")
        self.assertNotIn("suggest_variety", hybrid.CART_TOOLS)
        self.assertIn("variety_request", nlu._SYSTEM_PROMPT)

    def test_category_phrases(self):
        self.assertEqual(variety.category_for("ירקות ופירות"), "produce")
        self.assertEqual(variety.category_for("ירקות"), "produce")
        self.assertEqual(variety.category_for("בשר"), "meat_fish")
        self.assertIsNone(variety.category_for("חלב"))
        self.assertIsNone(variety.category_for(""))


class SuggestionEngineTests(unittest.TestCase):
    def setUp(self):
        self.storage = _storage()
        _seed_catalogue(self.storage)
        _seed_history(self.storage)
        self.cfg = VNextConfig()

    def names(self, category="produce", days=60, count=20):
        return [s.name for s in variety.suggest(self.storage, category, days, count, self.cfg, today=TODAY)]

    def test_recent_purchases_are_excluded_under_any_packaging(self):
        names = self.names()
        self.assertNotIn("גזר ארוז", names)
        self.assertNotIn("עגבניות שרי", names)

    def test_old_purchase_is_offered_again_and_ranked_after_never_bought(self):
        names = self.names()
        self.assertIn("אספרגוס", names)
        self.assertLess(names.index("לפת"), names.index("אספרגוס"))

    def test_produce_never_yields_frozen_dried_pickled_cooked_or_drinks(self):
        names = self.names()
        for bad in ("לקט ירקות מעורבים 800 ג'", "פירות יבשים מגש", "משקה בננה 1 ליטר",
                    "סלק מבושל בואקום", "מלפפונים קטנים בחומץ", "דניאלה בננה 88 גרם",
                    "פריגת תפוזים 1.5 ליטר", "תחליב רחצה יוגורט וניל", "עוף שלם טרי"):
            self.assertNotIn(bad, names, bad)
        self.assertEqual(set(names), {"לפת", "כרוב אדום", "קיווי", "אספרגוס"})

    def test_rejected_products_are_never_suggested(self):
        self.storage.reject_product("tivtaam", "לפת", "1", "לפת", source="human")
        self.assertNotIn("לפת", self.names())

    def test_offset_pages(self):
        first = self.names(count=2)
        second = [s.name for s in variety.suggest(self.storage, "produce", 60, 2, self.cfg, offset=2, today=TODAY)]
        self.assertEqual(len(first), 2)
        self.assertFalse(set(first) & set(second))

    def test_meat_request_is_not_restricted_to_fresh_but_stays_in_category(self):
        names = self.names("meat_fish")
        self.assertEqual(names, ["עוף שלם טרי"])

    def test_render_marks_chosen(self):
        sugs = variety.suggest(self.storage, "produce", 60, 3, self.cfg, today=TODAY)
        text = variety.render("produce", sugs, {sugs[0].key})
        self.assertIn("✅ " + sugs[0].name, text)
        self.assertIn("⬜ " + sugs[1].name, text)
        self.assertIn("כלום לא נוסף", text)


def _update(chat_id=555):
    update = mock.MagicMock()
    sent = mock.MagicMock()
    sent.message_id = 77
    sent.edit_text = mock.AsyncMock()
    update.message.reply_text = mock.AsyncMock(return_value=sent)
    update.effective_chat.id = chat_id
    update.effective_user.full_name = "Ishay"
    return update, sent


def _context(sent):
    context = mock.MagicMock()
    context.bot.send_message = mock.AsyncMock(return_value=sent)
    return context


def _query(data: str):
    query = mock.MagicMock()
    query.data = data
    query.answer = mock.AsyncMock()
    query.edit_message_text = mock.AsyncMock()
    query.message.message_id = 77
    query.message.edit_text = mock.AsyncMock()
    return query


class TelegramFlowTests(unittest.TestCase):
    def setUp(self):
        self.storage = _storage()
        _seed_catalogue(self.storage)
        _seed_history(self.storage)
        self.bot = GroceryBot(_config(), self.storage)
        self.raise_on_cart = mock.patch("grocery_bot.orchestrator.add_terms_to_cart",
                                        side_effect=AssertionError("the cart engine must never be reached"))
        self.raise_on_cart.start()
        self.addCleanup(self.raise_on_cart.stop)

    def _start(self):
        update, sent = _update()
        context = _context(sent)
        parsed = nlu.ParsedMessage(intent="variety_request", query="ירקות ופירות")
        asyncio.run(self.bot._do_variety_request(update, context, parsed, "Ishay"))
        return update, sent, context

    def _press(self, data: str):
        update, sent = _update()
        update.callback_query = _query(data)
        context = _context(sent)
        asyncio.run(self.bot.vnext.on_callback(update, context))
        return update.callback_query

    def test_request_opens_a_screen_with_toggles_and_adds_nothing(self):
        update, sent, context = self._start()
        text = sent.edit_text.call_args.args[0]
        markup = sent.edit_text.call_args.kwargs["reply_markup"]
        self.assertIn("הצעות לירקות ופירות", text)
        labels = [b.text for row in markup.inline_keyboard for b in row]
        self.assertTrue(any(l.startswith("⬜") for l in labels))
        self.assertIn("ביטול", labels)
        self.assertEqual(self.storage.list_pending_adhoc(), [])

    def test_toggle_then_add_selected_creates_exactly_the_chosen_requests_and_confirmations(self):
        self._start()
        state = self.bot.vnext._var_state(555)
        names = [d["name"] for d in state["batch"]]
        self._press("vn:var:t:0")
        self._press("vn:var:t:1")
        self._press("vn:var:t:1")   # un-tick the second
        q = self._press("vn:var:add")
        pending = [r.text for r in self.storage.list_pending_adhoc()]
        self.assertEqual(pending, [names[0]])
        rows = self.storage.list_vnext_product_confirmations("tivtaam")
        self.assertEqual([r["kind"] for r in rows], ["kept_exception_choice"])
        self.assertEqual(rows[0]["product_name"], names[0])
        self.assertIn("לא נגעתי בעגלה", q.edit_message_text.call_args.args[0])
        self.assertIsNone(self.bot.vnext._var_state(555))

    def test_add_with_nothing_ticked_adds_nothing(self):
        self._start()
        q = self._press("vn:var:add")
        q.answer.assert_called_with("לא סימנתם כלום")
        self.assertEqual(self.storage.list_pending_adhoc(), [])

    def test_cancel_clears_the_screen(self):
        self._start()
        self._press("vn:var:t:0")
        self._press("vn:var:no")
        self.assertIsNone(self.bot.vnext._var_state(555))
        self.assertEqual(self.storage.list_pending_adhoc(), [])

    def test_unknown_category_asks_instead_of_guessing(self):
        update, sent = _update()
        context = _context(sent)
        parsed = nlu.ParsedMessage(intent="variety_request", query="דברים טעימים")
        asyncio.run(self.bot._do_variety_request(update, context, parsed, "Ishay"))
        self.assertIn("איזו קטגוריה", update.message.reply_text.call_args.args[0])
        sent.edit_text.assert_not_called()

    def test_bare_aisle_word_in_add_offers_the_button_and_lists_nothing(self):
        update, sent = _update()
        context = _context(sent)
        parsed = nlu.ParsedMessage(intent="add_item", items=[nlu.ParsedItem(name="ירקות")])
        asyncio.run(self.bot._do_add(update, context, parsed, "Ishay"))
        self.assertEqual(self.storage.list_pending_adhoc(), [])
        markup = update.message.reply_text.call_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].text, "תציע לי")
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "varoffer:go")
        # ...and the button opens the same screen
        update2, sent2 = _update()
        update2.callback_query = _query("varoffer:go")
        with mock.patch("grocery_bot.telegram_bot._authorized", return_value=True):
            asyncio.run(self.bot.on_variety_offer(update2, _context(sent2)))
        self.assertIsNotNone(self.bot.vnext._var_state(555))


if __name__ == "__main__":
    unittest.main()
