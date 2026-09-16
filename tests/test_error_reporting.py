"""A breakage must not wear the costume of an ordinary answer.

Two shapes of the same mistake, both found on 2026-09-16. Here the error
handler only logged, so a failing handler produced *nothing* in the chat
and the household could not tell "the bot broke" from "the bot ignored
me". In the budget project the mirror image: a failed send surfaced as
the help menu, so a breakage read as "it did not understand you".
"""
import unittest


class _Bot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("telegram is down too")
        self.sent.append((chat_id, text))


class _Ctx:
    def __init__(self, bot, error):
        self.bot = bot
        self.error = error


class _Chat:
    def __init__(self, cid):
        self.id = cid


class _Update:
    def __init__(self, cid):
        self.effective_chat = _Chat(cid)


class ErrorReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_breakage_is_reported_not_swallowed(self):
        from grocery_bot.telegram_bot import _on_error

        bot = _Bot()
        await _on_error(_Update(7), _Ctx(bot, RuntimeError("boom")))
        self.assertEqual(len(bot.sent), 1)
        chat, text = bot.sent[0]
        self.assertEqual(chat, 7)
        self.assertIn("נשבר", text)
        # It says the request did NOT happen — the fact that decides
        # whether the household retries.
        self.assertIn("לא בוצעה", text)

    async def test_the_exception_text_never_reaches_the_family_group(self):
        from grocery_bot.telegram_bot import _on_error

        bot = _Bot()
        await _on_error(
            _Update(7), _Ctx(bot, RuntimeError("secret-token-abc123 leaked"))
        )
        self.assertNotIn("secret-token", bot.sent[0][1])

    async def test_an_expired_tap_is_still_silent(self):
        # Benign and not actionable; reporting it would be the noise this
        # filter was written to remove.
        from telegram.error import BadRequest

        from grocery_bot.telegram_bot import _on_error

        bot = _Bot()
        await _on_error(_Update(7), _Ctx(bot, BadRequest("Query is too old")))
        self.assertEqual(bot.sent, [])

    async def test_an_update_with_no_chat_is_not_an_error(self):
        from grocery_bot.telegram_bot import _on_error

        bot = _Bot()
        await _on_error(object(), _Ctx(bot, RuntimeError("boom")))
        self.assertEqual(bot.sent, [])

    async def test_failing_to_report_a_failure_does_not_raise(self):
        from grocery_bot.telegram_bot import _on_error

        bot = _Bot(fail=True)
        await _on_error(_Update(7), _Ctx(bot, RuntimeError("boom")))  # must not raise


if __name__ == "__main__":
    unittest.main()
