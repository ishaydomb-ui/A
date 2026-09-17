"""Runs one order cycle: base list + pending ad-hoc requests -> real cart(s).

No approval gate by design (per project goals): every item that resolves to
a single clear match gets added straight away. Only genuine ambiguity
(multiple plausible matches) is surfaced back to the user, and only for
that one item — never as a blanket "confirm this whole cart" step.
"""
from __future__ import annotations

import json

import logging
import time
from datetime import datetime, timezone

from . import breaker
from typing import Callable

from . import dealfill
from .adapters.base import StoreAdapter
from .disambiguate import resolve
from .models import CartAddResult, OrderCycleReport
from .storage import Storage

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[], StoreAdapter]


def run_order_cycle(
    storage: Storage,
    adapter_factories: dict[str, AdapterFactory],
    on_progress=None,
    add_deals: bool = True,
    proxy: str | None = None,
) -> dict[str, OrderCycleReport]:
    """Run the cycle against every enabled store.

    Returns one OrderCycleReport per store. Ambiguous results are also
    persisted via storage.save_pending_ambiguity so the bot can present
    them as follow-up questions after this function returns.

    With `add_deals`, the cycle also puts exceptional promotions on
    things the household already buys straight into the cart, rather than
    only reporting them — see dealfill.py for why, and for the guards
    that keep it from becoming waste.

    `on_progress(done, total, result)` is called after each item, so a
    caller can show progress. A full cycle is minutes of page loads, and
    without this the user watches an idle chat and cannot tell a slow run
    from a stuck one. It is called from this worker thread, so a caller
    on an event loop must marshal back to it; anything it raises is
    swallowed rather than killing a shopping run over a UI update.
    """
    def _progress(done: int, total: int, result) -> None:
        if on_progress is None:
            return
        try:
            on_progress(done, total, result)
        except Exception:
            logger.exception("Progress callback failed; continuing the cycle")

    from .models import PlanTerm

    started = time.monotonic()
    base_items = storage.list_active_base_items()
    adhoc_items = storage.list_pending_adhoc()

    # The run and its needs, registered before any store is touched. Base
    # and ad-hoc identities are store-independent by construction; a deal
    # pick is chain-specific and keyed on its term.
    breakers: dict = {}
    run_id = storage.start_cart_run("cycle")
    item_ids = storage.add_run_items(run_id, [
        *[PlanTerm(b.name, b.default_quantity, "base", str(b.id)) for b in base_items],
        *[PlanTerm(a.text, a.quantity, "adhoc", str(a.id)) for a in adhoc_items],
    ])

    reports: dict[str, OrderCycleReport] = {}
    # An ad-hoc request is only used up once some store actually managed
    # something with it. A transient failure (network blip, changed
    # markup) must not silently delete a request nobody will think to
    # re-send.
    resolved_adhoc: set[int] = set()

    for store, make_adapter in adapter_factories.items():
        report = OrderCycleReport(store=store)
        with make_adapter() as adapter:
            # Renew an expired session before spending a whole cycle on it.
            # Without this every item fails with a redirect-to-login that
            # looks like broken selectors, and the user gets asked to log in
            # again -- exactly the manual dependency the project rules out.
            ensure = getattr(adapter, "ensure_session", None)
            if ensure is not None and not ensure():
                report.record(
                    CartAddResult(
                        item_name="(session)",
                        store=store,
                        status="error",
                        detail="Session expired and could not be renewed automatically.",
                    )
                )
                _remember_failures(storage, report)
                reports[store] = report
                continue

            asked_terms = [b.search_term_for(store) for b in base_items] + [
                a.text for a in adhoc_items
            ]
            # Deals are chosen before the run so they can be prematched in
            # the same bulk call as everything else; a second matcher pass
            # would cost another ~40s on a cycle that already takes that.
            deal_picks = (
                dealfill.picks_for(storage, store, skip_terms=asked_terms)
                if add_deals
                else []
            )
            prematched = _prefetch_matches(
                adapter, storage, store, asked_terms + [p.term for p in deal_picks]
            )
            # Read the cart once, before touching it. See CartGuard: the
            # household edits this cart on the site, and until now the bot
            # only ever looked afterwards.
            guard = CartGuard.read(storage, adapter, store)
            brk = breaker.Breaker(store, proxy, run_id)
            breakers[store] = brk
            total_items = len(base_items) + len(adhoc_items) + len(deal_picks)
            done = 0

            for base_item in base_items:
                term = base_item.search_term_for(store)
                result, alive = _attempt(
                    brk, adapter,
                    lambda term=term, q=base_item.default_quantity: _add_one(
                        storage, adapter, store, term, q, prematched, guard,
                    ),
                )
                # Carry the weight through so the cart view can say "0.5 ק"ג"
                # rather than a meaningless "×1" for loose produce.
                if getattr(base_item, "amount", None):
                    result.amount = base_item.amount
                    result.unit = base_item.unit
                report.record(result)
                _record_attempt(storage, run_id, item_ids.get(("base", str(base_item.id))), store, result)
                done += 1
                _progress(done, total_items, result)
                if not alive:
                    break

            for adhoc in (adhoc_items if not brk.halted else []):
                result, alive = _attempt(
                    brk, adapter,
                    lambda a=adhoc: _add_one(
                        storage, adapter, store, a.text, a.quantity, prematched, guard,
                    ),
                )
                result.requested_by = adhoc.requested_by
                report.record(result)
                _record_attempt(storage, run_id, item_ids.get(("adhoc", str(adhoc.id))), store, result)
                if not alive:
                    done += 1
                    _progress(done, total_items, result)
                    break
                # Only a request that actually reached the cart is done
                # with. "not_found" used to count as resolved, which meant
                # the household was told "נוסף לרשימה", the cycle quietly
                # failed to find it, and the request vanished from both the
                # list and the cart with nobody told. A request that was not
                # bought stays pending so the next cycle tries again.
                # "ambiguous" is kept because a question was actually put to
                # the user, and answering it is what consumes the request.
                if result.status in ("added", "ambiguous"):
                    resolved_adhoc.add(adhoc.id)
                    # Which of the two it was is the whole point: "in a
                    # cart" and "waiting on a choice" are different
                    # answers to "did you get the tahini", and `consumed`
                    # said the same thing for both.
                    storage.set_adhoc_status(
                        adhoc.id,
                        "in_cart" if result.status == "added" else "awaiting",
                        store,
                    )
                done += 1
                _progress(done, total_items, result)

            for pick in (deal_picks if not brk.halted else []):
                result, alive = _attempt(
                    brk, adapter,
                    lambda p=pick: _add_one(
                        storage, adapter, store, p.term, p.quantity, prematched, guard,
                    ),
                )
                if not alive:
                    report.record(result)
                    break
                # Only a line that actually made it into the cart is worth
                # calling a deal. An ambiguous or missing one would put a
                # saving in the summary that is not in the cart — exactly
                # the "looks right, isn't" shape this project keeps hitting.
                if result.status == "added":
                    result.deal = pick.label
                report.record(result)
                # Deals are registered lazily: they are chosen per store,
                # inside the loop, so they cannot be listed up front.
                deal_ids = storage.add_run_items(run_id, [PlanTerm(pick.term, pick.quantity, "deal", None)])
                _record_attempt(storage, run_id, next(iter(deal_ids.values()), None), store, result)
                done += 1
                _progress(done, total_items, result)

        _remember_failures(storage, report)
        reports[store] = report

    for adhoc_id in resolved_adhoc:
        storage.mark_adhoc_consumed(adhoc_id)

    # Keep what the cycle chose on its own. The summary message is
    # otherwise the only record of it, and on 2026-09-07 that message
    # failed to send on a real order — leaving no way at all to answer
    # "what did it add, and why". A record on disk survives a bad send.
    record_deals(storage, reports)
    _finish_run(storage, run_id)
    _log_run("cycle", started, reports, run_id, breakers)
    return reports


