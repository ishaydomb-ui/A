import type Anthropic from "@anthropic-ai/sdk";
import { all, one, run, json, logActivity } from "../db";
import * as trackers from "../trackers";
import * as schedule from "../schedule";
import { requestApproval } from "../approvals";
import { searchProducts } from "../grocery/prices";
import { buildGroceryList } from "../grocery/list";
import { rememberFact, recallFacts, expiringFacts } from "../facts";
import { createDraft, listDrafts } from "../drafts";
import { sendToHousehold } from "../mail";
import { setFocus, activeFocus, clearFocus } from "../focus";
import {
  pendingDeliveries,
  staleDeliveries,
  recentlyDelivered,
  recordDelivery,
  setStatus as setDeliveryStatus,
} from "../deliveries";

/**
 * The agent's hands.
 *
 * Design rule that makes the assistant feel "smart": every factual question
 * resolves through one of these tools against real rows. The model is never
 * allowed to answer "which coupons are left" or "when do I pick up the kids"
 * from its own recollection - it must call a tool and report what came back.
 */

export interface ToolContext {
  actor: string; // 'ishay' | 'liran'
  channel: string; // web | whatsapp | voice
  skillKey?: string;
}

type Handler = (input: any, ctx: ToolContext) => Promise<unknown> | unknown;

interface ToolDef {
  spec: Anthropic.Tool;
  handler: Handler;
}

const tools: ToolDef[] = [];

function tool(spec: Anthropic.Tool, handler: Handler) {
  tools.push({ spec, handler });
}

// ------------------------------------------------------------------ trackers

tool(
  {
    name: "query_tracker",
    description:
      "Query any tracker (coupons, movie watchlist, gift ideas, anything the household tracks). " +
      "ALWAYS use this to answer questions like 'which coupons are still available' or " +
      "'what's on our watchlist' - never answer from memory. Use available_only=true for " +
      "'still available / still valid / not used yet' questions.",
    input_schema: {
      type: "object",
      properties: {
        tracker: { type: "string", description: "Tracker key, e.g. 'coupons', 'watchlist'" },
        available_only: {
          type: "boolean",
          description: "Only active items that have not expired. Use for 'still available'.",
        },
        expiring_within_days: { type: "number" },
        match: {
          type: "object",
          description: "Substring filter on fields, e.g. {store: 'shufersal'}",
        },
        limit: { type: "number" },
      },
      required: ["tracker"],
    },
  },
  (input) => {
    const items = trackers.queryItems(input.tracker, {
      availableOnly: input.available_only,
      expiringWithinDays: input.expiring_within_days,
      match: input.match,
      limit: input.limit,
    });
    return {
      count: items.length,
      items: items.map((i) => ({
        id: i.id,
        ...i.data,
        status: i.status,
        expires_at: i.expires_at,
      })),
    };
  },
);

tool(
  {
    name: "list_trackers",
    description:
      "List all trackers that exist, with their fields. Call this when unsure which tracker " +
      "holds the answer, or before creating a new one.",
    input_schema: { type: "object", properties: {} },
  },
  () =>
    trackers.listTrackers().map((t) => ({
      key: t.key,
      name: t.name,
      description: t.description,
      fields: t.fields.map((f) => `${f.name}:${f.type}`),
    })),
);

tool(
  {
    name: "add_tracker_item",
    description:
      "Add an item to a tracker. Use for 'add Dune 2 to our watchlist', 'save this coupon'. " +
      "Fields must match the tracker's schema - call list_trackers first if unsure.",
    input_schema: {
      type: "object",
      properties: {
        tracker: { type: "string" },
        data: { type: "object", description: "Field values matching the tracker schema" },
      },
      required: ["tracker", "data"],
    },
  },
  (input, ctx) => trackers.addItem(input.tracker, input.data, { actor: ctx.actor }),
);

