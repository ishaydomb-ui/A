"""The read-only benefits-catalog seam Miri calls (grocery_bot/cli.py's
`benefits-catalog` / `benefits-branches`, backed by benefits_catalog.py).

Uses a temp BENEFITS_DATA_DIR with hand-written CSVs rather than the real
harvested data, so these tests do not depend on data/benefits/ existing
or its (gitignored, real-household) contents.
"""
import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

CATALOG_ROWS = [
    {
        "חנות": "רשת מקסיקנה", "תת-קטגוריה": "מסעדות ובתי קפה", "סטטוס": "לבדיקה",
        "אחוז מקס": "20", "תקרת הנחה כוללת ₪": "3000",
        "ארנקים": "מסעדות(20%/₪500); פייטר(15%/₪2500)", "אונליין": "לא",
        "קטגוריה": "מסעדות ובתי קפה", "אתר": "https://mexicana.co.il/",
        "ערים": "תל אביב - יפו; ראשון לציון",
    },
    {
        "חנות": "דומינוס פיצה אונליין", "תת-קטגוריה": "רכישה אונליין", "סטטוס": "לבדיקה",
        "אחוז מקס": "20", "תקרת הנחה כוללת ₪": "500",
        "ארנקים": "מסעדות(20%/₪500)", "אונליין": "כן",
        "קטגוריה": "רכישה אונליין", "אתר": "https://www.dominos.co.il/branches",
        "ערים": "",
    },
]

BRANCH_ROWS_A = [
    {"chainID": "1565", "חנות": "רשת מקסיקנה", "קטגוריה": "מסעדות", "אונליין": "לא",
     "סניף": "מקסיקנה - כפר סבא", "כתובת": "התעש 24 כפר סבא", "טלפון": "1700500993",
     "אתר": "https://mexicana.co.il/"},
]

BRANCH_ROWS_B = [
    # Same branch, re-captured by a second (overlapping) crawl file — must
    # dedupe to one row, not two.
    {"chainID": "1565", "חנות": "רשת מקסיקנה", "קטגוריה": "מסעדות", "אונליין": "לא",
     "סניף": "מקסיקנה - כפר סבא", "כתובת": "התעש 24 כפר סבא", "טלפון": "1700500993",
     "אתר": "https://mexicana.co.il/"},
    {"chainID": "2091", "חנות": "דומינוס פיצה אונליין", "קטגוריה": "רכישה אונליין",
     "אונליין": "כן", "סניף": "דומינוס פיצה אונליין", "כתובת": ". מיקומים שונים",
     "טלפון": "1700707070", "אתר": "https://www.dominos.co.il/branches"},
]


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class LoadAndSearchTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._dir.name)
        _write_csv(self.data_dir / "catalog_tagged.csv", CATALOG_ROWS)
        _write_csv(self.data_dir / "branches_a.csv", BRANCH_ROWS_A)
        _write_csv(self.data_dir / "branches_b.csv", BRANCH_ROWS_B)
        self._old_env = os.environ.get("BENEFITS_DATA_DIR")
        os.environ["BENEFITS_DATA_DIR"] = str(self.data_dir)

    def tearDown(self):
        self._dir.cleanup()
        if self._old_env is None:
            os.environ.pop("BENEFITS_DATA_DIR", None)
        else:
            os.environ["BENEFITS_DATA_DIR"] = self._old_env

    def test_load_catalog_returns_all_rows(self):
        from grocery_bot.benefits_catalog import load_catalog
        self.assertEqual(len(load_catalog()), 2)

    def test_search_catalog_matches_store_name(self):
        from grocery_bot.benefits_catalog import search_catalog
        hits = search_catalog("מקסיקנה")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["חנות"], "רשת מקסיקנה")

    def test_search_catalog_also_matches_category(self):
        from grocery_bot.benefits_catalog import search_catalog
        hits = search_catalog("רכישה אונליין")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["חנות"], "דומינוס פיצה אונליין")

    def test_search_catalog_empty_query_returns_nothing(self):
        # Distinguishes "no query" (caller should use load_catalog) from a
        # real search with no hits — an empty string must not be treated
        # as "match everything".
        from grocery_bot.benefits_catalog import search_catalog
        self.assertEqual(search_catalog(""), [])

    def test_branches_are_deduped_across_overlapping_crawl_files(self):
        # branches_a.csv and branches_b.csv both contain the Kfar Saba
        # Mexicana branch — the harvest's crawlers are resumable and
        # overlap by design, so this must collapse to one row.
        from grocery_bot.benefits_catalog import load_branches
        rows = load_branches()
        self.assertEqual(len(rows), 2)  # one Mexicana + one Domino's, not three

    def test_search_branches_matches_address(self):
        from grocery_bot.benefits_catalog import search_branches
        hits = search_branches("כפר סבא")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["חנות"], "רשת מקסיקנה")

    def test_missing_catalog_file_is_an_empty_list_not_a_crash(self):
        # Miri may call this before any harvest has run.
        from grocery_bot.benefits_catalog import load_catalog
        (self.data_dir / "catalog_tagged.csv").unlink()
        self.assertEqual(load_catalog(), [])


