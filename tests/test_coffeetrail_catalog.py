"""The read-only coffee-cart seam (grocery_bot/cli.py's coffee-* commands,
backed by coffeetrail_catalog.py). Mirrors tests/test_benefits_catalog.py.

Uses a temp COFFEETRAIL_DATA_DIR with hand-written fixtures, not the real
harvested data, so these tests do not depend on data/coffeetrail/
existing or on scripts/harvest_coffeetrail.py having run.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent.parent
IL = ZoneInfo("Asia/Jerusalem")

TEL_AVIV = {
    "slug": "tel-aviv-cart", "url": "https://coffeetrail.co.il/coffeecart/tel-aviv-cart/",
    "name": "עגלת תל אביב", "legal_name": "עגלת תל אביב",
    "description": "עגלת קפה על שפת הים", "address_text": "טיילת תל אביב, Israel",
    "lat": 32.0809, "lng": 34.7806, "has_map": "https://maps.google/x",
    "phone": "", "opening_hours": ["Su,Mo,Tu,We,Th 07:00-19:00", "Fr 07:00-14:00"],
    "same_as": [], "logo": "", "photos": [], "date_modified": "2026-01-01T00:00:00+00:00",
}
HAIFA = {
    "slug": "haifa-cart", "url": "https://coffeetrail.co.il/coffeecart/haifa-cart/",
    "name": "עגלת חיפה", "legal_name": "עגלת חיפה",
    "description": "עגלת קפה בכרמל", "address_text": "כרמל, חיפה, Israel",
    "lat": 32.7940, "lng": 34.9896, "has_map": "",
    "phone": "", "opening_hours": [],
    "same_as": [], "logo": "", "photos": [], "date_modified": "2025-06-01T00:00:00+00:00",
}
NO_COORDS = {
    "slug": "no-coords-cart", "url": "https://coffeetrail.co.il/coffeecart/no-coords-cart/",
    "name": "עגלה בלי מיקום", "legal_name": "", "description": "",
    "address_text": "", "lat": None, "lng": None, "has_map": "",
    "phone": "", "opening_hours": [], "same_as": [], "logo": "", "photos": [],
    "date_modified": "",
}


def _write_catalog(data_dir: Path, rows: list[dict]) -> None:
    (data_dir / "carts.json").write_text(
        json.dumps({r["slug"]: r for r in rows}, ensure_ascii=False), encoding="utf-8"
    )


def _write_terms(data_dir: Path, terms: dict) -> None:
    (data_dir / "terms.json").write_text(json.dumps(terms, ensure_ascii=False), encoding="utf-8")


class LoadAndSearchTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._dir.name)
        _write_catalog(self.data_dir, [TEL_AVIV, HAIFA])
        self._old = os.environ.get("COFFEETRAIL_DATA_DIR")
        os.environ["COFFEETRAIL_DATA_DIR"] = str(self.data_dir)

    def tearDown(self):
        self._dir.cleanup()
        if self._old is None:
            os.environ.pop("COFFEETRAIL_DATA_DIR", None)
        else:
            os.environ["COFFEETRAIL_DATA_DIR"] = self._old

    def test_load_catalog_returns_all_rows(self):
        from grocery_bot.coffeetrail_catalog import load_catalog
        self.assertEqual(len(load_catalog()), 2)

    def test_search_matches_name(self):
        from grocery_bot.coffeetrail_catalog import search_catalog
        hits = search_catalog("תל אביב")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["slug"], "tel-aviv-cart")

    def test_search_matches_description(self):
        from grocery_bot.coffeetrail_catalog import search_catalog
        hits = search_catalog("כרמל")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["slug"], "haifa-cart")

    def test_empty_query_returns_nothing(self):
        from grocery_bot.coffeetrail_catalog import search_catalog
        self.assertEqual(search_catalog(""), [])

    def test_missing_file_is_empty_list_not_a_crash(self):
        from grocery_bot.coffeetrail_catalog import load_catalog
        (self.data_dir / "carts.json").unlink()
        self.assertEqual(load_catalog(), [])

    def test_freshness_reports_count_and_date_range(self):
        from grocery_bot.coffeetrail_catalog import freshness
        f = freshness()
        self.assertEqual(f["carts"], 2)
        self.assertEqual(f["oldest_change"], "2025-06-01T00:00:00+00:00")
        self.assertEqual(f["newest_change"], "2026-01-01T00:00:00+00:00")

    def test_freshness_on_empty_catalog_is_empty_dict(self):
        from grocery_bot.coffeetrail_catalog import freshness
        (self.data_dir / "carts.json").unlink()
        self.assertEqual(freshness(), {})


class NearbyTests(unittest.TestCase):
    """Structured lat/lng is the whole point — this is what makes "עגלת
    קפה קרובה" answerable at all."""

    def test_sorted_nearest_first(self):
        from grocery_bot.coffeetrail_catalog import nearby
        # A point near Tel Aviv: Haifa cart should rank second.
        rows = nearby(32.08, 34.78, radius_km=None, rows=[TEL_AVIV, HAIFA, NO_COORDS])
        self.assertEqual([r["slug"] for r in rows], ["tel-aviv-cart", "haifa-cart"])

    def test_radius_excludes_far_carts(self):
        from grocery_bot.coffeetrail_catalog import nearby
        rows = nearby(32.08, 34.78, radius_km=5, rows=[TEL_AVIV, HAIFA])
        self.assertEqual([r["slug"] for r in rows], ["tel-aviv-cart"])

    def test_carts_without_coordinates_are_excluded_not_ranked_last(self):
        from grocery_bot.coffeetrail_catalog import nearby
        rows = nearby(32.08, 34.78, radius_km=None, rows=[NO_COORDS])
        self.assertEqual(rows, [])

    def test_distance_km_is_attached_and_rounded(self):
        from grocery_bot.coffeetrail_catalog import nearby
        rows = nearby(32.0809, 34.7806, radius_km=None, rows=[TEL_AVIV])
        self.assertEqual(rows[0]["distance_km"], 0.0)


