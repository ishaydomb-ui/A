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


class NamesPeopleActuallyUse(unittest.TestCase):
    """Ishay types the mall the way he says it, not the canonical name.

    Every phrasing here is one he or a reasonable person would send, and
    the module is only useful if all of them land.
    """

    RAMAT_AVIV = "קניון רמת אביב, תל אביב"
    DIZENGOFF = "דיזנגוף סנטר, תל אביב"
    AZRIELI = "קניון עזריאלי, תל אביב"
    GLILOT = "ביג פאשן גלילות"
    SEVEN = "קניון שבעת הכוכבים, הרצליה"
    TLV = "TLV פאשן מול (גינדי), תל אביב"

    def test_a_whole_sentence_resolves(self):
        self.assertEqual(malls.resolve("יש לי הנחה בקניון רמת אביב"), self.RAMAT_AVIV)
        self.assertEqual(malls.resolve("אילו חנויות בהנחה בקניון עזריאלי"), self.AZRIELI)
        self.assertEqual(malls.resolve("מה יש לי ברמת אביב"), self.RAMAT_AVIV)
        self.assertEqual(malls.resolve("אני בגלילות"), self.GLILOT)

    def test_hebrew_prefixes_do_not_block_a_match(self):
        """ב/ה on the front of a word is not a different word."""
        for query in ("רמת אביב", "ברמת אביב", "הקניון ברמת אביב", "קניון רמת אביב"):
            self.assertEqual(malls.resolve(query), self.RAMAT_AVIV, query)

    def test_a_prefix_letter_inside_an_alias_is_not_stripped(self):
        """שבעת begins with ש, an inseparable prefix. An earlier version
        stripped prefixes from the aliases too and broke this."""
        for query in ("שבעת הכוכבים", "בשבעת הכוכבים הרצליה", "קניון שבעת הכוכבים"):
            self.assertEqual(malls.resolve(query), self.SEVEN, query)

    def test_spelling_variants_and_short_forms(self):
        self.assertEqual(malls.resolve("דיזינגוף סנטר"), self.DIZENGOFF)
        self.assertEqual(malls.resolve("דיזנגוף"), self.DIZENGOFF)
        self.assertEqual(malls.resolve("7 הכוכבים"), self.SEVEN)
        self.assertEqual(malls.resolve("ביג גלילות"), self.GLILOT)
        self.assertEqual(malls.resolve("גינדי"), self.TLV)

    def test_latin_transliterations(self):
        self.assertEqual(malls.resolve("ramat aviv"), self.RAMAT_AVIV)
        self.assertEqual(malls.resolve("dizengoff center"), self.DIZENGOFF)
        self.assertEqual(malls.resolve("azrieli"), self.AZRIELI)
        self.assertEqual(malls.resolve("big glilot"), self.GLILOT)
        self.assertEqual(malls.resolve("tlv fashion mall"), self.TLV)
        self.assertEqual(malls.resolve("seven stars"), self.SEVEN)

    def test_a_contradicting_city_refuses_rather_than_guessing(self):
        """The chains are a brand; the mall is a place. We hold only the
        Tel Aviv Azrieli, so a Haifa question must not answer with it."""
        self.assertEqual(malls.resolve("קניון עזריאלי חיפה"), "")
        self.assertEqual(malls.resolve("עזריאלי ירושלים"), "")
        self.assertEqual(malls.resolve("שבעת הכוכבים אילת"), "")

    def test_an_unknown_mall_returns_nothing(self):
        # קניון איילון was the example here until 2026-09-14, when Ishay
        # asked for it to be mapped. Replaced with malls that really are
        # unmapped, so the test keeps testing what it is named after.
        for query in ("יש לי הנחה בקניון מלחה", "קניון הזהב", "בסופר", ""):
            self.assertEqual(malls.resolve(query), "", query)

    def test_chains_for_query_pairs_the_mall_with_its_chains(self):
        rows = [{"chainID": "1", "חנות": "פוקס", "סניף": "פוקס",
                 "כתובת": "איינשטיין 40 תל אביב - יפו"}]
        name, found = malls.chains_for_query("יש לי הנחה בקניון רמת אביב", rows)
        self.assertEqual(name, self.RAMAT_AVIV)
        self.assertEqual([c[0] for c in found], ["פוקס"])

    def test_an_unresolvable_query_yields_no_mall_and_no_chains(self):
        self.assertEqual(malls.chains_for_query("קניון הזהב", []), ("", []))