class FormattingTests(unittest.TestCase):
    def test_a_result_names_the_wallets_and_ceiling(self):
        from grocery_bot.benefits_catalog import format_catalog_rows
        text = format_catalog_rows(CATALOG_ROWS[:1], "מקסיקנה")
        self.assertIn("רשת מקסיקנה", text)
        self.assertIn("פייטר", text)
        self.assertIn("3000", text)

    def test_no_results_says_so_by_name(self):
        from grocery_bot.benefits_catalog import format_catalog_rows
        text = format_catalog_rows([], "משהו שלא קיים")
        self.assertIn("משהו שלא קיים", text)

    def test_empty_catalog_with_no_query_names_the_real_cause(self):
        # Distinct message: not "no results for X", but "nothing has been
        # harvested yet" — the caller should not conclude the store just
        # doesn't have a benefit.
        from grocery_bot.benefits_catalog import format_catalog_rows
        text = format_catalog_rows([], "")
        self.assertIn("קציר", text)

    def test_a_branch_result_shows_the_street_address(self):
        from grocery_bot.benefits_catalog import format_branch_rows
        text = format_branch_rows(BRANCH_ROWS_A, "כפר סבא")
        self.assertIn("התעש 24 כפר סבא", text)


class CliContractTests(unittest.TestCase):
    """Run through the real subprocess CLI, the way Miri actually calls it."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._dir.name)
        _write_csv(self.data_dir / "catalog_tagged.csv", CATALOG_ROWS)
        _write_csv(self.data_dir / "branches_a.csv", BRANCH_ROWS_A)
        self._tmpdb = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()
        self._tmpdb.cleanup()

    def _run(self, args):
        # Mirrors tests/test_cli_contract.py's _run: ONLY the env a real
        # caller would have, to prove neither command needs the token.
        return subprocess.run(
            [sys.executable, "-m", "grocery_bot.cli", *args],
            cwd=PROJECT,
            env={
                "GROCERY_BOT_DB_PATH": str(Path(self._tmpdb.name) / "t.sqlite3"),
                "BENEFITS_DATA_DIR": str(self.data_dir),
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
            },
            capture_output=True,
            text=True,
        )

    def test_benefits_catalog_needs_no_telegram_token(self):
        result = self._run(["benefits-catalog", "מקסיקנה"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("מקסיקנה", result.stdout)

    def test_benefits_branches_needs_no_telegram_token(self):
        result = self._run(["benefits-branches", "כפר סבא"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("התעש", result.stdout)

    def test_json_flag_returns_parseable_data(self):
        import json
        result = self._run(["benefits-catalog", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 2)

    def test_a_miss_is_exit_1_not_a_crash(self):
        result = self._run(["benefits-catalog", "לא קיים בכלל"])
        self.assertEqual(result.returncode, 1)

    def test_both_are_on_the_token_free_path(self):
        from grocery_bot import cli
        self.assertIn("benefits-catalog", cli._DB_ONLY_COMMANDS)
        self.assertIn("benefits-branches", cli._DB_ONLY_COMMANDS)


if __name__ == "__main__":
    unittest.main()


MAX_ROWS = [
    {
        "club": "מקס", "חנות": "שוקה - תל אביב", "הנחה%": "25.0",
        "קטגוריה": "מזון ומשקאות", "כתובת": "אלנבי, 83, תל אביב - יפו",
        "עיר": "תל אביב - יפו", "אזור": "תל אביב-יפו", "טלפון": "",
        "אתר": "https://example.co.il", "תיאור": "מסעדה", "עודכן": "2026-09-01T00:00:00",
        "business_id": "12345",
    },
]


class MultiClubTests(unittest.TestCase):
    """behatsdaa and MAX in one catalog, without flattening their shapes.

    The two clubs genuinely carry different fields — behatsdaa has wallets
    and a discount ceiling because it is a prepaid wallet; MAX has a branch
    address and no ceiling because a card-linked discount has no balance to
    cap. Merging them into one schema would invent data, so rows keep their
    own columns and carry a `club` to tell them apart.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._dir.name)
        # behatsdaa's rescued file sits in a subdirectory; harvested clubs
        # are written at the root. Both must be found.
        rescue = self.data_dir / "lab_rescue"
        rescue.mkdir()
        _write_csv(rescue / "catalog_tagged.csv", CATALOG_ROWS)
        _write_csv(self.data_dir / "max_catalog.csv", MAX_ROWS)
        self._old = os.environ.get("BENEFITS_DATA_DIR")
        os.environ["BENEFITS_DATA_DIR"] = str(self.data_dir)

    def tearDown(self):
        self._dir.cleanup()
        if self._old is None:
            os.environ.pop("BENEFITS_DATA_DIR", None)
        else:
            os.environ["BENEFITS_DATA_DIR"] = self._old

    def test_both_clubs_load_together(self):
        from grocery_bot.benefits_catalog import load_catalog
        rows = load_catalog()
        self.assertEqual(len(rows), len(CATALOG_ROWS) + len(MAX_ROWS))

    def test_behatsdaa_rows_are_labelled_even_though_their_file_has_no_club_column(self):
        from grocery_bot.benefits_catalog import load_catalog
        clubs = {r.get("club") for r in load_catalog()}
        self.assertIn("בהצדעה", clubs)
        self.assertIn("מקס", clubs)

    def test_search_finds_a_max_store(self):
        from grocery_bot.benefits_catalog import search_catalog
        hits = search_catalog("שוקה")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["club"], "מקס")

    def test_searching_a_club_name_is_not_a_club_filter(self):
        # Caught by this test rather than in production: "מקס" is a
        # substring of "מקסיקנה", so a substring search on the club name
        # returns a *different* club's merchant. `club` is therefore left
        # out of the searched fields, and filtering by club is an
        # exact-match job on the field itself.
        from grocery_bot.benefits_catalog import search_catalog
        hits = search_catalog("מקס")
        self.assertTrue(any(r["חנות"] == "רשת מקסיקנה" for r in hits),
                        "substring search should still match the merchant name")
        exact = [r for r in hits if r.get("club") == "מקס"]
        self.assertNotEqual(len(hits), len(exact),
                            "if these were equal the collision would be hidden")

    def test_search_finds_a_max_row_by_city(self):
        # City is a MAX-only column; searching it must not break on
        # behatsdaa rows that have no such field.
        from grocery_bot.benefits_catalog import search_catalog
        hits = search_catalog("תל אביב - יפו")
        self.assertTrue(any(r.get("club") == "מקס" for r in hits))

    def test_a_max_row_renders_its_percent_and_address(self):
        from grocery_bot.benefits_catalog import format_catalog_rows
        text = format_catalog_rows(MAX_ROWS, "שוקה")
        self.assertIn("25.0% הנחה", text)
        self.assertIn("אלנבי", text)
        self.assertIn("מקס", text)

    def test_a_behatsdaa_row_still_renders_wallets_and_ceiling(self):
        from grocery_bot.benefits_catalog import format_catalog_rows
        text = format_catalog_rows(CATALOG_ROWS[:1], "מקסיקנה")
        self.assertIn("פייטר", text)
        self.assertIn("3000", text)


