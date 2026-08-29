"""Understand free-form Hebrew messages instead of requiring commands.

The bot's first version treated every incoming message as a literal item
name, so "תוסיף גבינה בולגרית" became an item called "תוסיף גבינה
בולגרית", and "מה" became an item called "מה". Anything short of real
language understanding just moves that problem around, so this asks a
model.

**Why the `claude` CLI and not an API key:** the server already has
Claude Code installed and signed in for this household's other
projects, and `claude -p` runs one non-interactive prompt against that
existing subscription. Using the Anthropic API directly would mean a
second credential and a second bill for what is, at this volume, a
handful of short prompts a day. If that tradeoff ever changes, only
`_ask_model` below needs replacing.

Latency is ~7-10s per message, which is why the bot sends a typing
indicator. If the CLI is missing, fails, or times out, `parse_message`
falls back to a small rule-based parser: degraded, but it still beats
silently filing "מה" as groceries.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_TIMEOUT_SECONDS = 120

INTENTS = {
    "add_item",
    "remove_item",
    "price_query",
    "deals",
    "show_list",
    "recipe",
    "meal_plan",
    "start_order",
    "smalltalk",
    "unclear",
}

_SYSTEM_PROMPT = """אתה מנתח הודעות של בוט קניות משפחתי בעברית. החזר JSON בלבד, בלי טקסט נוסף ובלי הסברים.

שדות:
- intent: אחד מ- add_item | remove_item | price_query | deals | show_list | recipe | meal_plan | start_order | smalltalk | unclear
- items: מערך של {"name","amount","unit","brand"} — רק שם המוצר עצמו, בלי פעלים כמו "תוסיף"/"תוריד"/"צריך".
  amount = מספר או null. unit = "גרם"/"קילו"/"יחידות"/"ליטר" או null. brand = שם יצרן אם צוין, אחרת null.
- query: מחרוזת חיפוש (ל-price_query, ל-recipe שם המנה, ל-meal_plan תיאור)
- reply: משפט קצר וידידותי בעברית להשיב למשתמש

