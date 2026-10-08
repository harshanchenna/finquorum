"""The propose -> approve -> apply loop. Every mutation an agent requests
becomes a pending proposal with a diff preview; nothing in the ledger
changes until a human approves it. Approval applies atomically and writes
to the append-only audit log; apply supports revert."""

import json
import sqlite3
from dataclasses import dataclass

from finquorum.ledger import get_or_create_category


class ProposalError(ValueError):
    pass


@dataclass
class Proposal:
    id: int
    type: str
    status: str
    payload: dict
    diff: dict
    created_at: str
    decided_at: str | None
    decided_by: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Proposal":
        return cls(
            id=row["id"],
            type=row["type"],
            status=row["status"],
            payload=json.loads(row["payload_json"]),
            diff=json.loads(row["diff_json"]),
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
        )


def _insert_proposal(conn: sqlite3.Connection, type_: str, payload: dict, diff: dict) -> Proposal:
    cur = conn.execute(
        "INSERT INTO proposals (type, payload_json, diff_json) VALUES (?, ?, ?)",
        (type_, json.dumps(payload), json.dumps(diff)),
    )
    conn.commit()
    return get_proposal(conn, cur.lastrowid)


def get_proposal(conn: sqlite3.Connection, proposal_id: int) -> Proposal:
    row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    if not row:
        raise ProposalError(f"no such proposal: {proposal_id}")
    return Proposal.from_row(row)