class FreshnessTests(unittest.TestCase):
    """Data freshness must be visible, not something a caller has to know.

    behatsdaa data is a static September snapshot that cannot be
    refreshed (its login is not automated), and another bot consuming
    numbers that originate here must not mistake it for live data.
    """

    def test_freshness_names_every_club(self):
        from grocery_bot.benefits_catalog import freshness
        f = freshness()
        self.assertIn("בהצדעה", f)
        self.assertIn("מקס", f)

    def test_behatsdaa_is_flagged_as_not_refreshing(self):
        from grocery_bot.benefits_catalog import freshness
        self.assertIn("לא מתרענן", freshness()["בהצדעה"])

    def test_results_carry_the_as_of_note_for_the_club_shown(self):
        from grocery_bot.benefits_catalog import format_catalog_rows
        rows = [{"club": "בהצדעה", "חנות": "רשת מקסיקנה", "קטגוריה": "מסעדות"}]
        text = format_catalog_rows(rows, "מקסיקנה")
        self.assertIn("2026-09-03", text)
        self.assertIn("לא מתרענן", text)


class RelevanceRankingTests(unittest.TestCase):
    """Closest name matches rank first; incidental ones sink.

    Substring search cannot separate a merchant from a same-prefixed but
    unrelated one (Fox fashion vs "פוקס דרי ישראל") without an identity
    key the data lacks — but the real matches should still come first.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._dir.name)
        _write_csv(self.data_dir / "catalog_tagged.csv", [
            {"חנות": "פוקס", "תת-קטגוריה": "אופנה", "ארנקים": "רשתות בהצדעה(15%/₪1500)",
             "אונליין": "לא", "קטגוריה": "אופנה ולייף סטייל", "ערים": "תל אביב - יפו"},
            {"חנות": "פוקס אונליין", "תת-קטגוריה": "רכישה אונליין", "ארנקים": "פייטר(15%/₪2500)",
             "אונליין": "כן", "קטגוריה": "רכישה אונליין", "ערים": ""},
        ])
        _write_csv(self.data_dir / "max_catalog.csv", [
            {"club": "מקס", "חנות": "פוקס דרי ישראל - חולון", "הנחה%": "3.5",
             "קטגוריה": "לבית ולגן", "כתובת": "מוהליבר 43, חולון", "עיר": "חולון",
             "אזור": "מרכז", "טלפון": "", "אתר": "", "תיאור": "", "עודכן": "", "business_id": "9"},
        ])
        self._old = os.environ.get("BENEFITS_DATA_DIR")
        os.environ["BENEFITS_DATA_DIR"] = str(self.data_dir)

    def tearDown(self):
        self._dir.cleanup()
        if self._old is None:
            os.environ.pop("BENEFITS_DATA_DIR", None)
        else:
            os.environ["BENEFITS_DATA_DIR"] = self._old

    def test_exact_name_ranks_first_and_unrelated_prefix_ranks_last(self):
        from grocery_bot.benefits_catalog import search_catalog
        names = [r["חנות"] for r in search_catalog("פוקס")]
        self.assertEqual(names[0], "פוקס", "exact match first")
        self.assertEqual(names[-1], "פוקס דרי ישראל - חולון",
                         "the unrelated same-prefix merchant sinks to the bottom")
