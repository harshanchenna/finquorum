"""SimpleFIN Bridge client, implementing the documented public SimpleFIN
protocol: setup token -> claim URL -> access URL (embeds basic-auth creds)
-> GET /accounts?start-date=...

SimpleFIN is an open protocol (see https://www.simplefin.org/protocol.html);
this client is a clean-room implementation against that spec. Tests run
against a local mock HTTP server with invented data — this module never
talks to the real SimpleFIN bridge service."""

import base64
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from finquorum.importers.common import get_or_create_account, insert_transaction


class SimpleFinError(RuntimeError):
    pass


@dataclass
class SimpleFinAccount:
    id: str
    name: str
    currency: str
    balance: str
    org_name: str | None
    transactions: list[dict]


def claim_setup_token(setup_token: str, client: httpx.Client | None = None) -> str:
    """Decode a base64 setup token into a claim URL, POST it, and return the
    resulting access URL (which embeds basic-auth credentials)."""
    try:
        claim_url = base64.b64decode(setup_token).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise SimpleFinError(f"invalid setup token: {exc}") from exc

    owns_client = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        resp = client.post(claim_url)
        resp.raise_for_status()
        access_url = resp.text.strip()
        if not access_url:
            raise SimpleFinError("claim response was empty")
        return access_url
    finally:
        if owns_client:
            client.close()


def _split_auth(access_url: str) -> tuple[str, tuple[str, str] | None]:
    """Split basic-auth credentials out of the access URL, returning the
    bare base URL and an (user, password) tuple for httpx's auth param."""
    parts = urlsplit(access_url)
    if parts.username is None:
        return access_url, None
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    bare = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return bare, (parts.username, parts.password or "")


class SimpleFinClient:
    def __init__(self, access_url: str, client: httpx.Client | None = None):
        self._base_url, self._auth = _split_auth(access_url)
        self._client = client or httpx.Client(timeout=15.0)

    def get_accounts(self, start_date: int | None = None) -> list[SimpleFinAccount]:
        """GET {access_url}/accounts?start-date=<unix ts>. start_date is a
        unix timestamp per the SimpleFIN spec; omit for the bridge default."""
        params = {}
        if start_date is not None:
            params["start-date"] = start_date
        url = self._base_url.rstrip("/") + "/accounts"
        resp = self._client.get(url, params=params, auth=self._auth)
        resp.raise_for_status()
        data = resp.json()
        accounts = []
        for acc in data.get("accounts", []):
            accounts.append(
                SimpleFinAccount(
                    id=acc["id"],
                    name=acc.get("name", acc["id"]),
                    currency=acc.get("currency", "USD"),
                    balance=acc.get("balance", "0"),
                    org_name=(acc.get("org") or {}).get("name"),
                    transactions=acc.get("transactions", []),
                )
            )
        return accounts

    def close(self):
        self._client.close()


@dataclass
class SimpleFinSyncResult:
    accounts_seen: int
    imported: int
    skipped_duplicate: int
    total_seen: int


def sync_simplefin(
    conn: sqlite3.Connection, client: SimpleFinClient, start_date: int | None = None
) -> SimpleFinSyncResult:
    """Pull accounts + transactions from a SimpleFIN bridge and import them
    into the ledger, idempotent via the same fingerprinting as other
    importers (using the SimpleFIN transaction id as the external id)."""
    accounts = client.get_accounts(start_date=start_date)
    imported = 0
    skipped = 0
    total = 0
    for acc in accounts:
        account_id = get_or_create_account(
            conn,
            name=acc.name,
            institution=acc.org_name,
            external_id=acc.id,
        )
        for tx in acc.transactions:
            total += 1
            posted_ts = tx.get("posted") or tx.get("transacted_at")
            posted_date = datetime.fromtimestamp(int(posted_ts), tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )
            amount_cents = round(float(tx["amount"]) * 100)
            payee = tx.get("payee") or tx.get("description") or "UNKNOWN"
            memo = tx.get("description") if tx.get("payee") else tx.get("memo")
            _, inserted = insert_transaction(
                conn,
                account_id=account_id,
                posted_date=posted_date,
                payee=payee,
                amount_cents=amount_cents,
                memo=memo,
                external_id=tx.get("id"),
            )
            if inserted:
                imported += 1
            else:
                skipped += 1
    return SimpleFinSyncResult(
        accounts_seen=len(accounts), imported=imported, skipped_duplicate=skipped, total_seen=total
    )
