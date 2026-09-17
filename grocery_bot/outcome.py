"""The terminal outcome of a cart run, and the one message that reports it.

Phase 11 of the reliability build (2026-09-17). A run ends in exactly
one of four states, decided from `run_items` — never from the report
buckets, which hold what the adapters *said*, and never from prose:

- `completed`                  every item verified (or already there)
- `completed_with_unverified`  nothing failed, but some adds were sent
                               without positive evidence they landed
- `completed_with_exceptions`  something was not found, could not be
                               added, or is waiting on a choice
- `aborted`                    items were left untouched — the run was
                               halted or the process died; resume picks
                               it up under the same id

The household gets one message per run: the state, the counts, and
every gap named. What it does not get is the cause narration — "the
exit node flapped, the breaker probed twice, recovered to Uset-PC" is
journal material. An item that did not land is listed as not added;
the reason lives in the journal and in /failures.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STATES = ("completed", "completed_with_unverified", "completed_with_exceptions", "aborted")

FAILURE_OUTCOMES = frozenset({"failed_product", "failed_session", "failed_infra",
                              "unresolved_ambiguity"})
# Outcomes that mean "nothing more to do for this item".
SETTLED_OUTCOMES = frozenset({"verified", "skipped"})


def classify(counts: dict, status: str = "") -> str:
    """The state a run's item counts earn. `status` is the lifecycle row."""
    if status == "aborted" or counts.get("pending"):
        return "aborted"
    if any(counts.get(k) for k in FAILURE_OUTCOMES):
        return "completed_with_exceptions"
    if counts.get("unverified"):
        return "completed_with_unverified"
    return "completed"


@dataclass
class RunOutcome:
    run_id: int
    state: str
    counts: dict
    # store -> bucket -> [term]. Bucket names are the run_items outcomes,
    # plus "not_added" which folds the session/infra failures together:
    # the household needs the item named, not the cause.
    gaps: dict = field(default_factory=dict)
    pending: list = field(default_factory=list)
    stores: list = field(default_factory=list)


_BUCKET = {
    "failed_product": "not_found",
    "failed_session": "not_added",
    "failed_infra": "not_added",
    "unresolved_ambiguity": "ambiguous",
    "unverified": "unverified",
}


def summarise(storage, run_id: int) -> RunOutcome:
    rows = storage.run_items_for(run_id)
    counts: dict = {"requested": len(rows)}
    gaps: dict = {}
    pending: list = []
    stores: list = []
    status = ""
    try:
        status = storage.cart_run_status(run_id)
    except Exception:  # noqa: BLE001
        status = ""
    for r in rows:
        outcome = r["outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
        store = r["store"] or ""
        if store and store not in stores:
            stores.append(store)
        if outcome == "pending":
            pending.append(r["term"])
            continue
        bucket = _BUCKET.get(outcome)
        if bucket:
            gaps.setdefault(store, {}).setdefault(bucket, []).append(r["term"])
        per_store = counts.setdefault("_by_store", {}).setdefault(store, {})
        per_store[outcome] = per_store.get(outcome, 0) + 1
    return RunOutcome(run_id, classify(counts, status), counts, gaps, pending, stores)


_STATE_LINE = {
    "completed": "✅ העגלה מוכנה",
    "completed_with_unverified": "✅ העגלה מוכנה — חלק לא אומת",
    "completed_with_exceptions": "⚠️ העגלה מוכנה עם הסתייגויות",
    "aborted": "🛑 הריצה נקטעה",
}

_GAP_LABEL = {
    "not_found": "⚠️ לא נמצא",
    "not_added": "🛑 לא נוסף",
    "ambiguous": "❓ ממתין לבחירה",
    "unverified": "🔎 לא אומת — בדקו בעגלה",
}


def format_outcome(out: RunOutcome) -> str:
    """One HTML message for one run: state, counts per store, gaps named."""
    from .chains import display_name
    from .htmltext import bold as _b, escape as _e

    lines = [_b(_STATE_LINE[out.state])]
    by_store = out.counts.get("_by_store", {})
    for store in out.stores:
        c = by_store.get(store, {})
        landed = c.get("verified", 0)
        attempted = sum(v for k, v in c.items() if k != "pending")
        parts = [f"{landed}/{attempted} בעגלה"]
        if c.get("skipped"):
            parts.append(f"{c['skipped']} כבר היו")
        lines.append(f"{_b(display_name(store))} — " + " · ".join(parts))
        for bucket in ("not_found", "not_added", "ambiguous", "unverified"):
            names = out.gaps.get(store, {}).get(bucket)
            if names:
                lines.append(f"   {_GAP_LABEL[bucket]}: " + ", ".join(_e(n) for n in names))
    stray = out.gaps.get("", {})
    for bucket, names in stray.items():
        lines.append(f"   {_GAP_LABEL[bucket]}: " + ", ".join(_e(n) for n in names))
    if out.pending:
        # The household consequence, not the mechanism: how much is done,
        # how much is left, and that it will be picked up.
        verified = out.counts.get("verified", 0)
        lines.append(f"לא הצלחתי לסיים כרגע; {verified} אומתו ו-{len(out.pending)} נשארו להשלמה — "
                     "אמשיך בהפעלה הבאה: "
                     + ", ".join(_e(n) for n in out.pending[:8])
                     + (" …" if len(out.pending) > 8 else ""))
    return "\n".join(lines)


def format_outcomes(storage, reports: dict) -> str:
    """The consolidated message for whatever runs these reports belong to.

    Empty when no report carries a run id — the caller then falls back
    to the bucket-based headline, so an older code path keeps talking.
    """
    run_ids: list = []
    for report in (reports or {}).values():
        rid = getattr(report, "run_id", None)
        if rid is not None and rid not in run_ids:
            run_ids.append(rid)
    if not run_ids:
        return ""
    return "\n\n".join(format_outcome(summarise(storage, rid)) for rid in run_ids)
