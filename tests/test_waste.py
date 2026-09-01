import tempfile
import unittest
from datetime import date
from pathlib import Path

from grocery_bot import waste
from grocery_bot.storage import Storage


class FractionTest(unittest.TestCase):
    def test_reads_the_quantity_words_people_use(self):
        self.assertEqual(waste.fraction_for("זרקתי חצי חסה"), 0.5)
        self.assertEqual(waste.fraction_for("זרקתי את כל העגבניות"), 1.0)
        self.assertEqual(waste.fraction_for("זרקתי קצת פטרוזיליה"), 0.25)
        self.assertEqual(waste.fraction_for("זרקתי את רוב הלחם"), 0.75)

    def test_no_quantity_defaults_to_half(self):
        # Least wrong when someone says "זרקתי חסה": a whole item
        # overstates, a token amount understates.
        self.assertEqual(waste.fraction_for("זרקתי חסה"), 0.5)

    def test_empty_text_is_safe(self):
        self.assertEqual(waste.fraction_for(""), 0.5)


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def test_reports_are_stored_and_summarised(self):
        waste.record(self.storage, [("חסה", 0.5), ("עגבניות", 0.25)], "ישי")
        summary = self.storage.waste_summary()
        self.assertEqual(summary["חסה"], (1, 0.5))

    def test_repeat_reports_accumulate(self):
        for _ in range(3):
            waste.record(self.storage, [("חסה", 0.5)], "ישי", date(2026, 9, 1))
        self.assertEqual(self.storage.waste_summary()["חסה"], (3, 1.5))

    def test_blank_names_are_ignored(self):
        self.assertEqual(waste.record(self.storage, [("", 0.5), ("  ", 1.0)]), 0)

    def test_who_reported_is_kept(self):
        # Both partners use this; knowing who said what matters for
        # follow-up, and it costs nothing to keep.
        waste.record(self.storage, [("חסה", 0.5)], "לירן")
        self.assertEqual(self.storage.recent_waste()[0]["reported_by"], "לירן")


class PatternTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(str(Path(self.tmp.name) / "t.sqlite3"))

    def _report(self, name, fraction, times=1):
        for _ in range(times):
            waste.record(self.storage, [(name, fraction)])

    def test_one_report_is_an_anecdote_not_a_pattern(self):
        # A bad week, cancelled guests, a trip away.
        self._report("חסה", 1.0)
        self.assertEqual(waste.patterns(self.storage), [])

    def test_repeated_heavy_waste_becomes_actionable(self):
        self._report("חסה", 1.0, times=3)
        found = waste.patterns(self.storage)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].item_name, "חסה")

    def test_occasional_waste_is_left_alone(self):
        # Some waste is normal; flagging it would be nagging.
        self._report("חסה", 0.25, times=4)
        self.assertEqual(waste.patterns(self.storage), [])

    def test_suggestion_scales_with_how_much_is_wasted(self):
        self._report("חסה", 1.0, times=3)
        self.assertIn("לא צריך", waste.patterns(self.storage)[0].suggestion)
        self._report("לחם", 0.5, times=3)
        bread = [p for p in waste.patterns(self.storage) if p.item_name == "לחם"][0]
        self.assertIn("חצי כמות", bread.suggestion)

    def test_worst_offender_ranks_first(self):
        self._report("חסה", 1.0, times=3)
        self._report("לחם", 0.5, times=3)
        self.assertEqual(waste.patterns(self.storage)[0].item_name, "חסה")


class ToneTest(unittest.TestCase):
    def test_acknowledgement_states_a_fact_and_nothing_else(self):
        text = waste.acknowledge([("חסה", 0.5)])
        self.assertIn("רשמתי", text)
        for scold in ("בזבוז", "חבל", "כדאי להיזהר", "אסור"):
            self.assertNotIn(scold, text)

    def test_nothing_understood_asks_plainly(self):
        self.assertIn("לא הבנתי", waste.acknowledge([]))

    def test_no_patterns_produces_no_message_at_all(self):
        # Silence is the correct output; an empty section would be noise.
        self.assertEqual(waste.format_patterns([]), "")


if __name__ == "__main__":
    unittest.main()
