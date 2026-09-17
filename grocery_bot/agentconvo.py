"""A persistent, tool-calling conversational backend, built on `claude-agent-sdk`.

Phase 2 of the "next product layer" (2026-09-17, after Ishay lifted the
five-clean-runs gate: "BUILD FORWARD, VALIDATE CONTINUOUSLY"). This is
the candidate the mandate's conversation-backend comparison names as
option C, evaluated in `convobench.py` against the classifier and the
planner. See `docs/reports/2026-09-17-conversation-backend-benchmark.md`
for the numbers this was built to be measured by.

**It does not invent a new tool surface.** Every tool this module can
offer the model is `planner.TOOLS` — the exact same catalogue the direct
planner already validates against, `FORBIDDEN` names excluded by
construction because they were never in that catalogue to begin with.
Argument cleaning reuses `planner._clean_args` verbatim (quantity
clamped 1-12, store one of the two real chains, scope one of
once/always) — the same guardrail, not a second one that could drift.

**It does not execute anything itself.** A tool call is dispatched to
the same Telegram-handler methods and the same `replace.py` /
`execution.py` functions the classifier path already runs, chosen
per-tool in `_dispatch_live` below. The agent decides *that* something
should happen and *what*; the deterministic code that decides *how* —
identity resolution, cart-run tracking, verification, the breaker,
outcome reporting — is unchanged and untouched. A tool this project has
not built a safe execution path for (removing one specific line from a
live cart; writing a preference with no resolved product) declines
honestly rather than improvising one, matching the project's existing
rule for a blocked step.

**Containment, the way Nigel's familyos build established it (see
HANDOFF.md 2026-09-16 — verified here again independently rather than
assumed):** `tools=[]` (no built-in filesystem/bash/web tools),
`setting_sources=[]` (no CLAUDE.md, no project settings — measured live
2026-09-17: leaving this on the repo's own `cwd` cost ~46k cache-creation
tokens per call; a scratch `cwd` outside any git repo cut that to ~1.4k),
`permission_mode="dontAsk"` (not `bypassPermissions` — nobody is at a
keyboard to be asked, and `dontAsk` is Nigel's own considered choice for
that reason), and an explicit `allowed_tools` list built from the same
catalogue. No checkout/payment/account tool exists in `planner.TOOLS`,
so none can be exposed here regardless of what the model asks for.

Off by default. `GORDON_CONVO_BACKEND=agent` turns it on; unset or any
other value keeps the classifier chain (`nlu.parse_message`) exactly as
it was. Flipping the flag back is the whole rollback.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query as sdk_query,
    tool as sdk_tool,
)

from . import planner

logger = logging.getLogger(__name__)

# A scratch directory outside any git repo and with no CLAUDE.md of its
# own. Measured live 2026-09-17: running the SDK with `cwd` left at this
# project's own directory pulled in ~46,000 cache-creation tokens per
# call (the repo's CLAUDE.md and this session's own auto-memory system);
# a bare scratch dir cut that to ~1,400. Created lazily, never written to
# by the model — it has no filesystem tool to write with anyway.
SCRATCH_CWD = Path.home() / ".gordon_agent_scratch"

MAX_TURNS = 8
SERVER_NAME = "gordon"

Dispatch = Callable[[str, dict], Awaitable[str]]

_ARG_JSON_TYPE = {
    "item": "string", "quantity": "number", "amount": "number", "unit": "string",
    "brand": "string", "store": "string", "old": "string", "new": "string",
    "dish": "string", "servings": "number", "note": "string", "term": "string",
    "scope": "string",
}

# Every tool that reaches the real cart — the same set `planner.Tool`
# already tags with `touches_cart`, read from there rather than kept as
# a second list.
CART_TOOLS = frozenset(t.name for t in planner.TOOLS.values() if t.touches_cart)

# Measured 2026-09-17 (the first live benchmark run): given an injected
# background that merely *describes* an existing cart, the model reached
# for `remove_from_cart`/`add_to_cart` on a plain list correction twice —
# the more expensive wrong answer, in the direction the classifier's own
# prompt was written to avoid. A real persistent session with no injected
# background at all got the same correction right. The fix is not a
# longer prompt: cart tools are only made callable at all when the
# household's own *current* message names the cart or a store — never
# from background/context text, which is exactly what leaked before.
_CART_WORDS = ("עגלה", "עגלת", "בעגלה", "לעגלה", "עגלתי", "סל", "לסל", "בסל",
               "שופרסל", "טיב טעם", "tivtaam", "shufersal")


def _mentions_cart(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in _CART_WORDS)

_SYSTEM_PROMPT_HEADER = """אתה גורדון, שכבת השיחה של בוט קניות משפחתי בעברית. אתה לא מסווג הודעות —
אתה מבין מה המשפחה רוצה ומפעיל את הכלים העומדים לרשותך כדי לבצע את זה.

