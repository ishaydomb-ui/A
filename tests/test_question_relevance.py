"""Only ask about the shop that just happened.

Measured on the live DB 2026-09-11: 80 variant questions would have gone
out at the next cycle, 73 of them about Tiv Taam — so a Shufersal-only
shop ended with 73 unrelated notifications, one Telegram message each,
about terms nobody had asked for that day. The queue was filtered by
nothing: not by store, not by age, not by whether the term was in the
run.

These tests pin the two halves of the fix: relevance, and a cap on how
many may arrive at once.
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

    def test_a_shufersal_shop_does_not_ask_about_tiv_taam(self) -> None:
        self._queue("tivtaam", "בצל")
        self._queue("tivtaam", "גזר")
        self._queue("shufersal", "קוטג")
        sent = self._ask({"shufersal": _report("shufersal", ["קוטג"])})
        self.assertEqual(sent, 1)
        self.assertTrue(any("קוטג" in m for m in self.recorder.messages))
        self.assertFalse(any("בצל" in m for m in self.recorder.messages))

    def test_the_backlog_is_named_but_not_pushed(self) -> None:
        self._queue("tivtaam", "בצל")
        self._queue("shufersal", "קוטג")
        self._ask({"shufersal": _report("shufersal", ["קוטג"])})
        backlog = [m for m in self.recorder.messages if "/questions" in m]
        self.assertEqual(len(backlog), 1)
        self.assertIn("1 שאלות", backlog[0])

    def test_questions_on_request_works_through_everything(self) -> None:
        for term in ("בצל", "גזר", "שמן זית"):
            self._queue("tivtaam", term)
        sent = self._ask(reports=None)
        self.assertEqual(sent, 3)

    def test_a_burst_is_capped(self) -> None:
        for n in range(MAX_QUESTIONS_PER_BURST + 5):
            self._queue("tivtaam", f"מוצר {n}")
        sent = self._ask(reports=None)
        self.assertEqual(sent, MAX_QUESTIONS_PER_BURST)
        self.assertTrue(any("/questions" in m for m in self.recorder.messages))

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


    def test_the_whole_set_arrives_as_one_message(self) -> None:
        """Before 2026-09-11 this was one Telegram message per question:
        80 after a single cycle, each its own notification."""
        for term in ("בצל", "גזר", "שמן זית", "קמח"):
            self._queue("tivtaam", term)
        self._ask(reports=None)
        questions = [m for m in self.recorder.messages if "איזה מהם" in m]
        self.assertEqual(len(questions), 1)
        self.assertIn("1 מתוך 4", questions[0])

    def test_a_single_question_carries_no_counter(self) -> None:
        self._queue("tivtaam", "בצל")
        self._ask(reports=None)
        self.assertNotIn("מתוך", self.recorder.messages[0])

    def test_the_set_is_remembered_for_the_next_tap(self) -> None:
        # Answered over minutes or hours; a restart in between is ordinary,
        # so the queue lives in storage and not in memory.
        import json

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
