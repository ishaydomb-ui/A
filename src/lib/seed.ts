import { run, one } from "./db";

/**
 * Seed the household with its real structure plus a starter set of Skills,
 * Trackers and Automations.
 *
 * Idempotent by design: everything upserts on a stable key, so this runs safely
 * on every boot. That is what lets a hosted deploy come up fully configured
 * without anyone opening a terminal.
 *
 * Returns true if this was the first run (the household was empty).
 */
export function seed(): boolean {
  const firstRun = !one(`SELECT 1 FROM people LIMIT 1`);


  // ------------------------------------------------------------------ people

  const people = [
    { key: "ishay", name: "Ishay", name_he: "ישי", role: "adult", email: "ishaydomb@gmail.com", color: "#2563eb" },
    { key: "liran", name: "Liran", name_he: "לירן", role: "adult", email: "lirikor@gmail.com", color: "#db2777" },
    { key: "yanai", name: "Yanai", name_he: "ינאי", role: "child", email: null, color: "#16a34a" },
    { key: "berry", name: "Berry", name_he: "ברי", role: "child", email: null, color: "#f59e0b" },
  ];

  for (const p of people) {
    run(
      `INSERT INTO people (key, name, name_he, role, email, color)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(key) DO UPDATE SET name=excluded.name, name_he=excluded.name_he,
         role=excluded.role, email=excluded.email, color=excluded.color`,
      [p.key, p.name, p.name_he, p.role, p.email, p.color],
    );
  }

  // ------------------------------------------------------------------ budget
  // Mirrors the working categories and monthly target already in use.

  const categories = [
    { key: "gan_tzaharon", name_he: "גן וצהרון", bucket: "base", budget: 6650 },
    { key: "super", name_he: "סופר ומוצרי בית שוטפים", bucket: "base", budget: 4200 },
    { key: "health", name_he: "בריאות וביטוחי בריאות וחיים", bucket: "base", budget: 2700 },
    { key: "home_bills", name_he: "חשבונות הבית ותחזוקה בסיסית", bucket: "base", budget: 2500 },
    { key: "car", name_he: "רכב אחד – עלות מנורמלת", bucket: "base", budget: 1900 },
    { key: "comms", name_he: "תקשורת ומנויים בסיסיים", bucket: "base", budget: 700 },
    { key: "eating_out", name_he: "מסעדות ובילויים", bucket: "capped", budget: 1800 },
    { key: "shopping", name_he: "קניות וביגוד", bucket: "capped", budget: 1500 },
    { key: "kids_extra", name_he: "ילדים – חוגים ופעילויות", bucket: "capped", budget: 1200 },
    { key: "gifts", name_he: "מתנות ואירועים", bucket: "capped", budget: 800 },
    { key: "home_goods", name_he: "בית, ריהוט ומכשירים", bucket: "fund", budget: 2000 },
    { key: "travel", name_he: "חופשות ונסיעות", bucket: "fund", budget: 2500 },
    { key: "unclassified", name_he: "לא מסווג", bucket: "capped", budget: 0 },
  ];

  for (const c of categories) {
    run(
      `INSERT INTO budget_categories (key, name_he, bucket, monthly_budget)
       VALUES (?, ?, ?, ?)
       ON CONFLICT(key) DO UPDATE SET name_he=excluded.name_he, bucket=excluded.bucket,
         monthly_budget=excluded.monthly_budget`,
      [c.key, c.name_he, c.bucket, c.budget],
    );
  }

  // ------------------------------------------------------------------ trackers

  const trackers = [
    {
      key: "coupons",
      name: "Coupons & vouchers",
      icon: "🎟️",
      description:
        "Vouchers, gift cards and discount codes we have not used yet. Ask 'which coupons are still available'.",
      fields: [
        { name: "title", label: "What", type: "text", required: true },
        { name: "store", label: "Store", type: "text" },
        { name: "code", label: "Code", type: "text" },
        { name: "value", label: "Value", type: "money" },
        { name: "expires", label: "Expires", type: "date" },
        { name: "url", label: "Link", type: "url" },
      ],
      behaviors: {
        expireField: "expires",
        expireAction: "archive",
        notifyBeforeDays: 14,
        dedupeOn: ["title", "store"],
      },
    },
    {
      key: "watchlist",
      name: "Watchlist",
      icon: "🎬",
      description: "Films and series we want to watch. Quick-add from anywhere.",
      fields: [
        { name: "title", label: "Title", type: "text", required: true },
        { name: "kind", label: "Type", type: "select", options: ["film", "series", "documentary"] },
        { name: "where", label: "Where", type: "text" },
        { name: "who", label: "For", type: "select", options: ["us", "ishay", "liran", "kids"] },
        { name: "note", label: "Note", type: "text" },
      ],
      behaviors: { dedupeOn: ["title"] },
    },
    {
      key: "gifts",
      name: "Gift ideas",
      icon: "🎁",
      description: "Present ideas noted through the year so birthdays are not a scramble.",
      fields: [
        { name: "idea", label: "Idea", type: "text", required: true },
        { name: "recipient", label: "For", type: "text" },
        { name: "occasion", label: "Occasion", type: "text" },
        { name: "budget", label: "Budget", type: "money" },
        { name: "url", label: "Link", type: "url" },
      ],
      behaviors: { dedupeOn: ["idea", "recipient"] },
    },
    {
      key: "home",
      name: "Home maintenance",
      icon: "🔧",
      description: "Things in the flat that need fixing, servicing or replacing.",
      fields: [
        { name: "item", label: "What", type: "text", required: true },
        { name: "vendor", label: "Who to call", type: "text" },
        { name: "due", label: "Due", type: "date" },
        { name: "cost", label: "Est. cost", type: "money" },
      ],
      behaviors: { expireField: "due", expireAction: "flag", notifyBeforeDays: 7 },
    },
  ];

  for (const t of trackers) {
    const exists = one<{ id: number }>(`SELECT id FROM trackers WHERE key = ?`, [t.key]);
    if (exists) {
      run(
        `UPDATE trackers SET name=?, icon=?, description=?, fields=?, behaviors=?, builtin=1 WHERE key=?`,
        [t.name, t.icon, t.description, JSON.stringify(t.fields), JSON.stringify(t.behaviors), t.key],
      );
    } else {
      run(
        `INSERT INTO trackers (key, name, icon, description, fields, view, behaviors, builtin)
         VALUES (?, ?, ?, ?, ?, 'list', ?, 1)`,
        [t.key, t.name, t.icon, t.description, JSON.stringify(t.fields), JSON.stringify(t.behaviors)],
      );
    }
  }

  // ------------------------------------------------------------------ skills
  // A skill is a fixed procedure. These encode how this household already works.

  const skills = [
    {
      key: "party-planning",
      name: "Event planning",
      autonomy: "auto",
      description:
        "A one-off event needs organising - a birthday party, a big dinner, a trip send-off.",
      body: `1. Create a tracker for the event's to-do items if one does not exist, with fields
   for the task, who owns it, cost, and whether it is done.
2. Call set_focus pointing at that tracker, with the "until" date set to the day after
   the event so it clears itself afterwards.
3. Work backwards from the date for the standard beats: guest list and invitations,
   venue or space, food and cake, decorations, activities or entertainment, gift bags,
   and anything to return or clean up afterwards.
4. Put dated items in the calendar and cost items in the budget under gifts and events.
5. Check query_schedule for clashes on the day, and for who is on shift.

Keep the list short and real. A party needs about a dozen items, not forty.`,
    },
    {
      key: "budget-intake",
      name: "Budget intake",
      autonomy: "auto",
      description:
        "Any time an expense, receipt, bill or card statement needs recording against the family budget.",
      body: `Rules (these come from the household's own budget methodology - follow them exactly):

  1. Assign the expense to the month of the TRANSACTION date, never the credit-card billing month.
  2. Report-worthy: anything over 300 ILS above baseline, plus every holiday, electronics
     purchase, furniture, large gift or camp fee. Routine small supermarket items need no report.
  3. Irregular income (reserve duty pay, bonuses, tax refunds) does NOT raise the routine budget.
     Record it, but never re-baseline spending because of it.
  4. Transfers (BIT, PayBox, cash) count as spending until proven to be an investment,
     a reimbursement or a capital move.
  5. Annual insurance and licensing are spread for the baseline but kept whole in actual cash flow.
  6. If the category is genuinely ambiguous, set needs_review=true and leave it unclassified.
     Never guess a category to make the report look tidy - an open item is better than a wrong one.
  7. Overspend is allowed when it is documented and funded. Say where it is funded from.

  Output: log the transaction, then state the category, the month it landed in, and whether it
  breaks the monthly target.`,
    },
    {
      key: "meal-planning",
      name: "Weekly meal planning",
      autonomy: "ask",
      description:
        "Planning meals for the coming week, or when asked what to cook / what to buy.",
      body: `Steps, in order:

  1. Call pantry_status with expiring_within_days=5. Anything expiring gets used first -
     build meals around it rather than letting it go to waste.
  2. Call query_schedule for kind='oncall' and kind='travel' across the week. On days Liran is
     on shift, plan something Ishay can cook quickly (<=30 min) or a leftovers night.
  3. Keep it kid-friendly for Yanai and Berry. Assume 2 adults + 2 children unless told otherwise.
  4. Plan dinners for every day; only plan lunches/breakfasts if asked.
  5. Call plan_meals with the result, then build_grocery_list for the same window.
  6. Before presenting, call query_tracker on 'coupons' with available_only=true and mention any
     supermarket coupon that applies.

  Present the week as a short table: day, meal, who cooks. Then the estimated grocery total.`,
    },
    {
      key: "grocery-run",
      name: "Grocery run",
      autonomy: "ask",
      description: "Filling a supermarket basket from a grocery list.",
      body: `1. Confirm which list and which chain. Default chain is Shufersal - it is the one with
     working basket automation. Tiv Taam can be priced and listed but not auto-filled.
  2. Re-price the list first so the estimate is current.
  3. Flag anything unusually expensive versus the last time we bought it, and any item the
     catalogue could not match - those need a human eye.
  4. Call fill_cart. This creates an approval; say clearly that nothing is ordered yet.
  5. NEVER attempt to check out, choose a delivery slot, or pay. The run ends at a filled basket
     that a human opens and completes.
  6. Shufersal locks baskets the evening before delivery, so if a slot is close, say so.`,
    },
    {
      key: "bureaucracy-escalation",
      name: "Bureaucracy escalation",
      autonomy: "ask",
      description:
        "An official body (municipality, ministry, insurer, utility) has gone quiet on an open case, or a reply needs writing.",
      body: `1. Call get_case for the full timeline. Establish: what was asked, when, to whom,
     under what reference number, and how long it has been silent.
  2. Draft in Hebrew, formal register, addressed to the named official where known.
  3. Structure: (a) reference the original submission with its date and case number,
     (b) state that no substantive reply has been received, (c) restate the specific request in
     one sentence, (d) name the deadline pressure if real, (e) request a reasoned decision.
  4. Stay factual and unemotional. Do not threaten legal action unless explicitly told to.
  5. Always cc both parents.
  6. Route via request_approval with kind='send_email'. Never send directly.
  7. After queueing, update the case's next_action and chase_after date.`,
    },
    {
      key: "trip-packing",
      name: "Trip packing list",
      autonomy: "auto",
      description: "A trip is coming up and needs a packing list.",
      body: `Use the household's established checklist structure, adapted to the trip:

  Sections, in this order: backpack (documents, electronics, medication, in-flight comfort),
  suitcase (clothing and beach), equipment and accessories, electronics, toiletries,
  backup medication, and a final pre-departure checklist.

  Rules:
  - Essential medication and documents go in the BACKPACK, never the suitcase.
  - Scale clothing counts to the number of nights.
  - Adjust for destination, season, and whether the children are coming.
  - Always end with the pre-departure block: offline maps, bookings and travel insurance
    downloaded, luggage weight and liquid limits checked, everything charged, early check-in
    done, eSIM/data plan activated.
  - Attach any flight and insurance documents already filed for this trip.`,
    },
    {
      key: "document-filing",
      name: "Document filing",
      autonomy: "auto",
      description: "A bill, receipt, policy, ticket or official letter arrives and needs filing.",
      body: `1. Classify: bill | receipt | policy | ticket | official | id | report.
  2. Extract vendor, amount, currency and document date. Keep Hebrew names in Hebrew.
  3. Store as a POINTER (Drive link), never a duplicate copy.
  4. If it belongs to an open case (an appeal, a claim, a trip), attach it to that case.
  5. If it is a bill or receipt, also run the budget-intake skill on it.
  6. If it carries a deadline (registration closing, policy renewal, payment due), create a task
     with that due date and a reminder ahead of it.
  7. Write a one-line summary in the language of the document.`,
    },
    {
      key: "daily-brief",
      name: "Daily brief",
      autonomy: "auto",
      description: "Producing the morning summary for the household.",
      body: `Assemble, in this order, and keep the whole thing under 200 words:

  1. Today's schedule - call query_schedule for today. Note who does pickup and any clash.
  2. Anything due today or overdue - call list_tasks with due_before=end of today.
  3. Pending approvals waiting on a human.
  4. Cases that have gone quiet past their chase_after date.
  5. Coupons expiring within 14 days - call query_tracker with expiring_within_days=14.
  6. Renewals due - call expiring_facts with within_days=30. Name what is due and when,
     never the value itself: a brief should say "driving licence due 14/9", not a licence
     number.
  7. Anything ready for collection, and any parcel that has gone quiet -
     call list_deliveries. Mention a delivery only when it needs action; a parcel
     quietly in transit is not news.
  8. Food expiring within 3 days, and tonight's planned meal.

  If a section is empty, omit it entirely rather than writing "nothing". Lead with whatever is
  most time-critical, not the list order above.`,
    },
  ];

  for (const s of skills) {
    const exists = one<{ id: number; version: number }>(
      `SELECT id, version FROM skills WHERE key = ?`,
      [s.key],
    );
    if (exists) {
      run(
        `UPDATE skills SET name=?, description=?, body=?, autonomy=?,
           version=version+1, updated_at=datetime('now') WHERE key=?`,
        [s.name, s.description, s.body, s.autonomy, s.key],
      );
    } else {
      run(
        `INSERT INTO skills (key, name, description, body, autonomy) VALUES (?, ?, ?, ?, ?)`,
        [s.key, s.name, s.description, s.body, s.autonomy],
      );
    }
  }

  // ------------------------------------------------------------------ automations

  const automations = [
    {
      name: "Morning brief",
      description: "Daily summary to both of us at 06:45.",
      trigger_type: "schedule",
      trigger_config: { cron: "45 6 * * *" },
      action_type: "run_skill",
      // Delivered in the app, never emailed - this system does not send mail.
      action_config: { skill: "daily-brief", deliver: ["dashboard"] },
    },
    {
      name: "Coupon sweep",
      description: "Retire expired coupons and flag ones expiring within two weeks.",
      trigger_type: "schedule",
      trigger_config: { cron: "0 8 * * *" },
      action_type: "agent_prompt",
      action_config: {
        prompt:
          "Sweep expired tracker items. Then list coupons expiring within 14 days and, if any, " +
          "create a task to use them.",
      },
    },
    {
      name: "Chase quiet cases",
      description:
        "If an open case passes its chase_after date with no reply, draft the next escalation.",
      trigger_type: "case_stale",
      trigger_config: { checkDaily: true },
      action_type: "run_skill",
      action_config: { skill: "bureaucracy-escalation" },
    },
    {
      name: "Weekly meal plan",
      description: "Plan next week's dinners and build the shopping list every Friday morning.",
      trigger_type: "schedule",
      trigger_config: { cron: "0 9 * * 5" },
      action_type: "run_skill",
      action_config: { skill: "meal-planning" },
    },
    {
      name: "Chase missing parcels",
      description:
        "Flag orders that have gone quiet - nobody notices the parcel that simply never came.",
      trigger_type: "delivery_stale",
      trigger_config: { afterDays: 14 },
      action_type: "agent_prompt",
      action_config: {
        prompt:
          "Call list_deliveries with stale=true. For each one, create a task to chase the " +
          "vendor, unless a task for it already exists.",
      },
    },
    {
      name: "Renewals watch",
      description:
        "Raise a task when a licence, passport, policy or warranty is coming up for renewal.",
      trigger_type: "fact_expiring",
      trigger_config: { withinDays: 30 },
      action_type: "agent_prompt",
      action_config: {
        prompt:
          "Call expiring_facts for the next 30 days. For anything due that has no open task " +
          "already, create one with the renewal date as the due date. Never put the stored " +
          "value in the task title.",
      },
    },
    {
      name: "Food expiry watch",
      description: "Suggest a recipe when something in the fridge is about to go off.",
      trigger_type: "pantry_expiring",
      trigger_config: { withinDays: 2 },
      action_type: "agent_prompt",
      action_config: {
        prompt:
          "These pantry items expire within 2 days. Suggest one quick dinner that uses them, " +
          "and add it to tonight's meal plan if nothing is planned.",
      },
    },
  ];

  for (const a of automations) {
    const exists = one<{ id: number }>(`SELECT id FROM automations WHERE name = ?`, [a.name]);
    if (!exists) {
      run(
        `INSERT INTO automations (name, description, trigger_type, trigger_config, action_type, action_config)
         VALUES (?, ?, ?, ?, ?, ?)`,
        [
          a.name,
          a.description,
          a.trigger_type,
          JSON.stringify(a.trigger_config),
          a.action_type,
          JSON.stringify(a.action_config),
        ],
      );
    }
  }

  // ------------------------------------------------------------------ standing facts

  const facts = [
    "Household of four in Tel Aviv: Ishay, Liran, and their children Yanai and Berry.",
    "Liran works shifts (תורנויות); on-call days are marked in the calendar and change who covers the kids.",
    "Monthly spending target is 35,000 ILS against a planning income of 38,500 ILS.",
    "Groceries: Shufersal is the default chain for automated basket filling. Tiv Taam is price-tracked only.",
  ];

  for (const body of facts) {
    const exists = one<{ id: number }>(`SELECT id FROM notes WHERE body = ?`, [body]);
    if (!exists) run(`INSERT INTO notes (topic, body, pinned) VALUES ('household', ?, 1)`, [body]);
  }

  return firstRun;
}

