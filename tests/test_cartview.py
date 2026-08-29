import unittest
from datetime import datetime

from grocery_bot.cartview import render_final, render_progress
from grocery_bot.models import CartAddResult, OrderCycleReport

WHEN = datetime(2026, 8, 29, 16, 5)


def _added(name, price, qty=1):
    return CartAddResult(
        item_name=name, store="shufersal", status="added", price=price, quantity=qty
    )


class ProgressTests(unittest.TestCase):
    def test_shows_position_in_the_run(self) -> None:
        text = render_progress([_added("קוטג'", 6.1)], 1, 17, WHEN)
        self.assertIn("(1/17)", text)

    def test_carries_a_timestamp_so_staleness_is_visible(self) -> None:
        """The exit node is a TV box; a frozen view must be detectable."""
        self.assertIn("16:05", render_progress([_added("קוטג'", 6.1)], 1, 5, WHEN))

    def test_running_total_multiplies_by_quantity(self) -> None:
        text = render_progress([_added("קוטג'", 6.1, qty=2)], 1, 5, WHEN)
        self.assertIn("12.20₪", text)

    def test_running_total_is_labelled_an_estimate(self) -> None:
        """It excludes delivery and club discounts, so it is not the bill."""
        text = render_progress([_added("קוטג'", 6.1)], 1, 5, WHEN)
        self.assertIn("בערך", text)
        self.assertIn("הערכה", text)

    def test_unpriced_items_do_not_break_the_total(self) -> None:
        results = [_added("קוטג'", 6.1), _added("משהו", None)]
        self.assertIn("6.10₪", render_progress(results, 2, 5, WHEN))

    def test_non_added_items_are_shown_but_not_counted(self) -> None:
        results = [
            _added("קוטג'", 6.1),
            CartAddResult(item_name="בננה", store="shufersal", status="ambiguous"),
        ]
        text = render_progress(results, 2, 5, WHEN)
        self.assertIn("בננה", text)
        self.assertIn("1 פריטים", text)

    def test_a_long_run_is_truncated(self) -> None:
        results = [_added(f"מוצר {i}", 1.0) for i in range(40)]
        text = render_progress(results, 40, 40, WHEN)
        self.assertIn("ועוד", text)
        self.assertLess(len(text.splitlines()), 25)


class FinalTests(unittest.TestCase):
    def test_prefers_the_stores_own_total(self) -> None:
        """Our sum misses delivery and discounts; the cart knows them."""
        text = render_final(
            [_added("קוטג'", 6.1)], {"ok": True, "total": 119.45, "items": [1] * 10}, WHEN
        )
        self.assertIn("119.45₪", text)
        self.assertIn("לתשלום", text)

    def test_unreadable_cart_says_so_instead_of_faking_a_total(self) -> None:
        text = render_final([_added("קוטג'", 6.1)], {"ok": False, "total": None}, WHEN)
        self.assertIn("הערכה", text)
        self.assertNotIn("לתשלום", text)

    def test_missing_cart_entirely_is_handled(self) -> None:
        text = render_final([_added("קוטג'", 6.1)], None, WHEN)
        self.assertIn("6.10₪", text)

    def test_always_states_that_nothing_was_purchased(self) -> None:
        """The hard project rule, restated where the user actually looks."""
        text = render_final([_added("קוטג'", 6.1)], {"ok": True, "total": 10.0, "items": []}, WHEN)
        self.assertIn("לא בוצעה קנייה", text)

    def test_problems_are_listed_after_the_additions(self) -> None:
        results = [
            _added("קוטג'", 6.1),
            CartAddResult(item_name="קינמון", store="shufersal", status="not_found"),
        ]
        text = render_final(results, None, WHEN)
        self.assertLess(text.index("קוטג'"), text.index("קינמון"))

    def test_empty_cycle_renders(self) -> None:
        self.assertIn("לא בוצעה קנייה", render_final([], None, WHEN))


class ReportResultsTests(unittest.TestCase):
    def test_results_lists_every_outcome_added_first(self) -> None:
        report = OrderCycleReport(store="shufersal")
        report.record(CartAddResult(item_name="ב", store="s", status="not_found"))
        report.record(CartAddResult(item_name="א", store="s", status="added"))
        self.assertEqual([r.item_name for r in report.results], ["א", "ב"])


if __name__ == "__main__":
    unittest.main()


class PriceParsingTests(unittest.TestCase):
    """The savings line is usually 0.00 and sits next to the real total."""

    def test_total_is_read_after_the_payable_label(self) -> None:
        from grocery_bot.adapters.shufersal import _price_after

        body = 'לתשלום:\n₪\nשקלים חדשים\n119.45\nסה”כ חסכת:₪\nשקלים חדשים\n0.00'
        self.assertEqual(_price_after(body, "לתשלום"), 119.45)

    def test_an_earlier_zero_does_not_win(self) -> None:
        from grocery_bot.adapters.shufersal import _price_after

        body = 'סה”כ חסכת: 0.00\nלתשלום: 119.45'
        self.assertEqual(_price_after(body, "לתשלום"), 119.45)

    def test_a_missing_label_gives_nothing_rather_than_a_wrong_number(self) -> None:
        from grocery_bot.adapters.shufersal import _price_after

        self.assertIsNone(_price_after("0.00 משהו אחר", "לתשלום"))