def list_proposals(conn: sqlite3.Connection, status: str | None = None) -> list[Proposal]:
    if status:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE status = ? ORDER BY id", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM proposals ORDER BY id").fetchall()
    return [Proposal.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Propose: builds a diff preview, inserts a pending proposal, touches
# nothing else in the ledger.
# ---------------------------------------------------------------------------


def propose_recategorize(conn: sqlite3.Connection, transaction_id: int, category_name: str) -> Proposal:
    tx = conn.execute(
        "SELECT t.*, c.name AS category_name FROM transactions t "
        "LEFT JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        (transaction_id,),
    ).fetchone()
    if not tx:
        raise ProposalError(f"no such transaction: {transaction_id}")
    diff = {
        "transaction_id": transaction_id,
        "payee": tx["payee"],
        "amount_cents": tx["amount_cents"],
        "before_category": tx["category_name"] or "Uncategorized",
        "after_category": category_name,
    }
    payload = {"transaction_id": transaction_id, "category_name": category_name}
    return _insert_proposal(conn, "recategorize", payload, diff)


def propose_split(
    conn: sqlite3.Connection, transaction_id: int, splits: list[dict]
) -> Proposal:
    """splits: list of {amount_cents, category_name, memo?}. Must sum to the
    original transaction's amount_cents exactly."""
    tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not tx:
        raise ProposalError(f"no such transaction: {transaction_id}")
    total = sum(s["amount_cents"] for s in splits)
    if total != tx["amount_cents"]:
        raise ProposalError(
            f"split amounts sum to {total} cents, must equal original {tx['amount_cents']} cents"
        )
    diff = {
        "transaction_id": transaction_id,
        "payee": tx["payee"],
        "before_amount_cents": tx["amount_cents"],
        "splits": splits,
    }
    payload = {"transaction_id": transaction_id, "splits": splits}
    return _insert_proposal(conn, "split", payload, diff)


def propose_rule(
    conn: sqlite3.Connection,
    match_field: str,
    match_type: str,
    match_value: str,
    category_name: str,
) -> Proposal:
    if match_field not in ("payee", "memo"):
        raise ProposalError("match_field must be 'payee' or 'memo'")
    if match_type not in ("contains", "equals"):
        raise ProposalError("match_type must be 'contains' or 'equals'")

    affected = _matching_transactions(conn, match_field, match_type, match_value)
    diff = {
        "match_field": match_field,
        "match_type": match_type,
        "match_value": match_value,
        "category_name": category_name,
        "affected_transaction_count": len(affected),
        "affected_transactions": affected[:20],
    }
    payload = {
        "match_field": match_field,
        "match_type": match_type,
        "match_value": match_value,
        "category_name": category_name,
    }
    return _insert_proposal(conn, "create_rule", payload, diff)


def _matching_transactions(
    conn: sqlite3.Connection, match_field: str, match_type: str, match_value: str
) -> list[dict]:
    col = "payee" if match_field == "payee" else "memo"
    if match_type == "contains":
        rows = conn.execute(
            f"SELECT id, payee, memo, amount_cents FROM transactions WHERE {col} LIKE ?",
            (f"%{match_value}%",),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT id, payee, memo, amount_cents FROM transactions WHERE {col} = ?",
            (match_value,),
        ).fetchall()
    return [dict(r) for r in rows]


def propose_budget(
    conn: sqlite3.Connection, category_name: str, year_month: str, amount_cents: int
) -> Proposal:
    cat_row = conn.execute("SELECT id FROM categories WHERE name = ?", (category_name,)).fetchone()
    before = None
    if cat_row:
        b = conn.execute(
            "SELECT amount_cents FROM budgets WHERE category_id = ? AND year_month = ?",
            (cat_row["id"], year_month),
        ).fetchone()
        before = b["amount_cents"] if b else None
    diff = {
        "category_name": category_name,
        "year_month": year_month,
        "before_amount_cents": before,
        "after_amount_cents": amount_cents,
    }
    payload = {"category_name": category_name, "year_month": year_month, "amount_cents": amount_cents}
    return _insert_proposal(conn, "set_budget", payload, diff)


def propose_mark_transfer(conn: sqlite3.Connection, transaction_id: int, is_transfer: bool) -> Proposal:
    tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not tx:
        raise ProposalError(f"no such transaction: {transaction_id}")
    diff = {
        "transaction_id": transaction_id,
        "payee": tx["payee"],
        "before_is_transfer": bool(tx["is_transfer"]),
        "after_is_transfer": is_transfer,
    }
    payload = {"transaction_id": transaction_id, "is_transfer": is_transfer}
    return _insert_proposal(conn, "mark_transfer", payload, diff)


# ---------------------------------------------------------------------------
# Approve / reject / apply / revert. Human-only: never called from the MCP
# server's write tools.
# ---------------------------------------------------------------------------


def reject_proposal(conn: sqlite3.Connection, proposal_id: int, decided_by: str = "human") -> Proposal:
    proposal = get_proposal(conn, proposal_id)
    if proposal.status != "pending":
        raise ProposalError(f"proposal {proposal_id} is not pending (status={proposal.status})")
    conn.execute(
        "UPDATE proposals SET status = 'rejected', decided_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
        "decided_by = ? WHERE id = ?",
        (decided_by, proposal_id),
    )
    conn.commit()
    return get_proposal(conn, proposal_id)


def _audit(conn, proposal_id, action, table_name, row_id, before, after):
    conn.execute(
        "INSERT INTO audit_log (proposal_id, action, table_name, row_id, before_json, after_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            proposal_id,
            action,
            table_name,
            row_id,
            json.dumps(before) if before is not None else None,
            json.dumps(after) if after is not None else None,
        ),
    )


def approve_proposal(conn: sqlite3.Connection, proposal_id: int, decided_by: str = "human") -> Proposal:
    """Approve and apply atomically: the ledger mutation and the audit log
    entries happen in one transaction, and a revert snapshot is stored."""
    proposal = get_proposal(conn, proposal_id)
    if proposal.status != "pending":
        raise ProposalError(f"proposal {proposal_id} is not pending (status={proposal.status})")

    try:
        revert_info = _APPLY_HANDLERS[proposal.type](conn, proposal)
    except KeyError as exc:
        raise ProposalError(f"unknown proposal type: {proposal.type}") from exc

    conn.execute(
        "UPDATE proposals SET status = 'approved', revert_json = ?, "
        "decided_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), decided_by = ? WHERE id = ?",
        (json.dumps(revert_info), decided_by, proposal_id),
    )
    conn.commit()
    return get_proposal(conn, proposal_id)


