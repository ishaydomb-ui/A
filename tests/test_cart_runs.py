"""Durable shopping runs — Phase 1 of the reliability build (2026-09-17).

Each test names the defect it prevents. Before this phase no run had an
identity, three of five fill sources discarded their originating id
before the fill, and "was this request bought" was answered by comparing
request text to the added product's name — which left 17 of 21 items
pending after they had landed in the cart.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import standingcart
from grocery_bot.models import PlanTerm
from grocery_bot.orchestrator import add_terms_to_cart, run_order_cycle
from grocery_bot.stock import StockItem
from grocery_bot.storage import Storage, normalize_term


class FakeAdapter:
    """Answers each term with a scripted status; never touches a browser."""

    name = "fake"

    def __init__(self, outcomes: dict, default: str = "added"):
        self.outcomes = outcomes
        self.default = default
        self.calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ensure_session(self):
        return True

    def search_and_add(self, term, quantity=1):
        from grocery_bot.models import CartAddResult

        self.calls.append((term, quantity))
        status = self.outcomes.get(term, self.default)
        detail = {
            "error": "Page.goto: net::ERR_SOCKS_CONNECTION_FAILED",
            "not_found": "no add control on the row",
        }.get(status, "")
        return CartAddResult(
            item_name=term, store=self.name, status=status, quantity=quantity,
            detail=detail, candidates=["א", "ב"] if status == "ambiguous" else [],
            product_code=f"P_{term}" if status == "added" else "",
            # A fake that says "added" must also say it saw it, or Phase 2
            # correctly records the add as unverified.
            verification="verified" if status == "added" else "n/a",
        )

    def add_specific_product(self, name, quantity=1, **kw):
        return self.search_and_add(name, quantity)


class SourceIdentitySurvivesPlanningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_stock_terms_carry_their_product_code(self):
        # One of the three places the id used to be dropped.
        self.storage.replace_stock_items("tivtaam", [
            StockItem("10297185", "שמן חמניות", 0.5, "שונות"),
        ])
        self.storage.record_last_purchase("tivtaam", [("10297185", "2026-09-01")])
        plan = standingcart.plan_refill(self.storage, "tivtaam")
        stock = [t for t in plan.terms if t.source_kind == "stock"]
        self.assertTrue(stock)
        self.assertEqual(stock[0].source_id, "10297185")

    def test_base_terms_carry_their_row_id(self):
        rid = self.storage.add_base_list_item("קוטג", default_quantity=2)
        plan = standingcart.plan_refill(self.storage, "tivtaam")
        self.assertEqual(plan.terms[0].source_kind, "base")
        self.assertEqual(plan.terms[0].source_id, str(rid))

    def test_a_legacy_tuple_still_works_as_a_freeform_need(self):
        # The CLI and older callers pass (term, qty); nothing breaks.
        pt = PlanTerm.coerce(("חלב", 2))
        self.assertEqual((pt.term, pt.quantity, pt.source_kind, pt.source_id),
                         ("חלב", 2, "freeform", None))


class RunLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_a_fill_creates_one_run_and_one_item_per_need(self):
        fake = FakeAdapter({})
        terms = [PlanTerm("חלב", 1, "adhoc", "7"), PlanTerm("לחם", 1, "adhoc", "8"), ("ביצים", 1)]
        add_terms_to_cart(self.storage, {"fake": lambda: fake}, terms)
        runs = self.storage.running_cart_runs()
        self.assertEqual(runs, [])  # finished, not left running
        # Exactly one run exists and it has three items.
        import sqlite3
        c = sqlite3.connect(str(Path(self._tmp.name) / "t.sqlite3"))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM cart_runs").fetchone()[0], 1)
        run_id = c.execute("SELECT id FROM cart_runs").fetchone()[0]
        self.assertEqual(self.storage.run_counts(run_id)["requested"], 3)

    def test_the_requested_denominator_is_stored(self):
        # "17 of 20" needs the 20. It was never stored before.
        fake = FakeAdapter({"לחם": "not_found"})
        run_id = self.storage.start_cart_run("manual")
        add_terms_to_cart(self.storage, {"fake": lambda: fake},
                          [("חלב", 1), ("לחם", 1)], run_id=run_id)
        counts = self.storage.run_counts(run_id)
        self.assertEqual(counts["requested"], 2)
        self.assertEqual(counts.get("verified"), 1)
        self.assertEqual(counts.get("failed_product"), 1)

    def test_the_run_ends_in_a_terminal_status(self):
        fake = FakeAdapter({})
        run_id = self.storage.start_cart_run("manual")
        add_terms_to_cart(self.storage, {"fake": lambda: fake}, [("חלב", 1)], run_id=run_id)
        # The caller owns the run here, so it is still open until it says.
        self.assertEqual(len(self.storage.running_cart_runs()), 1)
        self.storage.finish_cart_run(run_id, "completed")
        self.assertEqual(self.storage.running_cart_runs(), [])

    def test_a_run_the_function_owns_is_closed_by_it(self):
        fake = FakeAdapter({})
        add_terms_to_cart(self.storage, {"fake": lambda: fake}, [("חלב", 1)])
        self.assertEqual(self.storage.running_cart_runs(), [])

    def test_the_trigger_is_the_origin(self):
        run_id = self.storage.start_cart_run("watch_list")
        self.storage.mark_cart_run(run_id, "interrupted")
        run = self.storage.running_cart_runs()[0]
        self.assertEqual(run["trigger"], "watch_list")
        self.assertEqual(run["status"], "interrupted")


class UniquenessIsHouseholdIntentTests(unittest.TestCase):
    """Store is a resolution property, never part of the item's identity."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_the_same_need_at_two_stores_is_one_run_item(self):
        run_id = self.storage.start_cart_run("manual")
        fakes = {"tivtaam": FakeAdapter({}), "shufersal": FakeAdapter({})}
        add_terms_to_cart(
            self.storage, {k: (lambda v=v: v) for k, v in fakes.items()},
            [PlanTerm("חלב", 1, "adhoc", "7")], run_id=run_id,
        )
        self.assertEqual(self.storage.run_counts(run_id)["requested"], 1)

    def test_registering_the_same_need_twice_returns_the_same_row(self):
        run_id = self.storage.start_cart_run("manual")
        first = self.storage.add_run_items(run_id, [PlanTerm("חלב", 1, "adhoc", "7")])
        second = self.storage.add_run_items(run_id, [PlanTerm("חלב", 1, "adhoc", "7")])
        self.assertEqual(first, second)
        self.assertEqual(self.storage.run_counts(run_id)["requested"], 1)

    def test_freeform_identity_is_the_normalised_term(self):
        # Documented normalisation: fold quotes/apostrophes, collapse
        # whitespace. "קוטג'" and "קוטג׳  " are one need.
        run_id = self.storage.start_cart_run("manual")
        self.storage.add_run_items(run_id, [PlanTerm("קוטג'", 1), PlanTerm("קוטג׳  ", 1)])
        self.assertEqual(self.storage.run_counts(run_id)["requested"], 1)
        self.assertEqual(normalize_term("קוטג'"), normalize_term("קוטג׳  "))

    def test_different_needs_are_different_items(self):
        run_id = self.storage.start_cart_run("manual")
        self.storage.add_run_items(run_id, [PlanTerm("חלב", 1), PlanTerm("לחם", 1)])
        self.assertEqual(self.storage.run_counts(run_id)["requested"], 2)


