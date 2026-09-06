import unittest
from datetime import date

from grocery_bot.publishedprices import PortalFile


class FeedFreshnessTest(unittest.TestCase):
    def test_date_comes_from_the_filename_not_the_upload_time(self):
        # Re-uploading an unchanged file refreshes its modification time,
        # which would make a two-year-old snapshot look like today's.
        file = PortalFile("PriceFull7290-001-034-20260901-1405.gz", 1, "09/01/2026 14:10")
        self.assertEqual(file.published_on, date(2026, 9, 1))

    def test_a_long_dead_feed_reports_its_real_age(self):
        # Yohananof, observed 2026-09-01: parses perfectly, last updated
        # December 2024.
        file = PortalFile("PriceFull7290803800003-7999-202412271528.gz", 1, "")
        self.assertEqual(file.age_days(date(2026, 9, 1)), 613)

    def test_a_nameless_date_is_unknown_rather_than_assumed_fresh(self):
        self.assertIsNone(PortalFile("weird.gz", 1, "").age_days(date(2026, 9, 1)))

    def test_branch_id_is_read_from_the_filename(self):
        self.assertEqual(
            PortalFile("PriceFull7290058140886-001-737-20260901-120005.gz", 1, "").branch_id,
            "737",
        )


if __name__ == "__main__":
    unittest.main()
