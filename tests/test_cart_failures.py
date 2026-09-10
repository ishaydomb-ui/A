"""An item that fails every run must become visible, not just repeat.

Both cycles already retried failures. What was missing was memory: only
`report.added` was persisted, so a term failing on every single run was
indistinguishable from one failing for the first time, and the only
trace was a "לא נמצא (N)" count in a message that scrolled away.
Ishay asked on 2026-09-10 whether the daily runs retry what failed; they
do, and this is the half that was not there.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from grocery_bot.models import CartAddResult
from grocery_bot.orchestrator import format_repeat_failures
from grocery_bot.storage import Storage


def _res(name, store="shufersal", status="not_found", detail=""):
    return CartAddResult(item_name=name, store=store, status=status, detail=detail)


class Recording(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
        self.storage = Storage(self.path)

    def test_only_failures_are_recorded(self):
        n = self.storage.record_cart_failures([
            _res("חלב", status="added"),
            _res("לחם", status="not_found"),
            _res("ביצים", status="error", detail="timeout"),
            _res("קוטג'", status="ambiguous"),
        ])
        self.assertEqual(n, 2, "added and ambiguous are not failures")

    def test_an_ambiguous_item_is_not_a_failure(self):
        """A question was actually put to the household; that is not the
        same as the item being unavailable."""
        self.assertEqual(self.storage.record_cart_failures([_res("גבינה", status="ambiguous")]), 0)

    def test_nothing_to_record_is_not_an_error(self):
        self.assertEqual(self.storage.record_cart_failures([]), 0)
        self.assertEqual(self.storage.record_cart_failures(None), 0)


class RepeatDetection(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
        self.storage = Storage(self.path)

    def _fail(self, name, runs, store="shufersal", days_ago_start=10):
        for i in range(runs):
            when = (datetime.now(timezone.utc) - timedelta(days=days_ago_start - i)).isoformat(
                timespec="seconds"
            )
            self.storage.record_cart_failures([_res(name, store=store)], when=when)

    def test_a_single_miss_is_not_reported(self):
        """A first or second miss is usually the store's search. Reporting
        it would train everyone to ignore this line."""
        self._fail("לחם", runs=2)
        self.assertEqual(self.storage.repeat_failures(min_runs=3), [])

    def test_a_persistent_failure_is_reported_with_its_history(self):
        self._fail("במבה אישית", runs=5)
        rows = self.storage.repeat_failures(min_runs=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_name"], "במבה אישית")
        self.assertEqual(rows[0]["runs"], 5)
        self.assertTrue(rows[0]["first_failed"] < rows[0]["last_failed"])

    def test_one_bad_run_cannot_masquerade_as_a_persistent_problem(self):
        """A cycle writes many rows at one instant; counting rows rather
        than distinct runs would make a single bad night look chronic."""
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for _ in range(6):
            self.storage.record_cart_failures([_res("טופו")], when=when)
        self.assertEqual(self.storage.repeat_failures(min_runs=3), [])

    def test_the_same_item_on_two_chains_is_two_findings(self):
        """Failing at Shufersal and succeeding at Tiv Taam is a different
        situation from failing at both."""
        self._fail("חלב סויה", runs=4, store="shufersal")
        self._fail("חלב סויה", runs=4, store="tivtaam")
        rows = self.storage.repeat_failures(min_runs=3)
        self.assertEqual({r["store"] for r in rows}, {"shufersal", "tivtaam"})

    def test_old_failures_fall_out_of_the_window(self):
        self._fail("פסטה", runs=4, days_ago_start=200)
        self.assertEqual(self.storage.repeat_failures(min_runs=3, days=60), [])

    def test_clearing_forgets_a_fixed_term(self):
        self._fail("שמן זית", runs=4)
        self.assertTrue(self.storage.repeat_failures(min_runs=3))
        self.storage.clear_cart_failures("שמן זית")
        self.assertEqual(self.storage.repeat_failures(min_runs=3), [])


class TheReportLine(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
        self.storage = Storage(self.path)

    def test_nothing_recurring_produces_no_line_at_all(self):
        self.assertEqual(format_repeat_failures(self.storage), "")

    def test_a_recurring_item_is_named_with_its_run_count(self):
        for i in range(4):
            when = (datetime.now(timezone.utc) - timedelta(days=5 - i)).isoformat(
                timespec="seconds"
            )
            self.storage.record_cart_failures([_res("קרקר אורז")], when=when)
        text = format_repeat_failures(self.storage)
        self.assertIn("קרקר אורז", text)
        self.assertIn("4", text)
        self.assertIn("shufersal", text)

    def test_a_storage_failure_costs_the_line_not_the_report(self):
        class Broken:
            def repeat_failures(self, **kwargs):
                raise RuntimeError("db gone")

        self.assertEqual(format_repeat_failures(Broken()), "")


if __name__ == "__main__":
    unittest.main()


class WiredIntoTheCycle(unittest.TestCase):
    """Recording that no cycle calls is recording that never happens."""

    def test_every_point_a_report_is_finalised_records_first(self):
        import inspect
        import re

        from grocery_bot import orchestrator

        source = inspect.getsource(orchestrator)
        finalisations = len(re.findall(r"reports\[store\] = report", source))
        recordings = len(re.findall(r"_remember_failures\(storage, report\)", source))
        self.assertEqual(recordings, finalisations,
                         "a report finalised without recording loses its failures")
        self.assertGreaterEqual(finalisations, 4)

    def test_a_bookkeeping_failure_never_fails_the_cycle(self):
        """A cycle that filled a cart must not be reported as failed
        because an insert did not land."""
        from grocery_bot.orchestrator import _remember_failures

        class Broken:
            def record_cart_failures(self, results):
                raise RuntimeError("disk full")

        class Report:
            not_found = [_res("חלב")]
            errors = []

        _remember_failures(Broken(), Report())  # must not raise
