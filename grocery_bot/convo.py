"""What we were just talking about, so the next message can be short.

Ishay, 2026-09-11, on what he actually wants from this: *"היכולת שהצ׳אט
יבין אותי ולא אצטרך להיות מחושב בגיל שמתנסח כי אחרת זה לא נופל
להגדרות — זה צריך להיות בדיוק כמו שיחה פה."*

A person does not restate the subject every sentence. They say "בעצם
שניים", "את זה רק הפעם", "השני במקום הראשון" — and every one of those is
meaningless without the previous turn. The classifier was being handed
each message alone, so the only messages it could understand were the
ones that carried their own full context, which is exactly the careful
phrasing he does not want to have to do.

So the last turn is kept: what product, at which chain, how many, and
what was done about it. Not a transcript — one subject, the one a
follow-up would refer to.

**It expires.** `CONTEXT_TTL_SECONDS` is 45 minutes, because "בעצם
שניים" three hours after the fact is more likely a new thought about
something else, and resolving it against a stale subject would put the
wrong thing in the cart quietly. An expired context is not an error; it
simply means the next message has to say what it is about, and the bot
asks if it does not.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

STATE_KEY = "conversation_context"
CONTEXT_TTL_SECONDS = 45 * 60

# The rolling exchange, added 2026-09-14. One remembered subject answers
# "בעצם שניים" and nothing else: "לא זה, השני" needs to know what the
# *previous answer* offered, and "את כל השאר כרגיל" needs to know what
# "the rest" was. A session is what makes a bot a conversation, and this
# is the cheap form of one — the last few turns, not an Agent SDK.
#
# Six turns because it is three exchanges: what was asked, what was
# answered, and the correction. Longer buys little and costs prompt.
TRANSCRIPT_KEY = "conversation_transcript"
MAX_TURNS = 6
MAX_TURN_CHARS = 200


def remember(
    storage,
    subject: str = "",
    store: str = "",
    quantity: int = 0,
    action: str = "",
    detail: str = "",
    when: datetime | None = None,
) -> None:
    """Record what this turn was about. Never raises."""
    if not subject:
        return
    payload = {
        "subject": subject,
        "store": store,
        "quantity": quantity,
        "action": action,
        "detail": detail,
        "at": (when or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }
    try:
        storage.set_state(STATE_KEY, json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.exception("Could not store conversation context")


def recall(storage, now: datetime | None = None) -> dict:
    """The last turn, or {} when there is none or it has gone stale."""
    try:
        raw = storage.get_state(STATE_KEY, "") or ""
        data = json.loads(raw) if raw else {}
    except (ValueError, Exception):  # noqa: BLE001
        return {}
    if not isinstance(data, dict) or not data.get("subject"):
        return {}
    try:
        stamp = datetime.fromisoformat(data["at"])
    except (KeyError, ValueError):
        return {}
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (now or datetime.now(timezone.utc)) - stamp
    if age > timedelta(seconds=CONTEXT_TTL_SECONDS):
        return {}
    return data


def remember_turn(storage, role: str, text: str, when: datetime | None = None) -> None:
    """Append one line of the exchange. `role` is "user" or "bot"."""
    line = " ".join(str(text or "").split())[:MAX_TURN_CHARS]
    if not line:
        return
    stamp = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    turns = _turns(storage)
    turns.append({"role": role, "text": line, "at": stamp})
    try:
        storage.set_state(
            TRANSCRIPT_KEY, json.dumps(turns[-MAX_TURNS:], ensure_ascii=False)
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not store the conversation transcript")


def _turns(storage) -> list:
    try:
        raw = storage.get_state(TRANSCRIPT_KEY, "") or ""
        data = json.loads(raw) if raw else []
    except Exception:  # noqa: BLE001
        return []
    return data if isinstance(data, list) else []


def transcript(storage, now: datetime | None = None) -> list:
    """The recent exchange, dropping anything past the TTL.

    Stale turns are dropped rather than the whole transcript: a pause in
    the middle of a conversation is ordinary, and throwing away the part
    that is still fresh would lose exactly the turn a correction refers
    to.
    """
    moment = now or datetime.now(timezone.utc)
    fresh = []
    for turn in _turns(storage):
        try:
            stamp = datetime.fromisoformat(turn["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if moment - stamp <= timedelta(seconds=CONTEXT_TTL_SECONDS):
            fresh.append(turn)
    return fresh


def format_transcript(storage, now: datetime | None = None) -> str:
    """The exchange as lines a model can read, or "" when there is none."""
    lines = [
        f"{'אני' if t['role'] == 'user' else 'הבוט'}: {t['text']}"
        for t in transcript(storage, now)
    ]
    return "\n".join(lines)


def forget(storage) -> None:
    try:
        storage.set_state(TRANSCRIPT_KEY, "")
        storage.set_state(STATE_KEY, "")
    except Exception:  # noqa: BLE001
        logger.exception("Could not clear conversation context")


def describe(context: dict) -> str:
    """The context as one line for the model's prompt.

    Deliberately a sentence rather than JSON: it is read by a language
    model alongside Hebrew instructions, and it has to be obvious that
    this is background, not the message being classified.
    """
    if not context:
        return ""
    from .chains import display_name

    # Two shapes reach this: our own stored turn ("subject") and the
    # planner's context dict ("last_subject"). Reading either, and
    # returning nothing when there is neither, is the difference between
    # a line of background and a KeyError inside the parse path — where
    # it surfaces as "the model is unavailable" and every message falls
    # through to the rule-based fallback. Found on the comparison
    # harness, 2026-09-11, doing exactly that.
    # The subject is often a product name chosen from a chain's search
    # results, so it is fetched text and gets the same flattening as the
    # planner's context — a newline in it would otherwise break this out
    # of its own line in the prompt. See `untrusted.py`.
    from .untrusted import safe as flatten

    subject = flatten(context.get("subject") or context.get("last_subject") or "")
    if not subject:
        return ""
    parts = [f"המוצר שדובר עליו לאחרונה: {subject}"]
    store = context.get("store") or context.get("last_store")
    if store:
        parts.append(f"ברשת {flatten(display_name(store), 40)}")
    if context.get("quantity"):
        parts.append(f"בכמות {flatten(context['quantity'], 20)}")
    if context.get("action"):
        parts.append(f"(מה שנעשה: {flatten(context['action'], 40)})")
    return ", ".join(parts)
