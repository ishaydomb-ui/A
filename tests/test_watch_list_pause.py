"""When every enabled store's cart writer is paused (Work-MVP benchmark
or otherwise), the list watcher must not run a cycle or send a message.

Found 2026-09-19 from the household's own chat: with Tiv Taam paused,
the same "14 already there" notice kept landing every ~6h forever --
listwatch.note_ran() resets the cooldown unconditionally, and a paused
store's cart writer never actually consumes a pending request, so the
next cooldown always finds the same 14 items still pending and fires
again. See telegram_bot.py::watch_list.
"""
import asyncio
import tempfile
import unittest
from unittest import mock

from grocery_bot import cartpause
from grocery_bot.config import Config
from grocery_bot.storage import AdHocRequest, Storage
from grocery_bot.telegram_bot import GroceryBot


def _config(enabled_stores) -> Config:
    return Config(
        telegram_bot_token="t",
        allowed_telegram_user_ids=[],
        db_path=":memory:",
        shufersal_storage_state_path="x.json",
        tivtaam_storage_state_path="y.json",
        enabled_stores=enabled_stores,
    )


def _item() -> AdHocRequest:
    return AdHocRequest(
        id=1, text="חלב", requested_by="ishay", created_at="2026-09-19T03:00:00+00:00",
        quantity=1, consumed=False, amount=None, unit="", brand="",
    )


class WatchListPauseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.storage = Storage(self._tmp.name)
        self.storage.set_state("digest_chat_id", "555")

    def _run(self, bot) -> None:
        context = mock.MagicMock()
        context.bot.send_message = mock.AsyncMock()
        asyncio.run(bot.watch_list(context))
        return context

    def test_no_message_and_no_run_when_only_enabled_store_is_paused(self) -> None:
        config = _config(["tivtaam"])
        cartpause.set_paused(self.storage, "tivtaam", True, reason="Work MVP benchmark")
        bot = GroceryBot(config, self.storage)

        with mock.patch("grocery_bot.listwatch.assess", return_value=("run", [_item()])), \
             mock.patch("grocery_bot.telegram_bot.ensure_israeli_exit",
                        return_value=mock.Mock(available=True)), \
             mock.patch("grocery_bot.telegram_bot._build_adapter_factories",
                        return_value={"tivtaam": lambda: None}), \
             mock.patch("grocery_bot.telegram_bot.execution.run_list_items",
                        side_effect=AssertionError("must not run a cart cycle while paused")):
            context = self._run(bot)

        context.bot.send_message.assert_not_called()

    def test_run_proceeds_normally_when_not_paused(self) -> None:
        config = _config(["tivtaam"])
        bot = GroceryBot(config, self.storage)

        with mock.patch("grocery_bot.listwatch.assess", return_value=("run", [_item()])), \
             mock.patch("grocery_bot.telegram_bot.ensure_israeli_exit",
                        return_value=mock.Mock(available=True)), \
             mock.patch("grocery_bot.telegram_bot._build_adapter_factories",
                        return_value={"tivtaam": lambda: None}), \
             mock.patch("grocery_bot.telegram_bot.execution.run_list_items",
                        return_value=(1, {}, {}, [])), \
             mock.patch.object(GroceryBot, "_store_cycle_summary", return_value=("done", None)), \
             mock.patch.object(GroceryBot, "_ask_ambiguities", new=mock.AsyncMock()):
            context = self._run(bot)

        self.assertGreaterEqual(context.bot.send_message.call_count, 1)

    def test_still_gated_when_multiple_enabled_stores_are_all_paused(self) -> None:
        config = _config(["tivtaam", "shufersal"])
        cartpause.set_paused(self.storage, "tivtaam", True)
        cartpause.set_paused(self.storage, "shufersal", True)
        bot = GroceryBot(config, self.storage)

        with mock.patch("grocery_bot.listwatch.assess", return_value=("run", [_item()])), \
             mock.patch("grocery_bot.telegram_bot.ensure_israeli_exit",
                        return_value=mock.Mock(available=True)), \
             mock.patch("grocery_bot.telegram_bot._build_adapter_factories",
                        return_value={"tivtaam": lambda: None, "shufersal": lambda: None}), \
             mock.patch("grocery_bot.telegram_bot.execution.run_list_items",
                        side_effect=AssertionError("must not run while every store is paused")):
            context = self._run(bot)

        context.bot.send_message.assert_not_called()

    def test_run_proceeds_when_only_one_of_several_stores_is_paused(self) -> None:
        """A partial pause must not silence the stores that are live."""
        config = _config(["tivtaam", "shufersal"])
        cartpause.set_paused(self.storage, "tivtaam", True)
        bot = GroceryBot(config, self.storage)

        with mock.patch("grocery_bot.listwatch.assess", return_value=("run", [_item()])), \
             mock.patch("grocery_bot.telegram_bot.ensure_israeli_exit",
                        return_value=mock.Mock(available=True)), \
             mock.patch("grocery_bot.telegram_bot._build_adapter_factories",
                        return_value={"tivtaam": lambda: None, "shufersal": lambda: None}), \
             mock.patch("grocery_bot.telegram_bot.execution.run_list_items",
                        return_value=(1, {}, {}, [])), \
             mock.patch.object(GroceryBot, "_store_cycle_summary", return_value=("done", None)), \
             mock.patch.object(GroceryBot, "_ask_ambiguities", new=mock.AsyncMock()):
            context = self._run(bot)

        self.assertGreaterEqual(context.bot.send_message.call_count, 1)


if __name__ == "__main__":
    unittest.main()
