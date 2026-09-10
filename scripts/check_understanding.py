"""Does the bot understand the household, across many phrasings?

The paid tier of the phrasebook. Kept out of `pytest` on purpose: each
phrasing costs a `claude` CLI call (~8s, against Ishay's subscription),
so a full sweep is minutes and real token spend, not something to run on
every commit.

**Why it repeats each phrasing.** Classification is not deterministic.
Miri measured the same message producing three different behaviours in
three runs, and a suite that runs each case once reports whichever of
those it happened to get. So the unit of measurement here is a **rate**,
not a pass/fail: `3/3` is solid, `2/3` is a real finding about
stability, and reporting only the good run would be the exact
"looked checked and wasn't" failure this project keeps hitting.

Usage:
    python scripts/check_understanding.py                # intents, 1 pass
    python scripts/check_understanding.py --repeat 3     # expose instability
    python scripts/check_understanding.py --only price   # filter by substring
    python scripts/check_understanding.py --loop         # the safety barrier

The loop check is the one worth running after any change to `loop.py` or
its prompt: it asserts that messages nobody could parse never come back
as a cart-touching action.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

import phrasebook  # noqa: E402

from grocery_bot.nlu import parse_message  # noqa: E402
from grocery_bot.storage import Storage  # noqa: E402


def _storage():
    try:
        return Storage(os.environ.get("GROCERY_BOT_DB_PATH", "data/grocery_bot.sqlite3"))
    except Exception as exc:  # noqa: BLE001
        print(f"(no database: {type(exc).__name__} — running without preloaded context)")
        return None


def check_intents(repeat: int, only: str) -> int:
    storage = _storage()
    cases = [(p, ok) for p, ok in phrasebook.INTENTS if only in p]
    print(f"{len(cases)} phrasings x {repeat} run(s) — about "
          f"{len(cases) * repeat * 8 // 60} min\n")

    results = defaultdict(list)
    started = time.time()
    for phrase, acceptable in cases:
        for _ in range(repeat):
            began = time.time()
            try:
                parsed = parse_message(phrase, storage=storage)
                got, elapsed = parsed.intent, time.time() - began
            except Exception as exc:  # noqa: BLE001
                got, elapsed = f"ERROR:{type(exc).__name__}", time.time() - began
            results[phrase].append((got, elapsed, got in acceptable))

    failures, unstable = [], []
    for phrase, acceptable in cases:
        runs = results[phrase]
        good = sum(1 for _, _, ok in runs if ok)
        seen = {got for got, _, _ in runs}
        mark = "ok " if good == len(runs) else ("~  " if good else "FAIL")
        avg = sum(e for _, e, _ in runs) / len(runs)
        print(f"  {mark} {good}/{len(runs)}  {avg:5.1f}s  {phrase[:34]:36} "
              f"-> {', '.join(sorted(seen))}   (want {'/'.join(sorted(acceptable))})")
        if good == 0:
            failures.append((phrase, seen, acceptable))
        elif good < len(runs):
            unstable.append((phrase, seen))

    total = sum(len(r) for r in results.values())
    passed = sum(1 for r in results.values() for _, _, ok in r if ok)
    print(f"\n{passed}/{total} runs correct, {time.time() - started:.0f}s wall")
    if unstable:
        print(f"\n{len(unstable)} UNSTABLE — same message, different answers:")
        for phrase, seen in unstable:
            print(f"   {phrase[:40]:42} {sorted(seen)}")
    if failures:
        print(f"\n{len(failures)} WRONG every run:")
        for phrase, seen, want in failures:
            print(f"   {phrase[:40]:42} got {sorted(seen)}, want {sorted(want)}")
    # Instability is a finding, not a pass. Both count against the exit.
    return 1 if (failures or unstable) else 0


def check_loop_barrier(repeat: int) -> int:
    """Messages nobody could parse must never become a cart action.

    The barrier itself is unit-tested against `sanitise` and holds by
    construction; this exercises it end to end against the live model,
    which is the only way to see what the model actually proposes.
    """
    from grocery_bot.loop import CART_INTENTS

    storage = _storage()
    print(f"{len(phrasebook.LOOP_MUST_NOT_ACT)} unparseable messages x {repeat}\n")
    breaches = []
    for phrase in phrasebook.LOOP_MUST_NOT_ACT:
        for _ in range(repeat):
            parsed = parse_message(phrase, storage=storage)
            bad = parsed.intent in CART_INTENTS
            if bad:
                breaches.append((phrase, parsed.intent))
            asks = bool(parsed.reply)
            print(f"  {'BREACH' if bad else 'ok    '} {phrase[:22]:24} "
                  f"-> {parsed.intent:12} {'(asks)' if asks else '(silent)'}")
    if breaches:
        print(f"\n{len(breaches)} BREACHES — a cart intent from an unparseable message:")
        for phrase, intent in breaches:
            print(f"   {phrase!r} -> {intent}")
        return 2
    print("\nno breaches: no unparseable message produced a cart action")
    return 0


def export(path: str) -> int:
    """Write the corpus as JSON, for a consumer that is not this project.

    Miri routes the household's messages before they ever reach this
    project's CLI, so a phrasing Ishay types is tested here only after
    her side has already recognised and routed it. Sharing the corpus
    lets both sides run the same phrasings; sharing my *expectations*
    would not, because hers are about routing and mine are about
    resolution. So this exports the phrasings and the mall answers —
    which are facts about the data — and no intent labels, which are
    facts about this bot's own taxonomy.
    """
    import json

    payload = {
        "source": "grocery-automation tests/phrasebook.py",
        "note": ("Phrasings the household actually uses. `malls` answers are "
                 "this project's canonical names, verifiable via "
                 "`benefits-mall <phrase> --json`. An empty answer means the "
                 "phrase must NOT resolve — six of them are deliberate "
                 "refusals, including a right-brand-wrong-city case."),
        "malls": [{"phrase": p, "expect": e} for p, e in phrasebook.MALLS],
        "merchants": [p for p, _ in phrasebook.MERCHANTS],
        "merchants_absent": phrasebook.MERCHANTS_ABSENT,
        "food": phrasebook.FOOD,
        "unparseable": phrasebook.LOOP_MUST_NOT_ACT,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    counts = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
    print(f"wrote {path}: {counts}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1,
                        help="runs per phrasing; >1 exposes non-determinism")
    parser.add_argument("--only", default="", help="substring filter on the phrase")
    parser.add_argument("--loop", action="store_true",
                        help="check the loop's cart barrier instead of intents")
    parser.add_argument("--export", metavar="PATH",
                        help="write the corpus as JSON for another project")
    args = parser.parse_args()
    if args.export:
        return export(args.export)
    if args.loop:
        return check_loop_barrier(args.repeat)
    return check_intents(args.repeat, args.only)


if __name__ == "__main__":
    raise SystemExit(main())
