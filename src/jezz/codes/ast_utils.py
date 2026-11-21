"""Shared Python AST and lexical tokenization utilities for Building Code inspectors."""

import ast
import io
import tokenize
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Final

from pydantic import BaseModel, Field

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    BoundedText,
    ColumnOffset,
    IdentifierStr,
    LineNumber,
    LoopBudget,
    RecursionBudget,
    ShortText,
    SourceCodeText,
    jezz_callable,
    jezz_generator,
)

_MAX_SCOPE_WALK: Final[int] = 100_000
_MAX_COLLECTOR_DEPTH: Final[int] = 200


class SourceSpan(BaseModel):
    """Exact file, line, column, and symbol citation for an inspection finding."""

    model_config = STRICT_FROZEN_CONFIG

    file_path: IdentifierStr
    line: LineNumber = 1
    column: ColumnOffset = 0
    symbol: Annotated[str, Field(max_length=256)] = ""


class CommentToken(BaseModel):
    """A single lexical Python comment token with 1-based line and 0-based column."""

    model_config = STRICT_FROZEN_CONFIG

    line: LineNumber
    column: ColumnOffset
    text: BoundedText


class RawDiscrepancy(BaseModel):
    """Pre-variance discrepancy emitted by a rule evaluator."""

    model_config = STRICT_FROZEN_CONFIG

    code_id: IdentifierStr
    location: SourceSpan
    message: ShortText
    remediation: ShortText


@dataclass(frozen=True)
class CallableContext:
    """AST function/method node paired with lexical scope metadata."""

    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualname: str
    enclosing_class: ast.ClassDef | None
    in_type_checking: bool


@dataclass(frozen=True)
class SourceUnit:
    """Parsed Python source module ready for deterministic multi-rule inspection."""

    file_path: str
    module_name: str
    source: str
    tree: ast.Module
    comments: tuple[CommentToken, ...]
    is_test_file: bool
    callables: tuple[CallableContext, ...]
    classes: tuple[ast.ClassDef, ...]


@dataclass
class _ScopeCursor:
    """Mutable scope stack state held as a single cohesive field on _UnitCollector."""

    scope_names: list[str]
    class_stack: list[ast.ClassDef]
    type_checking_depth: int = 0


@jezz_callable
def dotted_expr_name(expr: ast.expr) -> BoundedText:
    """Extract a normalized dotted identifier from Name, Attribute, Call, or Subscript."""
    return _dotted_expr_bounded(expr, remaining_depth=32)


def _dotted_expr_bounded(expr: ast.expr, *, remaining_depth: int) -> str:
    if remaining_depth <= 0:
        return ""
    target = expr.value if isinstance(expr, ast.Subscript) else expr
    if isinstance(target, ast.Call):
        target = target.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        prefix = _dotted_expr_bounded(target.value, remaining_depth=remaining_depth - 1)
        return f"{prefix}.{target.attr}" if prefix else target.attr
    return ""


@jezz_callable
def is_type_checking_guard(test: ast.expr) -> bool:
    """Return True iff ``test`` guards an ``if TYPE_CHECKING:`` static-only block."""
    return dotted_expr_name(test) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}


@jezz_callable
def class_base_tails(cls_node: ast.ClassDef) -> frozenset[IdentifierStr]:
    """Return simple tail names of all direct base classes of ``cls_node``."""
    tails: set[str] = set()
    for base in cls_node.bases:
        dotted = dotted_expr_name(base)
        if dotted:
            tails.add(dotted.rsplit(".", maxsplit=1)[-1])
    return frozenset(tails)


@jezz_callable
def is_pydantic_model_class(cls_node: ast.ClassDef) -> bool:
    """Return True iff ``cls_node`` directly subclasses Pydantic ``BaseModel``."""
    return "BaseModel" in class_base_tails(cls_node)


@jezz_callable
def is_enum_class(cls_node: ast.ClassDef) -> bool:
    """Return True iff ``cls_node`` subclasses standard ``Enum`` / ``StrEnum`` / ``Flag``."""
    return bool(class_base_tails(cls_node) & {"Enum", "StrEnum", "IntEnum", "Flag", "IntFlag"})


@jezz_callable
def is_protocol_class(cls_node: ast.ClassDef) -> bool:
    """Return True iff ``cls_node`` is a ``typing.Protocol`` interface."""
    return "Protocol" in class_base_tails(cls_node)


@jezz_callable
def is_node_visitor_class(cls_node: ast.ClassDef) -> bool:
    """Return True iff ``cls_node`` subclasses ``ast.NodeVisitor`` or ``NodeTransformer``."""
    return bool(class_base_tails(cls_node) & {"NodeVisitor", "NodeTransformer"})


