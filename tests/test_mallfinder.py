"""Finding malls from coordinates, and auditing the ones declared by hand.

The join here is the whole correctness question, and the first version
got it wrong in a way that read as a finding: matching a cluster to a
mall by *chain overlap* put אבן גבירול 71 in Tel Aviv inside קניון שבעת
הכוכבים in Herzliya, because גולף and המשביר are in both and a chain
roster says nothing about where a building is.
"""
import unittest

from grocery_bot import mallfinder


def _row(chain, address, branch=""):
    return {"חנות": chain, "כתובת": address, "סניף": branch or chain}


def _geo(**pairs):
    return {addr: {"lat": lat, "lon": lon} for addr, (lat, lon) in pairs.items()}


class ClusterTests(unittest.TestCase):
    def test_chains_at_one_address_form_a_cluster(self):
        rows = [_row(f"chain{i}", "רחוב א 1") for i in range(9)]
        geo = {"רחוב א 1": {"lat": 32.0, "lon": 34.0}}
        found = mallfinder.clusters(min_chains=8, rows=rows, geo=geo)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].size, 9)

    def test_two_spellings_of_one_address_merge_when_they_geocode_together(self):
        rows = [_row(f"c{i}", "איינשטיין 40") for i in range(5)]
        rows += [_row(f"d{i}", "אינשטיין 40") for i in range(4)]
        geo = {"איינשטיין 40": {"lat": 32.1122, "lon": 34.7958},
               "אינשטיין 40": {"lat": 32.1122, "lon": 34.7958}}
        found = mallfinder.clusters(min_chains=8, rows=rows, geo=geo)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].size, 9)

    def test_the_same_chain_twice_counts_once(self):
        # A chain with two branches in one mall is one shop for this.
        rows = [_row("גולף", "רחוב א 1"), _row("גולף", "רחוב א 1")]
        rows += [_row(f"c{i}", "רחוב א 1") for i in range(7)]
        geo = {"רחוב א 1": {"lat": 32.0, "lon": 34.0}}
        self.assertEqual(mallfinder.clusters(8, rows, geo)[0].size, 8)

    def test_a_small_parade_of_shops_is_not_a_cluster(self):
        rows = [_row(f"c{i}", "רחוב ב 2") for i in range(4)]
        geo = {"רחוב ב 2": {"lat": 32.0, "lon": 34.0}}
        self.assertEqual(mallfinder.clusters(8, rows, geo), [])

    def test_an_address_without_coordinates_is_absent_not_guessed(self):
        rows = [_row(f"c{i}", "רחוב ללא קואורדינטות") for i in range(9)]
        self.assertEqual(mallfinder.clusters(8, rows, {}), [])

    def test_opposite_sides_of_a_city_do_not_merge(self):
        rows = [_row(f"c{i}", "צפון 1") for i in range(9)]
        rows += [_row(f"d{i}", "דרום 1") for i in range(9)]
        geo = {"צפון 1": {"lat": 32.11, "lon": 34.79},
               "דרום 1": {"lat": 32.05, "lon": 34.76}}
        self.assertEqual(len(mallfinder.clusters(8, rows, geo)), 2)


class LiveAuditTests(unittest.TestCase):
    """Against the real harvest, because the join bug was only visible there."""

    def setUp(self):
        self.findings = mallfinder.audit_known()
        if not self.findings:
            self.skipTest("no geocoded branch data on this machine")

    def test_a_tel_aviv_cluster_is_never_matched_to_a_herzliya_mall(self):
        for finding in self.findings:
            if not finding["matched_mall"]:
                continue
            in_tel_aviv = any("תל אביב" in a for a in finding["addresses"])
            if in_tel_aviv:
                self.assertNotIn("הרצליה", finding["matched_mall"],
                                 finding["addresses"][:1])

    def test_ramat_aviv_is_fully_covered_under_both_spellings(self):
        # The cluster view splits it in two; the mapping already declares
        # both spellings and loses nothing. The mapping is the right one.
        ramat = [f for f in self.findings if "רמת אביב" in (f["matched_mall"] or "")]
        self.assertTrue(ramat)
        for finding in ramat:
            self.assertEqual(finding["missing"], 0)

    def test_every_matched_cluster_shares_an_address_with_its_mall(self):
        from grocery_bot import malls

        rows = malls.load_rows()
        for finding in self.findings:
            mall = finding["matched_mall"]
            if not mall:
                continue
            claimed = {t[2].strip() for t in malls.chains_in(mall, rows) if len(t) > 2}
            self.assertTrue(set(finding["addresses"]) & claimed, mall)

    def test_the_report_separates_gaps_from_candidates(self):
        text = mallfinder.format_audit(self.findings)
        self.assertIn("אשכול הוא ראיה, לא קניון", text)


if __name__ == "__main__":
    unittest.main()
