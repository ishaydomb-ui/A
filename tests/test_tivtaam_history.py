"""Reading Tiv Taam's own order history — the chain that was invisible.

Every fixture here is a shape taken from the real order of 2026-09-12
(id 17655403, ₪382.29, 25 items), not one invented to make the code look
right. The substitution, the weighed lines, the newline inside a product
name and the delivery fee are all from that one order.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import tivtaamhistory as th
from grocery_bot.storage import Storage


def _line(**kw):
    base = {
        "productId": 16323094, "barcode": "693493231749",
        "name": "חסה לאליק הידרופונית", "quantity": 1, "actualQuantity": 1,
        "isWeightable": False, "price": 14.9, "totalPrice": 14.9,
        "status": 2, "substituteId": None,
    }
    base.update(kw)
    return base


ORDER = {
    "id": 17655403,
    "timePlaced": "2026-09-12T13:48:25.933Z",
    "totalAmount": 382.29,
    "itemsCount": 25,
    "totalDiscount": 70.65,
    # Everything below is what must never reach the database.
    "city": "תל אביב-יפו", "street": "אייזק שטרן", "houseNumber": 1,
    "floor": 7, "apartment": "35", "zipCode": "6323702",
    "addressText2": "קוד כניסה לבנין - 0591 ואז כוכבית",
    "phoneNumber": "0545882887", "email": "ishaydomb@gmail.com",
    "deliveryDriverFirstName": "נור", "collectorFName": "סונדוס",
    "lines": [
        _line(),
        _line(productId=1, name="טבעפרוסט תרד 800 גרם\n", totalPrice=10.45),
        _line(productId=2, name="בננות", quantity=0.5, actualQuantity=0.332,
              isWeightable=True, totalPrice=4.28),
        # The substitution, both halves.
        _line(productId=3, name="נייר טואלט לח נשטף", quantity=0,
              actualQuantity=2, totalPrice=29.9, substituteId=1187473746),
        _line(productId=4, name="נייר טואלט לח נשטף", quantity=2,
              actualQuantity=0, totalPrice=0.0, status=5),
        _line(productId=5, name="משלוח אינטרנט", totalPrice=29.9),
    ],
}


class SummaryTests(unittest.TestCase):
    def test_only_the_five_whitelisted_fields_survive(self):
        # The privacy boundary, asserted rather than described. The
        # payload carries the household's address, entrance code, phone,
        # email and the delivery driver's name.
        summary = th.summarise_order(ORDER)
        self.assertEqual(
            set(summary),
            {"code", "placed_at", "total", "item_count", "discount"},
        )
        blob = repr(summary)
        for leaked in ("אייזק שטרן", "0545882887", "ishaydomb", "0591", "נור"):
            self.assertNotIn(leaked, blob, leaked)

    def test_the_real_order_reads_correctly(self):
        summary = th.summarise_order(ORDER)
        self.assertEqual(summary["code"], "17655403")
        self.assertEqual(summary["total"], 382.29)
        self.assertEqual(summary["item_count"], 25)

    def test_utc_is_converted_to_israel_local_like_shufersal(self):
        # order_log mixes both chains in one MAX(placed_at). 13:48Z is
        # 16:48 in Israel; leaving it in UTC puts late-evening orders on
        # the previous calendar day.
        self.assertEqual(
            th.summarise_order(ORDER)["placed_at"], "2026-09-12T16:48:25"
        )

    def test_a_late_evening_order_keeps_its_own_date(self):
        order = dict(ORDER, timePlaced="2026-09-12T22:30:00.000Z")
        self.assertEqual(
            th.summarise_order(order)["placed_at"][:10], "2026-09-13"
        )

    def test_an_unparseable_date_is_kept_not_invented(self):
        order = dict(ORDER, timePlaced="not a date")
        self.assertEqual(th.summarise_order(order)["placed_at"], "not a date")


class LineTests(unittest.TestCase):
    def setUp(self):
        self.lines = th.order_lines(ORDER)
        self.names = [line["name"] for line in self.lines]

    def test_the_delivery_fee_is_not_a_product(self):
        # Otherwise it is the household's most-bought item, every order.
        self.assertNotIn("משלוח אינטרנט", self.names)

    def test_a_newline_inside_a_product_name_is_stripped(self):
        self.assertIn("טבעפרוסט תרד 800 גרם", self.names)
        self.assertFalse(any("\n" in name for name in self.names))

    def test_a_substitution_counts_once_and_as_what_arrived(self):
        toilet = [ln for ln in self.lines if ln["name"] == "נייר טואלט לח נשטף"]
        self.assertEqual(len(toilet), 1)
        self.assertEqual(toilet[0]["actual_quantity"], 2)
        self.assertTrue(toilet[0]["substituted"])

    def test_the_undelivered_half_is_not_recorded_as_bought(self):
        self.assertFalse(any(ln["total"] == 0.0 for ln in self.lines))

    def test_a_weighed_line_keeps_kilograms_and_what_was_actually_picked(self):
        # Shufersal sends grams for the same idea. Converting here would
        # bury the difference where nobody looks for it.
        banana = next(ln for ln in self.lines if ln["name"] == "בננות")
        self.assertTrue(banana["weighable"])
        self.assertEqual(banana["quantity"], 0.5)
        self.assertEqual(banana["actual_quantity"], 0.332)

    def test_an_order_with_no_lines_is_not_an_error(self):
        self.assertEqual(th.order_lines({"id": 1}), [])


class _FakeApi:
    def __init__(self, orders, detail=None):
        self._orders = orders
        self._detail = detail
        self.calls = []

    def orders(self, size=50, start=0):
        self.calls.append(("orders", size))
        return {"orders": self._orders, "total": len(self._orders)}

    def order(self, order_id):
        self.calls.append(("order", order_id))
        return self._detail


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def test_orders_land_under_the_right_chain(self):
        # The whole point. Before this, order_log held only Shufersal and
        # the nudge counted days from a chain the household had not used.
        result = th.sync(self.storage, _FakeApi([ORDER]))
        self.assertEqual(result["added"], 1)
        self.assertEqual(self.storage.order_dates("tivtaam"), ["2026-09-12T16:48:25"])
        self.assertEqual(self.storage.order_dates("shufersal"), [])

    def test_syncing_twice_adds_nothing(self):
        api = _FakeApi([ORDER])
        th.sync(self.storage, api)
        self.assertEqual(th.sync(self.storage, api)["added"], 0)

    def test_a_synced_order_confirms_a_shop_the_household_reported(self):
        # The step that could never fire for this chain.
        request = self.storage.add_adhoc_request("טחינה גולמית", "ishay")
        self.storage.set_adhoc_status(request, "in_cart", "tivtaam")
        self.storage.advance_adhoc_status("in_cart", "shopped", "tivtaam")
        th.sync(self.storage, _FakeApi([ORDER]))
        self.assertEqual(
            [r["text"] for r in self.storage.adhoc_by_status("confirmed")],
            ["טחינה גולמית"],
        )

    def test_an_order_at_this_chain_does_not_confirm_the_other(self):
        request = self.storage.add_adhoc_request("קוטג", "ishay")
        self.storage.set_adhoc_status(request, "in_cart", "shufersal")
        self.storage.advance_adhoc_status("in_cart", "shopped", "shufersal")
        th.sync(self.storage, _FakeApi([ORDER]))
        self.assertEqual(
            [r["text"] for r in self.storage.adhoc_by_status("shopped")], ["קוטג"]
        )

    def test_purchase_dates_come_from_the_order_not_from_today(self):
        api = _FakeApi([ORDER], detail=ORDER)
        th.record_purchases(self.storage, api, "17655403")
        dates = self.storage.last_purchase_dates("tivtaam")
        self.assertTrue(dates)
        self.assertTrue(all(str(d)[:10] == "2026-09-12" for d in dates.values()))
        # And the delivery fee is not a product with a purchase date.
        self.assertNotIn("5", dates)


class RecordPurchasesLineDetailTests(unittest.TestCase):
    """record_purchases (2026-09-20): the raw order-detail API already
    carries real ordered-vs-delivered quantity, weightable and price --
    this is what stops it being thrown away before it reaches storage."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def test_real_order_lines_are_persisted(self):
        api = _FakeApi([ORDER], detail=ORDER)
        th.record_purchases(self.storage, api, "17655403")
        lines = self.storage.tivtaam_purchase_lines_for("16323094")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["order_code"], "17655403")
        self.assertEqual(lines[0]["order_date"], "2026-09-12")

    def test_weighed_product_keeps_ordered_and_actual_quantity_separately(self):
        # "בננות" in the real fixture: 0.5 kg ordered, 0.332 kg delivered.
        api = _FakeApi([ORDER], detail=ORDER)
        th.record_purchases(self.storage, api, "17655403")
        row = self.storage.tivtaam_purchase_lines_for("2")[0]
        self.assertEqual(row["ordered_quantity"], 0.5)
        self.assertEqual(row["actual_quantity"], 0.332)
        self.assertEqual(row["weightable"], 1)
        self.assertEqual(row["unit"], 'ק"ג')

    def test_delivery_fee_and_not_delivered_substitution_half_are_excluded(self):
        api = _FakeApi([ORDER], detail=ORDER)
        th.record_purchases(self.storage, api, "17655403")
        all_lines = self.storage.tivtaam_purchase_lines()
        codes = {r["product_code"] for r in all_lines}
        self.assertNotIn("5", codes)  # משלוח אינטרנט
        # productId=4 is status:5, the un-delivered half of the substitution.
        self.assertNotIn("4", codes)

    def test_order_derived_price_and_last_purchase_are_both_updated_from_one_call(self):
        api = _FakeApi([ORDER], detail=ORDER)
        th.record_purchases(self.storage, api, "17655403")
        self.assertTrue(self.storage.last_purchase_dates("tivtaam"))
        prices = self.storage.recent_order_prices("tivtaam", "693493231749")
        self.assertEqual(prices, [{"date": "2026-09-12", "price": 14.9}])


