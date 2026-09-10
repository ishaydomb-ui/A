"""A rejected parse_mode must cost formatting, never the message.

This project has lost two real messages to that failure. A cross-chain
deals button whose handler ran correctly and whose send was rejected, so
the tap did nothing at all; and an order summary that vanished *after*
both carts had been filled, taking 39 unanswered disambiguation
questions with it. In both cases Telegram refused the text and the
exception surfaced nowhere the household could see.

`_send_html` and `_send_markdown` in `telegram_bot.py` already do this,
but they cover 9 sends. Forty others call `reply_text`,
`bot.send_message` or `edit_message_text` directly with
`parse_mode="Markdown"` and no protection — measured by Arthur
2026-09-09, and any one of them can fail the same silent way.

**Why this wraps the Bot rather than editing forty call sites.**
`Message.reply_text` delegates to `Bot.send_message`, and
`CallbackQuery.edit_message_text` to `Bot.edit_message_text` (verified
against python-telegram-bot 21.11.1). Wrapping those two methods
therefore covers every send in the project, including ones written
later, and it changes no markup — which matters because the alternative
is forty mechanical edits to f-strings that build `*bold*`, and this
project's own rule from 2026-09-08 is that blind string replacement on
code is more dangerous than the leak it fixes.

The fallback strips tags rather than re-escaping, so the household reads
"6*330 מ״ל" instead of nothing. Nigel's phrasing for the whole point:
it turns the failure mode from "the message was lost" into "the message
arrived less pretty".
"""
from __future__ import annotations

import logging
import re

from telegram.ext import ExtBot

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
# Order matters: &amp; must be last, or "&amp;lt;" would become "<".
_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&amp;", "&"))
# Legacy Markdown's markers, removed only when a send has already been
# refused — never on the happy path.
_MARKDOWN_MARKERS = re.compile(r"[*_`]")


def to_plain(text, parse_mode) -> str:
    """The same message with its formatting removed.

    Deliberately lossy and deliberately total: this runs only after
    Telegram has already refused the text, so the goal is a string that
    cannot be refused again, not a faithful rendering.
    """
    out = str(text or "")
    if (parse_mode or "").upper() == "HTML":
        out = _TAG.sub("", out)
        for entity, char in _ENTITIES:
            out = out.replace(entity, char)
        return out
    return _MARKDOWN_MARKERS.sub("", out)


def _wrap(original, what: str):
    async def guarded(*args, **kwargs):
        parse_mode = kwargs.get("parse_mode")
        try:
            return await original(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if not parse_mode:
                # Nothing to retry differently — a failure here is a real
                # failure and must not be swallowed.
                raise
            logger.warning(
                "%s rejected with parse_mode=%s (%s); resending as plain text",
                what, parse_mode, type(exc).__name__,
            )
            retry = dict(kwargs)
            retry.pop("parse_mode", None)
            text = retry.pop("text", None)
            if text is None and args:
                # `text` is positional in some call shapes.
                args = list(args)
                for index, value in enumerate(args):
                    if isinstance(value, str) and len(value) > 1:
                        args[index] = to_plain(value, parse_mode)
                        break
                args = tuple(args)
                return await original(*args, **retry)
            return await original(*args, text=to_plain(text, parse_mode), **retry)

    guarded.__name__ = getattr(original, "__name__", what)
    guarded.__doc__ = f"{what} with a plain-text fallback. See safesend.py."
    guarded.__wrapped__ = original
    return guarded


def install(bot) -> bool:
    """Give this bot's sends a plain-text fallback. Idempotent.

    Returns False when the wrapper could not be installed. **The real
    `telegram.Bot` defines `__slots__`, so this returns False for it** —
    monkey-patching the instance was the first design here and it does
    not work. `SafeBot` below is the mechanism that does; this remains
    for plain objects, and reports rather than silently doing nothing,
    which is the class of failure the whole module exists to prevent.
    """
    installed = False
    for name in ("send_message", "edit_message_text"):
        original = getattr(bot, name, None)
        if original is None or getattr(original, "__wrapped__", None) is not None:
            continue
        try:
            setattr(bot, name, _wrap(original, name))
        except (AttributeError, TypeError):
            logger.error(
                "safesend: could not wrap %s — sends have NO plain-text fallback", name
            )
            return False
        installed = True
    return installed


class SafeBot(ExtBot):
    """An ExtBot whose sends survive a rejected parse_mode.

    Subclassing rather than patching because `telegram.Bot` uses
    `__slots__`. Both overrides funnel through the same retry, and every
    `reply_text` / `edit_message_text` in the project reaches one of
    them — verified against python-telegram-bot 21.11.1, where
    `Message.reply_text` delegates to `Bot.send_message` and
    `CallbackQuery.edit_message_text` to `Bot.edit_message_text`.
    """

    async def send_message(self, *args, **kwargs):  # type: ignore[override]
        return await self._guarded(super().send_message, "send_message", args, kwargs)

    async def edit_message_text(self, *args, **kwargs):  # type: ignore[override]
        return await self._guarded(
            super().edit_message_text, "edit_message_text", args, kwargs
        )

    @staticmethod
    async def _guarded(original, what, args, kwargs):
        parse_mode = kwargs.get("parse_mode")
        try:
            return await original(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if not parse_mode:
                raise
            logger.warning(
                "%s rejected with parse_mode=%s (%s); resending as plain text",
                what, parse_mode, type(exc).__name__,
            )
            retry = dict(kwargs)
            retry.pop("parse_mode", None)
            if "text" in retry:
                retry["text"] = to_plain(retry["text"], parse_mode)
                return await original(*args, **retry)
            fixed = list(args)
            for index, value in enumerate(fixed):
                if isinstance(value, str) and len(value) > 1:
                    fixed[index] = to_plain(value, parse_mode)
                    break
            return await original(*fixed, **retry)
