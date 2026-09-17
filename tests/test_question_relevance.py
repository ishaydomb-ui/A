"""Decide, do not ask — and if you must ask, ask only about this shop.

Two rounds of the same problem, and the second answer supersedes the
first. On 2026-09-11 the fix was to *filter* the questions: only this
cycle's, capped at eight, the backlog behind /questions. On 2026-09-17
Ishay looked at 90 still-open questions and rejected the premise —
"אני לא מתכוון לענות על 90 שאלות. הפרוסס הזה לא עובד. קח החלטה מה לשים
על בסיס היסטוריית הקנייה שלי."

So `_ask_ambiguities` now resolves from purchase history first, and what
it can resolve it never asks. The relevance and cap machinery is kept for
whatever survives that, and these tests pin both layers: that a decidable
question is decided silently, and that the old filtering still holds for
anything left.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from grocery_bot.models import CartAddResult, OrderCycleReport
from grocery_bot.storage import Storage
from grocery_bot.telegram_bot import MAX_QUESTIONS_PER_BURST, GroceryBot


class _Recorder:
    """Captures what would have been sent, instead of sending it."""

    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)


class _Context:
    def __init__(self, bot):
        self.bot = bot


def _report(store, terms):
    report = OrderCycleReport(store=store)
    for term in terms:
        report.record(
            CartAddResult(item_name=term, store=store, status="ambiguous",
                          candidates=["א", "ב"], candidate_cards=[])
        )
    return report


class QuestionRelevanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self.bot = GroceryBot.__new__(GroceryBot)
        self.bot.storage = self.storage
        self.recorder = _Recorder()
        self.context = _Context(self.recorder)

    def _ask(self, reports=None):
        self.recorder.messages.clear()
        return asyncio.run(
            self.bot._ask_ambiguities(1, self.context, reports)  # noqa: SLF001
        )

    def _queue(self, store, term):
        self.storage.save_pending_ambiguity(
            store=store, original_term=term, quantity=1,
            candidates=["מוצר א", "מוצר ב"], candidate_cards=[],
        )

    def test_a_decidable_question_is_decided_not_asked(self) -> None:
        # The 2026-09-17 contract. Candidates that can be chosen between
        # are chosen between, and nothing is put to the household.
        self._queue("shufersal", "קוטג")
        sent = self._ask({"shufersal": _report("shufersal", ["קוטג"])})
        self.assertEqual(sent, 0)
        self.assertFalse(any("איזה מהם" in m for m in self.recorder.messages))

    def test_the_household_is_told_what_was_decided(self) -> None:
        # Deciding silently would be worse than asking: they cannot
        # correct what they were never shown.
        self._queue("shufersal", "קוטג")
        self._ask({"shufersal": _report("shufersal", ["קוטג"])})
        self.assertTrue(any("החלטתי" in m for m in self.recorder.messages))

    def test_a_whole_backlog_is_cleared_without_a_single_question(self) -> None:
        # 90 open questions was the thing he refused. None of these
        # should reach him.
        for n in range(MAX_QUESTIONS_PER_BURST + 20):
            self._queue("tivtaam", f"מוצר {n}")
        sent = self._ask(reports=None)
        self.assertEqual(sent, 0)
        self.assertFalse(any("/questions" in m for m in self.recorder.messages))

    def test_the_cap_still_guards_whatever_cannot_be_decided(self) -> None:
        # The 2026-09-11 machinery is kept, not deleted: if something ever
        # survives the resolver, a burst must still not flood the chat.
        from grocery_bot import autoresolve

        original = autoresolve.resolve_all
        autoresolve.resolve_all = lambda storage: []
        self.addCleanup(lambda: setattr(autoresolve, "resolve_all", original))
        for n in range(MAX_QUESTIONS_PER_BURST + 5):
            self._queue("tivtaam", f"מוצר {n}")
        sent = self._ask(reports=None)
        self.assertEqual(sent, MAX_QUESTIONS_PER_BURST)

    def test_store_filtering_still_holds_for_what_is_left(self) -> None:
        from grocery_bot import autoresolve

        original = autoresolve.resolve_all
        autoresolve.resolve_all = lambda storage: []
        self.addCleanup(lambda: setattr(autoresolve, "resolve_all", original))
        self._queue("tivtaam", "בצל")
        self._queue("shufersal", "קוטג")
        sent = self._ask({"shufersal": _report("shufersal", ["קוטג"])})
        self.assertEqual(sent, 1)
        self.assertFalse(any("בצל" in m for m in self.recorder.messages))

    def test_an_already_remembered_choice_is_never_asked(self) -> None:
        # Pre-existing behaviour, kept: an unresolved row can outlive its
        # own question when the choice was settled another way.
        self._queue("shufersal", "קוטג")
        self.storage.remember_choice(
            store="shufersal", term="קוטג",
            product_code="P_1", product_name="קוטג 5% תנובה",
        )
        self.assertEqual(self._ask({"shufersal": _report("shufersal", ["קוטג"])}), 0)
        self.assertEqual(self.recorder.messages, [])

    def test_nothing_queued_says_nothing(self) -> None:
        self.assertEqual(self._ask({"shufersal": _report("shufersal", [])}), 0)
        self.assertEqual(self.recorder.messages, [])


    def test_whatever_is_left_still_arrives_as_one_message(self) -> None:
        """Before 2026-09-11 this was one Telegram message per question:
        80 after a single cycle, each its own notification. Still true for
        anything the resolver cannot settle."""
        from grocery_bot import autoresolve

        original = autoresolve.resolve_all
        autoresolve.resolve_all = lambda storage: []
        self.addCleanup(lambda: setattr(autoresolve, "resolve_all", original))
        for term in ("בצל", "גזר", "שמן זית", "קמח"):
            self._queue("tivtaam", term)
        self._ask(reports=None)
        questions = [m for m in self.recorder.messages if "איזה מהם" in m]
        self.assertEqual(len(questions), 1)
        self.assertIn("1 מתוך 4", questions[0])

    def test_the_set_is_remembered_for_the_next_tap(self) -> None:
        # Answered over minutes or hours; a restart in between is ordinary,
        # so the queue lives in storage and not in memory. Only reachable
        # now for what the resolver could not settle.
        import json

        from grocery_bot import autoresolve

        original = autoresolve.resolve_all
        autoresolve.resolve_all = lambda storage: []
        self.addCleanup(lambda: setattr(autoresolve, "resolve_all", original))
        for term in ("בצל", "גזר"):
            self._queue("tivtaam", term)
        self._ask(reports=None)
        queued = json.loads(self.storage.get_state("question_queue", "") or "[]")
        self.assertEqual(len(queued), 2)


class _Query:
    """Just enough of a CallbackQuery to watch the message being edited."""

    def __init__(self):
        self.edits = []

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)


class QuestionPagingTests(unittest.TestCase):
    """One message, edited forward through the set."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self.bot = GroceryBot.__new__(GroceryBot)
        self.bot.storage = self.storage
        self.ids = [
            self.storage.save_pending_ambiguity(
                store="tivtaam", original_term=term, quantity=1,
                candidates=["מוצר א", "מוצר ב"], candidate_cards=[],
            )
            for term in ("בצל", "גזר", "שמן זית")
        ]
        self.storage.set_state("question_queue", json.dumps(self.ids))

    def test_answering_one_shows_the_next_in_the_same_message(self) -> None:
        query = _Query()
        moved = asyncio.run(self.bot._advance_question(query, self.ids[0]))  # noqa: SLF001
        self.assertTrue(moved)
        self.assertEqual(len(query.edits), 1)
        self.assertIn("גזר", query.edits[0])
        self.assertIn("2 מתוך 3", query.edits[0])

    def test_the_last_answer_ends_the_set(self) -> None:
        query = _Query()
        for ambiguity_id in self.ids[:2]:
            self.storage.mark_ambiguity_resolved(ambiguity_id)
            asyncio.run(self.bot._advance_question(query, ambiguity_id))  # noqa: SLF001
        self.storage.mark_ambiguity_resolved(self.ids[2])
        self.assertFalse(
            asyncio.run(self.bot._advance_question(query, self.ids[2]))  # noqa: SLF001
        )

    def test_a_question_settled_elsewhere_is_skipped_not_shown(self) -> None:
        # /autochoice, a bulk match or another tap can settle one after
        # the set was built.
        self.storage.remember_choice(
            store="tivtaam", term="גזר", product_code="1", product_name="גזר ארוז",
        )
        query = _Query()
        asyncio.run(self.bot._advance_question(query, self.ids[0]))  # noqa: SLF001
        self.assertIn("שמן זית", query.edits[0])
        self.assertNotIn("גזר", query.edits[0])

    def test_a_tap_from_an_old_set_does_not_hijack_the_current_one(self) -> None:
        query = _Query()
        self.assertFalse(
            asyncio.run(self.bot._advance_question(query, 9999))  # noqa: SLF001
        )
        self.assertEqual(query.edits, [])


if __name__ == "__main__":
    unittest.main()
