"""SQLite schema and connection helpers. Every call reads the DB path at
call time via finquorum.config.get_db_path(), never a module-level cached path."""

import sqlite3
from contextlib import contextmanager

from finquorum.config import get_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    institution TEXT,
    type TEXT NOT NULL DEFAULT 'checking',
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    posted_date TEXT NOT NULL,
    payee TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    memo TEXT,
    fingerprint TEXT NOT NULL UNIQUE,
    is_transfer INTEGER NOT NULL DEFAULT 0,
    split_parent_id INTEGER REFERENCES transactions(id),
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_field TEXT NOT NULL,
    match_type TEXT NOT NULL,
    match_value TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    year_month TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    UNIQUE(category_id, year_month)
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    revert_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    decided_at TEXT,
    decided_by TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    proposal_id INTEGER REFERENCES proposals(id),
    action TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id INTEGER,
    before_json TEXT,
    after_json TEXT
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection to the ledger DB, creating the schema if needed.
    Reads the path at call time (via get_db_path) unless one is passed in."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@contextmanager
def get_conn(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