class Api(unittest.TestCase):
    def test_an_unknown_mall_returns_nothing_rather_than_raising(self):
        self.assertEqual(malls.chains_in("קניון שלא קיים"), [])

    def test_a_chain_appears_once_even_with_two_units_in_the_mall(self):
        rows = [
            {"chainID": "1", "חנות": "פוקס", "סניף": "פוקס א", "כתובת": "שדרות שבעת הכוכבים 8 הרצליה"},
            {"chainID": "1", "חנות": "פוקס", "סניף": "פוקס ב", "כתובת": "שבעת הכוכבים 8 הרצליה"},
        ]
        self.assertEqual(len(malls.chains_in("קניון שבעת הכוכבים, הרצליה", rows)), 1)


class AyalonIsRamatGanAndThatIsFine(unittest.TestCase):
    """Added on Ishay's instruction 2026-09-14: "תוסיף למרות שהוא טכנית
    ברמת גן". A municipal boundary is not a fact about where anyone
    goes."""

    def test_every_way_he_might_name_it(self):
        for query in ("קניון איילון", "קניון אילון", "איילון",
                      "מה יש לי בקניון איילון", "ayalon mall",
                      "בקניון איילון ברמת גן"):
            with self.subTest(query):
                self.assertEqual(malls.resolve(query), "קניון איילון, רמת גן")

    def test_naming_ramat_gan_does_not_veto_its_own_mall(self):
        # A city in the question vetoes malls elsewhere, so a city with a
        # mall must be in _CITIES or the question matches nothing.
        self.assertIn("רמת גן", malls._CITIES)

    def test_a_tel_aviv_question_is_not_answered_with_ayalon(self):
        self.assertNotEqual(
            malls.resolve("קניון בתל אביב"), "קניון איילון, רמת גן"
        )

    def test_the_other_ayalon_streets_are_not_this_mall(self):
        """עמק איילון is in Shoham, פנחס איילון in Holon, נחל איילון in
        Tsur Yitzhak — three streets carrying the word."""
        rows = [
            {"חנות": "חנות א", "סניף": "שוהם", "כתובת": "עמק איילון 30 שוהם"},
            {"חנות": "חנות ב", "סניף": "חולון", "כתובת": "פנחס איילון 13 חולון"},
            {"חנות": "חנות ג", "סניף": "רמת גן", "כתובת": "אבא הלל 301 רמת גן"},
        ]
        _, chains = malls.chains_for_query("קניון איילון", rows)
        self.assertEqual([c[0] for c in chains], ["חנות ג"])

    def test_the_rest_of_abba_hillel_street_is_not_the_mall(self):
        # 43 of 46 rows on that street are at 301; the street runs on.
        rows = [
            {"חנות": "במספר 301", "סניף": "", "כתובת": "דרך אבא הלל 301 רמת גן"},
            {"חנות": "במספר 16", "סניף": "", "כתובת": "אבא הילל  16 רמת גן"},
            {"חנות": "במספר 101", "סניף": "", "כתובת": "דרך אבא הלל 101 רמת גן"},
        ]
        _, chains = malls.chains_for_query("איילון", rows)
        self.assertEqual([c[0] for c in chains], ["במספר 301"])

    def test_the_row_that_names_the_complex_with_no_number_is_kept(self):
        # "קניון איילון  0 רמת גן" — a house number of 0 would otherwise
        # be refused by the number rule.
        rows = [{"חנות": "Carter's", "סניף": "", "כתובת": "קניון איילון  0 רמת גן"}]
        _, chains = malls.chains_for_query("קניון איילון", rows)
        self.assertEqual([c[0] for c in chains], ["Carter's"])


if __name__ == "__main__":
    unittest.main()
