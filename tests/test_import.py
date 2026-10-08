from finquorum.importers.csv_importer import ColumnMapping, import_csv_file
from finquorum.importers.ofx import import_ofx_file
from tests.conftest import FIXTURES


def test_ofx_import_and_idempotency(conn):
    path = str(FIXTURES / "example_checking.ofx")
    result1 = import_ofx_file(conn, path, account_name="Example Checking", institution="Example Bank")
    assert result1.imported == 42
    assert result1.skipped_duplicate == 0

    result2 = import_ofx_file(conn, path, account_name="Example Checking", institution="Example Bank")
    assert result2.imported == 0
    assert result2.skipped_duplicate == 42

    count = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"]
    assert count == 42


def test_csv_import_and_idempotency(conn):
    path = str(FIXTURES / "example_credit.csv")
    mapping = ColumnMapping(
        date="Transaction Date",
        payee="Description",
        amount="Amount",
        memo="Notes",
        amount_sign_flip=True,
        date_format="%m/%d/%Y",
    )
    result1 = import_csv_file(conn, path, account_name="Example Credit Card", mapping=mapping)
    assert result1.imported == 42
    assert result1.skipped_duplicate == 0

    result2 = import_csv_file(conn, path, account_name="Example Credit Card", mapping=mapping)
    assert result2.imported == 0
    assert result2.skipped_duplicate == 42


def test_csv_amount_sign_flip_produces_expenses_as_negative(conn):
    path = str(FIXTURES / "example_credit.csv")
    mapping = ColumnMapping(
        date="Transaction Date", payee="Description", amount="Amount", memo="Notes",
        amount_sign_flip=True, date_format="%m/%d/%Y",
    )
    import_csv_file(conn, path, account_name="Example Credit Card", mapping=mapping)
    row = conn.execute("SELECT amount_cents FROM transactions LIMIT 1").fetchone()
    assert row["amount_cents"] < 0


def test_import_creates_account_once(conn):
    path = str(FIXTURES / "example_checking.ofx")
    import_ofx_file(conn, path, account_name="Example Checking", institution="Example Bank")
    import_ofx_file(conn, path, account_name="Example Checking", institution="Example Bank")
    count = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
    assert count == 1
