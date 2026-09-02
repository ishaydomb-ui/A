# What מירי can ask גורדון

The contract between the household's assistant (`~/familyos`) and this
project. **Written from this side; the routing itself is Miri's to
implement, in her own session.** Nothing here changes without the user.

The seam is a CLI, deliberately — each project has its own virtualenv, so
a shared import would couple their dependency trees. A subprocess call
has neither problem.

    cd /home/codex/grocery-automation
    .venv/bin/python -m grocery_bot.cli <command> [args]

Set `GROCERY_BOT_DB_PATH=/home/codex/grocery-automation/data/grocery_bot.sqlite3`.
**Nothing below needs this bot's Telegram token, a store session, or the
Israeli exit node.** That is deliberate: familyos should not hold a
credential it has no use for.

Every command prints one short line (or a short block) on stdout and uses
exit codes: **0 = done, 1 = nothing matched, 2 = usage error.** Exit 1 is
a real answer ("not on the list"), not a failure to retry.

---

## The gap this document exists to close

Today Miri routes free text to `add-item` and card confirmations to
`confirm-card`. Everything else below already works and is already
called by nobody. On 2026-09-02 the user asked Miri whether a list of
deals was worth taking; Miri relayed the question to a person rather
than calling this CLI, and the answer never came.

The concrete consequence: **"תכנן לי תפריט שבועי" said to Miri today most
likely lands as a grocery list item called "תכנן לי תפריט שבועי".** The
meal-plan capability exists on this side and is not reachable from that
side.

---

## Commands

### Meal planning and recipes — the ones worth wiring first

The user named these as the most relevant to how he and Liran actually
talk. All three call a model (via the `claude` CLI), so they take
**roughly 10–40 seconds**. Miri should say something before waiting.

| Command | What it does |
|---|---|
| `meal-plan [request] [--by NAME] [--all] [--preview]` | Five weekday dinners plus one consolidated ingredient list |
| `recipe <dish> [--by NAME] [--all] [--preview]` | One dish → its buyable ingredients |
| `recipe-text [--by NAME] [--all] [--preview]` | Same, from recipe text on **stdin** — the OCR/screenshot/paste path |

**`--preview` adds nothing** and prints what *would* be added. Use it
when Miri wants to show the split and ask first. Without it, the
ingredients go straight onto the list. `--all` queues everything
including what the household probably already owns.

The default behaviour is the important one: **only what the household
probably lacks is added.** Ingredients are split against the pantry, and
the ones it likely has are printed with `~` instead of `+`. Blindly
queueing flour and sugar for a kitchen that owns flour and sugar
recreates the delete-by-hand chore this project exists to remove.

Real output, run 2026-09-02:

    $ meal-plan "משהו קליל, בלי בשר אדום" --by ישי --preview
      ראשון: פסטה ברוטב עגבניות עם קוביות טופו וקישואים
      שני: שניצל עוף בתנור עם פירה דלעת וגזר
      ...
    meal plan: תפריט שבועי
      + פילה עוף (לשניצל) (800 גרם)
      + ביצים (6 יחידות)
      ~ פסטה (פנה) (כנראה יש)
      ~ קוטג' 5% שומן (כנראה יש)
    preview only — nothing added (7 would be added)

The plan is grounded in the household's own recurring products, not a
generic healthy-menu answer — that is why it is worth calling this
rather than answering from the model directly.

### The list

| Command | Notes |
|---|---|
| `add-item <text> [--by NAME] [--qty N]` | An exact repeat folds onto the pending row and prints `already on the list` |
| `remove-item <text>` | Fuzzy match — `remove-item עגבניות` removes `עגבניות שרי`. Exit 1 if nothing matched |
| `list-items` | One per line, with who asked |

`--by` matters: it is what makes `🙋לירן` appear beside the item in the
cart view, so the household can tell a personal request from a standing
one.

### Prices and deals

| Command | Notes |
|---|---|
| `price <query>` | Current shelf price, promotions, and ₪/kg with 🏆 on the best value |
| `deals` | Live promotions on the household's standing list |

Both were behind the Telegram token until 2026-09-02 and are now
token-free — this is new, and the reason "כמה עולה קוטג" could not be
asked through Miri before.

### Cadence and the card

| Command | Notes |
|---|---|
| `nudge [--last-nudged YYYY-MM-DD] [--why]` | Prints the overdue-shop message, or **nothing** when a shop is not due |
| `confirm-card ["<reply>"]` | Records that the ₪700 card was loaded this month |

`nudge` is already wired and runs hourly from familyos. Printing nothing
is the normal case; a caller that treats empty output as an error will
send noise.

`confirm-card` takes the household's **raw reply** and decides what it
means — pass the text through rather than interpreting it. "לא הטענתי
עדיין" contains "הטענתי"; treating it as a yes would silence the
reminder for a month the card was never loaded. Verified: `"כן טענתי"`
records, `"לא עוד לא"` records nothing, no argument records.

Confirming here silences the question everywhere, including at this
bot's own cart hand-off — one record, not two agreeing behaviours.

---

## The one command that is different

    add-to-cart <text> [--qty N]

This reaches the **real store carts right now**, so unlike everything
above it needs the store sessions and the Israeli exit. It fills every
enabled chain (Shufersal and Tiv Taam today) and names each one that
took the item. If nowhere took it, the request lands on the list rather
than vanishing.

`add-item` puts something on the list for the next cycle; `add-to-cart`
reaches the cart that is open right now. "תוסיף חלב" is the first;
"תוסיף חלב לעגלה" is the second.

**It never checks out.** No adapter in this project has a checkout
method and a test fails the build if one appears.

---

## How the bot itself routes free text, for reference

In its own Telegram chat this project classifies every message into one
of twelve intents — `add_item`, `remove_item`, `price_query`, `deals`,
`show_list`, `recipe`, `meal_plan`, `start_order`, `add_to_cart`,
`report_waste`, `smalltalk`, `unclear`. Miri does not have to reuse any
of this; the CLI is the contract. But the mapping is the obvious one, and
`scripts/route_check.py` here holds 33 real phrasings with their expected
intents (33/33 stable as of 2026-09-02), which is a ready-made test set
if it is useful.

One finding from building it, worth passing on: **an indirect phrasing is
where routing breaks.** "מה נאכל השבוע?" flipped between `meal_plan` and
`unclear` across identical runs, because the prompt covered only
imperative forms. It passed on the first test and failed on the second.
Any routing added on the Miri side should be checked more than once per
phrasing.
