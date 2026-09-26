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
# `core` since 26.09.2026 (Ishay, via Boss: "שגורדון יתקן" on a Tiv Taam
# cart grown to 281 lines / ₪5,688.88). Measured that day: core + deals
# is 34 lines at Tiv Taam and 44 at Shufersal, against real orders of 37
# and 48; `full` would be 65 / 97 and `everything` 285 / 181.
DEFAULT_LIST = "core"

_MANIFEST_KEY = "standing_cart_manifest"
_LAST_SHOP_KEY = "standing_cart_last_shop"
# Per chain, because a shop at one chain says nothing about the other's
# cart. `/done` has always known which chain it was — `done_shopping`
# takes a `store` and `_mark_shop_done` scopes the requests by it — and
# then dropped it on the floor here. That cost a real message on
# 2026-09-15: Ishay shopped Tiv Taam on 09-13, and the only honest
# options left were to describe both carts as ready (wrong for Tiv Taam,
# which he had just emptied) or neither (wrong for Shufersal, whose 120
# items were still sitting there untouched).
_LAST_SHOP_BY_STORE_KEY = "standing_cart_last_shop_by_store"
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
    terms: list  # of models.PlanTerm — was list[tuple[str, int]] until Phase 1
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

    from .models import PlanTerm

    rows = storage.list_stock_items(store)
    if not rows:
        # A chain with no purchase history of its own still deserves a
        # full cart: fall back to the household's curated base list
        # rather than filling nothing. Tiv Taam is exactly this case.
        base = storage.list_active_base_items()
        terms = [
            PlanTerm(b.search_term_for(store), b.default_quantity, "base", str(b.id))
            for b in base
        ]
    else:
        built = build_list(spec, rows, storage.last_purchase_dates(store))
        # The product code is the identity here, and it was in `row` all
        # along — this is one of the three places it used to be dropped.
        terms = [
            PlanTerm(row["product_name"], row.get("default_quantity") or 1,
                     "stock", str(row.get("product_code") or ""))
            for row in built.items
        ]

    picks = dealfill.picks_for(storage, store, skip_terms=[t.term for t in terms])
    # A pick carries no promotion id; the search term is its identity.
    terms += [PlanTerm(p.term, p.quantity, "deal", None) for p in picks]
    return RefillPlan(store=store, terms=terms, deals=picks)


def tag_deal_results(report, plan) -> int:
    """Mark the lines a refill added *because of a promotion*.

    The order cycle tags these as it goes; the standing-cart refill does
    not, because it fills through `add_terms_to_cart` — a path that only
    knows terms, not why each term is on the list. The consequence was
    found by Ishay on 2026-09-09 asking which items were deals:
    `/lastdeals` was empty after a refill that had added six of them, and
    the answer had to be reconstructed from a log file. A feature nobody
    can query is not a feature.

    Matching is on the term the pick was searched under, which is what
    `add_terms_to_cart` records as the item name.
    """
    labels = {p.term: p.label for p in (plan.deals or [])}
    tagged = 0
    for result in report.added:
        label = labels.get(result.item_name)
        if label:
            result.deal = label
            tagged += 1
    return tagged


def record_manifest(storage, reports, at: datetime | None = None) -> None:
    """Remember what we put in, so a later removal can be recognised.

    Keyed by the store's own product code where we have one. A name is
    kept alongside because the monthly note has to be readable, and a
    product code means nothing to a person.

    `at` overrides the timestamp, which exists so a test can place a
    manifest *before* a shop. That ordering is the whole of
    `manifest_is_stale` and it cannot be reproduced with a wall clock.
    """
    # Merged per chain, with a time per chain: since 26.09 a refill fills
    # only the chain that was shopped, and overwriting the whole manifest
    # would erase the other chain's record of what the bot put in.
    stamp = (at or datetime.now(timezone.utc)).isoformat()
    previous = _manifest(storage)
    manifest = dict(previous.get("stores") or {})
    at_by_store = dict(previous.get("at_by_store") or {})
    for store in manifest:
        at_by_store.setdefault(store, previous.get("at", ""))
    for store, report in (reports or {}).items():
        manifest[store] = [
            {"code": r.product_code, "name": r.item_name}
            for r in report.added
        ]
        at_by_store[store] = stamp
    storage.set_state(
        _MANIFEST_KEY,
        json.dumps(
            {
                "at": stamp,
                "at_by_store": at_by_store,
                "stores": manifest,
            },
            ensure_ascii=False,
        ),
    )


