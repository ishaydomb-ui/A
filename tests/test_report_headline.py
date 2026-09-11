"""The completion message as a decision screen.

Nine blocks had accumulated, each added for a real reason. The UX audit's
rule for cutting them: counts and money on one line, full lists behind a
button — but anything that would leave a silent gap stays visible.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot.models import CartAddResult, OrderCycleReport
from grocery_bot.orchestrator import format_report_headline
from grocery_bot.storage import Storage


def _deal(name, label):
    result = CartAddResult(item_name=name, store="shufersal", status="added")
    result.deal = label
    return result


class HeadlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def _headline(self, report):
        return format_report_headline(self.storage, {"shufersal": report})

    def _report(self):
        return OrderCycleReport(store="shufersal")

    def test_the_added_items_are_counted_not_listed(self) -> None:
        report = self._report()
        for n in range(30):
            report.record(CartAddResult(item_name=f"מוצר {n}", store="shufersal",
                                        status="added"))
        text = self._headline(report)
        self.assertIn("30 נוספו", text)
        self.assertNotIn("מוצר 17", text)

    def test_a_missing_product_is_never_behind_a_button(self) -> None:
        # The case a short message must not create: a cart quietly without
        # the thing that was asked for.
        report = self._report()
        report.record(CartAddResult(item_name="טחינה גולמית", store="shufersal",
                                    status="not_found"))
        text = self._headline(report)
        self.assertIn("טחינה גולמית", text)
        self.assertIn("נשאר ברשימה", text)

    def test_a_line_not_put_back_is_named(self) -> None:
        report = self._report()
        skipped = CartAddResult(item_name="חלב 3%", store="shufersal", status="skipped")
        skipped.detail = "הוסר ידנית מהעגלה"
        report.record(skipped)
        self.assertIn("חלב 3%", self._headline(report))

    def test_an_error_is_named(self) -> None:
        report = self._report()
        report.record(CartAddResult(item_name="(session)", store="shufersal",
                                    status="error"))
        self.assertIn("🛑", self._headline(report))

    def test_the_saving_is_summed_when_every_line_carries_one(self) -> None:
        report = self._report()
        report.record(_deal("דבש", "-70% · 5.00₪ במקום 16.90₪ (חיסכון 11.90₪)"))
        report.record(_deal("טונה", "-64% · 5.00₪ במקום 13.90₪ (חיסכון 8.90₪)"))
        self.assertIn("חיסכון 20.80₪", self._headline(report))

    def test_an_unknown_saving_is_not_printed_as_a_number(self) -> None:
        # A partial sum presented as the total is the exact failure this
        # project keeps meeting.
        report = self._report()
        report.record(_deal("דבש", "-70% · 5.00₪ במקום 16.90₪ (חיסכון 11.90₪)"))
        report.record(_deal("משהו", "מבצע"))
        text = self._headline(report)
        self.assertIn("2 מבצעים", text)
        self.assertNotIn("חיסכון", text)

    def test_a_repeated_failure_is_marked_on_the_item_not_in_a_second_block(self) -> None:
        for day in ("2026-09-08", "2026-09-09", "2026-09-10"):
            self.storage.record_cart_failures(
                [CartAddResult(item_name="טחינה גולמית", store="shufersal",
                               status="not_found")],
                when=f"{day}T10:00:00+00:00",
            )
        report = self._report()
        report.record(CartAddResult(item_name="טחינה גולמית", store="shufersal",
                                    status="not_found"))
        text = self._headline(report)
        self.assertIn("טחינה גולמית", text)
        self.assertIn("מחזורים", text)
        self.assertEqual(text.count("טחינה גולמית"), 1)


if __name__ == "__main__":
    unittest.main()