כללים חשובים:
- "מתכון ל..." או בקשה למנה => intent=recipe, query=שם המנה. לא להוסיף לרשימה.
- "תכנן לי שבוע" / "תפריט שבועי" => intent=meal_plan.
- מילה בודדת חסרת הקשר או הודעה לא מובנת (למשל "מה") => intent=unclear. עדיף unclear מאשר לנחש.
- "כמה עולה X" / "מחיר של X" / "יש מבצע על X" => price_query.
- "מה יש במבצע" => deals. "מה יש ברשימה" / "תראה לי את הרשימה" => show_list.
- "מלא את העגלה" / "תתחיל הזמנה" / "תתחיל מחזור" / "תזמין" => start_order. זו בקשה להתחיל למלא את הסל בפועל, לא פריט.
- הודעה שהיא רק פריט ("חלב", "לחם ועגבניות") => add_item.
- אפשר כמה פריטים בהודעה אחת."""


@dataclass
class ParsedItem:
    name: str
    amount: float | None = None
    unit: str = ""
    brand: str = ""


@dataclass
class ParsedMessage:
    intent: str
    items: list[ParsedItem] = field(default_factory=list)
    query: str = ""
    reply: str = ""
    used_fallback: bool = False


def _claude_cli() -> str:
    """Absolute path to the `claude` CLI.

    Never rely on the ambient PATH: as a systemd service the bot gets a
    minimal PATH without ~/.local/bin, so `claude` was simply not found
    and *every* message silently fell through to the rule-based
    fallback — which files anything it doesn't recognise as a shopping
    item. That turned "תכנן לי תפריט שבועי" into a list entry called
    "תכנן לי תפריט שבועי", with only a log line to say why.

    Resolution order: explicit override, then PATH, then the standard
    per-user install location.
    """
    override = os.environ.get("CLAUDE_CLI_PATH")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    return str(Path.home() / ".local" / "bin" / "claude")


def _ask_model(message: str) -> str:
    result = subprocess.run(
        [_claude_cli(), "-p", f'{_SYSTEM_PROMPT}\n\nהודעה: "{message}"'],
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT_SECONDS,
        stdin=subprocess.DEVNULL,  # otherwise the CLI waits on stdin it will never get
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:200]}")
    return result.stdout.strip()


def _extract_json(raw: str) -> dict:
    """Pull the JSON object out of a model reply.

    The CLI usually returns a bare object but sometimes wraps it in a
    ```json fence, so both are handled rather than trusting either.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model reply: {raw[:200]}")
    return json.loads(text[start : end + 1])


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Verbs people actually open a message with; stripped by the fallback so
# it doesn't repeat the original bug of filing "תוסיף חלב" as an item.
_ADD_PREFIXES = ("תוסיף", "תוסיפי", "להוסיף", "צריך", "צריכים", "תקנה", "לקנות", "תביא")
_REMOVE_PREFIXES = ("תוריד", "תורידי", "להוריד", "תמחק", "למחוק", "בטל")
_PRICE_PREFIXES = ("כמה עולה", "מה המחיר", "מחיר של", "מחיר")


def _fallback_parse(message: str) -> ParsedMessage:
    """Rule-based parsing for when the model is unavailable.

    Deliberately conservative: anything it can't confidently classify
    becomes `unclear` and gets asked about, rather than being filed as
    groceries.
    """
    text = message.strip()
    lowered = text.lower()

    if any(phrase in lowered for phrase in ("מלא את העגלה", "תמלא את העגלה", "תתחיל הזמנה", "תתחיל מחזור", "להתחיל הזמנה")):
        return ParsedMessage(intent="start_order", used_fallback=True)
    if any(word in lowered for word in ("מבצע", "מבצעים", "הנחות")):
        return ParsedMessage(intent="deals", used_fallback=True)
    if any(phrase in lowered for phrase in ("מה ברשימה", "מה יש ברשימה", "תראה את הרשימה", "הרשימה")):
        return ParsedMessage(intent="show_list", used_fallback=True)
    if "מתכון" in lowered:
        return ParsedMessage(
            intent="recipe",
            query=re.sub(r"^.*?מתכון\s*(ל|של)?\s*", "", text).strip(),
            used_fallback=True,
        )
    for prefix in _PRICE_PREFIXES:
        if lowered.startswith(prefix):
            return ParsedMessage(
                intent="price_query", query=text[len(prefix) :].strip(), used_fallback=True
            )
    for prefix in _REMOVE_PREFIXES:
        if lowered.startswith(prefix):
            name = re.sub(r"^\S+\s*(את\s+)?(ה)?", "", text, count=1).strip()
            return ParsedMessage(
                intent="remove_item", items=[ParsedItem(name=name)], used_fallback=True
            )

    stripped = text
    for prefix in _ADD_PREFIXES:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
            break

    # A single bare word with no verb is ambiguous ("מה"), and guessing is
    # exactly what produced the junk entries this module exists to stop.
    if not stripped or (stripped == text and len(stripped.split()) < 2):
        return ParsedMessage(intent="unclear", used_fallback=True)
    return ParsedMessage(
        intent="add_item", items=[ParsedItem(name=stripped)], used_fallback=True
    )


def parse_message(message: str) -> ParsedMessage:
    """Classify one free-text message; never raises."""
    text = (message or "").strip()
    if not text:
        return ParsedMessage(intent="unclear")

    try:
        payload = _extract_json(_ask_model(text))
    except Exception:
        logger.warning("NLU: model unavailable, using rule-based fallback", exc_info=True)
        return _fallback_parse(text)

    intent = str(payload.get("intent") or "").strip()
    if intent not in INTENTS:
        logger.warning("NLU: model returned unknown intent %r", intent)
        return _fallback_parse(text)

    items = []
    for raw_item in payload.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name") or "").strip()
        if not name:
            continue
        items.append(
            ParsedItem(
                name=name,
                amount=_to_float(raw_item.get("amount")),
                unit=str(raw_item.get("unit") or "").strip(),
                brand=str(raw_item.get("brand") or "").strip(),
            )
        )

    return ParsedMessage(
        intent=intent,
        items=items,
        query=str(payload.get("query") or "").strip(),
        reply=str(payload.get("reply") or "").strip(),
    )


_RECIPE_PROMPT = """אתה עוזר קניות. קיבלת בקשה למנה. החזר JSON בלבד:
{"dish": "שם המנה", "ingredients": [{"name","amount","unit"}], "note": "הערה קצרה"}

כללים:
- רק מצרכים שצריך לקנות בסופר. אל תכלול מים, מלח ופלפל אלא אם הם מרכזיים.
- שמות מוצרים כפי שמחפשים אותם בסופר בישראל (למשל "קמח לבן", "חמאה", "סוכר").
- amount מספר או null, unit "גרם"/"קילו"/"יחידות"/"כפות"/"כוסות" או null.
- עד 15 מצרכים."""


@dataclass
class Recipe:
    dish: str
    ingredients: list[ParsedItem] = field(default_factory=list)
    note: str = ""


def expand_recipe(dish: str) -> Recipe | None:
    """Turn a dish name into a buyable ingredient list, or None if unavailable."""
    try:
        result = subprocess.run(
            ["claude", "-p", f'{_RECIPE_PROMPT}\n\nהמנה: "{dish}"'],
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[:200])
        payload = _extract_json(result.stdout)
    except Exception:
        logger.warning("Recipe expansion failed for %r", dish, exc_info=True)
        return None

    ingredients = [
        ParsedItem(
            name=str(raw.get("name") or "").strip(),
            amount=_to_float(raw.get("amount")),
            unit=str(raw.get("unit") or "").strip(),
        )
        for raw in (payload.get("ingredients") or [])
        if isinstance(raw, dict) and str(raw.get("name") or "").strip()
    ]
    if not ingredients:
        return None
    return Recipe(
        dish=str(payload.get("dish") or dish).strip(),
        ingredients=ingredients,
        note=str(payload.get("note") or "").strip(),
    )


_MEAL_PLAN_PROMPT = """אתה מתכנן ארוחות למשפחה בישראל. בנה תפריט שבועי ל-5 ארוחות ערב.
החזר JSON בלבד:
{"meals":[{"day","dish"}], "ingredients":[{"name","amount","unit"}], "note":"הערה קצרה"}

כללים:
- ארוחות מגוונות, ריאליות ליום חול, לא מסובכות מדי.
- ingredients = רשימה *מאוחדת* לכל השבוע: אם שני מתכונים צריכים בצל, שורה אחת עם הכמות הכוללת.
- שמות מוצרים כפי שמחפשים בסופר בישראל.
- עד 25 מצרכים. בלי מים/מלח/פלפל."""


@dataclass
class MealPlan:
    meals: list[tuple[str, str]] = field(default_factory=list)  # (day, dish)
    ingredients: list[ParsedItem] = field(default_factory=list)
    note: str = ""


def build_meal_plan(request: str = "") -> MealPlan | None:
    """Plan a week of dinners and return one consolidated shopping list.

    Consolidation happens inside the single model call on purpose: asking
    per-dish and merging afterwards would need ingredient-name matching
    ("בצל" vs "בצל יבש") that is exactly the kind of fuzzy judgement the
    model is already making here.
    """
    prompt = _MEAL_PLAN_PROMPT
    if request.strip():
        prompt += f"\n\nבקשה מיוחדת מהמשתמש: {request.strip()}"
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[:200])
        payload = _extract_json(result.stdout)
    except Exception:
        logger.warning("Meal plan generation failed", exc_info=True)
        return None

    meals = [
        (str(m.get("day") or "").strip(), str(m.get("dish") or "").strip())
        for m in (payload.get("meals") or [])
        if isinstance(m, dict) and str(m.get("dish") or "").strip()
    ]
    ingredients = [
        ParsedItem(
            name=str(raw.get("name") or "").strip(),
            amount=_to_float(raw.get("amount")),
            unit=str(raw.get("unit") or "").strip(),
        )
        for raw in (payload.get("ingredients") or [])
        if isinstance(raw, dict) and str(raw.get("name") or "").strip()
    ]
    if not meals and not ingredients:
        return None
    return MealPlan(meals=meals, ingredients=ingredients, note=str(payload.get("note") or "").strip())
