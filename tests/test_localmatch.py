"""Resolving a household term against a chain's own feed.

Every test here is a real case from the live Tiv Taam catalogue. The two
refusals matter more than the acceptances: this writes product memory,
which buys the same thing every week without asking again, so a wrong
resolution is expensive and silent while an unresolved one costs exactly
one question.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import localmatch
from grocery_bot.storage import Storage


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _feed(self, *rows):
        self.storage.record_store_prices("tivtaam", [
            {"barcode": str(i), "name": name, "price": price,
             "observed_at": "2026-09-07", "source": "feed"}
            for i, (name, price) in enumerate(rows, start=1)
        ])

    def test_an_exact_name_wins(self):
        self._feed(("פלפל אדום", 8.90), ("פלפל אדום קלוי בשמן", 18.90))
        hit = localmatch.resolve_term(self.storage, "tivtaam", "פלפל אדום")
        self.assertEqual(hit.name, "פלפל אדום")

    def test_a_size_qualifier_is_still_the_same_product(self):
        self._feed(("דנונה דל לקטוז 200 גרם", 4.50))
        hit = localmatch.resolve_term(self.storage, "tivtaam", "דנונה דל לקטוז")
        self.assertEqual(hit.name, "דנונה דל לקטוז 200 גרם")

    def test_a_different_noun_after_the_term_is_refused(self):
        """Live error: 'בננה' resolved to בננה ציפס — a bag of crisps.
        The Tiv Taam feed carries no fresh bananas at all, so there was
        no right answer and the only safe one was to refuse."""
        self._feed(
            ("בננה ציפס 200 גרם ששון הקולה", 15.90),
            ("בננה מיובשת", 99.00),
            ("בננה ציפס שטוח", 55.00),
        )
        self.assertIsNone(localmatch.resolve_term(self.storage, "tivtaam", "בננה"))

    def test_the_cheapest_match_is_not_automatically_the_right_one(self):
        """Live error: 'מלפפון' resolved to מלפפון במלח (pickles) purely
        because pickles undercut fresh cucumbers on price."""
        self._feed(("מלפפון במלח 7-9 בית השיטה", 8.90), ("מלפפון ארוז", 11.90))
        hit = localmatch.resolve_term(self.storage, "tivtaam", "מלפפון")
        self.assertEqual(hit.name, "מלפפון ארוז")
        self.assertGreater(hit.price, 8.90, "it deliberately did not take the cheaper row")

    def test_a_price_controlled_product_beats_an_equal_alternative(self):
        self._feed(("חלב 1% קרטון - בפיקוח", 5.59), ("חלב 1% קרטון מהדרין", 5.40))
        hit = localmatch.resolve_term(self.storage, "tivtaam", "חלב 1% קרטון")
        self.assertTrue(hit.controlled)
        self.assertIn("בפיקוח", hit.name)

    def test_nothing_in_the_feed_resolves_to_nothing(self):
        self._feed(("פלפל אדום", 8.90))
        self.assertIsNone(localmatch.resolve_term(self.storage, "tivtaam", "אננס"))

    def test_seeding_writes_memory_only_for_what_it_is_sure_of(self):
        self._feed(("פלפל אדום", 8.90), ("בננה ציפס 200 גרם", 15.90))
        report = localmatch.seed_memory(
            self.storage, "tivtaam", ["פלפל אדום", "בננה"]
        )
        self.assertEqual([h.term for h in report["seeded"]], ["פלפל אדום"])
        self.assertEqual(report["unresolved"], ["בננה"])
        self.assertIsNotNone(self.storage.preferred_for("tivtaam", "פלפל אדום"))
        self.assertIsNone(self.storage.preferred_for("tivtaam", "בננה"))

    def test_a_dry_run_writes_nothing(self):
        self._feed(("פלפל אדום", 8.90))
        localmatch.seed_memory(self.storage, "tivtaam", ["פלפל אדום"], dry_run=True)
        self.assertIsNone(self.storage.preferred_for("tivtaam", "פלפל אדום"))

    def test_the_barcode_travels_with_the_choice(self):
        self._feed(("פלפל אדום", 8.90))
        localmatch.seed_memory(self.storage, "tivtaam", ["פלפל אדום"])
        self.assertEqual(
            self.storage.preferred_for("tivtaam", "פלפל אדום")["product_code"], "1"
        )


class ControlledMarkerTests(unittest.TestCase):
    def test_the_markers_the_chains_actually_use(self):
        for name in ("חלב 1% קרטון - בפיקוח", "חלב עמיד 3% - מחיר בפיקוח ממשלתי"):
            self.assertTrue(localmatch.is_price_controlled(name), name)

    def test_an_ordinary_name_is_not_controlled(self):
        self.assertFalse(localmatch.is_price_controlled("חלב 3% מהדרין שקית"))


if __name__ == "__main__":
    unittest.main()
