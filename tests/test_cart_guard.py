"""Do not undo what a person just did to the cart.

The cart is shared: Ishay fills it through the bot, his partner edits it
on the site with no Telegram in the loop. The bot only ever read the cart
*after* filling it, so a line she deleted came straight back on the next
automatic fill, and an item already there could be added twice.

This is not learning from removals — nothing here changes a preference, a
quantity or a list, which Ishay ruled out on 2026-09-07. It declines to
undo one edit, within one round.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import standingcart
from grocery_bot.models import CartAddResult, OrderCycleReport
from grocery_bot.orchestrator import CartGuard, add_terms_to_cart
from grocery_bot.storage import Storage


class _Adapter:
    """A store whose cart holds whatever the test says it holds."""

    name = "shufersal"

    def __init__(self, cart_items, ok=True):
        self._cart = {"ok": ok, "items": list(cart_items), "total": 0, "url": ""}
        self.added = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ensure_session(self):
        return True

    def cart_summary(self):
        return self._cart

    def search_and_add(self, term, quantity=1):
        self.added.append(term)
        return CartAddResult(item_name=term, store="shufersal", status="added",
                             quantity=quantity, product_code="P_new")


class _BrokenCartAdapter(_Adapter):
    def cart_summary(self):
        raise RuntimeError("cart page timed out")


def _manifest(storage, store, rows):
    report = OrderCycleReport(store=store)
    for code, name in rows:
        report.record(CartAddResult(item_name=name, store=store, status="added",
                                    product_code=code))
    standingcart.record_manifest(storage, {store: report})


class CartGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def _run(self, adapter, terms, guard_cart=True):
        return add_terms_to_cart(
            self.storage, {"shufersal": lambda: adapter}, terms,
            None, guard_cart,
        )["shufersal"]

    def test_a_line_the_household_deleted_is_not_put_back(self) -> None:
        _manifest(self.storage, "shufersal",
                  [("P_1", "חלב 3%"), ("P_2", "לחם אחיד")])
        # Bread survived, milk was taken out by a person.
        adapter = _Adapter([{"code": "P_2", "name": "לחם אחיד", "qty": "1"}])
        report = self._run(adapter, [("חלב 3%", 1)])
        self.assertEqual([r.item_name for r in report.skipped], ["חלב 3%"])
        self.assertIn("הוסר", report.skipped[0].detail)
        self.assertEqual(adapter.added, [])

    def test_something_already_in_the_cart_is_not_added_twice(self) -> None:
        adapter = _Adapter([{"code": "P_2", "name": "לחם אחיד", "qty": "1"}])
        report = self._run(adapter, [("לחם אחיד", 1)])
        self.assertEqual([r.item_name for r in report.skipped], ["לחם אחיד"])
        self.assertIn("כבר בעגלה", report.skipped[0].detail)

    def test_an_empty_cart_blocks_nothing(self) -> None:
        """After a shop the chain empties the cart. Every manifest line
        then reads as "removed" — and refusing to refill would leave the
        household with an empty cart and no explanation."""
        _manifest(self.storage, "shufersal",
                  [("P_1", "חלב 3%"), ("P_2", "לחם אחיד")])
        adapter = _Adapter([])
        report = self._run(adapter, [("חלב 3%", 1), ("לחם אחיד", 1)])
        self.assertEqual(report.skipped, [])
        self.assertEqual(len(report.added), 2)

    def test_an_unreadable_cart_blocks_nothing(self) -> None:
        # A page-load failure must not quietly stop a shop being filled.
        _manifest(self.storage, "shufersal", [("P_1", "חלב 3%")])
        adapter = _BrokenCartAdapter([])
        report = self._run(adapter, [("חלב 3%", 1)])
        self.assertEqual(report.skipped, [])
        self.assertEqual(len(report.added), 1)

    def test_an_explicit_request_is_never_refused(self) -> None:
        """"תוסיף חלב" means milk now. If it was deleted an hour ago and
        is being asked for again, that is a change of mind."""
        _manifest(self.storage, "shufersal",
                  [("P_1", "חלב 3%"), ("P_2", "לחם אחיד")])
        adapter = _Adapter([{"code": "P_2", "name": "לחם אחיד", "qty": "1"}])
        report = self._run(adapter, [("חלב 3%", 1)], guard_cart=False)
        self.assertEqual(report.skipped, [])
        self.assertEqual(adapter.added, ["חלב 3%"])

    def test_a_skipped_line_is_not_a_failure(self) -> None:
        # not_found would read as "the store does not stock it" and would
        # feed the repeat-failure report with things that never failed.
        _manifest(self.storage, "shufersal", [("P_1", "חלב 3%")])
        adapter = _Adapter([{"code": "P_9", "name": "משהו אחר", "qty": "1"}])
        report = self._run(adapter, [("חלב 3%", 1)])
        self.assertEqual(report.not_found, [])
        self.assertEqual(report.errors, [])
        self.assertEqual(len(report.skipped), 1)
        # And it stays out of the cart views, which render what happened.
        self.assertNotIn("חלב 3%", [r.item_name for r in report.results])


class EmptyCartIsNotRemovalsTests(unittest.TestCase):
    """`/done` runs right after a shop, and a chain empties the cart when
    an order is placed. Caught 2026-09-11 before it ever ran: the live
    manifest held 120 Shufersal lines, and the next `/done` would have
    logged all 120 as things the household deleted."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        _manifest(self.storage, "shufersal",
                  [("P_1", "חלב 3%"), ("P_2", "לחם אחיד"), ("P_3", "ביצים")])

    def test_an_empty_cart_reports_no_removals(self) -> None:
        self.assertEqual(standingcart.removals(self.storage, "shufersal", []), [])

    def test_a_cart_with_something_left_still_reports_removals(self) -> None:
        gone = standingcart.removals(
            self.storage, "shufersal",
            [{"code": "P_2", "name": "לחם אחיד"}],
        )
        self.assertEqual(sorted(r["name"] for r in gone), ["ביצים", "חלב 3%"])


if __name__ == "__main__":
    unittest.main()
