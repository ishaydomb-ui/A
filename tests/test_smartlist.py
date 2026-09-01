import tempfile
import unittest
from datetime import date
from pathlib import Path

from grocery_bot import shelflife, smartlist
from grocery_bot.storage import Storage


def _entry(name, orders, freq=None, family="", pid=1):
    product = {
        "id": pid,
        "names": {"1": {"short": name}},
        "family": {"names": {"1": {"name": family}}},
    }
    return {
        "product": product,
        "ordersNumber": orders,
        "purchaseFrequencyDays": freq,
        "retailerProductId": pid,
    }


class ConversionTest(unittest.TestCase):
    def test_measured_interval_is_carried_through(self):
        items = smartlist.to_stock_items({"items": [_entry("חלב", 18, 42)]})
        self.assertEqual(items[0].interval_days, 42.0)

    def test_share_is_relative_to_the_busiest_product(self):
        # The payload gives no order total, so share is relative — which is
        # what the tier thresholds need, and it can never claim a product
        # appeared in more orders than exist.
        items = smartlist.to_stock_items(
            {"items": [_entry("א", 20, pid=1), _entry("ב", 10, pid=2)]}
        )
        self.assertEqual([round(i.share, 2) for i in items], [1.0, 0.5])
        self.assertLessEqual(max(i.share for i in items), 1.0)

    def test_missing_frequency_leaves_the_interval_unknown(self):
        items = smartlist.to_stock_items({"items": [_entry("חלב", 5)]})
        self.assertIsNone(items[0].interval_days)

    def test_entries_without_a_name_are_skipped(self):
        payload = {"items": [{"product": {"id": 7}, "ordersNumber": 3}]}
        self.assertEqual(smartlist.to_stock_items(payload), [])

    def test_empty_payload_is_safe(self):
        self.assertEqual(smartlist.to_stock_items({}), [])
        self.assertEqual(smartlist.to_stock_items({"items": []}), [])

    def test_department_inferred_from_the_family_name(self):
        items = smartlist.to_stock_items(
            {"items": [_entry("קוטג'", 5, family="גבינות לבנות")]}
        )
        self.assertEqual(items[0].department, "מוצרי חלב וקירור")

    def test_unknown_family_falls_back_rather_than_guessing(self):
        items = smartlist.to_stock_items({"items": [_entry("משהו", 5, family="זזז")]})
        self.assertEqual(items[0].department, "שונות")


class IntervalPreferenceTest(unittest.TestCase):
    """A measured interval must beat the share-based estimate."""

    def _item(self, share, measured):
        return shelflife.ShelfItem(
            product_code="P", name="x", department="מזווה ושימורים",
            share=share, last_purchased=date(2026, 9, 1),
            measured_interval_days=measured,
        )

    def test_measured_wins(self):
        # 1/0.5 * 8 = 16 days estimated; the chain says 42.
        self.assertEqual(self._item(0.5, 42).expected_interval_days, 42.0)

    def test_estimate_used_when_nothing_was_measured(self):
        self.assertEqual(self._item(0.5, None).expected_interval_days, 16.0)

    def test_measured_interval_rescues_a_share_too_low_to_estimate_from(self):
        # A share this low normally yields no interval at all; a measured
        # figure is real data and should still be used.
        self.assertEqual(self._item(0.01, 30).expected_interval_days, 30.0)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    class _Api:
        def __init__(self, payload):
            self._payload = payload

        def smart_list(self):
            return self._payload

    def test_sync_writes_tivtaam_rows_with_intervals(self):
        api = self._Api({"items": [_entry("חלב", 18, 42), _entry("קוטג'", 9, 30, pid=2)]})
        self.assertEqual(smartlist.sync(api, self.storage), 2)
        rows = {r["product_name"]: r for r in self.storage.list_stock_items("tivtaam")}
        self.assertEqual(rows["חלב"]["interval_days"], 42.0)

    def test_sync_does_not_touch_the_other_store(self):
        self.storage.replace_stock_items(
            "shufersal",
            smartlist.to_stock_items({"items": [_entry("שופרסל פריט", 5)]}),
        )
        smartlist.sync(self._Api({"items": [_entry("חלב", 18, 42)]}), self.storage)
        self.assertEqual(len(self.storage.list_stock_items("shufersal")), 1)

    def test_empty_smart_list_leaves_existing_rows_alone(self):
        smartlist.sync(self._Api({"items": [_entry("חלב", 18, 42)]}), self.storage)
        self.assertEqual(smartlist.sync(self._Api({"items": []}), self.storage), 0)
        self.assertEqual(len(self.storage.list_stock_items("tivtaam")), 1)


if __name__ == "__main__":
    unittest.main()
