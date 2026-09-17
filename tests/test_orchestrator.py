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
        self.assertEqual([r.item_name for r in report.added], ["חלב", "טונה א"])
        self.assertEqual(report.ambiguous, [])
        self.assertEqual([r.item_name for r in report.not_found], ["סבון כלים"])
        # "טונה" now lands too: it came back ambiguous and was decided
        # from the candidates rather than put to the household.
        self.assertEqual(fake.added_calls, [("חלב", 2), ("טונה א", 1)])
        self.assertTrue(fake.closed)

    def test_an_ambiguous_result_is_decided_from_the_candidates(self) -> None:
        # Was: persisted as a question for follow-up. Now decided — see
        # RememberedChoiceTests for why the old contract was the bug.
        self.storage.add_base_list_item("טונה")
        fake = FakeAdapter({"טונה": "ambiguous"})

        reports = run_order_cycle(self.storage, {"fake_store": lambda: fake})

        self.assertEqual(self.storage.list_pending_ambiguities(), [])
        self.assertEqual(len(reports["fake_store"].added), 1)

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

    def test_without_a_choice_it_is_decided_rather_than_asked(self):
        """Contract changed 2026-09-17 on Ishay's instruction.

        Previously an ambiguous result became a question. It now resolves
        from purchase history, and only a term that nothing answers is
        left open. The old behaviour is why 134 questions accumulated:
        Tiv Taam's search returns names without cards, so the card-based
        resolver could never fire there and *every* ambiguous term became
        a question by construction."""
        report = self._cycle(_AlwaysAmbiguousAdapter())["shufersal"]
        self.assertEqual(report.ambiguous, [])
        self.assertEqual(len(report.added), 1)
        self.assertEqual(self.storage.list_pending_ambiguities(), [])

    def test_the_decision_says_what_it_rested_on(self):
        # A silent decision cannot be corrected by someone who never saw it.
        report = self._cycle(_AlwaysAmbiguousAdapter())["shufersal"]
        self.assertTrue(getattr(report.added[0], "auto_resolved", ""))

    def test_a_remembered_choice_stops_the_question(self):
        self.storage.remember_choice("shufersal", "חלב 3%", "P_111", "חלב 3% בקרטון")
        adapter = _AlwaysAmbiguousAdapter()
        report = self._cycle(adapter)["shufersal"]
        self.assertEqual(report.ambiguous, [])
        self.assertEqual(len(report.added), 1)
        self.assertEqual(adapter.specific_calls[0][1], "P_111", "should match on code, not name")

    def test_a_missing_remembered_product_falls_back_to_searching(self):
        self.storage.remember_choice("shufersal", "חלב 3%", "P_GONE", "מוצר שהוסר")
        report = self._cycle(_MissingSpecificAdapter())["shufersal"]
        self.assertEqual(len(report.ambiguous), 1, "should re-ask rather than fail")

    def test_a_missing_remembered_product_is_not_forgotten(self):
        """A single miss is usually the store's search, not a decision.

        Deleting on the first failure threw away choices that took a real
        conversation to establish, and it happened routinely: searching a
        long exact product name often fails to surface that very product.
        The cost of keeping it is one wasted lookup on a genuinely
        delisted item; the memory self-heals because answering the
        follow-up question overwrites it.
        """
        self.storage.remember_choice("shufersal", "חלב 3%", "P_GONE", "מוצר שהוסר")
        self._cycle(_MissingSpecificAdapter())
        self.assertIsNotNone(self.storage.preferred_for("shufersal", "חלב 3%"))

    def test_answering_the_question_replaces_a_stale_choice(self):
        self.storage.remember_choice("shufersal", "חלב 3%", "P_GONE", "מוצר שהוסר")
        self.storage.remember_choice("shufersal", "חלב 3%", "P_NEW", "חלב אחר")
        self.assertEqual(
            self.storage.preferred_for("shufersal", "חלב 3%")["product_code"], "P_NEW"
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


class _ExpiredSessionAdapter(_AlwaysAddsAdapter):
    """An adapter whose session is dead and cannot be renewed."""

    def __init__(self):
        super().__init__()
        self.searched = False

    def ensure_session(self) -> bool:
        return False

    def search_and_add(self, term, quantity=1):
        self.searched = True
        return super().search_and_add(term, quantity)


class _RenewedSessionAdapter(_AlwaysAddsAdapter):
    def ensure_session(self) -> bool:
        return True


class SessionGateTests(unittest.TestCase):
    """A dead session must abort the cycle, not burn every item on it."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "test.sqlite3"))
        self.storage.add_base_list_item("חלב", default_quantity=1)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_unrenewable_session_reports_error_without_searching(self) -> None:
        adapter = _ExpiredSessionAdapter()
        reports = run_order_cycle(self.storage, {"shufersal": lambda: adapter})

        self.assertFalse(adapter.searched, "should not search with a dead session")
        self.assertEqual(len(reports["shufersal"].errors), 1)
        self.assertEqual(reports["shufersal"].added, [])

    def test_renewed_session_proceeds_normally(self) -> None:
        adapter = _RenewedSessionAdapter()
        reports = run_order_cycle(self.storage, {"shufersal": lambda: adapter})

        self.assertEqual(len(reports["shufersal"].added), 1)
        self.assertEqual(reports["shufersal"].errors, [])

    def test_adapter_without_ensure_session_still_works(self) -> None:
        """Older/simpler adapters (and the test fakes) have no such method."""
        adapter = _AlwaysAddsAdapter()
        reports = run_order_cycle(self.storage, {"shufersal": lambda: adapter})

        self.assertEqual(len(reports["shufersal"].added), 1)


class AdhocSurvivesAFailedCycleTests(unittest.TestCase):
    """A request the household was told was added must not evaporate.

    Reported from the phone: "a large part of the products you said you
    added to the list were not in it". The cycle marked a `not_found`
    ad-hoc request as consumed, so it vanished from both the list and the
    cart and was never retried.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "test.sqlite3"))
        self.addCleanup(self._tmpdir.cleanup)

    def _pending(self) -> list[str]:
        return [r.text for r in self.storage.list_pending_adhoc()]

    def test_not_found_request_stays_pending_for_the_next_cycle(self) -> None:
        self.storage.add_adhoc_request("קינואה", requested_by="לירן")
        run_order_cycle(
            self.storage, {"fake_store": lambda: FakeAdapter({"קינואה": "not_found"})}
        )
        self.assertEqual(self._pending(), ["קינואה"])

    def test_a_later_cycle_can_still_buy_it(self) -> None:
        self.storage.add_adhoc_request("קינואה", requested_by="לירן")
        run_order_cycle(
            self.storage, {"fake_store": lambda: FakeAdapter({"קינואה": "not_found"})}
        )
        fake = FakeAdapter({"קינואה": "added"})
        run_order_cycle(self.storage, {"fake_store": lambda: fake})
        self.assertIn(("קינואה", 1), fake.added_calls)
        self.assertEqual(self._pending(), [])

    def test_added_request_is_consumed(self) -> None:
        self.storage.add_adhoc_request("חלב", requested_by="ישי")
        run_order_cycle(
            self.storage, {"fake_store": lambda: FakeAdapter({"חלב": "added"})}
        )
        self.assertEqual(self._pending(), [])

    def test_ambiguous_request_is_consumed_because_a_question_was_asked(self) -> None:
        # The user is now holding the decision; re-asking every cycle would
        # be worse than letting the answer resolve it.
        self.storage.add_adhoc_request("טונה", requested_by="ישי")
        run_order_cycle(
            self.storage, {"fake_store": lambda: FakeAdapter({"טונה": "ambiguous"})}
        )
        self.assertEqual(self._pending(), [])

    def test_report_says_the_item_is_still_on_the_list(self) -> None:
        self.storage.add_adhoc_request("קינואה", requested_by="לירן")
        reports = run_order_cycle(
            self.storage, {"fake_store": lambda: FakeAdapter({"קינואה": "not_found"})}
        )
        self.assertIn("נשאר ברשימה", format_report_summary(reports))


