from finquorum.ledger import detect_recurring_charges
from finquorum.importers.csv_importer import ColumnMapping, import_csv_file
from finquorum.importers.ofx import import_ofx_file
from tests.conftest import FIXTURES


def test_recurring_detection_finds_coffee_shop(conn):
    import_ofx_file(conn, str(FIXTURES / "example_checking.ofx"), account_name="Example Checking")
    mapping = ColumnMapping(
        date="Transaction Date", payee="Description", amount="Amount", memo="Notes",
        amount_sign_flip=True, date_format="%m/%d/%Y",
    )
    import_csv_file(conn, str(FIXTURES / "example_credit.csv"), account_name="Example Credit Card", mapping=mapping)

    recurring = detect_recurring_charges(conn)
    payees = {r["payee"] for r in recurring}
    assert "BLUE MOON COFFEE SHOP" in payees
    assert "STREAMBOX VIDEO" in payees  # monthly subscription

    coffee = next(r for r in recurring if r["payee"] == "BLUE MOON COFFEE SHOP")
    assert coffee["occurrences"] >= 3


def test_recurring_detection_ignores_one_off_merchants(conn):
    import_ofx_file(conn, str(FIXTURES / "example_checking.ofx"), account_name="Example Checking")
    recurring = detect_recurring_charges(conn)
    payees = {r["payee"] for r in recurring}
    assert "EXAMPLE PHARMACY" not in payees  # never imported for checking; sanity: no false positives
    assert "EXAMPLE HARDWARE STORE" not in payees
