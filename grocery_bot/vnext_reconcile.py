"""Which old pending requests were probably already bought? — vNext Phase 1.5.

Phase 1 counted every `adhoc_requests` row with `consumed=0` as an
active need. Fourteen of them date from 09-11/09-16, and a real Tiv Taam
order landed on 09-17 with lines that plainly answer several of them.
The production consumption rules cannot see this (the audit's gap #2),
and Phase 1.5 does not change them. Instead each pending request is
compared, read-only, against every order line recorded *after* it was
made, with the same semantic check the resolver uses — so "סלק (לא
באריזת ואקום)" is fulfilled by a plain beet and NOT by a vacuum one,
and "גרנולה ללא תוספת סוכר" is not fulfilled by a granola that says
nothing about sugar.

Result per request: `active` (no later line matches), `likely_fulfilled`
(a later line matches with every stated qualifier verified), or
`uncertain` (a later line matches the head but leaves a qualifier
unverified, or a later order exists at a chain whose lines Gordon
cannot read). Nothing is written; the request row is untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .vnext_resolver import ACCEPTABLE, EXACT, REJECTED, UNRESOLVED, check_line
from .vnext_semantics import parse_term

ACTIVE = "active"
LIKELY_FULFILLED = "likely_fulfilled"
UNCERTAIN = "uncertain"


@dataclass
class RequestReconciliation:
    request_id: int
    text: str
    created_at: str
    status_estimate: str
    fulfillment_evidence: list[dict] = field(default_factory=list)
    considered_lines: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id, "text": self.text, "created_at": self.created_at,
            "request_status_estimate": self.status_estimate,
            "fulfillment_evidence": self.fulfillment_evidence,
            "considered_lines": self.considered_lines, "note": self.note,
        }


def _day(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:19]).date()
    except ValueError:
        return None


def later_lines(storage, since: date) -> list[dict]:
    """Order lines at every chain dated after `since`. Tiv Taam has real
    lines; Shufersal has only order headers (no line detail is synced),
    which is reported as an `unreadable` pseudo-line so the caller can
    say 'uncertain' rather than 'active'."""
    out: list[dict] = []
    for line in storage.tivtaam_purchase_lines("tivtaam"):
        day = _day(line.get("order_date"))
        if day and day > since:
            out.append({"store": "tivtaam", "order_code": str(line.get("order_code")), "date": day.isoformat(),
                        "name": line.get("raw_name") or "", "quantity": line.get("actual_quantity"),
                        "substituted": bool(line.get("substituted")), "readable": True})
    try:
        for order in storage.list_orders("shufersal"):
            day = _day(order.get("placed_at"))
            if day and day > since:
                out.append({"store": "shufersal", "order_code": str(order.get("order_code") or order.get("code")),
                            "date": day.isoformat(), "name": "", "readable": False})
    except Exception:  # noqa: BLE001
        pass
    return out


def reconcile_request(req, lines: list[dict]) -> RequestReconciliation:
    created = _day(req.created_at) or date.min
    parsed = parse_term(req.text)
    evidence: list[dict] = []
    best = ACTIVE
    unreadable_orders = []
    considered = 0
    for line in lines:
        if _day(line["date"]) is None or _day(line["date"]) <= created:
            continue
        considered += 1
        if not line.get("readable"):
            unreadable_orders.append(line)
            continue
        status, unv = check_line(parsed, line["name"])
        if status == REJECTED:
            continue
        item = {"store": line["store"], "order_code": line["order_code"], "date": line["date"],
                "line": line["name"], "quantity": line.get("quantity"), "resolver_status": status,
                "qualifiers_unverified": unv, "substituted": line.get("substituted", False)}
        evidence.append(item)
        if status == EXACT:
            best = LIKELY_FULFILLED
        elif status in (ACCEPTABLE, UNRESOLVED) and best != LIKELY_FULFILLED:
            best = UNCERTAIN
    note = ""
    if best == ACTIVE and unreadable_orders:
        best = UNCERTAIN
        note = "a later order exists at a chain whose lines are not synced ({})".format(
            ", ".join(f"{o['store']} {o['order_code']} {o['date']}" for o in unreadable_orders[:3]))
        evidence += [{"store": o["store"], "order_code": o["order_code"], "date": o["date"],
                      "line": "", "resolver_status": "unreadable"} for o in unreadable_orders[:3]]
    elif best == ACTIVE:
        note = "no later order line matches this request"
    elif best == UNCERTAIN:
        note = "a later line matches the head noun but a stated qualifier is unverified"
    else:
        note = "a later line satisfies every stated qualifier"
    return RequestReconciliation(request_id=int(req.id), text=req.text, created_at=str(req.created_at or ""),
                                 status_estimate=best, fulfillment_evidence=evidence,
                                 considered_lines=considered, note=note)


def reconcile_all(storage) -> list[RequestReconciliation]:
    """Every pending request, judged against later order lines. Read-only."""
    pending = storage.list_pending_adhoc()
    if not pending:
        return []
    earliest = min((_day(r.created_at) or date.today()) for r in pending)
    lines = later_lines(storage, earliest)
    return [reconcile_request(r, lines) for r in pending]


def counts(results: list[RequestReconciliation]) -> dict:
    out = {ACTIVE: 0, LIKELY_FULFILLED: 0, UNCERTAIN: 0}
    for r in results:
        out[r.status_estimate] = out.get(r.status_estimate, 0) + 1
    return out
