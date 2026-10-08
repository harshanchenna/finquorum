"""Shared idempotent-insert logic used by every importer (OFX, CSV, SimpleFIN)."""

import sqlite3

from finquorum.fingerprint import compute_fingerprint


def get_or_create_account(
    conn: sqlite3.Connection,
    name: str,
    institution: str | None = None,
    account_type: str = "checking",
    external_id: str | None = None,
) -> int:
    row = conn.execute(
        "SELECT id FROM accounts WHERE name = ? AND (institution = ? OR (institution IS NULL AND ? IS NULL))",
        (name, institution, institution),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO accounts (name, institution, type, external_id) VALUES (?, ?, ?, ?)",
        (name, institution, account_type, external_id),
    )
    conn.commit()
    return cur.lastrowid


def insert_transaction(
    conn: sqlite3.Connection,
    account_id: int,
    posted_date: str,
    payee: str,
    amount_cents: int,
    memo: str | None = None,
    external_id: str | None = None,
) -> tuple[int | None, bool]:
    """Insert a transaction if its fingerprint is new. Returns (row_id, inserted).
    row_id is None and inserted False if this transaction was already present
    (idempotent re-import)."""
    fp = compute_fingerprint(account_id, posted_date, amount_cents, payee, external_id)
    existing = conn.execute(
        "SELECT id FROM transactions WHERE fingerprint = ?", (fp,)
    ).fetchone()
    if existing:
        return existing["id"], False
    cur = conn.execute(
        """INSERT INTO transactions
           (account_id, posted_date, payee, amount_cents, memo, fingerprint, external_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (account_id, posted_date, payee, amount_cents, memo, fp, external_id),
    )
    conn.commit()
    return cur.lastrowid, True
