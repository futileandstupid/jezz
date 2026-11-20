"""Deterministic Sonar-style Cognitive Complexity and callable structural metrics."""

import ast
from collections.abc import Sequence
from typing import Final

import icontract
from pydantic import BaseModel

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    IdentifierStr,
    LineNumber,
    LoopBudget,
    NonNegativeInt,
    RecursionBudget,
    jezz_callable,
)

COGNITIVE_COMPLEXITY_LIMIT: Final[int] = 15
MAX_NESTING_DEPTH_LIMIT: Final[int] = 4
MAX_PARAMETER_COUNT_LIMIT: Final[int] = 6
MAX_STATEMENT_COUNT_LIMIT: Final[int] = 35

_MAX_AST_STATEMENT_WALK: Final[int] = 50_000
_MAX_VISITOR_DEPTH: Final[int] = 200


class CallableMetrics(BaseModel):
    """Static complexity and structural span measurements for a single callable."""

    model_config = STRICT_FROZEN_CONFIG

    qualname: IdentifierStr
    line: LineNumber
    cognitive_complexity: NonNegativeInt
    max_nesting_depth: NonNegativeInt
    parameter_count: NonNegativeInt
    statement_count: NonNegativeInt


class _CognitiveComplexityVisitor(ast.NodeVisitor):
    """Depth-bounded AST visitor computing SonarSource Cognitive Complexity and nesting."""

    def __init__(self, root_name: str) -> None:
        self.root_name = root_name
        self.score = 0
        self.nesting = 0
        self.max_nesting = 0
        self._budget = RecursionBudget(max_depth=_MAX_VISITOR_DEPTH)

    def visit(self, node: ast.AST) -> None:
        with self._budget:
            super().visit(node)

    def _visit_nested_stmts(self, stmts: Sequence[ast.stmt]) -> None:
        self.nesting += 1
        if self.nesting > self.max_nesting:
            self.max_nesting = self.nesting
        for stmt in stmts:
            self.visit(stmt)
        self.nesting -= 1

    def _visit_structural_block(self, stmts: Sequence[ast.stmt]) -> None:
        self.score += 1 + self.nesting
        self._visit_nested_stmts(stmts)

    def _visit_orelse_chain(self, orelse: Sequence[ast.stmt]) -> None:
        if not orelse:
            return
        first = orelse[0]
        if len(orelse) == 1 and isinstance(first, ast.If):
            self.visit(first.test)
            self.score += 1
            self._visit_nested_stmts(first.body)
            self._visit_orelse_chain(first.orelse)
            return
        self.score += 1
        self._visit_nested_stmts(orelse)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_structural_block(node.body)
        self._visit_orelse_chain(node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_structural_block(node.body)
        if node.orelse:
            self.score += 1
            self._visit_nested_stmts(node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_structural_block(node.body)
        if node.orelse:
            self.score += 1
            self._visit_nested_stmts(node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_structural_block(node.body)
        if node.orelse:
            self.score += 1
            self._visit_nested_stmts(node.orelse)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        self._visit_structural_block(node.body)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        self.score += 1 + self.nesting
        self.nesting += 1
        if self.nesting > self.max_nesting:
            self.max_nesting = self.nesting
        for case in node.cases:
            if case.guard is not None:
                self.visit(case.guard)
            for stmt in case.body:
                self.visit(stmt)
        self.nesting -= 1

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.score += 1 + self.nesting
        self.nesting += 1
        if self.nesting > self.max_nesting:
            self.max_nesting = self.nesting
        self.visit(node.test)
        self.visit(node.body)
        self.visit(node.orelse)
        self.nesting -= 1

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.score += 1
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == self.root_name:
            self.score += 1
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.nesting += 1
        self.visit(node.body)
        self.nesting -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_nested_stmts(node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_nested_stmts(node.body)


@jezz_callable
def strip_leading_docstring(stmts: Sequence[ast.stmt]) -> Sequence[ast.stmt]:
    """Return ``stmts`` without a leading string-literal docstring statement, if present."""
    if not stmts:
        return stmts
    first = stmts[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return stmts[1:]
    return stmts


def _count_local_statements(stmts: Sequence[ast.stmt]) -> int:
    """Count executable AST statements in ``stmts`` without descending into nested defs."""
    total = 0
    stack: list[ast.AST] = list(strip_leading_docstring(stmts))
    budget = LoopBudget(max_steps=_MAX_AST_STATEMENT_WALK)
    while stack:
        budget.tick()
        current = stack.pop()
        if isinstance(current, ast.stmt):
            total += 1
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        stack.extend(ast.iter_child_nodes(current))
    return total


@jezz_callable
def count_non_receiver_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    is_method: bool,
) -> NonNegativeInt:
    """Count callable parameters excluding ``self``/``cls`` receiver on class methods."""
    positional = list(node.args.posonlyargs) + list(node.args.args)
    receiver_offset = 1 if is_method and positional and positional[0].arg in {"self", "cls"} else 0
    extra = (1 if node.args.vararg is not None else 0) + (1 if node.args.kwarg is not None else 0)
    return (len(positional) - receiver_offset) + len(node.args.kwonlyargs) + extra


@icontract.ensure(lambda result: result.cognitive_complexity >= 0)
@jezz_callable
def compute_callable_metrics(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    qualname: IdentifierStr = "<anonymous>",
    is_method: bool = False,
) -> CallableMetrics:
    """Compute Cognitive Complexity, nesting depth, parameter arity, and statement span."""
    effective_body = strip_leading_docstring(node.body)
    visitor = _CognitiveComplexityVisitor(root_name=node.name)
    for stmt in effective_body:
        visitor.visit(stmt)

    resolved_name = node.name if qualname == "<anonymous>" else qualname
    return CallableMetrics(
        qualname=resolved_name,
        line=node.lineno,
        cognitive_complexity=visitor.score,
        max_nesting_depth=visitor.max_nesting,
        parameter_count=count_non_receiver_parameters(node, is_method=is_method),
        statement_count=_count_local_statements(node.body),
    )
