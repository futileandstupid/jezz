"""Intentionally non-compliant module demonstrating JBC Edition 1.3 RED_TAG life-safety blocks."""

import sqlite3

from pydantic import BaseModel


class UnclosedRequest(BaseModel):
    """Violates JBC-SEC-003 (permits undeclared fields) and JBC-DBC-005 (unbounded str/int)."""

    account_id: str
    delta_cents: int


def unsafe_fetch_cents(conn: sqlite3.Connection, account_id: str) -> int:
    """Violates JBC-SEC-001 (SQL f-string), JBC-FLT-001 (silent except: pass), and JBC-DBC-004."""
    try:
        cur = conn.execute(f"SELECT cents FROM ledger WHERE account_id = '{account_id}'")
        row = cur.fetchone()
        return int(row[0]) if row is not None else 0
    except sqlite3.Error:
        pass
    return 0
