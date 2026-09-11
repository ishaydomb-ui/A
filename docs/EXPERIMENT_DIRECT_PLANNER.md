# Experiment: understand the request, don't classify it

**Branch:** `claude/experiment-direct-planner`. Nothing here is on the
main branch, and the current understanding layer is untouched — both live
side by side so the same messages can go through each.

## The question

Ishay, 2026-09-11:

> האם מודל שמקבל את השיחה, ההקשר המשפחתי, מצב מחזור הקנייה וה-tools
> הרלוונטיים יכול להבין ישירות את הבקשה ולהפיק plan/action מובנה, בלי
> שהמערכת מנסה קודם לצמצם כל הודעה ל-intent קשיח.

The suspicion under it: **our own rules may be part of the friction.**
Every message is squeezed into one of thirteen intents, and anything that
does not fit becomes `unclear` — which reads to the household as the bot
not understanding, when in fact the bot understood and the taxonomy had
no slot for it. Every shape we have added since — several requests in one
message, a correction that refers to the previous turn, "just this once"
— needed its own rule, because the shape was fixed first and the language
second.

## What changes, and what explicitly does not

| | current | direct |
|---|---|---|
| understanding | message → one of 13 intents | message + state + tools → plan |
| several requests | needed a rule (`actions`) | a plan has several steps |
| follow-ups | needed a rule (`change_quantity`) | context is in the prompt |
| second pass | `loop.reconsider` when unclear | none — one call |

**The deterministic layer does not move.** The model proposes; the code
disposes:

- **No checkout, no payment.** Not refused — *absent*. There is no tool,
  so no plan can contain one. `FORBIDDEN` lists the names a model might
  reach for, so a refusal is logged as a refusal.
- **List and cart stay separate.** They are different tools, with no
  argument that could mean either. A plan has nowhere to blur them.
- **Quantities, product identity, prices, sessions, idempotency** stay in
  code. The model says what the household asked for; the existing matcher
  finds the product, the feeds give the price, the adapters hold the
  session.
- **Cart changes go through the same adapters.**
- **A real ambiguity still stops and asks.** A plan may carry a question,
  and a question suspends the steps after it.

`planner.validate()` is the barrier, applied to whatever comes back
regardless of what the prompt asked for — same principle as
`loop.sanitise`: the prompt is a request, the validator is the rule.

## How to run the comparison

    python scripts/compare_understanding.py --verbose

Both paths are run **plan-only**: nothing is added to a list, nothing
touches a cart, no adapter opens. Measuring understanding by filling a
real cart twenty-five times would be its own kind of mistake.

The fixture set is in the script — 25 messages in five groups: ordinary
ones as a control, two-requests-in-one, follow-ups that mean nothing
without context, partial completion ("סיימתי בשופרסל, בטיב טעם עוד לא"),
and loose phrasings that fit no slot cleanly ("כמו בפעם שעברה אבל
לאירוח").

Both paths get the **same** context, because the follow-ups are
unanswerable by any path without it.

## Reading the result

The overall winner is the least interesting number. What decides which
rules come back is the disagreement list: messages one path gets and the
other does not.

- The current path getting something right that the direct path fluffs →
  that rule earned its place.
- The reverse → that rule was costing us.

Latency and model calls are reported because they are a real cost: one
planner call is a bigger prompt than one classifier call.

## Early observations (2026-09-11, first runs)

Two single messages, before the full sweep:

- `"תוסיף חלב וכמה עולה טחינה?"` → `add_to_list(חלב)` +
  `price_check(טחינה)`. The current path needed a dedicated `actions`
  rule to do this; here it falls out of having a plan at all.
- `"בעצם שניים"` → `add_to_list(קוטג 5% תנובה 250 גרם, quantity=2)`.
  Understood — but arguably the **wrong tool**: that cottage is already
  in the cart, so `set_cart_quantity` is the right step and this would
  add a second line. Exactly the kind of finding the comparison is for,
  and a candidate for one rule worth keeping.

Latency measured at 17s and 30s against the classifier's usual 7–10s.

## Decision, when there is one

Ishay: *"רק אחרי שנראה את התוצאות נחליט אילו rules להחזיר."* Nothing is
removed from the current path until then. The result of this experiment
is a table and a recommendation, not a merge.
