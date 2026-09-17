# Conversation-backend benchmark — Phase 11, run 2026-09-17

Mandate: compare the current classifier path, the direct planner, a
persistent Agent SDK session, and a minimal deterministic shortcut, on a
fixed suite of realistic household messages including every example the
mandate itself gave. Ishay lifted the five-clean-runs execution gate the
same day and asked for this to run now rather than wait: *"Proceed now
with the next product layer, including the conversation-backend
comparison and, if justified, a persistent Agent SDK implementation."*

Harness: `grocery_bot/convobench.py`, run via
`scripts/compare_conversation_backends.py`. Discipline unchanged from
the 2026-09-11 classifier/planner comparison: every backend runs
**plan-only** — nothing is added to a real list, no adapter opens, no
cart changes, whichever backend answers.

## The suite

30 messages (`grocery_bot/convobench.py:SUITE`), plus a 5-message ordered
mini-conversation (`SESSION_FLOW`) run once through a real persistent
session to test memory the injected-context messages structurally
cannot test. Includes, verbatim, every example message the mandate gave:
"תוסיף חלב", "תוסיף גם שני מלפפונים", "בעצם בלי המלפפונים", "לירן שלחה
מתכון — תכניס מה שחסר", "תכין קנייה לשבוע", an "X במקום Y" replace,
"מה חסר לנו?", and the brand-loyalty deals request.

## Results

Full suite, 30 messages, each backend given the same injected context
where the message needs one (`grocery_bot/convobench.py:_CONTEXT`):

| backend | tool correct | asked | unsafe | model calls | seconds (total/median) | cost (api-equiv $) |
|---|---|---|---|---|---|---|
| classifier (deployed) | **26/30** | 4 | 0 | 30 | 397.7 / 11.0 | not exposed by the CLI path |
| planner | **26/30** | 4 | 0 | 30 | 387.5 / 10.2 | not exposed by the CLI path |
| agent_stateless | **22/30** | 5 | 0 | **65** | 461.8 / 13.2 | **$0.81** |

`agent_session` — a real persistent conversation, 5 messages, **no
injected context at all** (unlike the three rows above, everything it
got right came from its own memory of the conversation):

| id | message | result | correct |
|---|---|---|---|
| s1 | תוסיף חלב | add_to_list | ✅ |
| s2 | תוסיף גם שני מלפפונים | add_to_list (a second item, not overwriting the first) | ✅ |
| s3 | בעצם בלי המלפפונים | remove_from_list | ✅ |
| s4 | מה חסר לנו? | show_list | ✅ |
| s5 | תחליף את החלב בחלב עמיד | remove_from_list + add_to_list | graded WRONG, see note |

5 messages, $0.3159 total, 26.1s total. The s5 "WRONG" is the
benchmark's own ground truth being arguably too strict, not a model
error: milk had only ever been *added to the list* in this
conversation, never to a real cart, so decomposing "replace" into a
list-side remove+add is the semantically correct action for the actual
state — `replace_in_cart` would have been wrong to call on something
that was never in a cart. Read as intended, this is 5/5.

**deterministic shortcuts**, same 30 messages, zero model calls: matched
only the 8 unambiguous `control` messages of the "plain, bare add" shape
and correctly declined all 22 others (corrections, multi-request
messages, anything with standing context) rather than guess. **8/30
(27%) of this suite could skip a model call entirely without any loss
of correctness** — the honest ceiling for how much a shortcut layer
could realistically take off the other backends' load, not a
competitor to them.

### Where agent_stateless actually lost points, and why it matters

