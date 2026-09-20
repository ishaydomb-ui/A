"""vNext Phase 1.5 — the semantic layer and resolver, on the real failures.

Every case here is a mapping the Phase 1 sample actually produced from
the household's data (a shower gel for a yogurt, a drink for a banana,
pickles for mini cucumbers, a vacuum beet for a fresh one) or a rule
the spec pins. The catalogue is synthetic and tiny; the rules are the
production ones.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import household_evidence as he
from grocery_bot import vnext_semantics as sem
from grocery_bot.storage import Storage
from grocery_bot.vnext_config import VNextConfig
from grocery_bot.vnext_resolver import ACCEPTABLE, EXACT, REJECTED, UNRESOLVED, check_line, resolve

CFG = VNextConfig()


def _status(term, product):
    vs = sem.violations(sem.parse_term(term), product)
    if any(v.hard for v in vs):
        return "rejected"
    return "substitute" if vs else "ok"


class CategorySanityTests(unittest.TestCase):
    def test_yogurt_never_matches_body_wash(self):
        self.assertEqual(_status("יוגורט Pro וניל", "תחליב רחצה יוגורט וניל"), "rejected")
        self.assertEqual(sem.category_of("תחליב רחצה יוגורט וניל"), sem.PERSONAL_CARE)
        rules = [v.rule for v in sem.violations(sem.parse_term("יוגורט Pro וניל"), "תחליב רחצה יוגורט וניל")]
        self.assertIn("category:non_food", rules)

    def test_yogurt_matches_a_real_vanilla_protein_yogurt(self):
        self.assertEqual(_status("יוגורט Pro וניל", "מולר פרוטאין יוגורט בטעם וניל 25 גרם חלבון 0% שומן 200 גרם"), "ok")

    def test_flavor_is_a_hard_constraint(self):
        self.assertEqual(_status("יוגורט Pro וניל", "יוגורט פרו עם שוקולד"), "rejected")

    def test_banana_never_matches_banana_drink_unless_beverage_requested(self):
        self.assertEqual(_status("בננה", "משקה בננה 1 ליטר"), "rejected")
        self.assertEqual(_status("בננה", "בננות"), "ok")
        # A beverage request may of course match a beverage.
        self.assertEqual(_status("משקה בננה", "משקה בננה 1 ליטר"), "ok")

    def test_fresh_beet_not_vacuum_packed_rejects_vacuum_beet(self):
        term = "סלק (לא באריזת ואקום)"
        self.assertEqual(_status(term, "סלק מבושל בואקום -אורגני"), "rejected")
        self.assertEqual(_status(term, "סלק בואקום 500 גר"), "rejected")
        self.assertEqual(_status(term, "סלק"), "ok")
        parsed = sem.parse_term(term)
        self.assertEqual(parsed.negatives, ["באריזת ואקום"])

    def test_mini_cucumbers_do_not_match_pickled_cucumbers(self):
        self.assertEqual(_status("מלפפונים מיני", "מלפפונים קטנים בחומץ"), "rejected")
        self.assertEqual(_status("מלפפונים מיני", "מלפפונים קטנים בחומץ560ג"), "rejected")
        self.assertEqual(_status("מלפפונים מיני", "מלפפון בייבי ארוז"), "ok")

    def test_no_added_sugar_qualifier_preserved(self):
        term = "גרנולה ללא תוספת סוכר"
        self.assertEqual(sem.parse_term(term).free_of, ["סוכר"])
        self.assertEqual(_status(term, "גרנולה עם פירות טרופיים"), "rejected")      # says nothing about sugar
        self.assertEqual(_status(term, "עוגיות גרנולה קינמון ללס"), "rejected")     # cookies, not granola
        self.assertEqual(_status(term, "גרנולה ללא תוספת סוכר 500 גרם"), "ok")
        self.assertEqual(_status(term, "גרנולה ללא סוכר 500 גר"), "ok")

    def test_fresh_produce_does_not_resolve_to_processed_product(self):
        for term, product in (("גזר", "מיץ גזר"), ("בננה", "מחית תפוח עץ ובננה"), ("שזיפים", "שזיפים מיובשים"),
                              ("עגבניות", "רסק עגבניות"), ("פלפלים", "ממרח פלפלים"), ("תפוח", "עוגת תפוחים")):
            self.assertEqual(_status(term, product), "rejected", (term, product))
        self.assertEqual(_status("גזר", "גזר ארוז"), "ok")
        self.assertEqual(_status("שזיפים", "שזיף סנטה רוזה"), "ok")

    def test_lexical_overlap_never_overrides_category(self):
        self.assertEqual(_status("חלב", "שוקולד חלב"), "rejected")
        self.assertEqual(_status("חלב עמיד", "מולר פרוטאין יוגורט 25 גרם חלבון"), "rejected")   # חלב is not in חלבון
        self.assertEqual(_status("מלח", "עגבניות שרי במלח"), "rejected")

    def test_variety_request_collapsing_to_one_color_is_a_substitute_not_a_match(self):
        self.assertEqual(_status("עגבניות שרי במגוון צבעים", "עגבניות שרי צהוב"), "substitute")
        self.assertEqual(_status("פלפלים בכמה צבעים", "פלפל אדום"), "substitute")
        self.assertEqual(_status("פלפלים בכמה צבעים", "פלפל צבעוני"), "ok")

    def test_stated_qualifier_contradicted_is_rejected_but_missing_is_only_unverified(self):
        parsed = sem.parse_term("לחם חיטה מלא דגנית עין בר")
        self.assertEqual(parsed.brand, ["דגנית עין בר"])
        self.assertEqual(_status("לחם חיטה מלא דגנית עין בר", "לחם לבן"), "rejected")
        self.assertEqual(_status("לחם חיטה מלא דגנית עין בר", "לחם חיטה מלא"), "ok")
        self.assertIn("brand:דגנית עין בר", sem.unverified(parsed, "לחם חיטה מלא"))
        self.assertEqual(sem.unverified(parsed, "לחם חיטה מלא דגנית עין בר"), [])


class ResolverBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _shelf(self, store, rows):
        self.storage.record_store_prices(store, [
            {"barcode": code, "name": name, "price": 9.9, "observed_at": "2026-09-20", "source": "feed"}
            for code, name in rows])

    def _resolve(self, term):
        evidence = he.gather(self.storage).by_term().get(he.key_for(term), [])
        return resolve(self.storage, he.key_for(term), evidence, CFG, purchases_by_code={}, raw=term)


class ResolverTests(ResolverBase):
    def test_remembered_wrong_mapping_is_rejected_and_a_real_product_found(self):
        self.storage.add_adhoc_request(text="יוגורט Pro וניל", requested_by="לירן")
        self.storage.remember_choice("shufersal", "יוגורט Pro וניל", "P_1", "תחליב רחצה יוגורט וניל", source="search")
        self._shelf("tivtaam", [("1", "מולר פרוטאין יוגורט בטעם וניל 200 גרם"), ("2", "יוגורט עם תות 3%")])
        r = self._resolve("יוגורט Pro וניל")
        self.assertEqual(r.status, EXACT)
        self.assertEqual(r.chosen.product_name, "מולר פרוטאין יוגורט בטעם וניל 200 גרם")
        rejected = [c for c in r.candidates if c.status == REJECTED]
        self.assertTrue(any(c.product_name == "תחליב רחצה יוגורט וניל" for c in rejected))
        self.assertIn("provenance", r.to_dict())
        self.assertTrue(r.qualifiers_checked)

    def test_human_confirmation_wins_but_a_violating_one_is_flagged_not_hidden(self):
        self.storage.add_adhoc_request(text="בננה", requested_by="ישי")
        self._shelf("tivtaam", [("1", "בננות"), ("2", "משקה בננה 1 ליטר")])
        self.storage.add_vnext_product_confirmation("tivtaam", "בננה", "1", "בננות", "explicit_statement", "ישי")
        r = self._resolve("בננה")
        self.assertEqual(r.status, EXACT)
        self.assertEqual(r.confidence, 1.0)
        self.assertEqual(r.chosen.product_code, "1")
        # A human confirmation of the *drink* is reported as a violation, never silently applied.
        self.storage.add_vnext_product_confirmation("tivtaam", "בננה", "2", "משקה בננה 1 ליטר", "later_correction", "ישי")
        r2 = self._resolve("בננה")
        self.assertIn("violates", r2.human_flagged)
        self.assertEqual(r2.chosen.product_code, "1")

    def test_only_substitutes_leaves_the_product_unresolved(self):
        self.storage.add_adhoc_request(text="פלפלים בכמה צבעים", requested_by="לירן")
        self._shelf("tivtaam", [("1", "פלפל אדום"), ("2", "פלפל ירוק")])
        r = self._resolve("פלפלים בכמה צבעים")
        self.assertEqual(r.status, UNRESOLVED)
        self.assertIsNone(r.chosen)
        self.assertEqual({c.product_name for c in r.substitution_candidates}, {"פלפל אדום", "פלפל ירוק"})

    def test_every_candidate_violating_is_rejected_by_constraints(self):
        self.storage.add_adhoc_request(text="סלק (לא באריזת ואקום)", requested_by="לירן")
        self._shelf("tivtaam", [("1", "סלק בואקום 500 גר"), ("2", "סלק מבושל בואקום")])
        r = self._resolve("סלק (לא באריזת ואקום)")
        self.assertEqual(r.status, REJECTED)
        self.assertIsNone(r.chosen)

    def test_purchases_raise_confidence_but_stay_inference(self):
        self.storage.add_adhoc_request(text="גזר", requested_by="לירן")
        self._shelf("tivtaam", [("1", "גזר ארוז")])
        evidence = he.gather(self.storage).by_term().get("גזר", [])
        low = resolve(self.storage, "גזר", evidence, CFG, purchases_by_code={}, raw="גזר")
        high = resolve(self.storage, "גזר", evidence, CFG, purchases_by_code={("tivtaam", "1"): 6}, raw="גזר")
        self.assertGreater(high.confidence, low.confidence)
        self.assertEqual(high.chosen.origin, he.Origin.INFERENCE)
        self.assertEqual(self.storage.list_preferences(), [])

    def test_check_line_statuses(self):
        p = sem.parse_term("חלב עמיד")
        self.assertEqual(check_line(p, "חלב עמיד 3% 1 ליטר")[0], EXACT)
        self.assertEqual(check_line(p, "חלב 3% קרטון מהדרין"), (ACCEPTABLE, ["qualifier:עמיד"]))
        self.assertEqual(check_line(p, "שוקולד חלב")[0], REJECTED)


if __name__ == "__main__":
    unittest.main()