class ConsumptionIsReadFromTheRunTests(unittest.TestCase):
    """Never again by comparing request text to product name."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_an_added_item_is_readable_by_its_source_id(self):
        rid = self.storage.add_adhoc_request("חלב עמיד", "ishay")
        fake = FakeAdapter({})
        # The product lands under a different name — the case that broke.
        fake.search_and_add = lambda term, quantity=1: __import__(
            "grocery_bot.models", fromlist=["CartAddResult"]
        ).CartAddResult(item_name="חלב עמיד 3% 1 ליטר", store="fake", status="added",
                        quantity=quantity, product_code="P_1", verification="verified")
        run_id = self.storage.start_cart_run("watch_list")
        add_terms_to_cart(self.storage, {"fake": lambda: fake},
                          [PlanTerm("חלב עמיד", 1, "adhoc", str(rid))], run_id=run_id)
        outcomes = self.storage.run_outcomes(run_id)
        self.assertEqual(outcomes[("adhoc", str(rid))], "verified")

    def test_a_failed_item_is_not_consumed(self):
        rid = self.storage.add_adhoc_request("נקטרינה", "ishay")
        fake = FakeAdapter({"נקטרינה": "not_found"})
        run_id = self.storage.start_cart_run("watch_list")
        add_terms_to_cart(self.storage, {"fake": lambda: fake},
                          [PlanTerm("נקטרינה", 1, "adhoc", str(rid))], run_id=run_id)
        self.assertEqual(self.storage.run_outcomes(run_id)[("adhoc", str(rid))],
                         "failed_product")


class FailureClassificationOnTheItemTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_a_socks_failure_is_failed_infra_not_failed_product(self):
        fake = FakeAdapter({"חלב": "error"})
        run_id = self.storage.start_cart_run("manual")
        add_terms_to_cart(self.storage, {"fake": lambda: fake}, [("חלב", 1)], run_id=run_id)
        item = self.storage.run_items_for(run_id)[0]
        self.assertEqual(item["outcome"], "failed_infra")
        self.assertEqual(item["failure_kind"], "infrastructure")

    def test_an_ambiguous_result_is_unresolved_not_failed(self):
        fake = FakeAdapter({"טונה": "ambiguous"})
        run_id = self.storage.start_cart_run("manual")
        add_terms_to_cart(self.storage, {"fake": lambda: fake}, [("טונה", 1)], run_id=run_id)
        item = self.storage.run_items_for(run_id)[0]
        # The resolver may settle it from candidates; either way it is
        # never recorded as a product failure.
        self.assertIn(item["outcome"], ("unresolved_ambiguity", "verified", "unverified"))

    def test_unknown_columns_are_refused_not_ignored(self):
        run_id = self.storage.start_cart_run("manual")
        ids = self.storage.add_run_items(run_id, [PlanTerm("חלב", 1)])
        with self.assertRaises(ValueError):
            self.storage.update_run_item(next(iter(ids.values())), outcom="added")


class CycleRegistersEverySourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_base_and_adhoc_items_become_run_items_with_ids(self):
        bid = self.storage.add_base_list_item("חלב")
        aid = self.storage.add_adhoc_request("טחינה", "ליאן")
        fake = FakeAdapter({})
        run_order_cycle(self.storage, {"fake": lambda: fake}, add_deals=False)
        import sqlite3
        c = sqlite3.connect(str(Path(self._tmp.name) / "t.sqlite3"))
        run_id = c.execute("SELECT id FROM cart_runs WHERE trigger='cycle'").fetchone()[0]
        outcomes = self.storage.run_outcomes(run_id)
        self.assertIn(("base", str(bid)), outcomes)
        self.assertIn(("adhoc", str(aid)), outcomes)


if __name__ == "__main__":
    unittest.main()