כללים:
- הפעל כלי לכל בקשה שאפשר לבצע. אל תסביר מה תעשה בלי לעשות — קרא לכלי.
- הודעה אחת יכולה להכיל כמה בקשות. הפעל כלי לכל אחת מהן, בסדר שנאמרו.
- שאל שאלה קצרה אחת, בטקסט, רק כשיש עמימות **מהותית** שאי אפשר להכריע
  מהשיחה עד כה (איזה מוצר בדיוק, איזו רשת ששתיהן פתוחות) — אל תשאל על
  מה שאפשר להסיק מהשיחה.
- אל תמציא שמות מוצרים, מחירים, ברקודים או כמויות שלא נאמרו. הכלים עצמם
  מוצאים את המוצר ומבצעים את הפעולה — אתה מוסר מה המשפחה ביקשה.
- הבחנה קבועה: "רשימה" = הרשימה הפנימית, נכנסת למחזור הבא. "עגלה"/"סל"
  = האתר האמיתי, פעולה מיידית.
- אין checkout, אין תשלום, אין שינוי פרטי חשבון — אלה לא קיימים ככלים,
  ולעולם אל תרמוז שביצעת אותם.
- אחרי שהפעלת כלי, אל תחזור גם על תשובה בטקסט — הכלי עצמו כבר הודיע
  למשפחה מה קרה. תשובת טקסט שלך מוצגת רק כשלא הפעלת שום כלי.