class SyncBackfillsLineDetailTests(unittest.TestCase):
    """sync() (2026-09-20): backfills real per-line evidence for whichever
    orders don't have it yet, capped so a large backlog doesn't hammer the
    API in one run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def test_a_fresh_sync_fetches_line_detail_for_the_new_order(self):
        api = _FakeApi([ORDER], detail=ORDER)
        result = th.sync(self.storage, api)
        self.assertEqual(result["line_detail_fetched"], 1)
        self.assertTrue(self.storage.tivtaam_order_lines_recorded("17655403"))

    def test_an_already_detailed_order_is_not_re_fetched(self):
        api = _FakeApi([ORDER], detail=ORDER)
        th.sync(self.storage, api)
        api2 = _FakeApi([ORDER], detail=ORDER)
        result = th.sync(self.storage, api2)
        self.assertEqual(result["line_detail_fetched"], 0)
        self.assertEqual([c for c in api2.calls if c[0] == "order"], [])

    def test_backfill_is_capped_per_sync(self):
        many_orders = [
            {**ORDER, "id": 1000 + i, "timePlaced": f"2026-01-{(i % 27) + 1:02d}T10:00:00Z"}
            for i in range(th.MAX_LINE_DETAIL_FETCHES_PER_SYNC + 5)
        ]
        api = _FakeApi(many_orders, detail=ORDER)
        result = th.sync(self.storage, api)
        self.assertEqual(result["line_detail_fetched"], th.MAX_LINE_DETAIL_FETCHES_PER_SYNC)

    def test_a_broken_order_detail_does_not_stop_the_rest_of_the_sync(self):
        class _FlakyApi(_FakeApi):
            def order(self, order_id):
                if order_id == ORDER["id"]:
                    raise RuntimeError("boom")
                return super().order(order_id)

        second = {**ORDER, "id": 999, "timePlaced": "2026-01-01T10:00:00Z"}
        api = _FlakyApi([ORDER, second], detail=second)
        result = th.sync(self.storage, api)
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["line_detail_fetched"], 1)
        self.assertTrue(self.storage.tivtaam_order_lines_recorded("999"))
        self.assertFalse(self.storage.tivtaam_order_lines_recorded("17655403"))


if __name__ == "__main__":
    unittest.main()


class CadenceSpansBothChainsTests(unittest.TestCase):
    """"When did you last shop" is a question about the fridge.

    Measured on the real history 2026-09-15: across 2026, Shufersal alone
    has 6 orders at a 27-day median with one 135-day hole; both chains
    together have 29 at a 7-day median. The first is Shufersal's share of
    the shop, not the household's rhythm.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        self.storage.log_orders(
            [{"code": "S1", "placed_at": "2026-09-07T08:57:00", "item_count": 41}],
            store="shufersal",
        )
        self.storage.log_orders(
            [{"code": "T1", "placed_at": "2026-09-12T16:48:25", "item_count": 25}],
            store="tivtaam",
        )

    def test_order_dates_can_span_chains(self):
        self.assertEqual(len(self.storage.order_dates("")), 2)
        self.assertEqual(len(self.storage.order_dates("shufersal")), 1)

    def test_the_digest_counts_from_the_newest_shop_at_either_chain(self):
        from grocery_bot import learn

        combined = learn.days_since_last_order(self.storage)
        shufersal_only = learn.days_since_last_order(self.storage, "shufersal")
        self.assertLess(combined, shufersal_only)

    def test_a_chain_with_no_orders_still_answers_about_the_household(self):
        from grocery_bot import learn

        self.assertIsNone(learn.days_since_last_order(self.storage, "victory"))
        self.assertIsNotNone(learn.days_since_last_order(self.storage))
