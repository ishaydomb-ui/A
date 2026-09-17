"""The execution service — Phase 10 (2026-09-17). Extraction, not new behaviour.

Each test pins a rule the Telegram handler used to hold inline, so the
move cannot quietly change it.
"""
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from grocery_bot import execution
from grocery_bot.models import CartAddResult
from grocery_bot.storage import Storage


class _Adapter:
    name = "tivtaam"

    def __init__(self, verify="verified", cart=None):
        self.verify = verify
        self.cart = cart or []
        self.adds = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def cart_summary(self):
        return {"ok": True, "items": list(self.cart), "total": 3.0, "complete": True}

    def search_and_add(self, term, quantity=1):
        self.adds.append(term)
        return CartAddResult(item_name=term, store=self.name, status="added", quantity=quantity,
                             product_code=f"P_{term}", verification=self.verify)

    def add_specific_product(self, name, quantity=1, **kw):
        return self.search_and_add(name, quantity)


@dataclass
class _Item:
    id: int
    text: str
    quantity: int = 1


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "t.sqlite3")
        self.storage = Storage(self.db)

    def _runs(self):
        return sqlite3.connect(self.db).execute("SELECT trigger, status FROM cart_runs").fetchall()


class ListItemsTests(Base):
    def _pending(self, *texts):
        ids = [self.storage.add_adhoc_request(text=t, requested_by="x") for t in texts]
        return [_Item(i, t) for i, t in zip(ids, texts)]

    def test_only_a_verified_add_consumes_a_request(self):
        items = self._pending("חלב", "לחם")
        ad = _Adapter(verify="unverified")
        run_id, reports, outcomes, consumed = execution.run_list_items(
            self.storage, {"tivtaam": lambda: ad}, items)
        self.assertEqual(consumed, [])
        self.assertEqual(set(outcomes.values()), {"unverified"})
        self.assertEqual(self._runs(), [("watch_list", "completed")])

    def test_a_verified_add_consumes_exactly_that_request(self):
        items = self._pending("חלב", "לחם")
        ad = _Adapter()
        _, _, _, consumed = execution.run_list_items(self.storage, {"tivtaam": lambda: ad}, items)
        self.assertEqual(sorted(consumed), sorted(i.id for i in items))
        pending = {r["text"] for r in self.storage.list_pending_adhoc()}
        self.assertEqual(pending, set())

    def test_no_text_means_no_run(self):
        run_id, reports, outcomes, consumed = execution.run_list_items(
            self.storage, {"tivtaam": lambda: _Adapter()}, [_Item(1, "")])
        self.assertIsNone(run_id)
        self.assertEqual(self._runs(), [])

    def test_a_crash_inside_the_fill_aborts_the_run_and_reraises(self):
        items = self._pending("חלב")

        class Boom(_Adapter):
            def ensure_session(self):
                raise RuntimeError("browser died")

        class Factory:
            def __call__(self):
                raise RuntimeError("no browser")
        with self.assertRaises(Exception):
            execution.run_list_items(self.storage, {"tivtaam": Factory()}, items)
        # Either the run was aborted or never opened — never left running.
        self.assertNotIn(("watch_list", "running"), self._runs())


class FinishRunTests(Base):
    def test_pending_items_leave_the_run_interrupted(self):
        run_id = self.storage.start_cart_run("terms")
        from grocery_bot.models import PlanTerm
        self.storage.add_run_items(run_id, [PlanTerm("א", 1, "adhoc", "1")])
        self.assertEqual(execution.finish_run(self.storage, run_id), "interrupted")

    def test_no_pending_items_completes_the_run(self):
        run_id = self.storage.start_cart_run("terms")
        self.assertEqual(execution.finish_run(self.storage, run_id), "completed")


class RefillTests(Base):
    def test_a_refill_runs_under_its_own_trigger_and_records_the_manifest(self):
        self.storage.add_base_list_item(name="חלב", default_quantity=1)
        ad = _Adapter()
        announced = []
        reports = execution.refill(self.storage, {"tivtaam": lambda: ad},
                                   announce=lambda store, total: announced.append((store, total)))
        self.assertIn("tivtaam", reports)
        self.assertEqual(announced[0][0], "tivtaam")
        self.assertIn(("refill", "completed"), self._runs())
        from grocery_bot import standingcart
        self.assertTrue(standingcart.cart_contents(self.storage))

    def test_nothing_planned_means_nothing_announced_and_no_run(self):
        reports = execution.refill(self.storage, {"tivtaam": lambda: _Adapter()},
                                   announce=lambda *a: self.fail("announced"))
        self.assertEqual(reports, {})
        self.assertEqual(self._runs(), [])


class MarkShoppedTests(Base):
    def test_all_marks_every_cart_capable_chain(self):
        from grocery_bot import standingcart
        from grocery_bot.chains import CART_CAPABLE
        execution.mark_shopped(self.storage, "all")
        for chain in CART_CAPABLE:
            self.assertTrue(standingcart.last_shop(self.storage, chain))

    def test_one_chain_marks_only_that_chain(self):
        from grocery_bot import standingcart
        execution.mark_shopped(self.storage, "tivtaam")
        self.assertTrue(standingcart.last_shop(self.storage, "tivtaam"))
        self.assertFalse(standingcart.last_shop(self.storage, "shufersal"))


class ReadCartsTests(Base):
    def test_an_unreachable_chain_is_simply_absent(self):
        class Dead:
            def __call__(self):
                raise RuntimeError("down")
        carts = execution.read_carts({"tivtaam": lambda: _Adapter(cart=[{"code": "1", "name": "x"}]),
                                      "shufersal": Dead()})
        self.assertEqual(set(carts), {"tivtaam"})


class RecoveryTests(Base):
    def test_interrupt_open_runs_marks_every_running_run(self):
        a = self.storage.start_cart_run("watch_list")
        b = self.storage.start_cart_run("refill")
        self.assertEqual(sorted(execution.interrupt_open_runs(self.storage)), [a, b])
        self.assertEqual({s for _, s in self._runs()}, {"interrupted"})

    def test_a_failing_resume_leaves_the_run_interrupted_not_lost(self):
        run_id = self.storage.start_cart_run("watch_list")
        from grocery_bot.models import PlanTerm
        self.storage.add_run_items(run_id, [PlanTerm("חלב", 1, "adhoc", "1")])

        class Dead:
            def __call__(self):
                raise RuntimeError("down")
        results = execution.resume_interrupted(self.storage, {"tivtaam": Dead()}, None)
        self.assertEqual(len(results), 1)
        # Reports may be None (raised) or a report with errors; the run
        # must not be 'running' and must not be gone.
        self.assertNotIn(("watch_list", "running"), self._runs())
        self.assertEqual(len(self._runs()), 1)


class RemovalsSessionNameTests(Base):
    def test_a_tivtaam_style_adapter_with_is_session_valid_is_read(self):
        # Tiv Taam names its check is_session_valid; the old handler only
        # knew ensure_session and logged an AttributeError every night.
        class TT:
            name = "tivtaam"
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def is_session_valid(self): return True
            def cart_summary(self): return {"ok": True, "items": [], "total": 0.0}
        seen = []
        from grocery_bot import standingcart
        orig = standingcart.removals
        standingcart.removals = lambda storage, store, items: seen.append((store, items)) or []
        try:
            execution.log_removals_from_carts(self.storage, {"tivtaam": lambda: TT()})
        finally:
            standingcart.removals = orig
        self.assertEqual(seen, [("tivtaam", [])])


if __name__ == "__main__":
    unittest.main()