def record_deals(storage: Storage, reports: dict[str, OrderCycleReport]) -> None:
    import json
    from datetime import datetime, timezone

    picked = [
        {"store": store, "name": r.item_name, "deal": r.deal}
        for store, report in reports.items()
        for r in report.added
        if getattr(r, "deal", "")
    ]
    if not picked:
        return
    try:
        storage.set_state(
            "last_deal_picks",
            json.dumps(
                {"at": datetime.now(timezone.utc).isoformat(), "picks": picked},
                ensure_ascii=False,
            ),
        )
    except Exception:  # noqa: BLE001 - a bookkeeping failure must not sink a shop
        logger.exception("Could not record this cycle's deal picks")


class CartGuard:
    """What a person did to this cart, and what that forbids us to do.

    The cart is shared. Ishay fills it through the bot; his partner edits
    it on the site, with no Telegram in the loop at all. Until now the
    bot read the cart only *after* filling it, so two things could happen
    silently: a line she had deleted came straight back on the next add,
    and an item already in the cart was added a second time.

    Neither is "learning from removals", which Ishay ruled out and which
    this deliberately does not do: nothing here changes a preference, a
    quantity or a list. It only declines to undo, within one round, an
    edit a person made to the cart it is about to touch.

    **An empty cart blocks nothing.** After a shop the chain empties the
    cart, so every manifest line reads as "removed" — which would stop
    the refill from putting anything back and leave the household with an
    empty cart and no explanation. Same trap as `standingcart.removals`,
    same answer.

    Unreadable is not empty: when the cart cannot be read the guard
    allows everything, because a page-load failure must not silently stop
    a shop from being filled.
    """

    def __init__(self, present: dict, removed: dict, readable: bool = True):
        self.present = present          # key -> name, already in the cart
        self.removed = removed          # key -> name, taken out by a person
        self.readable = readable

    @classmethod
    def empty(cls) -> "CartGuard":
        return cls({}, {}, readable=False)

    @classmethod
    def from_last_known(cls, storage: Storage, store: str) -> "CartGuard":
        """The guard to use when the cart cannot be read: last known evidence.

        Phase 2 (2026-09-17). `empty()` — "unreadable allows everything" —
        was the right call against *stopping* a fill, and the wrong call
        against *duplicating* one: on 2026-09-17 a re-run after an
        unverified add put `עגבניות שרי במלח` in the cart twice, because
        the cart read had failed and the guard therefore knew nothing.

        So an unreadable cart is guarded by what this bot last put in it
        (`standing_cart_manifest`), which is the best evidence available
        without a page. Items *we* added are treated as present; anything
        else proceeds. The cost, stated: an item the household deleted
        since the last fill will not be re-added on this pass — the
        conservative direction, and one pass only.
        """
        from . import standingcart

        present: dict = {}
        try:
            rows = standingcart._manifest(storage).get("stores", {}).get(store) or []  # noqa: SLF001
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            for key in (str(row.get("code") or ""), (row.get("name") or "").strip()):
                if key:
                    present[key] = (row.get("name") or "").strip()
        return cls(present, {}, readable=False)

    @classmethod
    def read(cls, storage: Storage, adapter, store: str) -> "CartGuard":
        from . import standingcart

        reader = getattr(adapter, "cart_summary", None)
        if reader is None:
            return cls.from_last_known(storage, store)
        try:
            summary = reader() or {}
        except Exception:  # noqa: BLE001
            logger.exception("Could not read the %s cart before filling it", store)
            return cls.from_last_known(storage, store)
        if not summary.get("ok"):
            return cls.from_last_known(storage, store)
        items = summary.get("items") or []
        present = {}
        for item in items:
            for key in (str(item.get("code") or ""), (item.get("name") or "").strip()):
                if key:
                    present[key] = (item.get("name") or "").strip()
        if not items:
            # Empty: a completed shop, not a hundred deletions.
            return cls(present={}, removed={})
        removed = {}
        for row in standingcart.removals(storage, store, items):
            for key in (str(row.get("code") or ""), (row.get("name") or "").strip()):
                if key:
                    removed[key] = (row.get("name") or "").strip()
        return cls(present, removed)

    def blocks(self, code: str = "", name: str = "") -> str:
        """Why this must not be added now, or "" when it may be."""
        for key in (str(code or ""), (name or "").strip()):
            if not key:
                continue
            if key in self.removed:
                return "הוסר ידנית מהעגלה"
            if key in self.present:
                return "כבר בעגלה"
        return ""


