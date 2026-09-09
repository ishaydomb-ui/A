"""Mall assignment must reject the near-misses, not just accept the hits.

Every case here is a row that actually appeared in the 2026-09-09
behatsdaa harvest and that name-based matching got wrong. The accepting
tests are the easy half; the rejecting ones are the reason the module
exists.
"""
import unittest

from grocery_bot import malls


class AddressBeatsName(unittest.TestCase):
    def test_a_branch_named_for_a_mall_elsewhere_is_not_admitted(self):
        """"עזריאלי" in the branch name, Holon in the address."""
        self.assertEqual(
            malls.mall_of("גולדה מאיר 7 חולון", "תיק התיקים קניון עזריאלי חולון"), ""
        )
        self.assertEqual(
            malls.mall_of("משה פלימן 4 חיפה", "נייק - קניון עזריאלי חיפה"), ""
        )

    def test_a_neighbouring_site_sharing_the_place_name_stays_separate(self):
        """קניון פי גלילות is not ביג פאשן גלילות."""
        self.assertEqual(
            malls.mall_of("קניון פי גלילות  הרצליה", "אופטיקנה מתחם פי גלילות"), ""
        )

    def test_a_row_whose_name_says_glilot_but_sits_elsewhere_is_rejected(self):
        self.assertEqual(malls.mall_of("הרב שלום נגר 1 הוד השרון", "סבון גלילות"), "")

    def test_the_same_street_name_in_another_city_is_rejected(self):
        """שבעת הכוכבים is a street in Eilat as well as a mall in Herzliya."""
        self.assertEqual(malls.mall_of("קניון שבעת הכוכבים אילת", "ריקושט אילת"), "")


class DirtyAddressesStillResolve(unittest.TestCase):
    def test_street_prefix_and_house_number_variants_agree(self):
        expected = "קניון שבעת הכוכבים, הרצליה"
        for address in ("שדרות שבעת הכוכבים 8 הרצליה",
                        "שד' שבעת הכוכבים 8 הרצליה",
                        "שבעת הכוכבים 8 הרצליה",
                        "קניון שבעת הכוכבים  0 הרצליה",
                        "שדרות שבעת הכוכבים  הרצליה"):
            self.assertEqual(malls.mall_of(address), expected, address)

    def test_a_typo_in_the_street_name_still_resolves(self):
        """The harvest really does contain 'שבעת הכובים'."""
        self.assertEqual(
            malls.mall_of("שבעת הכובים  8 הרצליה", "שילב"),
            "קניון שבעת הכוכבים, הרצליה",
        )

    def test_the_glilot_complex_is_one_mall_under_several_descriptions(self):
        expected = "ביג פאשן גלילות"
        for address in ("ביג גלילות  רמת השרון", "מתחם ביג  גלילות",
                        "צומת גלילות  רמת השרון", "רב מכר 1001 רמת השרון",
                        "ביג פאשן  גלילות"):
            self.assertEqual(malls.mall_of(address), expected, address)

    def test_the_older_street_name_for_azrieli_still_resolves(self):
        self.assertEqual(
            malls.mall_of('דרך פ"ת 132 תל אביב - יפו'), "קניון עזריאלי, תל אביב"
        )


class Api(unittest.TestCase):
    def test_an_unknown_mall_returns_nothing_rather_than_raising(self):
        self.assertEqual(malls.chains_in("קניון שלא קיים"), [])

    def test_a_chain_appears_once_even_with_two_units_in_the_mall(self):
        rows = [
            {"chainID": "1", "חנות": "פוקס", "סניף": "פוקס א", "כתובת": "שדרות שבעת הכוכבים 8 הרצליה"},
            {"chainID": "1", "חנות": "פוקס", "סניף": "פוקס ב", "כתובת": "שבעת הכוכבים 8 הרצליה"},
        ]
        self.assertEqual(len(malls.chains_in("קניון שבעת הכוכבים, הרצליה", rows)), 1)


if __name__ == "__main__":
    unittest.main()
