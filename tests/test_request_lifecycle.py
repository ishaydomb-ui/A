"""A request has a life after it leaves the queue.

`consumed` is one bit. It said the same thing about a request sitting in
a cart, one waiting on an unanswered choice, one in an order placed
yesterday and one delivered last week — so "did the tahini actually
arrive?" had no answer at all.

The user's report stays the trigger for a completed shop (his decision,
2026-09-07). The chain's own history arrives about 36 hours later and
only corroborates it.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot.storage import Storage


class RequestLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self.tahini = self.storage.add_adhoc_request("טחינה גולמית", "ishay")
        self.cottage = self.storage.add_adhoc_request("קוטג", "ishay")

    def test_in_cart_and_awaiting_are_different_answers(self) -> None:
        self.storage.set_adhoc_status(self.tahini, "in_cart", "shufersal")
        self.storage.set_adhoc_status(self.cottage, "awaiting", "shufersal")
        self.assertEqual(
            [r["text"] for r in self.storage.adhoc_by_status("in_cart")],
            ["טחינה גולמית"],
        )
        self.assertEqual(
            [r["text"] for r in self.storage.adhoc_by_status("awaiting")], ["קוטג"]
        )

    def test_the_household_reporting_a_shop_moves_what_was_in_the_cart(self) -> None:
        self.storage.set_adhoc_status(self.tahini, "in_cart", "shufersal")
        self.storage.set_adhoc_status(self.cottage, "awaiting", "shufersal")
        moved = self.storage.advance_adhoc_status("in_cart", "shopped")
        self.assertEqual(moved, 1)
        # The unanswered choice is untouched: nobody bought it.
        self.assertEqual(
            [r["text"] for r in self.storage.adhoc_by_status("awaiting")], ["קוטג"]
        )

    def test_the_order_history_confirms_late_and_only_confirms(self) -> None:
        # Measured gap between paying and the order appearing in the
        # chain's own history: about 36 hours.
        self.storage.set_adhoc_status(self.tahini, "in_cart", "shufersal")
        self.storage.advance_adhoc_status("in_cart", "shopped")
        self.assertEqual(self.storage.adhoc_by_status("confirmed"), [])
        self.storage.log_orders(
            [{"code": "05053629", "placed_at": "2026-09-07T08:57:00",
              "total": 670.89, "item_count": 40}],
            store="shufersal",
        )
        self.assertEqual(
            [r["text"] for r in self.storage.adhoc_by_status("confirmed")],
            ["טחינה גולמית"],
        )

    def test_an_order_at_one_chain_does_not_confirm_the_other(self) -> None:
        self.storage.set_adhoc_status(self.tahini, "in_cart", "tivtaam")
        self.storage.advance_adhoc_status("in_cart", "shopped")
        self.storage.log_orders(
            [{"code": "1", "placed_at": "2026-09-07T08:57:00"}], store="shufersal"
        )
        self.assertEqual(self.storage.adhoc_by_status("confirmed"), [])
        self.assertEqual(len(self.storage.adhoc_by_status("shopped")), 1)

    def test_a_repeated_order_row_confirms_nothing_twice(self) -> None:
        # log_orders is idempotent; a re-sync must not move fresh requests.
        order = [{"code": "1", "placed_at": "2026-09-07T08:57:00"}]
        self.storage.log_orders(order, store="shufersal")
        self.storage.set_adhoc_status(self.tahini, "in_cart", "shufersal")
        self.storage.advance_adhoc_status("in_cart", "shopped")
        self.storage.log_orders(order, store="shufersal")
        self.assertEqual(len(self.storage.adhoc_by_status("shopped")), 1)
        self.assertEqual(self.storage.adhoc_by_status("confirmed"), [])

    def test_an_unknown_state_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.storage.set_adhoc_status(self.tahini, "probably_fine")

    def test_the_old_flag_still_works(self) -> None:
        # Rows predating the lifecycle carry no status and must still read
        # correctly through `consumed`.
        self.assertEqual(len(self.storage.list_pending_adhoc()), 2)
        self.storage.mark_adhoc_consumed(self.tahini)
        self.assertEqual(
            [r.text for r in self.storage.list_pending_adhoc()], ["קוטג"]
        )


if __name__ == "__main__":
    unittest.main()
