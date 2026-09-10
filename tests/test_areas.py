"""Neighbourhood lookup — the question a mall name cannot answer.

Ishay asked Miri "ומחוץ לקניון, באזור הכללי של שכונת רמת אביב?" and she
correctly said she could only check a named mall or a named chain. The
cause was the data: of 6,136 harvested addresses, four contain the
string "רמת אביב" and three are the mall itself.
"""
import unittest

from grocery_bot import areas

GEO = {
    "איינשטיין 40 תל אביב - יפו": {"lat": 32.1123, "lon": 34.7947,
                                    "neighbourhood": "רמת-אביב", "city": "תל אביב"},
    "ברודצקי 43 תל אביב - יפו": {"lat": 32.1100, "lon": 34.7980,
                                  "neighbourhood": "רמת-אביב", "city": "תל אביב"},
    "דיזנגוף 50 תל אביב - יפו": {"lat": 32.0755, "lon": 34.7755,
                                  "neighbourhood": "הצפון הישן - החלק הדרומי",
                                  "city": "תל אביב"},
    "ארלוזורוב 1 תל אביב - יפו": {"lat": 32.0850, "lon": 34.7800,
                                   "neighbourhood": "הצפון הישן - החלק הצפוני",
                                   "city": "תל אביב"},
    "חבר הלאוומים 1 תל אביב - יפו": {},   # a real typo in the harvest
}

ROWS = [
    {"chainID": "1", "חנות": "פוקס", "סניף": "פוקס רמת אביב",
     "כתובת": "איינשטיין 40 תל אביב - יפו"},
    {"chainID": "2", "חנות": "פיצה עגבניה", "סניף": "עגבניה",
     "כתובת": "ברודצקי 43 תל אביב - יפו"},
    {"chainID": "3", "חנות": "גולף", "סניף": "גולף דיזנגוף",
     "כתובת": "דיזנגוף 50 תל אביב - יפו"},
    {"chainID": "4", "חנות": "סבון", "סניף": "סבון ארלוזורוב",
     "כתובת": "ארלוזורוב 1 תל אביב - יפו"},
    {"chainID": "5", "חנות": "רשת נעלמת", "סניף": "x",
     "כתובת": "חבר הלאוומים 1 תל אביב - יפו"},
]


class TheHyphenCase(unittest.TestCase):
    """OSM writes "רמת-אביב"; a person types "רמת אביב". Without folding
    the hyphen, the one neighbourhood actually asked about never
    matches."""

    def test_a_space_matches_the_hyphenated_name(self):
        self.assertEqual(areas.resolve_areas("רמת אביב", geo=GEO), ["רמת-אביב"])

    def test_the_whole_sentence_from_the_real_question_resolves(self):
        self.assertEqual(
            areas.resolve_areas("ומחוץ לקניון, באזור הכללי של שכונת רמת אביב?", geo=GEO),
            ["רמת-אביב"],
        )

    def test_filler_words_do_not_prevent_a_match(self):
        for query in ("שכונת רמת אביב", "מה יש לי באזור רמת אביב",
                      "אילו חנויות בהנחה ברמת אביב"):
            self.assertEqual(areas.resolve_areas(query, geo=GEO), ["רמת-אביב"], query)


class BroadAndNarrowAreas(unittest.TestCase):
    def test_a_broad_name_returns_every_part_it_covers(self):
        """OSM splits הצפון הישן into two parts. Someone asking about the
        neighbourhood means both, and picking one would hide half."""
        found = areas.resolve_areas("הצפון הישן", geo=GEO)
        self.assertEqual(len(found), 2)
        self.assertIn("הצפון הישן - החלק הדרומי", found)

    def test_naming_a_part_returns_only_that_part(self):
        self.assertEqual(
            areas.resolve_areas("הצפון הישן - החלק הדרומי", geo=GEO),
            ["הצפון הישן - החלק הדרומי"],
        )

    def test_chains_in_area_accepts_several_areas(self):
        found = areas.chains_in_area(
            ["הצפון הישן - החלק הדרומי", "הצפון הישן - החלק הצפוני"], ROWS, geo=GEO)
        self.assertEqual({c[0] for c in found}, {"גולף", "סבון"})

    def test_an_unknown_area_returns_nothing_rather_than_guessing(self):
        self.assertEqual(areas.resolve_areas("קריית שלום", geo=GEO), [])
        self.assertEqual(areas.resolve_areas("", geo=GEO), [])


class UnplacedAddressesAreDeclared(unittest.TestCase):
    """An address that failed to geocode is invisible to this module, so
    the gap has to be stated rather than absorbed."""

    def test_coverage_counts_the_misses(self):
        cover = areas.coverage(geo=GEO)
        self.assertEqual(cover["looked_up"], 5)
        self.assertEqual(cover["placed"], 4)
        self.assertEqual(cover["missing"], 1)

    def test_a_shop_at_an_unplaced_address_is_simply_absent(self):
        found = areas.chains_in_area("רמת-אביב", ROWS, geo=GEO)
        self.assertNotIn("רשת נעלמת", {c[0] for c in found})


class NearAPoint(unittest.TestCase):
    def test_only_chains_inside_the_radius_come_back(self):
        near = areas.chains_near(32.1123, 34.7947, ROWS, radius_km=1.0, geo=GEO)
        self.assertEqual({c[0] for c in near}, {"פוקס", "פיצה עגבניה"})

    def test_results_are_nearest_first(self):
        near = areas.chains_near(32.1123, 34.7947, ROWS, radius_km=5.0, geo=GEO)
        self.assertEqual([round(r[3], 2) for r in near], sorted(round(r[3], 2) for r in near))

    def test_a_tiny_radius_excludes_everything_but_the_spot(self):
        near = areas.chains_near(32.1123, 34.7947, ROWS, radius_km=0.05, geo=GEO)
        self.assertEqual({c[0] for c in near}, {"פוקס"})


class KnownAreas(unittest.TestCase):
    def test_areas_are_derived_from_the_data_not_hand_listed(self):
        """A curated list drifts from the harvest and starts refusing
        places that are present."""
        found = areas.known_areas(geo=GEO)
        self.assertIn("רמת-אביב", found)
        self.assertEqual(len(found), 3)

    def test_a_missing_cache_is_empty_not_an_error(self):
        self.assertEqual(areas.known_areas(geo={}), [])
        self.assertEqual(areas.resolve_areas("רמת אביב", geo={}), [])


if __name__ == "__main__":
    unittest.main()
