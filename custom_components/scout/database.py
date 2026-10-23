"""SQLite storage for Scout.

items     — the watchlist. User-owned: only the panel writes these after the
            one-time seed.
listings  — every listing ever seen for an item, from eBay or logged by hand.
            Source fields are refreshed on each sighting; review fields
            (flags/verdict) and user fields (user_status, notified_at) are
            never touched by a refresh, so a re-sighting can't wipe them.
meta      — key/value: schema_version, seeded, last_refresh, last_error
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

from homeassistant.core import HomeAssistant

from .const import DB_NAME
from .seeds import SEED_ITEMS

SCHEMA_VERSION = 1

ITEM_FIELDS = (
    "name", "grp", "query", "exclude_terms", "min_price", "target_price", "max_price",
    "condition", "notes", "link_sources", "ebay_enabled", "active",
)
MANUAL_FIELDS = ("source", "title", "url", "price", "shipping", "condition", "state", "note", "seen_at")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect(hass: HomeAssistant) -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(hass.config.config_dir, DB_NAME))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(hass: HomeAssistant) -> None:
    with _connect(hass) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                grp           TEXT NOT NULL DEFAULT '',
                query         TEXT NOT NULL,
                exclude_terms TEXT NOT NULL DEFAULT '',
                min_price     REAL,
                target_price  REAL NOT NULL,
                max_price     REAL NOT NULL,
                condition     TEXT NOT NULL DEFAULT 'any',
                notes         TEXT NOT NULL DEFAULT '',
                link_sources  TEXT NOT NULL DEFAULT '',
                ebay_enabled  INTEGER NOT NULL DEFAULT 1,
                active        INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS listings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id       INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                source        TEXT NOT NULL,          -- 'ebay' or a MANUAL_SOURCES key
                external_id   TEXT,                   -- eBay itemId; NULL for manual
                title         TEXT NOT NULL,
                url           TEXT,
                image_url     TEXT,
                price         REAL NOT NULL,
                shipping      REAL,                   -- NULL = unknown/calculated
                currency      TEXT NOT NULL DEFAULT 'USD',
                condition     TEXT,
                seller        TEXT,
                seller_score  INTEGER,
                seller_pct    REAL,
                location      TEXT,
                listed_at     TEXT,
                first_seen    TEXT NOT NULL,
                last_seen     TEXT NOT NULL,
                state         TEXT NOT NULL DEFAULT 'active',  -- active | gone | sold
                raw           TEXT,                   -- source JSON, for checking field mapping
                flags         TEXT NOT NULL DEFAULT '[]',
                verdict       TEXT,
                verdict_notes TEXT,
                reviewed_at   TEXT,
                notified_at   TEXT,
                user_status   TEXT,                   -- NULL | dismissed | wrong
                note          TEXT,
                UNIQUE (item_id, source, external_id)
            );
            CREATE INDEX IF NOT EXISTS idx_listings_item ON listings(item_id, state);
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        seeded = conn.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()
        if not seeded:
            for item in SEED_ITEMS:
                _insert_item(conn, item)
            conn.execute("INSERT INTO meta (key, value) VALUES ('seeded', ?)", (_now(),))


# ── Items ────────────────────────────────────────────────────────────────

def _insert_item(conn: sqlite3.Connection, data: dict) -> int:
    now = _now()
    row = {f: data.get(f) for f in ITEM_FIELDS}
    row["ebay_enabled"] = int(data.get("ebay_enabled", True))
    row["active"] = int(data.get("active", True))
    for f in ("grp", "exclude_terms", "notes", "link_sources"):
        row[f] = row[f] or ""
    row["condition"] = row["condition"] or "any"
    cur = conn.execute(
        f"INSERT INTO items ({', '.join(ITEM_FIELDS)}, created_at, updated_at) "
        f"VALUES ({', '.join('?' for _ in ITEM_FIELDS)}, ?, ?)",
        (*[row[f] for f in ITEM_FIELDS], now, now),
    )
    return cur.lastrowid


def create_item(hass: HomeAssistant, data: dict) -> int:
    with _connect(hass) as conn:
        return _insert_item(conn, data)


def update_item(hass: HomeAssistant, item_id: int, data: dict) -> bool:
    fields = [f for f in ITEM_FIELDS if f in data]
    if not fields:
        return False
    values = [int(data[f]) if f in ("ebay_enabled", "active") else data[f] for f in fields]
    with _connect(hass) as conn:
        cur = conn.execute(
            f"UPDATE items SET {', '.join(f'{f} = ?' for f in fields)}, updated_at = ? WHERE id = ?",
            (*values, _now(), item_id),
        )
    return cur.rowcount > 0


def delete_item(hass: HomeAssistant, item_id: int) -> bool:
    with _connect(hass) as conn:
        return conn.execute("DELETE FROM items WHERE id = ?", (item_id,)).rowcount > 0


def get_items(hass: HomeAssistant) -> list[dict]:
    """Every item, active or not (the panel shows paused ones too)."""
    with _connect(hass) as conn:
        rows = conn.execute("SELECT * FROM items ORDER BY grp, name").fetchall()
    return [_item(r) for r in rows]


def get_item(hass: HomeAssistant, item_id: int) -> dict | None:
    with _connect(hass) as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _item(row) if row else None


def _item(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["ebay_enabled"] = bool(d["ebay_enabled"])
    d["active"] = bool(d["active"])
    return d


# ── Listings ─────────────────────────────────────────────────────────────

def _listing(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("raw", None)
    d["flags"] = json.loads(d["flags"] or "[]")
    d["total"] = d["price"] + (d["shipping"] or 0)
    return d


def upsert_ebay(hass: HomeAssistant, item_id: int, records: list[dict]) -> int:
    """Store one search's results and mark this item's eBay listings that
    weren't in it as gone. Only called after a successful search, so a
    failed fetch never wipes the active set. Returns how many were new."""
    now = _now()
    with _connect(hass) as conn:
        known = {
            row["external_id"] for row in conn.execute(
                "SELECT external_id FROM listings WHERE item_id = ? AND source = 'ebay'", (item_id,)
            )
        }
        for r in records:
            conn.execute(
                """
                INSERT INTO listings (item_id, source, external_id, title, url, image_url, price,
                                      shipping, currency, condition, seller, seller_score,
                                      seller_pct, location, listed_at, first_seen, last_seen,
                                      state, raw)
                VALUES (?, 'ebay', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(item_id, source, external_id) DO UPDATE SET
                    title=excluded.title, url=excluded.url, image_url=excluded.image_url,
                    price=excluded.price, shipping=excluded.shipping, currency=excluded.currency,
                    condition=excluded.condition, seller=excluded.seller,
                    seller_score=excluded.seller_score, seller_pct=excluded.seller_pct,
                    location=excluded.location, last_seen=excluded.last_seen,
                    state='active', raw=excluded.raw
                """,
                (
                    item_id, r["external_id"], r["title"], r["url"], r["image_url"], r["price"],
                    r["shipping"], r["currency"], r["condition"], r["seller"], r["seller_score"],
                    r["seller_pct"], r["location"], r["listed_at"], now, now, r["raw"],
                ),
            )
        seen = [r["external_id"] for r in records]
        conn.execute(
            f"""
            UPDATE listings SET state = 'gone'
            WHERE item_id = ? AND source = 'ebay' AND state = 'active'
              AND external_id NOT IN ({', '.join('?' for _ in seen)})
            """,
            (item_id, *seen),
        )
    return len(set(seen) - known)


def add_manual(hass: HomeAssistant, item_id: int, data: dict) -> int:
    seen_at = data.get("seen_at") or _now()
    with _connect(hass) as conn:
        cur = conn.execute(
            """
            INSERT INTO listings (item_id, source, title, url, price, shipping, condition,
                                  first_seen, last_seen, state, note, verdict, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'match', ?)
            """,
            (
                item_id, data["source"], data["title"], data.get("url") or None, data["price"],
                data.get("shipping"), data.get("condition") or None, seen_at, seen_at,
                data.get("state") or "active", data.get("note") or None, _now(),
            ),
        )
    return cur.lastrowid


def get_listings(hass: HomeAssistant, item_id: int | None = None) -> list[dict]:
    """All listings (for one item, or every item), newest sighting first."""
    sql = "SELECT * FROM listings"
    args: tuple = ()
    if item_id is not None:
        sql += " WHERE item_id = ?"
        args = (item_id,)
    with _connect(hass) as conn:
        rows = conn.execute(sql + " ORDER BY first_seen DESC", args).fetchall()
    return [_listing(r) for r in rows]


def get_listing(hass: HomeAssistant, listing_id: int) -> dict | None:
    with _connect(hass) as conn:
        row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    return _listing(row) if row else None


def pending_review(hass: HomeAssistant, limit: int) -> list[dict]:
    """Active eBay listings in an item's price range that haven't been reviewed."""
    with _connect(hass) as conn:
        rows = conn.execute(
            """
            SELECT l.* FROM listings l JOIN items i ON i.id = l.item_id
            WHERE l.source = 'ebay' AND l.state = 'active' AND l.reviewed_at IS NULL
              AND l.price + COALESCE(l.shipping, 0) <= i.max_price
            ORDER BY l.first_seen
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_listing(r) for r in rows]


