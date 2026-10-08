"""CSV importer with a configurable column mapping, so it can read exports
from arbitrary "Example Bank"-style CSVs without hardcoding column names."""

import csv
import sqlite3
from dataclasses import dataclass, field

from finquorum.importers.common import get_or_create_account, insert_transaction


@dataclass
class ColumnMapping:
    date: str = "date"
    payee: str = "payee"
    amount: str = "amount"
    memo: str | None = "memo"
    external_id: str | None = None
    date_format: str | None = None  # e.g. "%m/%d/%Y"; None assumes YYYY-MM-DD
    amount_sign_flip: bool = False  # flip sign if source uses positive=expense


@dataclass
class CsvImportResult:
    account_id: int
    imported: int
    skipped_duplicate: int
    total_seen: int


def _parse_date(raw: str, fmt: str | None) -> str:
    if not fmt:
        return raw.strip()
    from datetime import datetime

    return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")


def import_csv_file(
    conn: sqlite3.Connection,
    path: str,
    account_name: str,
    mapping: ColumnMapping | None = None,
    institution: str | None = None,
) -> CsvImportResult:
    mapping = mapping or ColumnMapping()
    account_id = get_or_create_account(conn, account_name, institution=institution)

    imported = 0
    skipped = 0
    total = 0
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            posted_date = _parse_date(row[mapping.date], mapping.date_format)
            payee = row[mapping.payee].strip()
            amount_cents = round(float(row[mapping.amount]) * 100)
            if mapping.amount_sign_flip:
                amount_cents = -amount_cents
            memo = row.get(mapping.memo) if mapping.memo else None
            external_id = row.get(mapping.external_id) if mapping.external_id else None

            _, inserted = insert_transaction(
                conn,
                account_id=account_id,
                posted_date=posted_date,
                payee=payee,
                amount_cents=amount_cents,
                memo=memo,
                external_id=external_id,
            )
            if inserted:
                imported += 1
            else:
                skipped += 1

    return CsvImportResult(
        account_id=account_id, imported=imported, skipped_duplicate=skipped, total_seen=total
    )
