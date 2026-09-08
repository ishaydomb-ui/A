"""Keep the cart full between shops, so finishing is only deleting.

Ishay's own method, stated 2026-09-07 after watching a real order run:
he fills the cart with everything he might want and deletes what he
does not need, because under-buying costs a missing staple all week
while over-buying costs one tap. If that is the method, the cart may as
well already be full when he opens it.

    a shop completes  ->  refill from the standing list, plus deals
    during the week   ->  requests add or remove, as they arrive
    he opens the cart ->  reviews, deletes, pays
    repeat

**Why refilling is a separate path from the order cycle.** The cycle
fills from `base_list_items` — 28 curated items. This fills from the
`everything` list shape: every product bought in the past year, 147 of
them, rare ones included. That is a different question ("what might he
want") from the cycle's ("what did he ask for"), and answering both
from one list would force the two to agree.

**The list is configurable because it is expected to shrink.** Ishay,
choosing it: "נתחיל מהאופציה הראשונה + מבצעים. מניח שנוריד את זה בהמשך
לפחות מוצרים." So the shape is a setting, not a constant, and moving to
`full` (85) or `core` (19) later is a one-line change rather than a
rewrite.

**Removals are reported, never learned from — his call, same day.** The
bot records what it put in; the difference between that and what is in
the cart later is what he took out. A monthly note tells him what he
keeps deleting so *he* can decide to drop it. The tempting version —
demote automatically after N removals — was offered and declined, and
the reason to respect that is that a wrong auto-demotion silently stops
buying something the household actually needs, which is the expensive
direction of this trade.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import dealfill
from .listbuilder import available_lists, build as build_list

# Which list shape refills the cart. See the docstring: expected to move
# down as the household sees how much deleting it actually costs them.
DEFAULT_LIST = "everything"

_MANIFEST_KEY = "standing_cart_manifest"
_LAST_SHOP_KEY = "standing_cart_last_shop"
_REMOVAL_LOG_KEY = "standing_cart_removal_log"
_REMOVAL_REPORTED_KEY = "standing_cart_removals_reported"

# Ishay asked for the removal note monthly, not per shop: he shops
# roughly weekly, and being shown the same four products every week is
# the nagging he declined the automatic version to avoid.
REPORT_EVERY_DAYS = 30


@dataclass(frozen=True)
class RefillPlan:
    """What a refill will put in one chain's cart, before it runs."""

    store: str
    terms: list[tuple[str, int]]
    deals: list

    @property
    def total(self) -> int:
        return len(self.terms)


def plan_refill(storage, store: str, list_key: str = DEFAULT_LIST) -> RefillPlan:
    """The terms to put into `store`'s cart, standing list plus deals.

    Ad-hoc requests are deliberately not included: they are handled the
    moment they arrive, and re-adding a consumed one here would put back
    something the household already bought.
    """
    spec = next((s for s in available_lists() if s.key == list_key), None)
    if spec is None:
        raise KeyError(f"unknown list shape {list_key!r}")

    rows = storage.list_stock_items(store)
    if not rows:
        # A chain with no purchase history of its own still deserves a
        # full cart: fall back to the household's curated base list
        # rather than filling nothing. Tiv Taam is exactly this case.
        base = storage.list_active_base_items()
        terms = [(b.search_term_for(store), b.default_quantity) for b in base]
    else:
        built = build_list(spec, rows, storage.last_purchase_dates(store))
        terms = [(row["product_name"], row.get("default_quantity") or 1)
                 for row in built.items]

    picks = dealfill.picks_for(storage, store, skip_terms=[t for t, _ in terms])
    terms += [(p.term, p.quantity) for p in picks]
    return RefillPlan(store=store, terms=terms, deals=picks)


def record_manifest(storage, reports) -> None:
    """Remember what we put in, so a later removal can be recognised.

    Keyed by the store's own product code where we have one. A name is
    kept alongside because the monthly note has to be readable, and a
    product code means nothing to a person.
    """
    manifest = {}
    for store, report in (reports or {}).items():
        manifest[store] = [
            {"code": r.product_code, "name": r.item_name}
            for r in report.added
        ]
    storage.set_state(
        _MANIFEST_KEY,
        json.dumps(
            {"at": datetime.now(timezone.utc).isoformat(), "stores": manifest},
            ensure_ascii=False,
        ),
    )