tool(
  {
    name: "update_tracker_item",
    description:
      "Update or retire a tracker item. Use status 'used' when a coupon is redeemed, " +
      "'watched' items should set status 'archived'.",
    input_schema: {
      type: "object",
      properties: {
        id: { type: "number" },
        data: { type: "object" },
        status: { type: "string", enum: ["active", "used", "expired", "archived"] },
      },
      required: ["id"],
    },
  },
  (input, ctx) =>
    trackers.updateItem(input.id, { data: input.data, status: input.status, actor: ctx.actor }),
);

tool(
  {
    name: "create_tracker",
    description:
      "Create a brand new tracker when the household wants to track a new kind of thing " +
      "('start tracking books I want to read'). Infer sensible fields. Do this rather than " +
      "cramming unrelated data into an existing tracker.",
    input_schema: {
      type: "object",
      properties: {
        key: { type: "string", description: "lowercase slug, e.g. 'books'" },
        name: { type: "string" },
        icon: { type: "string", description: "a single emoji" },
        description: { type: "string" },
        fields: {
          type: "array",
          items: {
            type: "object",
            properties: {
              name: { type: "string" },
              label: { type: "string" },
              type: {
                type: "string",
                enum: ["text", "number", "money", "date", "bool", "select", "url", "person"],
              },
              options: { type: "array", items: { type: "string" } },
            },
            required: ["name", "label", "type"],
          },
        },
        behaviors: {
          type: "object",
          description:
            "Optional: {expireField, expireAction:'archive'|'flag', notifyBeforeDays, dedupeOn:[field]}",
        },
      },
      required: ["key", "name", "fields"],
    },
  },
  (input, ctx) => trackers.createTracker({ ...input, actor: ctx.actor }),
);

// ------------------------------------------------------------------ schedule

tool(
  {
    name: "query_schedule",
    description:
      "Query the household calendar for a date range. Use kind='pickup' for kid pickups, " +
      "'oncall' for Liran's shifts, 'class' for kids' activities. ALWAYS use this for any " +
      "question about when something happens - never guess dates.",
    input_schema: {
      type: "object",
      properties: {
        from: { type: "string", description: "ISO date/datetime, inclusive" },
        to: { type: "string", description: "ISO date/datetime, exclusive" },
        kind: {
          type: "string",
          enum: ["pickup", "dropoff", "class", "appointment", "oncall", "travel", "reserve"],
        },
      },
      required: ["from", "to"],
    },
  },
  (input) => schedule.eventsBetween(input.from, input.to, input.kind),
);

tool(
  {
    name: "who_does_pickup",
    description:
      "Answer 'which days am I picking up the kids' precisely. Returns each pickup/dropoff " +
      "run in the window with the date, time and who owns it.",
    input_schema: {
      type: "object",
      properties: {
        from: { type: "string" },
        to: { type: "string" },
        person: { type: "string", description: "Optional: 'ishay' or 'liran' to filter" },
      },
      required: ["from", "to"],
    },
  },
  (input) => schedule.pickupSchedule(input.from, input.to, input.person),
);

tool(
  {
    name: "check_conflicts",
    description:
      "Before booking or suggesting a time, check what else is happening then. " +
      "Returns overlapping events.",
    input_schema: {
      type: "object",
      properties: { start: { type: "string" }, end: { type: "string" } },
      required: ["start", "end"],
    },
  },
  (input) => schedule.conflicts(input.start, input.end),
);

// ------------------------------------------------------------------ tasks

tool(
  {
    name: "list_tasks",
    description: "List household tasks. Default is open tasks ordered by due date.",
    input_schema: {
      type: "object",
      properties: {
        status: { type: "string", enum: ["open", "done", "dropped"] },
        assignee: { type: "string", description: "person key" },
        due_before: { type: "string" },
        area: { type: "string" },
      },
    },
  },
  (input) => {
    const where = ["t.status = ?"];
    const params: unknown[] = [input.status ?? "open"];
    if (input.assignee) {
      where.push("p.key = ?");
      params.push(input.assignee);
    }
    if (input.due_before) {
      where.push("t.due_at IS NOT NULL AND t.due_at <= ?");
      params.push(input.due_before);
    }
    if (input.area) {
      where.push("t.area = ?");
      params.push(input.area);
    }
    return all(
      `SELECT t.id, t.title, t.notes, t.due_at, t.priority, t.area, p.name AS assignee
       FROM tasks t LEFT JOIN people p ON p.id = t.assignee_id
       WHERE ${where.join(" AND ")}
       ORDER BY (t.due_at IS NULL), t.due_at LIMIT 100`,
      params,
    );
  },
);

