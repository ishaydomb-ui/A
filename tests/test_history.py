import datetime
import tempfile
import unittest
from pathlib import Path

from grocery_bot.history import import_base_list, seed_product_memory, summarise
from grocery_bot.storage import Storage


def _entry(code, name, quantity, selling_method="BY_UNIT", weight_conversion=None):
    return {
        "quantity": quantity,
        "product": {
            "code": code,
            "name": name,
            "sellingMethod": {"code": selling_method},
            "weightConversion": weight_conversion,
        },
    }


def _order(code, day, entries):
    return {
        "code": code,
        "created": datetime.datetime(2025, 1, day, 10, 0),
        "entries": entries,
    }


class QuantityInterpretationTests(unittest.TestCase):
    """sellingMethod decides what `quantity` means -- not the unit."""

    def test_by_unit_quantity_is_a_count(self) -> None:
        history = summarise([_order("1", 1, [_entry("P_1", "קוטג'", 2)])])
        item = history.products[0]
        self.assertEqual(item.default_quantity, 2)
        self.assertEqual(item.amount_and_unit, (None, ""))

    def test_by_weight_quantity_is_grams(self) -> None:
        history = summarise([_order("1", 1, [_entry("P_2", "פלפל אדום", 500, "BY_WEIGHT")])])
        item = history.products[0]
        # One "add" carrying an amount, not 500 peppers.
        self.assertEqual(item.default_quantity, 1)
        self.assertEqual(item.amount_and_unit, (0.5, 'ק"ג'))

    def test_by_package_converts_grams_into_whole_packages(self) -> None:
        """The bug this guards: 1300g of 1300g-per-bag carrots is ONE bag.

        Read as a count it would order 1300 bags of carrots.
        """
        history = summarise(
            [_order("1", 1, [_entry("P_3", "גזר ארוז", 1300, "BY_PACKAGE", 1300.0)])]
        )
        item = history.products[0]
        self.assertEqual(item.default_quantity, 1)
        self.assertEqual(item.amount_and_unit, (1.3, 'ק"ג'))

    def test_by_package_multiple_packages(self) -> None:
        history = summarise(
            [_order("1", 1, [_entry("P_4", "כרוב לבן", 2000, "BY_PACKAGE", 1000.0)])]
        )
        self.assertEqual(history.products[0].default_quantity, 2)

    def test_by_package_without_conversion_falls_back_to_one(self) -> None:
        history = summarise([_order("1", 1, [_entry("P_5", "משהו", 900, "BY_PACKAGE", None)])])
        self.assertEqual(history.products[0].default_quantity, 1)

    def test_by_unit_with_kg_unit_is_still_a_count(self) -> None:
        """A 1kg bag of rice is quantity 1 -- the unit is KG but it's BY_UNIT."""
        history = summarise([_order("1", 1, [_entry("P_6", "אורז בסמטי", 1)])])
        self.assertEqual(history.products[0].default_quantity, 1)


class FrequencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orders = [
            _order("1", 1, [_entry("P_A", "קוטג'", 1), _entry("P_B", "חלב", 1)]),
            _order("2", 2, [_entry("P_A", "קוטג'", 1)]),
            _order("3", 3, [_entry("P_A", "קוטג'", 1), _entry("P_C", "במבה", 1)]),
            _order("4", 4, [_entry("P_A", "קוטג'", 1), _entry("P_B", "חלב", 1)]),
        ]

    def test_share_is_fraction_of_orders_containing_the_product(self) -> None:
        history = summarise(self.orders)
        by_code = {p.product_code: p for p in history.products}
        self.assertEqual(by_code["P_A"].share, 1.0)
        self.assertEqual(by_code["P_B"].share, 0.5)
        self.assertEqual(by_code["P_C"].share, 0.25)

    def test_frequent_filters_by_share(self) -> None:
        history = summarise(self.orders)
        self.assertEqual({p.product_code for p in history.frequent(0.5)}, {"P_A", "P_B"})

    def test_repeated_lines_in_one_order_count_once(self) -> None:
        """Two lines of the same product is still one order containing it."""
        history = summarise(
            [_order("1", 1, [_entry("P_A", "קוטג'", 1), _entry("P_A", "קוטג'", 1)])]
        )
        self.assertEqual(history.products[0].order_count, 1)

    def test_delivery_fee_lines_are_not_products(self) -> None:
        """Delivery appears on every order and would otherwise rank first."""
        history = summarise(
            [_order("1", 1, [_entry("P_D", "משלוח שופרסל אונליין", 1), _entry("P_A", "קוטג'", 1)])]
        )
        self.assertEqual([p.name for p in history.products], ["קוטג'"])

    def test_empty_history_is_handled(self) -> None:
        history = summarise([])
        self.assertEqual(history.orders_analysed, 0)
        self.assertEqual(history.frequent(0.5), [])


class ImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "test.sqlite3"))
        self.history = summarise(
            [
                _order("1", 1, [_entry("P_A", "קוטג'", 2), _entry("P_B", "פלפל", 500, "BY_WEIGHT")]),
                _order("2", 2, [_entry("P_A", "קוטג'", 2)]),
            ]
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_import_adds_frequent_items_only(self) -> None:
        count = import_base_list(self.storage, self.history, min_share=0.75)
        self.assertEqual(count, 1)
        self.assertEqual([i.name for i in self.storage.list_active_base_items()], ["קוטג'"])

    def test_imported_item_carries_quantity_and_amount(self) -> None:
        import_base_list(self.storage, self.history, min_share=0.4)
        items = {i.name: i for i in self.storage.list_active_base_items()}
        self.assertEqual(items["קוטג'"].default_quantity, 2)
        self.assertEqual(items["פלפל"].amount, 0.5)

    def test_import_records_the_exact_product_choice(self) -> None:
        """Otherwise every cycle re-asks which of ~20 search results was meant."""
        import_base_list(self.storage, self.history, min_share=0.75)
        preferred = self.storage.preferred_for("shufersal", "קוטג'")
        self.assertIsNotNone(preferred)
        self.assertEqual(preferred["product_code"], "P_A")

    def test_seed_product_memory_covers_everything_ever_bought(self) -> None:
        seeded = seed_product_memory(self.storage, self.history)
        self.assertEqual(seeded, 2)
        self.assertEqual(self.storage.preferred_for("shufersal", "פלפל")["product_code"], "P_B")


class ReplaceTests(unittest.TestCase):
    """Re-importing must refresh the list, not append a second copy."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._tmpdir.name) / "test.sqlite3"))
        self.history = summarise([_order("1", 1, [_entry("P_A", "קוטג'", 1)])])

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_reimport_does_not_duplicate(self) -> None:
        import_base_list(self.storage, self.history, min_share=0.5)
        import_base_list(self.storage, self.history, min_share=0.5)
        self.assertEqual([i.name for i in self.storage.list_active_base_items()], ["קוטג'"])

    def test_import_retires_previous_placeholder_items(self) -> None:
        self.storage.add_base_list_item("פלייסהולדר ישן")
        import_base_list(self.storage, self.history, min_share=0.5)
        names = [i.name for i in self.storage.list_active_base_items()]
        self.assertNotIn("פלייסהולדר ישן", names)

    def test_replace_false_appends(self) -> None:
        self.storage.add_base_list_item("קיים")
        import_base_list(self.storage, self.history, min_share=0.5, replace=False)
        names = [i.name for i in self.storage.list_active_base_items()]
        self.assertIn("קיים", names)


if __name__ == "__main__":
    unittest.main()