def manifest_at(storage, store: str) -> str:
    """When this chain's manifest was written ("" if never)."""
    data = _manifest(storage)
    return str((data.get("at_by_store") or {}).get(store) or data.get("at") or "")


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

    **An empty cart proves nothing and is treated as nothing.** This runs
    at `/done`, right after a shop, and a chain empties the cart when an
    order is placed. So "the manifest holds 120 items and the cart holds
    none" is the *expected* reading after a completed purchase, not 120
    deletions — and logging it as deletions would fill the habit report
    with every product the household actually bought. Caught 2026-09-11
    before it ever ran: the log was still empty, and the live manifest
    held 120 Shufersal lines waiting to be misread at the next `/done`.

    A cart with something in it is a different matter: the rest survived
    checkout or was never bought, so a line missing from it really was
    taken out.
    """
    data = _manifest(storage).get("stores", {}).get(store, [])
    if not data:
        return []
    if not cart_items:
        return []
    present = {str(item.get("code") or "") for item in (cart_items or [])}
    present |= {(item.get("name") or "").strip() for item in (cart_items or [])}
    return [
        row for row in data
        if str(row.get("code") or "") not in present
        and (row.get("name") or "").strip() not in present
    ]


_SHOPPED_SNAPSHOT_KEY = "standing_cart_shopped_snapshot"

# How far back an order may sit and still be the one that emptied this
# snapshot. The household pays, then the order surfaces in the chain's
# history hours later; two days of slack covers Shufersal's measured ~36
# hours with room for a late-evening shop, and stops a *later* order
# being matched against a stale snapshot.
SNAPSHOT_MATCH_DAYS = 2


def _snapshot_shopped_cart(storage, store: str, day: str) -> None:
    """Keep this chain's manifest as it stood when the shop finished."""
    manifest = _manifest(storage)
    items = manifest.get("stores", {}).get(store) or []
    if not items:
        return
    snapshots = _shopped_snapshots(storage)
    # `manifest_at` is when these lines went in. An order placed before
    # that cannot be judged against them — see `shop_comparison`.
    snapshots[store] = {"at": day, "manifest_at": manifest_at(storage, store), "items": items}
    storage.set_state(
        _SHOPPED_SNAPSHOT_KEY, json.dumps(snapshots, ensure_ascii=False)
    )


def _shopped_snapshots(storage) -> dict:
    raw = storage.get_state(_SHOPPED_SNAPSHOT_KEY)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def shop_comparison(storage, store: str, order_items, placed_at: str = "",
                    known_names=None) -> dict | None:
    """Compare the shopped snapshot with its order; None when not comparable.

    Returns {"removed": rows, "added": n, "identifiable": n}. `added` is
    what the bot put in; `identifiable` is how many of those a missing
    line could honestly be judged for.

    **A line the order could not recognise is not a deletion.** Tiv Taam
    manifests are mostly name-only, and the name is often the list's
    wording ("חלב 1% קרטון 1 ליטר") rather than the site's ("חלב 1% קרטון
    - בפיקוח") — measured 2026-09-26, 75 of 146 names matched any order
    ever placed. Counting every unmatched name as deleted would report a
    ~50% deletion rate made of spelling. So when `known_names` is given
    (names this chain's orders have really carried), a name-only line
    counts only if its name is one of them; the rest are left out of
    both the numerator and the denominator. None keeps the old behaviour
    of trusting every name, for callers that have no such list.

    Not comparable: no snapshot, no order lines, an order too far from
    the shop, or an order placed before the snapshot's lines went in
    (the 2026-09-24 case: a snapshot of an old manifest against the
    22.09 order would have logged ~70 phantom deletions). A snapshot
    written before `manifest_at` existed cannot be placed in time and is
    treated as not comparable too.
    """
    snapshot = _shopped_snapshots(storage).get(store) or {}
    items = snapshot.get("items") or []
    if not items or not order_items:
        return None
    manifest_at = str(snapshot.get("manifest_at") or "")
    if not manifest_at:
        return None
    if placed_at:
        try:
            ordered = date.fromisoformat(str(placed_at)[:10])
            filled = date.fromisoformat(manifest_at[:10])
        except ValueError:
            return None
        if ordered < filled:
            return None
        if snapshot.get("at"):
            try:
                shopped = date.fromisoformat(str(snapshot["at"])[:10])
            except ValueError:
                return None
            if abs((ordered - shopped).days) > SNAPSHOT_MATCH_DAYS:
                return None

    bought_codes = {str(item.get("code") or "") for item in order_items} - {""}
    bought_names = {(item.get("name") or "").strip() for item in order_items} - {""}
    known = None if known_names is None else ({n.strip() for n in known_names} | bought_names)
    removed, identifiable = [], 0
    for row in items:
        code = str(row.get("code") or "")
        name = (row.get("name") or "").strip()
        if (code and code in bought_codes) or (name and name in bought_names):
            identifiable += 1
            continue
        if code or known is None or name in known:
            identifiable += 1
            removed.append(row)
    return {"removed": removed, "added": len(items), "identifiable": identifiable}


