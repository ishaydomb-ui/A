-- Beitenu schema.
-- Design rules:
--  1. Anything the agent asserts as fact must be answerable by a query against this schema.
--     The agent never answers "which coupons are left" from memory - it queries tracker_items.
--  2. Anything the agent DOES is written to `activity`, so both of us can audit it.
--  3. User-extensible concepts (coupons, watchlists, anything new) live in `trackers` /
--     `tracker_items` so a new rubric never needs a schema migration or a deploy.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- household

CREATE TABLE IF NOT EXISTS people (
  id          INTEGER PRIMARY KEY,
  key         TEXT NOT NULL UNIQUE,       -- 'ishay' | 'liran' | 'yanai' | 'berry'
  name        TEXT NOT NULL,
  name_he     TEXT,
  role        TEXT NOT NULL DEFAULT 'adult',  -- adult | child
  email       TEXT,
  phone       TEXT,
  color       TEXT,
  birthday    TEXT,
  notes       TEXT
);

-- ---------------------------------------------------------------- tasks & schedule

CREATE TABLE IF NOT EXISTS tasks (
  id            INTEGER PRIMARY KEY,
  title         TEXT NOT NULL,
  notes         TEXT,
  assignee_id   INTEGER REFERENCES people(id),
  due_at        TEXT,
  status        TEXT NOT NULL DEFAULT 'open',   -- open | done | dropped
  priority      TEXT NOT NULL DEFAULT 'normal', -- low | normal | high | urgent
  area          TEXT,                            -- kids | home | admin | health | money | other
  case_id       INTEGER REFERENCES cases(id),
  -- recurrence: an RRULE-ish string, e.g. 'FREQ=WEEKLY;BYDAY=MO,WE'
  recurrence    TEXT,
  recurs_from   INTEGER REFERENCES tasks(id),
  source        TEXT DEFAULT 'manual',           -- manual | agent | email | automation
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at);

-- Calendar events mirrored from Google + created locally.
CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY,
  external_id   TEXT UNIQUE,
  calendar_id   TEXT,
  title         TEXT NOT NULL,
  description   TEXT,
  location      TEXT,
  starts_at     TEXT NOT NULL,
  ends_at       TEXT,
  all_day       INTEGER NOT NULL DEFAULT 0,
  recurrence_id TEXT,                       -- google recurringEventId
  attendees     TEXT,                       -- json array of emails
  -- Semantic tags the agent derives once so questions are cheap later:
  -- e.g. 'pickup', 'dropoff', 'oncall', 'class', 'appointment', 'travel'
  kind          TEXT,
  subject_id    INTEGER REFERENCES people(id),  -- whose event (e.g. Berry's class)
  owner_id      INTEGER REFERENCES people(id),  -- who is responsible (who does pickup)
  source        TEXT DEFAULT 'google',
  raw           TEXT,
  synced_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(starts_at);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, starts_at);

