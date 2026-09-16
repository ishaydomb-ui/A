"""Hebrew quote forms must not decide whether a search finds anything.

Raised by Nigel (family-budget-automation) 2026-09-16 and confirmed here
against the live feed before acting: of 547,069 rows, 48,776 names carry
the ASCII " and 48 carry the Hebrew gershayim ״ (U+05F4). The iOS Hebrew
keyboard produces the gershayim. So a query typed on Ishay's own phone
could not reach the 48,776, and an ASCII query could not reach the 48 —
and the failure looks exactly like "the shop doesn't sell it".

The single-quote family has folded since 2026-09-04; this is its
double-quote half, which was missing.
"""
import unittest

from grocery_bot.storage import _fold_apostrophes as fold


class DoubleQuoteFoldingTests(unittest.TestCase):
    def test_gershayim_and_ascii_quote_collapse_together(self):
        for ascii_form, hebrew_form in (
            ('ק"ג', "ק״ג"), ('מ"ל', "מ״ל"), ('ס"מ', "ס״מ"),
            ('ש"ח', "ש״ח"), ('תמ"ל', "תמ״ל"),
        ):
            with self.subTest(ascii_form):
                self.assertEqual(fold(ascii_form), fold(hebrew_form))

    def test_curly_double_quotes_fold_too(self):
        self.assertEqual(fold('חלב "טרי"'), fold("חלב “טרי”"))

    def test_the_single_quote_family_still_folds(self):
        # The 2026-09-04 behaviour, pinned so this change cannot undo it.
        self.assertEqual(fold("קוטג'"), fold("קוטג׳"))
        self.assertEqual(fold("קוטג'"), "קוטג")

    def test_ordinary_product_text_is_untouched(self):
        # Percent signs, digits, asterisks and Hebrew letters all survive:
        # they are what actually distinguishes one product from another.
        for name in ("חלב 3% 1 ליטר", "טונה בהירה בשמן 3*80 גרם",
                     "עגבניות שרי תמר לב"):
            with self.subTest(name):
                self.assertEqual(fold(name), name)

    def test_folding_is_idempotent(self):
        once = fold('חלב 3% ק"ג')
        self.assertEqual(fold(once), once)

    def test_nothing_is_not_an_error(self):
        self.assertEqual(fold(""), "")
        self.assertEqual(fold(None), "")


class FoldedSearchTests(unittest.TestCase):
    """Through the SQL function, which is where it actually matters."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from grocery_bot.storage import Storage

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        from contextlib import closing

        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO store_prices (store, barcode, name, price, observed_at, source)"
                " VALUES ('tivtaam', 'b1', 'ספרייט ליים 500 מ״ל', 6.9, '2026-09-16', 't')"
            )
            conn.execute(
                "INSERT INTO store_prices (store, barcode, name, price, observed_at, source)"
                ' VALUES (\'tivtaam\', \'b2\', \'חלב 3% 1 ליטר ק"ג\', 7.9, \'2026-09-16\', \'t\')'
            )
            conn.commit()

    def _count(self, term):
        from contextlib import closing

        from grocery_bot.storage import _fold_apostrophes

        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            return conn.execute(
                "SELECT COUNT(*) FROM store_prices WHERE fold(name) LIKE ?",
                (f"%{_fold_apostrophes(term)}%",),
            ).fetchone()[0]

    def test_an_ascii_query_reaches_a_gershayim_name(self):
        self.assertEqual(self._count('ספרייט ליים 500 מ"ל'), 1)

    def test_a_gershayim_query_reaches_an_ascii_name(self):
        self.assertEqual(self._count("חלב 3% 1 ליטר ק״ג"), 1)


if __name__ == "__main__":
    unittest.main()
