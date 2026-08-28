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
from datetime import datetime, timezone
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
"""


# Columns added after the first version shipped. SQLite has no
# "ADD COLUMN IF NOT EXISTS", and the database already holds a real list,
# so each is added only when missing rather than recreating the table.
_ADDED_COLUMNS = {
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
}


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
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
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
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO adhoc_requests "
                "(text, requested_by, quantity, created_at, amount, unit, brand) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    text,
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

    # -- pending ambiguity decisions --------------------------------------

    def save_pending_ambiguity(
        self, store: str, original_term: str, quantity: int, candidates: list[str]
    ) -> int:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO pending_ambiguities (store, original_term, quantity, candidates, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    store,
                    original_term,
                    quantity,
                    json.dumps(candidates, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return cur.lastrowid

    def list_pending_ambiguities(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, store, original_term, candidates FROM pending_ambiguities WHERE resolved = 0"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "store": row["store"],
                "original_term": row["original_term"],
                "candidates": json.loads(row["candidates"]),
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
        }

    def mark_ambiguity_resolved(self, ambiguity_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE pending_ambiguities SET resolved = 1 WHERE id = ?", (ambiguity_id,)
            )
            conn.commit()

    # -- price catalog ----------------------------------------------------

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
