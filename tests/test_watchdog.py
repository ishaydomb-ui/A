import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from grocery_bot import watchdog

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _heartbeat(hours_ago: float) -> dict:
    return {"last_run": (NOW - timedelta(hours=hours_ago)).isoformat()}


class HeartbeatTest(unittest.TestCase):
    def test_recent_run_is_healthy(self):
        self.assertTrue(watchdog.check(_heartbeat(0.5), None, None, NOW).ok)

    def test_stopped_timer_is_caught_even_though_nothing_failed(self):
        # The whole point: no unit goes red, no log appears, and the push
        # script keeps reporting success. Only the heartbeat's age shows it.
        health = watchdog.check(_heartbeat(5), None, None, NOW)
        self.assertEqual(health.keys, ["heartbeat_stale"])

    def test_a_quiet_night_is_not_mistaken_for_a_dead_timer(self):
        # No commits for hours is normal; the heartbeat is stamped on
        # no-op runs precisely so the two are distinguishable.
        self.assertTrue(watchdog.check(_heartbeat(1.5), None, None, NOW).ok)

    def test_never_run_is_reported_separately_from_stale(self):
        self.assertEqual(watchdog.check({}, None, None, NOW).keys, ["heartbeat_missing"])

    def test_unparseable_timestamp_is_treated_as_missing(self):
        health = watchdog.check({"last_run": "not-a-date"}, None, None, NOW)
        self.assertEqual(health.keys, ["heartbeat_missing"])

    def test_naive_timestamps_are_accepted(self):
        naive = (NOW - timedelta(minutes=10)).replace(tzinfo=None).isoformat()
        self.assertTrue(watchdog.check({"last_run": naive}, None, None, NOW).ok)


class PushAndTreeTest(unittest.TestCase):
    def test_commits_stuck_locally_are_caught(self):
        health = watchdog.check(
            _heartbeat(0.5), NOW - timedelta(hours=3), None, NOW
        )
        self.assertEqual(health.keys, ["push_failing"])

    def test_a_commit_made_minutes_ago_is_not_a_problem(self):
        self.assertTrue(
            watchdog.check(_heartbeat(0.5), NOW - timedelta(minutes=20), None, NOW).ok
        )

    def test_long_uncommitted_work_is_reported(self):
        health = watchdog.check(
            _heartbeat(0.5), None, NOW - timedelta(hours=20), NOW
        )
        self.assertEqual(health.keys, ["tree_dirty"])

    def test_an_ordinary_working_session_does_not_trip_the_dirty_alarm(self):
        self.assertTrue(
            watchdog.check(_heartbeat(0.5), None, NOW - timedelta(hours=3), NOW).ok
        )

    def test_several_problems_are_reported_together(self):
        health = watchdog.check(
            _heartbeat(5), NOW - timedelta(hours=4), NOW - timedelta(hours=20), NOW
        )
        self.assertEqual(
            health.keys, ["heartbeat_stale", "push_failing", "tree_dirty"]
        )


class TransitionTest(unittest.TestCase):
    def _stale(self):
        return watchdog.check(_heartbeat(5), None, None, NOW)

    def test_a_new_problem_alerts(self):
        action, text = watchdog.transition([], self._stale())
        self.assertEqual(action, "alert")
        self.assertIn("הטיימר כנראה נעצר", text)

    def test_the_same_problem_does_not_alert_again(self):
        health = self._stale()
        self.assertEqual(watchdog.transition(health.keys, health)[0], "silent")

    def test_an_additional_problem_re_alerts(self):
        worse = watchdog.check(_heartbeat(5), NOW - timedelta(hours=3), None, NOW)
        self.assertEqual(watchdog.transition(["heartbeat_stale"], worse)[0], "alert")

    def test_recovery_sends_one_all_clear(self):
        healthy = watchdog.check(_heartbeat(0.2), None, None, NOW)
        action, text = watchdog.transition(["heartbeat_stale"], healthy)
        self.assertEqual(action, "all_clear")
        self.assertIn("חזר לפעול", text)

    def test_staying_healthy_says_nothing(self):
        healthy = watchdog.check(_heartbeat(0.2), None, None, NOW)
        self.assertEqual(watchdog.transition([], healthy)[0], "silent")


class StateFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "nested" / "state.json"

    def test_round_trip(self):
        watchdog.save_state(self.path, ["push_failing", "heartbeat_stale"])
        self.assertEqual(
            watchdog.load_state(self.path), ["heartbeat_stale", "push_failing"]
        )

    def test_missing_file_reads_as_no_prior_report(self):
        self.assertEqual(watchdog.load_state(self.path), [])

    def test_corrupt_file_does_not_stop_the_check(self):
        # Worst case is one duplicate alert; refusing to run would mean no
        # alerting at all, which is the failure being guarded against.
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ not json")
        self.assertEqual(watchdog.load_state(self.path), [])

    def test_state_is_written_where_the_doctor_expects_it(self):
        watchdog.save_state(self.path, [])
        self.assertEqual(json.loads(self.path.read_text())["reported"], [])


if __name__ == "__main__":
    unittest.main()
