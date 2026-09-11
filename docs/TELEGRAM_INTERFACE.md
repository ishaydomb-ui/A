# The Telegram interface, end to end

What the household types, what the bot says back, in what form, and when.
This is the conversation design — not the code paths. It is written down
because it was never designed: it accreted one message at a time, each
addition sensible on its own, until a single cycle could produce eighty
notifications. `docs/reports/2026-09-11-ux-audit-response.md` has the
audit that forced the question.

## The one rule

**One message per cycle event, never one per item.**

Anything that scales with the length of the shopping list gets counted,
paged, or put behind a button — never enumerated as separate messages. A
person reads this on a phone, in Hebrew, usually once, while doing
something else.

Two corollaries, both learned the expensive way:

- **A gap is never behind a button.** A product that was not found, a
  line the bot declined to put back, an error — these stay in the first
  message. A short summary that sends someone to a cart quietly missing
  the thing they asked for is worse than a long one.
- **A number is only shown when it is known.** A partial sum presented
  as a total is this project's recurring failure; the line is omitted
  instead.

## Who speaks first

The household does, nearly always. The bot initiates in exactly three
situations: a scheduled reminder before the expected shopping day, a
deferred cycle finally running when the Israeli exit node comes back, and
the nightly digest. Everything else is a reply.

---

## 1. During the week — the household writes

Free text, in Hebrew, no commands needed. "נגמר החלב", "תוסיף קוטג
וגבינה צהובה", "כמה עולה טחינה", "מה יש במבצע", "סיימתי לקנות".

The message is classified locally first; anything the classifier is
unsure of gets one second pass with the standing list and pending items
loaded, and that second pass is **forbidden from producing a cart
action** — an unclear message can become a question or a price lookup,
never an unrequested purchase.

**The bot replies once**, and the reply says which of two things
happened, because they are easy to confuse and the difference matters:

- *"נוסף לרשימה"* — queued for the next cycle. Nothing is in a cart.
- *"נוסף לסל בשופרסל"* — it is in the real cart now.

Commands exist for things free text cannot express compactly. The ones
that matter: `/start_order`, `/done`, `/requests`, `/questions`,
`/autochoice`, `/basket`, `/deals`, `/digest`.

## 2. The cycle runs — one message, edited

A cycle is minutes of page loads. Instead of silence or a stream of
updates, **one message is sent and then edited in place** as items
resolve. Edits are throttled, because Telegram rate-limits repeated edits
to a single message and a forty-item run would exceed it.

This is the pattern the whole interface follows: a long-running thing
owns one message and rewrites it.

## 3. The cycle ends — the decision screen

One message. Counts and money on one line each; the full list behind a
**"📋 מה נוסף ולמה"** button.

```
שופרסל
✅ 34 נוספו · ❓ 3 בחירות · ⚠️ 2 לא נמצאו
🏷️ 5 מבצעים · חיסכון 43.20₪
⚠️ לא נמצא: טחינה גולמית (4 מחזורים), שמן שומשום
   נשאר ברשימה — אנסה שוב בפעם הבאה.
✋ לא הוחזרו — הוסרו מהעגלה ידנית: חלב 3%
```

Everything under the counts is a gap and stays visible. A repeated
failure marks the item — "(4 מחזורים)" — rather than forming a second
block naming the same product twice.

The detail behind the button is written to storage, not held in memory:
the button may be tapped tomorrow, a restart in between is ordinary, and
it is the only surviving record if the message itself fails to send —
which has happened on a real order.

## 4. Deals — said with their conditions

A promotion the bot acted on appears with the arithmetic done and the
condition named:

```
🏷️ נוספו בגלל מבצע חריג (5) — מחקו מה שלא צריך:
   • דבש טבעי לחיץ 250 גרם — -70% · 5.00₪ במקום 16.90₪
     (חיסכון 11.90₪) · מותנה בקנייה מעל 150₪
   • ברוקולי קפוא 800 גר — 2 יחידות ב-37.35₪ במקום 49.80₪
     (חיסכון 12.45₪)
```