הכלים:
"""


def enabled() -> bool:
    return os.environ.get("GORDON_CONVO_BACKEND", "classifier").strip().lower() == "agent"


def _tool_catalogue_text() -> str:
    lines = []
    for tool in planner.TOOLS.values():
        args = ", ".join(f"{k} ({v})" for k, v in tool.args.items()) or "ללא"
        lines.append(f"- {tool.name}: {tool.description}. פרמטרים: {args}")
    return "\n".join(lines)


def system_prompt() -> str:
    return _SYSTEM_PROMPT_HEADER + _tool_catalogue_text()


def _schema_for(tool: planner.Tool) -> dict:
    """A real JSON Schema, not the SDK's plain-dict shortcut.

    The shortcut (`{"item": str}`) marks every key required, and most of
    these tools have genuinely optional arguments (quantity, store) —
    marking them required would force the model to invent a value for
    something nobody said, which is exactly what the project's guardrails
    exist to prevent.
    """
    props = {
        name: {"type": _ARG_JSON_TYPE.get(name, "string"), "description": desc}
        for name, desc in tool.args.items()
    }
    return {"type": "object", "properties": props, "required": list(tool.required)}


def _clean(tool: planner.Tool, raw_args: dict) -> tuple[dict | None, list[str]]:
    """The same cleaning the planner applies to its own JSON plans.

    Reused verbatim rather than reimplemented: a quantity out of 1-12, an
    unrecognised store, or a scope that isn't once/always is dropped the
    same way here as there. `planner._clean_args` wants a `Plan` to
    collect refusals into; a scratch one is created and discarded — only
    its `refusals` list is read.
    """
    scratch = planner.Plan()
    clean = planner._clean_args(tool, raw_args or {}, scratch)  # noqa: SLF001
    return clean, scratch.refusals


def _make_handler(tool: planner.Tool, dispatch: Dispatch):
    async def handler(args: dict) -> dict:
        clean, refusals = _clean(tool, args)
        if clean is None:
            text = "חסר מידע כדי לבצע את זה" + (f" ({'; '.join(refusals)})" if refusals else "")
            return {"content": [{"type": "text", "text": text}]}
        try:
            text = await dispatch(tool.name, clean)
        except Exception:  # noqa: BLE001
            logger.exception("agentconvo: tool %s failed", tool.name)
            text = "הפעולה נכשלה בצד השרת — דווח כשגיאה, לא כהצלחה."
        return {"content": [{"type": "text", "text": text}]}

    return handler


def build_mcp_tools(dispatch: Dispatch) -> list:
    """One SDK tool per `planner.TOOLS` entry — the single tool catalogue.

    `planner.FORBIDDEN` names are absent from `planner.TOOLS` in the
    first place, so they are structurally unavailable here: there is no
    branch to add them to, not merely an instruction not to use them.
    """
    return [
        sdk_tool(t.name, t.description, _schema_for(t))(_make_handler(t, dispatch))
        for t in planner.TOOLS.values()
    ]


def build_options(
    dispatch: Dispatch, *, max_turns: int = MAX_TURNS,
    restrict_cart: bool = False, can_use_tool=None,
) -> ClaudeAgentOptions:
    """`restrict_cart=True` removes every cart-touching tool from what the
    model may call at all — for a one-shot call where the current message
    is already known not to mention the cart or a store. A persistent
    session instead passes `can_use_tool`, since its message changes every
    turn and its options are built once at connect time.
    """
    SCRATCH_CWD.mkdir(parents=True, exist_ok=True)
    tools = build_mcp_tools(dispatch)
    server = create_sdk_mcp_server(name=SERVER_NAME, tools=tools)
    names = [t.name for t in planner.TOOLS.values() if not (restrict_cart and t.name in CART_TOOLS)]
    return ClaudeAgentOptions(
        tools=[],
        setting_sources=[],
        cwd=str(SCRATCH_CWD),
        mcp_servers={SERVER_NAME: server},
        allowed_tools=[f"mcp__{SERVER_NAME}__{n}" for n in names],
        permission_mode="dontAsk",
        system_prompt=system_prompt(),
        max_turns=max_turns,
        can_use_tool=can_use_tool,
    )


@dataclass
class AgentTurn:
    """One message, answered. The same shape `planner.Plan` uses, so the
    benchmark can compare them on identical fields."""

    steps: list = field(default_factory=list)   # planner.Step, in call order
    reply: str = ""
    asked: bool = False
    model_calls: int = 0
    seconds: float = 0.0
    cost_usd: float | None = None
    error: str = ""


class _StepSink:
    """A dispatch that only records — used wherever nothing may actually run.

    Both the benchmark (never touch a real cart while measuring
    understanding) and a stateless one-off call use this: recording is
    the only safe thing to do without a real household conversation
    behind it to hold the outcome.
    """

    def __init__(self) -> None:
        self.steps: list[planner.Step] = []

    async def __call__(self, name: str, args: dict) -> str:
        self.steps.append(planner.Step(tool=name, args=args))
        return "נרשם."


async def ask_once(message: str, context_text: str = "") -> AgentTurn:
    """One stateless call — no session, no memory kept afterwards.

    Used by the benchmark's `agent_stateless` backend, so it is measured
    on the same footing as the classifier and the planner: one message
    in, no real mutation, a plan out.
    """
    import time

    sink = _StepSink()
    started = time.monotonic()
    prompt = message if not context_text else f"{context_text}\n\nההודעה:\n\"{message}\""
    turn = AgentTurn()
    try:
        # Only the current message unlocks a cart tool — never the
        # injected background, which describes a cart whether or not
        # this particular message is about it (see CART_TOOLS above).
        options = build_options(sink, max_turns=MAX_TURNS, restrict_cart=not _mentions_cart(message))
        async for msg in sdk_query(prompt=prompt, options=options):
            _consume(msg, turn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agentconvo: ask_once failed", exc_info=True)
        turn.error = f"{type(exc).__name__}: {exc}"
    turn.steps = sink.steps
    turn.asked = not sink.steps and bool(turn.reply)
    turn.seconds = time.monotonic() - started
    return turn


def _consume(msg, turn: AgentTurn) -> None:
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                turn.reply = block.text
    if isinstance(msg, ResultMessage):
        turn.model_calls = msg.num_turns or 1
        turn.cost_usd = msg.total_cost_usd


class AgentSession:
    """A persistent conversation, one per chat — the actual product feature.

    Where `ask_once` measures the model's understanding in isolation,
    this is what a household actually talks to: the SDK session keeps
    its own memory of the exchange, so a follow-up ("בעצם שניים") is
    resolved from real conversation history rather than a hand-built
    context dict. `bench=True` keeps every tool call as a recorded step
    with no real dispatch (used by `agent_session` in the benchmark);
    `bench=False` (the production default) dispatches through
    `_dispatch_live`, which is where every tool call actually reaches
    the household's cart, list, or storage — through the same functions
    the classifier path already uses.
    """

    def __init__(self, bot=None, *, bench: bool = False) -> None:
        self.bot = bot
        self.bench = bench
        self.sink = _StepSink() if bench else None
        self.turn_update = None
        self.turn_context = None
        self.turn_requested_by = ""
        self.last_tool_calls: list[tuple[str, dict]] = []
        self._current_text = ""
        self._client: ClaudeSDKClient | None = None
        self._lock = asyncio.Lock()

    async def _dispatch(self, name: str, args: dict) -> str:
        self.last_tool_calls.append((name, args))
        if self.bench:
            return await self.sink(name, args)
        return await _dispatch_live(self.bot, self, name, args)

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        bare = tool_name.rsplit("__", 1)[-1]
        if bare in CART_TOOLS and not _mentions_cart(self._current_text):
            # A persistent session's options are built once at connect
            # time, so unlike `ask_once` this can't just drop the tool
            # from `allowed_tools` per turn — the same rule is enforced
            # here instead, checked against the message that started
            # *this* turn, never the session's accumulated memory.
            return PermissionResultDeny(
                message="ההודעה הזו לא הזכירה עגלה או רשת בשם — זו כנראה בקשה "
                        "לרשימה הפנימית, לא לעגלה עצמה. אם באמת התכוונת לעגלה, "
                        "תגיד את זה בפירוש."
            )
        return PermissionResultAllow()

    async def _ensure_connected(self) -> None:
        if self._client is not None:
            return
        options = build_options(self._dispatch, can_use_tool=self._can_use_tool)
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()

    async def ask(self, message: str) -> AgentTurn:
        """Bench-mode entry: send one message, return the turn. No Telegram side effects."""
        import time

        async with self._lock:
            await self._ensure_connected()
            self._current_text = message
            self.last_tool_calls = []
            started = time.monotonic()
            turn = AgentTurn()
            await self._client.query(message)
            async for msg in self._client.receive_response():
                _consume(msg, turn)
            # `_dispatch` appends to `last_tool_calls` on every call, reset
            # above for this turn, in both bench and live mode — always
            # read from there. `self.sink.steps` is a session lifetime
            # list (kept only so the benchmark can also inspect the whole
            # conversation's calls after the fact) and using it here once
            # produced a bug where every turn's step count included every
            # earlier turn's calls too.
            turn.steps = [planner.Step(tool=n, args=a) for n, a in self.last_tool_calls]
            turn.asked = not self.last_tool_calls and bool(turn.reply)
            turn.seconds = time.monotonic() - started
            return turn

    async def handle(self, update, context, text: str, requested_by: str) -> None:
        """Production entry: send one message, dispatch every tool call live.

        Text is shown to the household only when nothing was dispatched
        this turn — every tool already reported its own outcome through
        the handler it called, exactly as the classifier path does.
        """
        async with self._lock:
            await self._ensure_connected()
            self.turn_update = update
            self.turn_context = context
            self.turn_requested_by = requested_by
            self._current_text = text
            self.last_tool_calls = []
            reply = ""
            await self._client.query(text)
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            reply = block.text
                if isinstance(msg, ResultMessage):
                    logger.info(
                        "AGENT chat=%s turns=%s cost=%.4f tools=%s",
                        getattr(update.effective_chat, "id", "?"), msg.num_turns,
                        msg.total_cost_usd or 0.0, [n for n, _ in self.last_tool_calls],
                    )
            if not self.last_tool_calls:
                await update.message.reply_text(
                    reply or "רגע, לא הבנתי — אפשר לנסח אחרת?"
                )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


# -- production dispatch: every tool call ends up here, and from here
# -- reaches only functions the classifier path already exercises -----

async def _dispatch_live(bot, session: AgentSession, name: str, args: dict) -> str:
    from .nlu import ParsedItem, ParsedMessage

    update, context, requested_by = session.turn_update, session.turn_context, session.turn_requested_by
    store = args.get("store") or ""

    if name == "add_to_list":
        await bot._do_add(update, context, ParsedMessage(  # noqa: SLF001
            intent="add_item",
            items=[ParsedItem(name=args["item"], amount=args.get("amount"), unit=args.get("unit", ""))],
        ), requested_by)
        return "בוצע."
    if name == "remove_from_list":
        await bot._do_remove(update, context, ParsedMessage(  # noqa: SLF001
            intent="remove_item", items=[ParsedItem(name=args["item"])],
        ), requested_by)
        return "בוצע."
    if name == "add_to_cart":
        await bot._do_add_to_cart(update, context, ParsedMessage(  # noqa: SLF001
            intent="add_to_cart",
            items=[ParsedItem(name=args["item"], amount=args.get("quantity", 1))], store=store,
        ), requested_by)
        return "בוצע."
    if name == "set_cart_quantity":
        await bot._do_change_quantity(update, context, ParsedMessage(  # noqa: SLF001
            intent="change_quantity",
            items=[ParsedItem(name=args["item"], amount=args.get("quantity"))], store=store,
        ), requested_by)
        return "בוצע."
    if name == "replace_in_cart":
        return await _dispatch_replace(bot, update, args)
    if name == "fill_cart":
        await bot.start_order(update, context)
        return "בוצע."
    if name == "report_shopped":
        await bot.done_shopping(update, context, store=store)
        return "בוצע."
    if name == "price_check":
        await bot._do_price(update, context, ParsedMessage(intent="price_query", query=args["item"]), requested_by)  # noqa: SLF001
        return "בוצע."
    if name == "show_deals":
        await bot._do_deals(update, context, ParsedMessage(intent="deals"), requested_by)  # noqa: SLF001
        return "בוצע."
    if name == "show_list":
        await bot._do_show_list(update, context, ParsedMessage(intent="show_list"), requested_by)  # noqa: SLF001
        return "בוצע."
    if name == "show_cart":
        return await _dispatch_show_cart(bot, update)
    if name == "recipe":
        await bot._do_recipe(update, context, ParsedMessage(intent="recipe", query=args["dish"]), requested_by)  # noqa: SLF001
        return "בוצע."
    if name == "meal_plan":
        await bot._do_meal_plan(update, context, ParsedMessage(intent="meal_plan", query=args.get("note", "")), requested_by)  # noqa: SLF001
        return "בוצע."
    if name == "report_waste":
        await bot._do_report_waste(update, context, ParsedMessage(intent="report_waste", items=[ParsedItem(name=args["item"])]), requested_by)  # noqa: SLF001
        return "בוצע."
    if name == "remove_from_cart":
        # No adapter offers a verified single-line remove that a term can
        # reliably resolve to outside of a replace (Shufersal's
        # `remove_item` needs a product code, which nothing here has
        # resolved) — declined honestly rather than guessed at.
        return ("אין לי עדיין דרך בטוחה להסיר שורה ספציפית מהעגלה האמיתית. "
                "אפשר לכתוב '<מוצר> במקום <מה שיוצא>' כדי להחליף, או שאני מוריד "
                "מהרשימה הפנימית לפעם הבאה.")
    if name == "remember_preference":
        # Phase 8 (preference provenance) ranks a remembered choice by
        # its evidence; writing one here with no resolved product code
        # would plant an unranked guess ahead of a real purchase record.
        return "לא שומר את זה עדיין בלי בחירה אמיתית מהעגלה — תגידו לי כשתעלה בחירה ואזכור אותה משם."
    return f"כלי לא מוכר: {name}"


async def _dispatch_replace(bot, update, args: dict) -> str:
    from .replace import format_replace, replace_product
    from .telegram_bot import _build_adapter_factories

    old, new = args.get("old", ""), args.get("new", "")
    if not old or not new:
        return "צריך גם מה יוצא וגם מה נכנס כדי להחליף."
    store = args.get("store") or (bot.config.enabled_stores or ["shufersal"])[0]
    factories = _build_adapter_factories(bot.config)
    if store not in factories:
        return f"אין לי גישה לרשת {store}."
    outcome = await asyncio.to_thread(replace_product, bot.storage, factories, store, old, new)
    await update.message.reply_text(format_replace(outcome))
    return "בוצע."


async def _dispatch_show_cart(bot, update) -> str:
    from . import execution
    from .chains import display_name
    from .telegram_bot import _build_adapter_factories

    factories = _build_adapter_factories(bot.config)
    carts = await asyncio.to_thread(execution.read_carts, factories)
    if not carts:
        text = "אין לי גישה לעגלה כרגע."
    else:
        lines = []
        for store, summary in carts.items():
            if summary.get("ok"):
                n = len(summary.get("items") or [])
                total = summary.get("total")
                line = f"{display_name(store)}: {n} פריטים"
                if total:
                    line += f", ₪{total:.2f}"
                lines.append(line)
            else:
                lines.append(f"{display_name(store)}: לא הצלחתי לקרוא את העגלה כרגע.")
        text = "\n".join(lines)
    await update.message.reply_text(text)
    return "בוצע."