tool(
  {
    name: "create_task",
    description:
      "Create a task or action item. Set recurrence for repeating chores " +
      "(e.g. 'FREQ=WEEKLY;BYDAY=SU' ).",
    input_schema: {
      type: "object",
      properties: {
        title: { type: "string" },
        notes: { type: "string" },
        assignee: { type: "string", description: "person key: ishay | liran" },
        due_at: { type: "string" },
        priority: { type: "string", enum: ["low", "normal", "high", "urgent"] },
        area: { type: "string", enum: ["kids", "home", "admin", "health", "money", "other"] },
        recurrence: { type: "string" },
      },
      required: ["title"],
    },
  },
  (input, ctx) => {
    const person = input.assignee ? schedule.personByKey(input.assignee) : undefined;
    const res = run(
      `INSERT INTO tasks (title, notes, assignee_id, due_at, priority, area, recurrence, source)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'agent')`,
      [
        input.title,
        input.notes ?? null,
        person?.id ?? null,
        input.due_at ?? null,
        input.priority ?? "normal",
        input.area ?? null,
        input.recurrence ?? null,
      ],
    );
    logActivity({
      actor: ctx.actor,
      action: "created_task",
      entityType: "task",
      entityId: res.lastInsertRowid as number,
      summary: `Task: ${input.title}`,
      skillKey: ctx.skillKey,
    });
    return { id: res.lastInsertRowid, ...input };
  },
);

tool(
  {
    name: "complete_task",
    description: "Mark a task done.",
    input_schema: {
      type: "object",
      properties: { id: { type: "number" } },
      required: ["id"],
    },
  },
  (input, ctx) => {
    run(
      `UPDATE tasks SET status='done', completed_at=datetime('now') WHERE id = ?`,
      [input.id],
    );
    logActivity({
      actor: ctx.actor,
      action: "completed_task",
      entityType: "task",
      entityId: input.id,
      summary: `Completed task #${input.id}`,
    });
    return { ok: true };
  },
);

// ------------------------------------------------------------------ money

tool(
  {
    name: "log_expense",
    description:
      "Record a transaction against the family budget. Follow the 'budget-intake' skill for " +
      "categorisation rules. Leave needs_review=true if the category is genuinely ambiguous " +
      "rather than guessing.",
    input_schema: {
      type: "object",
      properties: {
        amount: { type: "number" },
        vendor: { type: "string" },
        description: { type: "string" },
        occurred_on: { type: "string", description: "YYYY-MM-DD, the transaction date" },
        category: { type: "string", description: "budget category key" },
        payer: { type: "string", description: "person key" },
        needs_review: { type: "boolean" },
      },
      required: ["amount", "occurred_on"],
    },
  },
  (input, ctx) => {
    const cat = input.category
      ? one<{ id: number }>(`SELECT id FROM budget_categories WHERE key = ?`, [input.category])
      : undefined;
    const payer = input.payer ? schedule.personByKey(input.payer) : undefined;
    const res = run(
      `INSERT INTO transactions
        (occurred_on, amount, vendor, description, category_id, payer_id, needs_review, source)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'agent')`,
      [
        input.occurred_on,
        input.amount,
        input.vendor ?? null,
        input.description ?? null,
        cat?.id ?? null,
        payer?.id ?? null,
        input.needs_review || !cat ? 1 : 0,
      ],
    );
    logActivity({
      actor: ctx.actor,
      action: "logged_expense",
      entityType: "transaction",
      entityId: res.lastInsertRowid as number,
      summary: `${input.amount} ILS - ${input.vendor ?? input.description ?? "expense"}`,
      skillKey: ctx.skillKey,
    });
    return { id: res.lastInsertRowid, categorised: !!cat };
  },
);

