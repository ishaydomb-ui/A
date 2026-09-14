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

---

# Results — first full sweep, 2026-09-11

25 messages, both paths, plan-only. Raw output:
`scripts/compare_understanding.py --verbose --json`.

## The headline numbers

| | current | direct |
|---|---|---|
| produced an action or a question | 21/25 | **23/25** |
| total steps produced | **25** | 24 |
| stopped to ask instead | 4 | 4 |
| model calls | 29 | **25** |
| total seconds | **424** | 546 |
| median seconds | **17.0** | 18.0 |
| worst single message | 35.3s | **74.0s** |
| refusals by the validator | — | **0** |

Two of these matter more than the rest.

**Model calls went *down*, 29 → 25.** The current path spends a second
call whenever it gives up, which is precisely when the household is
already confused. One call that understands beats two that do not.

**Latency went up 29%**, and the tail is much worse: 74s on a
two-request message, 54s on "את כל השאר כרגיל". The median is nearly
identical (17.0 vs 18.0), so this is a tail problem, not a baseline one —
and a 74-second wait is its own kind of not-understanding.

**The validator blocked nothing in 25 messages.** No forbidden tool, no
invented tool, no absurd quantity. That is one sweep, not proof, but
there is no evidence here of a model straining at the barrier.

## What the counts hide

"Understood" in the table means *produced something*. It does not mean
*produced the right thing*, and both paths have confident wrong answers.
Reading every row by hand, my judgement:

### Where the direct path is clearly better

| message | current | direct |
|---|---|---|
| את זה רק הפעם | `add_item` — would file "זה" as a product | `remember_preference(scope=once)` |
| כמו בפעם שעברה אבל לאירוח | `add_item` — files the whole sentence | asks what is meant |
| בלי הטחינה הזאת, אחרת כן | `unclear` | `remove_from_list(טחינה גולמית)` |
| את כל השאר כרגיל | `unclear` | `fill_cart()` |
| בעצם שניים | `change_quantity` | `set_cart_quantity(..., store=shufersal)` — knew it was already in the cart |
| סיימתי בשופרסל, בטיב טעם עוד לא | `shopped` | `report_shopped(store=shufersal)` |

The first two are the important ones. The current path does not merely
miss them — it **files a sentence as a grocery item**, which is the exact
failure the NLU layer was built to end, still alive in the corners of the
taxonomy. The direct path asks instead.

### Where the current path is clearly better

| message | current | direct |
|---|---|---|
| נגמר הקוטג | `add_item` | **nothing at all** |
| תעשה 3 | `change_quantity` | asks |
| השני במקום הראשון | `replace_item` | asks |
| לא זה, השני | `replace_item` | `replace_in_cart(new="השני")` — "the second" as a product name |

"נגמר הקוטג" is the one that should stop a merge on its own: it is the
plainest phrasing in the whole set, the control case, and the direct path
returned neither a step nor a question. Silence is the worst outcome
available — worse than a wrong guess, which at least shows up.

### Where the direct path over-reached

"תוריד את הלחם ותוסיף פיתות" produced **three** steps:
`remove_from_cart` + `remove_from_list` + `add_to_list`. Nobody asked for
a cart removal. This is the shape of the risk in this design: given tools
and a goal, a model helpfully does more than it was asked, and "more"
here means touching the real cart. It is also the slowest row at 74s.

The deterministic layer contained it — the removal would have gone
through the adapter, been reported, and been visible — but *containment
is not correctness*.

## What I take from this

1. **The taxonomy is genuinely costing us.** Four of the 25 messages fail
   in the current path not because the language is hard but because there
   is no slot, and two of those four fail by filing a sentence as food.
   That is not a prompt problem and no amount of rule-writing has fixed
   it, because each new rule is another slot.
2. **The direct path is not ready to replace it.** A control-case silence
   and a 74-second tail are both disqualifying on their own.
3. **The rules worth keeping are the terse follow-ups.** "תעשה 3",
   "השני במקום הראשון" — short, high-context, and the explicit
   `change_quantity` / `replace_item` intents handle them while the
   open-ended planner hesitates.
4. **The most promising shape is neither of these two.** Keep the fast
   classifier for the cases it gets right in 7–10 seconds, and send what
   it cannot place to the planner instead of to `unclear` — which is what
   `loop.reconsider` already does structurally, except that it hands the
   hard cases to a *second classifier* rather than to something that can
   actually plan. That is one call for the easy majority and one call for
   the hard minority, which is where the model-call count went down.

## What this does not tell us

- One sweep, one fixture set, written by me. A message set drawn from
  real transcripts would be worth more than another run of this one.
- Plan-only: nothing measures whether a *correct plan* becomes a correct
  cart. The product matcher sits downstream of both paths and is where
  "חלב" becomes a specific carton.
- Nothing here measures the household's effort, which is the thing the
  whole review said to measure.
