"""The free tier: everything that resolves without asking a model.

Runs with the suite on every commit. A failure here is a real failure,
not a flaky one — these are table lookups over harvested data, and they
either match or they do not.

The paid tier (intent classification) is deliberately absent: it costs
~8s and a subscription call per phrasing, and it is not deterministic.
It lives in `scripts/check_understanding.py`, opt-in.
"""
import unittest

from grocery_bot import benefits_catalog, malls
from grocery_bot.storage import Storage

import phrasebook


class MallPhrasings(unittest.TestCase):
    """Ishay types the place, not its canonical name."""

    def test_every_phrasing_resolves_as_expected(self):
        wrong = []
        for phrase, expected in phrasebook.MALLS:
            actual = malls.resolve(phrase)
            if actual != expected:
                wrong.append((phrase, actual or "(none)", expected or "(none)"))
        self.assertEqual(
            wrong, [],
            "\n".join(f"{p!r} -> {a!r}, expected {e!r}" for p, a, e in wrong),
        )

    def test_the_corpus_covers_every_modelled_mall(self):
        """A mall with no phrasing in the corpus is untested by
        construction, which is the gap worth failing on."""
        covered = {malls.resolve(p) for p, e in phrasebook.MALLS if e}
        self.assertEqual(covered, set(malls.known_malls()))

    def test_refusals_are_a_real_part_of_the_corpus(self):
        """A corpus of only positive cases measures nothing — the wrong
        answer this feature must avoid is a confident one."""
        refusals = [p for p, e in phrasebook.MALLS if not e]
        self.assertGreaterEqual(len(refusals), 6)


class MerchantPhrasings(unittest.TestCase):
    def test_each_merchant_term_finds_its_merchant(self):
        missing = []
        for term, expected in phrasebook.MERCHANTS:
            rows = benefits_catalog.search_catalog(term)
            names = " | ".join(str(r.get("חנות", "")) for r in rows)
            if expected not in names:
                missing.append((term, expected, len(rows)))
        self.assertEqual(
            missing, [],
            "\n".join(f"{t!r}: no {e!r} among {n} results" for t, e, n in missing),
        )


    def test_a_chain_with_no_benefit_returns_nothing_confidently(self):
        """"No benefit for this chain" is a correct answer and must be
        distinguishable from "lookup failed". Castro and Renuar are real
        chains genuinely absent from the club."""
        for term in phrasebook.MERCHANTS_ABSENT:
            self.assertEqual(benefits_catalog.search_catalog(term), [], term)


class FoodPhrasings(unittest.TestCase):
    """The supermarket half: a bare noun typed into a phone must price."""

    @classmethod
    def setUpClass(cls):
        cls.storage = Storage("data/grocery_bot.sqlite3")

    def test_every_food_term_returns_at_least_one_priced_product(self):
        empty = []
        for term in phrasebook.FOOD:
            if not self.storage.search_products(term, limit=3):
                empty.append(term)
        self.assertEqual(empty, [], f"no priced product for: {empty}")

    def test_a_nonsense_term_returns_nothing_rather_than_anything(self):
        """The failure that started the price work was a query for milk
        returning sweets. Returning nothing is correct; returning
        something irrelevant is the bug."""
        self.assertEqual(self.storage.search_products("קססדגכדגכ", limit=3), [])


if __name__ == "__main__":
    unittest.main()