tool(
  {
    name: "budget_status",
    description:
      "Spending by category for a month vs budget. Use for 'how are we doing this month'.",
    input_schema: {
      type: "object",
      properties: { month: { type: "string", description: "YYYY-MM, defaults to current" } },
    },
  },
  (input) => {
    const month = input.month ?? new Date().toISOString().slice(0, 7);
    const rows = all<{ name_he: string; budget: number; spent: number }>(
      `SELECT c.name_he, c.monthly_budget AS budget,
              COALESCE(SUM(t.amount), 0) AS spent
       FROM budget_categories c
       LEFT JOIN transactions t
         ON t.category_id = c.id AND strftime('%Y-%m', t.occurred_on) = ?
       GROUP BY c.id ORDER BY c.monthly_budget DESC`,
      [month],
    );
    const unclassified = one<{ n: number; total: number }>(
      `SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS total FROM transactions
       WHERE strftime('%Y-%m', occurred_on) = ? AND needs_review = 1`,
      [month],
    );
    return {
      month,
      categories: rows,
      total_spent: rows.reduce((s, r) => s + r.spent, 0),
      total_budget: rows.reduce((s, r) => s + r.budget, 0),
      unclassified,
    };
  },
);

// ------------------------------------------------------------------ food & groceries

tool(
  {
    name: "pantry_status",
    description:
      "What's in the pantry/fridge, and what's expiring soon. Use this proactively when " +
      "planning meals so food doesn't get wasted.",
    input_schema: {
      type: "object",
      properties: { expiring_within_days: { type: "number" } },
    },
  },
  (input) => {
    if (input.expiring_within_days != null) {
      return all(
        `SELECT id, name, qty, unit, expires_at FROM pantry_items
         WHERE expires_at IS NOT NULL
           AND date(expires_at) <= date('now', '+' || ? || ' days')
         ORDER BY expires_at`,
        [input.expiring_within_days],
      );
    }
    return all(`SELECT id, name, qty, unit, category, expires_at, staple FROM pantry_items ORDER BY name`);
  },
);

tool(
  {
    name: "plan_meals",
    description:
      "Write meals into the weekly plan. Follow the 'meal-planning' skill: respect who is " +
      "cooking (check Liran's on-call days first), use up expiring pantry items, keep it " +
      "kid-friendly.",
    input_schema: {
      type: "object",
      properties: {
        entries: {
          type: "array",
          items: {
            type: "object",
            properties: {
              date: { type: "string", description: "YYYY-MM-DD" },
              meal: { type: "string", enum: ["breakfast", "lunch", "dinner"] },
              title: { type: "string" },
              cook: { type: "string", description: "person key" },
              notes: { type: "string" },
            },
            required: ["date", "title"],
          },
        },
      },
      required: ["entries"],
    },
  },
  (input, ctx) => {
    for (const e of input.entries) {
      const cook = e.cook ? schedule.personByKey(e.cook) : undefined;
      run(
        `INSERT INTO meal_plan (plan_date, meal, title, cook_id, notes)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(plan_date, meal) DO UPDATE SET title=excluded.title,
           cook_id=excluded.cook_id, notes=excluded.notes`,
        [e.date, e.meal ?? "dinner", e.title, cook?.id ?? null, e.notes ?? null],
      );
    }
    logActivity({
      actor: ctx.actor,
      action: "planned_meals",
      summary: `Planned ${input.entries.length} meals`,
      detail: input.entries,
      skillKey: ctx.skillKey,
    });
    return { planned: input.entries.length };
  },
);

tool(
  {
    name: "build_grocery_list",
    description:
      "Build a grocery list from the meal plan plus staples that are low, and price it " +
      "against the public price-transparency catalogue. This does NOT touch any store account.",
    input_schema: {
      type: "object",
      properties: {
        from: { type: "string", description: "YYYY-MM-DD" },
        to: { type: "string", description: "YYYY-MM-DD" },
        chain: { type: "string", enum: ["shufersal", "tivtaam"] },
        extra: { type: "array", items: { type: "string" }, description: "Ad-hoc items to add" },
      },
      required: ["from", "to"],
    },
  },
  (input, ctx) => buildGroceryList(input, ctx.actor),
);

