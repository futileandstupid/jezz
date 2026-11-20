"""Fibonacci sequence invariants for the agent behavior basis cardinality constraint."""

import math
from typing import Annotated, Final

import icontract
from pydantic import Field

from jezz.guards import LoopBudget, jezz_callable

FIBONACCI_BASIS_CARDINALITY: Final[int] = 21
_MAX_FIB_SEARCH_STEPS: Final[int] = 10_000

type AlgebraicInt = Annotated[int, Field(ge=-(10**30), le=10**30)]
type NonNegativeAlgebraicInt = Annotated[int, Field(ge=0, le=10**30)]
type PositiveAlgebraicInt = Annotated[int, Field(ge=1, le=10**30)]


@icontract.require(lambda n: not isinstance(n, bool), "Boolean values are not valid integers.")
@jezz_callable
def is_perfect_square(n: AlgebraicInt) -> bool:
    """Return True iff integer ``n`` is a non-negative perfect square."""
    if n < 0:
        return False
    root = math.isqrt(n)
    return root * root == n


def _in_iterative_fibonacci_sequence(n: int) -> bool:
    """Reference recurrence check used to cross-verify Gessel's algebraic criterion."""
    if n <= 0:
        return False
    budget = LoopBudget(max_steps=_MAX_FIB_SEARCH_STEPS)
    a, b = 1, 1
    while a < n:
        budget.tick()
        a, b = b, a + b
    return a == n


@icontract.require(lambda n: not isinstance(n, bool), "Boolean values are not valid cardinalities.")
@icontract.ensure(
    lambda n, result: result == _in_iterative_fibonacci_sequence(n),
    "Gessel's criterion must agree with the canonical Fibonacci recurrence.",
)
@jezz_callable
def is_fibonacci(n: AlgebraicInt) -> bool:
    """Return True iff positive integer ``n`` is a Fibonacci number (n >= 1).

    Uses Gessel's theorem: positive integer ``n`` is a Fibonacci number iff
    ``5*n^2 + 4`` or ``5*n^2 - 4`` is a perfect square.
    """
    if n <= 0:
        return False
    five_n_sq = 5 * n * n
    return is_perfect_square(five_n_sq + 4) or is_perfect_square(five_n_sq - 4)


@icontract.require(lambda index: not isinstance(index, bool) and index >= 0)
@icontract.ensure(lambda index, result: (index == 0 and result == 0) or is_fibonacci(result))
@jezz_callable
def fibonacci_number(index: AlgebraicInt) -> NonNegativeAlgebraicInt:
    """Compute the ``index``-th Fibonacci number (F_0 = 0, F_1 = 1, F_8 = 21)."""
    if index == 0:
        return 0
    a, b = 0, 1
    for _ in range(index - 1):
        a, b = b, a + b
    return b


@icontract.require(lambda value: not isinstance(value, bool) and value >= 1)
@icontract.ensure(
    lambda value, result: (
        (result is None and not is_fibonacci(value))
        or (result is not None and fibonacci_number(result) == value)
    )
)
@jezz_callable
def fibonacci_index(value: PositiveAlgebraicInt) -> PositiveAlgebraicInt | None:
    """Return the canonical Fibonacci index ``k >= 2`` (or ``1`` for ``value == 1``), else None."""
    if value == 1:
        return 1
    budget = LoopBudget(max_steps=_MAX_FIB_SEARCH_STEPS)
    idx = 2
    a, b = 1, 1
    while b < value:
        budget.tick()
        a, b = b, a + b
        idx += 1
    return idx if b == value else None
