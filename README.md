# finquorum

**Status: POC**

An agent-native personal-finance ledger with a **propose → human-approve → apply** loop,
exposed as an MCP server. Most finance MCP servers are read-only wrappers around an
existing app's API. finquorum is the write side: an agent can *propose* a recategorization,
a split, a rule, or a budget, but nothing touches the ledger until a human approves it
through the CLI or a local web page. Every applied change is atomic, append-only audit
logged, and revertible.

This is a proof of concept built to explore the idea, not a finished product. It uses
invented "Example Bank" fixture data throughout — it is not affiliated with, and does not
read from, any commercial finance app or bank.

## Why

Existing finance MCP servers let an agent *read* your transactions. None of the common
ones let an agent safely *write* — recategorize a transaction, propose a budget, build a
rule — behind a human approval gate with a diff preview and an audit trail. That's the
gap finquorum fills: agents get real write tools, but every write is a reviewable proposal
first, and approval is a human-only action that cannot be reached over MCP.

## What works

- SQLite ledger: accounts, transactions, categories, rules, monthly budgets, append-only audit log.
- OFX/QFX import (regex-based tolerant parser for the common `<STMTTRN>` shape) and CSV import with configurable column mapping, both idempotent via a stable transaction fingerprint (external id when available, else account+date+amount+normalized-payee).
- SimpleFIN Bridge client implementing the public protocol (setup token → claim URL → access URL → `GET /accounts?start-date=`), tested only against a local mock HTTP server with invented data. It never talks to the real SimpleFIN service.
- Proposal lifecycle: `propose_recategorize`, `propose_split`, `propose_rule`, `propose_budget`, `propose_mark_transfer` — each returns a pending proposal with a diff preview. `approve` applies atomically and writes the audit log; `reject` discards; `revert` undoes an applied proposal using a stored revert snapshot.
- Read tools: spending by category/month, budget vs actual, transaction search, and a simple recurring-charge heuristic (same payee, amount within tolerance, ~monthly cadence, ≥3 occurrences).
- MCP server (official `mcp` Python SDK, stdio transport): read tools are free; write tools only create proposals and return the proposal id + diff. `list_proposals` and `proposal_status` are available for an agent to check state. **There is no approve tool over MCP** — approval is human-only, via the CLI or the web UI, by design.
- CLI: `import`, `sync`, `proposals`, `approve`, `reject`, `revert`, `serve`, `mcp`.
- Minimal FastAPI + HTMX local web page: one pending-proposals list with approve/reject buttons, and revert on applied proposals.
- Tests: import idempotency, the full proposal lifecycle (including that MCP has no approve path), revert, recurring detection, and the SimpleFIN client against a mock server.

## What doesn't (POC limits)

- No multi-user auth on the web UI — it's meant to run on localhost for one person.
- No multi-currency handling; everything is treated as a single currency's integer cents.
- Split proposals only support splitting a transaction's full amount across category parts; no partial splits that leave a remainder uncategorized.
- No pagination/streaming for very large ledgers — fine for a POC, not for years of data.
- Recurring-charge detection is a simple heuristic (amount tolerance + cadence window), not a trained model; it will miss irregular or bi-weekly recurring charges.
- SimpleFIN sync has no scheduling/cron built in — `finquorum sync` is a one-shot pull.

## Install

```bash
cd finquorum
uv venv .venv
uv pip install -e ".[dev]"
```

## Run the tests

```bash
.venv/bin/pytest
```

## CLI usage

```bash
# Import fixture data
finquorum import fixtures/example_checking.ofx --account "Example Checking" --institution "Example Bank"
finquorum import fixtures/example_credit.csv --account "Example Credit Card" --institution "Example Bank" \
  --mapping '{"date": "Transaction Date", "payee": "Description", "amount": "Amount", "memo": "Notes", "amount_sign_flip": true, "date_format": "%m/%d/%Y"}'

# See proposals, approve/reject/revert
finquorum proposals
finquorum approve 1
finquorum reject 2
finquorum revert 1

# Local approve/reject web page
finquorum serve

# MCP server (stdio)
finquorum mcp
```

`FINQUORUM_DB` controls the ledger DB path (defaults to `~/.local/share/finquorum/finquorum.db`).

## Example MCP client config

```json
{
  "mcpServers": {
    "finquorum": {
      "command": "/path/to/finquorum/.venv/bin/finquorum",
      "args": ["mcp"],
      "env": {
        "FINQUORUM_DB": "/path/to/your/finquorum.db"
      }
    }
  }
}
```

## Example agent transcript

```
> agent: What did I spend on coffee this quarter, and is there a pattern?

agent calls detect_recurring_charges()
  -> [{"payee": "BLUE MOON COFFEE SHOP", "occurrences": 9, "avg_amount_cents": 544,
       "first_date": "2026-07-04", "last_date": "2026-09-26", "avg_gap_days": 10}, ...]

agent: Blue Moon Coffee Shop shows up ~9 times over 3 months, roughly every 10 days,
averaging $5.44. It's uncategorized right now. Want me to propose a rule so future
coffee purchases land in Dining automatically, plus a $60/month Dining budget?

> human: yes

agent calls propose_rule(match_field="payee", match_type="contains",
                          match_value="COFFEE", category_name="Dining")
  -> {"id": 1, "type": "create_rule", "status": "pending",
      "diff": {"match_field": "payee", "match_type": "contains", "match_value": "COFFEE",
               "category_name": "Dining", "affected_transaction_count": 9, ...}}

agent calls propose_budget(category_name="Dining", year_month="2026-09", amount_cents=6000)
  -> {"id": 2, "type": "set_budget", "status": "pending",
      "diff": {"category_name": "Dining", "year_month": "2026-09",
               "before_amount_cents": null, "after_amount_cents": 6000}}

agent: Created proposal #1 (rule: payee contains "COFFEE" -> Dining, would affect 9
existing transactions) and proposal #2 ($60 Dining budget for 2026-09). Nothing has
changed in the ledger yet — review and approve when ready.

$ finquorum proposals
#1 [pending] create_rule: {"match_field": "payee", "match_type": "contains", ...}
#2 [pending] set_budget: {"category_name": "Dining", "year_month": "2026-09", ...}

$ finquorum approve 1
approved #1 (create_rule)
$ finquorum approve 2
approved #2 (set_budget)

$ finquorum  # (via spending_by_category MCP tool, 2026-09)
Dining: -$54.40 across 9 transactions   # previously Uncategorized

$ finquorum revert 1
reverted #1 (create_rule)   # transactions back to Uncategorized, rule removed
```

## SimpleFIN protocol note

The SimpleFIN client implements the public, documented SimpleFIN Bridge protocol
(see simplefin.org). It is tested only against a local mock HTTP server with invented
account/transaction data in this repo's test suite, and never calls the real SimpleFIN
service. "SimpleFIN" is named here only because it is an open protocol this code
implements against, not a commercial product this project is affiliated with.

## License

MIT, see [LICENSE](LICENSE).
