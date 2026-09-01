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
    "stock_items": {"interval_days": "REAL"},
    "base_list_items": {
        "amount": "REAL",
        "unit": "TEXT NOT NULL DEFAULT ''",
        "brand": "TEXT NOT NULL DEFAULT ''",
    },
    "adhoc_requests": {
        "amount": "REAL",
        "unit": "TEXT NOT NULL DEFAULT ''",
        "brand": "TEXT NOT NULL DEFAULT ''",
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
                "(name = ? OR name LIKE ? OR ? LIKE '%' || name || '%') ORDER BY LENGTH(name) LIMIT 1",
                (needle, f"%{needle}%", needle),
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
                "(text = ? OR text LIKE ? OR ? LIKE '%' || text || '%') ORDER BY LENGTH(text) LIMIT 1",
                (needle, f"%{needle}%", needle),
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
                "skipped_count, interval_days, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                "(product_name = ? OR product_name LIKE ? OR ? LIKE '%' || product_name || '%') "
                "ORDER BY LENGTH(product_name) LIMIT 1",
                (store, needle, f"%{needle}%", needle),
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
        """Record placed orders (idempotent) so cadence can be learned."""
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
                "SELECT * FROM catalog_products WHERE name LIKE ? ORDER BY price LIMIT 400",
                (f"%{term}%",),
            ).fetchall()

        def score(name: str) -> tuple[int, int]:
            if name.startswith(term):
                rank = 0
            elif re.search(rf"(?:^|\s){re.escape(term)}(?:\s|$)", name):
                rank = 1
            elif re.search(rf"(?:^|\s){re.escape(term)}", name):
                rank = 2
            else:
                rank = 3
            return rank, len(name)

        ranked = sorted(rows, key=lambda row: score(row["name"]))
        return [self._row_to_product(row) for row in ranked[:limit]]

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
