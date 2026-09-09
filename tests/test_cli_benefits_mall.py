"""`benefits-mall` must never let "I don't know that mall" read as
"that mall has no discounts".

The distinction is the whole point of the command: the question it
answers is "am I leaving a discount on the table here", and a false
"nothing here" is the one wrong answer that costs money silently.
Raised by Miri 2026-09-09 against the JSON mode, hitting
"קניון בראשון לציון" — a real mall this project does not model.
"""
import io
import json
import unittest
from contextlib import redirect_stdout

from grocery_bot import cli


def _run(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli._benefits_mall(None, list(args))
    return code, out.getvalue()


class UnknownMallIsNotAnEmptyMall(unittest.TestCase):
    def test_json_says_recognized_false_rather_than_only_an_empty_list(self):
        code, text = _run("קניון בראשון לציון", "--json")
        payload = json.loads(text)
        self.assertEqual(code, 1)
        self.assertIs(payload["recognized"], False)
        self.assertIsNone(payload["mall"])
        # The empty list alone is the ambiguous part; `recognized` is what
        # a consumer must branch on.
        self.assertEqual(payload["chains"], [])

    def test_json_names_the_malls_it_does_know(self):
        """So a caller can say "I don't have that one, I have these"."""
        _, text = _run("קניון בראשון לציון", "--json")
        known = json.loads(text)["known_malls"]
        self.assertIn("קניון רמת אביב, תל אביב", known)
        self.assertEqual(len(known), 6)

    def test_a_recognized_mall_is_flagged_as_such(self):
        code, text = _run("רמת אביב", "--json")
        payload = json.loads(text)
        self.assertEqual(code, 0)
        self.assertIs(payload["recognized"], True)
        self.assertEqual(payload["mall"], "קניון רמת אביב, תל אביב")
        self.assertTrue(payload["chains"])

    def test_the_text_mode_says_it_in_words_and_lists_the_known_malls(self):
        code, text = _run("קניון בראשון לציון")
        self.assertEqual(code, 1)
        self.assertIn("לא זוהה קניון מוכר", text)
        self.assertIn("רמת אביב", text)

    def test_no_argument_prints_usage_rather_than_guessing(self):
        code, text = _run()
        self.assertEqual(code, 2)
        self.assertIn("usage", text)


class RatesShown(unittest.TestCase):
    def test_a_dead_wallet_is_never_offered_as_a_live_rate(self):
        """25% חודש ההוקרה expired 30.6.26 and cannot be loaded, but the
        catalogue still carries its rate."""
        _, text = _run("רמת אביב", "--json")
        wallets = {c["wallet"] for c in json.loads(text)["chains"]}
        self.assertNotIn("מבצע הוקרה 25%", wallets)


if __name__ == "__main__":
    unittest.main()


class NothingFailsSilently(unittest.TestCase):
    """A chain shown without a discount reads as "no benefit here".

    So every path that could not determine a rate must say so, rather
    than let the absence look like a zero. The catalogue being
    unreadable is the dangerous one: it would render an entire mall as
    discount-free while looking like a normal answer.
    """

    def test_an_unreadable_catalogue_is_reported_not_shown_as_no_discounts(self):
        import os
        from unittest import mock

        with mock.patch.object(cli, "_data_dir", create=True):
            with mock.patch("grocery_bot.benefits_catalog._data_dir",
                            return_value=os.path.join(os.sep, "nonexistent-dir")):
                code, text = _run("רמת אביב")
        self.assertEqual(code, 4, "an unreadable catalogue must not exit 0")
        self.assertIn("לא ידוע", text)
        self.assertNotIn("0%", text)

    def test_the_json_flags_that_rates_are_unknown(self):
        import os
        from unittest import mock

        with mock.patch("grocery_bot.benefits_catalog._data_dir",
                        return_value=os.path.join(os.sep, "nonexistent-dir")):
            code, text = _run("רמת אביב", "--json")
        payload = json.loads(text)
        self.assertEqual(code, 4)
        self.assertIs(payload["rates_known"], False)
        # The mall is still recognized — the failure is about rates only.
        self.assertIs(payload["recognized"], True)
        self.assertTrue(payload["chains"])
        # A rate that could not be read is null, never 0.
        self.assertTrue(all(c["discount"] is None for c in payload["chains"]))
        self.assertTrue(payload["problems"]["catalog_error"])

    def test_a_healthy_run_reports_no_problems(self):
        code, text = _run("רמת אביב", "--json")
        payload = json.loads(text)
        self.assertEqual(code, 0)
        self.assertIs(payload["rates_known"], True)
        self.assertEqual(payload["problems"]["catalog_error"], "")


class ClosedWalletsComeFromStatusNotAName(unittest.TestCase):
    """Matching a dead wallet by its Hebrew display name breaks the day
    the club rewords it. The published status file is the real signal."""

    def test_a_rate_is_closed_only_when_every_wallet_offering_it_is(self):
        """Two wallets share 15%; one being shut must not hide the other."""
        closed = cli._closed_rates()
        self.assertIn(25.0, closed)
        self.assertNotIn(15.0, closed)
        self.assertNotIn(30.0, closed)

    def test_the_source_of_the_exclusion_is_reported(self):
        _, text = _run("רמת אביב", "--json")
        payload = json.loads(text)
        self.assertEqual(payload["problems"]["closed_rates_source"],
                         "wallet_status.json")

    def test_a_missing_status_file_falls_back_rather_than_trusting_nothing(self):
        import os
        from unittest import mock

        with mock.patch("grocery_bot.benefits_catalog._data_dir",
                        return_value=os.path.join(os.sep, "nonexistent-dir")):
            self.assertEqual(cli._closed_rates(), set())
