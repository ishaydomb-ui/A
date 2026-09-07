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
