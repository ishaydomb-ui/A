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


class HouseNumbersSeparateAMallFromItsStreet(unittest.TestCase):
    """Three of the six malls sit on ordinary streets, where the street
    name alone sweeps in unrelated shops."""

    def test_dizengoff_center_is_number_50_not_the_street(self):
        self.assertEqual(malls.mall_of("דיזנגוף 50 תל אביב - יפו"), "דיזנגוף סנטר, תל אביב")
        for street_shop in ("דיזנגוף 116 תל אביב - יפו",
                            "דיזנגוף 122 תל אביב - יפו",
                            "דיזנגוף 269 תל אביב - יפו"):
            self.assertEqual(malls.mall_of(street_shop), "", street_shop)

    def test_both_spellings_of_dizengoff_resolve(self):
        self.assertEqual(
            malls.mall_of("דיזינגוף 50 תל אביב - יפו"), "דיזנגוף סנטר, תל אביב"
        )

    def test_a_row_naming_the_complex_without_a_number_is_still_admitted(self):
        self.assertEqual(
            malls.mall_of("דיזינגוף סנטר  תל אביב - יפו"), "דיזנגוף סנטר, תל אביב"
        )

    def test_ramat_aviv_is_einstein_40_and_not_68(self):
        self.assertEqual(
            malls.mall_of("איינשטיין 40 תל אביב - יפו"), "קניון רמת אביב, תל אביב"
        )
        self.assertEqual(malls.mall_of("איינשטיין 68 תל אביב - יפו"), "")

    def test_neighbouring_centres_are_not_the_ramat_aviv_mall(self):
        """מרכז שוסטר and ברודצקי are separate sites nearby."""
        for other in ("מרכז שוסטר רמת אביב 0 תל אביב - יפו",
                      "מרכז שוסטר תל אביב - יפו",
                      "ברודצקי 43 תל אביב - יפו"):
            self.assertEqual(malls.mall_of(other), "", other)

    def test_the_tlv_mall_spans_a_run_of_street_numbers(self):
        expected = "TLV פאשן מול (גינדי), תל אביב"
        for address in ("החשמונאים 88 88 תל אביב - יפו",
                        "החשמונאים 94 תל אביב - יפו",
                        "החשמונאים 96 תל אביב - יפו",
                        "החשמונאים  100 תל אביב - יפו",
                        "החשמונאים  132 תל אביב - יפו"):
            self.assertEqual(malls.mall_of(address), expected, address)

    def test_further_down_the_same_street_is_not_the_mall(self):
        self.assertEqual(malls.mall_of("החשמונאים 20 תל אביב - יפו"), "")

    def test_the_house_number_is_read_after_the_street_not_anywhere(self):
        """"פקטורי 54" as a chain name, and a repeated number, must not
        be mistaken for the house number."""
        self.assertEqual(
            malls.mall_of("החשמונאים 94 תל אביב - יפו", "פקטורי 54"),
            "TLV פאשן מול (גינדי), תל אביב",
        )

    def test_a_weizmann_street_elsewhere_is_not_a_tel_aviv_mall(self):
        self.assertEqual(malls.mall_of("ויצמן 207 כפר סבא"), "")


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
