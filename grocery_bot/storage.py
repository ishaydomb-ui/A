"""SQLite-backed persistence for the base list, ad-hoc queue and pending
ambiguity decisions.

Kept intentionally simple (stdlib sqlite3, no ORM) since this is a
single-household, low-volume personal tool.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import AdHocRequest, BaseListItem
from .prices import PricedProduct, PromotionItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS base_list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    search_terms TEXT NOT NULL DEFAULT '{}',
    default_quantity INTEGER NOT NULL DEFAULT 1,
    tags TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS adhoc_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_ambiguities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT NOT NULL,
    original_term TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    candidates TEXT NOT NULL DEFAULT '[]',
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Catalog mirrored from the public price feed (see prices.py). Rebuilt
-- wholesale on each refresh rather than merged: the feed publishes full
-- snapshots, and a merge would silently keep items the branch has since
-- delisted.
CREATE TABLE IF NOT EXISTS catalog_products (
    item_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manufacturer TEXT NOT NULL DEFAULT '',
    price REAL NOT NULL,
    unit_of_measure_price REAL NOT NULL DEFAULT 0,
    unit_of_measure TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '',
    is_weighted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS catalog_promotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    promotion_id TEXT NOT NULL,
    description TEXT NOT NULL,
    item_code TEXT NOT NULL,
    discounted_price REAL NOT NULL DEFAULT 0,
    min_qty REAL NOT NULL DEFAULT 1,
    discount_rate REAL NOT NULL DEFAULT 0,
    starts_at TEXT NOT NULL DEFAULT '',
    ends_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_catalog_promotions_item
    ON catalog_promotions(item_code);

CREATE TABLE IF NOT EXISTS catalog_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Which concrete product the household picked for a given search term.
-- Without this, every cycle re-asks: a real search for "חלב 3%" returns
-- 20 tiles, so *every* item would be "ambiguous" forever and the bot
-- would fire a dozen questions per run — the opposite of the project's
-- "minimum user dependency" rule.
CREATE TABLE IF NOT EXISTS preferred_products (
    store TEXT NOT NULL,
    term TEXT NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    chosen_at TEXT NOT NULL,
    PRIMARY KEY (store, term)
);

-- An order cycle asked for while the Israeli exit node was down (the exit
-- runs on a TV box at home that gets switched off, so this is routine, not
-- an error). The cycle is held here and run automatically once the exit
-- comes back, so the user never has to notice the outage or re-issue the
-- request. chat_id is stored because the report has to reach whoever asked,
-- in a conversation that may be long over by the time it runs.
CREATE TABLE IF NOT EXISTS deferred_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    requested_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);

-- Products the household buys regularly enough to be worth proposing,
-- with how reliably they appear and which part of the shop they live in.
-- Rebuilt from purchase history, but carries its own learning columns so
-- the user's actual choices override what the history inferred.
--
-- `store` is on the row rather than assumed, because a second chain is
-- coming: the same product is bought at whichever shop is cheaper that
-- week, and tiers have to be able to merge or stay separate per store
-- without a migration.
CREATE TABLE IF NOT EXISTS stock_items (
    store TEXT NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'C',
    share REAL NOT NULL DEFAULT 0,
    default_quantity INTEGER NOT NULL DEFAULT 1,
    amount REAL,
    unit TEXT NOT NULL DEFAULT '',
    -- How often the user kept vs removed this when it was proposed. The
    -- history cannot see what was bought at the other chain, so these are
    -- the only signal for "we stopped needing this".
    picked_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    -- Days between purchases as measured by the chain itself, where it
    -- publishes that. Tiv Taam does, and counts in-store purchases that
    -- never appear in the online history, so it is strictly better than
    -- our own 1/share estimate. NULL means nobody measured it.
    interval_days REAL,
    -- The manufacturer EAN, where the chain will tell us. This is what
    -- lets the same product bought at two chains merge into one habit;
    -- without it a Self-Point product id and a Shufersal sku look like
    -- two unrelated products. NULL for loose produce, which no chain
    -- barcodes.
    barcode TEXT,
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (store, product_code)
);

-- One round of "here is what I propose to buy" awaiting the user's ticks.
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS proposal_items (
    proposal_id INTEGER NOT NULL,
    store TEXT NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 1,
    amount REAL,
    unit TEXT NOT NULL DEFAULT '',
    selected INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (proposal_id, store, product_code)
);

-- One row per product per day: the closing price and best usable promo.
-- The feed only ever shows "now", so without this there is no way to say
-- whether 10₪ for an item is genuinely rare or its regular fortnightly
-- "sale" — which is exactly the difference between worth stockpiling and
-- never-pay-full-price. Pruned on a rolling window; see prune_price_history.
CREATE TABLE IF NOT EXISTS price_history (
    item_code TEXT NOT NULL,
    day TEXT NOT NULL,             -- YYYY-MM-DD
    price REAL NOT NULL,
    promo_price REAL,              -- best usable promo that day, if any
    PRIMARY KEY (item_code, day)
);

-- Prices seen at a chain, keyed by the manufacturer's EAN barcode.
--
-- The barcode is what makes cross-chain comparison honest: the same
-- carton of milk is 7290004131074 in both chains, so no Hebrew name
-- matching is involved and there is nothing to get subtly wrong.
--
-- Shufersal prices are not duplicated here — they already live in
-- catalog_products, whose item_code *is* the EAN. This table holds the
-- chains that have no public feed, Tiv Taam today, whose prices we only
-- learn by observing them.
--
-- observed_at matters and is never assumed to be today: a price taken
-- from a July order is what the household paid in July, and comparing it
-- against a live Shufersal price without saying so would be a lie
-- dressed as a saving.
CREATE TABLE IF NOT EXISTS store_prices (
    store TEXT NOT NULL,
    barcode TEXT NOT NULL,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    observed_at TEXT NOT NULL,     -- YYYY-MM-DD
    source TEXT NOT NULL DEFAULT 'order',  -- order | search
    PRIMARY KEY (store, barcode, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_store_prices_barcode
    ON store_prices(barcode);

-- Promotions published by a chain other than Shufersal. Kept apart from
-- catalog_promotions on purpose: that table keys on Shufersal's own
-- internal item_code, while every portal chain publishes promotions
-- against the manufacturer's EAN. The barcode key is the better one —
-- it joins straight to store_prices with no name matching anywhere in
-- the path, which is the failure mode this project keeps paying for.
CREATE TABLE IF NOT EXISTS store_promotions (
    store TEXT NOT NULL,
    barcode TEXT NOT NULL,
    promotion_id TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    discounted_price REAL NOT NULL,
    min_qty REAL NOT NULL DEFAULT 1,
    starts_at TEXT NOT NULL DEFAULT '',
    ends_at TEXT NOT NULL DEFAULT '',
    observed_at TEXT NOT NULL,     -- YYYY-MM-DD
    PRIMARY KEY (store, barcode, promotion_id)
);

CREATE INDEX IF NOT EXISTS idx_store_promotions_store
    ON store_promotions(store);

-- Monthly confirmations of a benefit the bot cannot observe in advance.
-- The loadable card only shows up in the data after an order is paid, by
-- which point an unloaded month is already lost, so the household is
-- asked and their answer recorded.
CREATE TABLE IF NOT EXISTS benefit_confirmations (
    kind TEXT NOT NULL,
    month TEXT NOT NULL,           -- YYYY-MM
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (kind, month)
);

-- What the household reported throwing away. The only signal here that
-- cannot be derived from any store: order history shows what was bought,
-- never what was eaten.
CREATE TABLE IF NOT EXISTS waste_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name TEXT NOT NULL,
    fraction REAL NOT NULL DEFAULT 0.5,
    reported_on TEXT NOT NULL,     -- YYYY-MM-DD
    reported_by TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_waste_item ON waste_reports(item_name);

-- Every cart-add that did not reach the cart. Added 2026-09-10 after
-- Ishay asked whether the daily runs retry what failed before. They do:
-- an ad-hoc request stays pending until it is bought, and the standing
-- list is rebuilt each cycle so a failed term is simply on it again.
-- What was missing is *memory* — only `report.added` was persisted, so
-- an item that fails on every single run was retried forever and nobody
-- could see the pattern. The household got a "לא נמצא (N)" count in each
-- report and nothing that survived it.
--
-- One row per attempt, not a counter: a count cannot answer "since
-- when", "on which chain", or "did it start failing after the feed
-- changed" — and those are the questions that decide whether a term is
-- wrong or a product is delisted.
CREATE TABLE IF NOT EXISTS cart_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name TEXT NOT NULL,
    store TEXT NOT NULL,
    status TEXT NOT NULL,          -- "not_found" | "error"
    detail TEXT NOT NULL DEFAULT '',
    failed_at TEXT NOT NULL        -- ISO8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_cart_failures_item
    ON cart_failures(store, item_name);
CREATE INDEX IF NOT EXISTS idx_cart_failures_at ON cart_failures(failed_at);

-- When each product was last actually bought. Separate from stock_items
-- because that table is rebuilt wholesale on every nightly sync, and a
-- purchase date stored there would be thrown away with it.
CREATE TABLE IF NOT EXISTS last_purchase (
    store TEXT NOT NULL,
    product_code TEXT NOT NULL,
    purchased_on TEXT NOT NULL,   -- YYYY-MM-DD
    PRIMARY KEY (store, product_code)
);

-- When orders were actually placed, to learn the household's cadence.
-- Fed by the nightly sync from the store's own order history, so manual
-- orders count too.
CREATE TABLE IF NOT EXISTS order_log (
    order_code TEXT PRIMARY KEY,
    store TEXT NOT NULL,
    placed_at TEXT NOT NULL,
    total REAL,
    item_count INTEGER
);

-- Small key/value state (last digest sent, last chat seen, sync marks).
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# Columns added after the first version shipped. SQLite has no
# "ADD COLUMN IF NOT EXISTS", and the database already holds a real list,
# so each is added only when missing rather than recreating the table.
_ADDED_COLUMNS = {
    # Added 2026-09-01 when Tiv Taam's smart list turned out to publish a
    # measured purchase interval, which beats our 1/share estimate.
    "stock_items": {"interval_days": "REAL", "barcode": "TEXT"},
    "base_list_items": {
        "amount": "REAL",
        "unit": "TEXT NOT NULL DEFAULT ''",
        "brand": "TEXT NOT NULL DEFAULT ''",
    },
    "adhoc_requests": {
        "amount": "REAL",
        "unit": "TEXT NOT NULL DEFAULT ''",
        "brand": "TEXT NOT NULL DEFAULT ''",
        # The lifecycle, added 2026-09-11. `consumed` is a single bit and
        # cannot answer "did that actually arrive": a request that reached
        # a cart, one still waiting on a choice, one in an order placed
        # yesterday and one delivered all looked identical. Kept alongside
        # `consumed` rather than replacing it, so an older row reads
        # correctly and nothing that queries the flag breaks.
        "status": "TEXT NOT NULL DEFAULT ''",
        "status_at": "TEXT NOT NULL DEFAULT ''",
        "store": "TEXT NOT NULL DEFAULT ''",
    },
    # Full candidate detail (price/size/brand) behind each choice. The
    # older `candidates` column holds names only, which are duplicated
    # across brands and so cannot be told apart in a chooser.
    "pending_ambiguities": {
        "candidate_cards": "TEXT NOT NULL DEFAULT '[]'",
    },
}


# Promotions this household cannot actually use. Confirmed with them
# rather than guessed, because the categories look alike in the feed and
# the split is not obvious:
#
#   - Shufersal's own club ("תו זהב") and store credit card: they hold
#     neither, so these prices are unreachable.
#   - Sodexo/Cibus meal vouchers: employer-issued cards they do not have.
#     By far the largest category in the feed (61% of rows).
#
# Manufacturer coupons ARE kept: those are handed out by the brand, not
# gated behind a Shufersal membership, and the household does use them.
# So are ordinary quantity and price promotions.
_UNUSABLE_MARKERS = (
    "תו זהב",
    "מועדון",
    "אשראי שופרסל",
    "כ.אשראי",
    "כרטיס אשראי",
    "סיבוס",
    "סודקסו",
)


def is_public_promotion(description: str) -> bool:
    """True when this household could actually claim the promotion."""
    text = description or ""
    return not any(marker in text for marker in _UNUSABLE_MARKERS)


# An explicit "stop suggesting this" outweighs any amount of past
# buying. Large enough that history cannot out-vote the user, finite so a
# genuine change of habit can still recover the item.
_STOCK_SUPPRESS_WEIGHT = 50


def _like_contains(term: str) -> str:
    """A `%term%` LIKE pattern with the user's own wildcards neutralised.

    Grocery queries are full of literal `%` — "חלב 3%", "קוטג' 5%" — and a
    bare `f"%{term}%"` hands that `%` to SQL as a wildcard, so "חלב 3%"
    silently matches "חלב 36 גרם" (chocolate) as well as milk. `_` is the
    single-char wildcard with the same problem. Escape all three (`\\`
    first, so it doesn't double-escape the escapes it adds) and pair this
    with `ESCAPE '\\'` in the query.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _name_match_rank(folded_term: str, name: str) -> int:
    """How well a product name matches a search term, lowest is best.

    Shared by `search_products` and `cross_chain_prices` so both answer
    "חלב" with milk before שוקולד חלב, rather than each guessing
    independently. 0 = the term is the name's own first whole word; 1 =
    the term appears as a whole word elsewhere (bounded by
    whitespace/edges on *both* sides); 2 = the term only starts a word
    that continues past it (e.g. "חלבה"/halva or "חלבי"/dairy-flavoured
    for "חלב" — a real bug once, when a blind `str.startswith` put these
    in the top tier ahead of actual milk); 3 = anywhere else, a bare
    substring with no word boundary at all.
    """
    folded_name = _fold_apostrophes(name)
    if re.match(rf"{re.escape(folded_term)}(?:\s|$)", folded_name):
        return 0
    if re.search(rf"(?:^|\s){re.escape(folded_term)}(?:\s|$)", folded_name):
        return 1
    if re.search(rf"(?:^|\s){re.escape(folded_term)}", folded_name):
        return 2
    return 3


# Product names are inconsistent about the apostrophe — "קוטג 5%" (none),
# "קוטג' 5%" (ASCII), "קוטג׳ 5%" (Hebrew geresh) — so "קוטג' 5%" found one
# row where "קוטג 5%" found three. Fold the apostrophe family away on both
# the query and the stored name before matching. (Ishay-approved
# normalisation, 2026-09-04.)
_APOSTROPHES = "'׳’`"
_FOLD_TABLE = str.maketrans("", "", _APOSTROPHES)


def _fold_apostrophes(term: str) -> str:
    """Drop the apostrophe family so the variants collapse to one form.

    Registered on every connection as the SQL function `fold(...)` so the
    stored column can be folded the same way — building a REPLACE() chain
    in SQL text instead hits the ASCII apostrophe as a string-literal
    delimiter and produces invalid SQL.
    """
    return (term or "").translate(_FOLD_TABLE)


class Storage:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # WAL lets a second process (the household's other bot, which adds
        # to this list on behalf of the other partner) write without
        # colliding with the grocery bot mid-cycle. In the default
        # rollback-journal mode a concurrent writer gets "database is
        # locked" instead, and a long cart cycle holds the file for
        # minutes at a time.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        # Fold the apostrophe family in SQL the same way Python does, so a
        # query and a stored name that differ only by ' / ׳ still match.
        conn.create_function("fold", 1, _fold_apostrophes, deterministic=True)
        return conn

    # -- base list -----------------------------------------------------

    def add_base_list_item(
        self,
        name: str,
        search_terms: dict[str, str] | None = None,
        default_quantity: int = 1,
        tags: list[str] | None = None,
        amount: float | None = None,
        unit: str = "",
        brand: str = "",
    ) -> int:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO base_list_items "
                "(name, search_terms, default_quantity, tags, amount, unit, brand) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    json.dumps(search_terms or {}, ensure_ascii=False),
                    default_quantity,
                    json.dumps(tags or [], ensure_ascii=False),
                    amount,
                    unit,
                    brand,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def deactivate_base_item_by_name(self, name: str) -> str | None:
        """Drop an item from the standing list by (fuzzy) name.

        Matched loosely because the request arrives as speech — "תוריד
        את הטונה" should find "טונה". Returns the name actually removed
        so the bot can confirm *what* it did, rather than claiming
        success for something the user didn't mean.
        """
        needle = name.strip()
        if not needle:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, name FROM base_list_items WHERE active = 1 AND "
                "(name = ? OR name LIKE ? ESCAPE '\\' OR ? LIKE '%' || name || '%') "
                "ORDER BY LENGTH(name) LIMIT 1",
                (needle, _like_contains(needle), needle),
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE base_list_items SET active = 0 WHERE id = ?", (row["id"],))
            conn.commit()
            return row["name"]

    def deactivate_all_base_items(self) -> int:
        """Retire the whole standing list, keeping the rows for history.

        Used when re-deriving the list from order history: without it a
        re-run appends a second copy of every item instead of replacing.
        Deactivating rather than deleting keeps any remembered choice
        that points at an old row meaningful.
        """
        with closing(self._connect()) as conn:
            cursor = conn.execute("UPDATE base_list_items SET active = 0 WHERE active = 1")
            conn.commit()
            return cursor.rowcount

    def list_active_base_items(self) -> list[BaseListItem]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM base_list_items WHERE active = 1 ORDER BY id"
            ).fetchall()
        return [self._row_to_base_item(row) for row in rows]

    def import_base_list_from_yaml(self, yaml_path: str) -> int:
        """Load items from a YAML file (see data/base_list.example.yaml).

        Existing items are left untouched; this only appends. Safe to call
        once during initial setup.
        """
        import yaml

        with open(yaml_path, encoding="utf-8") as fh:
            items = yaml.safe_load(fh) or []
        count = 0
        for item in items:
            self.add_base_list_item(
                name=item["name"],
                search_terms=item.get("search_terms", {}),
                default_quantity=item.get("default_quantity", 1),
                tags=item.get("tags", []),
            )
            count += 1
        return count

    @staticmethod
    def _row_to_base_item(row: sqlite3.Row) -> BaseListItem:
        keys = row.keys()
        return BaseListItem(
            id=row["id"],
            name=row["name"],
            search_terms=json.loads(row["search_terms"]),
            default_quantity=row["default_quantity"],
            tags=json.loads(row["tags"]),
            active=bool(row["active"]),
            amount=row["amount"] if "amount" in keys else None,
            unit=(row["unit"] if "unit" in keys else "") or "",
            brand=(row["brand"] if "brand" in keys else "") or "",
        )

    # -- ad-hoc queue ----------------------------------------------------

    def add_adhoc_request(
        self,
        text: str,
        requested_by: str,
        quantity: int = 1,
        amount: float | None = None,
        unit: str = "",
        brand: str = "",
    ) -> int:
        """Queue one ad-hoc item, folding exact repeats onto the open one.

        Repeats are the norm, not an edge case: when the bot looks broken
        (as it did while the NLU was dead) people resend the same message,
        and each copy used to become its own queue entry — the same
        question was then asked three times in one cycle. Same text while
        the first is still pending means "I want this", not "I want three".
        """
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT id FROM adhoc_requests WHERE consumed = 0 AND text = ?",
                (text.strip(),),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO adhoc_requests (text, requested_by, quantity, created_at, amount, unit, brand) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    text.strip(),
                    requested_by,
                    quantity,
                    datetime.now(timezone.utc).isoformat(),
                    amount,
                    unit,
                    brand,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def remove_adhoc_by_name(self, name: str) -> str | None:
        """Drop a pending ad-hoc request by fuzzy name; returns what was removed."""
        needle = name.strip()
        if not needle:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, text FROM adhoc_requests WHERE consumed = 0 AND "
                "(text = ? OR text LIKE ? ESCAPE '\\' OR ? LIKE '%' || text || '%') "
                "ORDER BY LENGTH(text) LIMIT 1",
                (needle, _like_contains(needle), needle),
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE adhoc_requests SET consumed = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            return row["text"]

    def list_pending_adhoc(self) -> list[AdHocRequest]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM adhoc_requests WHERE consumed = 0 ORDER BY id"
            ).fetchall()
        return [
            AdHocRequest(
                id=row["id"],
                text=row["text"],
                requested_by=row["requested_by"],
                created_at=row["created_at"],
                quantity=row["quantity"],
                consumed=bool(row["consumed"]),
                amount=row["amount"] if "amount" in row.keys() else None,
                unit=(row["unit"] if "unit" in row.keys() else "") or "",
                brand=(row["brand"] if "brand" in row.keys() else "") or "",
            )
            for row in rows
        ]

    def mark_adhoc_consumed(self, request_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE adhoc_requests SET consumed = 1 WHERE id = ?", (request_id,)
            )
            conn.commit()

    # -- what happened to a request after it left the queue ---------------
    #
    # `consumed` answers "is it still waiting". It cannot answer "did that
    # actually arrive", which is the question a person asks three days
    # later, and it cannot tell a request sitting in an order placed
    # yesterday from one that never made it into a cart.
    #
    # The states, and what moves between them:
    #
    #   in_cart      the bot put it in a cart (which cart is recorded)
    #   awaiting     a choice was put to the household and is unanswered
    #   shopped      the household said the shop is done — their word, and
    #                still the only trigger, per the 2026-09-07 decision
    #   confirmed    it turned up in the chain's own order history
    #   delivered    the order was delivered, where that is knowable
    #
    # `confirmed` exists because of a measured 36-hour gap: an order placed
    # on 09-07 was absent from Shufersal's own history for about a day and
    # a half. So between `shopped` and `confirmed` the honest line is
    # "דיווחת שהקנייה הושלמה; ממתין לפירוט מהחנות" — evidence arriving
    # late, never a replacement for what the household said.

    ADHOC_STATES = ("in_cart", "awaiting", "shopped", "confirmed", "delivered")

    def set_adhoc_status(self, request_id: int, status: str, store: str = "") -> None:
        if status not in self.ADHOC_STATES:
            raise ValueError(f"unknown request status {status!r}")
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE adhoc_requests SET status = ?, status_at = ?, "
                "store = CASE WHEN ? <> '' THEN ? ELSE store END WHERE id = ?",
                (status, stamp, store, store, request_id),
            )
            conn.commit()

    def advance_adhoc_status(self, from_status: str, to_status: str, store: str = "") -> int:
        """Move every request in one state to the next. Returns how many.

        Used when the household reports a shop: everything that reached a
        cart at that chain becomes `shopped` in one step, because that is
        exactly what their message means.
        """
        if to_status not in self.ADHOC_STATES:
            raise ValueError(f"unknown request status {to_status!r}")
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sql = "UPDATE adhoc_requests SET status = ?, status_at = ? WHERE status = ?"
        params = [to_status, stamp, from_status]
        if store:
            sql += " AND store = ?"
            params.append(store)
        with closing(self._connect()) as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount

    def adhoc_by_status(self, status: str) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, text, store, status, status_at, requested_by "
                "FROM adhoc_requests WHERE status = ? ORDER BY id",
                (status,),
            ).fetchall()
        return [dict(row) for row in rows]

    def adhoc_status_counts(self) -> dict:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM adhoc_requests "
                "WHERE status <> '' GROUP BY status"
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    # -- order cycles deferred until the Israeli exit is back --------------

    def defer_cycle(self, chat_id: int, requested_by: str) -> int:
        """Queue an order cycle to run when the exit node is reachable.

        Collapses onto any cycle already waiting rather than stacking:
        asking twice while the TV box is off means "I want a cycle", not
        "run two identical cycles back to back" — the second would find an
        already-filled cart and add everything a second time.
        """
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT id FROM deferred_cycles WHERE done = 0 ORDER BY id LIMIT 1"
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            cursor = conn.execute(
                "INSERT INTO deferred_cycles (chat_id, requested_by, created_at) VALUES (?, ?, ?)",
                (chat_id, requested_by, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def pending_deferred_cycle(self) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM deferred_cycles WHERE done = 0 ORDER BY id LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_deferred_cycle_done(self, cycle_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE deferred_cycles SET done = 1 WHERE id = ?", (cycle_id,))
            conn.commit()

    # -- stock items (what is worth proposing) -----------------------------

    def replace_stock_items(self, store: str, items: list) -> int:
        """Refresh the proposable set, preserving what the user taught us.

        picked/skipped counts survive a rebuild on purpose: they are the
        only signal that is not visible in the purchase history (which
        cannot see the other chain), so a re-derivation must never wipe
        them.
        """
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn:
            learned = {
                row["product_code"]: (row["picked_count"], row["skipped_count"])
                for row in conn.execute(
                    "SELECT product_code, picked_count, skipped_count FROM stock_items "
                    "WHERE store = ?",
                    (store,),
                )
            }
            conn.execute("DELETE FROM stock_items WHERE store = ?", (store,))
            conn.executemany(
                "INSERT INTO stock_items (store, product_code, product_name, department, "
                "category, tier, share, default_quantity, amount, unit, picked_count, "
                "skipped_count, interval_days, barcode, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        store,
                        item.product_code,
                        item.product_name,
                        item.department,
                        item.category,
                        item.tier,
                        item.share,
                        item.default_quantity,
                        item.amount,
                        item.unit,
                        learned.get(item.product_code, (0, 0))[0],
                        learned.get(item.product_code, (0, 0))[1],
                        getattr(item, "interval_days", None),
                        getattr(item, "barcode", None),
                        now,
                    )
                    for item in items
                ],
            )
            conn.commit()
        return len(items)

    def list_stock_items(self, store: str) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM stock_items WHERE store = ? ORDER BY share DESC", (store,)
            ).fetchall()
        return [dict(row) for row in rows]

    def confirm_benefit(self, kind: str, month: str) -> None:
        """Record that the household used a benefit this month."""
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO benefit_confirmations (kind, month, confirmed_at) "
                "VALUES (?, ?, ?)",
                (kind, month, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def benefit_confirmed(self, kind: str, month: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM benefit_confirmations WHERE kind = ? AND month = ?",
                (kind, month),
            ).fetchone()
        return row is not None

    def record_cart_failures(self, results, when=None) -> int:
        """Remember the items a cycle could not put in the cart.

        Successes were always persisted; failures were reported once and
        discarded, so a term that fails on every run looked identical to
        one failing for the first time. Recording an attempt per row —
        rather than bumping a counter — is what lets a later question be
        "since when, and on which chain", which is the difference between
        a wrong search term and a delisted product.
        """
        from datetime import datetime, timezone

        stamp = when or datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [
            (r.item_name, r.store, r.status, (r.detail or "")[:200], stamp)
            for r in (results or [])
            if getattr(r, "status", "") in ("not_found", "error") and r.item_name
        ]
        if not rows:
            return 0
        with closing(self._connect()) as conn:
            conn.executemany(
                "INSERT INTO cart_failures "
                "(item_name, store, status, detail, failed_at) VALUES (?,?,?,?,?)",
                rows,
            )
            conn.commit()
        return len(rows)

    def repeat_failures(self, min_runs: int = 3, days: int = 60) -> list[dict]:
        """Items that have failed on `min_runs` distinct runs recently.

        Distinct *runs*, counted by timestamp, not distinct rows: one
        cycle writes many rows at the same instant, and counting rows
        would let a single bad run masquerade as a persistent problem.

        Returns newest-first by last failure, because a term that stopped
        failing last week is a different matter from one that failed this
        morning.
        """
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
            timespec="seconds"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT item_name, store,
                       COUNT(DISTINCT failed_at) AS runs,
                       MIN(failed_at) AS first_failed,
                       MAX(failed_at) AS last_failed,
                       MAX(detail) AS detail
                  FROM cart_failures
                 WHERE failed_at >= ?
              GROUP BY store, item_name
                HAVING runs >= ?
              ORDER BY last_failed DESC, runs DESC
                """,
                (since, min_runs),
            ).fetchall()
        return [
            {"item_name": r[0], "store": r[1], "runs": r[2],
             "first_failed": r[3], "last_failed": r[4], "detail": r[5] or ""}
            for r in rows
        ]

    def clear_cart_failures(self, item_name: str, store: str = "") -> int:
        """Forget an item's failures — after a term is fixed or it is dropped."""
        with closing(self._connect()) as conn:
            if store:
                cur = conn.execute(
                    "DELETE FROM cart_failures WHERE item_name = ? AND store = ?",
                    (item_name, store),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM cart_failures WHERE item_name = ?", (item_name,)
                )
            conn.commit()
            return cur.rowcount

    def record_waste(self, rows: list[tuple]) -> int:
        """Store waste reports: (item_name, fraction, reported_on, by)."""
        with closing(self._connect()) as conn:
            cur = conn.executemany(
                "INSERT INTO waste_reports (item_name, fraction, reported_on, "
                "reported_by) VALUES (?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            return cur.rowcount

    def waste_summary(self) -> dict:
        """item_name -> (number of reports, total fraction wasted)."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT item_name, COUNT(*) AS reports, SUM(fraction) AS total "
                "FROM waste_reports GROUP BY item_name"
            ).fetchall()
        return {r["item_name"]: (r["reports"], float(r["total"] or 0)) for r in rows}

    def recent_waste(self, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM waste_reports ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def record_last_purchase(self, store: str, entries: list[tuple[str, str]]) -> int:
        """Remember when each product was last actually bought.

        Kept apart from stock_items because that table is rebuilt wholesale
        from order history on every sync; a purchase date living there would
        be lost and re-derived each night for no reason.
        """
        with closing(self._connect()) as conn:
            cur = conn.executemany(
                "INSERT INTO last_purchase (store, product_code, purchased_on) "
                "VALUES (?, ?, ?) ON CONFLICT(store, product_code) DO UPDATE SET "
                "purchased_on = MAX(purchased_on, excluded.purchased_on)",
                [(store, code, day) for code, day in entries],
            )
            conn.commit()
            return cur.rowcount

    def last_purchase_dates(self, store: str) -> dict:
        """product_code -> date last bought."""
        from datetime import datetime as _dt

        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT product_code, purchased_on FROM last_purchase WHERE store = ?",
                (store,),
            ).fetchall()
        out = {}
        for row in rows:
            try:
                out[row["product_code"]] = _dt.strptime(
                    row["purchased_on"][:10], "%Y-%m-%d"
                ).date()
            except (ValueError, TypeError):
                continue
        return out

    def suppress_stock_item_by_name(self, name: str, store: str = "shufersal") -> str | None:
        """Stop proposing a learned recurring product, by (fuzzy) name.

        The third place a "תוריד X" can mean. The standing list and the
        ad-hoc queue are things the user typed; this is a product the bot
        *learned* from order history and proposes on its own. Asking to
        remove one of those found nothing before this existed, because the
        item was never on either typed list — which reads as the bot being
        broken when it is in fact looking in the wrong drawer.

        Implemented as a large skip rather than a delete: the nightly sync
        rebuilds this table from real order history, so a deleted row would
        quietly return. A skip count survives the rebuild and is already
        what the proposal logic weighs.
        """
        needle = name.strip()
        if not needle:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT product_code, product_name FROM stock_items WHERE store = ? AND "
                "(product_name = ? OR product_name LIKE ? ESCAPE '\\' "
                "OR ? LIKE '%' || product_name || '%') "
                "ORDER BY LENGTH(product_name) LIMIT 1",
                (store, needle, _like_contains(needle), needle),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE stock_items SET skipped_count = skipped_count + ?, picked_count = 0 "
                "WHERE store = ? AND product_code = ?",
                (_STOCK_SUPPRESS_WEIGHT, store, row["product_code"]),
            )
            conn.commit()
            return row["product_name"]

    def record_stock_feedback(self, store: str, picked: list[str], skipped: list[str]) -> None:
        """Remember which proposals the user kept and which they removed."""
        with closing(self._connect()) as conn:
            conn.executemany(
                "UPDATE stock_items SET picked_count = picked_count + 1 "
                "WHERE store = ? AND product_code = ?",
                [(store, code) for code in picked],
            )
            conn.executemany(
                "UPDATE stock_items SET skipped_count = skipped_count + 1 "
                "WHERE store = ? AND product_code = ?",
                [(store, code) for code in skipped],
            )
            conn.commit()

    # -- observed prices at chains without a public feed ---------------------

    def record_store_prices(self, store: str, rows: list[dict]) -> int:
        """Store observed prices. Rows need barcode, name, price, observed_at."""
        with closing(self._connect()) as conn:
            cur = conn.executemany(
                "INSERT OR REPLACE INTO store_prices "
                "(store, barcode, name, price, observed_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        store,
                        str(r["barcode"]),
                        r.get("name", ""),
                        float(r["price"]),
                        r["observed_at"],
                        r.get("source", "order"),
                    )
                    for r in rows
                ],
            )
            conn.commit()
            return cur.rowcount

    def replace_store_promotions(self, store: str, rows: list[dict]) -> int:
        """Replace one chain's promotions wholesale.

        Wholesale rather than merged: a promotion that ended is not
        represented by any row in the new feed, so merging would keep
        advertising it forever. The feed is the whole truth for that
        chain at that moment.
        """
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM store_promotions WHERE store = ?", (store,))
            cur = conn.executemany(
                "INSERT OR REPLACE INTO store_promotions "
                "(store, barcode, promotion_id, description, discounted_price, "
                " min_qty, starts_at, ends_at, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        store,
                        str(r["barcode"]),
                        str(r.get("promotion_id", "")),
                        r.get("description", ""),
                        float(r["discounted_price"]),
                        float(r.get("min_qty") or 1),
                        r.get("starts_at", ""),
                        r.get("ends_at", ""),
                        r["observed_at"],
                    )
                    for r in rows
                ],
            )
            conn.commit()
            return cur.rowcount

    def live_store_promotions(
        self, store: str, now: datetime | None = None
    ) -> dict[str, dict]:
        """Promotions running right now at one chain, keyed by barcode.

        The feed carries long-dead and far-future rows the same way
        Shufersal's does (descriptions dated 2030 are routine), so
        anything not live at this moment is filtered out here rather than
        by every caller.
        """
        moment = (now or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM store_promotions WHERE store = ? "
                "AND (starts_at = '' OR starts_at <= ?) "
                "AND (ends_at = '' OR ends_at >= ?)",
                (store, moment, moment),
            ).fetchall()
        best: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            current = best.get(item["barcode"])
            # Cheapest wins where a barcode carries several promotions.
            if current is None or item["discounted_price"] < current["discounted_price"]:
                best[item["barcode"]] = item
        return best

    def feed_candidates(self, store: str, query: str, limit: int = 60) -> list[dict]:
        """Confident matches for a term in a chain's published feed.

        Only rank 0 — the product's name starts with the term as its own
        word. This backs product memory, which spends money without
        asking again, so the looser substitute match that
        `best_name_match` falls back to is deliberately not offered here
        (see localmatch's docstring).

        Newest row per barcode, so a product repriced yesterday is not
        matched at last year's price.
        """
        folded = _fold_apostrophes((query or "").strip())
        if not folded:
            return []
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT p.barcode, p.name, p.price FROM store_prices p "
                "JOIN (SELECT barcode, MAX(observed_at) mo FROM store_prices "
                "      WHERE store = ? GROUP BY barcode) latest "
                "  ON p.barcode = latest.barcode AND p.observed_at = latest.mo "
                "WHERE p.store = ? AND fold(p.name) LIKE ? ESCAPE '\\' "
                "ORDER BY p.price LIMIT 400",
                (store, store, _like_contains(folded)),
            ).fetchall()
        out = [
            {"barcode": r["barcode"], "name": r["name"], "price": r["price"]}
            for r in rows
            if r["price"] and _name_match_rank(folded, r["name"]) == 0
        ]
        return out[:limit]

    def priced_stores(self) -> list[str]:
        """Every chain we hold any price for, Shufersal aside."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT DISTINCT store FROM store_prices").fetchall()
        return sorted(row["store"] for row in rows)

    def best_name_match(self, store: str, query: str) -> dict | None:
        """The best product at one chain for a search term, with its rank.

        The building block for "price my basket at every chain". Returns
        the rank alongside the product because the *quality* of the match
        is the thing the household has to see: Shufersal's feed carries
        no barcode, so a cross-chain basket comparison is necessarily
        name-based, and a rank-0 hit ("חלב 3% קרטון" for "חלב") deserves
        different trust from a rank-1 one ("שוקולד חלב"). Returns None
        rather than a coincidence when nothing matches acceptably.
        """
        folded = _fold_apostrophes((query or "").strip())
        if not folded:
            return None
        pattern = _like_contains(folded)
        with closing(self._connect()) as conn:
            if store == "shufersal":
                rows = conn.execute(
                    "SELECT name, price FROM catalog_products "
                    "WHERE fold(name) LIKE ? ESCAPE '\\' ORDER BY price LIMIT 200",
                    (pattern,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT p.name, p.price FROM store_prices p "
                    "JOIN (SELECT barcode, MAX(observed_at) mo FROM store_prices "
                    "      WHERE store = ? GROUP BY barcode) latest "
                    "  ON p.barcode = latest.barcode AND p.observed_at = latest.mo "
                    "WHERE p.store = ? AND fold(p.name) LIKE ? ESCAPE '\\' "
                    "ORDER BY p.price LIMIT 200",
                    (store, store, pattern),
                ).fetchall()

        scored = [
            (rank, row["price"], row["name"])
            for row in rows
            if (rank := _name_match_rank(folded, row["name"])) < 2
        ]
        if scored:
            rank, price, name = min(scored, key=lambda s: (s[0], s[1]))
            return {"store": store, "name": name, "price": price, "rank": rank}

        # Nothing carries the whole phrase. Base-list names are long and
        # Shufersal-shaped ("שעועית עדינה שלמה קפואה", "חומוס עשיר ב40%
        # טחינה") and another chain will almost never word them the same
        # way, so insisting on the full string reports a chain as not
        # stocking cottage cheese when it plainly does. Retry on
        # progressively shorter leading phrases, and return anything
        # found that way as rank 1 — a substitute, drawn to the household
        # as 🔄, never as an exact match.
        words = folded.split()
        for length in range(len(words) - 1, 0, -1):
            shorter = " ".join(words[:length])
            if len(shorter) < 2:
                break
            hit = self.best_name_match(store, shorter)
            if hit:
                return {**hit, "rank": 1}
        return None

    def bought_barcodes(self, store: str) -> set[str]:
        """Barcodes this household has actually bought at one chain.

        Distinguished from the feed by `source`: a 'feed' row is the whole
        chain's catalogue, an 'order' row is something that was really in
        one of their baskets. The difference is what separates "a deal on
        something you buy" from "a deal on something in the shop".
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT barcode FROM store_prices "
                "WHERE store = ? AND source = 'order'",
                (store,),
            ).fetchall()
        return {row["barcode"] for row in rows}

    def latest_store_price(self, store: str, barcode: str) -> dict | None:
        """The most recently observed price for one barcode at one chain."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM store_prices WHERE store = ? AND barcode = ? "
                "ORDER BY observed_at DESC LIMIT 1",
                (store, str(barcode)),
            ).fetchone()
        return dict(row) if row else None

    def latest_store_prices(self, store: str) -> dict[str, dict]:
        """Newest price per barcode at one chain, keyed by barcode."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT p.* FROM store_prices p "
                "JOIN (SELECT barcode, MAX(observed_at) AS observed_at "
                "      FROM store_prices WHERE store = ? GROUP BY barcode) latest "
                "  ON p.barcode = latest.barcode AND p.observed_at = latest.observed_at "
                "WHERE p.store = ?",
                (store, store),
            ).fetchall()
        return {row["barcode"]: dict(row) for row in rows}

    def catalog_price_by_suffix(self, sku: str) -> dict | None:
        """Find a catalogue product whose EAN ends with this store sku.

        Shufersal's own product codes are the EAN with the manufacturer
        prefix stripped — P_4131074 for 7290004131074 — so this is the
        only join between its cart data and its price feed. Ambiguous
        matches are rejected rather than guessed: two products sharing a
        suffix would silently price the wrong one.
        """
        if not str(sku).isdigit():
            return None
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT item_code, name, price FROM catalog_products "
                "WHERE item_code LIKE ? LIMIT 2",
                (f"%{sku}",),
            ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None

    def catalog_prices_by_barcode(self) -> dict[str, dict]:
        """The whole catalogue keyed by EAN, in one query.

        The bulk form of `catalog_price`, and the difference is not
        cosmetic. `hotdeals.scan` called the single-row version once per
        chain row; when Tiv Taam went from 743 observed rows to a 20,889
        product feed that became ~21,000 separate SQLite round-trips and
        the nudge went from seconds to **80** — past the 90s timeout the
        delivering project allows, so the reminder started failing in
        production. One query, one dict, same answer.
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT item_code, name, price, unit_of_measure_price, unit_of_measure "
                "FROM catalog_products"
            ).fetchall()
        return {row["item_code"]: dict(row) for row in rows}

    def catalog_price(self, barcode: str) -> dict | None:
        """Shufersal's price for a barcode — its item_code *is* the EAN."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT item_code, name, price, unit_of_measure_price, unit_of_measure "
                "FROM catalog_products WHERE item_code = ?",
                (str(barcode),),
            ).fetchone()
        return dict(row) if row else None

    # -- proposals awaiting the user's ticks --------------------------------

    def create_proposal(self, chat_id: int, items: list[dict]) -> int:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE proposals SET status = 'abandoned' WHERE status = 'open'")
            cursor = conn.execute(
                "INSERT INTO proposals (chat_id, created_at) VALUES (?, ?)",
                (chat_id, datetime.now(timezone.utc).isoformat()),
            )
            proposal_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO proposal_items (proposal_id, store, product_code, product_name, "
                "department, quantity, amount, unit, selected) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        proposal_id,
                        item["store"],
                        item["product_code"],
                        item["product_name"],
                        item.get("department", ""),
                        item.get("quantity", 1),
                        item.get("amount"),
                        item.get("unit", ""),
                        1 if item.get("selected", True) else 0,
                    )
                    for item in items
                ],
            )
            conn.commit()
            return proposal_id

    def open_proposal(self) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM proposals WHERE status = 'open' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def proposal_items(self, proposal_id: int, department: str | None = None) -> list[dict]:
        query = "SELECT * FROM proposal_items WHERE proposal_id = ?"
        params: tuple = (proposal_id,)
        if department is not None:
            query += " AND department = ?"
            params += (department,)
        with closing(self._connect()) as conn:
            rows = conn.execute(query + " ORDER BY rowid", params).fetchall()
        return [dict(row) for row in rows]

    def toggle_proposal_item(self, proposal_id: int, product_code: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT selected FROM proposal_items WHERE proposal_id = ? AND product_code = ?",
                (proposal_id, product_code),
            ).fetchone()
            if row is None:
                return False
            new_value = 0 if row["selected"] else 1
            conn.execute(
                "UPDATE proposal_items SET selected = ? WHERE proposal_id = ? AND product_code = ?",
                (new_value, proposal_id, product_code),
            )
            conn.commit()
            return bool(new_value)

    def set_department_selection(self, proposal_id: int, department: str, selected: bool) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE proposal_items SET selected = ? WHERE proposal_id = ? AND department = ?",
                (1 if selected else 0, proposal_id, department),
            )
            conn.commit()

    def close_proposal(self, proposal_id: int, status: str = "confirmed") -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))
            conn.commit()

    # -- price history and cadence state -----------------------------------

    def record_price_snapshot(self) -> int:
        """Fold today's catalog into the price history, one row per item.

        Called from the catalog refresh, so history accumulates as a side
        effect of a job that already runs — no separate scheduler. Re-runs
        on the same day overwrite (the feed updates during the day), so a
        day holds one closing state, not three near-duplicates.
        """
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO price_history (item_code, day, price, promo_price)
                SELECT p.item_code, ?, p.price,
                       (SELECT MIN(pr.discounted_price) FROM catalog_promotions pr
                        WHERE pr.item_code = p.item_code
                          AND pr.discounted_price > 0
                          AND pr.discounted_price < p.price)
                FROM catalog_products p
                """,
                (day,),
            )
            conn.commit()
            return cursor.rowcount

    def prune_price_history(self, keep_days: int = 400) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM price_history WHERE day < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount

    def price_stats(self, item_code: str) -> dict | None:
        """How today's price compares with this item's own recorded past."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS days,
                       MIN(COALESCE(promo_price, price)) AS best,
                       AVG(COALESCE(promo_price, price)) AS avg,
                       SUM(CASE WHEN promo_price IS NOT NULL THEN 1 ELSE 0 END) AS promo_days
                FROM price_history WHERE item_code = ?
                """,
                (item_code,),
            ).fetchone()
        if row is None or not row["days"]:
            return None
        return {
            "days": row["days"],
            "best": row["best"],
            "avg": row["avg"],
            "promo_share": row["promo_days"] / row["days"],
        }

    def log_orders(self, orders: list[dict], store: str = "shufersal") -> int:
        """Record placed orders (idempotent) so cadence can be learned.

        A new order here is also the late evidence for a shop the
        household already reported: anything sitting at `shopped` for that
        chain moves to `confirmed`. Late by design — the measured gap
        between paying and the order appearing in Shufersal's own history
        was about 36 hours — so this corroborates their report and never
        replaces it.
        """
        added = 0
        with closing(self._connect()) as conn:
            for order in orders:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO order_log (order_code, store, placed_at, total, item_count) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        order.get("code"),
                        store,
                        order.get("placed_at", ""),
                        order.get("total"),
                        order.get("item_count"),
                    ),
                )
                added += cursor.rowcount
            conn.commit()
        if added:
            self.advance_adhoc_status("shopped", "confirmed", store)
        return added

    def order_dates(self, store: str = "shufersal") -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT placed_at FROM order_log WHERE store = ? ORDER BY placed_at", (store,)
            ).fetchall()
        return [row["placed_at"] for row in rows]

    def get_state(self, key: str, default: str = "") -> str:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value)
            )
            conn.commit()

    # -- pending ambiguity decisions --------------------------------------

    def save_pending_ambiguity(
        self, store: str, original_term: str, quantity: int, candidates: list[str],
        candidate_cards: list[dict] | None = None
    ) -> int:
        """Record a choice to put to the user, once per open term.

        Questions used to pile up across cycles: an unanswered "גבינה
        צהובה" from one run was still pending on the next, so the user was
        shown the same question three times in a row alongside a fresh
        copy. One open question per term is all that can be meaningfully
        answered.
        """
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT id FROM pending_ambiguities "
                "WHERE resolved = 0 AND store = ? AND original_term = ?",
                (store, original_term),
            ).fetchone()
            if existing is not None:
                # Refresh the options; the old ones may be stale.
                conn.execute(
                    "UPDATE pending_ambiguities SET candidates = ?, candidate_cards = ?, "
                    "quantity = ?, created_at = ? WHERE id = ?",
                    (
                        json.dumps(candidates, ensure_ascii=False),
                        json.dumps(candidate_cards or [], ensure_ascii=False),
                        quantity,
                        datetime.now(timezone.utc).isoformat(),
                        existing["id"],
                    ),
                )
                conn.commit()
                return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO pending_ambiguities "
                "(store, original_term, quantity, candidates, candidate_cards, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    store,
                    original_term,
                    quantity,
                    json.dumps(candidates, ensure_ascii=False),
                    json.dumps(candidate_cards or [], ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return cur.lastrowid

    def expire_stale_ambiguities(self, older_than_hours: int = 6) -> int:
        """Drop questions the user never answered from an earlier run.

        An unanswered question is not a to-do list: by the next cycle the
        cart and the offers have moved on, and re-asking a half-day-old
        question next to a fresh one is just noise.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        ).isoformat()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE pending_ambiguities SET resolved = 1 "
                "WHERE resolved = 0 AND created_at < ?",
                (cutoff,),
            )
            conn.commit()
            return cur.rowcount

    def list_pending_ambiguities(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, store, original_term, candidates, candidate_cards "
                "FROM pending_ambiguities WHERE resolved = 0"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "store": row["store"],
                "original_term": row["original_term"],
                "candidates": json.loads(row["candidates"]),
                "candidate_cards": json.loads(row["candidate_cards"] or "[]"),
            }
            for row in rows
        ]

    def get_pending_ambiguity(self, ambiguity_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM pending_ambiguities WHERE id = ? AND resolved = 0",
                (ambiguity_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "store": row["store"],
            "original_term": row["original_term"],
            "quantity": row["quantity"],
            "candidates": json.loads(row["candidates"]),
            "candidate_cards": json.loads(
                (row["candidate_cards"] if "candidate_cards" in row.keys() else "") or "[]"
            ),
        }

    def reopen_ambiguity(self, ambiguity_id: int) -> None:
        """Put a settled question back, for "עוד סוג" and "שנה".

        The row is reused rather than a new one written: its candidate
        cards are what the question was built from, and re-searching the
        store would offer a different list than the one just answered.
        """
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE pending_ambiguities SET resolved = 0 WHERE id = ?",
                (ambiguity_id,),
            )
            conn.commit()

    def mark_ambiguity_resolved(self, ambiguity_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE pending_ambiguities SET resolved = 1 WHERE id = ?", (ambiguity_id,)
            )
            conn.commit()

    # -- remembered product choices ----------------------------------------

    def remember_choice(
        self, store: str, term: str, product_code: str, product_name: str
    ) -> None:
        """Record which product a search term should resolve to from now on.

        This is what stops the bot re-asking the same question every
        cycle. Keyed on the search term rather than the base-list row so
        an ad-hoc "טונה" benefits from a choice made for the standing
        "טונה" too.
        """
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO preferred_products "
                "(store, term, product_code, product_name, chosen_at) VALUES (?, ?, ?, ?, ?)",
                (
                    store,
                    term.strip(),
                    product_code,
                    product_name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def preferred_for(self, store: str, term: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT product_code, product_name FROM preferred_products "
                "WHERE store = ? AND term = ?",
                (store, term.strip()),
            ).fetchone()
        if row is None:
            return None
        return {"product_code": row["product_code"], "product_name": row["product_name"]}

    def list_preferences(self, store: str | None = None) -> list[dict]:
        query = "SELECT store, term, product_code, product_name FROM preferred_products"
        params: tuple = ()
        if store:
            query += " WHERE store = ?"
            params = (store,)
        with closing(self._connect()) as conn:
            rows = conn.execute(query + " ORDER BY term", params).fetchall()
        return [dict(row) for row in rows]

    def forget_choice(self, store: str, term: str) -> bool:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "DELETE FROM preferred_products WHERE store = ? AND term = ?",
                (store, term.strip()),
            )
            conn.commit()
            return cur.rowcount > 0

    # -- price catalog ----------------------------------------------------

    def replace_products_only(
        self, products: list[PricedProduct], meta: dict[str, str] | None = None
    ) -> None:
        """Refresh prices while leaving the stored promotions alone.

        For the case where a snapshot comes back with no promotions at
        all: that is far more likely to be a transient hole in the feed
        listing than a branch genuinely running zero promotions, and
        wiping them makes /deals answer "nothing on offer" with no way
        to tell that apart from having no data.
        """
        preserved_promo_file = self.catalog_meta().get("promo_file", "")
        merged = dict(meta or {})
        merged["promo_file"] = preserved_promo_file
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM catalog_products")
                conn.executemany(
                    "INSERT OR REPLACE INTO catalog_products "
                    "(item_code, name, manufacturer, price, unit_of_measure_price, "
                    " unit_of_measure, quantity, is_weighted) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            p.item_code,
                            p.name,
                            p.manufacturer,
                            p.price,
                            p.unit_of_measure_price,
                            p.unit_of_measure,
                            p.quantity,
                            int(p.is_weighted),
                        )
                        for p in products
                    ],
                )
                for key, value in merged.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
                        (key, value),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES ('refreshed_at', ?)",
                    (datetime.now(timezone.utc).isoformat(),),
                )

    def replace_catalog(
        self,
        products: list[PricedProduct],
        promotions: list[PromotionItem],
        meta: dict[str, str] | None = None,
    ) -> None:
        """Swap in a freshly downloaded snapshot, atomically.

        Done in one transaction so a failure mid-refresh leaves the
        previous snapshot intact — answering with slightly stale prices
        is fine, answering from a half-written catalog is not.
        """
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM catalog_products")
                conn.execute("DELETE FROM catalog_promotions")
                conn.executemany(
                    "INSERT OR REPLACE INTO catalog_products "
                    "(item_code, name, manufacturer, price, unit_of_measure_price, "
                    " unit_of_measure, quantity, is_weighted) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            p.item_code,
                            p.name,
                            p.manufacturer,
                            p.price,
                            p.unit_of_measure_price,
                            p.unit_of_measure,
                            p.quantity,
                            int(p.is_weighted),
                        )
                        for p in products
                    ],
                )
                conn.executemany(
                    "INSERT INTO catalog_promotions "
                    "(promotion_id, description, item_code, discounted_price, min_qty, "
                    " discount_rate, starts_at, ends_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            r.promotion_id,
                            r.description,
                            r.item_code,
                            r.discounted_price,
                            r.min_qty,
                            r.discount_rate,
                            r.starts_at,
                            r.ends_at,
                        )
                        for r in promotions
                    ],
                )
                for key, value in (meta or {}).items():
                    conn.execute(
                        "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
                        (key, value),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES ('refreshed_at', ?)",
                    (datetime.now(timezone.utc).isoformat(),),
                )

    def catalog_meta(self) -> dict[str, str]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT key, value FROM catalog_meta").fetchall()
            count = conn.execute("SELECT COUNT(*) AS n FROM catalog_products").fetchone()["n"]
        meta = {row["key"]: row["value"] for row in rows}
        meta["product_count"] = str(count)
        return meta

    def search_products(self, query: str, limit: int = 8) -> list[PricedProduct]:
        """Relevance-ranked product search.

        SQL `LIKE` alone is not good enough here: searching חלב returns
        dozens of שוקולד חלב rows before actual milk. So the shortlist is
        widened in SQL and ranked in Python, favouring names that *start*
        with the query, then whole-word matches, then anything else —
        with shorter names winning ties, since the plain staple ("לחם
        אחיד") is nearly always what someone means over an elaborate
        variant.
        """
        term = query.strip()
        if not term:
            return []
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM catalog_products WHERE fold(name) LIKE ? ESCAPE '\\' "
                "ORDER BY price LIMIT 400",
                (_like_contains(_fold_apostrophes(term)),),
            ).fetchall()

        folded_term = _fold_apostrophes(term)

        # Among equally good matches the price-controlled staple wins.
        # Ishay, 2026-09-07: where a comparable alternative exists he
        # wants the supervised one — it is capped by regulation rather
        # than by this week's promotion. The chains write it into the
        # product name ("חלב 1% קרטון - בפיקוח"), so it costs nothing to
        # honour and it removes a decision.
        from .localmatch import is_price_controlled

        ranked = sorted(
            rows,
            key=lambda row: (
                _name_match_rank(folded_term, row["name"]),
                not is_price_controlled(row["name"]),
                len(row["name"]),
            ),
        )
        return [self._row_to_product(row) for row in ranked[:limit]]

    def cross_chain_prices(self, query: str) -> list[dict]:
        """Best name-match for a product at every chain we hold prices for.

        The canonical "where is X cheapest" answer. Honest about a hard
        limit: Shufersal's transparency feed carries **no barcode**, and
        the other chains are keyed by barcode, so there is no shared key to
        prove two rows are the *same* product. This therefore matches by
        NAME per chain — a genuine signal, but the caller must present it
        as "cheapest thing called X at each chain," not "the identical
        product compared," because sizes and variants differ (a 30g bag vs
        a multipack both contain במבה).

        Per chain this used to mean the single cheapest *substring* match,
        which is exactly backwards for a query like "חלב": a ₪2 candy
        whose name happens to contain the word is reliably cheaper than any
        actual milk, so it silently won every time. Each chain's
        candidates are now ranked with the same `_name_match_rank` used by
        `search_products` (favouring a name that starts with, or contains
        as a whole word, the query) and only the cheapest *among the
        best-ranked* candidates is kept; a chain with no rank 0-2 match at
        all is left out rather than shown a coincidental substring hit.
        This does not fix the harder case — a product whose name genuinely
        contains the query as its own word but means something else, like
        "מטבעות שוקולד חלב" for "חלב" — no data available distinguishes
        that from real milk by name alone; the caller must keep disclosing
        that this is a name match, not a proven same product.

        Where a unit price exists it is included, which is the fairer
        comparison across sizes.

        One row per chain: `{store, name, price, unit_price, unit}` sorted
        cheapest first. Shufersal comes from `catalog_products`; every other
        chain from the newest `store_prices` row per barcode.
        """
        from .chains import display_name

        folded = _fold_apostrophes(query.strip())
        if not folded:
            return []
        pattern = _like_contains(folded)
        out: list[dict] = []

        def best(candidates: list[tuple]) -> tuple | None:
            """Cheapest candidate among the best-ranked ones, or None.

            Each candidate is (name, price, ...extra). Only rank 0 (the
            name starts with the query) and rank 1 (the query is its own
            whole word) are accepted — rank 2 is a *different* word that
            happens to share a prefix ("חלבה"/halva, "חלבי"/dairy-flavoured
            for "חלב") and rank 3 is a bare substring; neither is a real
            signal, so a chain with only those is left out rather than
            given a coincidental last resort.
            """
            scored = [
                (rank, c[1], c)
                for c in candidates
                if (rank := _name_match_rank(folded, c[0])) < 2
            ]
            if not scored:
                return None
            return min(scored, key=lambda s: (s[0], s[1]))[2]

        with closing(self._connect()) as conn:
            # Shufersal — its own feed, name only, with a real unit price.
            candidates = conn.execute(
                "SELECT name, price, unit_of_measure_price, unit_of_measure "
                "FROM catalog_products WHERE fold(name) LIKE ? ESCAPE '\\' "
                "ORDER BY price LIMIT 200",
                (pattern,),
            ).fetchall()
            picked = best([tuple(row) for row in candidates])
            if picked is not None:
                name, price, unit_price, unit = picked
                out.append({
                    "store": "shufersal", "chain": display_name("shufersal"),
                    "name": name, "price": price,
                    "unit_price": unit_price, "unit": unit,
                })

            # Every barcode chain — newest row per barcode, best match.
            for store in conn.execute(
                "SELECT DISTINCT store FROM store_prices"
            ).fetchall():
                s = store["store"]
                candidates = conn.execute(
                    "SELECT p.name, p.price FROM store_prices p "
                    "JOIN (SELECT barcode, MAX(observed_at) mo FROM store_prices "
                    "      WHERE store = ? GROUP BY barcode) latest "
                    "  ON p.barcode = latest.barcode AND p.observed_at = latest.mo "
                    "WHERE p.store = ? AND fold(p.name) LIKE ? ESCAPE '\\' "
                    "ORDER BY p.price LIMIT 200",
                    (s, s, pattern),
                ).fetchall()
                picked = best([tuple(row) for row in candidates])
                if picked is not None:
                    name, price = picked
                    out.append({
                        "store": s, "chain": display_name(s),
                        "name": name, "price": price,
                        "unit_price": None, "unit": None,
                    })

        out.sort(key=lambda d: d["price"] if d["price"] is not None else float("inf"))
        return out

    def active_promotions_for(self, item_code: str, now: datetime | None = None) -> list[PromotionItem]:
        """Promotions currently running for one item.

        The feed keeps long-dead and far-future rows (coupons dated 2014
        through 2031), so anything not live right now is filtered out —
        otherwise the bot would advertise deals that don't exist.
        """
        moment = (now or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM catalog_promotions WHERE item_code = ? "
                "AND (starts_at = '' OR starts_at <= ?) AND (ends_at = '' OR ends_at >= ?)",
                (item_code, moment, moment),
            ).fetchall()
        return [
            PromotionItem(
                promotion_id=row["promotion_id"],
                description=row["description"],
                item_code=row["item_code"],
                discounted_price=row["discounted_price"],
                min_qty=row["min_qty"],
                discount_rate=row["discount_rate"],
                starts_at=row["starts_at"],
                ends_at=row["ends_at"],
            )
            for row in rows
        ]

    def best_deal_for(
        self, product: PricedProduct, now: datetime | None = None
    ) -> PromotionItem | None:
        """The cheapest genuine promotion on an item, or None.

        Most rows attached to an item are not really discounts. The feed
        is full of blanket entries — payment-card coupons ("ע. סיבוס
        קופון"), club perks — that are listed against every product with
        a "discounted" price equal to the shelf price. Reporting those as
        deals would make every single item look like it's on sale, which
        is worse than saying nothing. So a row only counts when it
        actually beats the shelf price.
        """
        candidates = [
            promo
            for promo in self.active_promotions_for(product.item_code, now)
            if 0 < promo.discounted_price < product.price
            and is_public_promotion(promo.description)
        ]
        return min(candidates, key=lambda p: p.discounted_price) if candidates else None

    def search_with_deals(
        self, query: str, limit: int = 8, now: datetime | None = None
    ) -> list[tuple[PricedProduct, PromotionItem | None]]:
        return [(p, self.best_deal_for(p, now)) for p in self.search_products(query, limit)]

    def catalog_deals(
        self, now: datetime | None = None
    ) -> list[tuple[PricedProduct, PromotionItem]]:
        """Every Shufersal product currently beaten by its own promotion.

        The whole-catalogue counterpart to `search_with_deals`, for the
        question "what is deeply discounted right now" rather than "is
        this one thing on offer". Filtered in SQL first because the
        catalogue is ~6k products and almost none of them are on a real
        promotion at any moment; the blanket coupon rows are then
        dropped by the same `is_public_promotion` guard `best_deal_for`
        uses, so a club perk never reads as a discount.
        """
        moment = (now or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT p.*, r.promotion_id, r.description, r.discounted_price, "
                "       r.min_qty, r.discount_rate, r.starts_at, r.ends_at "
                "FROM catalog_products p "
                "JOIN catalog_promotions r ON r.item_code = p.item_code "
                "WHERE (r.starts_at = '' OR r.starts_at <= ?) "
                "  AND (r.ends_at = '' OR r.ends_at >= ?) "
                "  AND r.discounted_price > 0 "
                "  AND r.discounted_price < p.price",
                (moment, moment),
            ).fetchall()

        best: dict[str, tuple[PricedProduct, PromotionItem]] = {}
        for row in rows:
            if not is_public_promotion(row["description"]):
                continue
            promo = PromotionItem(
                promotion_id=row["promotion_id"],
                description=row["description"],
                item_code=row["item_code"],
                discounted_price=row["discounted_price"],
                min_qty=row["min_qty"],
                discount_rate=row["discount_rate"],
                starts_at=row["starts_at"],
                ends_at=row["ends_at"],
            )
            current = best.get(row["item_code"])
            if current is None or promo.discounted_price < current[1].discounted_price:
                best[row["item_code"]] = (self._row_to_product(row), promo)
        return list(best.values())

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> PricedProduct:
        return PricedProduct(
            item_code=row["item_code"],
            name=row["name"],
            manufacturer=row["manufacturer"],
            price=row["price"],
            unit_of_measure_price=row["unit_of_measure_price"],
            unit_of_measure=row["unit_of_measure"],
            quantity=row["quantity"],
            is_weighted=bool(row["is_weighted"]),
        )
