"""Verification suite for remediated ledger gallery example."""

import sqlite3

from gallery.ledger import LedgerRequest, apply_credit_cents, fetch_account_cents


def test_apply_credit_and_parameterized_query() -> None:
    """Verify conservation and SQLite parameterized fetch."""
    req = LedgerRequest(account_id="acct_01", credit_cents=500)
    assert apply_credit_cents(1_000, req.credit_cents) == 1_500

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ledger (account_id TEXT PRIMARY KEY, cents INTEGER NOT NULL)")
    conn.execute("INSERT INTO ledger VALUES ('acct_01', 1500)")
    assert fetch_account_cents(conn, req.account_id) == 1_500
    conn.close()