A multi-buy is stated **in units, not unit prices**: "2 יחידות ב-37.35₪"
is what the till will show, and a per-unit figure would hide the one
thing a person needs to see before agreeing — that the outlay went up.

Promotions whose condition cannot be read are reported and not bought,
with the chain's own wording passed through verbatim. The two chains mean
opposite things by the same field, so a computed figure would be wrong
half the time.

## 5. Choices — one message, paged

When several products match a term and none is clearly right, the bot
asks. **The whole set is one message**, edited forward as it is answered:

```
❓ 3 בחירות — 1 מתוך 3
בצל — איזה מהם?
1️⃣ בצל יבש ארוז · 5.90₪ 💰
2️⃣ בצל אדום · 7.90₪
3️⃣ בצל שאלוט · 12.90₪
[1️⃣] [2️⃣] [3️⃣]  [דלג]
```

- 💰 marks the cheapest candidate. Nothing is pre-selected.
- A tap **adds that variant immediately and leaves the question open** —
  a household genuinely buys both the 5% and the 3% cottage cheese in one
  shop. The chosen line turns into "✅ … — נוסף לסל" and loses its button.
- "סיום" closes the question and the message becomes question 2 of 3.
- The first pick becomes the remembered default for that term. A second
  pick in the same question is an addition, not a correction, and does
  not overwrite it.
- A question answered once is never asked again — 660 such choices are
  remembered today.

**Only questions this cycle actually raised are pushed**, capped at 8.
Anything older is counted in one line pointing at `/questions`. Before
2026-09-11 the queue was filtered by nothing at all: a Shufersal-only
shop ended with 80 separate messages, 73 of them about Tiv Taam terms
nobody had asked about that day.

`/autochoice` offers to close the subset a rule can answer — it previews
what it would do and applies nothing until it is tapped, because a rule
nobody chose is a rule nobody can predict.

## 6. The hand-off — reviewing and paying

The bot fills carts and stops. It never checks out, never enters payment
details, never touches account settings. The hand-off message carries the
cart total read from the store itself (not summed locally, which would
disagree with what is actually payable) and a button straight to each
chain's cart page.

Two things may follow it, each only when there is something to act on:

- **Gift threshold.** "₪38 short of the ₪599 gift" — reported, never
  acted on. Buying something unwanted to reach a threshold is not a
  saving, and that decision is not the bot's.
- **One targeted waste question**, about a single product, with a
  cooldown so it rotates rather than nags, and as a button so an incoming
  message is never mistaken for an answer.

## 7. After the shop — the household says so

`/done`, or just saying it. **The household's word is the trigger**, by
explicit decision: order history is reliable but slow — a real order was
absent from the chain's own history for about 36 hours — and an empty
cart cannot distinguish a completed shop from a cart someone cleared.

On that word the bot: records what was removed before refilling wipes the
evidence, moves every request that reached a cart to "bought", refills
both carts from the standing list plus this week's promotions, and
reports once.

Requests carry a state from then on — **in a cart → you said it's bought
→ confirmed in the chain's order history → delivered** — visible with
`/requests`. Between the second and third, the honest line is
*"דיווחת שהקנייה הושלמה; ממתין לפירוט מהחנות"*. The chain's history
corroborates; it never overrides what the household said.

## 8. What the bot will not do in this conversation

- Complete a purchase, enter payment details, or open any checkout page
  beyond reading the cart total.
- Change account settings — address, phone, club membership, delivery
  defaults — even to fix something that looks wrong. It reports instead.
- Learn from a deletion. Removals are recorded and reported monthly so a
  person can decide; nothing is demoted automatically.
- Undo an edit a person made to the cart. A line someone removed is not
  put back in the same round, and it says so rather than leaving a silent
  gap.
- Claim a saving it cannot verify.

## Message budget, per interaction

| Interaction | Messages |
|---|---|
| An ad-hoc request during the week | 1 |
| `/start_order` | 2–5 (progress · summary · alternatives · choices · backlog line) |
| Cart hand-off | 1–3 |
| `/done` | 3–5 |

The number that matters is that none of these scales with the length of
the shopping list.
