"""Evidence-weighted, semantics-first product resolution — vNext Phase 1.5.

Separate from `preferred_products` inference on purpose. That table
remembers whatever the old resolver once picked (a shower gel for
"יוגורט Pro וניל", a drink for "בננה") and the audit showed it cannot be
trusted as-is. Here every candidate — remembered, purchased, or found
in a chain's catalogue — is checked against the *meaning* of the
request first (`vnext_semantics`), and only what survives is ranked by
evidence.

Output is a `Resolution` with a status in
{exact_match, acceptable_match, unresolved, rejected_by_constraints},
a confidence, the reason it matched, which qualifiers were checked and
which were not, and full provenance including every candidate that was
considered and why it was rejected. Nothing here writes anywhere.

Human confirmation (`vnext_product_confirmations`, or a
`preferred_products` row with source='human') short-circuits to
exact_match — but the sanity check still runs, and a human choice that
violates the request is *flagged*, never silently overridden.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .household_evidence import Evidence, EvidenceType, Origin
from . import vnext_catalogue
from .vnext_config import DEFAULT, VNextConfig
from .vnext_semantics import ParsedTerm, checked, parse_term, unverified, violations

EXACT = "exact_match"
ACCEPTABLE = "acceptable_match"
UNRESOLVED = "unresolved"
REJECTED = "rejected_by_constraints"

CART_CAPABLE = ("shufersal", "tivtaam")


@dataclass
class Candidate:
    store: str
    product_code: str
    product_name: str
    source: str                      # human_confirmation | human_preference | remembered:<src> | purchase_history | catalogue
    origin: Origin
    purchases: int = 0               # real completed purchases on record
    violations: list[dict] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    status: str = UNRESOLVED
    confidence: float = 0.0
    why: str = ""
    substitution: bool = False       # soft violation only: offerable as a substitute

    def to_dict(self) -> dict:
        return {
            "store": self.store, "product_code": self.product_code, "product_name": self.product_name,
            "source": self.source, "origin": self.origin.value, "purchases": self.purchases,
            "status": self.status, "confidence": round(self.confidence, 3), "why": self.why,
            "violations": self.violations, "unverified": self.unverified, "checked": self.checked,
            "substitution": self.substitution,
        }


@dataclass
class Resolution:
    term: str
    parsed: ParsedTerm
    status: str
    confidence: float
    chosen: Candidate | None
    why: str
    qualifiers_checked: list[str]
    qualifiers_unverified: list[str]
    provenance: list[str]
    candidates: list[Candidate]
    substitution_candidates: list[Candidate]
    human_flagged: str = ""         # a human choice that violates the request, reported not overridden

    @property
    def product_confidence(self) -> float:
        return self.confidence

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "parsed": self.parsed.to_dict(),
            "status": self.status,
            "confidence": round(self.confidence, 3),
            "chosen": self.chosen.to_dict() if self.chosen else None,
            "why": self.why,
            "qualifiers_checked": self.qualifiers_checked,
            "qualifiers_unverified": self.qualifiers_unverified,
            "provenance": self.provenance,
            "candidates_considered": [c.to_dict() for c in self.candidates],
            "substitution_candidates": [c.to_dict() for c in self.substitution_candidates],
            "human_flagged": self.human_flagged,
        }


# -- candidate gathering ------------------------------------------------------------

def _candidates_from_evidence(term: str, evidence: list[Evidence], purchases_by_code: dict) -> list[Candidate]:
    out: dict[tuple[str, str], Candidate] = {}

    def add(store, code, name, source, origin):
        key = (store, str(code))
        if not code or key in out:
            if key in out and origin == Origin.HUMAN_DECLARED:
                out[key].origin = origin
                out[key].source = source
            return
        out[key] = Candidate(store=store, product_code=str(code), product_name=name or "", source=source,
                             origin=origin, purchases=int(purchases_by_code.get(key, 0)))

    for e in evidence:
        if e.type == EvidenceType.explicit_preference and e.product_code:
            if e.data.get("standing"):
                continue
            if e.origin == Origin.HUMAN_DECLARED:
                add(e.store, e.product_code, e.product_name, "human_preference", Origin.HUMAN_DECLARED)
            else:
                add(e.store, e.product_code, e.product_name, f"remembered:{e.data.get('source')}", Origin.INFERENCE)
        elif e.type == EvidenceType.purchase_history and e.product_code:
            add(e.store, e.product_code, e.product_name, "purchase_history", Origin.INFERENCE)
    return list(out.values())


def _catalogue_candidates(storage, parsed: ParsedTerm, config: VNextConfig, purchases_by_code: dict) -> list[Candidate]:
    """A shortlist from each chain's catalogue: the head noun alone (short
    names first), then the head with each stated qualifier word so a
    qualified request ("יוגורט Pro וניל") reaches the specific product
    and not only the twelve shortest yogurts. Read-only."""
    from .vnext_semantics import _BRAND_ALIASES, search_key  # noqa: PLC0415

    out: list[Candidate] = []
    query = search_key(parsed)
    if not query:
        return out
    extra_sets: list[list[str]] = [[]]
    words: list[str] = list(parsed.flavor) + list(parsed.colors) + list(parsed.positive)
    for b in parsed.brand:
        words += list(_BRAND_ALIASES.get(b, (b,)))
    if parsed.size == "small":
        words += ["מיני", "בייבי", "קטן"]
    if parsed.variety:
        words += ["צבעוני", "מיקס", "מגוון"]
    for w in words:
        extra_sets.append([w])
    seen: set[tuple[str, str]] = set()
    for also in extra_sets:
        try:
            rows = vnext_catalogue.search(storage, "shufersal", query, config.resolver_catalogue_candidates,
                                          also, config.catalogue_cache_ttl_seconds)
        except Exception:  # noqa: BLE001 - a catalogue hiccup must not sink resolution
            rows = []
        for row in rows:
            key = ("shufersal", str(row["code"]))
            if key in seen:
                continue
            seen.add(key)
            out.append(Candidate(store="shufersal", product_code=key[1], product_name=row["name"],
                                 source="catalogue", origin=Origin.INFERENCE,
                                 purchases=int(purchases_by_code.get(key, 0))))
        try:
            rows = vnext_catalogue.search(storage, "tivtaam", query, config.resolver_catalogue_candidates,
                                          also, config.catalogue_cache_ttl_seconds)
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            key = ("tivtaam", str(row["code"]))
            if key in seen:
                continue
            seen.add(key)
            out.append(Candidate(store="tivtaam", product_code=key[1], product_name=row["name"],
                                 source="catalogue", origin=Origin.INFERENCE,
                                 purchases=int(purchases_by_code.get(key, 0))))
    return out


def purchases_index(storage) -> dict[tuple[str, str], int]:
    """(store, product_code) -> completed purchases on record. Tiv Taam
    from real order lines (by product code and by barcode); Shufersal
    from stock_items.picked_count where present."""
    idx: dict[tuple[str, str], int] = {}
    try:
        for line in storage.tivtaam_purchase_lines("tivtaam"):
            for key in (("tivtaam", str(line.get("product_code"))), ("tivtaam", str(line.get("barcode") or ""))):
                if key[1]:
                    idx[key] = idx.get(key, 0) + 1
    except Exception:  # noqa: BLE001
        pass
    for store in CART_CAPABLE:
        try:
            for row in storage.list_stock_items(store):
                key = (store, str(row.get("product_code")))
                n = int(row.get("picked_count") or 0)
                if n and key not in idx:
                    idx[key] = n
        except Exception:  # noqa: BLE001
            pass
    return idx


def human_confirmations(storage, term: str) -> list[Candidate]:
    """Positive confirmations for this term, as HUMAN_DECLARED candidates.

    The newest row per (store, product) decides: a product corrected away
    and later confirmed again is confirmed; the reverse is rejected.
    """
    out = []
    for (store, code), row in _latest_by_product(storage, term).items():
        if (row.get("polarity") or "positive") != "negative":
            out.append(Candidate(store=store, product_code=code,
                                 product_name=row.get("product_name") or "",
                                 source=f"human_confirmation:{row.get('kind')}", origin=Origin.HUMAN_DECLARED))
    return out


def human_rejections(storage, term: str) -> set:
    """(store, code) pairs the household corrected *away* from for this term.

    Phase 2a: "שנה" after a tap and the old product of a replacement are
    recorded as negative confirmations; they must never be offered
    again as human-confirmed, whatever the old `preferred_products` says.
    """
    return {key for key, row in _latest_by_product(storage, term).items()
            if (row.get("polarity") or "positive") == "negative"}


def _latest_by_product(storage, term: str) -> dict:
    latest: dict = {}
    for row in _confirmation_rows(storage, term):  # ordered by confirmed_at
        latest[(row["store"], str(row["product_code"]))] = row
    return latest


def _confirmation_rows(storage, term: str) -> list[dict]:
    try:
        rows = storage.list_vnext_product_confirmations()
    except Exception:  # noqa: BLE001
        return []
    return [r for r in rows if r.get("term") == term]


# -- resolution ------------------------------------------------------------------------

def _score(c: Candidate, parsed: ParsedTerm, config: VNextConfig) -> None:
    vs = violations(parsed, c.product_name)
    c.violations = [{"rule": v.rule, "detail": v.detail, "hard": v.hard} for v in vs]
    c.unverified = unverified(parsed, c.product_name)
    c.checked = checked(parsed, c.product_name)
    hard = [v for v in vs if v.hard]
    soft = [v for v in vs if not v.hard]
    bonus = min(config.resolver_evidence_bonus_cap, c.purchases * config.resolver_evidence_bonus_per_purchase)
    if c.origin == Origin.HUMAN_DECLARED and not hard:
        c.status, c.confidence = EXACT, 1.0
        c.why = "human-confirmed for this term"
        return
    if hard:
        c.status, c.confidence = REJECTED, 0.0
        c.why = "; ".join(v.detail for v in hard)
        return
    if soft:
        c.status, c.substitution = UNRESOLVED, True
        c.confidence = max(0.0, config.resolver_acceptable_base - config.resolver_unverified_penalty * (len(c.unverified) + 1)) + bonus
        c.why = "substitute only: " + "; ".join(v.detail for v in soft)
        return
    if not c.unverified:
        c.status = EXACT
        c.confidence = min(0.98, config.resolver_exact_base + bonus)
        c.why = "every stated qualifier verified in the product name"
        return
    c.status = ACCEPTABLE
    c.confidence = max(0.2, config.resolver_acceptable_base - config.resolver_unverified_penalty * len(c.unverified)) + bonus
    c.why = "no conflict; unverified: " + ", ".join(c.unverified)


def resolve(storage, term: str, evidence: list[Evidence], config: VNextConfig = DEFAULT,
            purchases_by_code: dict | None = None, raw: str | None = None,
            catalogue: bool = True) -> Resolution:
    """Resolve one household term against everything known. Read-only."""
    raw_text = raw or next((str(e.data.get("raw")) for e in evidence
                            if e.type == EvidenceType.explicit_need and e.data.get("raw")), term)
    parsed = parse_term(raw_text)
    purchases_by_code = purchases_by_code if purchases_by_code is not None else purchases_index(storage)
    rejected = {(e.store, str(e.product_code)) for e in evidence if e.type == EvidenceType.explicit_rejection}
    rejected |= human_rejections(storage, term)

    candidates = human_confirmations(storage, term)
    candidates += _candidates_from_evidence(term, evidence, purchases_by_code)
    seen = {(c.store, c.product_code) for c in candidates}
    if catalogue:
        for c in _catalogue_candidates(storage, parsed, config, purchases_by_code):
            if (c.store, c.product_code) not in seen:
                candidates.append(c)
                seen.add((c.store, c.product_code))

    provenance: list[str] = []
    for c in candidates:
        if (c.store, c.product_code) in rejected:
            c.status, c.confidence, c.why = REJECTED, 0.0, "household rejected this product for this term"
            c.violations = [{"rule": "human:rejected", "detail": c.why, "hard": True}]
            continue
        _score(c, parsed, config)

    human_flagged = ""
    for c in candidates:
        if c.origin == Origin.HUMAN_DECLARED and c.status == REJECTED and "human:rejected" not in [v["rule"] for v in c.violations]:
            human_flagged = f"human choice '{c.product_name}' violates the request: {c.why}"

    order = {EXACT: 0, ACCEPTABLE: 1, UNRESOLVED: 2, REJECTED: 3}
    ranked = sorted(candidates, key=lambda c: (order[c.status], -c.confidence, -c.purchases, len(c.product_name)))
    chosen = next((c for c in ranked if c.status in (EXACT, ACCEPTABLE)), None)
    subs = [c for c in ranked if c.substitution]
    provenance = [f"{c.source}@{c.store}:{c.product_code}={c.status}" for c in ranked]

    if chosen is not None:
        status, confidence, why = chosen.status, chosen.confidence, chosen.why
        q_checked, q_unv = chosen.checked, chosen.unverified
    elif candidates and all(c.status == REJECTED for c in candidates):
        status, confidence = REJECTED, 0.0
        why = "every known candidate violates the request: " + "; ".join(
            f"{c.product_name} ({c.violations[0]['rule'] if c.violations else '?'})" for c in ranked[:3])
        q_checked, q_unv = parsed.qualifiers, []
    else:
        status, confidence = UNRESOLVED, 0.0
        why = ("only substitutes found: " + ", ".join(c.product_name for c in subs[:3])) if subs else \
              "no candidate at any chain"
        q_checked, q_unv = parsed.qualifiers, parsed.qualifiers if not subs else subs[0].unverified

    return Resolution(term=term, parsed=parsed, status=status, confidence=confidence, chosen=chosen, why=why,
                      qualifiers_checked=q_checked, qualifiers_unverified=q_unv, provenance=provenance,
                      candidates=ranked, substitution_candidates=subs, human_flagged=human_flagged)


def check_line(parsed: ParsedTerm, line_name: str) -> tuple[str, list[str]]:
    """Would this order line satisfy the request? -> (status, unverified).
    Used by reconciliation; same rules as resolution, no evidence weighting."""
    vs = violations(parsed, line_name)
    if any(v.hard for v in vs):
        return REJECTED, []
    unv = unverified(parsed, line_name)
    if vs:
        return UNRESOLVED, unv
    return (EXACT if not unv else ACCEPTABLE), unv