@jezz_callable
def is_abstract_or_overload(ctx: CallableContext) -> bool:
    """Return True iff ``ctx`` is an abstractmethod, typing overload, or Protocol stub."""
    if ctx.in_type_checking:
        return True
    if ctx.enclosing_class is not None and is_protocol_class(ctx.enclosing_class):
        return True
    decorator_tails = {
        dotted_expr_name(dec).rsplit(".", maxsplit=1)[-1] for dec in ctx.node.decorator_list
    }
    return bool(decorator_tails & {"abstractmethod", "overload"})


@jezz_generator
def iter_local_scope_nodes(root: ast.AST) -> Iterator[ast.AST]:
    """Yield descendants of ``root`` without crossing into nested def/class scopes."""
    stack: list[ast.AST] = list(ast.iter_child_nodes(root))
    budget = LoopBudget(max_steps=_MAX_SCOPE_WALK)
    while stack:
        budget.tick()
        current = stack.pop()
        yield current
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        stack.extend(ast.iter_child_nodes(current))


@jezz_callable
def extract_comment_tokens(source: SourceCodeText) -> tuple[CommentToken, ...]:
    """Extract all Python ``COMMENT`` tokens via ``tokenize`` without matching string literals."""
    comments: list[CommentToken] = []
    reader = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(reader):
        if tok.type == tokenize.COMMENT:
            comments.append(
                CommentToken(
                    line=tok.start[0],
                    column=tok.start[1],
                    text=tok.string,
                )
            )
    return tuple(comments)


class _UnitCollector(ast.NodeVisitor):
    """Depth-bounded collector of callables and classes with enclosing lexical context."""

    def __init__(self) -> None:
        self.callables: list[CallableContext] = []
        self.classes: list[ast.ClassDef] = []
        self._cursor = _ScopeCursor(scope_names=[], class_stack=[])
        self._budget = RecursionBudget(max_depth=_MAX_COLLECTOR_DEPTH)

    def visit(self, node: ast.AST) -> None:
        with self._budget:
            super().visit(node)

    def visit_If(self, node: ast.If) -> None:
        is_tc = is_type_checking_guard(node.test)
        if is_tc:
            self._cursor.type_checking_depth += 1
        for stmt in node.body:
            self.visit(stmt)
        if is_tc:
            self._cursor.type_checking_depth -= 1
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node)
        self._cursor.scope_names.append(node.name)
        self._cursor.class_stack.append(node)
        self.generic_visit(node)
        self._cursor.class_stack.pop()
        self._cursor.scope_names.pop()

    def _record_callable(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qual = ".".join((*self._cursor.scope_names, node.name))
        enclosing = self._cursor.class_stack[-1] if self._cursor.class_stack else None
        self.callables.append(
            CallableContext(
                node=node,
                qualname=qual,
                enclosing_class=enclosing,
                in_type_checking=self._cursor.type_checking_depth > 0,
            )
        )
        self._cursor.scope_names.append(node.name)
        self._visit_function_body(node.body)
        self._cursor.scope_names.pop()

    def _visit_function_body(self, body: Sequence[ast.stmt]) -> None:
        saved = self._cursor.class_stack
        self._cursor.class_stack = []
        for stmt in body:
            self.visit(stmt)
        self._cursor.class_stack = saved

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_callable(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_callable(node)


@jezz_callable
def infer_is_test_file(file_path: IdentifierStr) -> bool:
    """Infer whether ``file_path`` belongs to a verification/test suite."""
    posix = PurePosixPath(file_path.replace("\\", "/"))
    name = posix.name
    return (
        bool({"tests", "test"} & set(posix.parts))
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


@jezz_callable
def infer_module_name(file_path: IdentifierStr) -> BoundedText:
    """Derive a dotted Python module name from ``file_path`` (stripping leading ``src/``)."""
    posix = PurePosixPath(file_path.replace("\\", "/"))
    parts = list(posix.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1].endswith(".py"):
        stem = parts[-1][:-3]
        parts = parts[:-1] if stem == "__init__" else [*parts[:-1], stem]
    return ".".join(parts)


@jezz_callable
def parse_source_unit(
    file_path: IdentifierStr,
    source: SourceCodeText,
    *,
    module_name: BoundedText = "",
    is_test_file: bool | None = None,
) -> SourceUnit:
    """Parse ``source`` into an immutable ``SourceUnit`` with AST and comment tokens."""
    tree = ast.parse(source, filename=file_path)
    collector = _UnitCollector()
    collector.visit(tree)
    return SourceUnit(
        file_path=file_path,
        module_name=module_name or infer_module_name(file_path),
        source=source,
        tree=tree,
        comments=extract_comment_tokens(source),
        is_test_file=infer_is_test_file(file_path) if is_test_file is None else is_test_file,
        callables=tuple(collector.callables),
        classes=tuple(collector.classes),
    )