def set_flags(hass: HomeAssistant, listing_id: int, flags: list[str]) -> None:
    with _connect(hass) as conn:
        conn.execute("UPDATE listings SET flags = ? WHERE id = ?", (json.dumps(flags), listing_id))


def set_review(hass: HomeAssistant, listing_id: int, verdict: str, notes: str) -> None:
    with _connect(hass) as conn:
        conn.execute(
            "UPDATE listings SET verdict = ?, verdict_notes = ?, reviewed_at = ? WHERE id = ?",
            (verdict, notes, _now(), listing_id),
        )


def mark_notified(hass: HomeAssistant, listing_id: int) -> None:
    with _connect(hass) as conn:
        conn.execute("UPDATE listings SET notified_at = ? WHERE id = ?", (_now(), listing_id))


def set_user_status(hass: HomeAssistant, listing_id: int, status: str | None) -> bool:
    with _connect(hass) as conn:
        cur = conn.execute("UPDATE listings SET user_status = ? WHERE id = ?", (status, listing_id))
    return cur.rowcount > 0


def update_manual(hass: HomeAssistant, listing_id: int, data: dict) -> bool:
    fields = [f for f in MANUAL_FIELDS if f in data and f != "seen_at"]
    values = [data[f] for f in fields]
    if "seen_at" in data and data["seen_at"]:
        fields += ["first_seen", "last_seen"]
        values += [data["seen_at"], data["seen_at"]]
    if not fields:
        return False
    with _connect(hass) as conn:
        cur = conn.execute(
            f"UPDATE listings SET {', '.join(f'{f} = ?' for f in fields)} "
            "WHERE id = ? AND source != 'ebay'",
            (*values, listing_id),
        )
    return cur.rowcount > 0


def delete_manual(hass: HomeAssistant, listing_id: int) -> bool:
    """Only hand-logged listings can be deleted; eBay ones are dismissed instead."""
    with _connect(hass) as conn:
        cur = conn.execute("DELETE FROM listings WHERE id = ? AND source != 'ebay'", (listing_id,))
    return cur.rowcount > 0


# ── Meta ─────────────────────────────────────────────────────────────────

def get_meta(hass: HomeAssistant, key: str) -> str | None:
    with _connect(hass) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(hass: HomeAssistant, key: str, value: str) -> None:
    with _connect(hass) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
