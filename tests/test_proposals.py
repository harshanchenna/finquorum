import pytest

from finquorum import proposals
from finquorum.importers.common import get_or_create_account, insert_transaction
from finquorum.proposals import ProposalError


def _make_tx(conn, payee="BLUE MOON COFFEE SHOP", amount_cents=-500, date="2026-07-04"):
    account_id = get_or_create_account(conn, "Example Checking", institution="Example Bank")
    tx_id, inserted = insert_transaction(conn, account_id, date, payee, amount_cents)
    assert inserted
    return tx_id


def test_recategorize_proposal_does_not_mutate_until_approved(conn):
    tx_id = _make_tx(conn)
    p = proposals.propose_recategorize(conn, tx_id, "Dining")
    assert p.status == "pending"
    assert p.diff["after_category"] == "Dining"

    row = conn.execute("SELECT category_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    assert row["category_id"] is None  # unchanged

    approved = proposals.approve_proposal(conn, p.id)
    assert approved.status == "approved"
    row = conn.execute(
        "SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()
    assert row["name"] == "Dining"


def test_reject_proposal_leaves_ledger_unchanged(conn):
    tx_id = _make_tx(conn)
    p = proposals.propose_recategorize(conn, tx_id, "Dining")
    proposals.reject_proposal(conn, p.id)
    row = conn.execute("SELECT category_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    assert row["category_id"] is None

    with pytest.raises(ProposalError):
        proposals.approve_proposal(conn, p.id)  # already decided


def test_approve_is_idempotent_guarded(conn):
    tx_id = _make_tx(conn)
    p = proposals.propose_recategorize(conn, tx_id, "Dining")
    proposals.approve_proposal(conn, p.id)
    with pytest.raises(ProposalError):
        proposals.approve_proposal(conn, p.id)


def test_revert_recategorize_restores_previous_category(conn):
    tx_id = _make_tx(conn)
    p1 = proposals.propose_recategorize(conn, tx_id, "Dining")
    proposals.approve_proposal(conn, p1.id)

    p2 = proposals.propose_recategorize(conn, tx_id, "Groceries")
    proposals.approve_proposal(conn, p2.id)

    reverted = proposals.revert_proposal(conn, p2.id)
    assert reverted.status == "reverted"
    row = conn.execute(
        "SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()
    assert row["name"] == "Dining"  # back to what it was before p2


def test_split_proposal_sum_mismatch_rejected(conn):
    tx_id = _make_tx(conn, amount_cents=-1000)
    with pytest.raises(ProposalError):
        proposals.propose_split(
            conn, tx_id, [{"amount_cents": -400, "category_name": "Dining"}]
        )


def test_split_apply_and_revert(conn):
    tx_id = _make_tx(conn, amount_cents=-1000)
    p = proposals.propose_split(
        conn,
        tx_id,
        [
            {"amount_cents": -600, "category_name": "Groceries"},
            {"amount_cents": -400, "category_name": "Dining"},
        ],
    )
    proposals.approve_proposal(conn, p.id)

    parent = conn.execute("SELECT amount_cents FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    assert parent["amount_cents"] == 0
    children = conn.execute(
        "SELECT amount_cents FROM transactions WHERE split_parent_id = ?", (tx_id,)
    ).fetchall()
    assert sorted(c["amount_cents"] for c in children) == [-600, -400]

    proposals.revert_proposal(conn, p.id)
    parent = conn.execute("SELECT amount_cents FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    assert parent["amount_cents"] == -1000
    children = conn.execute(
        "SELECT amount_cents FROM transactions WHERE split_parent_id = ?", (tx_id,)
    ).fetchall()
    assert len(children) == 0


def test_rule_proposal_affects_existing_transactions_only_after_approval(conn):
    tx_id = _make_tx(conn, payee="BLUE MOON COFFEE SHOP")
    p = proposals.propose_rule(conn, "payee", "contains", "COFFEE", "Dining")
    assert p.diff["affected_transaction_count"] == 1

    row = conn.execute("SELECT category_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    assert row["category_id"] is None

    proposals.approve_proposal(conn, p.id)
    row = conn.execute(
        "SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()
    assert row["name"] == "Dining"

    rule_count = conn.execute("SELECT COUNT(*) AS c FROM rules").fetchone()["c"]
    assert rule_count == 1

    proposals.revert_proposal(conn, p.id)
    row = conn.execute("SELECT category_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    assert row["category_id"] is None
    rule_count = conn.execute("SELECT COUNT(*) AS c FROM rules").fetchone()["c"]
    assert rule_count == 0


def test_budget_proposal_apply_and_revert(conn):
    p = proposals.propose_budget(conn, "Dining", "2026-09", 6000)
    proposals.approve_proposal(conn, p.id)
    row = conn.execute(
        "SELECT b.amount_cents FROM budgets b JOIN categories c ON c.id = b.category_id "
        "WHERE c.name = ? AND b.year_month = ?",
        ("Dining", "2026-09"),
    ).fetchone()
    assert row["amount_cents"] == 6000

    proposals.revert_proposal(conn, p.id)
    row = conn.execute(
        "SELECT b.amount_cents FROM budgets b JOIN categories c ON c.id = b.category_id "
        "WHERE c.name = ? AND b.year_month = ?",
        ("Dining", "2026-09"),
    ).fetchone()
    assert row is None


def test_mark_transfer_proposal(conn):
    tx_id = _make_tx(conn, payee="TRANSFER TO EXAMPLE SAVINGS", amount_cents=-50000)
    p = proposals.propose_mark_transfer(conn, tx_id, True)
    proposals.approve_proposal(conn, p.id)
    row = conn.execute("SELECT is_transfer FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    assert row["is_transfer"] == 1

    proposals.revert_proposal(conn, p.id)
    row = conn.execute("SELECT is_transfer FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    assert row["is_transfer"] == 0


def test_audit_log_written_on_approve_and_revert(conn):
    tx_id = _make_tx(conn)
    p = proposals.propose_recategorize(conn, tx_id, "Dining")
    proposals.approve_proposal(conn, p.id)
    proposals.revert_proposal(conn, p.id)

    rows = conn.execute(
        "SELECT action FROM audit_log WHERE proposal_id = ? ORDER BY id", (p.id,)
    ).fetchall()
    actions = [r["action"] for r in rows]
    assert "update" in actions
    assert "revert" in actions


def test_mcp_write_tools_never_expose_approve():
    """The MCP server module must not define any approve/reject tool —
    approval is human-only via CLI/web."""
    from finquorum import mcp_server

    tool_names = {name for name in dir(mcp_server) if not name.startswith("_")}
    assert "approve_proposal" not in tool_names
    assert "reject_proposal" not in tool_names
    assert not hasattr(mcp_server, "approve")

    # The underlying FastMCP instance's registered tools also exclude approve.
    import asyncio

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert "list_proposals" in names
    assert "proposal_status" in names
    assert not any("approve" in n for n in names)
    assert not any("reject" in n for n in names)
