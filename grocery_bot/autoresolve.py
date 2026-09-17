"""Decide which product a term means, from what the household actually buys.

Set by Ishay 2026-09-17, after being shown 90 open choice questions:
*"אני לא מתכוון לענות על 90 שאלות. הפרוסס הזה לא עובד. קח החלטה מה לשים
על בסיס היסטוריית הקנייה שלי."*

He is right that the process failed, and the reason is worth stating
precisely, because it was not laziness on his part. **The questions were
generated before the evidence to answer them existed.** 108 of the 134
open questions are Tiv Taam, and until 2026-09-15 this project could not
read a single Tiv Taam order — `last_purchase` held 313 Shufersal rows
and zero Tiv Taam ones, and `stock_items` held 94 Tiv Taam products
derived from a thin partial source. So the bot asked a person to supply,
one message at a time, what its own account had been holding all along:
40 orders, 377 distinct products, with the share of orders each appears
in.

With that in hand most of these answer themselves. `פלפל אדום` is not a
hard choice between "פלפל אדום", "תבלין פלפל אדום" and "טבסקו רוטב על
בסיס פלפל אדום" — the household has bought plain `פלפל אדום` in **78% of
their Tiv Taam orders**.

**The order of evidence**, strongest first. Each step is a fact about
this household, not a guess about shoppers in general:

1. `bought_here` — a candidate is a product they buy at this chain.
   Ranked by share of orders, so the weekly one beats the one-off.
2. `bought_other` — they buy it at the other chain. A real preference,
   just expressed elsewhere.
3. `preferred` — they answered this same question before.
4. `controlled` — a price-controlled item (מוצר בפיקוח) among otherwise
   equal candidates. Ishay's standing tie-break, set 2026-09-04.
5. `unit_price` — best value per unit, the arithmetic he was doing by
   hand anyway.
6. `shortest` — last resort. The plainest name is the base product;
   the long ones are the flavoured, gourmet and derivative versions
   ("ממרח קישואים קלויים" is not "קישואים").

**Nothing is left unanswered**, because leaving it unanswered is the
behaviour he rejected. What the weaker bases buy is honesty rather than
certainty: `confidence` is carried through to the summary so the two or
three worth a second look can be shown as exactly that, instead of
hiding ninety decisions behind a number.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Bases in descending strength; the summary groups by this order.
STRONG_BASES = ("bought_here", "bought_other", "preferred")

# A product bought this rarely is not evidence of a habit — at 40 orders
# this is one appearance. Kept low on purpose: one real purchase still
# beats every guess below it.
MIN_SHARE = 0.02

# Israeli price-controlled goods are marked in the name itself.
CONTROLLED_MARKERS = ("בפיקוח", "פיקוח")


@dataclass
class Decision:
    ambiguity_id: int
    store: str
    term: str
    index: int
    name: str
    basis: str
    detail: str = ""
    code: str = ""
    alternatives: list = field(default_factory=list)
    # How much of the search term the chosen name actually covers, 0..1.
    score: float = 0.0

    @property
    def confident(self) -> bool:
        """Strong evidence *and* a full answer to the term.

        Both halves are needed. `פלפל צילי` resolved to `פלפל אדום` —
        bought in 94% of orders, so the evidence could hardly be
        stronger, and it covers only the head word: they asked for chili
        and would get a sweet red pepper. Partial coverage belongs in the
        "worth a look" list, not among the certainties."""
        return self.basis in STRONG_BASES and self.score >= 1.0


def _fold(text) -> str:
    from .storage import _fold_apostrophes

    return " ".join(_fold_apostrophes(str(text or "")).split()).strip()


def _bought_index(storage, store: str) -> dict:
    """Folded product name -> (share, code, name) for what they buy here."""
    out = {}
    try:
        rows = storage.list_stock_items(store)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read stock items for %s", store)
        return out
    for row in rows:
        name = row.get("product_name") if isinstance(row, dict) else getattr(row, "product_name", "")
        share = row.get("share") if isinstance(row, dict) else getattr(row, "share", 0.0)
        code = row.get("product_code") if isinstance(row, dict) else getattr(row, "product_code", "")
        folded = _fold(name)
        if not folded or (share or 0) < MIN_SHARE:
            continue
        if folded not in out or (share or 0) > out[folded][0]:
            out[folded] = (share or 0.0, code or "", name or "")
    return out


def _candidates(row) -> list:
    """(index, name, card) for every candidate, whichever shape was stored.

    Tiv Taam rows carry names only — `candidate_cards` is empty for all
    108 of them — so anything that needs a price or a code has to cope
    with its absence rather than assume the richer shape.
    """
    cards = []
    try:
        cards = json.loads(row["candidate_cards"] or "[]")
    except (ValueError, TypeError, KeyError):
        cards = []
    if cards:
        return [(i, str(c.get("name") or ""), c) for i, c in enumerate(cards)]
    try:
        names = json.loads(row["candidates"] or "[]")
    except (ValueError, TypeError, KeyError):
        names = []
    return [(i, str(n), {}) for i, n in enumerate(names)]


MIN_WORD = 3


def _words(text) -> list:
    return [w for w in _fold(text).split() if len(w) >= MIN_WORD]


def _relevance(term: str, name: str) -> float:
    """How well a candidate answers *this* term. 0 means it does not.

    **This gate is the whole correctness of the module** and its absence
    was the first version's defect. Ranking candidates purely by how
    often the household buys them let any high-share staple win whenever
    the chain's autocomplete returned a noisy list: `גבינה צהובה מגוררת`
    resolved to `פלפל צהוב` (bought in 88% of orders, shares the word
    צהוב), `מארז אוכמניות` to `דלעת ארוזה`, `ביצי משק M` to `כרוב לבן`.
    All three are confident-looking nonsense, which is worse than a
    question.

    A purchase share says "they like this product". It never says "this
    product is what they asked for". So relevance decides *which*
    candidates are eligible and the share only orders them.

    The head word carries the identity in Hebrew — the noun comes first
    and the qualifiers follow — so a candidate that does not contain it
    is not the same kind of thing at all.
    """
    term_words = _words(term)
    name_words = _words(name)
    if not term_words or not name_words:
        return 0.0
    head = term_words[0]
    if not any(head == w or w.startswith(head) or head.startswith(w) for w in name_words):
        return 0.0
    covered = sum(
        1 for t in term_words
        if any(t == w or w.startswith(t) or t.startswith(w) for w in name_words)
    )
    return covered / len(term_words)


def _match(folded_name: str, index: dict):
    """Exact fold match first, then a clean prefix. Never a loose contains.

    "ממרח קישואים" contains "קישואים" and is a different product; matching
    on containment is how a spice paste becomes a vegetable.
    """
    if folded_name in index:
        return index[folded_name]
    best = None
    for key, value in index.items():
        if folded_name.startswith(key + " ") or key.startswith(folded_name + " "):
            if best is None or value[0] > best[0]:
                best = value
    return best


def _controlled(name: str) -> bool:
    return any(mark in (name or "") for mark in CONTROLLED_MARKERS)


def _unit_price(card) -> float | None:
    try:
        value = float(card.get("unitPrice"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def decide(storage, row, here=None, other=None) -> Decision:
    """One question, answered. Never returns None."""
    store = row["store"]
    term = row["original_term"]
    candidates = _candidates(row)
    if not candidates:
        return Decision(row["id"], store, term, -1, "", "none",
                        "no candidates were stored")

    other_store = "shufersal" if store == "tivtaam" else "tivtaam"
    here = _bought_index(storage, store) if here is None else here
    other = _bought_index(storage, other_store) if other is None else other

    alternatives = [name for _, name, _ in candidates]

    # Only candidates that actually answer the term are eligible. See
    # `_relevance`: without this, purchase share alone picked the
    # household's favourite staple out of a noisy candidate list.
    eligible = [
        (i, name, card, score)
        for i, name, card in candidates
        if (score := _relevance(term, name)) > 0
    ]

    # 1 + 2: what they actually buy, this chain before the other. Ranked
    # by relevance first and purchase share second — a better answer to
    # the question beats a more popular product.
    for index, label, basis in ((here, "כאן", "bought_here"),
                                (other, "ברשת השנייה", "bought_other")):
        scored = []
        for i, name, card, score in eligible:
            hit = _match(_fold(name), index)
            if hit:
                scored.append((score, hit[0], i, name, hit))
        if scored:
            scored.sort(key=lambda s: (-s[0], -s[1]))
            score, share, i, name, hit = scored[0]
            detail = f"נקנה {label} ב-{share * 100:.0f}% מההזמנות"
            if score < 1.0:
                detail += " · התאמה חלקית למונח"
            return Decision(
                row["id"], store, term, i, name, basis, detail,
                code=hit[1], alternatives=alternatives, score=score,
            )

    # 3: an answer they already gave for this term.
    try:
        remembered = storage.preferred_for(store, term)
    except Exception:  # noqa: BLE001
        remembered = None
    if remembered:
        wanted = _fold(remembered.get("product_name") if isinstance(remembered, dict)
                       else getattr(remembered, "product_name", ""))
        for i, name, _ in candidates:
            if _fold(name) == wanted:
                return Decision(row["id"], store, term, i, name, "preferred",
                                "נבחר על ידכם בעבר", alternatives=alternatives)

    # Below here, prefer an eligible candidate and fall back to the full
    # list only when nothing answers the term at all.
    pool = [(i, n, c) for i, n, c, _ in eligible] or candidates

    # 4: price-controlled wins among otherwise equal candidates.
    for i, name, _ in pool:
        if _controlled(name):
            return Decision(row["id"], store, term, i, name, "controlled",
                            "מוצר בפיקוח", alternatives=alternatives)

    # 5: best value per unit, where the cards carry one.
    # Ranked by how well it answers the term *first*, price second.
    # Cheapest-wins alone turned `מרק בקר` into `מרק בטעם עוף פרווה` and
    # `בפלות שוקולד` into `בפלות בטעם לימון`: the same product family at
    # a better price, and not the thing that was asked for.
    priced = [
        (-_relevance(term, n), u, i, n)
        for i, n, c in pool if (u := _unit_price(c)) is not None
    ]
    if priced:
        priced.sort()
        _, unit, i, name = priced[0]
        return Decision(row["id"], store, term, i, name, "unit_price",
                        f"הזול ביותר ליחידה ({unit:.2f})", alternatives=alternatives)

    # 6: the plainest name is the base product.
    if pool is candidates:
        # Nothing on the list answers the term at all. Naming one anyway
        # is how `בייקון` became `מצלמת EYEX4` — a confident falsehood,
        # worse than the question it replaced. Say so instead, and let
        # the term be searched fresh next time.
        return Decision(row["id"], store, term, -1, "", "no_match",
                        "לא נמצא מוצר מתאים — לא הוספתי כלום",
                        alternatives=alternatives)
    i, name, _ = min(pool, key=lambda c: (len(c[1]), c[0]))
    return Decision(row["id"], store, term, i, name, "shortest",
                    "השם הפשוט ביותר — כנראה המוצר הבסיסי",
                    alternatives=alternatives)


def resolve_all(storage) -> list:
    """A decision for every open question."""
    from contextlib import closing

    with closing(storage._connect()) as conn:  # noqa: SLF001 - storage-internal
        rows = conn.execute(
            "SELECT * FROM pending_ambiguities WHERE resolved = 0 ORDER BY store, id"
        ).fetchall()

    indexes = {}
    decisions = []
    for row in rows:
        store = row["store"]
        other_store = "shufersal" if store == "tivtaam" else "tivtaam"
        for key in (store, other_store):
            if key not in indexes:
                indexes[key] = _bought_index(storage, key)
        try:
            decisions.append(decide(storage, row, indexes[store], indexes[other_store]))
        except Exception:  # noqa: BLE001
            logger.exception("Could not decide ambiguity %s", row["id"])
    return decisions


def apply(storage, decisions) -> int:
    """Record the decisions so they are reused and stop being asked."""
    applied = 0
    for decision in decisions:
        if decision.basis == "no_match":
            # Stop asking, but do not write a falsehood. Applying this
            # branch once recorded `בייקון = מצלמת EYEX4` as the
            # household's preference — a wrong answer that would then be
            # reused forever, which is strictly worse than the question
            # it replaced. The term simply gets searched fresh next time.
            try:
                storage.mark_ambiguity_resolved(decision.ambiguity_id)
            except Exception:  # noqa: BLE001
                logger.exception("Could not close %s", decision.term)
            continue
        if decision.index < 0 or not decision.name:
            continue
        try:
            storage.remember_choice(
                decision.store, decision.term, decision.code or decision.name,
                decision.name,
            )
            storage.mark_ambiguity_resolved(decision.ambiguity_id)
            applied += 1
        except Exception:  # noqa: BLE001
            logger.exception("Could not apply decision for %s", decision.term)
    return applied


def format_summary(decisions, limit: int = 12) -> str:
    """What was decided, with the uncertain ones surfaced rather than buried."""
    if not decisions:
        return "אין שאלות פתוחות."
    from .chains import display_name

    confident = [d for d in decisions if d.confident]
    weak = [d for d in decisions if not d.confident and d.index >= 0]
    unmatched = [d for d in decisions if d.basis == "no_match"]

    lines = [f"✅ *החלטתי על {len(decisions)} פריטים לפי מה שאתם באמת קונים*", ""]
    if confident:
        lines.append(f"*{len(confident)} לפי היסטוריית קנייה* — אלה בטוחים:")
        for d in confident[:limit]:
            lines.append(f"• {d.term} → *{d.name}* ({d.detail})")
        if len(confident) > limit:
            lines.append(f"_ועוד {len(confident) - limit}._")
    if weak:
        lines += ["", f"*{len(weak)} בלי היסטוריה* — ניחוש מנומק, שווה מבט:"]
        for d in weak[:limit]:
            lines.append(
                f"• {d.term} → *{d.name}* ({d.detail}) · {display_name(d.store)}"
            )
        if len(weak) > limit:
            lines.append(f"_ועוד {len(weak) - limit}._")
    if unmatched:
        lines += ["", f"*{len(unmatched)} לא מצאתי בכלל* — לא הוספתי כלום:"]
        lines += [f"• {d.term}" for d in unmatched[:limit]]
    lines += ["", "_לשנות משהו — פשוט תכתבו לי, למשל \"לא, תביא את הגדול\"._"]
    return "\n".join(lines)