def _log_run(kind: str, started: float, reports: dict, run_id: int | None = None,
             breakers: dict | None = None) -> None:
    """One journal line per cart run, per store.

    Phase 0 instrumentation (2026-09-17). Before this the journal held no
    run-level record at all: how many items were requested, how many
    landed, how long it took. The verified/unverified split and the run
    id arrive with Phases 1–2 and are added here then; this line is the
    baseline they are measured against.
    """
    elapsed = time.monotonic() - started
    for store, report in (reports or {}).items():
        requested = (
            len(report.added) + len(report.ambiguous) + len(report.not_found)
            + len(report.errors) + len(getattr(report, "skipped", []) or [])
        )
        infra = sum(
            1 for r in list(report.errors) + list(report.not_found)
            if any(m.lower() in ((getattr(r, "detail", "") or "").lower())
                   for m in INFRASTRUCTURE_MARKERS)
        )
        brk = (breakers or {}).get(store)
        verified = sum(1 for r in report.added if getattr(r, "verification", "") == "verified")
        logger.info(
            "RUN run=%s kind=%s store=%s requested=%d added=%d verified=%d unverified=%d "
            "ambiguous=%d not_found=%d errors=%d skipped=%d infra=%d recoveries=%d "
            "halted=%s elapsed_s=%.0f",
            run_id if run_id is not None else "-",
            kind, store, requested, len(report.added), verified, len(report.added) - verified,
            len(report.ambiguous), len(report.not_found), len(report.errors),
            len(getattr(report, "skipped", []) or []), infra,
            getattr(brk, "recoveries", 0), getattr(brk, "halted", False), elapsed,
        )


# Moved to breaker.py in Phase 4 so the fill loop and the recorder share
# one classification. Note what is *not* in it any more: "Timeout" and
# "Session expired". A timeout is ambiguous (breaker.AMBIGUOUS_MARKERS)
# and a session failure is its own class; neither is infrastructure.
from .breaker import INFRASTRUCTURE_MARKERS, SESSION_MARKERS  # noqa: E402


def _remember_failures(storage: Storage, report) -> None:
    """Persist what this run could not add, so a pattern can be seen.

    Both cycles already retried failures — an ad-hoc request stays
    pending until it is bought, and the standing list is rebuilt each
    run so a failed term is simply on it again. What neither did was
    *remember*, so an item failing on every single run was
    indistinguishable from one failing for the first time, and the only
    trace was a "לא נמצא (N)" count in a message that scrolled away.

    Never fatal: a cycle that filled a cart must not be reported as
    failed because a bookkeeping insert did not land.
    """
    # A dropped connection is not a fact about a product. On 2026-09-17
    # the exit node failed mid-run and 14 items were written to
    # `cart_failures` with ERR_SOCKS_CONNECTION_FAILED, where they read
    # exactly like fourteen products the shop does not stock — and
    # `failstrategy` would then propose shortening search terms that were
    # never the problem. The network's state belongs in the log, not in
    # the per-item history.
    infra = [m.lower() for m in INFRASTRUCTURE_MARKERS] + list(SESSION_MARKERS)
    def _is_infrastructure(result) -> bool:
        kind = (getattr(result, "failure_kind", "") or "")
        if kind in ("infrastructure", "session"):
            return True
        detail = (getattr(result, "detail", "") or "").lower()
        return any(mark in detail for mark in infra)

    recordable = [
        r for r in list(report.not_found) + list(report.errors)
        if not _is_infrastructure(r)
    ]
    skipped = (len(report.not_found) + len(report.errors)) - len(recordable)
    if skipped:
        logger.warning(
            "%d failure(s) were connectivity, not products — not recorded "
            "against the items", skipped,
        )
    try:
        storage.record_cart_failures(recordable)
    except Exception:  # noqa: BLE001
        logger.exception("Could not record cart failures; the cycle itself is unaffected")


