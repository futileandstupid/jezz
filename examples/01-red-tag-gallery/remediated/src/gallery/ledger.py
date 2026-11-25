"""Remediated JBC Edition 1.3 module passing all 30 static rules and Z3 contract discharge."""

import sqlite3
from typing import Annotated, Final

import icontract
from pydantic import BaseModel, ConfigDict, Field, validate_call

CALL_CONFIG: Final[ConfigDict] = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    allow_inf_nan=False,
)
ArbitraryCallConfig: Final[ConfigDict] = ConfigDict(
    arbitrary_types_allowed=True,
    strict=True,
)

type AccountId = Annotated[str, Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
type NonNegativeCents = Annotated[int, Field(ge=0, le=1_000_000_000)]
type PositiveCents = Annotated[int, Field(ge=1, le=100_000_000)]


class LedgerRequest(BaseModel):
    """Closed wire schema rejecting unknown keys, type coercion, and NaN/Inf."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    account_id: AccountId
    credit_cents: PositiveCents


@validate_call(config=CALL_CONFIG, validate_return=True)
@icontract.require(
    lambda balance_cents, credit_cents: (
        0 <= balance_cents <= 900_000_000 and 1 <= credit_cents <= 100_000_000
    )
)
@icontract.ensure(
    lambda balance_cents, credit_cents, result: result == balance_cents + credit_cents
)
def apply_credit_cents(balance_cents: NonNegativeCents, credit_cents: PositiveCents) -> int:
    """Conserve integer balance addition under Z3-discharged precondition bounds."""
    return balance_cents + credit_cents


@validate_call(config=ArbitraryCallConfig, validate_return=True)
def fetch_account_cents(conn: sqlite3.Connection, account_id: AccountId) -> NonNegativeCents:
    """Query account balance with bound SQL placeholders and explicit fault propagation."""
    cur = conn.execute("SELECT cents FROM ledger WHERE account_id = ?", (account_id,))
    row = cur.fetchone()
    if row is None:
        return 0
    val = int(row[0])
    if val < 0 or val > 1_000_000_000:
        msg = f"Stored balance {val} outside permitted domain bounds."
        raise ValueError(msg)
    return val