CREATE TABLE IF NOT EXISTS reminders (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
  event_id    INTEGER REFERENCES events(id) ON DELETE CASCADE,
  text        TEXT,
  remind_at   TEXT NOT NULL,
  channel     TEXT NOT NULL DEFAULT 'push',  -- push | email | whatsapp
  target_id   INTEGER REFERENCES people(id),
  sent_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(sent_at, remind_at);

-- ---------------------------------------------------------------- cases (life events)

-- A "case" bundles everything about one live thread: the kindergarten appeal,
-- a renovation, an insurance claim. Deadlines and next actions stay visible.
CREATE TABLE IF NOT EXISTS cases (
  id             INTEGER PRIMARY KEY,
  title          TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'open',  -- open | waiting | closed
  summary        TEXT,
  subject_id     INTEGER REFERENCES people(id),
  opened_at      TEXT NOT NULL DEFAULT (datetime('now')),
  due_at         TEXT,
  next_action    TEXT,
  next_action_at TEXT,
  -- if nothing happens by this date, the agent drafts a follow-up
  chase_after    TEXT,
  reference      TEXT,                          -- external case number
  closed_at      TEXT
);

CREATE TABLE IF NOT EXISTS case_items (
  id          INTEGER PRIMARY KEY,
  case_id     INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,     -- email | document | event | task | note
  ref_id      TEXT,              -- gmail thread id / drive file id / local id
  title       TEXT,
  url         TEXT,
  occurred_at TEXT,
  body        TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_case_items_case ON case_items(case_id, occurred_at);

-- ---------------------------------------------------------------- documents

CREATE TABLE IF NOT EXISTS documents (
  id             INTEGER PRIMARY KEY,
  title          TEXT NOT NULL,
  kind           TEXT,            -- bill | receipt | policy | ticket | official | id | report
  drive_file_id  TEXT,
  url            TEXT,
  mime           TEXT,
  tags           TEXT,            -- json array
  case_id        INTEGER REFERENCES cases(id),
  vendor         TEXT,
  amount         REAL,
  currency       TEXT DEFAULT 'ILS',
  doc_date       TEXT,
  source_ref     TEXT,            -- gmail message id it came from
  summary        TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind, doc_date);

-- ---------------------------------------------------------------- money

CREATE TABLE IF NOT EXISTS budget_categories (
  id             INTEGER PRIMARY KEY,
  key            TEXT NOT NULL UNIQUE,
  name_he        TEXT NOT NULL,
  name_en        TEXT,
  bucket         TEXT NOT NULL DEFAULT 'base',  -- base | capped | fund
  monthly_budget REAL NOT NULL DEFAULT 0,
  notes          TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
  id           INTEGER PRIMARY KEY,
  occurred_on  TEXT NOT NULL,          -- transaction date, NOT credit-card billing date
  amount       REAL NOT NULL,
  currency     TEXT NOT NULL DEFAULT 'ILS',
  vendor       TEXT,
  description  TEXT,
  category_id  INTEGER REFERENCES budget_categories(id),
  payer_id     INTEGER REFERENCES people(id),
  card         TEXT,
  document_id  INTEGER REFERENCES documents(id),
  -- 'unclassified' items stay visible rather than silently guessed
  needs_review INTEGER NOT NULL DEFAULT 0,
  source       TEXT DEFAULT 'manual',  -- manual | email | csv | agent
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(occurred_on);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category_id, occurred_on);

-- ---------------------------------------------------------------- trackers (the extensibility primitive)

-- A tracker is a user-defined rubric: coupons, movie watchlist, gift ideas,
-- home maintenance, wine, anything. Adding one is a row, not a deploy.
CREATE TABLE IF NOT EXISTS trackers (
  id            INTEGER PRIMARY KEY,
  key           TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  icon          TEXT,
  description   TEXT,
  -- json array of {name,label,type,required?,options?}
  -- type: text | number | date | money | bool | select | url | person
  fields        TEXT NOT NULL,
  view          TEXT NOT NULL DEFAULT 'list',   -- list | board | grid | calendar
  -- json: {expire_field, expire_action, notify_before_days, dedupe_on, autofill}
  behaviors     TEXT,
  builtin       INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tracker_items (
  id          INTEGER PRIMARY KEY,
  tracker_id  INTEGER NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
  data        TEXT NOT NULL,                    -- json object matching tracker.fields
  status      TEXT NOT NULL DEFAULT 'active',   -- active | used | expired | archived
  expires_at  TEXT,                             -- lifted out of data for cheap querying
  owner_id    INTEGER REFERENCES people(id),
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tracker_items ON tracker_items(tracker_id, status, expires_at);

-- ---------------------------------------------------------------- skills (consistency)

-- A skill is a named, versioned procedure. The agent must follow it verbatim for
-- that class of work, and must record which skill it used.
CREATE TABLE IF NOT EXISTS skills (
  id           INTEGER PRIMARY KEY,
  key          TEXT NOT NULL UNIQUE,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL,          -- when to use it (matched by the agent)
  body         TEXT NOT NULL,          -- markdown: the actual procedure
  version      INTEGER NOT NULL DEFAULT 1,
  enabled      INTEGER NOT NULL DEFAULT 1,
  autonomy     TEXT NOT NULL DEFAULT 'ask',  -- auto | ask | never
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS skill_runs (
  id         INTEGER PRIMARY KEY,
  skill_id   INTEGER NOT NULL REFERENCES skills(id),
  version    INTEGER NOT NULL,
  input      TEXT,
  output     TEXT,
  actor      TEXT,
  ok         INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- approvals & audit

-- Anything irreversible, money-spending, or outward-facing lands here first.
CREATE TABLE IF NOT EXISTS approvals (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,       -- send_email | fill_cart | book | pay | submit_form | other
  title        TEXT NOT NULL,
  summary      TEXT,
  payload      TEXT NOT NULL,       -- json: exactly what will happen if approved
  risk         TEXT NOT NULL DEFAULT 'medium',  -- low | medium | high
  status       TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected | expired | done
  requested_by TEXT NOT NULL DEFAULT 'agent',
  skill_key    TEXT,
  decided_by   TEXT,
  decided_at   TEXT,
  result       TEXT,
  expires_at   TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);

CREATE TABLE IF NOT EXISTS activity (
  id           INTEGER PRIMARY KEY,
  actor        TEXT NOT NULL,       -- agent | ishay | liran | automation
  action       TEXT NOT NULL,       -- created_task | logged_expense | filled_cart | ...
  entity_type  TEXT,
  entity_id    TEXT,
  summary      TEXT NOT NULL,
  detail       TEXT,                -- json
  skill_key    TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_time ON activity(created_at DESC);

-- ---------------------------------------------------------------- automations

CREATE TABLE IF NOT EXISTS automations (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  description   TEXT,
  trigger_type  TEXT NOT NULL,     -- schedule | email_match | case_stale | tracker_expiring | pantry_expiring
  trigger_config TEXT NOT NULL,    -- json
  action_type   TEXT NOT NULL,     -- run_skill | agent_prompt | digest | reminder
  action_config TEXT NOT NULL,     -- json
  enabled       INTEGER NOT NULL DEFAULT 1,
  last_run_at   TEXT,
  last_result   TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- conversation / intake

CREATE TABLE IF NOT EXISTS conversations (
  id          INTEGER PRIMARY KEY,
  title       TEXT,
  channel     TEXT NOT NULL DEFAULT 'web',
  person_id   INTEGER REFERENCES people(id),
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL,   -- user | assistant | tool
  channel         TEXT NOT NULL DEFAULT 'web',  -- web | whatsapp | voice | telegram | email
  content         TEXT,
  -- for voice notes: where the audio lives and what we transcribed
  media_url       TEXT,
  transcript      TEXT,
  tool_calls      TEXT,            -- json
  person_id       INTEGER REFERENCES people(id),
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

-- ---------------------------------------------------------------- food

CREATE TABLE IF NOT EXISTS pantry_items (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  qty          REAL,
  unit         TEXT,
  category     TEXT,
  purchased_at TEXT,
  expires_at   TEXT,
  staple       INTEGER NOT NULL DEFAULT 0,   -- always keep in stock
  source       TEXT DEFAULT 'manual',
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pantry_expiry ON pantry_items(expires_at);

CREATE TABLE IF NOT EXISTS recipes (
  id           INTEGER PRIMARY KEY,
  title        TEXT NOT NULL,
  ingredients  TEXT NOT NULL,     -- json array of {name, qty, unit}
  steps        TEXT,
  tags         TEXT,              -- json array: kid-friendly, quick, vegetarian...
  prep_minutes INTEGER,
  servings     INTEGER DEFAULT 4,
  source_url   TEXT,
  last_made_at TEXT,
  rating       INTEGER,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meal_plan (
  id         INTEGER PRIMARY KEY,
  plan_date  TEXT NOT NULL,
  meal       TEXT NOT NULL DEFAULT 'dinner',  -- breakfast | lunch | dinner
  recipe_id  INTEGER REFERENCES recipes(id),
  title      TEXT,
  cook_id    INTEGER REFERENCES people(id),
  notes      TEXT,
  UNIQUE(plan_date, meal)
);

CREATE TABLE IF NOT EXISTS grocery_lists (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',  -- open | queued | in_cart | ordered | done
  chain       TEXT,
  est_total   REAL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  ordered_at  TEXT
);

CREATE TABLE IF NOT EXISTS grocery_items (
  id           INTEGER PRIMARY KEY,
  list_id      INTEGER NOT NULL REFERENCES grocery_lists(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  qty          REAL NOT NULL DEFAULT 1,
  unit         TEXT,
  category     TEXT,
  note         TEXT,
  checked      INTEGER NOT NULL DEFAULT 0,
  -- resolved against the public price-transparency catalogue
  item_code    TEXT,
  matched_name TEXT,
  est_price    REAL,
  source       TEXT DEFAULT 'manual',   -- manual | meal_plan | staple | agent
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_grocery_items_list ON grocery_items(list_id);

-- Catalogue cached from the government-mandated price transparency feeds.
CREATE TABLE IF NOT EXISTS store_products (
  id          INTEGER PRIMARY KEY,
  chain       TEXT NOT NULL,
  store_id    TEXT,
  item_code   TEXT NOT NULL,
  name        TEXT NOT NULL,
  manufacturer TEXT,
  unit        TEXT,
  qty         TEXT,
  price       REAL,
  promo       TEXT,           -- json
  updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(chain, store_id, item_code)
);
CREATE INDEX IF NOT EXISTS idx_products_name ON store_products(chain, name);

-- ---------------------------------------------------------------- credentials (encrypted)

CREATE TABLE IF NOT EXISTS credentials (
  id          INTEGER PRIMARY KEY,
  service     TEXT NOT NULL UNIQUE,   -- 'shufersal' | 'tivtaam' | ...
  username    TEXT,
  secret_enc  TEXT NOT NULL,          -- AES-256-GCM, key from CREDENTIALS_KEY
  session_enc TEXT,                   -- stored cookies so we log in rarely
  notes       TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- knowledge graph

-- Lightweight entity/relation store so cross-domain questions resolve by
-- traversal instead of needing a bespoke query per question.
CREATE TABLE IF NOT EXISTS entities (
  id         INTEGER PRIMARY KEY,
  kind       TEXT NOT NULL,    -- person | place | org | thing | service
  name       TEXT NOT NULL,
  attrs      TEXT,             -- json
  person_id  INTEGER REFERENCES people(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(kind, name)
);

CREATE TABLE IF NOT EXISTS relations (
  id          INTEGER PRIMARY KEY,
  from_id     INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  rel         TEXT NOT NULL,   -- attends | treats | insures | supplies | parent_of
  to_id       INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  attrs       TEXT,
  valid_from  TEXT,
  valid_to    TEXT
);
CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_id, rel);

-- Free-form durable memory the agent can write and search.
CREATE TABLE IF NOT EXISTS notes (
  id         INTEGER PRIMARY KEY,
  topic      TEXT,
  body       TEXT NOT NULL,
  person_id  INTEGER REFERENCES people(id),
  pinned     INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- auth & sync
-- Added when Google sign-in landed. Applied idempotently like everything above.

-- OAuth tokens per person, per provider. Refresh tokens are long-lived
-- credentials, so they are encrypted with the same key as store logins and are
-- never placed in a model prompt.
CREATE TABLE IF NOT EXISTS oauth_tokens (
  id                INTEGER PRIMARY KEY,
  person_id         INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  provider          TEXT NOT NULL DEFAULT 'google',
  access_token_enc  TEXT,
  refresh_token_enc TEXT,
  expires_at        TEXT,
  scope             TEXT,
  updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(person_id, provider)
);

-- Per-calendar incremental sync state, so routine syncs cost one small request
-- instead of re-reading the year.
CREATE TABLE IF NOT EXISTS calendar_sync (
  id           INTEGER PRIMARY KEY,
  person_id    INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  calendar_id  TEXT NOT NULL,
  summary      TEXT,
  sync_token   TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  last_synced  TEXT,
  last_result  TEXT,
  UNIQUE(person_id, calendar_id)
);

-- ---------------------------------------------------------------- household facts
-- The "what's Yanai's ID number / where did we put the drill / when is the
-- licence due" store. One table covers three shapes of question:
--   * a standing fact      -> value only          ("mum's building code")
--   * a dated occurrence   -> occurred_on set     ("last blood test")
--   * an expiring fact     -> valid_until set     ("driving licence")
-- Keeping them together means one capture path and one lookup path, instead of
-- three half-used features.
CREATE TABLE IF NOT EXISTS facts (
  id            INTEGER PRIMARY KEY,
  -- what/who this is about: 'yanai', 'mum', 'garage', 'car', 'flat'
  subject       TEXT NOT NULL,
  label         TEXT NOT NULL,          -- 'ID number', 'building code', 'location'
  -- Plaintext, or AES-256-GCM ciphertext when sensitive = 1.
  value         TEXT NOT NULL,
  sensitive     INTEGER NOT NULL DEFAULT 0,
  category      TEXT,                   -- identity | access | location | medical
                                        -- | vehicle | admin | contact | other
  occurred_on   TEXT,                   -- set => a dated occurrence
  valid_until   TEXT,                   -- set => expires and should remind
  remind_days_before INTEGER NOT NULL DEFAULT 30,
  person_id     INTEGER REFERENCES people(id),
  source        TEXT DEFAULT 'agent',   -- agent | manual | whatsapp | email
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject, label);
CREATE INDEX IF NOT EXISTS idx_facts_expiry ON facts(valid_until);
CREATE INDEX IF NOT EXISTS idx_facts_occurred ON facts(subject, label, occurred_on);

-- ---------------------------------------------------------------- deliveries
-- "What's in transit right now" - assembled from order and shipping emails.
-- Keyed on vendor + order reference so the four emails a single order generates
-- (confirmed, shipped, out for delivery, delivered) collapse into one row that
-- moves through its states rather than four separate entries.
CREATE TABLE IF NOT EXISTS deliveries (
  id            INTEGER PRIMARY KEY,
  vendor        TEXT NOT NULL,
  order_ref     TEXT,
  description   TEXT,
  -- ordered | shipped | in_transit | ready_for_pickup | delivered | cancelled
  status        TEXT NOT NULL DEFAULT 'ordered',
  carrier       TEXT,
  tracking_url  TEXT,
  amount        REAL,
  currency      TEXT DEFAULT 'ILS',
  ordered_at    TEXT,
  expected_at   TEXT,
  delivered_at  TEXT,
  last_update   TEXT NOT NULL DEFAULT (datetime('now')),
  -- gmail message id the latest state came from, so a wrong call is traceable
  source_ref    TEXT,
  source        TEXT DEFAULT 'email',
  person_id     INTEGER REFERENCES people(id),
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(vendor, order_ref)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status, last_update);