tool(
  {
    name: "search_products",
    description:
      "Search the supermarket catalogue (from the public price-transparency feeds) for prices. " +
      "Use to compare or to resolve a vague item name to a real product.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string" },
        chain: { type: "string", enum: ["shufersal", "tivtaam"] },
        limit: { type: "number" },
      },
      required: ["query"],
    },
  },
  (input) => searchProducts(input.query, input.chain, input.limit ?? 10),
);

tool(
  {
    name: "fill_cart",
    description:
      "Ask to fill the supermarket basket with a grocery list, via the browser worker. " +
      "This ALWAYS creates an approval request - it never checks out and never pays. " +
      "The basket is left ready for a human to review and complete.",
    input_schema: {
      type: "object",
      properties: {
        list_id: { type: "number" },
        chain: { type: "string", enum: ["shufersal", "tivtaam"] },
      },
      required: ["list_id"],
    },
  },
  (input, ctx) => {
    const list = one<{ id: number; name: string }>(
      `SELECT id, name FROM grocery_lists WHERE id = ?`,
      [input.list_id],
    );
    if (!list) return { error: "list not found" };
    const items = all<{ name: string; qty: number; est_price: number | null }>(
      `SELECT name, qty, est_price FROM grocery_items WHERE list_id = ?`,
      [input.list_id],
    );
    const estimate = items.reduce((s, i) => s + (i.est_price ?? 0) * i.qty, 0);
    return requestApproval({
      kind: "fill_cart",
      title: `Fill ${input.chain ?? "supermarket"} basket: ${list.name}`,
      summary: `${items.length} items, estimated ${estimate.toFixed(2)} ILS. ` +
        `The worker stops at the filled basket - it will not check out or pay.`,
      payload: { listId: input.list_id, chain: input.chain, items, estimate },
      risk: "high",
      requestedBy: ctx.actor,
      skillKey: ctx.skillKey,
    });
  },
);

// ------------------------------------------------------------------ documents & cases

tool(
  {
    name: "search_documents",
    description:
      "Find a stored document (bill, receipt, policy, ticket, official letter) by text.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string" },
        kind: { type: "string" },
        limit: { type: "number" },
      },
      required: ["query"],
    },
  },
  (input) => {
    const like = `%${input.query}%`;
    const params: unknown[] = [like, like, like];
    let sql = `SELECT id, title, kind, url, vendor, amount, doc_date, summary
               FROM documents WHERE (title LIKE ? OR summary LIKE ? OR vendor LIKE ?)`;
    if (input.kind) {
      sql += ` AND kind = ?`;
      params.push(input.kind);
    }
    return all(`${sql} ORDER BY doc_date DESC LIMIT ?`, [...params, input.limit ?? 20]);
  },
);

tool(
  {
    name: "list_cases",
    description:
      "Open 'cases' - live threads like the kindergarten appeal, with their next action and " +
      "whether they have gone quiet.",
    input_schema: {
      type: "object",
      properties: { status: { type: "string", enum: ["open", "waiting", "closed"] } },
    },
  },
  (input) =>
    all(
      `SELECT id, title, status, summary, reference, due_at, next_action, next_action_at, chase_after
       FROM cases WHERE status = ? ORDER BY (due_at IS NULL), due_at`,
      [input.status ?? "open"],
    ),
);

tool(
  {
    name: "get_case",
    description: "Full timeline of one case: every linked email, document, event and note.",
    input_schema: {
      type: "object",
      properties: { id: { type: "number" } },
      required: ["id"],
    },
  },
  (input) => ({
    case: one(`SELECT * FROM cases WHERE id = ?`, [input.id]),
    timeline: all(
      `SELECT kind, title, url, occurred_at, body FROM case_items
       WHERE case_id = ? ORDER BY occurred_at DESC`,
      [input.id],
    ),
    tasks: all(`SELECT id, title, status, due_at FROM tasks WHERE case_id = ?`, [input.id]),
  }),
);

