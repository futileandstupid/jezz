"""Layer 0 runtime breakers: strict call validation, bounded primitives, budgets, and safe paths."""

import functools
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Annotated, Final, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    PlainValidator,
    validate_call,
)

STRICT_FROZEN_CONFIG: Final[ConfigDict] = ConfigDict(
    frozen=True,
    extra="forbid",
    strict=True,
    allow_inf_nan=False,
)

STRICT_MUTABLE_CONFIG: Final[ConfigDict] = ConfigDict(
    extra="forbid",
    strict=True,
    allow_inf_nan=False,
)

_CALL_BREAKER_CONFIG: Final[ConfigDict] = ConfigDict(
    strict=True,
    allow_inf_nan=False,
    arbitrary_types_allowed=True,
)

# Reusable bounded scalar aliases (JBC-DBC-005)
type IdentifierStr = Annotated[str, Field(min_length=1, max_length=256)]
type NonEmptyStr = Annotated[str, Field(min_length=1, max_length=8192)]
type ShortText = Annotated[str, Field(min_length=1, max_length=1024)]
type BoundedText = Annotated[str, Field(max_length=65_536)]
type SourceCodeText = Annotated[str, Field(max_length=2_000_000)]

type LineNumber = Annotated[int, Field(ge=1, le=1_000_000)]
type ColumnOffset = Annotated[int, Field(ge=0, le=100_000)]
type NonNegativeInt = Annotated[int, Field(ge=0, le=1_000_000_000_000_000_000)]
type PositiveInt = Annotated[int, Field(ge=1, le=1_000_000_000_000_000_000)]
type BoundedInt = Annotated[int, Field(ge=-1_000_000_000_000_000_000, le=1_000_000_000_000_000_000)]
type FiniteFloat = Annotated[float, Field(ge=-1e18, le=1e18, allow_inf_nan=False)]


def _coerce_json_list_to_tuple(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _coerce_json_list_to_frozenset(value: object) -> object:
    return frozenset(value) if isinstance(value, list) else value


WIRE_TUPLE_VALIDATOR: Final[BeforeValidator] = BeforeValidator(_coerce_json_list_to_tuple)
WIRE_FROZENSET_VALIDATOR: Final[BeforeValidator] = BeforeValidator(_coerce_json_list_to_frozenset)


def _build_lazy_breaker[**P, R](
    func: Callable[P, R],
    *,
    check_return: bool,
) -> Callable[P, R]:
    compiled: Callable[P, R] | None = None

    @functools.wraps(func)
    def _enforced(*args: P.args, **kwargs: P.kwargs) -> R:
        nonlocal compiled
        active = compiled
        if active is None:
            active = validate_call(
                config=_CALL_BREAKER_CONFIG,
                validate_return=check_return,
            )(func)
            compiled = active
        return active(*args, **kwargs)

    return _enforced


def jezz_callable[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Attach a strict runtime input and return signature breaker (JBC-DBC-004)."""
    return _build_lazy_breaker(func, check_return=True)


def jezz_generator[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Attach a strict runtime input breaker without consuming lazy generator returns."""
    return _build_lazy_breaker(func, check_return=False)


class InstanceGuard:
    """Pydantic annotation enforcing runtime isinstance(v, cls) without dataclass recursion."""

    @jezz_callable
    def __init__(self, expected_type: type[object]) -> None:
        self._expected_type = expected_type

    def _validate_instance(self, value: object) -> object:
        if not isinstance(value, self._expected_type):
            msg = (
                f"Expected instance of '{self._expected_type.__name__}', "
                f"got '{type(value).__name__}'."
            )
            raise TypeError(msg)
        return value

    def __get_pydantic_core_schema__(
        self,
        source_type: object,
        handler: GetCoreSchemaHandler,
    ) -> object:
        _ = source_type
        return handler.generate_schema(Annotated[object, PlainValidator(self._validate_instance)])


class BudgetExceededError(RuntimeError):
    """Raised when a deterministic loop step, recursion depth, or byte budget is exceeded."""


class LoopBudget:
    """Deterministic step counter guaranteeing finite while-loop termination (JBC-FLT-004)."""

    @jezz_callable
    def __init__(self, max_steps: PositiveInt) -> None:
        self._max_steps: int = max_steps
        self._steps: int = 0

    @property
    def steps(self) -> int:
        """Return the number of loop iterations executed so far."""
        return self._steps

    @jezz_callable
    def tick(self) -> None:
        """Advance iteration count by 1 or raise ``BudgetExceededError`` on overrun."""
        self._steps += 1
        if self._steps > self._max_steps:
            msg = f"While-loop exceeded deterministic budget of {self._max_steps} steps."
            raise BudgetExceededError(msg)


class RecursionBudget:
    """Context-managed fuel/depth guard preventing unbounded recursion (JBC-FLT-004, CWE-674)."""

    @jezz_callable
    def __init__(self, max_depth: Annotated[int, Field(ge=1, le=500)] = 64) -> None:
        self._max_depth: int = max_depth
        self._depth: int = 0

    @property
    def depth(self) -> int:
        """Return current active recursion nesting depth."""
        return self._depth

    def __enter__(self) -> Self:
        self._depth += 1
        if self._depth > self._max_depth:
            self._depth -= 1
            msg = f"Recursion exceeded maximum fuel depth of {self._max_depth}."
            raise BudgetExceededError(msg)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._depth > 0:
            self._depth -= 1


class ContainedPath(BaseModel):
    """Canonicalized filesystem path verified to stay inside a trusted root (JBC-SEC-005)."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
    )

    root_dir: Path
    resolved_path: Path
    relative_posix: IdentifierStr

    @classmethod
    @jezz_callable
    def from_parts(cls, root_dir: Path, relative_target: IdentifierStr | Path) -> ContainedPath:
        """Resolve ``relative_target`` under ``root_dir`` and reject '../' or symlink escapes."""
        resolved_root = root_dir.resolve()
        candidate = resolved_root.joinpath(relative_target).resolve()
        if not candidate.is_relative_to(resolved_root):
            msg = f"Path traversal blocked: '{relative_target}' escapes root '{resolved_root}'."
            raise ValueError(msg)
        rel_str = candidate.relative_to(resolved_root).as_posix() or "."
        return ContainedPath(
            root_dir=resolved_root,
            resolved_path=candidate,
            relative_posix=rel_str,
        )


@jezz_callable
def read_bounded_text(
    target: ContainedPath,
    *,
    max_bytes: Annotated[int, Field(ge=1, le=10_000_000)] = 2_000_000,
) -> SourceCodeText:
    """Read UTF-8 text from ``target`` with a hard ``max_bytes`` ceiling (JBC-FLT-006)."""
    with target.resolved_path.open("rb") as fh:
        raw = fh.read(max_bytes + 1)
    if len(raw) > max_bytes:
        msg = f"File '{target.relative_posix}' exceeds maximum read ceiling of {max_bytes} bytes."
        raise BudgetExceededError(msg)
    return raw.decode("utf-8")