def _outcome_for(result) -> tuple[str, str, str]:
    """(outcome, failure_kind, evidence) for a run item, from one add result.

    Phase 1 vocabulary. `added` stays `added` until Phase 2 splits it into
    verified/unverified on real evidence — recording it as verified here
    would be the exact lie this build exists to remove.
    """
    status = getattr(result, "status", "") or ""
    detail = (getattr(result, "detail", "") or "")[:160]
    low = detail.lower()
    if status == "added":
        # Phase 2: "added" is a claim; the evidence decides the outcome.
        # An adapter that did not verify — or a fake that never said —
        # yields `unverified`, never `verified`. This is the rule that
        # stops "the click did not throw" from meaning "it is in the cart".
        if getattr(result, "verification", "n/a") == "verified":
            return "verified", "", detail
        return "unverified", "", detail or "add sent, no positive evidence"
    if status == "ambiguous":
        return "unresolved_ambiguity", "ambiguous", f"{len(getattr(result, 'candidates', []) or [])} candidates"
    if status == "skipped":
        return "skipped", "", detail
    if status == "not_found":
        return "failed_product", "product", detail
    # error — the adapter's own classification wins when it gave one.
    kind = getattr(result, "failure_kind", "") or ""
    if kind == "session" or "session expired" in low or "browser has been closed" in low:
        return "failed_session", "session", detail
    if kind == "infrastructure" or any(m.lower() in low for m in INFRASTRUCTURE_MARKERS):
        return "failed_infra", "infrastructure", detail
    if kind == "ambiguous":
        # e.g. "the click did not change the cart" — could be lag, could
        # be a dead button. Not a fact about the product either way.
        return "unverified", "ambiguous", detail
    return "failed_product", "product", detail


def _record_attempt(storage: Storage, run_id: int, item_id: int | None, store: str, result) -> None:
    """Write the item's current state and one journal line for the attempt.

    Attempt *history* lives in the journal, not in a table: one line per
    attempt carrying run, item, store, product, result, kind and evidence,
    timestamped by journald and greppable by run id. Inspected before
    deciding (Phase 1): that is every field an attempts table would hold,
    and the run item keeps the current authoritative state.
    """
    outcome, kind, evidence = _outcome_for(result)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger.info(
        "ATTEMPT run=%s item=%s store=%s term=%r status=%s outcome=%s kind=%s "
        "product=%s evidence=%r",
        run_id, item_id, store, (getattr(result, "item_name", "") or "")[:40],
        getattr(result, "status", ""), outcome, kind or "-",
        getattr(result, "product_code", "") or "-", evidence[:80],
    )
    if item_id is None:
        return
    try:
        storage.update_run_item(
            item_id, store=store, outcome=outcome, failure_kind=kind,
            evidence=evidence, attempted_at=now,
            product_code=getattr(result, "product_code", "") or "",
        )
    except Exception:  # noqa: BLE001 - bookkeeping must not kill a fill
        logger.exception("Could not update run item %s", item_id)


def _attempt(brk, adapter, fn):
    """Run one add through the breaker. Returns (result, keep_going).

    `fn` performs the add and returns a CartAddResult. If the breaker
    says `recover`, recovery runs and — on success — `fn` is called once
    more for the same item, so the item that tripped is not left as an
    infrastructure failure when the route came back. `keep_going=False`
    means this store is done for the run; the caller leaves the rest
    pending.
    """
    result = fn()
    action = brk.observe(result)
    if action == "continue":
        return result, True
    if action == "recover" and brk.recover(adapter):
        logger.info("BREAKER run=%s store=%s resuming at the item that tripped",
                    brk.run_id, brk.store)
        result = fn()
        # A second failure of the same kind straight after recovery is
        # not retried again here; the next observe() decides.
        action = brk.observe(result)
        if action == "continue":
            return result, True
        if action == "recover" and brk.recover(adapter):
            return fn(), True
    return result, False


def _finish_run(storage: Storage, run_id: int) -> str:
    """Close the run with the only status Phase 1 can justify.

    `completed` if every item reached a terminal outcome, `interrupted`
    if any is still pending. The unverified variant is Phase 2's.
    """
    counts = storage.run_counts(run_id)
    status = "interrupted" if counts.get("pending") else "completed"
    storage.finish_cart_run(run_id, status)
    return status


