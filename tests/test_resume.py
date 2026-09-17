"""Idempotent resume — Phase 5. An unverified add is checked, never replayed blind.

The duplicate this prevents is real: on 2026-09-17 a re-run after an
unverified add put עגבניות שרי במלח in the cart twice, because nothing
asked the cart first.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot.models import CartAddResult, PlanTerm
from grocery_bot.orchestrator import add_terms_to_cart, presence_check, resume_run
from grocery_bot.storage import Storage


class _Adapter:
    """Scripted cart; counts every add so a duplicate is visible."""

    name = "tivtaam"

    def __init__(self, cart_items=None, ok=True, add_verified=True):
        self.cart_items = cart_items or []
        self.ok = ok
        self.add_verified = add_verified
        self.adds = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def cart_summary(self):
        return {"ok": self.ok, "items": list(self.cart_items), "total": 10.0}

    def search_and_add(self, term, quantity=1):
        self.adds.append(term)
        self.cart_items.append({"code": f"P_{term}", "name": term})
        return CartAddResult(item_name=term, store=self.name, status="added", quantity=quantity,
                             product_code=f"P_{term}",
                             verification="verified" if self.add_verified else "unverified")

    def add_specific_product(self, name, quantity=1, **kw):
        return self.search_and_add(name, quantity)


class PresenceCheckTests(unittest.TestCase):
    def test_present_by_code(self):
        ad = _Adapter([{"code": "P_1", "name": "חלב"}])
        self.assertEqual(presence_check(ad, "tivtaam", "P_1", "חלב"), "present")

    def test_present_by_name_when_no_code(self):
        ad = _Adapter([{"code": "", "name": "עגבניות שרי במלח"}])
        self.assertEqual(presence_check(ad, "tivtaam", "", "עגבניות שרי במלח"), "present")

    def test_absent_is_positive_only_from_a_readable_named_cart(self):
        ad = _Adapter([{"code": "P_9", "name": "לחם"}])
        self.assertEqual(presence_check(ad, "tivtaam", "P_1", "חלב"), "absent")

    def test_unreadable_cart_is_unknown_not_absent(self):
        ad = _Adapter([], ok=False)
        self.assertEqual(presence_check(ad, "tivtaam", "P_1", "חלב"), "unknown")

    def test_placeholder_lines_are_unknown(self):
        # Tiv Taam with the panel shut: count known, names not.
        ad = _Adapter([{"name": "", "qty": ""}, {"name": "", "qty": ""}])
        self.assertEqual(presence_check(ad, "tivtaam", "P_1", "חלב"), "unknown")

    def test_an_adapter_without_a_reader_is_unknown(self):
        class NoReader:
            pass
        self.assertEqual(presence_check(NoReader(), "x", "P_1", "חלב"), "unknown")


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _unverified_run(self, term="חלב"):
        """A run whose one item was sent but never confirmed."""
        run_id = self.storage.start_cart_run("watch_list")
        ids = self.storage.add_run_items(run_id, [PlanTerm(term, 1, "adhoc", "7")])
        item_id = next(iter(ids.values()))
        self.storage.update_run_item(item_id, store="tivtaam", outcome="unverified",
                                     product_code=f"P_{term}", evidence="add sent")
        self.storage.mark_cart_run(run_id, "interrupted")
        return run_id, item_id

    def test_an_item_found_present_is_verified_and_not_re_added(self):
        run_id, item_id = self._unverified_run()
        ad = _Adapter([{"code": "P_חלב", "name": "חלב"}])   # it did land
        resume_run(self.storage, {"tivtaam": lambda: ad}, run_id)
        self.assertEqual(ad.adds, [])                          # no duplicate
        item = self.storage.run_items_for(run_id)[0]
        self.assertEqual(item["outcome"], "verified")
        self.assertIn("presence check", item["evidence"])
        self.assertIsNotNone(item["verified_at"])

    def test_an_item_positively_absent_is_added_once(self):
        run_id, item_id = self._unverified_run()
        ad = _Adapter([{"code": "P_9", "name": "לחם"}])      # readable, not there
        resume_run(self.storage, {"tivtaam": lambda: ad}, run_id)
        self.assertEqual(ad.adds, ["חלב"])
        self.assertEqual(self.storage.run_items_for(run_id)[0]["outcome"], "verified")

    def test_an_unreadable_cart_does_not_re_add(self):
        # The rule that prevents the duplicate: unknown is not absent.
        run_id, item_id = self._unverified_run()
        ad = _Adapter([], ok=False)
        resume_run(self.storage, {"tivtaam": lambda: ad}, run_id)
        self.assertEqual(ad.adds, [])
        item = self.storage.run_items_for(run_id)[0]
        self.assertEqual(item["outcome"], "unverified")
        self.assertIn("presence unknown", item["evidence"])

    def test_resume_keeps_the_run_id_and_its_trigger(self):
        run_id, _ = self._unverified_run()
        ad = _Adapter([{"code": "P_חלב", "name": "חלב"}])
        resume_run(self.storage, {"tivtaam": lambda: ad}, run_id)
        import sqlite3
        c = sqlite3.connect(str(Path(self._tmp.name) / "t.sqlite3"))
        rows = c.execute("SELECT id, trigger, status FROM cart_runs").fetchall()
        self.assertEqual(len(rows), 1)                         # no second run
        self.assertEqual(rows[0][1], "watch_list")             # origin untouched
        self.assertEqual(rows[0][2], "completed")

    def test_pending_items_are_attempted_and_verified_ones_left_alone(self):
        run_id = self.storage.start_cart_run("manual")
        ids = self.storage.add_run_items(run_id, [
            PlanTerm("חלב", 1, "adhoc", "1"), PlanTerm("לחם", 1, "adhoc", "2"),
        ])
        done, todo = ids[("adhoc", "1")], ids[("adhoc", "2")]
        self.storage.update_run_item(done, store="tivtaam", outcome="verified", product_code="P_חלב")
        self.storage.mark_cart_run(run_id, "interrupted")
        ad = _Adapter([{"code": "P_חלב", "name": "חלב"}])
        resume_run(self.storage, {"tivtaam": lambda: ad}, run_id)
        self.assertEqual(ad.adds, ["לחם"])                    # only the pending one
        counts = self.storage.run_counts(run_id)
        self.assertEqual(counts.get("verified"), 2)

    def test_a_run_with_nothing_open_is_simply_completed(self):
        run_id = self.storage.start_cart_run("manual")
        ids = self.storage.add_run_items(run_id, [PlanTerm("חלב", 1, "adhoc", "1")])
        self.storage.update_run_item(next(iter(ids.values())), outcome="verified")
        ad = _Adapter()
        self.assertEqual(resume_run(self.storage, {"tivtaam": lambda: ad}, run_id), {})
        self.assertEqual(ad.adds, [])
        self.assertEqual(self.storage.running_cart_runs(), [])

    def test_the_presence_check_only_applies_to_unverified_items(self):
        # A fresh pending item is added without a cart read first.
        run_id = self.storage.start_cart_run("manual")
        ad = _Adapter([], ok=False)                            # cart unreadable
        add_terms_to_cart(self.storage, {"tivtaam": lambda: ad},
                          [PlanTerm("חלב", 1, "adhoc", "1")], run_id=run_id)
        self.assertEqual(ad.adds, ["חלב"])


if __name__ == "__main__":
    unittest.main()
