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


class RememberedChoiceTests(unittest.TestCase):
    """A real search returns ~20 tiles for everyday terms, so without a
    remembered choice every item is "ambiguous" and the bot interrogates
    the user on every cycle. These cover the memory that prevents that."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._dir.name) / "t.sqlite3"))
        self.storage.add_base_list_item(name="חלב 3%")

    def tearDown(self):
        self._dir.cleanup()

    def _cycle(self, adapter):
        return run_order_cycle(self.storage, {"shufersal": lambda: adapter})

    def test_without_a_choice_the_user_is_asked(self):
        report = self._cycle(_AlwaysAmbiguousAdapter())["shufersal"]
        self.assertEqual(len(report.ambiguous), 1)
        self.assertEqual(len(self.storage.list_pending_ambiguities()), 1)

    def test_a_remembered_choice_stops_the_question(self):
        self.storage.remember_choice("shufersal", "חלב 3%", "P_111", "חלב 3% בקרטון")
        adapter = _AlwaysAmbiguousAdapter()
        report = self._cycle(adapter)["shufersal"]
        self.assertEqual(report.ambiguous, [])
        self.assertEqual(len(report.added), 1)
        self.assertEqual(adapter.specific_calls[0][1], "P_111", "should match on code, not name")

    def test_a_delisted_remembered_product_falls_back_to_searching(self):
        self.storage.remember_choice("shufersal", "חלב 3%", "P_GONE", "מוצר שהוסר")
        report = self._cycle(_MissingSpecificAdapter())["shufersal"]
        self.assertEqual(len(report.ambiguous), 1, "should re-ask rather than fail")
        self.assertIsNone(
            self.storage.preferred_for("shufersal", "חלב 3%"),
            "a dead choice must be forgotten so it isn't retried forever",
        )

    def test_adhoc_survives_a_transient_error(self):
        self.storage.add_adhoc_request(text="פסטרמה", requested_by="ישי")
        self._cycle(_ErroringAdapter())
        self.assertEqual(
            [r.text for r in self.storage.list_pending_adhoc()],
            ["פסטרמה"],
            "an errored request must stay queued, not vanish",
        )

    def test_adhoc_is_consumed_once_added(self):
        self.storage.add_adhoc_request(text="פסטרמה", requested_by="ישי")
        self._cycle(_AlwaysAddsAdapter())
        self.assertEqual(self.storage.list_pending_adhoc(), [])


class _AlwaysAmbiguousAdapter:
    name = "shufersal"

    def __init__(self):
        self.specific_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search_and_add(self, term, quantity=1):
        return CartAddResult(
            item_name=term, store="shufersal", status="ambiguous",
            candidates=[f"{term} א", f"{term} ב"], quantity=quantity,
        )

    def add_specific_product(self, label, quantity=1, product_code="", search_term=""):
        self.specific_calls.append((label, product_code, search_term))
        return CartAddResult(
            item_name=label, store="shufersal", status="added",
            quantity=quantity, product_code=product_code,
        )


class _MissingSpecificAdapter(_AlwaysAmbiguousAdapter):
    def add_specific_product(self, label, quantity=1, product_code="", search_term=""):
        return CartAddResult(item_name=label, store="shufersal", status="not_found", quantity=quantity)


class _ErroringAdapter(_AlwaysAmbiguousAdapter):
    def search_and_add(self, term, quantity=1):
        return CartAddResult(item_name=term, store="shufersal", status="error", detail="boom")


class _AlwaysAddsAdapter(_AlwaysAmbiguousAdapter):
    def search_and_add(self, term, quantity=1):
        return CartAddResult(item_name=term, store="shufersal", status="added", quantity=quantity)