Two of its eight wrong answers are not just "wrong" — they are wrong in
the *more expensive direction*: `m3_correction` ("בעצם בלי המלפפונים")
and `multi_remove_add` ("תוריד את הלחם ותוסיף פיתות") both called
**`remove_from_cart`/`add_to_cart`** where the correct answer was the
list-side tool. Tellingly, the exact same correction (`s3` above) was
handled *correctly* by `agent_session` with no injected context at
all — the difference is the injected `_CONTEXT` dict used for the
stateless rows describes an active Shufersal cart, and the model reads
that as license to act on the cart rather than the list. This is a real
finding about the model's behaviour under this system prompt, not a
harness bug: **given ambiguous standing context, the agent's default
skews toward the tool with the larger blast radius**, which is the
opposite of the caution the classifier's prompt was written to have
("רשימה מול עגלה/סל" is one of its most emphasised rules, added after a
real confusion cost a day of a live order — see HANDOFF 2c). None of
these calls were unsafe by the strict definition (every tool called was
in the real catalogue, never forbidden), but a system prompt this
opinionated should not need the injected context to be this careful to
keep the model on the safer side.

**Fixed the same day, then re-tested live, honestly — not a full win.**
`agentconvo.py` now makes cart tools callable **only when the current
message itself** names the cart or a store (`_mentions_cart`), never
from injected background — enforced by dropping them from
`allowed_tools` for a one-shot call, or by a `can_use_tool` permission
hook checked against the turn's own text for a persistent session
(`ClaudeAgentOptions.can_use_tool`, since a session's options are fixed
at connect time and its message changes every turn). Re-run live after
the fix:

- `m3_correction` ("בעצם בלי המלפפונים") — now **correctly**
  `remove_from_list`. The exact bug this section describes, fixed.
- `multi_remove_add` ("תוריד את הלחם ותוסיף פיתות") — **regressed to no
  action at all.** With the cart tools no longer even offered, the model
  tried them anyway, found nothing to call, and gave up rather than
  falling back to the list-side tools that were still available and
  correct: *"נתקלתי בחסימת הרשאות של המערכת ולא הצלחתי להסיר את הלחם
  מהעגלה או להוסיף פיתות."* Before the fix this message at least
  dispatched something (the wrong tool); after it, nothing happens and
  the household is told about a permissions error that means nothing to
  them.

So the honest state of this: the restriction resolves the single-item
correction cleanly, but for a bare "תוריד X ותוסיף Y" with no cart or
list word at all, the model's own default reading leans cart-ward
strongly enough that removing the option makes it give up rather than
reach for the correct alternative sitting right next to it. The
classifier's prompt has an explicit, unambiguous default for exactly
this shape ("תוריד X" without naming the cart/list always means the
list) that this agent's prompt states but does not reliably follow. A
full 30-message re-score after this fix was **not** run today — the
point above is already made by these two concrete, reproduced cases,
and re-running the whole suite a third time would spend more of the
same shared weekly allowance this section just showed is not free, for
a number this report does not need to make its point.

### An operational fact this run surfaced, unplanned

Partway through `agent_stateless` (after ~21 of its own calls), the
Anthropic subscription this project already shares with the classifier,
the planner, and the household's other bots returned **"You've hit your
weekly limit · resets 4pm (Europe/Berlin)."** The plain `claude -p` CLI
call the classifier and planner use kept working throughout (checked
directly, and confirmed against the production bot's own journal — no
`NLU: model unavailable` fallback lines in that window) — this was
**specific to the Agent SDK path**, not a subscription-wide outage, and
had recovered within the hour on its own. The 8 affected messages were
re-run cleanly once it cleared; the numbers above are final, not
padded.