class OpenNowTests(unittest.TestCase):
    """None means "we don't know" — never conflated with "closed" (the
    same failure class as the L3 benefits finding: absence of data
    dressed as a negative answer)."""

    def test_no_hours_on_file_is_none_not_false(self):
        from grocery_bot.coffeetrail_catalog import open_now
        self.assertIsNone(open_now(HAIFA))

    def test_open_during_listed_hours(self):
        from grocery_bot.coffeetrail_catalog import open_now
        # Monday 10:00 Israel time is within "Su,Mo,Tu,We,Th 07:00-19:00".
        monday_10am = datetime(2026, 9, 7, 10, 0, tzinfo=IL)  # a Monday
        self.assertTrue(open_now(TEL_AVIV, when=monday_10am))

    def test_closed_outside_listed_hours(self):
        from grocery_bot.coffeetrail_catalog import open_now
        monday_11pm = datetime(2026, 9, 7, 23, 0, tzinfo=IL)
        self.assertFalse(open_now(TEL_AVIV, when=monday_11pm))

    def test_friday_uses_the_shorter_hours(self):
        from grocery_bot.coffeetrail_catalog import open_now
        friday_1pm = datetime(2026, 9, 11, 13, 0, tzinfo=IL)  # a Friday
        friday_4pm = datetime(2026, 9, 11, 16, 0, tzinfo=IL)
        self.assertTrue(open_now(TEL_AVIV, when=friday_1pm))
        self.assertFalse(open_now(TEL_AVIV, when=friday_4pm))

    def test_unparseable_entry_is_none_not_false(self):
        from grocery_bot.coffeetrail_catalog import open_now
        odd = dict(TEL_AVIV, opening_hours=["something the parser doesn't understand"])
        self.assertIsNone(open_now(odd))


class TermsTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._dir.name)
        _write_catalog(self.data_dir, [TEL_AVIV, HAIFA])
        _write_terms(self.data_dir, {
            "region": {
                "dan": {"name": "דן", "carts": ["tel-aviv-cart"], "note": "best-effort"},
            },
        })
        self._old = os.environ.get("COFFEETRAIL_DATA_DIR")
        os.environ["COFFEETRAIL_DATA_DIR"] = str(self.data_dir)

    def tearDown(self):
        self._dir.cleanup()
        if self._old is None:
            os.environ.pop("COFFEETRAIL_DATA_DIR", None)
        else:
            os.environ["COFFEETRAIL_DATA_DIR"] = self._old

    def test_search_by_term_resolves_full_rows(self):
        from grocery_bot.coffeetrail_catalog import search_by_term
        rows = search_by_term("region", "dan")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "עגלת תל אביב")

    def test_unknown_term_is_empty_not_a_crash(self):
        from grocery_bot.coffeetrail_catalog import search_by_term
        self.assertEqual(search_by_term("region", "nonexistent"), [])

    def test_unknown_taxonomy_is_empty_not_a_crash(self):
        from grocery_bot.coffeetrail_catalog import search_by_term
        self.assertEqual(search_by_term("nonexistent", "dan"), [])


class CliContractTests(unittest.TestCase):
    """Run through the real subprocess CLI, the way Miri actually calls it."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._dir.name)
        _write_catalog(self.data_dir, [TEL_AVIV, HAIFA])
        _write_terms(self.data_dir, {
            "region": {"dan": {"name": "דן", "carts": ["tel-aviv-cart"], "note": "best-effort"}},
        })
        self._tmpdb = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()
        self._tmpdb.cleanup()

    def _run(self, args):
        return subprocess.run(
            [sys.executable, "-m", "grocery_bot.cli", *args],
            cwd=PROJECT,
            env={
                "GROCERY_BOT_DB_PATH": str(Path(self._tmpdb.name) / "t.sqlite3"),
                "COFFEETRAIL_DATA_DIR": str(self.data_dir),
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
            },
            capture_output=True, text=True,
        )

    def test_coffee_catalog_needs_no_telegram_token(self):
        result = self._run(["coffee-catalog", "תל אביב"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("עגלת תל אביב", result.stdout)

    def test_coffee_catalog_json(self):
        result = self._run(["coffee-catalog", "--json"])
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 2)

    def test_coffee_catalog_miss_is_exit_1(self):
        result = self._run(["coffee-catalog", "לא קיים בכלל"])
        self.assertEqual(result.returncode, 1)

    def test_coffee_nearby(self):
        result = self._run(["coffee-nearby", "32.0809", "34.7806", "--radius", "5"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("עגלת תל אביב", result.stdout)
        self.assertNotIn("עגלת חיפה", result.stdout)

    def test_coffee_nearby_needs_two_numbers(self):
        result = self._run(["coffee-nearby", "not-a-number", "34.78"])
        self.assertEqual(result.returncode, 2)

    def test_coffee_terms_lists_taxonomies(self):
        result = self._run(["coffee-terms"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("region", result.stdout)

    def test_coffee_by_term(self):
        result = self._run(["coffee-by-term", "region", "dan"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("עגלת תל אביב", result.stdout)
        self.assertIn("חלקית", result.stdout)  # the best-effort caveat

    def test_both_are_on_the_token_free_path(self):
        from grocery_bot import cli
        for cmd in ("coffee-catalog", "coffee-nearby", "coffee-terms", "coffee-by-term"):
            self.assertIn(cmd, cli._DB_ONLY_COMMANDS)


if __name__ == "__main__":
    unittest.main()
