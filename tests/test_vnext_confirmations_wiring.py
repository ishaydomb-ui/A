"""vNext Phase 2a: human confirmations accumulate from the household's
real acts -- a disambiguation tap, "שנה" after it, a spoken replacement --
and from nothing else. A failure in the recorder never changes what the
household sees.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grocery_bot import vnext_confirmations
from grocery_bot.config import Config
from grocery_bot.models import CartAddResult
from grocery_bot.replace import replace_product
from grocery_bot.storage import Storage
from grocery_bot.telegram_bot import GroceryBot
from grocery_bot.vnext_resolver import human_confirmations, human_rejections


def _config() -> Config:
    return Config(
        telegram_bot_token="t", allowed_telegram_user_ids=[], db_path=":memory:",
        shufersal_storage_state_path="x.json", tivtaam_storage_state_path="y.json",
        enabled_stores=["shufersal"],
    )


class _Adapter:
    name = "shufersal"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def cart_summary(self):
        return {"ok": True, "items": [], "total": 0.0, "complete": True}

    def add_specific_product(self, name, quantity=1, **kw):
        return CartAddResult(item_name=name, store=self.name, status="added",
                             product_code=kw.get("product_code", ""), verification="verified")

    def search_and_add(self, term, quantity=1):
        return CartAddResult(item_name=f"{term} 1 ליטר", store=self.name, status="added",
                             product_code=f"P_{term}", verification="verified")

    def remove_item(self, code):
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def rows(self):
        return self.storage.list_vnext_product_confirmations()


class RecorderTests(Base):
    def test_later_correction_is_negative_by_default(self):
        vnext_confirmations.record_human_confirmation(self.storage, "חלב", "P1", "shufersal", "later_correction")
        self.assertEqual(self.rows()[0]["polarity"], "negative")

    def test_a_tap_is_positive(self):
        vnext_confirmations.record_human_confirmation(self.storage, "חלב", "P1", "shufersal", "explicit_statement")
        self.assertEqual(self.rows()[0]["polarity"], "positive")

    def test_note_interaction_never_raises(self):
        broken = mock.Mock()
        broken.add_vnext_product_confirmation.side_effect = RuntimeError("disk on fire")
        self.assertIsNone(vnext_confirmations.note_interaction(broken, "חלב", "P1", "shufersal", "explicit_statement"))
        self.assertIsNone(vnext_confirmations.note_interaction(self.storage, "", "P1", "shufersal", "explicit_statement"))
        self.assertEqual(self.rows(), [])

    def test_resolver_reads_polarity_and_the_newest_row_wins(self):
        vnext_confirmations.record_human_confirmation(self.storage, "חלב", "P1", "shufersal", "explicit_statement", product_name="חלב 3%")
        self.assertEqual([c.product_code for c in human_confirmations(self.storage, "חלב")], ["P1"])
        vnext_confirmations.record_human_confirmation(self.storage, "חלב", "P1", "shufersal", "later_correction")
        self.assertEqual(human_confirmations(self.storage, "חלב"), [])
        self.assertEqual(human_rejections(self.storage, "חלב"), {("shufersal", "P1")})
        vnext_confirmations.record_human_confirmation(self.storage, "חלב", "P1", "shufersal", "explicit_statement")
        self.assertEqual([c.product_code for c in human_confirmations(self.storage, "חלב")], ["P1"])
        self.assertEqual(human_rejections(self.storage, "חלב"), set())


class DisambiguationTapTests(Base):
    def _tap(self, bot, data):
        query = mock.MagicMock()
        query.data = data
        query.from_user.id = 42
        query.answer = mock.AsyncMock()
        query.edit_message_text = mock.AsyncMock()
        query.edit_message_reply_markup = mock.AsyncMock()
        query.message.chat_id = 555
        update = mock.MagicMock(callback_query=query)
        context = mock.MagicMock()
        context.bot.send_message = mock.AsyncMock()
        with mock.patch("grocery_bot.telegram_bot._build_adapter_factories",
                        return_value={"shufersal": lambda: _Adapter()}), \
             mock.patch.object(GroceryBot, "_advance_question", new=mock.AsyncMock(return_value=False)):
            asyncio.run(bot.resolve_ambiguity(update, context))
        return query, context

    def test_a_tap_records_exactly_one_explicit_statement(self):
        bot = GroceryBot(_config(), self.storage)
        amb = self.storage.save_pending_ambiguity(
            "shufersal", "קוטג", 1, ["קוטג' 5%", "קוטג' 3%"],
            [{"code": "P_5", "name": "קוטג' 5%"}, {"code": "P_3", "name": "קוטג' 3%"}],
        )
        self._tap(bot, f"resolve:{amb}:1")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["product_code"], rows[0]["polarity"], rows[0]["confirmed_by"]),
                         ("explicit_statement", "P_3", "positive", "42"))

    def test_a_recorder_failure_does_not_change_the_reply(self):
        bot = GroceryBot(_config(), self.storage)
        amb = self.storage.save_pending_ambiguity(
            "shufersal", "קוטג", 1, ["קוטג' 5%"], [{"code": "P_5", "name": "קוטג' 5%"}],
        )
        with mock.patch("grocery_bot.storage.Storage.add_vnext_product_confirmation",
                        side_effect=RuntimeError("boom")):
            query, _ = self._tap(bot, f"resolve:{amb}:0")
        text = query.edit_message_text.call_args[0][0]
        self.assertIn("נוסף לסל", text)
        self.assertEqual(self.rows(), [])
        self.assertIsNotNone(self.storage.preferred_for("shufersal", "קוטג"))

    def test_undo_records_a_negative_later_correction(self):
        bot = GroceryBot(_config(), self.storage)
        amb = self.storage.save_pending_ambiguity(
            "shufersal", "קוטג", 1, ["קוטג' 5%"], [{"code": "P_5", "name": "קוטג' 5%"}],
        )
        self.storage.remember_choice("shufersal", "קוטג", "P_5", "קוטג' 5%", source="human")
        query = mock.MagicMock()
        query.data = f"undo:{amb}"
        query.from_user.id = 42
        query.answer = mock.AsyncMock()
        query.message.chat_id = 555
        update = mock.MagicMock(callback_query=query)
        context = mock.MagicMock()
        context.bot.send_message = mock.AsyncMock()
        with mock.patch("grocery_bot.telegram_bot._authorized", return_value=True), \
             mock.patch.object(GroceryBot, "_undo_choice", return_value=True):
            asyncio.run(bot.on_choice_followup(update, context))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["product_code"], rows[0]["polarity"]),
                         ("later_correction", "P_5", "negative"))


class ReplaceFlowTests(Base):
    def test_replace_records_old_as_correction_and_new_as_accepted_substitution(self):
        self.storage.remember_choice("shufersal", "חלב תנובה", "P_OLD", "חלב תנובה 3%", source="inferred")
        replace_product(self.storage, {"shufersal": lambda: _Adapter()}, "shufersal", "חלב תנובה", "חלב יטבתה")
        rows = {(r["term"], r["kind"]): r for r in self.rows()}
        self.assertEqual(len(rows), 2)
        old = rows[("חלב תנובה", "later_correction")]
        new = rows[("חלב יטבתה", "accepted_substitution")]
        self.assertEqual((old["product_code"], old["polarity"]), ("P_OLD", "negative"))
        self.assertEqual((new["product_code"], new["polarity"]), ("P_חלב יטבתה", "positive"))

    def test_autoresolve_writes_nothing_here(self):
        from grocery_bot import autoresolve
        source = open(autoresolve.__file__, encoding="utf-8").read()
        self.assertNotIn("vnext_confirmations", source)


if __name__ == "__main__":
    unittest.main()
