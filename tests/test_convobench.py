"""Grading logic for the conversation-backend benchmark — no model calls.

The benchmark's own correctness (does `Row.tool_correct` mean what it
says) has to be right before its numbers mean anything.
"""
import unittest

from grocery_bot.convobench import Message, Row, run_shortcut


def _msg(**kw):
    return Message(id="t", group="t", text=kw.pop("text", "x"), **kw)


class RowGradingTests(unittest.TestCase):
    def test_a_matching_single_tool_is_correct(self):
        row = Row(_msg(expected_tools=("add_to_list",)), "x", tools=["add_to_list"])
        self.assertTrue(row.tool_correct)

    def test_a_wrong_tool_is_not_correct(self):
        row = Row(_msg(expected_tools=("add_to_list",)), "x", tools=["remove_from_list"])
        self.assertFalse(row.tool_correct)

    def test_tool_order_does_not_matter_for_multi_tool_messages(self):
        row = Row(_msg(expected_tools=("add_to_list", "price_check")), "x",
                  tools=["price_check", "add_to_list"])
        self.assertTrue(row.tool_correct)

    def test_a_message_that_expects_a_question_is_correct_only_when_asked_and_no_tool_ran(self):
        row = Row(_msg(expects_question=True), "x", tools=[], asked=True)
        self.assertTrue(row.tool_correct)
        wrong = Row(_msg(expects_question=True), "x", tools=["add_to_list"])
        self.assertFalse(wrong.tool_correct)

    def test_asking_when_a_tool_was_expected_is_an_unnecessary_question(self):
        row = Row(_msg(expected_tools=("add_to_list",)), "x", tools=[], asked=True)
        self.assertTrue(row.unnecessary_question)

    def test_not_asking_when_a_question_was_needed_but_a_tool_ran_is_a_missed_question(self):
        row = Row(_msg(expects_question=True), "x", tools=["add_to_list"], asked=False)
        self.assertTrue(row.missed_necessary_question)

    def test_a_forbidden_tool_name_is_unsafe(self):
        row = Row(_msg(), "x", tools=["checkout"])
        self.assertTrue(row.unsafe)

    def test_a_hallucinated_tool_name_is_unsafe(self):
        row = Row(_msg(), "x", tools=["teleport_groceries"])
        self.assertTrue(row.unsafe)

    def test_a_real_catalogued_tool_is_not_unsafe(self):
        row = Row(_msg(), "x", tools=["add_to_list", "price_check"])
        self.assertFalse(row.unsafe)

    def test_a_declined_shortcut_is_never_graded_correct(self):
        row = Row(_msg(expected_tools=("add_to_list",)), "shortcut", tools=[], declined=True)
        self.assertFalse(row.tool_correct)


class ShortcutTests(unittest.TestCase):
    def test_a_bare_add_with_no_context_matches(self):
        row = run_shortcut(_msg(text="תוסיף חלב"))
        self.assertEqual(row.tools, ["add_to_list"])
        self.assertFalse(row.declined)
        self.assertEqual(row.model_calls, 0)

    def test_a_correction_is_declined_not_guessed(self):
        row = run_shortcut(_msg(text="בעצם שניים"))
        self.assertTrue(row.declined)

    def test_context_with_a_standing_subject_defers_even_to_a_plain_looking_add(self):
        row = run_shortcut(_msg(text="תוסיף חלב", context={"last_subject": "קוטג"}))
        self.assertTrue(row.declined)

    def test_a_multi_request_message_is_declined(self):
        row = run_shortcut(_msg(text="תוסיף חלב וכמה עולה טחינה?"))
        self.assertTrue(row.declined)

    def test_never_a_model_call(self):
        for text in ("תוסיף חלב", "בעצם שניים", "מה זה", "X במקום Y"):
            self.assertEqual(run_shortcut(_msg(text=text)).model_calls, 0)


if __name__ == "__main__":
    unittest.main()
