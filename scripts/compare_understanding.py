"""Run the same real requests through both understanding layers.

Set up by Ishay 2026-09-11: build the stripped-down path, then decide
which rules to bring back **after** seeing results, not before.

    current  = nlu.parse_message  -> one intent (+ actions, + a second
               pass through loop.reconsider when it gives up)
    direct   = planner.plan_message -> a validated plan of tool calls

Both are run **plan-only**. Nothing is added to a list, nothing touches a
cart, no adapter is opened. What is measured is understanding, and
measuring it by filling a real cart forty times would be its own kind of
mistake.

Reported per message, then totalled:

    understood     did it produce an action at all, or give up
    steps          how many of the requests in the message survived
    questions      did it stop to ask
    latency        wall clock seconds
    model calls    subprocesses spawned

**The expectation to hold loosely.** The interesting number is not which
path wins overall. It is which messages one path understands and the
other does not, so run with --verbose and read those rows: a rule worth
keeping shows up as the current path getting something right that the
direct path fluffs, and a rule worth dropping shows up as the reverse.

Usage:
    python scripts/compare_understanding.py                 # the fixture set
    python scripts/compare_understanding.py --verbose
    python scripts/compare_understanding.py --only direct
    python scripts/compare_understanding.py --file my_messages.txt
    python scripts/compare_understanding.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grocery_bot import nlu, planner  # noqa: E402

# Real shapes, not clean ones. Grouped by what they are meant to expose;
# the plain adds are here as a control, because a path that understands
# the hard ones and breaks the easy ones is not an improvement.
FIXTURES: list[tuple[str, str]] = [
    # -- control: the ordinary cases that already work ------------------
    ("plain", "תוסיף חלב"),
    ("plain", "נגמר הקוטג"),
    ("plain", "צריך 3 קילו עגבניות"),
    ("plain", "כמה עולה טחינה גולמית"),
    ("plain", "מה יש במבצע"),
    ("plain", "תראה לי את הרשימה"),
    ("plain", "סיימתי לקנות"),
    ("plain", "מתכון לשקשוקה"),
    # -- two requests in one message ------------------------------------
    ("multi", "תוסיף חלב וכמה עולה טחינה?"),
    ("multi", "תוריד את הלחם ותוסיף פיתות"),
    ("multi", "קוטג, גבינה צהובה, ומה המחיר של שמן זית?"),
    # -- corrections that only mean something in context ----------------
    ("followup", "בעצם שניים"),
    ("followup", "השני במקום הראשון"),
    ("followup", "את זה רק הפעם"),
    ("followup", "לא זה, השני"),
    ("followup", "תעשה 3"),
    # -- scope and partial completion -----------------------------------
    ("scope", "סיימתי בשופרסל, בטיב טעם עוד לא"),
    ("scope", "את כל השאר כרגיל"),
    ("scope", "בלי הטחינה הזאת, אחרת כן"),
    # -- phrasings that do not fit a slot cleanly -----------------------
    ("loose", "לא הגבינה הזאת, תחליף לזו שקנינו בפעם הקודמת"),
    ("loose", "תכין לי קניות לביף בורגיניון לשישה"),
    ("loose", "כמו בפעם שעברה אבל לאירוח"),
    ("loose", "מה חסר לנו לשבת?"),
    ("loose", "העגלה מוכנה?"),
    ("loose", "אפשר משהו לילדים לבית ספר"),
]

# What the previous turn was, for the follow-up fixtures. Without it those
# messages are unanswerable by *any* path, which is the point of passing
# the same context to both.
CONTEXT = {
    "last_subject": "קוטג 5% תנובה 250 גרם",
    "last_store": "shufersal",
    "pending": ["טחינה גולמית", "לחם אחיד"],
    "carts": {"shufersal": {"count": 14, "items": ["קוטג 5% תנובה", "לחם אחיד"]}},
    "stores": ["shufersal", "tivtaam"],
    "open_questions": 3,
}


def run_current(message: str) -> dict:
    started = time.monotonic()
    parsed = nlu.parse_message(message, storage=None, context=CONTEXT)
    seconds = time.monotonic() - started
    actions = [a.intent for a in parsed.actions] or [parsed.intent]
    understood = parsed.intent not in ("unclear", "")
    return {
        "understood": understood,
        "steps": len([a for a in actions if a not in ("unclear", "smalltalk")]),
        "detail": ", ".join(actions),
        "question": bool(parsed.reply) and not understood,
        "seconds": round(seconds, 1),
        # One call normally; two when the first gave up and the loop ran.
        "model_calls": 2 if (not understood and not parsed.used_fallback) else 1,
        "fallback": parsed.used_fallback,
    }


def run_direct(message: str) -> dict:
    plan = planner.plan_message(message, CONTEXT)
    return {
        "understood": bool(plan.steps) or bool(plan.question),
        "steps": len(plan.steps),
        "detail": ", ".join(
            f"{s.tool}({', '.join(f'{k}={v}' for k, v in s.args.items())})"
            for s in plan.steps
        ) or "—",
        "question": bool(plan.question),
        "seconds": round(plan.seconds, 1),
        "model_calls": plan.model_calls,
        "refusals": plan.refusals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("current", "direct"))
    parser.add_argument("--file", help="one message per line, instead of the fixtures")
    parser.add_argument("--verbose", action="store_true", help="print what each produced")
    parser.add_argument("--json", help="write the full result to this file")
    args = parser.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            fixtures = [("file", line.strip()) for line in handle if line.strip()]
    else:
        fixtures = FIXTURES

    paths = {"current": run_current, "direct": run_direct}
    if args.only:
        paths = {args.only: paths[args.only]}

    rows = []
    for group, message in fixtures:
        row = {"group": group, "message": message}
        for name, run in paths.items():
            try:
                row[name] = run(message)
            except Exception as exc:  # noqa: BLE001
                row[name] = {"understood": False, "steps": 0, "detail": f"ERROR {exc}",
                             "question": False, "seconds": 0.0, "model_calls": 0}
            print(".", end="", flush=True)
        rows.append(row)
    print()

    for name in paths:
        got = [r[name] for r in rows]
        understood = sum(1 for g in got if g["understood"])
        print(f"\n=== {name}")
        print(f"  understood      {understood}/{len(got)}")
        print(f"  total steps     {sum(g['steps'] for g in got)}")
        print(f"  asked instead   {sum(1 for g in got if g['question'])}")
        print(f"  model calls     {sum(g['model_calls'] for g in got)}")
        print(f"  seconds total   {sum(g['seconds'] for g in got):.1f}"
              f"  (median {sorted(g['seconds'] for g in got)[len(got) // 2]:.1f})")

    if len(paths) > 1:
        print("\n=== where they disagree")
        for row in rows:
            a, b = row["current"], row["direct"]
            if a["understood"] != b["understood"] or a["steps"] != b["steps"]:
                print(f"  [{row['group']}] {row['message']}")
                print(f"      current: {a['detail']}")
                print(f"      direct : {b['detail']}")

    if args.verbose:
        print("\n=== every row")
        for row in rows:
            print(f"\n[{row['group']}] {row['message']}")
            for name in paths:
                got = row[name]
                print(f"   {name:8} {got['seconds']:>5.1f}s  {got['detail']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=1)
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
