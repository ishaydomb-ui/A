# Brief for external review: friction in the *user's* process

**Audience:** an AI reviewer with no access to this code, this repository,
or the household's store accounts. Everything needed to reason is below.
**What we want back:** a critique of the human workflow, not the
implementation. Where a recommendation depends on a fact we have not
given you, say which fact and how you would obtain it.

A previous review of this system covered architecture, matching and deal
policy, and was useful. This brief deliberately narrows to one question:
**what does this system still cost the person using it, and what is the
smallest change that would cost them less?**

---

## 1. The system in one paragraph

A Telegram bot that fills online supermarket carts for one household in
Tel Aviv. Two chains: Shufersal (the primary, years of order history) and
Tiv Taam (added 2026-08-31). It reads Israel's mandated price-transparency
feeds for both chains, drives a real logged-in browser session to search
and add items, and stops. **It never completes a purchase, never enters
payment details, and never touches account settings** — the user reviews
the cart and pays by hand. That boundary is fixed and is not a subject for
review.

The user operates it almost entirely from a phone, in Hebrew, mostly
unattended. A second adult in the household (referred to below as the
partner) shops from the same cart but does not use the bot.

## 2. The workflow as it actually runs

```
during the week   user messages the bot in free text ("we're out of milk")
                    -> parsed, queued as an ad-hoc request
a shop completes  -> cart is refilled from a standing list + promotions
                    -> a summary is sent
before paying     -> user opens the store site, reviews, deletes, pays
```

Two list shapes feed it, deliberately different:

- The **order cycle** fills from a curated list — 55 rows today.
- The **standing refill** fills from "everything bought in the past
  year" — 147 products, rare ones included.

The design premise, stated by the user after watching a real order run:
*under-buying costs a missing staple all week; over-buying costs one tap.*
So the cart is kept full and finishing a shop is meant to be **deleting**,
not adding.

When a search term matches several products the bot cannot separate
("cottage cheese 5%" → six variants), it asks — an inline-button question
per term. An answered question is remembered per chain and never asked
again.

## 3. Decided, and not open — challenge only with evidence

These were decided by the user with reasons, after seeing the
alternatives. Treat them as constraints. If you think one is wrong, say
so explicitly and say what evidence would settle it — do not route around
it silently.

1. **Fill-and-delete**, not ask-and-add (2026-09-06).
2. **Removals are reported, never learned from** (2026-09-07). An earlier
   auto-demotion mechanism was offered and declined: a wrong demotion
   silently stops buying something the household needs, which is the
   expensive direction of the trade.
3. **Deals are wanted in the grocery context**, including on products
   never bought before, with a tighter cap and a higher discount bar. (A
   household rule about "don't buy junk on sale" applies to credit-card
   leisure benefits, not to groceries.)
4. **The user reports the end of a shop in his own words**; it is not
   inferred from store state.
5. **No Telegram Mini App before ~October 2026.**
6. Where a comparable alternative exists, prefer **price-controlled
   products** (מוצרים בפיקוח) as a tie-break.

## 4. The measured friction surface

All figures below are read from the live database on 2026-09-11, not
estimated.

**Variant questions queued for the user**

| | |
|---|---|
| questions ever created | 143 |
| ever answered | 27 |
| still open | 116 |
| would actually be sent at the next cycle | **80** |
| of those, Tiv Taam / Shufersal | **73 / 7** |
| distinct terms (i.e. no duplicates) | 106 |

Created per day: 19, 15, 9, 9, **36, 55** (29–30 Aug, then 7–10 Sep). The
rate is **rising**, and the rise coincides with the second chain becoming
active.

**What works**

| | |
|---|---|
| remembered product choices | 660 (341 Shufersal, 319 Tiv Taam) |
| products with purchase history | 399 (**305** Shufersal, **94** Tiv Taam) |
| ad-hoc requests ever received | 109, all resolved |
| real orders logged | 21, Shufersal only, Jan 2025 – Sep 2026 |

**A partial fix we tested and rejected as insufficient:** 32 of the 80
open questions already have an answer at the *other* chain — but in 14 of
those the remembered name is identical to the search term, so it
separates nothing. Only **18** questions could close that way.

**What the end-of-cycle message now contains**, in order: items added ·
items auto-chosen from habit · items added because of a promotion ·
promotions needing a second unit (reported, not bought) · not found ·
items failing repeatedly across runs · a gift-threshold check · a waste
question · a benefits-card prompt.

**Instrumentation we do not have:** anything about the user's side. No
time-in-app, no tap counts, no record of which suggestions were deleted
before paying beyond a cart diff, and no measurement of how long a shop
takes him now versus before.

## 5. The questions

