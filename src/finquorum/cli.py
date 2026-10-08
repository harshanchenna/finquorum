"""finquorum command-line interface."""

import argparse
import json
import sys

from finquorum.db import get_conn
from finquorum import proposals
from finquorum.importers.csv_importer import ColumnMapping, import_csv_file
from finquorum.importers.ofx import import_ofx_file


def _cmd_import(args):
    with get_conn() as conn:
        if args.file.lower().endswith((".ofx", ".qfx")):
            result = import_ofx_file(conn, args.file, account_name=args.account, institution=args.institution)
        else:
            mapping = ColumnMapping(**json.loads(args.mapping)) if args.mapping else ColumnMapping()
            result = import_csv_file(
                conn, args.file, account_name=args.account, mapping=mapping, institution=args.institution
            )
    print(f"imported={result.imported} skipped_duplicate={result.skipped_duplicate} total_seen={result.total_seen}")


def _cmd_sync(args):
    from finquorum.simplefin import SimpleFinClient, sync_simplefin

    with open(args.access_url_file) as f:
        access_url = f.read().strip()
    client = SimpleFinClient(access_url)
    with get_conn() as conn:
        result = sync_simplefin(conn, client, start_date=args.start_date)
    print(
        f"accounts_seen={result.accounts_seen} imported={result.imported} "
        f"skipped_duplicate={result.skipped_duplicate} total_seen={result.total_seen}"
    )


def _cmd_proposals(args):
    with get_conn() as conn:
        for p in proposals.list_proposals(conn, status=args.status):
            print(f"#{p.id} [{p.status}] {p.type}: {json.dumps(p.diff)}")


def _cmd_approve(args):
    with get_conn() as conn:
        p = proposals.approve_proposal(conn, args.id, decided_by="cli")
    print(f"approved #{p.id} ({p.type})")


def _cmd_reject(args):
    with get_conn() as conn:
        p = proposals.reject_proposal(conn, args.id, decided_by="cli")
    print(f"rejected #{p.id} ({p.type})")


def _cmd_revert(args):
    with get_conn() as conn:
        p = proposals.revert_proposal(conn, args.id, decided_by="cli")
    print(f"reverted #{p.id} ({p.type})")


def _cmd_serve(args):
    import uvicorn

    uvicorn.run("finquorum.web:app", host=args.host, port=args.port)


def _cmd_mcp(_args):
    from finquorum.mcp_server import main as mcp_main

    mcp_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finquorum")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="import an OFX/QFX or CSV file")
    p_import.add_argument("file")
    p_import.add_argument("--account", required=True)
    p_import.add_argument("--institution", default=None)
    p_import.add_argument("--mapping", default=None, help="JSON ColumnMapping overrides for CSV")
    p_import.set_defaults(func=_cmd_import)

    p_sync = sub.add_parser("sync", help="sync transactions from a SimpleFIN bridge access URL")
    p_sync.add_argument("--access-url-file", required=True, help="file containing the SimpleFIN access URL")
    p_sync.add_argument("--start-date", type=int, default=None, help="unix timestamp")
    p_sync.set_defaults(func=_cmd_sync)

    p_proposals = sub.add_parser("proposals", help="list proposals")
    p_proposals.add_argument("--status", default=None)
    p_proposals.set_defaults(func=_cmd_proposals)

    p_approve = sub.add_parser("approve", help="approve and apply a pending proposal")
    p_approve.add_argument("id", type=int)
    p_approve.set_defaults(func=_cmd_approve)

    p_reject = sub.add_parser("reject", help="reject a pending proposal")
    p_reject.add_argument("id", type=int)
    p_reject.set_defaults(func=_cmd_reject)

    p_revert = sub.add_parser("revert", help="revert an approved proposal")
    p_revert.add_argument("id", type=int)
    p_revert.set_defaults(func=_cmd_revert)

    p_serve = sub.add_parser("serve", help="run the local approve/reject web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.set_defaults(func=_cmd_serve)

    p_mcp = sub.add_parser("mcp", help="run the MCP server (stdio)")
    p_mcp.set_defaults(func=_cmd_mcp)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