_SHOP_STATS_KEY = "standing_cart_shop_stats"


def record_shop_outcome(storage, store: str, order_items, placed_at: str = "",
                        order_code: str = "", known_names=None) -> dict | None:
    """Compare, log the deletions, keep one stats row, clear the snapshot.

    The one entry point for both chains. The per-shop stats row is the
    metric (how much of what the bot put in was deleted); the item log
    stays what it was, the input to the monthly note. Returns the stats
    row, or None when this order says nothing about the snapshot — in
    which case the snapshot is kept for a later order, unless it can
    never be compared (no `manifest_at`, or an order already past the
    match window), when it is dropped so it cannot misfire later.
    """
    snapshot = _shopped_snapshots(storage).get(store) or {}
    if not snapshot:
        return None
    result = shop_comparison(storage, store, order_items, placed_at, known_names)
    if result is None:
        if not snapshot.get("manifest_at"):
            clear_shopped_snapshot(storage, store)
        elif placed_at and snapshot.get("at"):
            try:
                late = (date.fromisoformat(str(placed_at)[:10])
                        - date.fromisoformat(str(snapshot["at"])[:10])).days > SNAPSHOT_MATCH_DAYS
            except ValueError:
                late = False
            if late:
                clear_shopped_snapshot(storage, store)
        return None
    log_removals(storage, store, result["removed"])
    row = {
        "store": store,
        "shop": str(snapshot.get("at") or ""),
        "order": str(order_code or ""),
        "placed_at": str(placed_at or ""),
        "added": result["added"],
        "identifiable": result["identifiable"],
        "removed": len(result["removed"]),
        "rate": round(len(result["removed"]) / result["identifiable"], 3)
        if result["identifiable"] else None,
    }
    stats = shop_stats(storage)
    stats.append(row)
    storage.set_state(_SHOP_STATS_KEY, json.dumps(stats[-200:], ensure_ascii=False))
    clear_shopped_snapshot(storage, store)
    return row