def add_terms_to_cart(
    storage: Storage,
    adapter_factories: dict[str, AdapterFactory],
    terms: list,
    on_progress=None,
    guard_cart: bool = False,
    run_id: int | None = None,
    trigger: str = "terms",
    proxy: str | None = None,
) -> dict[str, OrderCycleReport]:
    """Put specific items straight into the real cart.

    Distinct from a full cycle on purpose. "תעדכן את העגלה עם קוטג
    וגבינה" names the things to add; running the whole standing list
    would drop another dozen products into the cart the user never asked
    for in that message.

    `guard_cart` decides whether a person's edits to the cart veto an
    add, and it is **off by default on purpose**. This function serves
    two callers that mean opposite things. The standing refill fills the
    cart on its own and must not undo a deletion, so it passes True. A
    person typing "תוסיף חלב" is asking for milk *now* — if they deleted
    it an hour ago and are asking again, they changed their mind, and a
    bot that answered "it was removed" would be refusing an instruction
    by citing the instruction it was given earlier.
    """
    from .models import PlanTerm

    started = time.monotonic()
    # Legacy tuples become free-form needs; PlanTerms keep their source.
    plan_terms = [PlanTerm.coerce(t) for t in (terms or [])]
    owns_run = run_id is None
    if owns_run:
        run_id = storage.start_cart_run(trigger)
    item_ids = storage.add_run_items(run_id, plan_terms)

    def _item_id(pt: PlanTerm) -> int | None:
        from .storage import normalize_term
        sid = pt.source_id if pt.source_id is not None else normalize_term(pt.term)
        return item_ids.get((pt.source_kind, str(sid)))

    reports: dict[str, OrderCycleReport] = {}
    breakers: dict = {}
    for store, make_adapter in adapter_factories.items():
        report = OrderCycleReport(store=store)
        with make_adapter() as adapter:
            ensure = getattr(adapter, "ensure_session", None)
            if ensure is not None and not ensure():
                report.record(
                    CartAddResult(
                        item_name="(session)",
                        store=store,
                        status="error",
                        detail="Session expired and could not be renewed automatically.",
                    )
                )
                _remember_failures(storage, report)
                reports[store] = report
                continue

            prematched = _prefetch_matches(adapter, storage, store, [t.term for t in plan_terms])
            guard = CartGuard.read(storage, adapter, store) if guard_cart else CartGuard.empty()
            brk = breaker.Breaker(store, proxy, run_id)
            breakers[store] = brk
            for index, pt in enumerate(plan_terms, start=1):
                result, alive = _attempt(
                    brk, adapter,
                    lambda pt=pt: _add_one(
                        storage, adapter, store, pt.term, int(pt.quantity or 1),
                        prematched, guard,
                    ),
                )
                report.record(result)
                _record_attempt(storage, run_id, _item_id(pt), store, result)
                if on_progress is not None:
                    try:
                        on_progress(index, len(plan_terms), result)
                    except Exception:
                        logger.exception("Progress callback failed; continuing")
                if not alive:
                    # The rest of this store's items stay `pending` on the
                    # run — not failed, not attempted. Phase 4's whole point.
                    logger.warning(
                        "BREAKER run=%s store=%s stopped after item %d/%d; %d left pending",
                        run_id, store, index, len(plan_terms), len(plan_terms) - index,
                    )
                    break
        _remember_failures(storage, report)
        reports[store] = report
    if owns_run:
        _finish_run(storage, run_id)
    _log_run("terms", started, reports, run_id, breakers)
    return reports


def _prefetch_matches(adapter, storage, store: str, terms: list[str]) -> dict:
    """Resolve every term up front with the store's bulk matcher.

    Turns a cycle from one page load per item into a single call: 89
    items took ~15 minutes of searching and now resolve in ~40 seconds.
    Terms already in product memory are skipped — a remembered code is a
    decision the household made, and is better than any matcher's guess.

    Returns {} on any failure, which simply restores the old per-item
    path; the caller must treat this as a speed-up, never a requirement.
    """
    matcher = getattr(adapter, "bulk_match", None)
    if matcher is None:
        return {}
    unknown = [t for t in terms if storage.preferred_for(store, t) is None]
    if not unknown:
        return {}
    logger.info("Bulk-matching %d unresolved terms", len(unknown))
    return matcher(unknown)