def _apply_recategorize(conn: sqlite3.Connection, proposal: Proposal) -> dict:
    tx_id = proposal.payload["transaction_id"]
    category_name = proposal.payload["category_name"]
    tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    before_category_id = tx["category_id"]
    category_id = get_or_create_category(conn, category_name)
    conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?", (category_id, tx_id))
    _audit(
        conn, proposal.id, "update", "transactions", tx_id,
        {"category_id": before_category_id}, {"category_id": category_id},
    )
    return {"transaction_id": tx_id, "before_category_id": before_category_id}


def _apply_split(conn: sqlite3.Connection, proposal: Proposal) -> dict:
    tx_id = proposal.payload["transaction_id"]
    splits = proposal.payload["splits"]
    tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    before_state = dict(tx)

    child_ids = []
    for s in splits:
        category_id = get_or_create_category(conn, s["category_name"])
        cur = conn.execute(
            """INSERT INTO transactions
               (account_id, posted_date, payee, amount_cents, category_id, memo, fingerprint, split_parent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tx["account_id"],
                tx["posted_date"],
                tx["payee"],
                s["amount_cents"],
                category_id,
                s.get("memo"),
                f"split:{tx_id}:{len(child_ids)}:{proposal.id}",
                tx_id,
            ),
        )
        child_id = cur.lastrowid
        child_ids.append(child_id)
        _audit(conn, proposal.id, "insert", "transactions", child_id, None, dict(s))

    # Zero out the parent's amount so it's not double-counted, but keep it
    # as a record (split_parent_id on children points back to it).
    conn.execute("UPDATE transactions SET amount_cents = 0 WHERE id = ?", (tx_id,))
    _audit(
        conn, proposal.id, "update", "transactions", tx_id,
        {"amount_cents": before_state["amount_cents"]}, {"amount_cents": 0},
    )
    return {"transaction_id": tx_id, "before_amount_cents": before_state["amount_cents"], "child_ids": child_ids}


def _apply_create_rule(conn: sqlite3.Connection, proposal: Proposal) -> dict:
    p = proposal.payload
    category_id = get_or_create_category(conn, p["category_name"])
    cur = conn.execute(
        "INSERT INTO rules (match_field, match_type, match_value, category_id) VALUES (?, ?, ?, ?)",
        (p["match_field"], p["match_type"], p["match_value"], category_id),
    )
    rule_id = cur.lastrowid
    _audit(conn, proposal.id, "insert", "rules", rule_id, None, p)

    affected = _matching_transactions(conn, p["match_field"], p["match_type"], p["match_value"])
    before_categories = []
    for row in affected:
        tx_id = row["id"]
        tx = conn.execute("SELECT category_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        before_categories.append({"transaction_id": tx_id, "before_category_id": tx["category_id"]})
        conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?", (category_id, tx_id))
        _audit(
            conn, proposal.id, "update", "transactions", tx_id,
            {"category_id": tx["category_id"]}, {"category_id": category_id},
        )
    return {"rule_id": rule_id, "recategorized": before_categories}


def _apply_set_budget(conn: sqlite3.Connection, proposal: Proposal) -> dict:
    p = proposal.payload
    category_id = get_or_create_category(conn, p["category_name"])
    existing = conn.execute(
        "SELECT id, amount_cents FROM budgets WHERE category_id = ? AND year_month = ?",
        (category_id, p["year_month"]),
    ).fetchone()
    if existing:
        before = existing["amount_cents"]
        conn.execute("UPDATE budgets SET amount_cents = ? WHERE id = ?", (p["amount_cents"], existing["id"]))
        budget_id = existing["id"]
    else:
        before = None
        cur = conn.execute(
            "INSERT INTO budgets (category_id, year_month, amount_cents) VALUES (?, ?, ?)",
            (category_id, p["year_month"], p["amount_cents"]),
        )
        budget_id = cur.lastrowid
    _audit(
        conn, proposal.id, "upsert", "budgets", budget_id,
        {"amount_cents": before}, {"amount_cents": p["amount_cents"]},
    )
    return {"budget_id": budget_id, "before_amount_cents": before, "existed": existing is not None}


def _apply_mark_transfer(conn: sqlite3.Connection, proposal: Proposal) -> dict:
    tx_id = proposal.payload["transaction_id"]
    is_transfer = proposal.payload["is_transfer"]
    tx = conn.execute("SELECT is_transfer FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    before = bool(tx["is_transfer"])
    conn.execute("UPDATE transactions SET is_transfer = ? WHERE id = ?", (int(is_transfer), tx_id))
    _audit(
        conn, proposal.id, "update", "transactions", tx_id,
        {"is_transfer": before}, {"is_transfer": is_transfer},
    )
    return {"transaction_id": tx_id, "before_is_transfer": before}


_APPLY_HANDLERS = {
    "recategorize": _apply_recategorize,
    "split": _apply_split,
    "create_rule": _apply_create_rule,
    "set_budget": _apply_set_budget,
    "mark_transfer": _apply_mark_transfer,
}


def revert_proposal(conn: sqlite3.Connection, proposal_id: int, decided_by: str = "human") -> Proposal:
    proposal = get_proposal(conn, proposal_id)
    if proposal.status != "approved":
        raise ProposalError(f"proposal {proposal_id} is not approved (status={proposal.status})")

    row = conn.execute("SELECT revert_json FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    revert_info = json.loads(row["revert_json"])

    if proposal.type == "recategorize":
        tx_id = revert_info["transaction_id"]
        before_category_id = revert_info["before_category_id"]
        conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?", (before_category_id, tx_id))
        _audit(conn, proposal.id, "revert", "transactions", tx_id, None, {"category_id": before_category_id})
    elif proposal.type == "split":
        tx_id = revert_info["transaction_id"]
        for child_id in revert_info["child_ids"]:
            conn.execute("DELETE FROM transactions WHERE id = ?", (child_id,))
            _audit(conn, proposal.id, "revert_delete", "transactions", child_id, None, None)
        conn.execute(
            "UPDATE transactions SET amount_cents = ? WHERE id = ?",
            (revert_info["before_amount_cents"], tx_id),
        )
        _audit(
            conn, proposal.id, "revert", "transactions", tx_id, None,
            {"amount_cents": revert_info["before_amount_cents"]},
        )
    elif proposal.type == "create_rule":
        conn.execute("DELETE FROM rules WHERE id = ?", (revert_info["rule_id"],))
        _audit(conn, proposal.id, "revert_delete", "rules", revert_info["rule_id"], None, None)
        for item in revert_info["recategorized"]:
            conn.execute(
                "UPDATE transactions SET category_id = ? WHERE id = ?",
                (item["before_category_id"], item["transaction_id"]),
            )
            _audit(
                conn, proposal.id, "revert", "transactions", item["transaction_id"], None,
                {"category_id": item["before_category_id"]},
            )
    elif proposal.type == "set_budget":
        budget_id = revert_info["budget_id"]
        if revert_info["existed"]:
            conn.execute(
                "UPDATE budgets SET amount_cents = ? WHERE id = ?",
                (revert_info["before_amount_cents"], budget_id),
            )
            _audit(conn, proposal.id, "revert", "budgets", budget_id, None, {"amount_cents": revert_info["before_amount_cents"]})
        else:
            conn.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))
            _audit(conn, proposal.id, "revert_delete", "budgets", budget_id, None, None)
    elif proposal.type == "mark_transfer":
        tx_id = revert_info["transaction_id"]
        conn.execute(
            "UPDATE transactions SET is_transfer = ? WHERE id = ?",
            (int(revert_info["before_is_transfer"]), tx_id),
        )
        _audit(conn, proposal.id, "revert", "transactions", tx_id, None, {"is_transfer": revert_info["before_is_transfer"]})
    else:
        raise ProposalError(f"unknown proposal type: {proposal.type}")

    conn.execute(
        "UPDATE proposals SET status = 'reverted', decided_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
        "decided_by = ? WHERE id = ?",
        (decided_by, proposal_id),
    )
    conn.commit()
    return get_proposal(conn, proposal_id)