class _ResolvesCleanlyAdapter(StoreAdapter):
    """Search resolves to exactly one product, the ordinary happy path."""

    name = "shufersal"

    def __init__(self, code="P_777", resolved="חלב 3% בקרטון תנובה"):
        self.code = code
        self.resolved = resolved
        self.search_calls = []
        self.specific_calls = []

    def is_session_valid(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def search_and_add(self, term, quantity=1):
        self.search_calls.append(term)
        return CartAddResult(
            item_name=self.resolved, store=self.name, status="added",
            product_code=self.code, quantity=quantity,
        )

    def add_specific_product(self, label, quantity=1, product_code="", search_term=""):
        self.specific_calls.append((label, product_code))
        return CartAddResult(
            item_name=label, store=self.name, status="added",
            product_code=product_code, quantity=quantity,
        )


class _EchoesTheTermAdapter(_ResolvesCleanlyAdapter):
    """An adapter that hands the search term back instead of a product."""

    def search_and_add(self, term, quantity=1):
        self.search_calls.append(term)
        return CartAddResult(item_name=term, store=self.name, status="added")


class CleanResolutionIsRememberedTests(unittest.TestCase):
    """A term that resolves to one product should not be searched again.

    Bulk-matches, auto-resolutions and answered questions were all
    remembered; a plain successful search was not, so a settled product
    went back through the store's search every cycle. At Shufersal that
    is a wasted page load. At Tiv Taam it is a correctness problem: its
    autocomplete returned 4, then 0, then 5 candidates for one query in a
    single afternoon, and a 0 is reported as "not found" for something
    the household buys weekly.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.storage = Storage(str(Path(self._dir.name) / "t.sqlite3"))
        self.storage.add_base_list_item(name="חלב 3%")

    def _cycle(self, adapter):
        return run_order_cycle(self.storage, {"shufersal": lambda: adapter})

    def test_a_clean_resolution_is_remembered(self):
        self._cycle(_ResolvesCleanlyAdapter())
        remembered = self.storage.preferred_for("shufersal", "חלב 3%")
        self.assertIsNotNone(remembered)
        self.assertEqual(remembered["product_code"], "P_777")

    def test_the_second_cycle_does_not_search_again(self):
        self._cycle(_ResolvesCleanlyAdapter())
        second = _ResolvesCleanlyAdapter()
        report = self._cycle(second)["shufersal"]
        self.assertEqual(len(report.added), 1)
        self.assertEqual(second.search_calls, [], "should have gone straight to the product")
        self.assertEqual(second.specific_calls[0][1], "P_777")

    def test_an_adapter_that_echoes_the_term_teaches_nothing(self):
        # Remembering that "חלב 3%" means a product called "חלב 3%" would
        # send the next cycle looking for a product by that exact name and
        # resolve to nothing — worse than not remembering at all.
        self._cycle(_EchoesTheTermAdapter())
        self.assertIsNone(self.storage.preferred_for("shufersal", "חלב 3%"))

    def test_a_resolved_name_without_a_code_is_still_worth_remembering(self):
        # Tiv Taam's adapter returns the resolved product name but no
        # code, and its add_specific_product matches on name — so the name
        # alone is a usable memory there.
        adapter = _ResolvesCleanlyAdapter(code="")
        self._cycle(adapter)
        remembered = self.storage.preferred_for("shufersal", "חלב 3%")
        self.assertIsNotNone(remembered)
        self.assertEqual(remembered["product_name"], "חלב 3% בקרטון תנובה")

    def test_a_decided_choice_is_remembered_so_it_is_decided_once(self):
        # Was: "an ambiguous result is still asked about", asserting the
        # memory stayed empty. Since 2026-09-17 the choice is made from
        # purchase history instead of asked, and remembering it is the
        # point — otherwise the same search is re-decided every cycle.
        self._cycle(_AlwaysAmbiguousAdapter())
        remembered = self.storage.preferred_for("shufersal", "חלב 3%")
        self.assertIsNotNone(remembered)
        self.assertTrue(remembered["product_name"])


class DealsInTheCartTests(unittest.TestCase):
    """A cycle may add an exceptional promotion nobody asked for.

    The rule it must never break: a saving is only claimed for a line
    that actually reached the cart.
    """

    def setUp(self):
        from grocery_bot.prices import PricedProduct, PromotionItem
        from grocery_bot.stock import StockItem

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.storage = Storage(str(Path(self._dir.name) / "t.sqlite3"))
        self.storage.add_base_list_item(name="חלב")
        self.storage.replace_stock_items(
            "shufersal",
            [StockItem("P_1", "מרכך כביסה", 0.05, "טיפוח, תינוקות וניקיון")],
        )
        self.storage.replace_catalog(
            [PricedProduct("1", "מרכך כביסה סנו", "", 20.0, 0, "1 ליטר", "", False)],
            [PromotionItem("p1", "מבצע", "1", 10.0, 1, 0,
                           "2000-01-01T00:00:00", "2099-01-01T00:00:00")],
        )

    def test_a_deep_deal_is_added_and_labelled_as_such(self):
        fake = FakeAdapter({"חלב": "added", "מרכך כביסה": "added"})
        report = run_order_cycle(self.storage, {"shufersal": lambda: fake})["shufersal"]
        dealt = [r for r in report.added if r.deal]
        self.assertEqual([r.item_name for r in dealt], ["מרכך כביסה"])
        self.assertIn("-50%", dealt[0].deal)

    def test_a_deal_that_did_not_reach_the_cart_claims_no_saving(self):
        # Otherwise the summary reports money saved on something that is
        # not in the cart — the "looks right, isn't" shape.
        fake = FakeAdapter({"חלב": "added", "מרכך כביסה": "not_found"})
        report = run_order_cycle(self.storage, {"shufersal": lambda: fake})["shufersal"]
        self.assertEqual([r for r in report.added if r.deal], [])
        self.assertEqual([r.item_name for r in report.not_found], ["מרכך כביסה"])

    def test_it_can_be_turned_off(self):
        fake = FakeAdapter({"חלב": "added", "מרכך כביסה": "added"})
        report = run_order_cycle(
            self.storage, {"shufersal": lambda: fake}, add_deals=False
        )["shufersal"]
        self.assertEqual([r.item_name for r in report.added], ["חלב"])

    def test_the_summary_separates_them_from_what_was_asked_for(self):
        fake = FakeAdapter({"חלב": "added", "מרכך כביסה": "added"})
        reports = run_order_cycle(self.storage, {"shufersal": lambda: fake})
        summary = format_report_summary(reports)
        self.assertIn("נוספו בגלל מבצע חריג", summary)
        # The asked-for line must not silently absorb the deal item.
        asked_line = next(l for l in summary.splitlines() if l.startswith("✅"))
        self.assertIn("חלב", asked_line)
        self.assertNotIn("מרכך כביסה", asked_line)
