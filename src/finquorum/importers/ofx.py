"""Minimal OFX/QFX importer. OFX 1.x is SGML-ish (leaf tags often unclosed),
so this uses a tolerant regex-based tag extractor rather than a full XML
parser. Handles the common <STMTTRN> bank-transaction-list shape."""

import re
import sqlite3
from dataclasses import dataclass

from finquorum.importers.common import get_or_create_account, insert_transaction

_TAG_RE = re.compile(r"<([A-Z0-9.]+)>([^<\r\n]*)")
_STMTTRN_RE = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.DOTALL | re.IGNORECASE)


@dataclass
class OfxImportResult:
    account_id: int
    imported: int
    skipped_duplicate: int
    total_seen: int


def _extract_tags(block: str) -> dict:
    tags = {}
    for m in _TAG_RE.finditer(block):
        tags[m.group(1).upper()] = m.group(2).strip()
    return tags


def _parse_date(ofx_date: str) -> str:
    # OFX dates: YYYYMMDD or YYYYMMDDHHMMSS[.xxx][tz]
    digits = ofx_date[:8]
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


def import_ofx_file(
    conn: sqlite3.Connection,
    path: str,
    account_name: str,
    institution: str | None = None,
) -> OfxImportResult:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    header_tags = _extract_tags(content[: content.upper().find("<STMTTRN>") if "<STMTTRN>" in content.upper() else len(content)])
    external_acct_id = header_tags.get("ACCTID")

    account_id = get_or_create_account(
        conn, account_name, institution=institution, external_id=external_acct_id
    )

    imported = 0
    skipped = 0
    total = 0
    for m in _STMTTRN_RE.finditer(content):
        total += 1
        tags = _extract_tags(m.group(1))
        amount_str = tags.get("TRNAMT", "0")
        amount_cents = round(float(amount_str) * 100)
        posted_date = _parse_date(tags.get("DTPOSTED", ""))
        payee = tags.get("NAME") or tags.get("PAYEE") or "UNKNOWN"
        memo = tags.get("MEMO")
        fitid = tags.get("FITID")

        _, inserted = insert_transaction(
            conn,
            account_id=account_id,
            posted_date=posted_date,
            payee=payee,
            amount_cents=amount_cents,
            memo=memo,
            external_id=fitid,
        )
        if inserted:
            imported += 1
        else:
            skipped += 1

    return OfxImportResult(
        account_id=account_id, imported=imported, skipped_duplicate=skipped, total_seen=total
    )