def shop_stats(storage) -> list[dict]:
    """Every compared shop, oldest first. Never cleared by the monthly note."""
    raw = storage.get_state(_SHOP_STATS_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def removals_from_order(storage, store: str, order_items, placed_at: str = "") -> list:
    """What the household deleted before paying, read from the order.

    The reliable observation, and the one the previous design could not
    make. `removals()` compares the manifest against the *live cart*,
    which works only before checkout — after a real shop the cart is
    always empty, so an honest empty-cart guard (added when an empty cart
    was being read as "everything was deleted") left removals never
    recorded at all. What the order actually contained is the answer that
    survives the checkout.

    Returns [] rather than guessing whenever the comparison would be
    meaningless: no snapshot, no order lines, or an order too far from
    the shop to be the one that emptied this cart.
    """
    snapshot = _shopped_snapshots(storage).get(store) or {}
    items = snapshot.get("items") or []
    if not items or not order_items:
        return []

    if placed_at and snapshot.get("at"):
        try:
            shopped = date.fromisoformat(str(snapshot["at"])[:10])
            ordered = date.fromisoformat(str(placed_at)[:10])
        except ValueError:
            pass
        else:
            # An order from well before the shop cannot be the one that
            # emptied this cart; neither can one from well after.
            if abs((ordered - shopped).days) > SNAPSHOT_MATCH_DAYS:
                return []

    bought = {str(item.get("code") or "") for item in order_items}
    bought |= {(item.get("name") or "").strip() for item in order_items}
    bought.discard("")
    return [
        row for row in items
        if str(row.get("code") or "") not in bought
        and (row.get("name") or "").strip() not in bought
    ]


def clear_shopped_snapshot(storage, store: str) -> None:
    """Forget a snapshot once its order has been compared against it."""
    snapshots = _shopped_snapshots(storage)
    if snapshots.pop(store, None) is None:
        return
    storage.set_state(
        _SHOPPED_SNAPSHOT_KEY, json.dumps(snapshots, ensure_ascii=False)
    )


def pending_snapshot_stores(storage) -> list:
    """Chains whose shopped cart is still waiting for its order."""
    return sorted(_shopped_snapshots(storage))


def shops_detected_since_refill(storage, stores=None) -> dict:
    """Which chains have an order newer than their own last refill.

    Returns ``{store: order date}``, empty when nothing is new. The
    per-chain version of `shop_detected_since_refill`, and the reason it
    exists: until 2026-09-15 `order_log` held only Shufersal, so this
    backstop could only ever fire for one chain. Tiv Taam's own API had
    the 09-12 order the same day it was placed — against Shufersal's
    measured ~36 hours — which makes it the *faster* of the two, not a
    straggler.

    The comparison falls back to the global shop date for a chain with no
    record of its own. Without that, every pre-2026-09-15 order would read
    as new the first time this runs and refill a cart nobody emptied.
    Note this is the opposite default from `manifest_is_stale`, which
    treats an unknown chain as *not* shopped — both choices err towards
    not acting on something we do not know.
    """
    from contextlib import closing

    from .chains import CART_CAPABLE

    with closing(storage._connect()) as conn:  # noqa: SLF001 - storage-internal
        rows = conn.execute(
            "SELECT store, MAX(placed_at) AS newest FROM order_log GROUP BY store"
        ).fetchall()

    wanted = set(stores or CART_CAPABLE)
    global_last = last_shop(storage)
    detected = {}
    for row in rows:
        store = row["store"]
        newest = row["newest"] or ""
        if store not in wanted or not newest:
            continue
        last = last_shop(storage, store) or global_last
        if last and newest[:10] <= last:
            continue
        detected[store] = newest
    return detected


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


def manifest_is_stale(storage, store: str) -> bool:
    """Whether the manifest describes a cart this chain has since emptied.

    A manifest written *before* a shop at this chain describes items that
    left the cart at checkout. Saying "the cart is ready" from it is not
    a small inaccuracy — it is a claim about the present tense built from
    a snapshot the household already paid for.

    Measured 2026-09-15: the nudge told Ishay "120 items in Shufersal and
    1 in Tiv Taam, the cart is ready" from a manifest dated 09-08, two
    days after he had shopped Tiv Taam. Normally a refill follows a shop
    and rewrites the manifest, so the two stay in step; that only holds
    while every shop is actually recorded, and this is the guard for when
    one is not.

    Scoped per chain because that message was wrong about exactly one of
    the two: the 120 Shufersal items really were still in the cart.
    """
    at = manifest_at(storage, store)[:10]
    last = last_shop(storage, store)
    if not at or not last:
        return False
    return at < last


def cart_contents(storage) -> list[tuple[str, int]]:
    """What we last put in each cart: (store key, item count).

    Read from the manifest rather than from the live carts on purpose —
    the nudge is composed by a CLI with no browser, no store session and
    no Israeli exit, and making a reminder depend on all three would
    mean no reminder whenever any of them is down.

    A chain whose manifest predates its own last shop is left out — see
    `manifest_is_stale`. Describing nothing is recoverable; describing a
    cart that was emptied at checkout is not.
    """
    stores = _manifest(storage).get("stores", {})
    return [
        (store, len(items))
        for store, items in stores.items()
        if items and not manifest_is_stale(storage, store)
    ]


def mark_shopped(storage, today: date | None = None, store: str = "") -> str:
    """Record that a shop finished, which is what triggers a refill.

    `store` names the chain when it is known — pass it whenever the
    caller has it. The global date is still written either way, because
    every existing reader depends on it and because the household having
    shopped *somewhere* is the fact the nudge needs.
    """
    day = (today or date.today()).isoformat()
    # Freeze what was in this chain's cart *before* the refill overwrites
    # it. This is the step that makes removals recoverable at all.
    #
    # The order of events is the problem: the household pays, `/done`
    # fires, the refill immediately rewrites the manifest — and the order
    # only appears in the chain's own history later (Tiv Taam the same
    # day, Shufersal ~36 hours). By then the manifest describes the *new*
    # cart, so comparing it against the order answers a question nobody
    # asked. The snapshot is the cart as it stood at checkout, kept until
    # an order turns up to compare it with.
    if store:
        _snapshot_shopped_cart(storage, store, day)
    storage.set_state(_LAST_SHOP_KEY, day)
    if store:
        by_store = _last_shop_by_store(storage)
        by_store[store] = day
        storage.set_state(_LAST_SHOP_BY_STORE_KEY, json.dumps(by_store, ensure_ascii=False))
    return day


def _last_shop_by_store(storage) -> dict:
    raw = storage.get_state(_LAST_SHOP_BY_STORE_KEY)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def last_shop(storage, store: str = "") -> str:
    """The last shop date — overall, or at one chain when `store` is given.

    A chain with no shop of its own on record returns "" rather than
    falling back to the global date: "we do not know" and "they shopped
    here" are different answers, and only one of them justifies telling
    the household their cart was emptied.
    """
    if store:
        return str(_last_shop_by_store(storage).get(store) or "")
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
    from .htmltext import escape as md

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
