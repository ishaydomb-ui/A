"""The list -> cart seam: announce a burst once, then fill the cart once.

Set by Ishay 2026-09-16 21:40 (verbatim, relayed through Miri). The gap
was real and already costing: `cli add-item` inserted a row and nothing
watched the table, so Liran's 20 items of 2026-09-16 sat untouched and
`חלב עמיד`, added 09-11, had been pending five days.

Debounced rather than per-item because a cart add costs ~30s of real
browser: twenty items arriving over three minutes would otherwise mean
twenty sessions and twenty messages.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from grocery_bot import listwatch
from grocery_bot.storage import Storage

NOW = datetime(2026, 9, 16, 21, 40, tzinfo=timezone.utc)


class AssessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def _add(self, text, by="לירן"):
        return self.storage.add_adhoc_request(text, by)

    def test_a_new_item_is_announced(self):
        self._add("בצל ירוק")
        action, items = listwatch.assess(self.storage, NOW)
        self.assertEqual(action, "announce")
        self.assertEqual([i.text for i in items], ["בצל ירוק"])

    def test_an_announced_item_is_not_announced_twice(self):
        self._add("בצל ירוק")
        _, items = listwatch.assess(self.storage, NOW)
        listwatch.note_announced(self.storage, items, NOW)
        action, _ = listwatch.assess(self.storage, NOW)
        self.assertEqual(action, "wait")

    def test_a_burst_still_settling_is_not_run(self):
        self._add("בצל ירוק")
        _, items = listwatch.assess(self.storage, NOW)
        listwatch.note_announced(self.storage, items, NOW)
        # Two minutes later: Liran is still typing.
        action, _ = listwatch.assess(self.storage, NOW + timedelta(minutes=2))
        self.assertEqual(action, "wait")

    def test_a_settled_burst_runs(self):
        self._add("בצל ירוק")
        _, items = listwatch.assess(self.storage, NOW)
        listwatch.note_announced(self.storage, items, NOW)
        action, pending = listwatch.assess(
            self.storage, NOW + timedelta(minutes=listwatch.QUIET_MINUTES + 1)
        )
        self.assertEqual(action, "run")
        self.assertEqual([i.text for i in pending], ["בצל ירוק"])

    def test_a_second_item_restarts_the_quiet_clock(self):
        # The whole point of debouncing: twenty items over three minutes
        # must become one run, not twenty.
        self._add("בצל ירוק")
        _, first = listwatch.assess(self.storage, NOW)
        listwatch.note_announced(self.storage, first, NOW)
        later = NOW + timedelta(minutes=3)
        self._add("דפי אורז")
        action, second = listwatch.assess(self.storage, later)
        self.assertEqual(action, "announce")
        listwatch.note_announced(self.storage, second, later)
        # Ten minutes after the *first* item is still inside the window
        # measured from the second.
        action, _ = listwatch.assess(self.storage, NOW + timedelta(minutes=10))
        self.assertEqual(action, "wait")

    def test_an_empty_list_does_nothing(self):
        self.assertEqual(listwatch.assess(self.storage, NOW), ("wait", []))

    def test_an_item_that_cannot_be_added_does_not_loop_forever(self):
        # A request is consumed only when a store managed it, which is
        # correct — but a permanently failing item would otherwise drive a
        # browser cycle every quiet period, all night.
        self._add("מוצר שלא קיים")
        _, items = listwatch.assess(self.storage, NOW)
        listwatch.note_announced(self.storage, items, NOW)
        settled = NOW + timedelta(minutes=listwatch.QUIET_MINUTES + 1)
        self.assertEqual(listwatch.assess(self.storage, settled)[0], "run")
        listwatch.note_ran(self.storage, settled)
        self.assertEqual(
            listwatch.assess(self.storage, settled + timedelta(minutes=30))[0], "wait"
        )

    def test_a_new_item_beats_the_cooldown(self):
        # Cooling off must not mean ignoring the household.
        self._add("ישן")
        _, items = listwatch.assess(self.storage, NOW)
        listwatch.note_announced(self.storage, items, NOW)
        listwatch.note_ran(self.storage, NOW)
        self._add("חדש")
        action, fresh = listwatch.assess(self.storage, NOW + timedelta(minutes=5))
        self.assertEqual(action, "announce")
        self.assertEqual([i.text for i in fresh], ["חדש"])

    def test_the_cooldown_expires(self):
        self._add("פריט")
        _, items = listwatch.assess(self.storage, NOW)
        listwatch.note_announced(self.storage, items, NOW)
        listwatch.note_ran(self.storage, NOW)
        later = NOW + timedelta(hours=listwatch.COOLDOWN_HOURS + 1)
        self.assertEqual(listwatch.assess(self.storage, later)[0], "run")


class MessageTests(unittest.TestCase):
    class _Item:
        def __init__(self, text, by="לירן"):
            self.text = text
            self.requested_by = by
            self.id = 1

    def test_a_burst_is_one_message_listing_everything(self):
        items = [self._Item(f"פריט {i}") for i in range(20)]
        text = listwatch.format_added(items)
        self.assertIn("נוספו 20 פריטים", text)
        self.assertIn("פריט 0", text)
        self.assertIn("פריט 19", text)

    def test_one_item_is_singular(self):
        text = listwatch.format_added([self._Item("בצל ירוק")])
        self.assertIn("נוסף לרשימה", text)
        self.assertNotIn("נוספו", text)

    def test_it_says_who_added_them(self):
        self.assertIn("לירן", listwatch.format_added([self._Item("בצל")]))

    def test_it_says_when_they_will_reach_the_cart(self):
        # Otherwise the message raises the question it exists to answer.
        text = listwatch.format_added([self._Item("בצל")])
        self.assertIn("לעגלה", text)
        self.assertIn(str(listwatch.QUIET_MINUTES), text)

    def test_nothing_produces_no_message(self):
        self.assertEqual(listwatch.format_added([]), "")


if __name__ == "__main__":
    unittest.main()


class BacklogPhrasingTests(unittest.TestCase):
    """The first tick meets a backlog, not a burst.

    On 2026-09-16 there were 21 pending items, one of them added on 09-11.
    Announcing a five-day-old request as "just added" is the kind of small
    false note that makes the whole message untrustworthy.
    """

    class _Item:
        def __init__(self, text, created_at, by="לירן"):
            self.text = text
            self.created_at = created_at
            self.requested_by = by
            self.id = 1

    def test_old_items_are_described_as_waiting_not_as_new(self):
        old = self._Item("חלב עמיד", "2026-09-11T12:48:00+00:00")
        text = listwatch.format_added([old], NOW)
        self.assertIn("ממתין", text)
        self.assertNotIn("נוסף לרשימה", text)

    def test_fresh_items_are_still_described_as_added(self):
        fresh = self._Item("בצל ירוק", (NOW - timedelta(minutes=2)).isoformat())
        text = listwatch.format_added([fresh], NOW)
        self.assertIn("נוסף לרשימה", text)
        self.assertNotIn("ממתין", text)

    def test_a_mixed_batch_is_called_a_backlog(self):
        # One old item makes "just added" false for the message as a whole.
        items = [
            self._Item("חלב עמיד", "2026-09-11T12:48:00+00:00"),
            self._Item("בצל ירוק", (NOW - timedelta(minutes=1)).isoformat()),
        ]
        self.assertIn("ממתינים", listwatch.format_added(items, NOW))

    def test_an_unparseable_date_does_not_break_the_message(self):
        item = self._Item("משהו", "not a date")
        self.assertIn("משהו", listwatch.format_added([item], NOW))
