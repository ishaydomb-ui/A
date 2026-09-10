"""A rejected parse_mode must cost formatting, never the message.

The cases that matter are the two this project actually lost: a deals
button whose send was refused so the tap did nothing, and an order
summary that vanished after both carts were filled.
"""
import asyncio
import unittest

from telegram.error import BadRequest

from grocery_bot import safesend


class FakeBot:
    """Refuses anything sent with a parse_mode, like Telegram does when
    an entity is malformed."""

    def __init__(self, fail_with_parse_mode=True):
        self.fail_with_parse_mode = fail_with_parse_mode
        self.calls = []

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self.calls.append({"text": text, **kwargs})
        if self.fail_with_parse_mode and kwargs.get("parse_mode"):
            raise BadRequest("Can't parse entities")
        return "sent"

    async def edit_message_text(self, text=None, **kwargs):
        self.calls.append({"text": text, **kwargs})
        if self.fail_with_parse_mode and kwargs.get("parse_mode"):
            raise BadRequest("Can't parse entities")
        return "edited"


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class TheMessageSurvives(unittest.TestCase):
    def test_a_refused_markdown_send_arrives_as_plain_text(self):
        bot = FakeBot()
        self.assertTrue(safesend.install(bot))
        result = run(bot.send_message(chat_id=1, text="*מבצע* 6*330 מ\"ל",
                                      parse_mode="Markdown"))
        self.assertEqual(result, "sent")
        self.assertEqual(len(bot.calls), 2, "one refused attempt, one retry")
        self.assertIsNone(bot.calls[1].get("parse_mode"))
        self.assertEqual(bot.calls[1]["text"], "מבצע 6330 מ\"ל")

    def test_a_refused_html_send_arrives_with_tags_stripped(self):
        bot = FakeBot()
        safesend.install(bot)
        run(bot.send_message(chat_id=1, text="<b>M&amp;S</b> ביסקוויט",
                             parse_mode="HTML"))
        self.assertEqual(bot.calls[1]["text"], "M&S ביסקוויט")

    def test_an_edit_is_protected_too(self):
        """The deals button edits a message rather than sending one."""
        bot = FakeBot()
        safesend.install(bot)
        result = run(bot.edit_message_text(text="*מבצעים*", parse_mode="Markdown",
                                           chat_id=1, message_id=2))
        self.assertEqual(result, "edited")
        self.assertEqual(bot.calls[1]["text"], "מבצעים")

    def test_a_healthy_send_is_untouched_and_keeps_its_formatting(self):
        bot = FakeBot(fail_with_parse_mode=False)
        safesend.install(bot)
        run(bot.send_message(chat_id=1, text="*מבצע*", parse_mode="Markdown"))
        self.assertEqual(len(bot.calls), 1, "no retry on the happy path")
        self.assertEqual(bot.calls[0]["parse_mode"], "Markdown")
        self.assertEqual(bot.calls[0]["text"], "*מבצע*")

    def test_a_failure_without_parse_mode_is_raised_not_swallowed(self):
        """Only a formatting failure is recoverable. A network error must
        stay an error rather than be reported as a delivered message."""

        class Broken(FakeBot):
            async def send_message(self, **kwargs):
                raise BadRequest("chat not found")

        bot = Broken()
        safesend.install(bot)
        with self.assertRaises(BadRequest):
            run(bot.send_message(chat_id=1, text="שלום"))


class Installation(unittest.TestCase):
    def test_installing_twice_does_not_double_wrap(self):
        bot = FakeBot()
        safesend.install(bot)
        first = bot.send_message
        self.assertFalse(safesend.install(bot))
        self.assertIs(bot.send_message, first)

    def test_a_bot_that_cannot_be_wrapped_reports_false(self):
        """Silently doing nothing is the failure this module prevents."""

        class Slotted:
            __slots__ = ()

            async def send_message(self, **kwargs):
                return None

        self.assertFalse(safesend.install(Slotted()))


class ToPlain(unittest.TestCase):
    def test_markdown_markers_go_and_the_text_stays(self):
        self.assertEqual(safesend.to_plain("*א* _ב_ `ג`", "Markdown"), "א ב ג")

    def test_html_entities_are_decoded_in_a_safe_order(self):
        """&amp;lt; must become &lt;, not <."""
        self.assertEqual(safesend.to_plain("&amp;lt;", "HTML"), "&lt;")

    def test_a_product_name_survives_the_fallback(self):
        for name in ("בירה קרומבאכר 6*330 מ\"ל", "M&amp;S", "קוטג' 5%"):
            self.assertTrue(safesend.to_plain(name, "HTML"))

    def test_empty_and_none_are_safe(self):
        self.assertEqual(safesend.to_plain(None, "HTML"), "")
        self.assertEqual(safesend.to_plain("", "Markdown"), "")



class SafeBotIsTheRealMechanism(unittest.TestCase):
    """Patching the instance does not work — telegram.Bot has __slots__.
    Discovered by trying it against the real class rather than only a
    fake, which is the whole reason `install` reports False."""

    TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    def test_install_refuses_the_real_bot_rather_than_pretending(self):
        from telegram import Bot

        self.assertFalse(safesend.install(Bot(self.TOKEN)))

    def test_safebot_retries_a_refused_send_as_plain_text(self):
        from unittest import mock

        from telegram.ext import ExtBot

        bot = safesend.SafeBot(self.TOKEN)
        seen = []

        async def fake(*args, **kwargs):
            seen.append(kwargs)
            if kwargs.get("parse_mode"):
                raise BadRequest("Can't parse entities")
            return "sent"

        with mock.patch.object(ExtBot, "send_message", fake):
            result = run(bot.send_message(chat_id=1, text="*מבצע* 6*330",
                                          parse_mode="Markdown"))
        self.assertEqual(result, "sent")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1]["text"], "מבצע 6330")

    def test_a_reply_text_reaches_the_override(self):
        """This is what the 40 unprotected call sites actually do."""
        from unittest import mock

        from telegram import Chat, Message
        from telegram.ext import ExtBot

        bot = safesend.SafeBot(self.TOKEN)
        message = Message(message_id=1, date=None, chat=Chat(id=7, type="private"))
        message.set_bot(bot)
        seen = []

        async def fake(*args, **kwargs):
            seen.append(kwargs)
            if kwargs.get("parse_mode"):
                raise BadRequest("Can't parse entities")
            return "sent"

        with mock.patch.object(ExtBot, "send_message", fake):
            run(message.reply_text("*מבצעים*", parse_mode="Markdown"))
        self.assertEqual(len(seen), 2, "reply_text must reach SafeBot.send_message")
        self.assertEqual(seen[1]["text"], "מבצעים")

if __name__ == "__main__":
    unittest.main()
