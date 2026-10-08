import os
import sqlite3
from pathlib import Path

import pytest

from finquorum.db import connect

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> str:
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("FINQUORUM_DB", path)
    return path


@pytest.fixture
def conn(db_path) -> sqlite3.Connection:
    c = connect(db_path)
    yield c
    c.close()
