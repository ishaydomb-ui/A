"""Decide from purchase history instead of asking 90 questions.

Set by Ishay 2026-09-17: "אני לא מתכוון לענות על 90 שאלות. הפרוסס הזה לא
עובד. קח החלטה מה לשים על בסיס היסטוריית הקנייה שלי."

The first version of this module was confidently wrong, and the tests
that matter are the ones pinning why. Ranking candidates by how often the
household buys them — with no check that the candidate answers the term —
resolved `גבינה צהובה מגוררת` to `פלפל צהוב` (bought in 88% of orders,
shares the word צהוב), `מארז אוכמניות` to `דלעת ארוזה`, and `ביצי משק M`
to `כרוב לבן`. A purchase share says "they like this product"; it never
says "this is what they asked for".
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import autoresolve
from grocery_bot.stock import StockItem
from grocery_bot.storage import Storage


class _Row(dict):
    """Stands in for a sqlite3.Row, which indexes by name."""


def _row(id, store, term, candidates, cards="[]"):
    import json

    return _Row(id=id, store=store, original_term=term,
                candidates=json.dumps(candidates, ensure_ascii=False),
                candidate_cards=cards)


class RelevanceGateTests(unittest.TestCase):
    """The gate whose absence was the whole defect."""

    def test_a_shared_adjective_is_not_a_match(self):
        # גבינה צהובה מגוררת vs פלפל צהוב — both "yellow", nothing else.
        self.assertEqual(autoresolve._relevance("גבינה צהובה מגוררת", "פלפל צהוב"), 0.0)

    def test_an_unrelated_staple_is_not_a_match(self):
        self.assertEqual(autoresolve._relevance("מארז אוכמניות", "דלעת ארוזה"), 0.0)
        self.assertEqual(autoresolve._relevance("ביצי משק M", "כרוב לבן"), 0.0)

    def test_the_same_product_scores_fully(self):
        self.assertEqual(autoresolve._relevance("מארז אוכמניות", "מארז אוכמניות 125 גרם"), 1.0)

    def test_a_qualifier_that_is_missing_scores_partially(self):
        # They asked for chili and would get a sweet red pepper.
        score = autoresolve._relevance("פלפל צילי", "פלפל אדום")
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))
        self.storage.replace_stock_items("tivtaam", [
            StockItem("1", "פלפל אדום", 0.94, "שונות"),
            StockItem("2", "מלפפונים", 0.48, "שונות"),
            StockItem("3", "קוטג' 5% שומן", 0.88, "שונות"),
        ])
        self.storage.replace_stock_items("shufersal", [
            StockItem("9", "עלי תרד בייבי 200 גרם", 0.15, "שונות"),
        ])

    def _decide(self, term, candidates, store="tivtaam"):
        return autoresolve.decide(self.storage, _row(1, store, term, candidates))

    def test_a_product_they_buy_here_wins(self):
        d = self._decide("קוטג", ["קוטג' 5% שומן", "קוטג' 9%", "ממרח קוטג"])
        self.assertEqual(d.name, "קוטג' 5% שומן")
        self.assertEqual(d.basis, "bought_here")
        self.assertTrue(d.confident)

    def test_a_popular_staple_cannot_hijack_an_unrelated_term(self):
        # The original bug, pinned. פלפל אדום is bought in 94% of orders.
        d = self._decide("גבינה צהובה מגוררת", ["פלפל אדום", "גבינה צהובה מגוררת 200 גרם"])
        self.assertEqual(d.name, "גבינה צהובה מגוררת 200 גרם")

    def test_a_partial_match_is_decided_but_not_called_confident(self):
        d = self._decide("פלפל צילי", ["פלפל אדום", "רוטב צילי מתוק"])
        self.assertEqual(d.name, "פלפל אדום")
        self.assertEqual(d.basis, "bought_here")
        self.assertFalse(d.confident)
        self.assertIn("חלקית", d.detail)

    def test_the_other_chain_counts_when_this_one_has_nothing(self):
        d = self._decide("עלי תרד", ["עלי תרד בייבי 200 גרם", "תרד קפוא"])
        self.assertEqual(d.basis, "bought_other")

    def test_a_candidate_list_answering_nothing_is_said_so(self):
        # בייקון -> מצלמת EYEX4 was real. Saying "no candidate answers
        # this" is the honest outcome; asserting one would be worse.
        d = self._decide("בייקון", ["מצלמת EYEX4", "כבל USB"])
        self.assertEqual(d.basis, "no_match")
        self.assertFalse(d.confident)

    def test_a_price_controlled_item_wins_a_tie(self):
        d = self._decide("חלב", ["חלב סויה", "חלב 3% - מחיר בפיקוח"])
        self.assertEqual(d.basis, "controlled")

    def test_the_plainest_name_is_the_last_resort(self):
        d = self._decide("ריבה", ["ריבה", "ריבת חלב בטעם קרמל מיוחד"])
        self.assertEqual(d.name, "ריבה")
        self.assertEqual(d.basis, "shortest")

    def test_every_question_is_settled_without_asking(self):
        # The behaviour he rejected was leaving them open for him. Settled
        # means either a product or an explicit "nothing here matches" —
        # never a question, and never a name that answers nothing.
        for term, cands in (("משהו", ["א", "ב"]), ("ריבה", ["ריבה"])):
            d = self._decide(term, cands)
            self.assertTrue(d.index >= 0 or d.basis in ("no_match", "none"))

    def test_an_unmatched_term_names_no_product_at_all(self):
        # `בייקון -> מצלמת EYEX4` was written into preferences once.
        d = self._decide("בייקון", ["מצלמת EYEX4", "כבל USB"])
        self.assertEqual(d.index, -1)
        self.assertEqual(d.name, "")

    def test_no_candidates_at_all_is_not_a_crash(self):
        d = self._decide("משהו", [])
        self.assertEqual(d.basis, "none")
        self.assertEqual(d.index, -1)


class SummaryTests(unittest.TestCase):
    def test_uncertain_decisions_are_surfaced_not_buried(self):
        strong = autoresolve.Decision(1, "tivtaam", "קוטג", 0, "קוטג' 5%",
                                      "bought_here", "נקנה כאן", score=1.0)
        weak = autoresolve.Decision(2, "tivtaam", "בייקון", 0, "מצלמה",
                                    "no_match", "אף מועמד", score=0.0)
        text = autoresolve.format_summary([strong, weak])
        self.assertIn("2", text)
        self.assertIn("בייקון", text)
        self.assertIn("שווה מבט", text)

    def test_nothing_open_says_so(self):
        self.assertIn("אין", autoresolve.format_summary([]))


if __name__ == "__main__":
    unittest.main()


class FreshBeforeProcessedTests(unittest.TestCase):
    """A produce term must not resolve to the jar version of itself.

    Raised by Miri 2026-09-17 from reviewing the real order, and verified
    systematic on the Tiv Taam feed rather than treated as five unlucky
    cases: a bare produce term returns processed goods first, every time.
    שזיפים -> dried plums, תפוחים -> apple juice, אפרסק -> peach syrup,
    סלק -> horseradish sauce.

    It is why Liran asked for peppers and got pepper spread, asked for
    plums and got plum purée, and asked for beetroot *not* vacuum-packed
    and got vacuum-packed beetroot.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _decide(self, term, candidates):
        return autoresolve.decide(self.storage, _row(1, "tivtaam", term, candidates))

    def test_fruit_beats_the_puree(self):
        d = self._decide("שזיפים", ["מחית אורגנית שזיפים", "שזיפים טריים"])
        self.assertEqual(d.name, "שזיפים טריים")

    def test_vegetable_beats_the_spread(self):
        d = self._decide("פלפלים", ["ממרח פלפלים", "פלפלים 4 העונות במשקל"])
        self.assertEqual(d.name, "פלפלים 4 העונות במשקל")

    def test_fresh_beats_the_vacuum_pack(self):
        # She wrote "לא באריזת ואקום" and got exactly that.
        d = self._decide("סלק", ["סלק אדום מקולף ומבושל", "סלק"])
        self.assertEqual(d.name, "סלק")

    def test_fruit_beats_the_juice(self):
        d = self._decide("תפוחים", ["מיץ תפוחים גרניני 1 ליטר", "תפוחים"])
        self.assertEqual(d.name, "תפוחים")

    def test_granola_beats_granola_cookies(self):
        d = self._decide("גרנולה", ["עוגיות גרנולה", "גרנולה פירות 500 גרם"])
        self.assertEqual(d.name, "גרנולה פירות 500 גרם")

    def test_asking_for_the_processed_form_still_works(self):
        # The mirror error this must not introduce: "מיץ לימון" and
        # "רסק עגבניות" are legitimate requests for the processed thing.
        self.assertEqual(
            self._decide("מיץ לימון", ["מיץ לימון", "לימון"]).name, "מיץ לימון")
        self.assertEqual(
            self._decide("רסק עגבניות", ["רסק עגבניות", "עגבניות"]).name,
            "רסק עגבניות")

    def test_only_processed_candidates_is_not_a_reason_to_refuse(self):
        # If the shop genuinely sells no fresh version, the processed one
        # is the honest answer — better than adding nothing.
        d = self._decide("שזיפים", ["מחית אורגנית שזיפים", "ריבת שזיפים"])
        self.assertGreaterEqual(d.index, 0)