// ------------------------------------------------------------------ memory

tool(
  {
    name: "remember",
    description:
      "Store a durable fact about the household ('Berry is allergic to X', 'our plumber is Y'). " +
      "Use this whenever you learn something that should survive this conversation.",
    input_schema: {
      type: "object",
      properties: {
        topic: { type: "string" },
        body: { type: "string" },
        about: { type: "string", description: "person key this concerns" },
      },
      required: ["body"],
    },
  },
  (input, ctx) => {
    const person = input.about ? schedule.personByKey(input.about) : undefined;
    const res = run(`INSERT INTO notes (topic, body, person_id) VALUES (?, ?, ?)`, [
      input.topic ?? null,
      input.body,
      person?.id ?? null,
    ]);
    logActivity({
      actor: ctx.actor,
      action: "remembered",
      entityType: "note",
      entityId: res.lastInsertRowid as number,
      summary: input.body.slice(0, 120),
    });
    return { id: res.lastInsertRowid };
  },
);

tool(
  {
    name: "recall",
    description: "Search durable household facts previously stored with `remember`.",
    input_schema: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"],
    },
  },
  (input) =>
    all(
      `SELECT n.id, n.topic, n.body, p.name AS about, n.created_at
       FROM notes n LEFT JOIN people p ON p.id = n.person_id
       WHERE n.body LIKE ? OR n.topic LIKE ? ORDER BY n.pinned DESC, n.created_at DESC LIMIT 30`,
      [`%${input.query}%`, `%${input.query}%`],
    ),
);

// ------------------------------------------------------------------ household facts

tool(
  {
    name: "remember_fact",
    description:
      "Store a durable household fact: an ID number, a door code, where something is kept, " +
      "a renewal date, or that something happened on a date. Do this WITHOUT being asked " +
      "whenever one is mentioned in passing - 'Yanai's ID is 123456789', 'the drill is on " +
      "the top shelf in the garage', 'licence expires in March'. " +
      "Set occurred_on for things that happened ('blood test today') so 'when was the last " +
      "time' works. Set valid_until for anything that expires, and a reminder follows. " +
      "ID numbers, codes and passwords are encrypted automatically.",
    input_schema: {
      type: "object",
      properties: {
        subject: {
          type: "string",
          description: "Who or what it concerns: 'yanai', 'mum', 'garage', 'car', 'flat'",
        },
        label: { type: "string", description: "e.g. 'ID number', 'building code', 'location'" },
        value: { type: "string" },
        category: {
          type: "string",
          enum: [
            "identity",
            "access",
            "location",
            "medical",
            "vehicle",
            "admin",
            "contact",
            "other",
          ],
        },
        sensitive: {
          type: "boolean",
          description: "Encrypt at rest. Defaults true for identity and access.",
        },
        occurred_on: { type: "string", description: "YYYY-MM-DD, for a dated occurrence" },
        valid_until: { type: "string", description: "YYYY-MM-DD, for anything that expires" },
      },
      required: ["subject", "label", "value"],
    },
  },
  (input, ctx) =>
    rememberFact({
      subject: input.subject,
      label: input.label,
      value: input.value,
      category: input.category,
      sensitive: input.sensitive,
      occurredOn: input.occurred_on,
      validUntil: input.valid_until,
      source: ctx.channel,
      actor: ctx.actor,
    }),
);

tool(
  {
    name: "recall_facts",
    description:
      "Look up household facts. Use for 'what is Yanai's ID', 'what's mum's building code', " +
      "'where did we put the drill', 'when do I renew my licence', 'when was the last blood " +
      "test'. Set latest_only=true for 'when was the last time' questions. " +
      "ALWAYS call this before saying you don't know something factual about the household.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Free text matched against subject and label" },
        subject: { type: "string" },
        category: { type: "string" },
        latest_only: {
          type: "boolean",
          description: "Only the most recent occurrence per subject+label",
        },
      },
    },
  },
  (input) =>
    recallFacts({
      query: input.query,
      subject: input.subject,
      category: input.category,
      latestOnly: input.latest_only,
    }),
);

