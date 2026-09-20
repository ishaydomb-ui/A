"""The terminal outcome contract — Phase 11 (2026-09-17).

A run ends in exactly one of four states, decided from run_items, and
the household gets one message naming every gap and no cause narration.
"""
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from grocery_bot import outcome
from grocery_bot.models import CartAddResult, PlanTerm
from grocery_bot.orchestrator import add_terms_to_cart
from grocery_bot.storage import Storage


class ClassifyTests(unittest.TestCase):
    def test_all_verified_is_completed(self):
        self.assertEqual(outcome.classify({"requested": 3, "verified": 3}), "completed")

    def test_skipped_counts_as_settled(self):
        self.assertEqual(outcome.classify({"verified": 2, "skipped": 1}), "completed")

    def test_unverified_without_failures(self):
        self.assertEqual(outcome.classify({"verified": 2, "unverified": 1}), "completed_with_unverified")

    def test_any_failure_is_exceptions(self):
        for k in ("failed_product", "failed_session", "failed_infra", "unresolved_ambiguity"):
            self.assertEqual(outcome.classify({"verified": 2, "unverified": 1, k: 1}),
                             "completed_with_exceptions", k)

    def test_pending_is_aborted_whatever_else_happened(self):
        self.assertEqual(outcome.classify({"verified": 5, "pending": 1}), "aborted")

    def test_an_aborted_lifecycle_is_aborted(self):
        self.assertEqual(outcome.classify({"verified": 5}, status="aborted"), "aborted")

    def test_every_state_is_one_of_the_four(self):
        for counts in ({}, {"verified": 1}, {"unverified": 1}, {"failed_infra": 1}, {"pending": 1}):
            self.assertIn(outcome.classify(counts), outcome.STATES)


class _Adapter:
    name = "tivtaam"

    def __init__(self, script):
        self.script = script  # term -> (status, verification, failure_kind)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def search_and_add(self, term, quantity=1):
        status, verification, kind = self.script.get(term, ("added", "verified", ""))
        return CartAddResult(item_name=term, store=self.name, status=status, quantity=quantity,
                             product_code=f"P_{term}", verification=verification,
                             failure_kind=kind, detail="x" if status != "added" else "")

    def add_specific_product(self, name, quantity=1, **kw):
        return self.search_and_add(name, quantity)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "t.sqlite3")
        self.storage = Storage(self.db)

    def _run(self, script, terms):
        ad = _Adapter(script)
        with __import__("unittest").mock.patch.dict("os.environ", {"GORDON_BREAKER": "off"}):
            reports = add_terms_to_cart(self.storage, {"tivtaam": lambda: ad},
                                        [PlanTerm(t, 1, "adhoc", str(i)) for i, t in enumerate(terms)])
        return reports


class EndToEndTests(Base):
    def test_reports_carry_their_run_id_and_the_run_records_its_outcome(self):
        reports = self._run({}, ["חלב", "לחם"])
        run_id = reports["tivtaam"].run_id
        self.assertIsNotNone(run_id)
        row = sqlite3.connect(self.db).execute(
            "SELECT status, outcome FROM cart_runs WHERE id = ?", (run_id,)).fetchone()
        self.assertEqual(row, ("completed", "completed"))

    def test_a_not_found_item_makes_the_run_exceptional_and_is_named(self):
        reports = self._run({"לחם": ("not_found", "n/a", "product")}, ["חלב", "לחם"])
        out = outcome.summarise(self.storage, reports["tivtaam"].run_id)
        self.assertEqual(out.state, "completed_with_exceptions")
        text = outcome.format_outcome(out)
        self.assertIn("לא נמצא: לחם", text)
        self.assertIn("1/2 בעגלה", text)

    def test_an_unverified_add_is_flagged_not_hidden(self):
        reports = self._run({"לחם": ("added", "unverified", "")}, ["חלב", "לחם"])
        out = outcome.summarise(self.storage, reports["tivtaam"].run_id)
        self.assertEqual(out.state, "completed_with_unverified")
        self.assertIn("לא אומת — בדקו בעגלה: לחם", outcome.format_outcome(out))

    def test_an_infrastructure_failure_names_the_item_not_the_cause(self):
        reports = self._run({"לחם": ("error", "n/a", "infrastructure")}, ["חלב", "לחם"])
        out = outcome.summarise(self.storage, reports["tivtaam"].run_id)
        self.assertEqual(out.state, "completed_with_exceptions")
        text = outcome.format_outcome(out)
        self.assertIn("לא נוסף: לחם", text)
        for word in ("SOCKS", "exit", "breaker", "Uset", "infra"):
            self.assertNotIn(word, text)

    def test_the_consolidated_message_covers_all_reports_of_one_run(self):
        reports = self._run({}, ["חלב"])
        text = outcome.format_outcomes(self.storage, reports)
        self.assertTrue(text.startswith("<b>✅ העגלה מוכנה</b>"))

    def test_reports_without_a_run_id_yield_no_message(self):
        from grocery_bot.models import OrderCycleReport
        self.assertEqual(outcome.format_outcomes(self.storage, {"tivtaam": OrderCycleReport("tivtaam")}), "")

    def test_a_pending_item_reads_as_aborted_with_the_item_named(self):
        run_id = self.storage.start_cart_run("watch_list")
        ids = self.storage.add_run_items(run_id, [PlanTerm("חלב", 1, "adhoc", "1"), PlanTerm("ביצים", 1, "adhoc", "2")])
        self.storage.update_run_item(ids[("adhoc", "1")], store="tivtaam", outcome="verified")
        out = outcome.summarise(self.storage, run_id)
        self.assertEqual(out.state, "aborted")
        text = outcome.format_outcome(out)
        self.assertIn("הריצה נקטעה", text)
        self.assertIn("1 אומתו ו-1 נשארו להשלמה", text)
        self.assertIn("אמשיך בהפעלה הבאה: ביצים", text)


