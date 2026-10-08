"""Tests for the SimpleFIN client against a local mock HTTP server with
invented data. Never touches the real SimpleFIN service."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from finquorum.simplefin import SimpleFinClient, claim_setup_token, sync_simplefin

MOCK_ACCOUNTS_RESPONSE = {
    "accounts": [
        {
            "id": "ACC-001",
            "name": "Example Mock Checking",
            "currency": "USD",
            "balance": "1234.56",
            "org": {"name": "Example Mock Bank"},
            "transactions": [
                {"id": "TX-1", "posted": 1767225600, "amount": "-42.10", "payee": "MOCK UTILITY", "description": "Utility bill"},
                {"id": "TX-2", "posted": 1767312000, "amount": "-5.25", "payee": "MOCK COFFEE", "description": "Coffee"},
            ],
        }
    ]
}


class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test output
        pass

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(b"mockuser:mockpass").decode()
        return auth == expected

    def do_POST(self):
        if self.path == "/claim/abc123":
            self.send_response(200)
            self.end_headers()
            access_url = f"http://mockuser:mockpass@127.0.0.1:{self.server.server_port}/sf"
            self.wfile.write(access_url.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if not self._check_auth():
            self.send_response(401)
            self.end_headers()
            return
        if self.path.startswith("/sf/accounts"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(MOCK_ACCOUNTS_RESPONSE).encode())
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


def test_claim_setup_token(mock_server):
    port = mock_server.server_port
    claim_url = f"http://127.0.0.1:{port}/claim/abc123"
    setup_token = base64.b64encode(claim_url.encode()).decode()

    access_url = claim_setup_token(setup_token)
    assert access_url.startswith("http://mockuser:mockpass@127.0.0.1:")


def test_get_accounts_requires_auth(mock_server):
    port = mock_server.server_port
    bad_client = SimpleFinClient(f"http://wronguser:wrongpass@127.0.0.1:{port}/sf")
    with pytest.raises(Exception):
        bad_client.get_accounts()


def test_get_accounts_returns_parsed_data(mock_server):
    port = mock_server.server_port
    client = SimpleFinClient(f"http://mockuser:mockpass@127.0.0.1:{port}/sf")
    accounts = client.get_accounts(start_date=1767225600)
    assert len(accounts) == 1
    assert accounts[0].name == "Example Mock Checking"
    assert len(accounts[0].transactions) == 2


def test_sync_simplefin_is_idempotent(conn, mock_server):
    port = mock_server.server_port
    client = SimpleFinClient(f"http://mockuser:mockpass@127.0.0.1:{port}/sf")

    result1 = sync_simplefin(conn, client)
    assert result1.imported == 2
    assert result1.skipped_duplicate == 0

    result2 = sync_simplefin(conn, client)
    assert result2.imported == 0
    assert result2.skipped_duplicate == 2