tool(
  {
    name: "expiring_facts",
    description:
      "Renewals and expiries coming up - licences, passports, policies, warranties. " +
      "Values are withheld here; this answers what is due, not what the secret is.",
    input_schema: {
      type: "object",
      properties: { within_days: { type: "number" } },
    },
  },
  (input) => expiringFacts(input.within_days),
);

// ------------------------------------------------------------------ drafting

tool(
  {
    name: "draft_email",
    description:
      "Write an email for Ishay or Liran to send THEMSELVES. This system never sends mail - " +
      "it produces a draft they open, check and send from their own account. " +
      "Use for any outward correspondence: an escalation to the municipality, a reply to a " +
      "school, a query to an insurer. Write it complete and ready to send, in the language " +
      "of the recipient. Say plainly afterwards that it is a draft and has not been sent.",
    input_schema: {
      type: "object",
      properties: {
        to: { type: "array", items: { type: "string" } },
        cc: { type: "array", items: { type: "string" } },
        subject: { type: "string" },
        body: { type: "string", description: "The full email, ready to send" },
        language: { type: "string", enum: ["he", "en"] },
        case_id: { type: "number", description: "Attach to an open case if relevant" },
      },
      required: ["body"],
    },
  },
  (input, ctx) =>
    createDraft({
      to: input.to,
      cc: input.cc,
      subject: input.subject,
      body: input.body,
      language: input.language,
      caseId: input.case_id,
      skillKey: ctx.skillKey,
      actor: ctx.actor,
    }),
);

tool(
  {
    name: "list_drafts",
    description: "Drafts waiting for a human to review and send.",
    input_schema: { type: "object", properties: {} },
  },
  () => listDrafts("draft"),
);

tool(
  {
    name: "email_us",
    description:
      "Email Ishay and/or Liran. Approved for digests and for anything they ask you to send " +
      "them - a shopping list, a summary, a checklist. Recipients are restricted to the two " +
      "of them in code; there is no way to email anyone else with this. " +
      "For correspondence with ANYONE outside the household, use draft_email instead.",
    input_schema: {
      type: "object",
      properties: {
        to: {
          type: "array",
          items: { type: "string" },
          description: "Household addresses. Omit to send to both of them.",
        },
        subject: { type: "string" },
        body: { type: "string", description: "Plain text" },
      },
      required: ["subject", "body"],
    },
  },
  (input, ctx) =>
    sendToHousehold({
      to: input.to,
      subject: input.subject,
      body: input.body,
      actor: ctx.actor,
    }),
);

// ------------------------------------------------------------------ focus

tool(
  {
    name: "set_focus",
    description:
      "Pin something to the top of the dashboard as the current priority - a birthday party " +
      "next week, a house move, an ongoing medical thing. Use when they say something is the " +
      "focus right now, or when a big one-off event is approaching. " +
      "ALWAYS set `until` so it clears itself; a focus with no end stops being a focus.",
    input_schema: {
      type: "object",
      properties: {
        title: { type: "string" },
        note: { type: "string", description: "One line of context" },
        entity_type: {
          type: "string",
          enum: ["tracker", "case", "document", "task", "url"],
        },
        entity_ref: {
          type: "string",
          description: "Tracker key, case id, or a Drive/sheet URL",
        },
        url: { type: "string" },
        until: { type: "string", description: "YYYY-MM-DD - when it stops being the focus" },
      },
      required: ["title"],
    },
  },
  (input, ctx) =>
    setFocus({
      title: input.title,
      note: input.note,
      entityType: input.entity_type,
      entityRef: input.entity_ref,
      url: input.url,
      until: input.until,
      actor: ctx.actor,
    }),
);

tool(
  {
    name: "get_focus",
    description: "What is currently pinned as the priority.",
    input_schema: { type: "object", properties: {} },
  },
  () => activeFocus(),
);

tool(
  {
    name: "clear_focus",
    description: "Unpin a focus once it is over.",
    input_schema: {
      type: "object",
      properties: { id: { type: "number" } },
      required: ["id"],
    },
  },
  (input, ctx) => ({ cleared: clearFocus(input.id, ctx.actor) }),
);

