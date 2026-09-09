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
