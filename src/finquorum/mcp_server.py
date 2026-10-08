"""MCP server (stdio) exposing finquorum's read tools freely, and write
tools that only ever create proposals (returning the proposal id + diff).

By design there is NO approve tool here. Approval is human-only: it happens
through the CLI (`finquorum approve <id>`) or the local web page
(`finquorum serve`), never over MCP. An agent can see what's pending
(list_proposals, proposal_status) but cannot move a proposal out of
'pending' itself.
"""

import json

from mcp.server.mcpserver import MCPServer

from finquorum.db import get_conn
from finquorum import ledger, proposals

mcp = MCPServer("finquorum")


def _dumps(obj) -> str:
    return json.dumps(obj, default=lambda o: o.__dict__, indent=2)


# --- Read tools (free, no approval needed) ---------------------------------


@mcp.tool()
def spending_by_category(year_month: str | None = None) -> str:
    """Spending totals per category. year_month is optional, format 'YYYY-MM'."""
    with get_conn() as conn:
        return _dumps(ledger.spending_by_category(conn, year_month))


@mcp.tool()
def budget_vs_actual(year_month: str) -> str:
    """Budgeted vs actual spend per category for a given 'YYYY-MM' month."""
    with get_conn() as conn:
        return _dumps(ledger.budget_vs_actual(conn, year_month))


@mcp.tool()
def search_transactions(
    query: str | None = None,
    category: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 200,
) -> str:
    """Search transactions by payee/memo substring, category, and date range."""
    with get_conn() as conn:
        return _dumps(
            ledger.search_transactions(conn, query, category, start_date, end_date, limit)
        )


@mcp.tool()
def detect_recurring_charges() -> str:
    """Detect likely recurring/subscription charges (same payee, similar amount,
    ~monthly cadence)."""
    with get_conn() as conn:
        return _dumps(ledger.detect_recurring_charges(conn))


@mcp.tool()
def list_proposals(status: str | None = None) -> str:
    """List proposals, optionally filtered by status (pending/approved/rejected/reverted)."""
    with get_conn() as conn:
        return _dumps([p.__dict__ for p in proposals.list_proposals(conn, status)])


@mcp.tool()
def proposal_status(proposal_id: int) -> str:
    """Get the current status and diff of a single proposal."""
    with get_conn() as conn:
        return _dumps(proposals.get_proposal(conn, proposal_id).__dict__)


# --- Write tools: these ONLY create a pending proposal, never mutate the
# ledger directly. There is intentionally no approve_proposal tool here.
# -----------------------------------------------------------------------


@mcp.tool()
def propose_recategorize(transaction_id: int, category_name: str) -> str:
    """Propose recategorizing a single transaction. Creates a pending proposal
    with a diff preview; does not change the ledger. A human must approve it
    via the CLI or web UI."""
    with get_conn() as conn:
        p = proposals.propose_recategorize(conn, transaction_id, category_name)
        return _dumps(p.__dict__)


@mcp.tool()
def propose_split(transaction_id: int, splits: list[dict]) -> str:
    """Propose splitting a transaction into multiple categorized parts.
    splits: list of {amount_cents, category_name, memo?}, must sum to the
    original amount. Creates a pending proposal; does not change the ledger."""
    with get_conn() as conn:
        p = proposals.propose_split(conn, transaction_id, splits)
        return _dumps(p.__dict__)


@mcp.tool()
def propose_rule(match_field: str, match_type: str, match_value: str, category_name: str) -> str:
    """Propose a categorization rule (e.g. payee contains 'COFFEE' -> Dining).
    match_field: 'payee' or 'memo'. match_type: 'contains' or 'equals'.
    Creates a pending proposal showing which existing transactions would be
    affected; does not change the ledger until approved."""
    with get_conn() as conn:
        p = proposals.propose_rule(conn, match_field, match_type, match_value, category_name)
        return _dumps(p.__dict__)


@mcp.tool()
def propose_budget(category_name: str, year_month: str, amount_cents: int) -> str:
    """Propose a monthly budget for a category. Creates a pending proposal;
    does not change the ledger until approved."""
    with get_conn() as conn:
        p = proposals.propose_budget(conn, category_name, year_month, amount_cents)
        return _dumps(p.__dict__)


@mcp.tool()
def propose_mark_transfer(transaction_id: int, is_transfer: bool) -> str:
    """Propose marking (or unmarking) a transaction as an inter-account
    transfer, excluding it from spending totals. Creates a pending proposal;
    does not change the ledger until approved."""
    with get_conn() as conn:
        p = proposals.propose_mark_transfer(conn, transaction_id, is_transfer)
        return _dumps(p.__dict__)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
