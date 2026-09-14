"""The experimental understanding layer, and the barrier under it.

The branch's premise is that our own taxonomy causes part of the friction
— thirteen intents, and anything that does not fit becomes `unclear`,
which reads to the household as "it did not understand" when in fact the
bot understood and the taxonomy had no slot.

What must not move with it is the execution layer. These tests are about
that: whatever the model proposes, only declared tools with sane
arguments survive, and the things this project does not do are not
expressible at all.
"""
import tempfile
import unittest
from pathlib import Path

from grocery_bot import planner
from grocery_bot.storage import Storage


class TheBarrierHoldsWhateverTheModelProposes(unittest.TestCase):
    def test_checkout_and_payment_are_not_tools_at_all(self) -> None:
        for name in ("checkout", "pay", "place_order", "confirm_purchase",
                     "enter_payment"):
            self.assertNotIn(name, planner.TOOLS, name)
            self.assertIn(name, planner.FORBIDDEN, name)

    def test_a_forbidden_step_is_refused_and_recorded(self) -> None:
        plan = planner.validate({"steps": [
            {"tool": "add_to_list", "args": {"item": "חלב"}},
            {"tool": "checkout", "args": {}},
        ]})
        self.assertEqual([s.tool for s in plan.steps], ["add_to_list"])
        # Recorded, not silently dropped: a refusal nobody can see is
        # indistinguishable from a model that never proposed it.
        self.assertIn("forbidden:checkout", plan.refusals)

    def test_an_invented_tool_cannot_run(self) -> None:
        plan = planner.validate({"steps": [{"tool": "order_taxi", "args": {}}]})
        self.assertEqual(plan.steps, [])
        self.assertIn("unknown:order_taxi", plan.refusals)

    def test_account_changes_are_not_expressible(self) -> None:
        for name in ("update_account", "change_address", "join_club",
                     "apply_coupon", "cancel_order"):
            self.assertIn(name, planner.FORBIDDEN, name)

    def test_the_list_and_the_cart_are_different_tools(self) -> None:
        # Not an argument that could take either value — a plan cannot
        # blur the distinction because there is nowhere to blur it.
        self.assertIn("add_to_list", planner.TOOLS)
        self.assertIn("add_to_cart", planner.TOOLS)
        self.assertNotIn("target", planner.TOOLS["add_to_list"].args)
        self.assertNotIn("target", planner.TOOLS["add_to_cart"].args)

    def test_an_undeclared_argument_is_dropped(self) -> None:
        plan = planner.validate({"steps": [
            {"tool": "price_check", "args": {"item": "טחינה", "discount": "90%"}},
        ]})
        self.assertEqual(plan.steps[0].args, {"item": "טחינה"})

    def test_a_step_missing_what_it_needs_does_not_run(self) -> None:
        plan = planner.validate({"steps": [
            {"tool": "set_cart_quantity", "args": {"item": "קוטג"}},
        ]})
        self.assertEqual(plan.steps, [])
        self.assertIn("set_cart_quantity:missing:quantity", plan.refusals)

    def test_an_absurd_quantity_is_treated_as_unstated(self) -> None:
        # A stray zero or a typo that orders forty is the model's error,
        # not the household's decision.
        plan = planner.validate({"steps": [
            {"tool": "add_to_cart", "args": {"item": "לחם", "quantity": 99}},
        ]})
        self.assertEqual(plan.steps[0].args, {"item": "לחם"})

    def test_a_chain_we_do_not_have_is_dropped(self) -> None:
        plan = planner.validate({"steps": [
            {"tool": "add_to_cart", "args": {"item": "לחם", "store": "rami levy"}},
        ]})
        self.assertNotIn("store", plan.steps[0].args)

    def test_the_number_of_steps_is_capped(self) -> None:
        plan = planner.validate({"steps": [
            {"tool": "add_to_list", "args": {"item": f"מוצר {n}"}} for n in range(20)
        ]})
        self.assertEqual(len(plan.steps), planner.MAX_STEPS)

    def test_a_question_survives_with_no_steps(self) -> None:
        plan = planner.validate({"steps": [], "question": "איזה קוטג?"})
        self.assertEqual(plan.question, "איזה קוטג?")
        self.assertEqual(plan.steps, [])

    def test_rubbish_is_a_plan_that_does_nothing(self) -> None:
        for payload in ({}, {"steps": "no"}, {"steps": [None, 3, "x"]}):
            self.assertEqual(planner.validate(payload).steps, [])


class ThePromptCarriesTheRealState(unittest.TestCase):
    def test_the_catalogue_is_the_interface(self) -> None:
        prompt = planner.build_prompt("תוסיף חלב", {})
        for name in planner.TOOLS:
            self.assertIn(name, prompt, name)
        # Named only as a prohibition, never offered as a tool line.
        self.assertNotIn("- checkout:", prompt)
        self.assertIn("אין checkout", prompt)

    def test_context_is_marked_as_background_not_as_the_message(self) -> None:
        prompt = planner.build_prompt(
            "בעצם שניים", {"last_subject": "קוטג 5%"}
        )
        self.assertIn("רקע", prompt)
        self.assertIn("קוטג 5%", prompt)
        self.assertIn("בעצם שניים", prompt)

    def test_no_context_is_not_an_error(self) -> None:
        self.assertEqual(planner.describe_context({}), "")


class ContextIsReadNeverGuessed(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.storage = Storage(str(Path(self._tmpdir.name) / "t.sqlite3"))

    def test_pending_requests_and_last_subject_are_carried(self) -> None:
        from grocery_bot import convo, plancontext

        self.storage.add_adhoc_request("טחינה גולמית", "ishay")
        convo.remember(self.storage, subject="קוטג 5%", store="shufersal")
        context = plancontext.build(self.storage)
        self.assertEqual(context["pending"], ["טחינה גולמית"])
        self.assertEqual(context["last_subject"], "קוטג 5%")

    def test_an_unreadable_cart_is_absent_not_empty(self) -> None:
        """A plan built on an imagined cart would decline to add the milk
        because it "knows" it is already there."""
        from grocery_bot import plancontext

        class _Broken:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cart_summary(self):
                raise RuntimeError("cart page timed out")

        carts = plancontext._read_carts({"shufersal": _Broken})
        self.assertEqual(carts, {})


if __name__ == "__main__":
    unittest.main()
