import unittest

from grocery_bot.listbuilder import as_paste_text, available_lists, build, summarise


def _row(name, share, department):
    return {"product_name": name, "share": share, "department": department}


ROWS = [
    _row("פלפל אדום", 0.95, "פירות וירקות"),
    _row("קוטג'", 0.89, "מוצרי חלב וקירור"),
    _row("טונה", 0.21, "מזווה ושימורים"),
    _row("שעועית קפואה", 0.36, "קפואים ומזון בסיסי"),
    _row("מוצר נדיר", 0.05, "מזווה ושימורים"),
]


def _spec(key):
    return next(s for s in available_lists() if s.key == key)


class ThresholdTests(unittest.TestCase):
    def test_core_keeps_only_frequent_items(self) -> None:
        names = [r["product_name"] for r in build(_spec("core"), ROWS).items]
        self.assertIn("פלפל אדום", names)
        self.assertNotIn("טונה", names)

    def test_full_reaches_further_down(self) -> None:
        names = [r["product_name"] for r in build(_spec("full"), ROWS).items]
        self.assertIn("טונה", names)

    def test_rare_items_are_never_included(self) -> None:
        for key in ("core", "full", "fresh", "pantry"):
            names = [r["product_name"] for r in build(_spec(key), ROWS).items]
            self.assertNotIn("מוצר נדיר", names, key)

    def test_items_are_ordered_by_how_often_they_are_bought(self) -> None:
        items = build(_spec("full"), ROWS).items
        shares = [r["share"] for r in items]
        self.assertEqual(shares, sorted(shares, reverse=True))


class SplitTests(unittest.TestCase):
    def test_fresh_holds_only_perishables(self) -> None:
        departments = {r["department"] for r in build(_spec("fresh"), ROWS).items}
        self.assertEqual(departments, {"פירות וירקות", "מוצרי חלב וקירור"})

    def test_pantry_is_everything_else(self) -> None:
        """The two lists must partition, not overlap — different rhythms."""
        fresh = {r["product_name"] for r in build(_spec("fresh"), ROWS).items}
        pantry = {r["product_name"] for r in build(_spec("pantry"), ROWS).items}
        self.assertEqual(fresh & pantry, set())
        full = {r["product_name"] for r in build(_spec("full"), ROWS).items}
        self.assertEqual(fresh | pantry, full)


class PasteTests(unittest.TestCase):
    def test_paste_text_is_bare_names_one_per_line(self) -> None:
        """Anything else is matched as part of a product name."""
        text = as_paste_text(build(_spec("core"), ROWS))
        for line in text.splitlines():
            self.assertNotIn("•", line)
            self.assertNotIn("·", line)
            self.assertFalse(line.startswith(" "))
        self.assertEqual(len(text.splitlines()), 3)

    def test_summary_reports_counts_per_department(self) -> None:
        text = summarise(build(_spec("full"), ROWS))
        self.assertIn("4 מוצרים", text)
        self.assertIn("פירות וירקות", text)

    def test_an_empty_list_says_so(self) -> None:
        spec = build(_spec("core"), [_row("x", 0.01, "מזווה ושימורים")])
        self.assertIn("אין מוצרים", summarise(spec))


if __name__ == "__main__":
    unittest.main()
