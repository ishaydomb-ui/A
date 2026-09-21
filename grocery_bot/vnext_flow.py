"""The household's shopping draft and the five Telegram screens — vNext Phase 2b.

A *draft* is the ShoppingPlan turned into something a person can edit
from a phone: a list of items with a per-chain product and price, a
chain toggle, a page, and the answers to the cards (waste, stock-up).
It lives in `vnext_drafts` as JSON so every inline button survives a
restart, and it is a proposal until the household taps "אשר והכן
עגלה" — nothing in this module touches a cart. Execution, when it comes,
goes through the existing engine (`orchestrator.add_terms_to_cart`)
with the resolver's product as the identity for each chain; and it
stops there. The mock's "עבור לתשלום" is a link to the chain's own cart
page, never an action: the hard rule (`planner.FORBIDDEN`, CLAUDE.md)
that this bot never completes a purchase holds here as everywhere.

Every function that renders returns `(text, keyboard)` where the
keyboard is rows of `(label, callback_data | ("url", href))`, so the
screens can be tested without Telegram. Text is plain (no parse mode) —
a product name with `*` or `<` cannot break a send.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .chains import display_name
from .telegram_vnext_view import count
from .vnext_config import DEFAULT, VNextConfig

logger = logging.getLogger(__name__)

CB = "vn"                       # callback-data prefix for every button of this flow
KIND_ROUTINE, KIND_REQUEST, KIND_MEAL, KIND_STOCKUP, KIND_ALT = "routine", "request", "meal", "stockup", "alternative"
CHAIN_CART_URL = {
    "shufersal": "https://www.shufersal.co.il/online/he/cart/cartsummary",
    "tivtaam": "https://www.tivtaam.co.il/",
}
NUDGE_LAST_KEY = "vnext_nudge_last_at"
NUDGE_SNOOZE_KEY = "vnext_nudge_snoozed_until"
KEY_URL = "url"


def cb(*parts) -> str:
    return ":".join([CB, *[str(p) for p in parts]])


# -- the draft ------------------------------------------------------------------------

@dataclass
class Draft:
    id: int
    chat_id: int
    status: str
    data: dict           # items, chains, cards, summary, answers, meal
    page: int = 0
    message_id: int | None = None
    screen: str = "proposal"
    awaiting: str = ""
    result: dict | None = None

    @property
    def items(self) -> list[dict]:
        return self.data.setdefault("items", [])

    @property
    def included(self) -> list[dict]:
        return [i for i in self.items if i.get("included") and not i.get("removed")]

    @property
    def optional(self) -> list[dict]:
        return [i for i in self.items if not i.get("included") and not i.get("removed")]

    @property
    def chains(self) -> dict:
        return self.data.setdefault("chains", {})

    def enabled_chains(self) -> list[str]:
        return [s for s, on in self.chains.items() if on]

    def find(self, key) -> dict | None:
        """By term key, or by the numeric id used in callback data."""
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            n = int(key)
            return next((i for i in self.items if i.get("n") == n), None)
        return next((i for i in self.items if i.get("key") == key), None)

    def add_item(self, term: str, quantity: float = 1, kind: str = KIND_REQUEST, products: dict | None = None) -> dict:
        existing = self.find(term)
        if existing is not None:
            existing["included"], existing["removed"] = True, False
            if quantity and quantity != 1:
                existing["quantity"] = float(quantity)
            return existing
        item = {"n": max([i.get("n", -1) for i in self.items] + [-1]) + 1, "key": term, "term": term,
                "display_name": term, "quantity": float(quantity or 1), "unit": "", "kind": kind,
                "included": True, "removed": False, "mandatory": kind == KIND_REQUEST, "decision": "auto_include",
                "products": products or {}, "prices": {}, "promo": {},
                "exception": {"class": "quiet", "reason": ""}, "waste_reduced": False, "meal": "",
                "substitutes": []}
        self.items.append(item)
        return item

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False)


def load_draft(storage, chat_id: int, config: VNextConfig = DEFAULT) -> Draft | None:
    row = storage.open_vnext_draft(chat_id)
    if row is None:
        return None
    try:
        updated = datetime.fromisoformat(row["updated_at"])
    except (TypeError, ValueError):
        updated = datetime.now(timezone.utc)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if row["status"] == "draft" and datetime.now(timezone.utc) - updated > timedelta(hours=config.draft_ttl_hours):
        storage.update_vnext_draft(row["id"], status="cancelled")
        logger.info("vNext draft %s expired after %.0fh", row["id"], config.draft_ttl_hours)
        return None
    return _from_row(row)


def draft_by_id(storage, draft_id: int) -> Draft | None:
    row = storage.get_vnext_draft(draft_id)
    return _from_row(row) if row else None


def _from_row(row: dict) -> Draft:
    return Draft(
        id=int(row["id"]), chat_id=int(row["chat_id"]), status=row["status"],
        data=json.loads(row["plan_json"] or "{}"), page=int(row.get("page") or 0),
        message_id=row.get("message_id"), screen=row.get("screen") or "proposal",
        awaiting=row.get("awaiting") or "", result=json.loads(row.get("result_json") or "{}") or None,
    )


def save_draft(storage, draft: Draft, **fields) -> None:
    storage.update_vnext_draft(draft.id, plan_json=draft.to_json(), page=draft.page,
                               screen=draft.screen, awaiting=draft.awaiting, **fields)


def new_draft(storage, chat_id: int, plan, config: VNextConfig = DEFAULT, important_only: bool = False,
              enabled_stores: list[str] | None = None) -> Draft:
    """Turn a ShoppingPlan into an editable draft and persist it."""
    data = draft_data_from_plan(storage, plan, config, important_only, enabled_stores)
    draft_id = storage.create_vnext_draft(chat_id, json.dumps(data, ensure_ascii=False),
                                          json.dumps(data["chains"]))
    return Draft(id=draft_id, chat_id=chat_id, status="draft", data=data)


def draft_data_from_plan(storage, plan, config: VNextConfig = DEFAULT, important_only: bool = False,
                         enabled_stores: list[str] | None = None) -> dict:
    from . import basket_optimizer

    stores = list(enabled_stores or ["shufersal", "tivtaam"])
    items: list[dict] = []
    seen: set = set()
    for pi in plan.items:
        if pi.request_status_estimate == "likely_fulfilled":
            continue
        kind = _kind_of(pi)
        included = pi.decision == "auto_include" or (kind == KIND_STOCKUP and not important_only)
        if important_only and kind in (KIND_STOCKUP, KIND_ALT):
            included = False
        key = pi.term
        if key in seen:
            continue
        seen.add(key)
        products = _products_by_store(pi, stores)
        waste_reduced = "waste reported" in (pi.reason or "")
        items.append({
            "n": len(items), "key": key, "term": pi.term, "display_name": pi.display_name or pi.term,
            "quantity": float(pi.quantity or 1), "unit": pi.unit or "",
            "kind": kind, "included": bool(included), "removed": False,
            "mandatory": bool(pi.mandatory), "decision": pi.decision,
            "products": products, "prices": {}, "promo": _promo_of(pi),
            "exception": {"class": pi.exception_class, "reason": pi.exception_reason},
            "waste_reduced": waste_reduced, "meal": pi.meal_or_event or "",
            "substitutes": [c.get("product_name", "") for c in
                            (pi.product_resolution or {}).get("substitution_candidates", [])[:2]],
        })
    data = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": plan.as_of,
        "items": items,
        "chains": {s: True for s in stores},
        "meal": next((i["meal"] for i in items if i["meal"]), ""),
        "important_only": important_only,
        "cards": {"waste": [i["key"] for i in items if i["waste_reduced"]],
                  "stockup": [i["key"] for i in items if i["kind"] == KIND_STOCKUP][:config.stockup_cards]},
        "answers": {},
        "plan_summary": {k: plan.summary.get(k) for k in ("auto_include", "suggest", "decisions_needed",
                                                           "agent_resolvable", "pending_requests")},
        "caveats": list(plan.caveats)[-1:],
    }
    basket_optimizer.price_items(storage, items, config)
    data["alternatives"] = basket_optimizer.cheaper_alternatives(storage, items, config)
    data["quotes"] = basket_optimizer.quotes_for(items, data["chains"], config)
    return data


def _kind_of(pi) -> str:
    if pi.mandatory:
        return KIND_REQUEST
    if pi.meal_or_event:
        return KIND_MEAL
    if pi.stock_up:
        return KIND_STOCKUP
    return KIND_ROUTINE


def _products_by_store(pi, stores: list[str]) -> dict:
    out: dict = {}
    for cand in (pi.product_resolution or {}).get("candidates_considered", []):
        store = cand.get("store")
        if store not in stores or cand.get("status") not in ("exact_match", "acceptable_match"):
            continue
        current = out.get(store)
        if current is None or float(cand.get("confidence") or 0) > float(current.get("confidence") or 0):
            out[store] = {"product_code": str(cand.get("product_code") or ""),
                          "product_name": cand.get("product_name") or "",
                          "confidence": float(cand.get("confidence") or 0), "status": cand.get("status"),
                          "human": cand.get("origin") == "HUMAN_DECLARED"}
    chosen = pi.human_product or pi.inferred_product
    if chosen and chosen.get("store") in stores and chosen["store"] not in out:
        out[chosen["store"]] = {"product_code": str(chosen.get("product_code") or ""),
                                "product_name": chosen.get("product_name") or "",
                                "confidence": float(pi.product_confidence or 0), "status": "chosen",
                                "human": bool(pi.human_product)}
    return out


def _promo_of(pi) -> dict:
    a = pi.stockup_assessment or {}
    if not a.get("worthwhile"):
        return {}
    e = a.get("economics") or {}
    return {"store": a.get("store"), "product_name": a.get("product_name"),
            "units": a.get("recommended_units"), "deal_price": e.get("deal_price"),
            "reference_price": e.get("reference_price"), "discount": e.get("real_discount"),
            "saving": e.get("total_saving")}


# -- counts and money -----------------------------------------------------------------

def counts(draft: Draft) -> dict:
    inc = draft.included
    return {
        "total": len(inc),
        "routine": len([i for i in inc if i["kind"] == KIND_ROUTINE]),
        "request": len([i for i in inc if i["kind"] == KIND_REQUEST]),
        "meal": len([i for i in inc if i["kind"] == KIND_MEAL]),
        "stockup": len([i for i in inc if i["kind"] == KIND_STOCKUP]),
        "alternatives": len([a for a in draft.data.get("alternatives", []) if draft.find(a["key"])
                             and draft.find(a["key"]).get("included")]),
        "decisions": len([i for i in inc if i["exception"]["class"] == "true_user_decision"]),
    }


def expected_saving(draft: Draft) -> float:
    """Promotion savings on included stock-ups + cheaper-alternative savings."""
    total = 0.0
    for i in draft.included:
        if i["promo"].get("saving"):
            total += float(i["promo"]["saving"] or 0)
    for a in draft.data.get("alternatives", []):
        item = draft.find(a["key"])
        if item and item.get("included") and a.get("accepted"):
            total += float(a.get("saving") or 0)
    return round(total, 2)


def item_price(item: dict, chains: dict | None = None) -> tuple[str, float | None]:
    """(store, unit price) at the cheapest enabled chain that has the product."""
    best = ("", None)
    for store, price in (item.get("prices") or {}).items():
        if chains and not chains.get(store, True):
            continue
        if price is None:
            continue
        if best[1] is None or price < best[1]:
            best = (store, float(price))
    return best


def _money(v: float | None) -> str:
    return f"₪{v:,.2f}".replace(".00", "") if v is not None else "—"


def _qty(item: dict) -> str:
    q = float(item.get("quantity") or 1)
    unit = item.get("unit") or ""
    q_text = f"{q:g}"
    return f"({q_text} {unit})" if unit and unit not in ("יח'", "יח", "unit") else f"({q_text})"


# -- screen 1: the proactive nudge -----------------------------------------------------

def nudge_suppression(storage, r, config: VNextConfig = DEFAULT, now: datetime | None = None,
                      open_draft: bool = False) -> str:
    """Why the nudge must NOT be sent now, or "" when it may. Every reason
    is a string worth logging — Ishay hates noise, so silence is explained."""
    now = now or datetime.now(timezone.utc)
    if r.suggested_action not in ("prepare_now", "prepare_soon"):
        return f"readiness says {r.suggested_action}"
    if r.suggested_action == "prepare_soon" and not config.nudge_on_prepare_soon:
        return "prepare_soon and nudge_on_prepare_soon is off"
    if open_draft:
        return "a draft is already open"
    israel_hour = _israel_hour(now)
    if not (config.nudge_hour_from <= israel_hour < config.nudge_hour_to):
        return f"outside {config.nudge_hour_from:02d}-{config.nudge_hour_to:02d} Israel (hour {israel_hour})"
    snoozed = storage.get_state(NUDGE_SNOOZE_KEY, "")
    if snoozed:
        try:
            if now < datetime.fromisoformat(snoozed):
                return f"snoozed until {snoozed}"
        except ValueError:
            pass
    last = storage.get_state(NUDGE_LAST_KEY, "")
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(days=config.nudge_min_days):
                return f"last nudge {last}, min {config.nudge_min_days:g} days"
        except ValueError:
            pass
    return ""


def _israel_hour(now: datetime) -> int:
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("Asia/Jerusalem")).hour
    except Exception:  # noqa: BLE001
        return (now.hour + 3) % 24


def note_nudge_sent(storage, now: datetime | None = None) -> None:
    storage.set_state(NUDGE_LAST_KEY, (now or datetime.now(timezone.utc)).isoformat())


def snooze_nudge(storage, config: VNextConfig = DEFAULT, now: datetime | None = None) -> None:
    until = (now or datetime.now(timezone.utc)) + timedelta(days=config.nudge_snooze_days)
    storage.set_state(NUDGE_SNOOZE_KEY, until.isoformat())


def nudge_screen(r, plan=None) -> tuple[str, list]:
    s = r.signals
    lines = ["שלום!", "נראה שכדאי להזמין קניות 😊", ""]
    essentials = count(s.get("essentials_due", 0), "• מוצר אחד עומד להיגמר", "מוצרים עומדים להיגמר")
    if essentials and not essentials.startswith("•"):
        essentials = "• " + essentials
    meal_items = int(s.get("meal_items", 0) or 0)
    meals = count(meal_items, "• מוצר אחד חסר למתכונים", "מוצרים חסרים למתכונים")
    if meals and not meals.startswith("•"):
        meals = "• " + meals
    pending = count(int(s.get("explicit_active", s.get("explicit_pending", 0)) or 0),
                    "• בקשה פתוחה אחת", "בקשות פתוחות")
    if pending and not pending.startswith("•"):
        pending = "• " + pending
    for line in (essentials, meals, pending):
        if line:
            lines.append(line)
    if s.get("savings_opportunities", 0):
        lines.append("• יש מבצעים טובים השבוע")
    lines += ["", "רוצה שאכין הצעת קנייה?"]
    keyboard = [
        [("כן, תכין הצעה", cb("nudge", "yes"))],
        [("רק מה שחשוב", cb("nudge", "important"))],
        [("לא עכשיו", cb("nudge", "later"))],
        [("הצג פריטים", cb("nudge", "items"))],
    ]
    return "\n".join(lines), keyboard


def nudge_items_text(plan) -> str:
    due = [i for i in plan.auto_items if not i.mandatory][:12]
    req = [i for i in plan.active_requests][:12]
    lines = []
    if due:
        lines.append("עומדים להיגמר (לפי הקצב שלכם):")
        lines += [f"• {i.display_name}" for i in due]
    if req:
        lines.append("")
        lines.append("בקשות פתוחות:")
        lines += [f"• {i.display_name}" for i in req]
    return "\n".join(lines) or "אין פריטים לציין כרגע."


# -- screen 2: the proposal ------------------------------------------------------------

def proposal_screen(draft: Draft) -> tuple[str, list]:
    c = counts(draft)
    lines = [f"הנה הצעת הקנייה שלך 🛒 ({count(c['total'], 'מוצר אחד', 'מוצרים', '0 מוצרים')})", ""]
    if c["routine"]:
        lines.append(f"✅ {count(c['routine'], 'פריט אחד להשלמה שוטפת', 'פריטים להשלמה שוטפת')}")
    if c["meal"]:
        lines.append(f"🍳 {count(c['meal'], 'פריט אחד למתכונים הקרובים', 'פריטים למתכונים הקרובים')}")
    if c["request"]:
        lines.append(f"📝 {count(c['request'], 'בקשה אחת שלכם', 'בקשות שלכם')}")
    if c["stockup"]:
        lines.append(f"💰 {count(c['stockup'], 'מבצע אחד ששווה לאגור', 'מבצעים ששווה לאגור')}")
    if c["alternatives"]:
        lines.append(f"🔄 {count(c['alternatives'], 'אלטרנטיבה זולה יותר אחת', 'אלטרנטיבות זולות יותר')}")
    saving = expected_saving(draft)
    if saving > 0:
        lines += ["", f"סיכום חיסכון צפוי: {_money(saving)}", "(בהשוואה למחירים רגילים)"]
    if c["decisions"]:
        lines += ["", "❓ " + count(c["decisions"], "החלטה אחת מחכה לך בעריכה", "החלטות מחכות לך בעריכה")]
    keyboard = [
        [("הצג את כל הרשימה", cb("list", 0))],
        [("ערוך / הסר פריטים", cb("edit", 0))],
        [("השווה בין רשתות", cb("compare"))],
        [("אשר והכן עגלה", cb("go"))],
        [("בטל את ההצעה", cb("cancel"))],
    ]
    return "\n".join(lines), keyboard


# -- cards: waste and stock-up -----------------------------------------------------

def waste_card(draft: Draft, storage, config: VNextConfig = DEFAULT) -> tuple[str, list] | None:
    keys = [k for k in draft.data.get("cards", {}).get("waste", []) if k not in draft.data.get("answers", {})]
    if not keys:
        return None
    key = keys[0]
    item = draft.find(key)
    if item is None:
        return None
    recent = _recent_waste_for(storage, item, config)
    lines = ["שים לב 🥬", ""]
    if recent:
        lines.append(f"דיווחתם שזרקתם {item['display_name']} לפני {recent} ימים.")
    else:
        lines.append(f"דיווחתם בעבר שזרקתם {item['display_name']}.")
    if item.get("included"):
        lines.append(f"הפחתתי את הכמות ל-{float(item['quantity']):g} הפעם.")
    else:
        lines.append("לא הוספתי הפעם.")
    lines += ["", "רוצה שאוסיף בכל זאת?"]
    return "\n".join(lines), [[("כן, הוסף", cb("waste", item["n"], "yes")), ("לא", cb("waste", item["n"], "no"))]]


def _recent_waste_for(storage, item: dict, config: VNextConfig) -> int | None:
    try:
        from .storage import normalize_term
        key = normalize_term(item["term"])
        for row in storage.recent_waste(50):
            if normalize_term(row.get("item_name", "")) == key:
                when = datetime.fromisoformat(str(row.get("reported_on") or "")[:10])
                days = (datetime.now() - when).days
                return days if days <= config.waste_window_days else None
    except Exception:  # noqa: BLE001
        logger.debug("waste lookup failed", exc_info=True)
    return None


def stockup_card(draft: Draft) -> tuple[str, list] | None:
    keys = [k for k in draft.data.get("cards", {}).get("stockup", []) if k not in draft.data.get("answers", {})]
    if not keys:
        return None
    item = draft.find(keys[0])
    if item is None or not item.get("promo"):
        return None
    p = item["promo"]
    store = display_name(p.get("store") or "")
    lines = ["מצאתי מבצע משתלם! 🔥", "",
             f"{p.get('product_name') or item['display_name']} ב-{store}"]
    if p.get("discount"):
        lines[-1] += f" ב-{float(p['discount']):.0%} הנחה"
    if p.get("deal_price") and p.get("reference_price"):
        lines.append(f"({_money(float(p['deal_price']))} במקום {_money(float(p['reference_price']))})")
    lines.append("")
    lines.append("זו לא קנייה קבועה שלך, אבל משתלם לאגור.")
    if p.get("units"):
        lines.append(f"להוסיף {p['units']} לעגלה?")
    else:
        lines.append("להוסיף לעגלה?")
    return "\n".join(lines), [[("כן, הוסף", cb("su", item["n"], "yes")), ("לא עכשיו", cb("su", item["n"], "no"))]]


# -- screen 3: review & adjust ----------------------------------------------------------

def _pages(items: list[dict], size: int) -> int:
    return max(1, (len(items) + size - 1) // size)


def review_screen(draft: Draft, config: VNextConfig = DEFAULT, full: bool = False) -> tuple[str, list]:
    """`full=True` lists optional items too (the "הצג את כל הרשימה" view)."""
    items = draft.included + (draft.optional if full else [])
    size = config.review_page_size
    pages = _pages(items, size)
    page = max(0, min(draft.page, pages - 1))
    chunk = items[page * size:(page + 1) * size]
    head = "הנה הרשימה. מה תרצה לשנות?" if not full else "כל הרשימה — כולל הצעות שלא נכנסו:"
    lines = [head, ""]
    for n, item in enumerate(chunk, start=1):
        store, price = item_price(item, draft.chains)
        mark = "✅" if item.get("included") else "▫️"
        icon = {KIND_MEAL: "🍳", KIND_STOCKUP: "💰", KIND_REQUEST: "📝"}.get(item["kind"], "")
        tail = f" {_money(price)}" if price is not None else ""
        flag = " ❓" if item["exception"]["class"] == "true_user_decision" else ""
        lines.append(f"{n}. {mark}{icon} {item['display_name']} {_qty(item)}{tail}{flag}")
    if pages > 1:
        lines += ["", f"עמוד {page + 1} מתוך {pages} · סה\"כ {len(items)} פריטים"]
    keyboard: list = []
    nums = [(str(n), cb("item", page * size + n - 1, "f" if full else "i")) for n in range(1, len(chunk) + 1)]
    for row_start in range(0, len(nums), 5):
        keyboard.append(nums[row_start:row_start + 5])
    nav = []
    if page > 0:
        nav.append(("◀", cb("list" if full else "edit", page - 1)))
    if page < pages - 1:
        nav.append(("▶", cb("list" if full else "edit", page + 1)))
    if nav:
        keyboard.append(nav)
    keyboard.append([("הוסף פריט", cb("add")), ("השווה בין רשתות", cb("compare"))])
    keyboard.append([("חזרה להצעה", cb("proposal")), ("אשר והכן עגלה", cb("go"))])
    return "\n".join(lines), keyboard


def item_screen(draft: Draft, index: int, full: bool = False) -> tuple[str, list] | None:
    items = draft.included + (draft.optional if full else [])
    if index < 0 or index >= len(items):
        return None
    item = items[index]
    lines = [item["display_name"], ""]
    for store, prod in (item.get("products") or {}).items():
        price = (item.get("prices") or {}).get(store)
        lines.append(f"• {display_name(store)}: {prod.get('product_name') or '—'}"
                     + (f" — {_money(float(price))}" if price is not None else "")
                     + (" (אישרת)" if prod.get("human") else ""))
    if not item.get("products"):
        lines.append("• לא מצאתי מוצר תואם ברשתות — יחופש בזמן המילוי")
    if item["exception"]["class"] == "true_user_decision":
        lines += ["", "❓ " + _decision_text(item)]
    lines += ["", f"כמות: {float(item['quantity']):g}"]
    keyboard = []
    if item.get("included"):
        keyboard.append([("הסר", cb("rm", item["n"])), ("כמות 1", cb("qty", item["n"], 1)), ("2", cb("qty", item["n"], 2)),
                         ("3", cb("qty", item["n"], 3)), ("4", cb("qty", item["n"], 4))])
    else:
        keyboard.append([("הוסף להצעה", cb("inc", item["n"]))])
    if item["exception"]["class"] == "true_user_decision" and item.get("products"):
        keyboard.append([("קח את ההצעה שלך", cb("keep", item["n"]))])
    if item.get("substitutes") and not item.get("products"):
        for n, sub in enumerate(item["substitutes"][:2]):
            keyboard.append([(f"במקום: {sub}"[:60], cb("sub", item["n"], n))])
    keyboard.append([("מצא אלטרנטיבה זולה יותר", cb("alt", item["n"]))])
    keyboard.append([("חזרה לרשימה", cb("list" if full else "edit", draft.page))])
    return "\n".join(lines), keyboard


def _decision_text(item: dict) -> str:
    why = item["exception"].get("reason") or ""
    products = item.get("products") or {}
    name = next((p.get("product_name") for p in products.values() if p.get("product_name")), "")
    if "brand not found" in why:
        return f"המותג המבוקש לא נמצא — לקחת {name}?" if name else "המותג המבוקש לא נמצא."
    if "substitutes differ" in why:
        subs = item.get("substitutes") or []
        return "לא נמצא בדיוק — " + (" / ".join(subs) if subs else "מה במקום?")
    if "stock-up commitment" in why:
        return f"מבצע גדול — לאגור {float(item['quantity']):g}?"
    return "צריך החלטה שלך"


# -- screen 4: multi-store -----------------------------------------------------------------

def compare_screen(draft: Draft, config: VNextConfig = DEFAULT) -> tuple[str, list]:
    from . import basket_optimizer

    quotes = basket_optimizer.quotes_for(draft.included, draft.chains, config)
    draft.data["quotes"] = quotes
    lines = ["השוואת מחירים הושלמה ✅", ""]
    for store in ("tivtaam", "shufersal"):
        q = quotes.get("chains", {}).get(store)
        if q is None:
            continue
        on = draft.chains.get(store, True)
        mark = "✅" if on else "▫️"
        lines.append(f"{mark} {display_name(store)}: {count(q['items'], 'פריט אחד', 'פריטים', '0 פריטים')} — "
                     f"{_money(q['subtotal'])}" + (f" + משלוח {_money(q['delivery'])}" if q['delivery'] else ""))
        if q.get("promos"):
            lines.append(f"   כולל {count(q['promos'], 'מבצע אחד', 'מבצעים')}")
        if q.get("missing"):
            lines.append(f"   לא נמצא שם: {', '.join(q['missing'][:4])}" + (" …" if len(q['missing']) > 4 else ""))
    rec = quotes.get("recommendation") or {}
    lines.append("")
    if rec.get("split"):
        lines.append("💡 הצעה חכמה")
        parts = " ו-".join(f"{count(n, 'פריט אחד', 'פריטים')} ב{display_name(s)}" for s, n in rec["split"].items())
        lines.append(f"לקנות {parts}.")
        lines.append(f"חיסכון צפוי: {_money(rec.get('saving'))} (אחרי שני משלוחים)")
    elif rec.get("single"):
        lines.append(f"💡 הכי משתלם: הכל ב{display_name(rec['single'])} — {_money(rec.get('total'))} כולל משלוח")
        if rec.get("saving_vs_other"):
            lines.append(f"(זול ב-{_money(rec['saving_vs_other'])} מהרשת השנייה)")
    if rec.get("note"):
        lines.append(rec["note"])
    toggles = [(("✅ " if draft.chains.get(s, True) else "▫️ ") + display_name(s), cb("chain", s))
               for s in ("tivtaam", "shufersal") if s in draft.chains]
    keyboard = [toggles, [("אשר והכן עגלות", cb("go"))], [("הצג פירוט מלא", cb("detail"))],
                [("חזרה להצעה", cb("proposal"))]]
    return "\n".join(lines), keyboard


def detail_screen(draft: Draft) -> str:
    quotes = draft.data.get("quotes") or {}
    lines = ["פירוט לפי רשת:", ""]
    for store, q in (quotes.get("chains") or {}).items():
        lines.append(f"{display_name(store)} — {_money(q['subtotal'])}")
        for line in q.get("lines", [])[:40]:
            lines.append(f"  • {line['name']} {_money(line['price'])}" + (" 🔥" if line.get("promo") else ""))
        if len(q.get("lines", [])) > 40:
            lines.append(f"  … ועוד {len(q['lines']) - 40}")
        lines.append("")
    return "\n".join(lines).strip() or "אין פירוט זמין."


# -- screen 5: execution --------------------------------------------------------------

def execution_terms(draft: Draft, quotes: dict | None = None) -> dict:
    """{store: [(PlanTerm, Identity|None), ...]} — what actually goes to
    the engine, per chain, following the optimizer's assignment. Items no
    chain can quote go to the first enabled chain by name search."""
    from .identity import Identity
    from .models import PlanTerm

    quotes = quotes or draft.data.get("quotes") or {}
    assignment = quotes.get("assignment") or {}
    enabled = draft.enabled_chains() or list(draft.chains)
    out: dict = {s: [] for s in enabled}
    for item in draft.included:
        store = assignment.get(item["key"]) or next((s for s in enabled if s in (item.get("products") or {})), None)
        if store not in out:
            store = enabled[0] if enabled else None
        if store is None:
            continue
        prod = (item.get("products") or {}).get(store)
        ident = None
        if prod and prod.get("product_code") and prod.get("product_name"):
            ident = Identity(store, prod["product_code"], prod["product_name"], "", "vnext_resolver")
        qty = max(1, int(round(float(item.get("quantity") or 1))))
        kind = "adhoc" if item["kind"] == KIND_REQUEST else "freeform"
        out[store].append((PlanTerm(item["term"], qty, kind, None), ident))
    return {s: t for s, t in out.items() if t}


def execution_screen(draft: Draft, results: dict, carts: dict | None = None) -> tuple[str, list]:
    """`results`: {store: {"added": n, "already": n, "failed": [names], "unverified": [names]}}."""
    lines = ["🎉 הכל מוכן!", ""] if results else ["לא היה מה להכניס.", ""]
    keyboard: list = []
    links = []
    for store, r in results.items():
        cart = (carts or {}).get(store) or {}
        total = cart.get("total")
        n = r.get("added", 0) + r.get("already", 0)
        head = f"✅ {display_name(store)} — עגלה מוכנה ({count(n, 'פריט אחד', 'פריטים', '0 פריטים')}"
        head += f", {_money(float(total))})" if total else ")"
        lines.append(head)
        if r.get("already"):
            lines.append(f"   {count(r['already'], 'אחד כבר היה בעגלה', 'כבר היו בעגלה')}")
        if r.get("failed"):
            lines.append(f"   ⚠️ לא נמצא: {', '.join(r['failed'][:5])}" + (" …" if len(r['failed']) > 5 else ""))
        if r.get("unverified"):
            lines.append(f"   🔎 לא אומת: {', '.join(r['unverified'][:5])}")
        links.append((f"🛒 פתח את העגלה ב{display_name(store)}", (KEY_URL, CHAIN_CART_URL.get(store, ""))))
    lines += ["", "רוצה לעבור לתשלום באתר, או להשאיר את העגלות לאישור ידני?",
              "(אני לא משלם ולא מזמין — זה תמיד שלך.)"]
    for link in links:
        keyboard.append([link])
    keyboard.append([("השאר לאישור ידני", cb("done", "manual"))])
    keyboard.append([("שלח לי קישורים", cb("links")), ("בצע שינויים נוספים", cb("edit", 0))])
    return "\n".join(lines), keyboard


def summarise_reports(reports: dict) -> dict:
    """Per-store counts from the engine's OrderCycleReports, for the screen."""
    out: dict = {}
    for store, report in (reports or {}).items():
        added = [r for r in getattr(report, "added", [])]
        skipped = list(getattr(report, "skipped", []) or [])
        failed = [r.item_name for r in list(getattr(report, "not_found", []) or []) + list(getattr(report, "errors", []) or [])]
        unverified = [r.item_name for r in added if getattr(r, "verification", "") == "unverified"]
        out[store] = {
            "added": len([r for r in added if getattr(r, "verification", "") != "unverified"]),
            "already": len([r for r in skipped if "כבר" in (getattr(r, "detail", "") or "")]),
            "failed": failed, "unverified": unverified,
        }
    return out


def links_text(results: dict) -> str:
    return "\n".join(f"{display_name(s)}: {CHAIN_CART_URL.get(s, '')}" for s in results) or "אין עגלות פתוחות."


def alternative_prompt(item: dict, added_qty: int, has_alternative: bool) -> str:
    text = f"הבנתי. הוספתי {item['display_name']} ({added_qty} יח') לעגלה."
    if has_alternative:
        text += "\nרוצה שאבדוק אם יש מבצע או אלטרנטיבה זולה יותר?"
    return text