def _add_one(
    storage: Storage,
    adapter: StoreAdapter,
    store: str,
    term: str,
    quantity: int,
    prematched: dict | None = None,
    guard: "CartGuard | None" = None,
):
    """Add one term, honouring a previously remembered product choice.

    `guard` is what a person did to this cart since we last filled it.
    Every add is checked against it first, at whatever identity we have
    at that point: the remembered product, the bulk match, or — when the
    search has not run yet — the term itself.

    Without the memory lookup this bot is unusable in practice: a real
    Shufersal search for an everyday term returns ~20 tiles, so every
    single item comes back "ambiguous" and the user is asked a dozen
    questions per cycle. Remembering the choice turns that into a
    one-time question per product, which is what the project's "focused
    decision point, only on genuine ambiguity" rule actually asks for.
    """
    guard = guard or CartGuard.empty()
    blocked = guard.blocks(name=term)
    if blocked:
        return CartAddResult(
            item_name=term, store=store, status="skipped",
            detail=blocked, quantity=quantity,
        )

    preferred = storage.preferred_for(store, term)
    if preferred is not None:
        blocked = guard.blocks(preferred["product_code"], preferred["product_name"])
        if blocked:
            return CartAddResult(
                item_name=term, store=store, status="skipped",
                detail=blocked, quantity=quantity,
            )
        result = adapter.add_specific_product(
            preferred["product_name"],
            quantity,
            product_code=preferred["product_code"],
            search_term=term,
        )
        # A remembered product can be delisted or renamed; fall back to a
        # fresh search rather than reporting a spurious failure. The
        # memory is deliberately *kept*: one failed lookup usually means
        # the store's search didn't surface it this time, not that the
        # household changed its mind, and deleting on the first miss
        # silently threw away choices that took a real conversation to
        # establish.
        if result.status != "not_found":
            return result
        logger.info(
            "Shufersal: remembered product for %r not found this run; keeping the memory", term
        )

    # A bulk-matched hit skips the search entirely: we already have the
    # product code, so this is a direct add rather than a page load.
    hit = (prematched or {}).get(term)
    if hit and hit.get("code"):
        blocked = guard.blocks(hit["code"], hit.get("name") or "")
        if blocked:
            return CartAddResult(
                item_name=term, store=store, status="skipped",
                detail=blocked, quantity=quantity,
            )
        if not hit.get("in_stock", True):
            logger.info("Skipping %r — matched product is out of stock", term)
            return CartAddResult(
                item_name=term, store=store, status="not_found",
                detail="אזל מהמלאי", quantity=quantity,
            )
        result = adapter.add_specific_product(
            hit.get("name") or term, quantity,
            product_code=hit["code"], search_term=term,
        )
        if result.status == "added":
            storage.remember_choice(
                store=store, term=term,
                product_code=hit["code"], product_name=hit.get("name") or term,
            )
            result.auto_resolved = "bulk_match"
            return result

    result = adapter.search_and_add(term, quantity)
    if result.status == "added":
        # Remember a clean resolution too, not only a bulk-match, an
        # auto-resolve or an answered question. Those three were wired up
        # and this one was not, so a term that resolved to exactly one
        # product went back through the store's search every single cycle.
        #
        # At Shufersal that is a wasted page load. At Tiv Taam it is a
        # correctness problem: its search is an autocomplete dropdown that
        # returned 4, then 0, then 5 candidates for the same query within
        # one afternoon (2026-09-02), so re-searching a settled product
        # rolls the dice again — and a 0 reads as "not found" for
        # something the household buys every week.
        #
        # Guarded on a resolved identity: an adapter that echoes the
        # search term back would otherwise teach the memory that "חלב"
        # means a product called "חלב", which resolves to nothing.
        #
        # **Known risk, accepted deliberately.** "Resolved cleanly" means
        # the search returned exactly one candidate — and at Tiv Taam the
        # candidate list is itself unstable: "קוטג" returned 4, then 0,
        # then 5, then 1 across one afternoon. So a single candidate can
        # be an artefact of a half-loaded dropdown rather than a real
        # unique match, and this will occasionally remember the wrong
        # product. It is still the better trade: the alternative re-rolls
        # that same dice every cycle *and* keeps asking. A wrong memory is
        # corrected by the household answering the question once — the
        # ambiguity flow overwrites it (see telegram_bot's chooser) — and
        # `storage.forget_choice` exists for the same purpose.
        # Reconsider if it turns out to bite: the honest fix is resolving
        # names against our own catalog instead of the live dropdown.
        if result.product_code or (result.item_name and result.item_name != term):
            storage.remember_choice(
                store=store,
                term=term,
                product_code=result.product_code,
                product_name=result.item_name or term,
            )
        return result
    if result.status != "ambiguous":
        return result

    # Before asking, check whether the answer is already known. Most
    # "ambiguity" is a search returning twenty tiles, of which exactly one
    # is a product this household has bought for years.
    cards = getattr(result, "candidate_cards", None)
    if cards:
        known = _known_products(storage, store)
        decision = resolve(term, cards, known["names"], known["codes"])
        if decision.resolved:
            chosen = decision.card
            picked = adapter.add_specific_product(
                chosen.get("name", term),
                quantity,
                product_code=chosen.get("code", ""),
                search_term=term,
            )
            if picked.status == "added":
                storage.remember_choice(
                    store=store,
                    term=term,
                    product_code=chosen.get("code", ""),
                    product_name=chosen.get("name", term),
                )
                picked.auto_resolved = decision.reason
                return picked

    # No cards, only names — which is every Tiv Taam term, because that
    # adapter's search returns a name list and nothing else. The branch
    # above could therefore never fire there, so **every** ambiguous Tiv
    # Taam term became a question by construction. That is where the 108
    # Tiv Taam questions of 2026-09-17 came from, and why resolving the
    # backlog did not stop new ones arriving.
    #
    # The purchase history answers most of them: 377 products with the
    # share of orders each appears in, readable only since 09-15.
    if not cards and result.candidates:
        from . import autoresolve

        row = {
            "id": 0,
            "store": store,
            "original_term": term,
            "candidates": json.dumps(list(result.candidates), ensure_ascii=False),
            "candidate_cards": "[]",
        }
        try:
            decision = autoresolve.decide(storage, row)
        except Exception:  # noqa: BLE001
            logger.exception("Could not auto-decide %r", term)
            decision = None
        if decision is not None and decision.index >= 0 and decision.name:
            # Pass the code only when there is one. A name-only chain
            # has none, and an adapter that never sees codes need not
            # accept the argument.
            extra = {"search_term": term}
            if decision.code:
                extra["product_code"] = decision.code
            try:
                picked = adapter.add_specific_product(
                    decision.name, quantity, **extra
                )
            except TypeError:
                picked = adapter.add_specific_product(decision.name, quantity)
            if picked.status == "added":
                storage.remember_choice(
                    store=store, term=term,
                    product_code=decision.code or "",
                    product_name=decision.name,
                )
                picked.auto_resolved = f"{decision.basis}: {decision.detail}"
                return picked

    storage.save_pending_ambiguity(
        store=store,
        original_term=term,
        quantity=result.quantity,
        candidates=result.candidates,
        candidate_cards=cards or [],
    )
    return result


