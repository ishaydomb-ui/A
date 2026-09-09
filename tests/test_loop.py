"""The second pass may propose. Only the dispatcher acts.

The tests that matter here are the refusals. Miri measured on
2026-09-09 that asking a prompt to behave held on the first run and was
skipped on the second — so every guarantee below is asserted against
`sanitise`, which sees whatever the model actually returned, rather than
against the prompt that asked for it.
"""
import unittest
from unittest import mock

from grocery_bot import loop, nlu


class TheBarrierHoldsWhateverTheModelSays(unittest.TestCase):
    """These messages are, by definition, ones nobody could parse.
    Turning an unparseable message into a real cart action is the wrong
    direction to resolve doubt in."""

    def test_a_cart_intent_is_refused_and_becomes_a_question(self):
        for intent in ("start_order", "add_to_cart"):
            result = loop.sanitise({"intent": intent, "items": [{"name": "חלב"}]})
            self.assertEqual(result.intent, "unclear", intent)
            self.assertTrue(result.reply, "a refusal must still say something")

    def test_shopped_is_refused_because_it_refills_both_real_carts(self):
        """Its blast radius is a cart, not a log line."""
        result = loop.sanitise({"intent": "shopped"})
        self.assertEqual(result.intent, "unclear")

    def test_every_cart_intent_is_covered(self):
        self.assertEqual(loop.CART_INTENTS, {"start_order", "add_to_cart", "shopped"})
        for intent in loop.CART_INTENTS:
            self.assertIn(intent, nlu.INTENTS, "a guarded intent must be a real one")

    def test_an_invented_intent_is_refused_not_passed_through(self):
        for intent in ("checkout", "pay", "confirm_purchase", "", "ADD_ITEM"):
            self.assertEqual(loop.sanitise({"intent": intent}).intent, "unclear", intent)

    def test_a_refused_cart_intent_keeps_the_models_own_wording_if_any(self):
        result = loop.sanitise({"intent": "add_to_cart", "reply": "להוסיף לסל?"})
        self.assertEqual(result.reply, "להוסיף לסל?")

    def test_a_safe_intent_passes_with_its_items(self):
        result = loop.sanitise({
            "intent": "add_item",
            "items": [{"name": "קוטג'", "amount": "2", "unit": "יחידות"}],
        })
        self.assertEqual(result.intent, "add_item")
        self.assertEqual([i.name for i in result.items], ["קוטג'"])
        self.assertEqual(result.items[0].amount, 2.0)

    def test_malformed_items_are_dropped_not_fatal(self):
        result = loop.sanitise({
            "intent": "add_item",
            "items": ["חלב", {"name": ""}, {"name": "לחם", "amount": "לא מספר"}],
        })
        self.assertEqual([i.name for i in result.items], ["לחם"])
        self.assertIsNone(result.items[0].amount)


class PreloadingIsDoneInCode(unittest.TestCase):
    """A lookup done in code is a lookup that cannot be skipped."""

    def test_context_comes_from_the_database(self):
        storage = mock.Mock()
        storage.list_active_base_items.return_value = [mock.Mock(name="x")]
        storage.list_active_base_items.return_value[0].name = "חלב 3%"
        storage.list_pending_adhoc.return_value = [mock.Mock()]
        storage.list_pending_adhoc.return_value[0].text = "עגבניות"
        context = loop.preload(storage)
        self.assertEqual(context["standing"], ["חלב 3%"])
        self.assertEqual(context["pending"], ["עגבניות"])

    def test_a_database_failure_degrades_the_loop_it_does_not_break_it(self):
        storage = mock.Mock()
        storage.list_active_base_items.side_effect = RuntimeError("db down")
        storage.list_pending_adhoc.side_effect = RuntimeError("db down")
        self.assertEqual(loop.preload(storage), {"standing": [], "pending": []})

    def test_no_storage_is_allowed(self):
        self.assertEqual(loop.preload(None), {"standing": [], "pending": []})


class ReconsiderIsNeverWorseThanNotHavingIt(unittest.TestCase):
    def test_an_unavailable_model_returns_none_so_the_caller_keeps_its_result(self):
        with mock.patch.object(loop, "_ask", side_effect=RuntimeError("no cli")):
            self.assertIsNone(loop.reconsider("משהו", None))

    def test_unparseable_output_returns_none(self):
        with mock.patch.object(loop, "_ask", return_value="not json at all"):
            self.assertIsNone(loop.reconsider("משהו", None))

    def test_a_bare_unclear_adds_nothing_and_returns_none(self):
        with mock.patch.object(loop, "_ask", return_value='{"intent":"unclear"}'):
            self.assertIsNone(loop.reconsider("משהו", None))

    def test_an_unclear_carrying_a_question_is_an_improvement(self):
        with mock.patch.object(
            loop, "_ask", return_value='{"intent":"unclear","reply":"איזה גבינה?"}'
        ):
            result = loop.reconsider("הלבנה", None)
        self.assertIsNotNone(result)
        self.assertEqual(result.reply, "איזה גבינה?")

    def test_an_empty_message_is_not_sent_to_the_model(self):
        with mock.patch.object(loop, "_ask") as ask:
            self.assertIsNone(loop.reconsider("   ", None))
        ask.assert_not_called()


class OnlyUnclearReachesTheLoop(unittest.TestCase):
    """The common path must pay none of the second pass's latency, and a
    confident classification must never be overturned by it."""

    def _classifier_returns(self, payload):
        return mock.patch.object(nlu, "_ask_model", return_value=payload)

    def test_a_confident_result_never_calls_the_loop(self):
        with self._classifier_returns('{"intent":"add_item","items":[{"name":"חלב"}]}'):
            with mock.patch("grocery_bot.loop.reconsider") as second:
                parsed = nlu.parse_message("תוסיף חלב", storage=None)
        second.assert_not_called()
        self.assertEqual(parsed.intent, "add_item")

    def test_an_unclear_result_calls_the_loop(self):
        with self._classifier_returns('{"intent":"unclear"}'):
            with mock.patch("grocery_bot.loop.reconsider") as second:
                second.return_value = nlu.ParsedMessage(intent="show_list")
                parsed = nlu.parse_message("הלבנה", storage=None)
        second.assert_called_once()
        self.assertEqual(parsed.intent, "show_list")

    def test_the_loop_returning_none_leaves_the_original_untouched(self):
        with self._classifier_returns('{"intent":"unclear","reply":"מה?"}'):
            with mock.patch("grocery_bot.loop.reconsider", return_value=None):
                parsed = nlu.parse_message("???", storage=None)
        self.assertEqual(parsed.intent, "unclear")
        self.assertEqual(parsed.reply, "מה?")

    def test_a_loop_that_raises_leaves_the_original_untouched(self):
        with self._classifier_returns('{"intent":"unclear"}'):
            with mock.patch("grocery_bot.loop.reconsider", side_effect=RuntimeError):
                parsed = nlu.parse_message("???", storage=None)
        self.assertEqual(parsed.intent, "unclear")

    def test_the_rule_based_fallback_also_gets_a_second_pass(self):
        """When the CLI is down the fallback is crude; if it lands on
        unclear the loop is still worth one attempt."""
        with mock.patch.object(nlu, "_ask_model", side_effect=RuntimeError("no cli")):
            with mock.patch("grocery_bot.loop.reconsider") as second:
                second.return_value = nlu.ParsedMessage(intent="deals")
                parsed = nlu.parse_message("מה", storage=None)
        second.assert_called_once()
        self.assertEqual(parsed.intent, "deals")


if __name__ == "__main__":
    unittest.main()