The cause is structural, not a fluke: each `agent_stateless` call is a
brand-new `query()` with no session to amortise across, and a real
tool-calling turn costs at least two model turns (send the message, get
a tool call; send the tool's result, get the final answer) — measured
here as **65 model calls for 30 messages**, more than double the
classifier's and planner's one-call-per-message. Thirty independent
one-shot sessions, each paying that multiplier, is enough to meaningfully
dent a shared weekly allowance in a way thirty single classifier calls
is not. This is the single most important cost fact in this report: **a
stateless per-message agent call is not a drop-in cost equivalent of a
classifier call — it is worth at least two of them**, and the real
subscription felt that difference within one benchmark run.

## What each backend actually is here

- **classifier** (`nlu.parse_message`) — the deployed default: one model
  call against a fixed taxonomy, a second pass (planner, then the
  thinking loop) only when the first gives up.
- **planner** (`planner.plan_message`) — one model call, asked for a
  plan of tool calls against the same `planner.TOOLS` catalogue the
  agent uses, validated the same way.
- **agent_stateless** (`agentconvo.ask_once`) — one real Agent-SDK
  `query()` call, genuine multi-turn tool-calling within that one call,
  but no memory kept afterwards. Graded on the same injected-context
  messages as the other two, for a fair one-call-per-message comparison.
- **agent_session** (`agentconvo.AgentSession`, bench mode) — a real
  persistent session across the 5-message mini-conversation, with no
  injected context at all: whatever it gets right, it got right from
  the conversation itself.
- **shortcut** — a regex fast path for an unambiguous bare "תוסיף X"
  with no standing context. Measured to see how much traffic could skip
  a model call entirely, not to compete with the others on hard cases.

## Real cost/latency facts measured getting here

- **`cwd` matters enormously.** Running the SDK with its working
  directory left at this repository (which carries a large CLAUDE.md
  and this session's own auto-memory files) cost **~46,000
  cache-creation tokens** on the very first call. Pointing `cwd` at an
  empty scratch directory (`agentconvo.SCRATCH_CWD`, created outside any
  git repo) cut that to **~1,400**. `agentconvo.py` sets this
  unconditionally; the difference is not optional tuning.
- **The system prompt (with the full 15-tool catalogue) is cache-warm
  after the first call.** It is constant across every message in a run,
  so a cold first call costs noticeably more (~$0.20 in this run's
  first `agent_stateless` call) than every call after it, once
  Anthropic's server-side prompt cache has the prefix. A production
  deployment sees this once per cache TTL (5 minutes idle, or up to an
  hour on the plan this subscription uses), not once per message.
- No `ANTHROPIC_API_KEY` was needed anywhere — `claude_agent_sdk.query()`
  and `ClaudeSDKClient` both shell out through the same `claude` CLI
  subscription `nlu._ask_model` already uses. `total_cost_usd` on
  `ResultMessage` is the API-equivalent cost, useful as a real proxy for
  how expensive a path is, not a separate bill.

## Decision

**Not adopted as the default today. Shipped, tested, and left available
behind a flag, because one real capability it has is genuinely
validated and the mandate's own instruction is to let measurement decide
rather than wait.**

On matched footing — the same injected context the classifier and
planner get — the agent's one-shot mode answered this suite **worse**
(22/30 against 26/30 for both of the others), used **more than double
the model calls per message**, and its heavier per-call cost measurably
strained the household's shared weekly allowance within one benchmark
run. None of that is close enough to call a win, and the mandate is
explicit: *"7-10 seconds is not automatically an acceptable fast path.
Let measurements decide."* These measurements say no, not on the
one-shot design.

What they also say, just as clearly: **a real persistent session,
tested with zero injected context at all, correctly resolved a same-
conversation correction ("בעצם בלי המלפפונים") that the classifier and
planner can only reach because this project hand-built a context dict
for them.** That is the actual thesis the mandate asked to test — "does
a persistent Agent SDK session materially improve context handling" —
and on the one direct test of it in this report, it did. It is also the
part of the comparison a 30-message one-shot suite structurally
under-tests, because giving every backend the same injected context
erases the one advantage a session has by construction.

So: `agentconvo.py` ships, is off by default (`GORDON_CONVO_BACKEND`
unset or anything but `agent`), and the honest next step is not a
bigger benchmark — it is Ishay trying it in a real conversation for a
few days (`GORDON_CONVO_BACKEND=agent`, one flag, one restart, one flag
back to undo) and seeing whether the memory advantage is felt in
practice, the way the mandate's own closing principle asks for: *"adopt
it and iterate from real failures rather than waiting for an arbitrary
number of clean runs."* The clean-runs gate was about execution
reliability, already validated by construction here (every tool call
reaches the same verified execution path the classifier uses) — this
decision is about conversation quality, which real use, not another
synthetic suite, is what will actually answer it.

## What was built regardless of the outcome

`grocery_bot/agentconvo.py` — a working, tested (30 unit tests,
`tests/test_agentconvo.py`) persistent Agent-SDK backend:

- **One tool catalogue, not two.** Every tool it can offer the model is
  `planner.TOOLS` — nothing added, nothing renamed. `planner.FORBIDDEN`
  names (checkout, pay, place_order, ...) are absent from that catalogue
  in the first place, so they are structurally unreachable here, not
  merely instructed against.
- **Argument cleaning is the planner's own** (`planner._clean_args`,
  imported and reused, not reimplemented) — a quantity outside 1-12 or
  an unrecognised store is dropped the same way in both places.
- **Every real tool call reaches the same function the classifier path
  already calls** — `bot._do_add_to_cart`, `bot._do_change_quantity`,
  `replace.replace_product`, `bot.start_order`, `bot.done_shopping`, and
  so on (`agentconvo._dispatch_live`). The agent decides *that* and
  *what*; identity resolution, cart-run tracking, verification, the
  breaker, and outcome reporting are the exact same code, untouched.
- **A cart tool is only callable when the current message names the
  cart or a store**, never from injected background — the fix that came
  out of measuring this report's own benchmark, with an honestly
  reported residual gap; see "Where agent_stateless actually lost
  points" above.
- **Two tools decline rather than improvise.** `remove_from_cart` (no
  adapter offers a verified single-line remove from a bare term outside
  a replace) and `remember_preference` (writing one with no resolved
  product code would plant an unranked guess ahead of a real purchase
  record, undoing Phase 8's provenance ranking) both answer honestly
  instead of inventing an unreviewed mutation path.
- **Off by default.** `GORDON_CONVO_BACKEND=agent` turns it on for a
  chat; unset (or anything else) keeps the classifier chain exactly as
  it was. Flipping the flag back is the whole rollback.

## Recommendation

1. **Leave `GORDON_CONVO_BACKEND` unset (classifier chain) as the
   household's live default.** It is more accurate on this suite,
   costs a fraction of the model calls, and has years of real tuning
   this project has already put into its prompt.
2. **Offer Ishay the flag as an opt-in trial**, framed exactly as what
   it is: a session that remembers the conversation instead of needing
   the current turn's state handed to it, worth trying specifically on
   the follow-up/correction messages that are its one measured
   strength, not as a general replacement yet.
3. **Done, partially: cart tools now require the current message to name
   the cart or a store** (`agentconvo._mentions_cart`), not just the
   background. This resolved the single-item correction cleanly but
   exposed a second, harder case — a bare "תוריד X ותוסיף Y" with
   neither word, where the model still defaults cart-ward and now gives
   up instead of falling back to the list, rather than getting the tool
   wrong. Before enabling any wider trial, this needs a real fix (a
   worked example in the system prompt, most likely — not another
   restriction), not just a note in this report.
4. **Prefer real `agent_session` conversations over more
   `agent_stateless` one-shots for any further measurement** — the
   one-shot design's per-call cost makes it a poor way to spend the
   shared weekly allowance for a mode this report is not recommending
   anyway, and a full third pass over the 30-message suite was
   deliberately not run today for the same reason.
5. Do not build the Phase-11 mandate's optional next step (migrating to
   "persistent Gordon conversational agent → rich deterministic grocery
   tools → execution service" as the primary architecture) until an
   opt-in trial produces real evidence, not this benchmark's.
