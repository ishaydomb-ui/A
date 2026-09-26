"""The review before the household opens the store's site.

Basics in Order §3 (Ishay, 26.09.2026, via Boss under Mandate 1): "הודעת
סקירה בטלגרם לפני שישי פותח את האתר". The standing cart is filled right
after a shop and sits for days; by the time he opens it, the useful
questions are the ones the cart itself cannot answer — what the bot put
in on its own, what it could not put in, and what he keeps deleting.

**Quiet by construction** — Ishay, 20.09: cart pings "שולח כמה פעמים ביום
ואין משמעות". So this is one message per filled cart, sent only on the
day the household is already being told to shop (right after the
cadence nudge or digest), and on demand with /review. A cart that was
not refilled since the last review is not reviewed again.

Read-only, from the database: no browser, no store request. The facts
are the last fill's own records, so they say "as filled", not "as it is
now" — the household may already have edited the cart.
"""
from __future__ import annotations

import json
from collections import Counter

from .htmltext import bold, escape

_SENT_KEY = "cart_review_sent_for"

NEEDS_ATTENTION = ("failed_product", "unverified", "unresolved_ambiguity")
_LIST_LIMIT = 8


def _manifest(storage) -> dict:
    try:
        return json.loads(storage.get_state("standing_cart_manifest") or "{}")
    except ValueError:
        return {}


def _since_fill(storage, fill_at: str) -> dict[str, list[tuple[str, str]]]:
    """(term, outcome) the runs since the fill could not settle, per chain."""
    out: dict[str, list[tuple[str, str]]] = {}
    rows = storage.run_items_since(fill_at[:10], NEEDS_ATTENTION + ("verified", "skipped"))
    # A term that failed once and landed in a later run (or was already
    # there) is not open; only its latest outcome counts.
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        latest[(row["store"], (row["term"] or "").strip())] = row
    rows = [r for r in latest.values() if r["outcome"] in NEEDS_ATTENTION]
    seen = set()
    for row in rows:
        store, term, outcome = row["store"], row["term"], row["outcome"]
        key = (store, (term or "").strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.setdefault(store, []).append((key[1], outcome))
    return out


def _usually_deleted(storage) -> list[str]:
    try:
        log = json.loads(storage.get_state("standing_cart_removal_log") or "[]")
    except ValueError:
        return []
    counts = Counter((row.get("name") or "").strip() for row in log if isinstance(row, dict))
    return [name for name, n in counts.most_common() if name and n >= 2][:_LIST_LIMIT]


def build(storage) -> tuple[str, list[tuple[str, str]]]:
    """(HTML text, [(button label, url)]), or ("", []) when nothing is filled."""
    from .chains import display_name

    manifest = _manifest(storage)
    stores = {s: rows for s, rows in (manifest.get("stores") or {}).items() if rows}
    if not stores:
        return "", []
    fill_at = str(manifest.get("at") or "")
    lines = [bold("🧾 לפני שנכנסים לעגלה"), f"מולאה ב-{escape(fill_at[:10])}; ייתכן שכבר שינית משהו."]

    for store, rows in sorted(stores.items()):
        lines.append(f"• {escape(display_name(store))}: {len(rows)} שורות הוכנסו")

    deals: dict[str, list[str]] = {}
    for row in storage.run_items_since(fill_at[:10], ("verified",)):
        if row.get("source_kind") == "deal" and row.get("term"):
            names = deals.setdefault(row["store"], [])
            if row["term"] not in names:
                names.append(row["term"])
    if deals:
        lines += ["", bold("🏷️ מבצעים שהכנסתי לבד (אפשר למחוק):")]
        for store, names in sorted(deals.items()):
            lines.append(f"• {escape(display_name(store))}: " + ", ".join(escape(n) for n in names[:_LIST_LIMIT]))

    open_items = _since_fill(storage, fill_at)
    if open_items:
        lines += ["", bold("⚠️ לא נכנסו או לא ודאי שנכנסו — כדאי לבדוק באתר:")]
        for store, items in sorted(open_items.items()):
            names = ", ".join(escape(term) for term, _ in items[:_LIST_LIMIT])
            more = f" ועוד {len(items) - _LIST_LIMIT}" if len(items) > _LIST_LIMIT else ""
            lines.append(f"• {escape(display_name(store))}: {names}{more}")
        if any(o == "unresolved_ambiguity" for items in open_items.values() for _, o in items):
            lines.append("לבחירה בין מוצרים: /questions")

    pending = storage.list_pending_adhoc()
    if pending:
        names = ", ".join(escape(r.text) for r in pending[:_LIST_LIMIT])
        more = f" ועוד {len(pending) - _LIST_LIMIT}" if len(pending) > _LIST_LIMIT else ""
        lines += ["", bold("📝 בקשות שעוד מחכות:") + f" {names}{more}"]

    deleted = _usually_deleted(storage)
    if deleted:
        lines += ["", bold("🗑️ בדרך כלל נמחק אצלך:") + " " + ", ".join(escape(n) for n in deleted)]

    lines += ["", "לא משלמים דרכי — הקנייה נגמרת אצלך באתר."]
    return "\n".join(lines), _cart_buttons(stores)


def _cart_buttons(stores) -> list[tuple[str, str]]:
    from .adapters.shufersal import CART_URL as SHUFERSAL_CART
    from .adapters.tivtaam import CART_URL as TIVTAAM_CART
    from .chains import display_name

    urls = {"shufersal": SHUFERSAL_CART, "tivtaam": TIVTAAM_CART}
    return [(f"🛒 {display_name(s)}", urls[s]) for s in sorted(stores) if s in urls]


def due(storage) -> bool:
    """Has this fill not been reviewed yet?"""
    at = str(_manifest(storage).get("at") or "")
    return bool(at) and storage.get_state(_SENT_KEY) != at


def mark_sent(storage) -> None:
    storage.set_state(_SENT_KEY, str(_manifest(storage).get("at") or ""))
