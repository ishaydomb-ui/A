"""The planner takes over where `unclear` used to be — and nowhere else.

Measured on 25 message shapes (2026-09-11): the classifier is better at
terse follow-ups and answers the common cases in 7-10s; the planner is
better at everything the taxonomy has no slot for, including two messages
the classifier does not merely miss but **files as grocery items**. And
the planner alone is unsafe as the only path: it returned neither a step
nor a question for "נגמר הקוטג", the plainest message in the set.

So: one seam, at the point of giving up.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import hybrid, planner
from grocery_bot.storage import Storage


class _Plan:
    def __init__(self, steps=(), question="", reply=""):
        self.steps = list(steps)
        self.question = question
        self.reply = reply
        self.refusals = []
        self.model_calls = 1
        self.seconds = 0.0


def _step(tool, **args):
    return planner.Step(tool=tool, args=args)


class TheSeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))
        self._original = planner.plan_message
        self.addCleanup(lambda: setattr(planner, "plan_message", self._original))

    def _plans(self, plan):
        planner.plan_message = lambda text, context=None: plan

    def test_a_plan_becomes_actions(self) -> None:
        self._plans(_Plan([
            _step("add_to_list", item="חלב"),
            _step("price_check", item="טחינה"),
        ], reply="בסדר"))
        parsed = hybrid.reconsider("משהו", self.storage)
        self.assertEqual([a.intent for a in parsed.actions],
                         ["add_item", "price_query"])
        self.assertEqual(parsed.items[0].name, "חלב")

    def test_a_cart_tool_is_understood_and_refused(self) -> None:
        """This is the guessing path. A plan reached from a message nobody
        could classify must not write to the real cart — the same rule as
        loop.CART_INTENTS, for the same reason."""
        self._plans(_Plan([_step("add_to_cart", item="חלב")]))
        parsed = hybrid.reconsider("משהו", self.storage)
        self.assertEqual(parsed.intent, "unclear")
        # Refused, but it says what it understood rather than "I didn't
        # understand" — which would be a lie.
        self.assertIn("עגלה", parsed.reply)

    def test_a_cart_tool_does_not_suppress_the_rest_of_the_plan(self) -> None:
        self._plans(_Plan([
            _step("add_to_cart", item="חלב"),
            _step("price_check", item="טחינה"),
        ]))
        parsed = hybrid.reconsider("משהו", self.storage)
        self.assertEqual([a.intent for a in parsed.actions], ["price_query"])

    def test_every_cart_tool_is_covered(self) -> None:
        # A new cart tool must not silently become runnable from the
        # guessing path.
        cart_tools = {name for name, tool in planner.TOOLS.items() if tool.touches_cart}
        self.assertEqual(cart_tools, set(hybrid.CART_TOOLS))

    def test_a_question_is_passed_through_as_a_question(self) -> None:
        self._plans(_Plan([], question="איזה קוטג בדיוק?"))
        parsed = hybrid.reconsider("משהו", self.storage)
        self.assertEqual(parsed.intent, "unclear")
        self.assertEqual(parsed.reply, "איזה קוטג בדיוק?")

    def test_an_empty_plan_keeps_unclear_for_the_next_pass(self) -> None:
        # None, not a ParsedMessage: the loop pass still gets its turn,
        # so this is strictly additive to what existed before.
        self._plans(_Plan([]))
        self.assertIsNone(hybrid.reconsider("משהו", self.storage))

    def test_an_unavailable_planner_is_not_an_error(self) -> None:
        def _boom(text, context=None):
            raise RuntimeError("claude CLI missing")

        planner.plan_message = _boom
        self.assertIsNone(hybrid.reconsider("משהו", self.storage))

    def test_an_unmapped_tool_is_reported_not_run(self) -> None:
        # remember_preference has no handler mapping yet; it must not
        # quietly become something else.
        self._plans(_Plan([_step("remember_preference", term="קוטג", item="קוטג 5%")]))
        self.assertIsNone(hybrid.reconsider("משהו", self.storage))


if __name__ == "__main__":
    unittest.main()
