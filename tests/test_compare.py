import tempfile
import unittest
from datetime import date
from pathlib import Path

from grocery_bot import compare
from grocery_bot.storage import Storage

# A real Tiv Taam order line shape, trimmed to the fields that matter.
MILK = "7290004131074"
COTTAGE = "7290004127329"
LOOSE_APPLES = "4412470"  # chain-internal produce code, not an EAN
DELIVERY = "9966"


def _order(lines, placed="2026-08-06T09:37:47.223Z"):
    return {"timePlaced": placed, "lines": lines}


def _line(barcode, name, price, qty=1.0):
    return {
        "barcode": barcode,
        "name": name,
        "price": price,
        "actualQuantity": qty,
    }


class BarcodeFilterTest(unittest.TestCase):
    def test_manufacturer_ean_is_comparable(self):
        self.assertTrue(compare.is_comparable_barcode(MILK))

    def test_loose_produce_code_is_not_comparable(self):
        # 4412470 is Tiv Taam's own code for apples; at Shufersal it is
        # either nothing or an unrelated product. Matching it would produce
        # a confident, wrong comparison.
        self.assertFalse(compare.is_comparable_barcode(LOOSE_APPLES))

    def test_delivery_line_is_not_a_product(self):
        self.assertFalse(compare.is_comparable_barcode(DELIVERY))

    def test_blank_and_nonnumeric_are_rejected(self):
        for value in (None, "", "  ", "abc123"):
            self.assertFalse(compare.is_comparable_barcode(value))


class OrderLineExtractionTest(unittest.TestCase):
    def test_keeps_only_comparable_priced_lines(self):
        order = _order([
            _line(MILK, "חלב 3%", 7.35, 3),
            _line(LOOSE_APPLES, "תפוח עץ", 12.9, 0.8),
            _line(DELIVERY, "משלוח", 29.9, 1),
        ])
        rows = compare.lines_from_tivtaam_order(order)
        self.assertEqual([r["barcode"] for r in rows], [MILK])
        self.assertEqual(rows[0]["quantity"], 3)

    def test_line_supplied_at_zero_is_dropped(self):
        # Out of stock: the household never bought it, and pricing it would
        # inflate both sides of the basket.
        order = _order([_line(MILK, "חלב 3%", 7.35, 0)])
        self.assertEqual(compare.lines_from_tivtaam_order(order), [])


class BasketComparisonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))
        with self.storage._connect() as conn:  # noqa: SLF001 - test fixture
            conn.executemany(
                "INSERT INTO catalog_products (item_code, name, price) VALUES (?, ?, ?)",
                [(MILK, "חלב בקרטון 3% שומן 1 ל", 7.35), (COTTAGE, "קוטג 5%", 6.40)],
            )
            conn.commit()

    def test_cheaper_chain_and_difference(self):
        order = _order([
            _line(MILK, "חלב 3% קרטון מהדרין", 7.35, 2),
            _line(COTTAGE, "קוטג' 5% מהדרין", 6.10, 2),
        ])
        result = compare.compare_basket(
            self.storage, compare.lines_from_tivtaam_order(order), "2026-08-06"
        )
        self.assertEqual(result.tivtaam_total, 26.90)
        self.assertEqual(result.shufersal_total, 27.50)
        self.assertEqual(result.difference, 0.60)
        self.assertEqual(result.cheaper_chain, "tivtaam")

    def test_equal_prices_pick_no_chain(self):
        order = _order([_line(MILK, "חלב", 7.35, 1)])
        result = compare.compare_basket(
            self.storage, compare.lines_from_tivtaam_order(order)
        )
        self.assertIsNone(result.cheaper_chain)
        self.assertIsNone(result.lines[0].cheaper_at)

    def test_product_absent_from_shufersal_is_reported_not_guessed(self):
        order = _order([
            _line(MILK, "חלב", 7.35, 1),
            _line("7290000000001", "מוצר שאין בשופרסל", 20.0, 1),
        ])
        result = compare.compare_basket(
            self.storage, compare.lines_from_tivtaam_order(order)
        )
        self.assertEqual(len(result.compared), 1)
        self.assertEqual(len(result.unmatched), 1)
        self.assertAlmostEqual(result.coverage, 0.5)
        # The unmatched item must not leak into either total.
        self.assertEqual(result.tivtaam_total, 7.35)

    def test_quantity_scales_the_difference(self):
        order = _order([_line(COTTAGE, "קוטג'", 6.10, 10)])
        result = compare.compare_basket(
            self.storage, compare.lines_from_tivtaam_order(order)
        )
        line = result.lines[0]
        self.assertEqual(line.delta, 0.30)
        self.assertEqual(line.line_delta, 3.00)

    def test_ingested_price_is_reused_when_basket_carries_none(self):
        compare.ingest_tivtaam_order(
            self.storage, _order([_line(MILK, "חלב", 6.00, 1)], "2026-07-01T00:00:00Z")
        )
        result = compare.compare_basket(self.storage, [{"barcode": MILK, "quantity": 1}])
        self.assertEqual(result.lines[0].tivtaam_price, 6.00)
        self.assertEqual(result.lines[0].tivtaam_observed_at, "2026-07-01")

    def test_newest_observation_wins(self):
        for day, price in (("2026-01-01T00:00:00Z", 5.0), ("2026-06-01T00:00:00Z", 9.0)):
            compare.ingest_tivtaam_order(
                self.storage, _order([_line(MILK, "חלב", price, 1)], day)
            )
        self.assertEqual(self.storage.latest_store_price("tivtaam", MILK)["price"], 9.0)

    def test_staleness_is_reported_so_it_can_be_said_out_loud(self):
        order = _order([_line(MILK, "חלב", 7.35, 1)], "2026-07-06T00:00:00Z")
        result = compare.compare_basket(
            self.storage, compare.lines_from_tivtaam_order(order), "2026-07-06"
        )
        self.assertEqual(
            compare.staleness_days(result, today=date(2026, 8, 6)), 31
        )

    def test_biggest_gaps_ranks_by_money_not_unit_price(self):
        # A 30-agorot gap bought ten times matters more than a one-shekel
        # gap bought once; ranking by unit price would invert that.
        order = _order([
            _line(COTTAGE, "קוטג'", 6.10, 10),
            _line(MILK, "חלב", 6.35, 1),
        ])
        result = compare.compare_basket(
            self.storage, compare.lines_from_tivtaam_order(order)
        )
        self.assertEqual(result.biggest_gaps(1)[0].barcode, COTTAGE)


if __name__ == "__main__":
    unittest.main()
