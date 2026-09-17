"""Crash/restart resume — Phase 7. A run left `running` is picked up on start.

Deploys cannot interrupt a fill (refresh_bot.sh refuses while a child
browser is active); this covers crash, OOM, manual kill, unexpected
restart. Before it, nothing looked for an interrupted run at startup and
the list watcher's six-hour cooldown then blocked any retry.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grocery_bot.models import CartAddResult, PlanTerm
from grocery_bot.storage import Storage
from grocery_bot.telegram_bot import GroceryBot


class _Adapter:
    name = "tivtaam"

    def __init__(self, cart=None):
        self.cart = cart or []
        self.adds = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def cart_summary(self):
        return {"ok": True, "items": list(self.cart), "total": 1.0, "complete": True}

    def search_and_add(self, term, quantity=1):
        self.adds.append(term)
        self.cart.append({"code": f"P_{term}", "name": term})
        return CartAddResult(item_name=term, store=self.name, status="added", quantity=quantity,
                             product_code=f"P_{term}", verification="verified")

    def add_specific_product(self, name, quantity=1, **kw):
        return self.search_and_add(name, quantity)


class _Ctx:
    class _Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)

    def __init__(self):
        self.bot = self._Bot()


class StartupResumeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.bot = GroceryBot.__new__(GroceryBot)
        self.bot.storage = self.storage
        self.bot.config = type("C", (), {"playwright_proxy": "socks5://x"})()
        self.adapter = _Adapter()
        self._patches = [
            mock.patch("grocery_bot.telegram_bot.ensure_israeli_exit",
                       return_value=type("S", (), {"available": True, "detail": ""})()),
            mock.patch("grocery_bot.telegram_bot._build_adapter_factories",
                       return_value={"tivtaam": lambda: self.adapter}),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _crashed_run(self):
        """A run a previous process left mid-fill: one verified, one unverified, one untouched."""
        run_id = self.storage.start_cart_run("watch_list")
        ids = self.storage.add_run_items(run_id, [
            PlanTerm("חלב", 1, "adhoc", "1"), PlanTerm("לחם", 1, "adhoc", "2"), PlanTerm("ביצים", 1, "adhoc", "3"),
        ])
        self.storage.update_run_item(ids[("adhoc", "1")], store="tivtaam", outcome="verified", product_code="P_חלב")
        self.storage.update_run_item(ids[("adhoc", "2")], store="tivtaam", outcome="unverified", product_code="P_לחם")
        # "running" and no finished_at: exactly what a crash leaves behind.
        return run_id

    def test_a_crashed_run_is_resumed_under_its_own_id(self):
        run_id = self._crashed_run()
        self.adapter.cart = [{"code": "P_חלב", "name": "חלב"}, {"code": "P_לחם", "name": "לחם"}]  # לחם did land
        asyncio.run(self.bot.resume_runs(_Ctx()))
        import sqlite3
        c = sqlite3.connect(str(Path(self._tmp.name) / "t.sqlite3"))
        runs = c.execute("SELECT id, trigger, status FROM cart_runs").fetchall()
        self.assertEqual(len(runs), 1)                        # same run, no new one
        self.assertEqual(runs[0][1], "watch_list")            # origin preserved
        self.assertEqual(runs[0][2], "completed")
        # לחם was presence-checked and found — not re-added. ביצים was added once.
        self.assertEqual(self.adapter.adds, ["ביצים"])
        counts = self.storage.run_counts(run_id)
        self.assertEqual(counts.get("verified"), 3)

    def test_the_unverified_item_is_not_replayed_when_presence_is_unknown(self):
        run_id = self._crashed_run()
        self.adapter.cart_summary = lambda: {"ok": False, "items": [], "total": None, "read": "failed"}
        asyncio.run(self.bot.resume_runs(_Ctx()))
        self.assertNotIn("לחם", self.adapter.adds)
        item = next(r for r in self.storage.run_items_for(run_id) if r["term"] == "לחם")
        self.assertEqual(item["outcome"], "unverified")

    def test_nothing_open_means_nothing_happens(self):
        asyncio.run(self.bot.resume_runs(_Ctx()))
        self.assertEqual(self.adapter.adds, [])

    def test_no_exit_node_leaves_the_run_interrupted_not_lost(self):
        run_id = self._crashed_run()
        with mock.patch("grocery_bot.telegram_bot.ensure_israeli_exit",
                        return_value=type("S", (), {"available": False, "detail": "down"})()):
            asyncio.run(self.bot.resume_runs(_Ctx()))
        self.assertEqual(self.adapter.adds, [])
        self.assertEqual(self.storage.running_cart_runs()[0]["status"], "interrupted")
        self.assertEqual(self.storage.running_cart_runs()[0]["id"], run_id)

    def test_the_watchers_cooldown_does_not_shadow_the_resume(self):
        # The interrupted run itself set the cooldown; resume must ignore it.
        from grocery_bot import listwatch
        listwatch.note_ran(self.storage)                       # cooldown active
        self._crashed_run()
        self.adapter.cart = [{"code": "P_חלב", "name": "חלב"}, {"code": "P_לחם", "name": "לחם"}]
        asyncio.run(self.bot.resume_runs(_Ctx()))
        self.assertEqual(self.adapter.adds, ["ביצים"])


if __name__ == "__main__":
    unittest.main()
