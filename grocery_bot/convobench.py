"""The conversation-backend benchmark — mandate Phase 11, run 2026-09-17.

Compares four ways of turning a Hebrew household message into an action:

    classifier      nlu.parse_message         — the deployed default
    planner         planner.plan_message      — one-shot JSON plan
    agent_stateless agentconvo.ask_once       — one Agent-SDK call, no memory
    agent_session   agentconvo.AgentSession   — a real multi-turn session

`deterministic_shortcuts` is a fifth, much smaller thing: a regex fast
path for the handful of message shapes plain enough to need no model at
all. It is graded on the same suite to see how much of the traffic it
could take off the other four, not to compete with them on the hard
cases.

Every run here is **plan-only** — the same discipline
`scripts/compare_understanding.py` set in 2026-09-11: nothing is added
to a real list, no adapter opens, no cart changes. `agentconvo.ask_once`
and `AgentSession(bench=True)` record tool calls into a `_StepSink`
instead of dispatching them; `nlu`/`planner` were already side-effect
free when given no `storage`.

The message suite below is fixed, not cherry-picked per run — it is the
`SUITE` list, and it includes every example message the 2026-09-17
mandate itself gave, verbatim, plus enough of the existing
`compare_understanding.py` fixture shapes to cover corrections, multi-
request messages, and cases with no clean taxonomy slot.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import agentconvo, nlu, planner

# -- ground truth ------------------------------------------------------------

# Which tool a classifier intent corresponds to, for grading purposes
# only. "remove_item" always means the internal list — the classifier
# has never had a way to remove one specific line from a live cart.
_INTENT_TO_TOOL = {
    "add_item": "add_to_list",
    "remove_item": "remove_from_list",
    "price_query": "price_check",
    "deals": "show_deals",
    "show_list": "show_list",
    "recipe": "recipe",
    "meal_plan": "meal_plan",
    "start_order": "fill_cart",
    "add_to_cart": "add_to_cart",
    "report_waste": "report_waste",
    "shopped": "report_shopped",
    "change_quantity": "set_cart_quantity",
    "replace_item": "replace_in_cart",
}


@dataclass
class Message:
    id: str
    group: str
    text: str
    context: dict = field(default_factory=dict)
    # The tool(s) a correct answer calls, in any order. Empty means "a
    # genuine question is the only correct answer" — see `expects_question`.
    expected_tools: tuple = ()
    expects_question: bool = False
    note: str = ""


# What the previous turn was, for every fixture that leans on it — the
# same discipline `compare_understanding.py` used: without this, a
# follow-up is unanswerable *in principle* by any backend, which would
# not measure understanding, only the absence of memory.
_CONTEXT = {
    "last_subject": "קוטג 5% תנובה 250 גרם",
    "last_store": "shufersal",
    "pending": ["טחינה גולמית", "לחם אחיד"],
    "carts": {"shufersal": {"count": 14, "items": ["קוטג 5% תנובה", "לחם אחיד"]}},
    "stores": ["shufersal", "tivtaam"],
    "open_questions": 3,
}

SUITE: list[Message] = [
    # -- control: plain, unambiguous requests --------------------------------
    Message("plain_add", "control", "תוסיף חלב", expected_tools=("add_to_list",)),
    Message("plain_missing", "control", "נגמר הקוטג", expected_tools=("add_to_list",)),
    Message("plain_amount", "control", "צריך 3 קילו עגבניות", expected_tools=("add_to_list",)),
    Message("plain_price", "control", "כמה עולה טחינה גולמית", expected_tools=("price_check",)),
    Message("plain_deals", "control", "מה יש במבצע", expected_tools=("show_deals",)),
    Message("plain_list", "control", "תראה לי את הרשימה", expected_tools=("show_list",)),
    Message("plain_shopped", "control", "סיימתי לקנות", expected_tools=("report_shopped",)),
    Message("plain_recipe", "control", "מתכון לשקשוקה", expected_tools=("recipe",)),
    # -- the mandate's own examples, verbatim --------------------------------
    Message("m1_add", "mandate", "תוסיף חלב", expected_tools=("add_to_list",)),
    Message("m2_also_add", "mandate", "תוסיף גם שני מלפפונים",
            context={**_CONTEXT, "last_subject": "חלב"},
            expected_tools=("add_to_list",), note="a second add, not a correction"),
    Message("m3_correction", "mandate", "בעצם בלי המלפפונים",
            context={**_CONTEXT, "last_subject": "מלפפונים"},
            expected_tools=("remove_from_list",)),
    Message("m4_pasted_recipe", "mandate", "לירן שלחה מתכון — תכניס מה שחסר",
            expects_question=True,
            note="no recipe text is actually in the message; the only honest answer is to ask for it"),
    Message("m5_weekly", "mandate", "תכין קנייה לשבוע", expected_tools=("meal_plan",)),
    Message("m6_replace", "mandate", "תחליף את הקוטג בקוטג 5% אחר",
            context=_CONTEXT, expected_tools=("replace_in_cart",)),
    Message("m7_missing", "mandate", "מה חסר לנו?", expected_tools=("show_list",),
            note="no dedicated 'what's missing' tool exists; show_list is the closest real capability"),
    Message("m8_deals_no_substitute", "mandate",
            "שים מה שבמבצע אבל לא תחליף מותגים שאני רגיל אליהם",
            expected_tools=("show_deals", "fill_cart"),
            note="either reading (show what's on sale, or actually fill with deals) is defensible; "
                 "a wrong answer is one that promises brand substitution will now happen automatically"),
    # -- corrections that only mean something in context ---------------------
    Message("f_qty_only", "followup", "בעצם שניים", context=_CONTEXT,
            expected_tools=("set_cart_quantity",)),
    Message("f_replace_terse", "followup", "השני במקום הראשון", context=_CONTEXT,
            expected_tools=("replace_in_cart",)),
    Message("f_scope_once", "followup", "את זה רק הפעם", context=_CONTEXT,
            expects_question=True, note="a scope marker with no subject; classifier alone can't act on it"),
    Message("f_pick_other", "followup", "לא זה, השני", context=_CONTEXT,
            expected_tools=("replace_in_cart",)),
    Message("f_set_qty", "followup", "תעשה 3", context=_CONTEXT,
            expected_tools=("set_cart_quantity",)),
    # -- two requests in one message ------------------------------------------
    Message("multi_add_price", "multi", "תוסיף חלב וכמה עולה טחינה?",
            expected_tools=("add_to_list", "price_check")),
    Message("multi_remove_add", "multi", "תוריד את הלחם ותוסיף פיתות",
            expected_tools=("remove_from_list", "add_to_list")),
    Message("multi_three", "multi", "קוטג, גבינה צהובה, ומה המחיר של שמן זית?",
            expected_tools=("add_to_list", "price_check")),
    # -- scope and partial completion ------------------------------------------
    Message("scope_partial_shop", "scope", "סיימתי בשופרסל, בטיב טעם עוד לא",
            expected_tools=("report_shopped",)),
    Message("scope_rest", "scope", "את כל השאר כרגיל", expects_question=True,
            note="carries no product and no clear referent even with context"),
    # -- loose phrasing with no clean taxonomy slot -----------------------------
    Message("loose_swap_prior", "loose", "לא הגבינה הזאת, תחליף לזו שקנינו בפעם הקודמת",
            context=_CONTEXT, expected_tools=("replace_in_cart",)),
    Message("loose_dish_for_n", "loose", "תכין לי קניות לביף בורגיניון לשישה",
            expected_tools=("recipe",)),
    Message("loose_ready", "loose", "העגלה מוכנה?", expected_tools=("show_cart",)),
    Message("loose_for_kids", "loose", "אפשר משהו לילדים לבית ספר", expects_question=True,
            note="genuinely open-ended; a specific product guess here would be inventing one"),
]

# A short, ordered real conversation for `agent_session` only — the point
# is genuine memory across turns, not an injected context dict. Each
# entry after the first refers to something only the *conversation*, not
# a context blob, establishes.
SESSION_FLOW: list[Message] = [
    Message("s1", "session", "תוסיף חלב", expected_tools=("add_to_list",)),
    Message("s2", "session", "תוסיף גם שני מלפפונים", expected_tools=("add_to_list",)),
    Message("s3", "session", "בעצם בלי המלפפונים", expected_tools=("remove_from_list",)),
    Message("s4", "session", "מה חסר לנו?", expected_tools=("show_list",)),
    Message("s5", "session", "תחליף את החלב בחלב עמיד", expected_tools=("replace_in_cart",)),
]

# -- deterministic shortcuts: no model, a small regex fast path --------------

_SHORTCUT_ADD = re.compile(r"^(?:תוסיף|תוסיפי|צריך|צריכים|תקנה|לקנות|תביא)\s+([\w\"'׳״ ]{2,30})$")


def run_shortcut(message: Message) -> "Row":
    started = time.monotonic()
    m = _SHORTCUT_ADD.match(message.text.strip())
    seconds = time.monotonic() - started
    if m and not message.context.get("last_subject"):
        # Deliberately conservative: only a bare, unambiguous add with no
        # standing context to potentially override it.
        return Row(message, "shortcut", tools=["add_to_list"], asked=False,
                   seconds=seconds, model_calls=0)
    return Row(message, "shortcut", tools=[], asked=False, seconds=seconds, model_calls=0,
               declined=True)


# -- rows and grading ----------------------------------------------------------

@dataclass
class Row:
    message: Message
    backend: str
    tools: list = field(default_factory=list)
    asked: bool = False
    reply: str = ""
    seconds: float = 0.0
    model_calls: int = 0
    cost_usd: float | None = None
    declined: bool = False   # shortcut only: "not confident, defer to a real backend"
    error: str = ""

    @property
    def tool_correct(self) -> bool:
        if self.declined:
            return False
        expected = set(self.message.expected_tools)
        if not expected:
            return self.asked and not self.tools
        return set(self.tools) == expected

    @property
    def unnecessary_question(self) -> bool:
        return self.asked and not self.tools and bool(self.message.expected_tools)

    @property
    def missed_necessary_question(self) -> bool:
        return self.message.expects_question and not self.asked and bool(self.tools)

    @property
    def unsafe(self) -> bool:
        allowed = set(planner.TOOLS) | {""}
        return any(t not in allowed for t in self.tools) or any(
            t in planner.FORBIDDEN for t in self.tools
        )


def run_classifier(message: Message) -> Row:
    started = time.monotonic()
    parsed = nlu.parse_message(message.text, storage=None, context=message.context)
    seconds = time.monotonic() - started
    actions = parsed.actions or [None]
    tools = []
    for a in (parsed.actions or []):
        tool = _INTENT_TO_TOOL.get(a.intent)
        if tool:
            tools.append(tool)
    if not tools and parsed.intent not in ("unclear", "smalltalk"):
        tool = _INTENT_TO_TOOL.get(parsed.intent)
        if tool:
            tools.append(tool)
    asked = parsed.intent in ("unclear", "smalltalk") or (bool(parsed.reply) and not tools)
    return Row(message, "classifier", tools=tools, asked=asked, reply=parsed.reply,
               seconds=seconds, model_calls=1 if not parsed.used_fallback else 0)


def run_planner(message: Message) -> Row:
    plan = planner.plan_message(message.text, message.context)
    tools = [s.tool for s in plan.steps]
    return Row(message, "planner", tools=tools, asked=bool(plan.question),
               reply=plan.reply or plan.question, seconds=plan.seconds,
               model_calls=plan.model_calls)


def run_agent_stateless(message: Message) -> Row:
    import asyncio

    context_text = planner.describe_context(message.context)
    turn = asyncio.run(agentconvo.ask_once(message.text, context_text))
    return Row(message, "agent_stateless", tools=[s.tool for s in turn.steps],
               asked=turn.asked, reply=turn.reply, seconds=turn.seconds,
               model_calls=turn.model_calls, cost_usd=turn.cost_usd, error=turn.error)


def run_agent_session(messages: list[Message]) -> list[Row]:
    import asyncio

    session = agentconvo.AgentSession(bench=True)

    async def _run_all():
        rows = []
        for m in messages:
            turn = await session.ask(m.text)
            rows.append(Row(m, "agent_session", tools=[s.tool for s in turn.steps],
                            asked=turn.asked, reply=turn.reply, seconds=turn.seconds,
                            model_calls=turn.model_calls, cost_usd=turn.cost_usd, error=turn.error))
        await session.close()
        return rows

    return asyncio.run(_run_all())


BACKENDS = {
    "classifier": run_classifier,
    "planner": run_planner,
    "agent_stateless": run_agent_stateless,
    "shortcut": run_shortcut,
}


def summarise(rows: list[Row]) -> dict:
    n = len(rows) or 1
    return {
        "n": len(rows),
        "correct": sum(r.tool_correct for r in rows),
        "asked": sum(r.asked for r in rows),
        "unnecessary_question": sum(r.unnecessary_question for r in rows),
        "missed_question": sum(r.missed_necessary_question for r in rows),
        "unsafe": sum(r.unsafe for r in rows),
        "declined": sum(r.declined for r in rows),
        "errors": sum(bool(r.error) for r in rows),
        "seconds_total": round(sum(r.seconds for r in rows), 1),
        "seconds_median": round(sorted(r.seconds for r in rows)[len(rows) // 2], 1) if rows else 0.0,
        "model_calls": sum(r.model_calls for r in rows),
        "cost_usd": round(sum(r.cost_usd or 0.0 for r in rows), 4),
    }