def _manifest(storage) -> dict:
    raw = storage.get_state(_MANIFEST_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def removals(storage, store: str, cart_items) -> list[dict]:
    """What the household took out since the cart was filled.

    `cart_items` is the adapter's own reading of the live cart. Anything
    in the manifest and no longer in the cart was removed by a person —
    the bot never removes on its own.
    """
    data = _manifest(storage).get("stores", {}).get(store, [])
    if not data:
        return []
    present = {str(item.get("code") or "") for item in (cart_items or [])}
    present |= {(item.get("name") or "").strip() for item in (cart_items or [])}
    return [
        row for row in data
        if str(row.get("code") or "") not in present
        and (row.get("name") or "").strip() not in present
    ]


def shop_detected_since_refill(storage, store: str = "shufersal") -> str:
    """A newer order than our last refill means a shop happened. Returns
    the order date, or "" when there is nothing new.

    The backstop for the signal that actually matters. Ishay's own words
    on finding an empty cart: he had *told* the bot he ordered, and it
    still waited for a command. Free text now covers that, and this
    covers the case where nobody says anything at all.

    Deliberately slow and certain rather than fast. Measured 2026-09-08:
    the Shufersal order placed on 09-07 was **not** in the chain's own
    order history hours later, and only appeared about 36 hours after
    checkout. So this cannot be the primary signal — it is the one that
    catches what the others miss, a day or two late, which for a cart
    that is not needed for five days is late enough to still be useful.
    A confirmation email arrives within minutes and is the natural fast
    path; it needs mailbox credentials this project does not hold.
    """
    from contextlib import closing

    with closing(storage._connect()) as conn:  # noqa: SLF001 - storage-internal
        row = conn.execute(
            "SELECT MAX(placed_at) AS newest FROM order_log WHERE store = ?",
            (store,),
        ).fetchone()
    newest = (row["newest"] or "") if row else ""
    if not newest:
        return ""
    # A refill after the order means this shop is already handled; the
    # cart it produced is what the household is looking at now.
    last = last_shop(storage)
    if last and newest[:10] <= last:
        return ""
    return newest


def cart_contents(storage) -> list[tuple[str, int]]:
    """What we last put in each cart: (store key, item count).

    Read from the manifest rather than from the live carts on purpose —
    the nudge is composed by a CLI with no browser, no store session and
    no Israeli exit, and making a reminder depend on all three would
    mean no reminder whenever any of them is down.
    """
    stores = _manifest(storage).get("stores", {})
    return [(store, len(items)) for store, items in stores.items() if items]


def mark_shopped(storage, today: date | None = None) -> str:
    """Record that a shop finished, which is what triggers a refill."""
    day = (today or date.today()).isoformat()
    storage.set_state(_LAST_SHOP_KEY, day)
    return day


def last_shop(storage) -> str:
    return storage.get_state(_LAST_SHOP_KEY) or ""


def log_removals(storage, store: str, rows: list[dict], today: date | None = None) -> int:
    """Add this shop's removals to the running log. Returns the new total.

    Recorded at `/done`, which is the one moment the answer is knowable:
    the manifest says what went in, the cart at checkout says what
    survived, and the difference is what a person took out.
    """
    if not rows:
        return 0
    day = (today or date.today()).isoformat()
    log = _removal_log(storage)
    log.extend(
        {"store": store, "name": (r.get("name") or "").strip(), "on": day}
        for r in rows if (r.get("name") or "").strip()
    )
    # Keep a year at most; this is a habit signal, not an archive.
    log = log[-2000:]
    storage.set_state(_REMOVAL_LOG_KEY, json.dumps(log, ensure_ascii=False))
    return len(log)


def _removal_log(storage) -> list[dict]:
    raw = storage.get_state(_REMOVAL_LOG_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def removal_report_due(storage, today: date | None = None) -> bool:
    """Has a month passed since the household last saw this?"""
    day = today or date.today()
    last = storage.get_state(_REMOVAL_REPORTED_KEY)
    if not last:
        return bool(_removal_log(storage))
    try:
        previous = date.fromisoformat(last[:10])
    except ValueError:
        return True
    return (day - previous).days >= REPORT_EVERY_DAYS and bool(_removal_log(storage))


def due_removal_report(storage, today: date | None = None) -> str:
    """The monthly note, or "" — and it clears the log once shown."""
    if not removal_report_due(storage, today):
        return ""
    from .chains import display_name

    log = _removal_log(storage)
    parts = []
    for store in sorted({row.get("store", "") for row in log}):
        rows = [r for r in log if r.get("store") == store]
        text = format_removal_report(rows, display_name(store))
        if text:
            parts.append(text)
    if not parts:
        return ""
    storage.set_state(_REMOVAL_REPORTED_KEY, (today or date.today()).isoformat())
    storage.set_state(_REMOVAL_LOG_KEY, json.dumps([], ensure_ascii=False))
    return "\n\n".join(parts)


def format_removal_report(rows: list[dict], store_name: str) -> str:
    """The monthly note. Reports, never nags, and suggests nothing.

    Ishay chose reporting over automatic learning, so this deliberately
    stops at the fact. It does not ask whether to drop the item, because
    a question every month about the same product is its own kind of
    nagging.
    """
    if not rows:
        return ""
    from .mdtext import escape as md

    counted = {}
    for row in rows:
        name = (row.get("name") or "").strip()
        if name:
            counted[name] = counted.get(name, 0) + 1
    ranked = sorted(counted.items(), key=lambda kv: -kv[1])
    lines = [f"🧾 *מה הורדת מהעגלה ב{store_name}*", ""]
    for name, times in ranked[:15]:
        suffix = f" ×{times}" if times > 1 else ""
        lines.append(f"• {md(name)}{suffix}")
    lines.append("")
    lines.append("_רק דיווח — לא שיניתי כלום ברשימה._")
    return "\n".join(lines)