class IsUneventfulTests(Base):
    """Ishay, 2026-09-20: no message when a run finds everything already
    in the cart -- the exact "0/14 בעגלה · 14 כבר היו" shape that was
    recurring every listwatch.COOLDOWN_HOURS."""

    def test_everything_already_there_is_uneventful(self):
        reports = self._run({"חלב": ("skipped", "n/a", ""), "לחם": ("skipped", "n/a", "")},
                            ["חלב", "לחם"])
        self.assertTrue(outcome.is_uneventful(self.storage, reports))

    def test_a_genuine_add_is_not_uneventful(self):
        reports = self._run({}, ["חלב"])
        self.assertFalse(outcome.is_uneventful(self.storage, reports))

    def test_a_not_found_item_is_not_uneventful_even_if_others_are_skipped(self):
        reports = self._run(
            {"חלב": ("skipped", "n/a", ""), "לחם": ("not_found", "n/a", "product")},
            ["חלב", "לחם"],
        )
        self.assertFalse(outcome.is_uneventful(self.storage, reports))

    def test_an_unverified_add_is_not_uneventful(self):
        reports = self._run({"לחם": ("added", "unverified", "")}, ["לחם"])
        self.assertFalse(outcome.is_uneventful(self.storage, reports))

    def test_no_run_id_at_all_is_not_uneventful(self):
        from grocery_bot.models import OrderCycleReport
        self.assertFalse(outcome.is_uneventful(self.storage, {"tivtaam": OrderCycleReport("tivtaam")}))

    def test_empty_reports_is_not_uneventful(self):
        self.assertFalse(outcome.is_uneventful(self.storage, {}))


class EveryClosingPathRecordsAnOutcome(unittest.TestCase):
    """Whichever path closes a run, cart_runs.outcome is set — no silent run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "t.sqlite3")
        self.storage = Storage(self.db)

    def _outcomes(self):
        return sqlite3.connect(self.db).execute("SELECT trigger, status, outcome FROM cart_runs").fetchall()

    def test_the_full_cycle(self):
        from grocery_bot.orchestrator import run_order_cycle
        self.storage.add_base_list_item("חלב")
        with unittest.mock.patch.dict("os.environ", {"GORDON_BREAKER": "off"}):
            reports = run_order_cycle(self.storage, {"tivtaam": lambda: _Adapter({})}, add_deals=False)
        rows = self._outcomes()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "cycle")
        self.assertIn(rows[0][2], outcome.STATES)
        self.assertEqual(reports["tivtaam"].run_id, 1)

    def test_the_resume_path(self):
        from grocery_bot.execution import resume_interrupted
        run_id = self.storage.start_cart_run("watch_list")
        self.storage.add_run_items(run_id, [PlanTerm("חלב", 1, "adhoc", "1")])
        ad = _Adapter({})
        with unittest.mock.patch.dict("os.environ", {"GORDON_BREAKER": "off"}):
            resume_interrupted(self.storage, {"tivtaam": lambda: ad}, None)
        rows = self._outcomes()
        self.assertEqual(rows[0][1:], ("completed", "completed"))

    def test_the_list_watcher_path(self):
        from dataclasses import dataclass
        from grocery_bot.execution import run_list_items

        @dataclass
        class Item:
            id: int
            text: str
            quantity: int = 1
        rid = self.storage.add_adhoc_request(text="חלב", requested_by="x")
        with unittest.mock.patch.dict("os.environ", {"GORDON_BREAKER": "off"}):
            run_list_items(self.storage, {"tivtaam": lambda: _Adapter({})}, [Item(rid, "חלב")])
        self.assertEqual(self._outcomes()[0][1:], ("completed", "completed"))


if __name__ == "__main__":
    unittest.main()
