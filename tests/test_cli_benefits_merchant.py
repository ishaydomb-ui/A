"""The chain-scoped question: does this shop carry a benefit at all?

"No benefit for this chain" and "the lookup found nothing" are different
answers that lead to different actions, and `benefits-catalog --json`
returns a bare `[]` for both — byte-identical for קסטרו, a real chain
with no benefit, and for gibberish. Miri hit that from the other side:
asked "יש לי הטבה בקסטרו?" she had no chain-scoped tool at all.
"""
import io
import json
import unittest
from contextlib import redirect_stdout

from grocery_bot import cli


def _run(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli._benefits_merchant(None, list(args))
    return code, out.getvalue()


class AbsenceIsAnAnswer(unittest.TestCase):
    def test_a_chain_with_no_benefit_is_found_false_not_an_error(self):
        code, text = _run("קסטרו", "--json")
        payload = json.loads(text)
        self.assertEqual(code, 1)
        self.assertIs(payload["found"], False)
        self.assertEqual(payload["merchants"], [])

    def test_the_reply_names_which_clubs_were_actually_searched(self):
        """Absence means "not in the clubs we hold", never "does not
        exist" — four of the household's six are not harvested."""
        _, text = _run("קסטרו", "--json")
        payload = json.loads(text)
        self.assertIn("בהצדעה", payload["searched_clubs"])
        self.assertTrue(payload["unsearched_clubs"],
                        "unsearched clubs must be stated, not hidden")

    def test_the_text_reply_says_no_benefit_rather_than_not_found(self):
        code, text = _run("קסטרו")
        self.assertEqual(code, 1)
        self.assertIn("אין הטבה", text)
        self.assertIn("לא נסרקו", text)

    def test_freshness_travels_with_the_answer(self):
        _, text = _run("קסטרו", "--json")
        self.assertTrue(json.loads(text)["freshness"])


class PresenceIsAlsoAnAnswer(unittest.TestCase):
    def test_a_chain_with_a_benefit_reports_found_and_its_rate(self):
        code, text = _run("פוקס", "--json")
        payload = json.loads(text)
        self.assertEqual(code, 0)
        self.assertIs(payload["found"], True)
        names = [m["name"] for m in payload["merchants"]]
        self.assertIn("פוקס", names)
        self.assertTrue(any(m["max_rate"] for m in payload["merchants"]))

    def test_every_absent_chain_in_the_phrasebook_reports_found_false(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        import phrasebook

        for term in phrasebook.MERCHANTS_ABSENT:
            code, text = _run(term, "--json")
            self.assertEqual(code, 1, term)
            self.assertIs(json.loads(text)["found"], False, term)

    def test_every_present_chain_in_the_phrasebook_reports_found_true(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        import phrasebook

        for term, _expected in phrasebook.MERCHANTS:
            code, _ = _run(term, "--json")
            self.assertEqual(code, 0, term)

    def test_no_argument_prints_usage(self):
        code, text = _run()
        self.assertEqual(code, 2)
        self.assertIn("usage", text)


if __name__ == "__main__":
    unittest.main()
