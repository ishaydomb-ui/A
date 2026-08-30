import datetime
import tempfile
import unittest
from pathlib import Path

from grocery_bot.learn import (
    DEFAULT_GAP_DAYS,
    days_since_last_order,
    digest_due,
    typical_gap_days,
)
from grocery_bot.storage import Storage


def _iso(days_ago: float) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


class CadenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _orders(self, *days_ago):
        self.storage.log_orders(
            [{"code": f"o{i}", "placed_at": _iso(d)} for i, d in enumerate(days_ago)]
        )

    def test_no_history_gives_the_default_gap(self) -> None:
        self.assertEqual(typical_gap_days(self.storage), DEFAULT_GAP_DAYS)

    def test_the_stated_rhythm_beats_the_learned_one(self) -> None:
        """One chain's order log overstates the real fridge cycle when the
        household splits across two chains — believe what they told us."""
        self._orders(80, 60, 40, 20, 0)  # learned: ~20 days
        self.storage.set_state("target_gap_days", "9")
        self.assertEqual(typical_gap_days(self.storage), 9.0)

    def test_gap_is_learned_from_order_spacing(self) -> None:
        self._orders(28, 21, 14, 7, 0)
        self.assertEqual(typical_gap_days(self.storage), 7.0)

    def test_days_since_last_order(self) -> None:
        self._orders(10, 3)
        self.assertAlmostEqual(days_since_last_order(self.storage), 3, delta=0.1)

    def test_not_due_before_the_gap(self) -> None:
        self.storage.set_state("target_gap_days", "9")
        self._orders(20, 10, 4)
        due, _ = digest_due(self.storage)
        self.assertFalse(due)

    def test_due_after_the_gap(self) -> None:
        self.storage.set_state("target_gap_days", "9")
        self._orders(30, 20, 10)
        due, reason = digest_due(self.storage)
        self.assertTrue(due, reason)

    def test_a_sent_digest_is_not_repeated_immediately(self) -> None:
        """A reminder, not a nag."""
        self.storage.set_state("target_gap_days", "9")
        self._orders(30, 20, 10)
        self.storage.set_state("last_digest_sent", _iso(1))
        due, _ = digest_due(self.storage)
        self.assertFalse(due)

    def test_the_reminder_returns_after_three_days(self) -> None:
        self.storage.set_state("target_gap_days", "9")
        self._orders(30, 20, 12)
        self.storage.set_state("last_digest_sent", _iso(4))
        due, _ = digest_due(self.storage)
        self.assertTrue(due)

    def test_logging_the_same_order_twice_counts_once(self) -> None:
        self.storage.log_orders([{"code": "x", "placed_at": _iso(1)}])
        added = self.storage.log_orders([{"code": "x", "placed_at": _iso(1)}])
        self.assertEqual(added, 0)
        self.assertEqual(len(self.storage.order_dates()), 1)


class PriceHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_snapshot_records_one_row_per_product(self) -> None:
        from grocery_bot.prices import PricedProduct

        self.storage.replace_catalog(
            [PricedProduct("1", "חלב", "", 7.0, 0, "", "", False)], []
        )
        self.assertEqual(self.storage.record_price_snapshot(), 1)
        # Same-day rerun overwrites rather than duplicating.
        self.assertEqual(self.storage.record_price_snapshot(), 1)
        stats = self.storage.price_stats("1")
        self.assertEqual(stats["days"], 1)
        self.assertEqual(stats["best"], 7.0)

    def test_no_history_gives_none(self) -> None:
        self.assertIsNone(self.storage.price_stats("nope"))


if __name__ == "__main__":
    unittest.main()