### Q1 — Is per-item disambiguation the wrong interaction model?
80 queued questions, rising, 106 distinct terms, 27 ever answered in two
weeks. The mechanism is individually defensible (each question is real
ambiguity; an answer is permanent) and collectively failing.

Is the correct move (a) answer them automatically under a rule and report
the choices for one-tap correction, (b) batch them into one review
surface, (c) drop the question and let the cart carry a default the user
deletes — consistent with fill-and-delete — or (d) something else? What
would make (a) safe: what rule, and what is the failure it must not have?

### Q2 — Cold start on a second chain
305 products with history at chain A, 94 at chain B. Product codes,
package sizes and own-brands differ between chains, so a remembered
*choice* does not transfer — only the *intent* behind it might.

Is transferring intent by product name sound, or a category error that
will produce confident wrong picks? If sound, what is the minimal
representation of "what the household means by חלב" that survives across
chains — and how would you validate it without the user grading 106
terms?

### Q3 — Is the deletion premise actually true?
The whole design rests on "deleting is cheaper than adding." We have never
tested it. We *can* observe what was put in and what was in the cart later,
so removals are measurable; we cannot observe effort, time, or annoyance.

Design a test we can actually run from this data that would falsify the
premise, or tell us it cannot be falsified without instrumenting the user
and say what the minimum viable instrumentation is.

### Q4 — The request lifecycle is binary and should not be
An ad-hoc request carries one flag: consumed, yes or no. There is no
distinction between *added to a cart*, *ordered*, and *delivered*. The
user therefore cannot ask "did that actually arrive," and a refill
immediately after a shop can collide with an order still in flight.

Complicating fact: a placed order does not appear in the chain's own
order history for roughly **36 hours** (measured). A confirmation email
arrives within minutes, but the project holds no mailbox credentials.

What state machine do you propose, what triggers each transition given
that 36-hour blind spot, and which transitions should be visible to the
user at all rather than internal?

### Q5 — Unasked-for additions
The bot adds a capped number of promotional items nobody requested,
including a few products never bought before. The household's stated
preference is for this. The failure mode is waste, and our waste table
has **zero rows** — the reporting mechanism is built and has never
received a single report.

How do you evaluate an intervention whose cost is invisible by
construction? Is there a proxy for waste that does not depend on the user
volunteering data they have never once volunteered?

### Q6 — Conditions that exist only in free text
Both chains encode real purchase conditions in the promotion's
description string and nowhere in structured fields. Measured on the live
feed: 3,650 of 13,087 active promotions at one chain are worded
multi-buy, and **1,530 of those carry a quantity field of 1**. Another
chain marks "valid above a ₪150 basket" as the suffix `-מות150`, and a
leading bare number in *its* description is a price, not a quantity.

We currently parse the text conservatively and refuse what we cannot
confirm. The cost of a false positive is a promised saving that does not
happen; the cost of a false negative is a missed deal.

Is conservative text parsing the right call, or should any promotion with
an unparsed condition be excluded entirely? How would you set that
threshold without access to the till receipt?

### Q7 — Proving the thing works
The previous review's sharpest line was that correct classifications do
not prove the cart is good. We agree, and we do not know what to
instrument. Suggest a small number of metrics that (a) can be collected
from a Telegram bot plus a cart diff, (b) would change a decision, and
(c) do not require the user to fill anything in. Include what you would
*stop* measuring.

### Q8 — Message volume at the end of a cycle
Nine distinct blocks now arrive when a cycle finishes (listed in §4). Each
was added for a real reason and each has a defender. Which of them should
not be in the completion message, and what is the rule for deciding —
given that this is read on a phone, in Hebrew, usually once?

### Q9 — Making "reported, never learned" actually useful
Removals are recorded and reported monthly, by the user's explicit
decision. In practice a monthly list of "things you keep deleting" is easy
to ignore. Without crossing into automatic demotion, what presentation of
that data would actually cause a decision?

### Q10 — Where the second person fits
The partner shops from the same cart and does not use the bot. Requests,
removals and the final review are therefore not all the same person's.
Nothing in the system models this. Is that a real gap or a distraction,
and what is the cheapest thing that would make it not a problem?

## 6. Ground rules for your answer

- Distinguish **what you verified**, **what you inferred**, and **what you
  are assuming**. The last review's strongest finding came from a correct
  inference; its two weakest came from inferring a gap that our own
  document had merely failed to describe.
- Rank your recommendations by expected reduction in user effort per unit
  of implementation risk, and say which one you would do **first**.
- For each recommendation, state the observation that would tell us it was
  wrong.
- Where a recommendation conflicts with something in §3, say so
  explicitly.
