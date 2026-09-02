import unittest
from datetime import datetime

from grocery_bot.cartview import render_final, render_final_by_store, render_progress
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


class AttributionTests(unittest.TestCase):
    """Who asked distinguishes a personal request from the standing list."""

    def test_a_personal_request_is_marked(self) -> None:
        result = CartAddResult(
            item_name="מלפפונים חמוצים", store="s", status="added",
            price=12.9, requested_by="לירן",
        )
        self.assertIn("🙋לירן", render_final([result], None, WHEN))

    def test_a_standing_list_item_is_not_marked(self) -> None:
        self.assertNotIn("🙋", render_final([_added("פלפל", 9.9)], None, WHEN))

    def test_attribution_shows_during_the_run_too(self) -> None:
        result = CartAddResult(
            item_name="לימון", store="s", status="added", price=5.0, requested_by="ישי"
        )
        self.assertIn("🙋ישי", render_progress([result], 1, 3, WHEN))


class MultiStoreFinalViewTests(unittest.TestCase):
    """A cycle fills every enabled chain, so the hand-off must show each.

    Before this, the two chains' results were flattened into one list with
    one total and one button — a message describing no cart that actually
    existed. Choosing between the chains is the decision this message
    exists to support, so each gets its own section, total and misses.
    """

    def _report(self, store, added=(), missing=()):
        from grocery_bot.orchestrator import OrderCycleReport
        from grocery_bot.models import CartAddResult

        report = OrderCycleReport(store=store)
        for name, price in added:
            report.record(
                CartAddResult(
                    item_name=name, store=store, status="added", price=price, quantity=1
                )
            )
        for name in missing:
            report.record(
                CartAddResult(item_name=name, store=store, status="not_found")
            )
        return report

    def _reports(self):
        return {
            "shufersal": self._report(
                "shufersal", added=[("קוטג 5%", 6.40)], missing=["טחינה גולמית"]
            ),
            "tivtaam": self._report("tivtaam", added=[("קוטג' 5%", 7.10)]),
        }

    def test_each_chain_is_named(self):
        text = render_final_by_store(self._reports(), {}, WHEN)
        self.assertIn("שופרסל", text)
        self.assertIn("טיב טעם", text)

    def test_each_chain_keeps_its_own_total(self):
        text = render_final_by_store(
            self._reports(),
            {
                "shufersal": {"ok": True, "total": 143.20, "items": [1, 2]},
                "tivtaam": {"ok": True, "total": 98.40, "items": [1]},
            },
            WHEN,
        )
        self.assertIn("143.20", text)
        self.assertIn("98.40", text)

    def test_a_chain_whose_cart_could_not_be_read_says_so_rather_than_borrowing(self):
        # The old single reading was applied to whatever was rendered, so
        # an unreadable Tiv Taam cart would have shown Shufersal's total.
        text = render_final_by_store(
            self._reports(), {"shufersal": {"ok": True, "total": 143.20, "items": [1]}}, WHEN
        )
        self.assertIn("143.20", text)
        self.assertIn("הערכה בלבד", text)

    def test_a_chains_misses_stay_under_that_chain(self):
        text = render_final_by_store(self._reports(), {}, WHEN)
        shufersal_section = text.split("טיב טעם")[0]
        self.assertIn("טחינה גולמית", shufersal_section)

    def test_one_chain_falls_back_to_the_single_cart_wording(self):
        # An ordinary Shufersal-only run should not grow a heading it does
        # not need, or talk about choosing between chains.
        reports = {"shufersal": self._report("shufersal", added=[("קוטג 5%", 6.40)])}
        text = render_final_by_store(reports, {}, WHEN)
        self.assertIn("העגלה מוכנה", text)
        self.assertNotIn("לבחור רשת", text)

    def test_nothing_was_filled_anywhere_still_renders(self):
        self.assertIn("לא בוצעה קנייה", render_final_by_store({}, {}, WHEN))

    def test_the_no_purchase_warning_survives_the_multi_chain_path(self):
        # The hard rule's visible half: the household must never be left
        # thinking an order was placed.
        text = render_final_by_store(self._reports(), {}, WHEN)
        self.assertIn("לא בוצעה קנייה", text)


class CartButtonTests(unittest.TestCase):
    """One button per chain that actually has something in its cart."""

    def _reports(self, added_stores, empty_stores=()):
        from grocery_bot.orchestrator import OrderCycleReport
        from grocery_bot.models import CartAddResult

        reports = {}
        for store in added_stores:
            report = OrderCycleReport(store=store)
            report.record(CartAddResult(item_name="קוטג", store=store, status="added"))
            reports[store] = report
        for store in empty_stores:
            reports[store] = OrderCycleReport(store=store)
        return reports

    def _labels(self, reports):
        from grocery_bot.telegram_bot import GroceryBot

        return [row[0].text for row in GroceryBot._cart_buttons(reports)]

    def test_a_button_per_filled_chain(self):
        labels = self._labels(self._reports(["shufersal", "tivtaam"]))
        self.assertEqual(len(labels), 2)
        self.assertTrue(any("שופרסל" in label for label in labels))
        self.assertTrue(any("טיב טעם" in label for label in labels))

    def test_an_empty_chain_gets_no_button(self):
        # A link to a cart nothing went into is an invitation to look at
        # nothing.
        labels = self._labels(
            self._reports(["shufersal"], empty_stores=["tivtaam"])
        )
        self.assertEqual(len(labels), 1)
        self.assertIn("שופרסל", labels[0])

    def test_each_button_points_at_that_chains_own_cart(self):
        from grocery_bot.chains import cart_url
        from grocery_bot.telegram_bot import GroceryBot

        rows = GroceryBot._cart_buttons(self._reports(["shufersal", "tivtaam"]))
        urls = {row[0].url for row in rows}
        self.assertEqual(urls, {cart_url("shufersal"), cart_url("tivtaam")})