// ------------------------------------------------------------------ deliveries

tool(
  {
    name: "list_deliveries",
    description:
      "What is currently in transit, built from order and shipping emails. Use for " +
      "'what am I waiting for', 'any packages coming', 'did the thing from iHerb arrive'. " +
      "stale=true returns orders that have gone quiet and may never have arrived.",
    input_schema: {
      type: "object",
      properties: {
        stale: { type: "boolean", description: "Only orders with no update for a while" },
        stale_days: { type: "number" },
        recently_delivered: { type: "boolean" },
      },
    },
  },
  (input) => {
    if (input.stale) return staleDeliveries(input.stale_days ?? 14);
    if (input.recently_delivered) return recentlyDelivered();
    return pendingDeliveries();
  },
);

tool(
  {
    name: "track_delivery",
    description:
      "Start tracking an order the email parser missed, or one mentioned in conversation " +
      "('I ordered a lamp from Ivory, should come next week').",
    input_schema: {
      type: "object",
      properties: {
        vendor: { type: "string" },
        order_ref: { type: "string" },
        description: { type: "string" },
        expected_at: { type: "string", description: "YYYY-MM-DD" },
        tracking_url: { type: "string" },
      },
      required: ["vendor"],
    },
  },
  (input, ctx) =>
    recordDelivery({
      vendor: input.vendor,
      orderRef: input.order_ref,
      description: input.description,
      expectedAt: input.expected_at,
      trackingUrl: input.tracking_url,
      actor: ctx.actor,
    }),
);

tool(
  {
    name: "update_delivery",
    description:
      "Change a parcel's state - usually to mark it arrived when someone says so before " +
      "the vendor email lands.",
    input_schema: {
      type: "object",
      properties: {
        id: { type: "number" },
        status: {
          type: "string",
          enum: ["ordered", "shipped", "in_transit", "ready_for_pickup", "delivered", "cancelled"],
        },
      },
      required: ["id", "status"],
    },
  },
  (input, ctx) => setDeliveryStatus(input.id, input.status, ctx.actor),
);

// ------------------------------------------------------------------ approvals

tool(
  {
    name: "request_approval",
    description:
      "Queue an action that needs a human yes/no: filling a shopping basket, booking " +
      "something that costs money, submitting an official form. Describe exactly what will " +
      "happen. Never take these actions directly. " +
      "NOT for email or messages - those are never sent by this system at all; use draft_email.",
    input_schema: {
      type: "object",
      properties: {
        kind: {
          type: "string",
          enum: ["fill_cart", "book", "pay", "submit_form", "other"],
          description:
            "Never 'send_email' or any sending kind - use draft_email instead. " +
            "This system has no way to transmit anything.",
        },
        title: { type: "string" },
        summary: { type: "string" },
        payload: { type: "object", description: "Exactly what executes on approval" },
        risk: { type: "string", enum: ["low", "medium", "high"] },
      },
      required: ["kind", "title", "payload"],
    },
  },
  (input, ctx) =>
    requestApproval({
      kind: input.kind,
      title: input.title,
      summary: input.summary,
      payload: input.payload,
      risk: input.risk ?? "medium",
      requestedBy: ctx.actor,
      skillKey: ctx.skillKey,
    }),
);

// ------------------------------------------------------------------ registry

export function toolSpecs(): Anthropic.Tool[] {
  return tools.map((t) => t.spec);
}

export async function runTool(
  name: string,
  input: unknown,
  ctx: ToolContext,
): Promise<{ ok: boolean; result: unknown }> {
  const def = tools.find((t) => t.spec.name === name);
  if (!def) return { ok: false, result: `Unknown tool: ${name}` };
  try {
    return { ok: true, result: await def.handler(input, ctx) };
  } catch (err) {
    // Surfaced back to the model so it can recover or explain, not swallowed.
    return { ok: false, result: `Error: ${(err as Error).message}` };
  }
}

export { json };
