"""Run the fixed message suite through every conversation backend and report.

Mandate Phase 11 (2026-09-17), unblocked the same day when Ishay lifted
the five-clean-runs gate on execution and asked for this comparison to
run now rather than after. See grocery_bot/convobench.py for what each
backend is and the discipline (plan-only, nothing touches a real cart).

Usage:
    python scripts/compare_conversation_backends.py                # everything
    python scripts/compare_conversation_backends.py --only planner
    python scripts/compare_conversation_backends.py --skip-session  # faster
    python scripts/compare_conversation_backends.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grocery_bot import convobench  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", choices=list(convobench.BACKENDS))
    ap.add_argument("--skip-session", action="store_true")
    ap.add_argument("--json", help="write every row to this file")
    ap.add_argument("--jsonl", help="append each row here as it finishes, "
                    "so a kill mid-run (this host also runs three live bots; "
                    "a real cart cycle can start at any time) loses only what "
                    "has not run yet, not everything already measured")
    ap.add_argument("--start-at", type=int, default=0, help="skip the first N suite messages (resume)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    backends = convobench.BACKENDS if not args.only else {args.only: convobench.BACKENDS[args.only]}
    suite = convobench.SUITE[args.start_at:]

    def _append_jsonl(row) -> None:
        if not args.jsonl:
            return
        with open(args.jsonl, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "backend": row.backend, "id": row.message.id, "group": row.message.group,
                "text": row.message.text, "expected": list(row.message.expected_tools),
                "expects_question": row.message.expects_question, "tools": row.tools,
                "asked": row.asked, "correct": row.tool_correct, "unsafe": row.unsafe,
                "declined": row.declined, "seconds": round(row.seconds, 2),
                "model_calls": row.model_calls, "cost_usd": row.cost_usd,
                "reply": row.reply[:200], "error": row.error,
            }, ensure_ascii=False) + "\n")
            fh.flush()

    all_rows: dict[str, list] = {name: [] for name in backends}
    t0 = time.monotonic()
    for i, message in enumerate(suite, 1):
        for name, run in backends.items():
            try:
                row = run(message)
            except Exception as exc:  # noqa: BLE001
                row = convobench.Row(message, name, error=f"{type(exc).__name__}: {exc}")
            all_rows[name].append(row)
            _append_jsonl(row)
        print(f"\r{args.start_at + i}/{len(convobench.SUITE)} messages", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)

    session_rows = []
    if not args.skip_session and (not args.only or args.only == "agent_stateless"):
        print("running the agent_session mini-conversation...", file=sys.stderr)
        session_rows = convobench.run_agent_session(convobench.SESSION_FLOW)

    elapsed = time.monotonic() - t0

    for name, rows in all_rows.items():
        s = convobench.summarise(rows)
        print(f"\n=== {name}  (n={s['n']}, {elapsed:.0f}s total wall clock for the run)")
        print(f"  tool correct        {s['correct']}/{s['n']}")
        print(f"  asked a question    {s['asked']}")
        print(f"  unnecessary Q       {s['unnecessary_question']}")
        print(f"  missed a needed Q   {s['missed_question']}")
        print(f"  unsafe tool         {s['unsafe']}")
        if name == "shortcut":
            print(f"  declined (safe)     {s['declined']}")
        print(f"  errors              {s['errors']}")
        print(f"  seconds (total/med) {s['seconds_total']} / {s['seconds_median']}")
        print(f"  model calls         {s['model_calls']}")
        if s["cost_usd"]:
            print(f"  cost (api-equiv $)  {s['cost_usd']}")

    if session_rows:
        s = convobench.summarise(session_rows)
        print(f"\n=== agent_session  (n={s['n']}, one persistent conversation)")
        print(f"  tool correct        {s['correct']}/{s['n']}")
        print(f"  seconds (total/med) {s['seconds_total']} / {s['seconds_median']}")
        print(f"  cost (api-equiv $)  {s['cost_usd']}")
        for r in session_rows:
            print(f"    [{r.message.id}] {r.message.text!r} -> {r.tools or ('ASK: ' + r.reply[:60])}"
                  f"  {'OK' if r.tool_correct else 'WRONG'}")

    if args.verbose:
        print("\n=== every row")
        for name, rows in all_rows.items():
            for r in rows:
                mark = "OK" if r.tool_correct else ("DECLINED" if r.declined else "WRONG")
                print(f"[{name:16}] [{r.message.group:8}] {r.message.text[:40]:40} "
                      f"-> {r.tools or ('ASK' if r.asked else '—')}  {mark}"
                      + (f"  ERROR:{r.error}" if r.error else ""))

    if args.json:
        payload = {
            name: [
                {"id": r.message.id, "group": r.message.group, "text": r.message.text,
                 "expected": list(r.message.expected_tools), "expects_question": r.message.expects_question,
                 "tools": r.tools, "asked": r.asked, "correct": r.tool_correct,
                 "unsafe": r.unsafe, "seconds": round(r.seconds, 2), "model_calls": r.model_calls,
                 "cost_usd": r.cost_usd, "reply": r.reply[:200], "error": r.error}
                for r in rows
            ]
            for name, rows in all_rows.items()
        }
        payload["agent_session"] = [
            {"id": r.message.id, "text": r.message.text, "expected": list(r.message.expected_tools),
             "tools": r.tools, "correct": r.tool_correct, "seconds": round(r.seconds, 2),
             "cost_usd": r.cost_usd, "reply": r.reply[:200]}
            for r in session_rows
        ]
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
