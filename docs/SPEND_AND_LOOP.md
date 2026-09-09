# Spend log and thinking loop — this project's assessment

Written 2026-09-09 at Ishay's request, relayed through Miri with his
verbatim words: *"אני רוצה שתחלקי את ההתקדמות הזאת וגם את ה-spend log
עם הבוטים האחרים. תבקשי מהם לאמץ את ה-spend log (שיפנו אלי ואאשר)
ולבחון את עניין הלולאה באופן דומה כדי להמליץ לי אם כדאי אצלם גם ומה
דעתם."*

Miri's findings and measurements: `~/familyos/docs/spend-log-and-loop.md`.
Everything below was measured **here**, not taken on report.

## The premise needs correcting first

Miri's doc says only familyos makes paid model calls — *"רק familyos
מזכיר `OPENAI_API_KEY` בקוד (budget/grocery/portfolio — אפס)"*. The
OpenAI half is confirmed: `grep` for OPENAI across this project returns
nothing.

**But this project does make model calls, and a lot of them.** They go
through the `claude` CLI on Ishay's own subscription, which is why an
OpenAI-shaped search cannot see them. Four call sites in
`grocery_bot/nlu.py`:

| Call site | When it runs |
|---|---|
| `_ask_model` (via `parse_message`) | **every free-text Telegram message** |
| `expand_recipe` | a recipe request |
| `build_meal_plan` | a meal-plan request |
| `extract_recipe_from_text` | pasted/OCR recipe text |

`parse_message` has **no rule-based fast path**: the model is asked
first and the rule fallback runs only when the call fails. So every
message the household sends costs one CLI invocation.

**Measured here, three runs of a real classification**
("תוסיף חלב ולחם לרשימה"): **8.27s, 9.31s, 7.06s**. Consistent with
Miri's ~7s CLI overhead, measured independently.

**Consequence for Ishay's actual question.** He asked how much of the
$42.57 was bot versus development. The split Miri's report implies —
OpenAI is the bots, the Claude subscription is the five dev sessions —
**does not hold for this project.** Grocery's bot traffic sits inside
the Claude subscription pool, mixed with development. A report built
only on the OpenAI key would show grocery as costing nothing, which is
not true; it would show it as costing nothing *in dollars*, which is a
different statement.

## Decision (Ishay, 2026-09-09): loop yes, spend log not for now

*"Hybrid loop. No need for spend here for now."* The loop is built —
`grocery_bot/loop.py`, wired into `parse_message`. The spend log is
**not built and not to be built here** until he says otherwise; the
assessment below is kept because the reasoning still holds if it comes
back, and because the premise correction above matters regardless.

## Recommendation on the spend log: adopt, in a different currency

Worth having, with one design change. **The unit cannot be USD.** A
subscription has no per-call price, so a dollar figure here would be
invented — and an invented number is worse than no number, which is the
whole lesson of the four bugs below. The meaningful unit is **the call**:

    {"at": "...", "who": "ישי", "chat": "private",
     "site": "parse_message", "intent": "add_item",
     "seconds": 8.3, "fallback": false, "text": "תוסיף חלב ולחם"}

That answers "how much of the subscription is the grocery bot" by call
count and wall time, which is the honest available answer, and it is the
only place that question *can* be answered — no other project can see
these calls.

Miri's three design decisions carry over unchanged, and the third
especially: **a failure to write the log must never block a reply.**
Accounting that breaks an answer to the household is a worse bug than
missing accounting.

One addition specific to here: log `fallback`. When the CLI is
unavailable this bot silently degrades to rule-based parsing, and today
nothing records how often that happens. That is the same family as the
bugs below — a degradation nobody reads.

**Not built — Ishay declined it here for now (2026-09-09).** The
premise correction above still stands on its own: any spend report built
only on the OpenAI key shows this project at zero, which is true only in
dollars.

## Recommendation on the loop: yes, but only past the classifier

This bot's classifier is one `claude -p` returning one intent from a
fixed set. Miri's loop runs *before* the classifier and returns the same
decision structure, so handlers and gates are untouched — **the loop
proposes, only the dispatcher acts.**

**That property is a hard requirement here, not a nicety.** CLAUDE.md's
non-negotiable rule is that nothing may reach a checkout or payment
step. A loop that could act would put that boundary inside a model's
judgement. Miri's shape keeps it in the dispatcher, where it is code.
Any loop adopted here must keep that, and `add-to-cart` must remain
reachable only through the existing allowlist.

**Where a loop would actually help.** Not the common path — "תוסיף חלב"
is classified correctly today in 7-9s and a loop would only make it
slower. It would help exactly where this bot currently fails: an
ambiguous product term. The Tiv Taam flood of 39 disambiguation
questions was the bot asking because it could not check. A loop that
queries the catalogue before answering would resolve most of those
without asking.

**So the recommendation is a hybrid, not a replacement:** keep the
classifier for the common path, and invoke a loop only when the
classifier returns `unclear`. That preserves latency for the majority of
messages and adds thinking where the bot is currently weakest.

**Built 2026-09-09, `grocery_bot/loop.py`.** Measured end to end on the
live model:

| Message | Path | Result |
|---|---|---|
| "תזמין עכשיו הכל" | classifier only, 11.5s | `start_order` — loop never ran |
| "תוסיף חלב" | classifier only, 9.1s | `add_item` |
| "נגמר" | classifier `unclear` → loop, 21.3s | `unclear` **with a focused question**: "מה נגמר בדיוק? תגיד לי את שם המוצר" |

The last row is the whole point: before, that message produced a bare
shrug. The extra ~12s is paid only when the classifier has already
failed.

**The barrier, and why it is in code.** `loop.sanitise` refuses
`start_order`, `add_to_cart` and `shopped` whatever the model returns,
and refuses any intent not in `INTENTS`. `shopped` is on that list
because it refills both real carts — its blast radius is a cart, not a
log line. A refused proposal becomes an `unclear` that asks. Cart
actions remain fully reachable through the classifier, which is where a
clearly-worded request already lands.

**Two of Miri's measurements transfer directly.**

- **Preloading, 94s → 11s.** The same fix applies here and is cheaper
  than a loop: hand the loop the catalogue rows and the standing list
  rather than let it search for them. A lookup done in code is a lookup
  that cannot be skipped.
- **`--effort low` is destructive to a loop** — same message, three
  runs, three behaviours; `medium` was 3/3. This project passes no
  effort flag today (`nlu.py` passes only `-p`), so nothing is broken,
  but a loop must not be added with `low`.

## The four bugs were one bug

Miri lists `benefits-mall`'s JSON among them, correctly: an unrecognised
mall returned `{"chains": []}`, which to an automated consumer reads as
"no discounts here" — the single wrong answer that command exists to
prevent. Fixed the same day with an explicit `recognized` flag.

All four were *"something that looked checked and was not"*, and all
four were caught only because someone tested the case meant to fail. The
defence that worked was structural, never a prompt instruction: an
arithmetic barrier, preloading, and verifying against the source rather
than against an API's own success response.

Two more from this project the same day, same family:

- `except OSError: pass` around the benefits catalogue read. An
  unreadable catalogue rendered every chain with a dash and exited 0 —
  an entire mall reported as discount-free, looking like a normal
  answer.
- Reading `is_load_allowed` without checking the expiry. The flag was
  true at capture; the date passes on its own and the file never
  changes, so the 30% wallet would have been offered after 2026-09-30.
