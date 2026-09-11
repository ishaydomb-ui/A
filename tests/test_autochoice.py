"""Close the questions a rule can answer — and only after being asked.

The UX audit's §2: replace some questions with a defined default, and
"כלל חדש דורש בחירה מפורשת של המשתמש. אי־מחיקה אינה הסכמה". So the rule
is previewed with its own examples and applied only on a tap.

The rule itself is `localmatch.resolve_term`, which already existed and
is deliberately timid. Measured on the real backlog 2026-09-11: it closes
14 of 80 open questions. The rest — בצל → יבש / אדום / שאלוט — are real
decisions, and that number is reported rather than smoothed over.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot.storage import Storage
from grocery_bot.telegram_bot import GroceryBot


class AutoChoicePreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self.bot = GroceryBot.__new__(GroceryBot)
        self.bot.storage = self.storage
        self.storage.record_store_prices("tivtaam", [
            # An exact name: the rule may take it.
            {"barcode": "1", "name": "מגבונים לניקוי כללי", "price": 19.9,
             "observed_at": "2026-09-10", "source": "feed"},
            {"barcode": "2", "name": "מגבונים לניקוי כללי 3*50 יחידות", "price": 24.9,
             "observed_at": "2026-09-10", "source": "feed"},
            # Genuinely different products: the rule must not choose.
            {"barcode": "3", "name": "בצל יבש", "price": 5.9,
             "observed_at": "2026-09-10", "source": "feed"},
            {"barcode": "4", "name": "בצל אדום", "price": 7.9,
             "observed_at": "2026-09-10", "source": "feed"},
        ])

    def _queue(self, term):
        self.storage.save_pending_ambiguity(
            store="tivtaam", original_term=term, quantity=1,
            candidates=["א", "ב"], candidate_cards=[],
        )

    def test_an_exact_name_can_be_answered_by_the_rule(self) -> None:
        self._queue("מגבונים לניקוי כללי")
        counts, picks = self.bot._autochoice_preview()  # noqa: SLF001
        self.assertEqual(counts["open"], 1)
        self.assertEqual([hit.term for _, hit in picks], ["מגבונים לניקוי כללי"])

    def test_a_real_choice_is_left_to_the_household(self) -> None:
        # Dry onion and red onion are not the same product, and no
        # tie-break makes that decision safe to take on their behalf.
        self._queue("בצל")
        counts, picks = self.bot._autochoice_preview()  # noqa: SLF001
        self.assertEqual(counts["open"], 1)
        self.assertEqual(picks, [])

    def test_the_preview_changes_nothing(self) -> None:
        self._queue("מגבונים לניקוי כללי")
        self.bot._autochoice_preview()  # noqa: SLF001
        self.assertIsNone(
            self.storage.preferred_for("tivtaam", "מגבונים לניקוי כללי")
        )
        self.assertEqual(len(self.storage.list_pending_ambiguities()), 1)

    def test_an_already_remembered_term_is_not_offered_again(self) -> None:
        self._queue("מגבונים לניקוי כללי")
        self.storage.remember_choice(
            store="tivtaam", term="מגבונים לניקוי כללי",
            product_code="1", product_name="מגבונים לניקוי כללי",
        )
        counts, picks = self.bot._autochoice_preview()  # noqa: SLF001
        self.assertEqual(counts["open"], 0)
        self.assertEqual(picks, [])


if __name__ == "__main__":
    unittest.main()
