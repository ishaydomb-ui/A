"""Talking to it like a person, not like a form.

Ishay, 2026-09-11: *"זה צריך להיות בדיוק כמו שיחה פה"* — no careful
phrasing to make a message fall inside a definition. These tests pin the
two things that requires: a message may carry more than one request, and
a short follow-up resolves against what was just discussed.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from grocery_bot import convo, nlu
from grocery_bot.storage import Storage


class ContextMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def test_the_last_subject_comes_back(self) -> None:
        convo.remember(self.storage, subject="קוטג 5%", store="shufersal",
                       quantity=1, action="נוסף לרשימה")
        recalled = convo.recall(self.storage)
        self.assertEqual(recalled["subject"], "קוטג 5%")
        self.assertEqual(recalled["store"], "shufersal")

    def test_a_stale_subject_is_not_used(self) -> None:
        """"בעצם שניים" three hours later is a new thought, and resolving
        it against an old subject puts the wrong thing in the cart."""
        old = datetime.now(timezone.utc) - timedelta(
            seconds=convo.CONTEXT_TTL_SECONDS + 60
        )
        convo.remember(self.storage, subject="קוטג 5%", when=old)
        self.assertEqual(convo.recall(self.storage), {})

    def test_a_fresh_subject_survives_a_pause(self) -> None:
        recent = datetime.now(timezone.utc) - timedelta(minutes=20)
        convo.remember(self.storage, subject="קוטג 5%", when=recent)
        self.assertEqual(convo.recall(self.storage)["subject"], "קוטג 5%")

    def test_nothing_remembered_is_not_an_error(self) -> None:
        self.assertEqual(convo.recall(self.storage), {})

    def test_corrupt_state_is_not_an_error(self) -> None:
        self.storage.set_state(convo.STATE_KEY, "{not json")
        self.assertEqual(convo.recall(self.storage), {})

    def test_the_prompt_line_reads_as_background(self) -> None:
        convo.remember(self.storage, subject="קוטג 5%", store="tivtaam", quantity=2)
        line = convo.describe(convo.recall(self.storage))
        self.assertIn("קוטג 5%", line)
        self.assertIn("טיב טעם", line)
        self.assertEqual(convo.describe({}), "")


class MultiRequestParsingTests(unittest.TestCase):
    """One message, two requests — the second must not be dropped."""

    def _parse(self, payload, monkey):
        monkey(payload)
        return nlu.parse_message("whatever")

    def setUp(self) -> None:
        self._original = nlu._ask_model

        def _install(payload):
            nlu._ask_model = lambda message, context=None: payload

        self._install = _install
        self.addCleanup(lambda: setattr(nlu, "_ask_model", self._original))

    def test_two_requests_both_survive(self) -> None:
        parsed = self._parse(
            '{"intent":"add_item","items":[{"name":"חלב"}],'
            '"actions":[{"intent":"add_item","items":[{"name":"חלב"}]},'
            '{"intent":"price_query","query":"טחינה"}],"reply":"בסדר"}',
            self._install,
        )
        self.assertEqual([a.intent for a in parsed.actions],
                         ["add_item", "price_query"])
        # The first is mirrored so every existing caller keeps working.
        self.assertEqual(parsed.intent, "add_item")
        self.assertEqual(parsed.actions[1].query, "טחינה")

    def test_one_request_still_produces_one_action(self) -> None:
        parsed = self._parse(
            '{"intent":"add_item","items":[{"name":"חלב"}]}', self._install
        )
        self.assertEqual(len(parsed.actions), 1)
        self.assertEqual(parsed.actions[0].intent, "add_item")

    def test_an_invented_action_intent_is_dropped_not_run(self) -> None:
        parsed = self._parse(
            '{"intent":"add_item","items":[{"name":"חלב"}],'
            '"actions":[{"intent":"add_item","items":[{"name":"חלב"}]},'
            '{"intent":"checkout"}]}',
            self._install,
        )
        self.assertEqual([a.intent for a in parsed.actions], ["add_item"])

    def test_a_named_chain_is_carried(self) -> None:
        parsed = self._parse(
            '{"intent":"shopped","store":"shufersal"}', self._install
        )
        self.assertEqual(parsed.store, "shufersal")

    def test_an_unknown_chain_is_not_carried(self) -> None:
        parsed = self._parse(
            '{"intent":"shopped","store":"rami levy"}', self._install
        )
        self.assertEqual(parsed.store, "")

    def test_only_once_and_always_count_as_scope(self) -> None:
        for value, expected in (("once", "once"), ("always", "always"),
                                ("maybe", ""), (None, "")):
            parsed = self._parse(
                '{"intent":"add_item","items":[{"name":"חלב"}],'
                f'"scope":{"null" if value is None else chr(34) + value + chr(34)}}}',
                self._install,
            )
            self.assertEqual(parsed.scope, expected, value)


if __name__ == "__main__":
    unittest.main()