def _known_products(storage: Storage, store: str) -> dict:
    """Every product this household has actually bought, by name and code."""
    preferences = storage.list_preferences(store)
    return {
        "names": {p["product_name"] for p in preferences if p.get("product_name")},
        "codes": {p["product_code"] for p in preferences if p.get("product_code")},
    }


def format_repeat_failures(storage: Storage, min_runs: int = 3) -> str:
    """The line that turns recurring noise into something actionable.

    "לא נמצא (4)" in a single report is normal — a store's search misses
    things. The same four items missing for five runs running is not
    noise, it is a delisted product or a search term that never matched,
    and only one of those is worth the household's attention.

    Deliberately not shown below `min_runs`: a first or second miss is
    usually the store, and reporting it would train everyone to ignore
    this line, which is how the old "לא נמצא" count came to mean nothing.
    """
    try:
        rows = storage.repeat_failures(min_runs=min_runs)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read repeat failures")
        return ""
    if not rows:
        return ""
    lines = [f"🔁 נכשלים שוב ושוב ({len(rows)}) — כנראה ירדו מהמדף או שהמונח לא מדויק:"]
    for row in rows[:10]:
        since = (row["first_failed"] or "")[:10]
        lines.append(
            f"   • {row['item_name']} ({row['store']}) — {row['runs']} מחזורים, מאז {since}"
        )
    if len(rows) > 10:
        lines.append(f"   ...ועוד {len(rows) - 10}")
    lines.append("   <i>אפשר לתקן את המונח, או להוריד מהרשימה.</i>")
    return "\n".join(lines)


def format_multi_buy_note(storage: Storage, reports) -> str:
    """The "worth taking two" block for the chains this cycle filled.

    Separate from the deal block above it because nothing here is in the
    cart: these are promotions that only pay out on a second unit, which
    the bot refuses to buy on the household's behalf (see
    `dealfill.multi_buy_offers`). Reported so the choice exists, and
    reported *with the promotion's own wording* because the two chains
    mean different things by the same field.
    """
    from . import dealfill

    blocks = []
    stores = reports if isinstance(reports, dict) else {s: None for s in (reports or [])}
    for store, report in stores.items():
        # A deal the cycle actually took at the right quantity is not also
        # a deal it declined to take.
        taken = {r.item_name for r in getattr(report, "added", [])} if report else set()
        try:
            offers = dealfill.multi_buy_offers(storage, store, skip=taken)
        except Exception:  # noqa: BLE001
            logger.exception("Could not read multi-buy offers for %s", store)
            continue
        text = dealfill.format_multi_buy_offers(offers)
        if text:
            from .chains import display_name
            blocks.append(f"{display_name(store)}\n{text}")
    return "\n\n".join(blocks)


def format_report_headline(storage: Storage, reports: dict[str, OrderCycleReport]) -> str:
    """The end of a cycle as a decision screen, not a transcript.

    The completion message had grown to nine blocks — everything the run
    did, everything it chose, everything it noticed — and the UX audit's
    point stands: the household needs three facts immediately. Is the
    cart ready, what is missing, and what still needs a decision.

    So counts and money go on one line each, and the **full lists move
    behind a button**. What does *not* move behind a button is anything
    that would be a silent gap: a product that was not found, a line the
    bot declined to put back, an error. Those are the cases where a short
    message would send someone to a cart that is quietly missing the
    thing they asked for.

    A sum is only printed when it is actually known: the saving is summed
    from the deal lines that really went in, and when none did, the line
    is absent rather than showing 0.00₪.
    """
    from .chains import display_name
    from .htmltext import bold as _b, escape as _md

    repeats = {}
    try:
        for row in storage.repeat_failures(min_runs=3):
            repeats[(row["store"], row["item_name"])] = row["runs"]
    except Exception:  # noqa: BLE001
        logger.exception("Could not read repeat failures for the headline")

    lines: list[str] = []
    for store, report in reports.items():
        lines.append(_b(display_name(store)))
        deals = [r for r in report.added if getattr(r, "deal", "")]
        counts = [f"✅ {len(report.added)} נוספו"]
        if report.ambiguous:
            counts.append(f"❓ {len(report.ambiguous)} בחירות")
        if report.not_found:
            counts.append(f"⚠️ {len(report.not_found)} לא נמצאו")
        lines.append(" · ".join(counts))

        saved = _deal_saving(deals)
        if deals:
            note = f"🏷️ {len(deals)} מבצעים"
            # Only when every line carries a readable figure. A partial
            # sum presented as the total is the failure this project
            # keeps meeting.
            if saved is not None:
                note += f" · חיסכון {saved:.2f}₪"
            lines.append(note)

        # Everything below is a gap. None of it goes behind a button.
        if report.not_found:
            named = []
            for r in report.not_found:
                runs = repeats.get((store, r.item_name))
                named.append(
                    f"{_md(r.item_name)} <i>({runs} מחזורים)</i>" if runs
                    else _md(r.item_name)
                )
            lines.append(
                f"⚠️ לא נמצא: " + ", ".join(named)
                + "\n   <i>נשאר ברשימה — אנסה שוב בפעם הבאה.</i>"
            )
        removed = [r for r in report.skipped if "הוסר" in (r.detail or "")]
        if removed:
            lines.append(
                f"✋ לא הוחזרו — הוסרו מהעגלה ידנית: "
                + ", ".join(_md(r.item_name) for r in removed)
            )
        if report.errors:
            lines.append(
                f"🛑 שגיאה ({len(report.errors)}): "
                + ", ".join(_md(r.item_name) for r in report.errors)
            )
        lines.append("")
    return "\n".join(lines).strip()


