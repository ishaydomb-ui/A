import tempfile
import unittest
from pathlib import Path

from grocery_bot.adapters.base import StoreAdapter
from grocery_bot.models import CartAddResult
from grocery_bot.orchestrator import format_report_summary, run_order_cycle
from grocery_bot.storage import Storage


class FakeAdapter(StoreAdapter):
    """In-memory stand-in for a real browser-driven adapter, for testing
    the orchestrator's merge/reporting logic without Playwright or a
    network call."""

    name = "fake_store"

    def __init__(self, outcomes: dict[str, str]):
        # term -> status ("added" | "ambiguous" | "not_found" | "error")
        self._outcomes = outcomes
        self.added_calls: list[tuple[str, int]] = []
        self.closed = False

    def is_session_valid(self) -> bool:
        return True

    def search_and_add(self, term: str, quantity: int = 1) -> CartAddResult:
        status = self._outcomes.get(term, "not_found")
        candidates = [f"{term} א", f"{term} ב"] if status == "ambiguous" else []
        if status == "added":
            self.added_calls.append((term, quantity))
        return CartAddResult(item_name=term, store=self.name, status=status, candidates=candidates, quantity=quantity)

    def add_specific_product(self, product_label: str, quantity: int = 1) -> CartAddResult:
        self.added_calls.append((product_label, quantity))
        return CartAddResult(item_name=product_label, store=self.name, status="added", quantity=quantity)

    def close(self) -> None:
        self.closed = True


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmpdir.name) / "test.sqlite3")
        self.storage = Storage(db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_merges_base_list_and_adhoc_and_classifies_results(self) -> None:
        self.storage.add_base_list_item("חלב", default_quantity=2)
        self.storage.add_base_list_item("טונה")
        self.storage.add_adhoc_request("סבון כלים", requested_by="ליראן")

        fake = FakeAdapter({"חלב": "added", "טונה": "ambiguous", "סבון כלים": "not_found"})
        reports = run_order_cycle(self.storage, {"fake_store": lambda: fake})

        report = reports["fake_store"]
        self.assertEqual([r.item_name for r in report.added], ["חלב"])
        self.assertEqual([r.item_name for r in report.ambiguous], ["טונה"])
        self.assertEqual([r.item_name for r in report.not_found], ["סבון כלים"])
        self.assertEqual(fake.added_calls, [("חלב", 2)])
        self.assertTrue(fake.closed)

    def test_ambiguous_results_are_persisted_for_followup(self) -> None:
        self.storage.add_base_list_item("טונה")
        fake = FakeAdapter({"טונה": "ambiguous"})

        run_order_cycle(self.storage, {"fake_store": lambda: fake})

        pending = self.storage.list_pending_ambiguities()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["original_term"], "טונה")
        self.assertEqual(pending[0]["candidates"], ["טונה א", "טונה ב"])

    def test_adhoc_requests_are_consumed_after_the_cycle(self) -> None:
        self.storage.add_adhoc_request("מגבות נייר", requested_by="אני")
        fake = FakeAdapter({"מגבות נייר": "added"})

        run_order_cycle(self.storage, {"fake_store": lambda: fake})

        self.assertEqual(self.storage.list_pending_adhoc(), [])

    def test_format_report_summary_is_readable(self) -> None:
        self.storage.add_base_list_item("חלב")
        fake = FakeAdapter({"חלב": "added"})
        reports = run_order_cycle(self.storage, {"fake_store": lambda: fake})

        summary = format_report_summary(reports)
        self.assertIn("fake_store", summary)
        self.assertIn("חלב", summary)


if __name__ == "__main__":
    unittest.main()
