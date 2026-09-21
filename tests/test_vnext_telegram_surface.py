"""vNext Phase 2a: the read-only Telegram surface -- /plan, /readiness,
the reconciliation lines under /requests, and a command menu that
cannot drift from the handler registrations."""
import asyncio
import re
import unittest
from unittest import mock

from grocery_bot import telegram_bot, vnext_catalogue
from grocery_bot.config import Config
from grocery_bot.telegram_bot import COMMAND_MENU, GroceryBot
from tests.test_vnext_plan_shadow import Seeded, _dump


def _config() -> Config:
    return Config(
        telegram_bot_token="t", allowed_telegram_user_ids=[], db_path=":memory:",
        shufersal_storage_state_path="x.json", tivtaam_storage_state_path="y.json",
        enabled_stores=["tivtaam"],
    )


def _update():
    update = mock.MagicMock()
    sent = mock.MagicMock()
    sent.edit_text = mock.AsyncMock()
    update.message.reply_text = mock.AsyncMock(return_value=sent)
    update.effective_chat.id = 555
    return update, sent


class CommandMenuMatchesRegistrations(unittest.TestCase):
    def test_every_registered_command_is_in_the_menu_and_vice_versa(self):
        source = open(telegram_bot.__file__, encoding="utf-8").read()
        registered = set(re.findall(r'CommandHandler\("([a-z_]+)"', source))
        listed = {name for name, _ in COMMAND_MENU}
        self.assertEqual(registered, listed)
        self.assertNotIn("propose", listed)
        self.assertTrue(all(text.strip() for _, text in COMMAND_MENU))

    def test_a_telegram_failure_while_registering_is_logged_not_fatal(self):
        application = mock.MagicMock()
        application.bot.set_my_commands = mock.AsyncMock(side_effect=RuntimeError("telegram down"))
        asyncio.run(telegram_bot._register_bot_metadata(application))  # must not raise


class ReadOnlySurface(Seeded):
    def setUp(self):
        super().setUp()
        self.addCleanup(vnext_catalogue.clear_cache)
        self.bot = GroceryBot(_config(), self.storage)
        self.patches = [
            mock.patch("grocery_bot.telegram_bot._authorized", return_value=True),
            mock.patch("grocery_bot.telegram_bot.execution.run_list_items",
                       side_effect=AssertionError("no cart run from /plan")),
            mock.patch("grocery_bot.orchestrator.add_terms_to_cart",
                       side_effect=AssertionError("no cart write from /plan")),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_plan_and_readiness_reply_and_change_nothing(self):
        before = _dump(self.db)
        update, sent = _update()
        asyncio.run(self.bot.vnext_plan(update, mock.MagicMock()))
        text = sent.edit_text.call_args[0][0]
        self.assertIn("תוכנית בלבד", text)
        self.assertIn("הקנייה מוכנה", text)
        self.assertNotIn("parse_mode", sent.edit_text.call_args.kwargs)
        update, sent = _update()
        asyncio.run(self.bot.vnext_readiness(update, mock.MagicMock()))
        self.assertIn("קנייה", sent.edit_text.call_args[0][0])
        self.assertEqual(_dump(self.db), before)

    def test_plan_failure_is_reported_not_raised(self):
        update, sent = _update()
        with mock.patch("grocery_bot.shopping_plan.build_plan", side_effect=RuntimeError("boom")):
            asyncio.run(self.bot.vnext_plan(update, mock.MagicMock()))
        self.assertIn("לא הצלחתי", sent.edit_text.call_args[0][0])

    def test_requests_shows_reconciliation_without_touching_rows(self):
        before = _dump(self.db)
        update, _ = _update()
        context = mock.MagicMock()
        with mock.patch("grocery_bot.telegram_bot._send_html", new=mock.AsyncMock()) as send:
            asyncio.run(self.bot.requests_status(update, context))
        text = send.call_args[0][2]
        self.assertIn("לפי ההזמנות שהגיעו אחרי הבקשה", text)
        self.assertIn("גזר — פתוח", text)
        self.assertEqual(_dump(self.db), before)

    def test_start_help_mentions_the_new_commands(self):
        update = mock.MagicMock()
        update.message.reply_text = mock.AsyncMock()
        context = mock.MagicMock(args=[])
        asyncio.run(self.bot.start(update, context))
        text = update.message.reply_text.call_args[0][0]
        self.assertIn("/plan", text)
        self.assertIn("/readiness", text)


if __name__ == "__main__":
    unittest.main()
