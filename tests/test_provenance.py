"""Preference provenance and explicit rejections — Phase 8 (2026-09-17).

Every remembered choice used to be written as if it were the household's
word; 410 of 782 were the resolver's own guesses from one afternoon. Now
a write carries its source and never downgrades, and an explicit "not
that one" is a durable rejection the resolver cannot hand back.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import autoresolve
from grocery_bot.models import CartAddResult, PlanTerm
from grocery_bot.orchestrator import add_terms_to_cart
from grocery_bot.storage import Storage


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))


class RankTests(Base):
    def test_a_search_hit_cannot_overwrite_a_human_choice(self):
        self.storage.remember_choice("tivtaam", "חלב", "H", "חלב תנובה 3%", source="human")
        self.assertFalse(self.storage.remember_choice("tivtaam", "חלב", "S", "חלב יטבתה", source="search"))
        self.assertEqual(self.storage.preferred_for("tivtaam", "חלב")["product_code"], "H")

    def test_an_inferred_pick_cannot_overwrite_a_purchase(self):
        self.storage.remember_choice("tivtaam", "חלב", "P", "חלב", source="purchase")
        self.assertFalse(self.storage.remember_choice("tivtaam", "חלב", "I", "חלב אחר", source="inferred"))
        self.assertEqual(self.storage.preferred_for("tivtaam", "חלב")["source"], "purchase")

    def test_a_human_choice_overwrites_anything(self):
        self.storage.remember_choice("tivtaam", "חלב", "P", "חלב", source="purchase")
        self.assertTrue(self.storage.remember_choice("tivtaam", "חלב", "H", "חלב אחר", source="human"))
        pref = self.storage.preferred_for("tivtaam", "חלב")
        self.assertEqual((pref["product_code"], pref["source"]), ("H", "human"))

    def test_reconfirming_the_same_product_bumps_evidence(self):
        self.storage.remember_choice("tivtaam", "חלב", "P", "חלב", source="search")
        self.storage.remember_choice("tivtaam", "חלב", "P", "חלב", source="purchase")
        self.storage.remember_choice("tivtaam", "חלב", "P", "חלב", source="search")
        pref = self.storage.preferred_for("tivtaam", "חלב")
        self.assertEqual(pref["evidence_count"], 3)
        self.assertEqual(pref["source"], "purchase")          # the best authority sticks

    def test_the_default_source_is_search(self):
        self.storage.remember_choice("tivtaam", "חלב", "S", "חלב")
        self.assertEqual(self.storage.preferred_for("tivtaam", "חלב")["source"], "search")

    def test_an_unknown_source_is_refused(self):
        with self.assertRaises(ValueError):
            self.storage.remember_choice("tivtaam", "חלב", "X", "חלב", source="guess")

    def test_an_old_row_without_provenance_reads_as_search(self):
        # Rows written before the migration carry the column default.
        from contextlib import closing
        with closing(self.storage._connect()) as conn:  # noqa: SLF001
            conn.execute("INSERT INTO preferred_products (store, term, product_code, product_name, chosen_at)"
                         " VALUES ('tivtaam', 'ישן', 'O', 'ישן', '2026-08-01')")
            conn.commit()
        self.assertEqual(self.storage.preferred_for("tivtaam", "ישן")["source"], "search")
        self.assertTrue(self.storage.remember_choice("tivtaam", "ישן", "N", "חדש", source="inferred"))


class RejectionTests(Base):
    def test_a_rejected_pair_is_never_remembered_again(self):
        self.storage.reject_product("tivtaam", "בייקון", "CAM", "מצלמת EYEX4", source="human")
        self.assertFalse(self.storage.remember_choice("tivtaam", "בייקון", "CAM", "מצלמת EYEX4", source="inferred"))
        self.assertFalse(self.storage.remember_choice("tivtaam", "בייקון", "CAM", "מצלמת EYEX4", source="human"))
        self.assertIsNone(self.storage.preferred_for("tivtaam", "בייקון"))

    def test_rejecting_the_current_choice_removes_it(self):
        self.storage.remember_choice("tivtaam", "בייקון", "CAM", "מצלמת EYEX4", source="inferred")
        self.storage.reject_product("tivtaam", "בייקון", "CAM", source="human")
        self.assertIsNone(self.storage.preferred_for("tivtaam", "בייקון"))
        self.assertTrue(self.storage.is_rejected("tivtaam", "בייקון", "CAM"))

    def test_a_different_product_for_the_same_term_is_unaffected(self):
        self.storage.reject_product("tivtaam", "בייקון", "CAM", source="human")
        self.assertTrue(self.storage.remember_choice("tivtaam", "בייקון", "REAL", "בייקון הודו", source="inferred"))

    def test_rejection_is_per_store(self):
        self.storage.reject_product("tivtaam", "בייקון", "CAM", source="human")
        self.assertFalse(self.storage.is_rejected("shufersal", "בייקון", "CAM"))

    def test_only_known_sources_are_accepted(self):
        with self.assertRaises(ValueError):
            self.storage.reject_product("tivtaam", "בייקון", "CAM", source="guess")

    def test_an_empty_code_rejects_nothing(self):
        self.storage.reject_product("tivtaam", "בייקון", "", source="human")
        self.assertEqual(self.storage.list_rejections(), [])


class _Adapter:
    name = "tivtaam"

    def __init__(self, cards):
        self.cards = cards
        self.specific = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ensure_session(self):
        return True

    def search_and_add(self, term, quantity=1):
        return CartAddResult(item_name=term, store=self.name, status="ambiguous",
                             candidates=[c["name"] for c in self.cards],
                             candidate_cards=list(self.cards))

    def add_specific_product(self, label, quantity=1, product_code="", search_term=""):
        self.specific.append(product_code)
        return CartAddResult(item_name=label, store=self.name, status="added",
                             product_code=product_code, verification="verified")


class ResolverExclusionTests(Base):
    def test_a_rejected_candidate_is_not_picked_even_if_it_was_bought_before(self):
        # The resolver prefers a product with a purchase record; a
        # rejection outranks that.
        self.storage.remember_choice("tivtaam", "משהו אחר", "OLD", "חלב תנובה 1 ליטר", source="purchase")
        self.storage.reject_product("tivtaam", "חלב", "OLD", source="human")
        ad = _Adapter([{"code": "OLD", "name": "חלב תנובה 1 ליטר"}, {"code": "NEW", "name": "חלב יטבתה 1 ליטר"}])
        add_terms_to_cart(self.storage, {"tivtaam": lambda: ad}, [PlanTerm("חלב", 1, "adhoc", "1")])
        self.assertNotIn("OLD", ad.specific)

    def test_autoresolve_skips_a_rejected_candidate(self):
        self.storage.reject_product("tivtaam", "חלב", "OLD", source="human")
        row = {"id": 1, "store": "tivtaam", "original_term": "חלב",
               "candidates": '["חלב תנובה"]', "candidate_cards": '[{"code": "OLD", "name": "חלב תנובה"}]'}
        decision = autoresolve.decide(self.storage, row)
        self.assertEqual(decision.index, -1)


if __name__ == "__main__":
    unittest.main()
