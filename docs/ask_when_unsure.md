# Ask-when-unsure — where merchant filters live, rank vs ask

Opinion for Arthur's `2026-09-04-ask-when-unsure.md`, per Ishay's
direction: the model should know when confidence is low, ask, and let the
household refine in free text. Two design answers, both grounded in
machinery this project already runs. **Opinion, not implementation.**

## The trigger is answer-divergence, not candidate-count

Arthur's draft: "more than one candidate → ask." I'd sharpen it, because
a flat count over-asks and over-asking on a clear question is itself a
failure (Ishay). **Ask when more than one candidate would produce a
*different answer to the question asked* — not merely when more than one
matches.**

- "קוטג' 5%" → בעלז / שטראוס / טרה. Three brands, but it's a **price**
  question and the answer is "cheapest is בעלז ₪6.40." Candidates agree on
  the shape of the answer → **rank/return, don't ask.**
- "פוקס" → פוקס (fashion, ₪5,000 cap) / פוקס הום (homeware, ₪2,500) /
  פוקס דרי (dairy, unrelated). A **benefit** question where the wallets
  and rates differ per candidate → picking wrong is silent-wrong →
  **ask.**
- "אמריקן" → איגל (fashion) / פיצה / בורגר. Different categories entirely
  → **ask.**

Operationally: resolve the top candidates, compute the answer for each; if
they agree, answer; if they diverge and no candidate is an **exact-name
match** (the user typed the whole name → resolved), ask — presenting the
candidates as options the household can also refine in free text. This
ties the question to real confidence (does the answer change?) and
directly minimises both measures: silent-wrong (ask when it would differ)
and over-ask (never ask when the answer is stable).

## Normalization comes first — a form-miss is not a not-found

The case-sensitivity and apostrophe/geresh gaps found today are a
**different failure** and must be handled *upstream* of rank-vs-ask: a
"Terminal X" or "קוטג׳ 5%" that returns nothing is "I didn't understand
the form," masquerading as "doesn't exist." Fold case and apostrophe
variants *before* deciding found/one/many. Only then does 0 mean genuine
not-found, 1 means resolved, >1-divergent means ask. Without this,
ask-when-unsure never fires because the miss looks like a clean zero.

And the 77% address gap is the same principle on the answer side: a
`benefits-branches` miss must say **"address unknown,"** never read as "no
benefit." Absence of data is not a negative answer.

## Where the disambiguation memory lives: here, and it already exists

I own the merchant/product vocabulary, so the resolution memory lives in
grocery-automation — and **it should reuse the mechanism that already
works**, not a new subsystem:

- `preferred_products` + `remember_choice(term → product)` already turns
  "קוטג" into the exact cottage this household buys, so the grocery bot
  stops re-asking (309 Shufersal / 292 Tiv Taam choices today).
- `pending_ambiguities` + the variant chooser already are the ask-then-
  remember loop, with buttons.

So when Ishay says "no, I meant פוקס הום," that is the **same primitive**:
store `term "פוקס" → merchant "פוקס הום"` (a `preferred_merchants` table,
or `preferred_products` with a `benefits` scope), written **here, on
Ishay's confirmation**, so next time "פוקס" resolves without asking. The
free-text correction becomes a durable filter exactly as a grocery
variant choice does.

**Recommendation:** do not build a new disambiguation system. Extend the
term→resolution memory and the ambiguity-chooser this project already
runs, from grocery variants to benefit merchants, gated on the
answer-divergence trigger and fed by normalized input. The pieces exist;
the work is wiring them to the benefits seam and adding the merchant
memory table — which is Ishay's call to approve, not a mid-cycle fix.
