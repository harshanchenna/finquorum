"""Stable transaction fingerprinting, for idempotent import across re-runs
and across OFX/CSV sources that describe the same underlying transaction."""

import hashlib
import re


def _normalize_payee(payee: str) -> str:
    payee = payee.strip().upper()
    payee = re.sub(r"\s+", " ", payee)
    payee = re.sub(r"[^A-Z0-9 ]", "", payee)
    return payee


def compute_fingerprint(
    account_id: int,
    posted_date: str,
    amount_cents: int,
    payee: str,
    external_id: str | None = None,
) -> str:
    """Prefer the source's own external id (e.g. OFX FITID) when present;
    otherwise derive a fingerprint from account + date + amount + normalized
    payee so re-importing the same file (or an overlapping CSV) is a no-op."""
    if external_id:
        base = f"ext:{account_id}:{external_id}"
    else:
        base = f"derived:{account_id}:{posted_date}:{amount_cents}:{_normalize_payee(payee)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