def _deal_saving(deals) -> float | None:
    """Total saving across deal lines, or None when any figure is missing."""
    total = 0.0
    for result in deals:
        text = getattr(result, "deal", "") or ""
        marker = "חיסכון "
        start = text.find(marker)
        if start < 0:
            return None
        try:
            total += float(text[start + len(marker):].split("₪")[0].strip())
        except ValueError:
            return None
    return total if deals else None


def format_report_summary(reports: dict[str, OrderCycleReport]) -> str:
    """Human-readable (Hebrew) summary suitable for a Telegram message.

    Every product name is escaped. 349 of this branch's products carry a
    `*` as a multiplication sign ("400*3ג"), Telegram reads it as an
    unclosed bold entity and rejects the WHOLE message — which is exactly
    what happened to a real order's summary on 2026-09-07: the carts were
    filled at both chains and the household was told nothing at all,
    because this function interpolated raw names and the caller sent it
    with parse_mode="Markdown" and no fallback.
    """
    from .chains import display_name
    from .htmltext import bold as _b, escape as _md

    lines: list[str] = []
    for store, report in reports.items():
        # The chain name, in Hebrew, not the internal key. Two chains are
        # cart-capable now (Shufersal and Tiv Taam) and a cycle fills both,
        # so "תוסיף לסל" is never about one cart — and a deal spotted at
        # one chain would be filled at the other's price, unless the reply
        # says where each item went.
        lines.append(_b(display_name(store)))
        if report.added:
            asked = [r for r in report.added if not getattr(r, "deal", "")]
            if asked:
                lines.append(
                    f"✅ נוספו ({len(asked)}): "
                    + ", ".join(_md(r.item_name) for r in asked)
                )
            # An automatic pick must be visible: it replaced a question the
            # user would otherwise have answered, so they need to be able to
            # spot a wrong one.
            auto = [r for r in report.added if getattr(r, "auto_resolved", "")]
            if auto:
                lines.append(
                    f"   <i>נבחרו לפי הרגלי הקנייה שלכם ({len(auto)}): </i>"
                    + ", ".join(_md(r.item_name) for r in auto)
                )
            # Its own block, not mixed into the list above: these are the
            # lines nobody asked for, so they are the ones most likely to
            # be deleted and have to be findable at a glance.
            dealt = [r for r in report.added if getattr(r, "deal", "")]
            if dealt:
                lines.append(f"🏷️ נוספו בגלל מבצע חריג ({len(dealt)}) — מחקו מה שלא צריך:")
                for r in dealt:
                    lines.append(f"   • {_md(r.item_name)} — <i>{_md(r.deal)}</i>")
        if report.skipped:
            # Said out loud, never silently. "The cart is missing the milk
            # I asked for" with no explanation is worse than one extra
            # line, and this is the only place the reason exists.
            removed = [r for r in report.skipped if "הוסר" in (r.detail or "")]
            already = [r for r in report.skipped if r not in removed]
            if removed:
                lines.append(
                    f"✋ לא הוחזרו ({len(removed)}) — הוסרו מהעגלה ידנית: "
                    + ", ".join(_md(r.item_name) for r in removed)
                )
            if already:
                lines.append(
                    f"↩️ כבר בעגלה ({len(already)}): "
                    + ", ".join(_md(r.item_name) for r in already)
                )
        if report.ambiguous:
            lines.append(
                f"❓ דורש בחירה ({len(report.ambiguous)}): "
                + ", ".join(_md(r.item_name) for r in report.ambiguous)
            )
        if report.not_found:
            # Say it stays on the list. Otherwise a long report reads as
            # "these are gone", and the household has no way to know the
            # request is still queued for the next cycle.
            lines.append(
                f"⚠️ לא נמצא ({len(report.not_found)}): "
                + ", ".join(_md(r.item_name) for r in report.not_found)
                + "\n   <i>נשאר ברשימה — אנסה שוב בפעם הבאה.</i>"
            )
        if report.errors:
            lines.append(
                f"🛑 שגיאה ({len(report.errors)}): "
                + ", ".join(_md(r.item_name) for r in report.errors)
            )
        lines.append("")
    return "\n".join(lines).strip()
