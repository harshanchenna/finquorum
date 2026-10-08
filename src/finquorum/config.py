"""Runtime configuration. Read env vars at call time, never at import time,
so tests can point at a tmp DB path without caring about process cwd."""

import os
from pathlib import Path


def get_db_path() -> str:
    """Return the configured ledger DB path, read fresh on every call."""
    path = os.environ.get("FINQUORUM_DB")
    if path:
        return path
    default_dir = Path.home() / ".local" / "share" / "finquorum"
    default_dir.mkdir(parents=True, exist_ok=True)
    return str(default_dir / "finquorum.db")
