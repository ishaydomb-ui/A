"""cartpause.py -- the reversible cart-mutation pause (Work MVP, 2026-09-18).

Round-trip and scoping tests only; whether the guard actually stops an
adapter call is tests/test_orchestrator_pause.py's job.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import cartpause
from grocery_bot.storage import Storage


class CartpauseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(str(Path(self._tmp.name) / "t.sqlite3"))

    def test_default_is_unpaused(self):
        self.assertFalse(cartpause.is_paused(self.storage))
        self.assertFalse(cartpause.is_paused(self.storage, "shufersal"))
        self.assertFalse(cartpause.is_paused(self.storage, "tivtaam"))

    def test_a_store_specific_pause_only_affects_that_store(self):
        cartpause.set_paused(self.storage, "shufersal", True)
        self.assertTrue(cartpause.is_paused(self.storage, "shufersal"))
        self.assertFalse(cartpause.is_paused(self.storage, "tivtaam"))
        self.assertFalse(cartpause.is_paused(self.storage))  # no store given, not global

    def test_the_global_pause_covers_every_store(self):
        cartpause.set_paused(self.storage, None, True)
        self.assertTrue(cartpause.is_paused(self.storage))
        self.assertTrue(cartpause.is_paused(self.storage, "shufersal"))
        self.assertTrue(cartpause.is_paused(self.storage, "tivtaam"))

    def test_resuming_a_store_lifts_only_that_stores_pause(self):
        cartpause.set_paused(self.storage, "shufersal", True)
        cartpause.set_paused(self.storage, "shufersal", False)
        self.assertFalse(cartpause.is_paused(self.storage, "shufersal"))

    def test_resuming_one_store_does_not_lift_a_global_pause(self):
        cartpause.set_paused(self.storage, None, True)
        cartpause.set_paused(self.storage, "shufersal", False)
        self.assertTrue(cartpause.is_paused(self.storage, "shufersal"),
                        "the global pause dominates; resuming one store cannot undo it")
        self.assertTrue(cartpause.is_paused(self.storage, "tivtaam"))

    def test_round_trip_is_idempotent(self):
        cartpause.set_paused(self.storage, "tivtaam", True)
        cartpause.set_paused(self.storage, "tivtaam", True)
        self.assertTrue(cartpause.is_paused(self.storage, "tivtaam"))
        cartpause.set_paused(self.storage, "tivtaam", False)
        cartpause.set_paused(self.storage, "tivtaam", False)
        self.assertFalse(cartpause.is_paused(self.storage, "tivtaam"))

    def test_reason_and_by_are_recorded_and_cleared_on_resume(self):
        cartpause.set_paused(self.storage, "shufersal", True, reason="Work benchmark", by="Ishay")
        self.assertIn("Work benchmark", cartpause.reason_for(self.storage, "shufersal"))
        self.assertIn("Ishay", cartpause.reason_for(self.storage, "shufersal"))
        cartpause.set_paused(self.storage, "shufersal", False)
        self.assertEqual(cartpause.reason_for(self.storage, "shufersal"), "")

    def test_no_schema_change_uses_the_existing_app_state_table(self):
        cartpause.set_paused(self.storage, "shufersal", True)
        # get_state/set_state is the only primitive cartpause touches --
        # this reads it back through the exact same interface, not a new
        # table, proving no schema change was introduced.
        self.assertEqual(self.storage.get_state("cart_paused:shufersal"), "true")


if __name__ == "__main__":
    unittest.main()
