"""Static AST inspectors for Article I (DBC — Design by Contract & Signature Breakers)."""

import ast
from collections.abc import Sequence
from typing import Final

from jezz.codes.ast_utils import (
    CallableContext,
    GuardedSourceUnit,
    RawDiscrepancy,
    SourceSpan,
    SourceUnit,
    class_base_tails,
    dotted_expr_name,
    is_abstract_or_overload,
    is_node_visitor_class,
    is_pydantic_model_class,
    iter_local_scope_nodes,
)
from jezz.codes.complexity import strip_leading_docstring
from jezz.guards import jezz_callable

_CONTRACT_DEC_NAMES: Final[frozenset[str]] = frozenset({"require", "ensure", "invariant"})
_VALIDATOR_DEC_NAMES: Final[frozenset[str]] = frozenset({"field_validator", "model_validator"})
_CALL_BREAKER_DEC_TAILS: Final[frozenset[str]] = frozenset(
    {"jezz_callable", "jezz_generator", "validate_call"}
)
_UNBOUNDED_SCALAR_NAMES: Final[frozenset[str]] = frozenset({"str", "bytes", "int", "float"})
_UNBOUNDED_CONTAINER_NAMES: Final[frozenset[str]] = frozenset(
    {"list", "set", "frozenset", "dict", "Sequence", "Mapping"}
)
_BOUND_FIELD_KWARGS: Final[frozenset[str]] = frozenset(
    {"min_length", "max_length", "pattern", "ge", "gt", "le", "lt", "allow_inf_nan"}
)


def _names_in_ast(root: ast.AST) -> set[str]:
    """Collect all ``ast.Name`` identifiers referenced within ``root``."""
    return {sub.id for sub in ast.walk(root) if isinstance(sub, ast.Name)}


def _contract_decorator_issue(dec: ast.expr, tail: str) -> str | None:
    """Return a violation description if ``@icontract`` decorator ``dec`` is vacuous."""
    if tail not in _CONTRACT_DEC_NAMES or not isinstance(dec, ast.Call) or not dec.args:
        return None
    first_arg = dec.args[0]
    if not isinstance(first_arg, ast.Lambda):
        return None
    if isinstance(first_arg.body, ast.Constant):
        return f"Contract '@{tail}' uses a constant literal predicate."
    if tail == "ensure" and "result" not in _names_in_ast(first_arg.body):
        return "Postcondition '@ensure' does not inspect 'result' in its predicate body."
    return None


def _check_contract_decorators(unit: SourceUnit, ctx: CallableContext) -> list[RawDiscrepancy]:
    """Detect vacuous @icontract decorators and no-op Pydantic validators."""
    discrepancies: list[RawDiscrepancy] = []
    for dec in ctx.node.decorator_list:
        tail = dotted_expr_name(dec).rsplit(".", maxsplit=1)[-1]
        issue = _contract_decorator_issue(dec, tail)
        if issue is not None:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-001",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=dec.lineno,
                        column=dec.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=f"{issue} ('{ctx.qualname}').",
                    remediation="Write a falsifiable predicate referencing constrained values.",
                )
            )
        if tail in _VALIDATOR_DEC_NAMES:
            has_check = any(
                isinstance(n, ast.Raise | ast.Call) for n in iter_local_scope_nodes(ctx.node)
            )
            if not has_check:
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-DBC-001",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=ctx.node.lineno,
                            column=ctx.node.col_offset,
                            symbol=ctx.qualname,
                        ),
                        message=f"Validator '{ctx.qualname}' performs no checks or raises.",
                        remediation="Enforce domain invariant and raise ValueError on failure.",
                    )
                )
    return discrepancies


def _guarded_parameter_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Collect parameter identifiers referenced in decorators, if-guards, asserts, or matches."""
    guarded: set[str] = set()
    for dec in func.decorator_list:
        guarded |= _names_in_ast(dec)
    for node in iter_local_scope_nodes(func):
        if isinstance(node, ast.If | ast.Assert):
            guarded |= _names_in_ast(node.test)
        elif isinstance(node, ast.Match):
            guarded |= _names_in_ast(node.subject)
    return guarded


_PATH_VAR_EXACT: Final[frozenset[str]] = frozenset(
    {"path", "dir", "root", "repo", "folder", "tmp_path", "cwd"}
)
_PATH_VAR_SUFFIXES: Final[tuple[str, ...]] = (
    "_path",
    "_dir",
    "_root",
    "_repo",
    "_folder",
    "_file",
    "_filename",
)
_SEGMENT_PARAM_SUFFIXES: Final[tuple[str, ...]] = (
    "_name",
    "_filename",
    "_file",
    "_path",
    "_dir",
    "_slug",
    "_rel",
    "_subpath",
)


def _non_path_param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return parameter names excluding Path/PurePath/str path-join operands."""
    names: set[str] = set()
    for arg in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs):
        if arg.annotation is not None:
            ann_str = ast.unparse(arg.annotation)
            if any(tok in ann_str for tok in ("Path", "str", "IdentifierStr")):
                continue
        names.add(arg.arg)
    return names


def _expr_is_path_like(expr: ast.expr, *, remaining_depth: int = 8) -> bool:
    if remaining_depth <= 0:
        return False
    if isinstance(expr, ast.Name):
        low = expr.id.lower()
        return low in _PATH_VAR_EXACT or low.endswith(_PATH_VAR_SUFFIXES)
    if isinstance(expr, ast.Attribute):
        low = expr.attr.lower()
        return low in _PATH_VAR_EXACT or low.endswith(_PATH_VAR_SUFFIXES)
    if isinstance(expr, ast.Call):
        tail = dotted_expr_name(expr.func).rsplit(".", maxsplit=1)[-1]
        return tail in {"Path", "PurePath", "PurePosixPath", "PureWindowsPath"}
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return _expr_is_path_like(expr.left, remaining_depth=remaining_depth - 1)
    return False


def _is_path_join_div(node: ast.BinOp) -> bool:
    if not isinstance(node.op, ast.Div):
        return False
    if _expr_is_path_like(node.left):
        return True
    return isinstance(node.right, ast.Name) and node.right.id.lower().endswith(
        _SEGMENT_PARAM_SUFFIXES
    )


@jezz_callable
def check_dbc_001(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-DBC-001 (preconditions, postconditions, & validators)."""
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        discrepancies.extend(_check_contract_decorators(unit, ctx))
        param_names = _non_path_param_names(ctx.node)
        guarded = _guarded_parameter_names(ctx.node)
        for node in iter_local_scope_nodes(ctx.node):
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Div | ast.FloorDiv | ast.Mod)
                and not _is_path_join_div(node)
                and isinstance(node.right, ast.Name)
                and node.right.id in param_names
                and node.right.id not in guarded
            ):
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-DBC-001",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=node.lineno,
                            column=node.col_offset,
                            symbol=ctx.qualname,
                        ),
                        message=(
                            f"Divisor parameter '{node.right.id}' in '{ctx.qualname}' lacks "
                            "a precondition or zero-guard."
                        ),
                        remediation=(
                            f"Add @icontract.require(lambda {node.right.id}: "
                            f"{node.right.id} != 0) or an explicit zero-check."
                        ),
                    )
                )
    return discrepancies


def _annotation_uses_any(annotation: ast.expr) -> bool:
    for sub in ast.walk(annotation):
        if isinstance(sub, ast.Name) and sub.id == "Any":
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "Any":
            return True
    return False


def _check_callable_annotations(unit: SourceUnit, ctx: CallableContext) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    positional = [*ctx.node.args.posonlyargs, *ctx.node.args.args]
    for idx, arg in enumerate(positional):
        is_receiver = idx == 0 and ctx.enclosing_class is not None and arg.arg in {"self", "cls"}
        if not is_receiver and arg.annotation is None:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-002",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=arg.lineno,
                        column=arg.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=f"Parameter '{arg.arg}' in '{ctx.qualname}' lacks a type annotation.",
                    remediation=f"Annotate '{arg.arg}' with an explicit static type.",
                )
            )
    extra_args = [
        *ctx.node.args.kwonlyargs,
        *([ctx.node.args.vararg] if ctx.node.args.vararg is not None else []),
        *([ctx.node.args.kwarg] if ctx.node.args.kwarg is not None else []),
    ]
    for arg in extra_args:
        if arg.annotation is None:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-002",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=arg.lineno,
                        column=arg.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=f"Parameter '{arg.arg}' in '{ctx.qualname}' lacks a type annotation.",
                    remediation=f"Annotate '{arg.arg}' with an explicit static type.",
                )
            )
    if ctx.node.returns is None:
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-DBC-002",
                location=SourceSpan(
                    file_path=unit.file_path,
                    line=ctx.node.lineno,
                    column=ctx.node.col_offset,
                    symbol=ctx.qualname,
                ),
                message=f"Callable '{ctx.qualname}' is missing a return type annotation.",
                remediation="Add an explicit return annotation (use '-> None' for procedures).",
            )
        )
    return discrepancies


def _node_type_annotation(node: ast.AST) -> ast.expr | None:
    if isinstance(node, ast.arg):
        return node.annotation
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return node.returns
    if isinstance(node, ast.AnnAssign):
        return node.annotation
    return None


@jezz_callable
def check_dbc_002(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-DBC-002 (100% callable annotations & zero Any erasure)."""
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        discrepancies.extend(_check_callable_annotations(unit, ctx))

    for node in ast.walk(unit.tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "Any" for a in node.names):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-002",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message="Importing 'Any' erases static type guarantees.",
                    remediation="Use concrete types, TypeVars, Protocols, or 'object'.",
                )
            )
        annotation = _node_type_annotation(node)
        if annotation is not None and _annotation_uses_any(annotation):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-002",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=annotation.lineno,
                        column=annotation.col_offset,
                    ),
                    message="Type annotation uses 'Any', bypassing static type verification.",
                    remediation="Replace 'Any' with a bounded generic, Protocol, or 'object'.",
                )
            )
    return discrepancies


def _is_void_return_annotation(returns: ast.expr) -> bool:
    if isinstance(returns, ast.Constant) and returns.value is None:
        return True
    tail = dotted_expr_name(returns).rsplit(".", maxsplit=1)[-1]
    return tail in {"None", "Never", "NoReturn"}


def _stmt_always_terminates(stmt: ast.stmt, *, remaining_depth: int) -> bool:
    """Fuel-bounded check returning True iff ``stmt`` terminates on every branch."""
    if remaining_depth <= 0:
        return False
    nxt = remaining_depth - 1
    if isinstance(stmt, ast.Return | ast.Raise):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        tail = dotted_expr_name(stmt.value.func).rsplit(".", maxsplit=1)[-1]
        return tail in {"assert_never", "exit"}
    if isinstance(stmt, ast.If):
        return (
            bool(stmt.orelse)
            and _block_terminates(stmt.body, remaining_depth=nxt)
            and _block_terminates(stmt.orelse, remaining_depth=nxt)
        )
    if isinstance(stmt, ast.With | ast.AsyncWith):
        return _block_terminates(stmt.body, remaining_depth=nxt)
    if isinstance(stmt, ast.Try | ast.TryStar):
        if _block_terminates(stmt.finalbody, remaining_depth=nxt):
            return True
        return _block_terminates(stmt.body, remaining_depth=nxt) and all(
            _block_terminates(h.body, remaining_depth=nxt) for h in stmt.handlers
        )
    if isinstance(stmt, ast.Match):
        has_irrefutable = any(
            isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None and c.guard is None
            for c in stmt.cases
        )
        return has_irrefutable and all(
            _block_terminates(c.body, remaining_depth=nxt) for c in stmt.cases
        )
    return False


def _block_terminates(stmts: Sequence[ast.stmt], *, remaining_depth: int = 64) -> bool:
    if remaining_depth <= 0:
        return False
    return any(_stmt_always_terminates(stmt, remaining_depth=remaining_depth - 1) for stmt in stmts)


def _is_side_effect_visitor_method(
    ctx: CallableContext,
    returns: Sequence[ast.Return],
) -> bool:
    return (
        ctx.enclosing_class is not None
        and is_node_visitor_class(ctx.enclosing_class)
        and ctx.node.name.startswith("visit_")
        and not any(r.value is not None for r in returns)
    )


@jezz_callable
def check_dbc_003(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-DBC-003 (total return paths & no mixed bare/value returns)."""
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        if is_abstract_or_overload(ctx):
            continue
        local_nodes = list(iter_local_scope_nodes(ctx.node))
        if any(isinstance(n, ast.Yield | ast.YieldFrom) for n in local_nodes):
            continue

        returns = [n for n in local_nodes if isinstance(n, ast.Return)]
        if any(r.value is not None for r in returns) and any(r.value is None for r in returns):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-003",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=ctx.node.lineno,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=f"Callable '{ctx.qualname}' mixes value 'return expr' and bare return.",
                    remediation="Use explicit 'return None' on optional branches with 'T | None'.",
                )
            )

        if _is_side_effect_visitor_method(ctx, returns):
            continue
        body = strip_leading_docstring(ctx.node.body)
        expects_value = ctx.node.returns is not None and not _is_void_return_annotation(
            ctx.node.returns
        )
        if expects_value and not _block_terminates(body):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-003",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=ctx.node.lineno,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=(
                        f"Value-returning callable '{ctx.qualname}' can fall off end into "
                        "implicit None."
                    ),
                    remediation="Ensure every control-flow branch ends with 'return' or 'raise'.",
                )
            )
    return discrepancies


def _requires_public_breaker(ctx: CallableContext, unit_path: str) -> bool:
    """Return True iff ``ctx`` is a public module/class feeder under Option A."""
    if ctx.node.name.startswith("_") or is_abstract_or_overload(ctx):
        return False
    # Decorator factories in guards.py ARE the breaker implementations themselves
    if unit_path.endswith("jezz/guards.py") and ctx.node.name in {
        "jezz_callable",
        "jezz_generator",
    }:
        return False
    # Nested local closures inside functions are not public module feeders
    if ctx.enclosing_class is None and "." in ctx.qualname:
        return False
    dec_tails = {dotted_expr_name(d).rsplit(".", maxsplit=1)[-1] for d in ctx.node.decorator_list}
    if "property" in dec_tails:
        return False
    if ctx.enclosing_class is not None:
        if is_node_visitor_class(ctx.enclosing_class) and (
            ctx.node.name == "visit" or ctx.node.name.startswith("visit_")
        ):
            return False
        bases = class_base_tails(ctx.enclosing_class)
        if bases & {"BaseNode", "AtomicBehaviorGraphNode"} and ctx.node.name == "run":
            return False
    return True


@jezz_callable
def check_dbc_004(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-DBC-004 (Option A @jezz_callable signature & return breakers)."""
    if unit.is_test_file:
        return []
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        if not _requires_public_breaker(ctx, unit.file_path):
            continue
        dec_tails = {
            dotted_expr_name(d).rsplit(".", maxsplit=1)[-1] for d in ctx.node.decorator_list
        }
        span = SourceSpan(
            file_path=unit.file_path,
            line=ctx.node.lineno,
            column=ctx.node.col_offset,
            symbol=ctx.qualname,
        )
        if not (dec_tails & _CALL_BREAKER_DEC_TAILS):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-004",
                    location=span,
                    message=(
                        f"Public callable '{ctx.qualname}' lacks a runtime signature breaker "
                        "(@jezz_callable)."
                    ),
                    remediation="Decorate public callable with @jezz_callable.",
                )
            )
            continue
        is_gen = any(
            isinstance(n, ast.Yield | ast.YieldFrom) for n in iter_local_scope_nodes(ctx.node)
        )
        if "jezz_generator" in dec_tails and not is_gen:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-004",
                    location=span,
                    message=(
                        f"Non-generator '{ctx.qualname}' uses @jezz_generator, skipping "
                        "return validation."
                    ),
                    remediation="Use @jezz_callable so return values are validated.",
                )
            )
    return discrepancies


def _field_call_has_bounds(value: ast.expr | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    tail = dotted_expr_name(value.func).rsplit(".", maxsplit=1)[-1]
    if tail != "Field":
        return False
    return any(kw.arg in _BOUND_FIELD_KWARGS for kw in value.keywords)


def _unwrap_optional_arms(expr: ast.expr, *, remaining_depth: int = 16) -> list[ast.expr]:
    """Split ``A | B | None`` union expressions into non-None type arms."""
    if remaining_depth <= 0:
        return [expr]
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        left = _unwrap_optional_arms(expr.left, remaining_depth=remaining_depth - 1)
        right = _unwrap_optional_arms(expr.right, remaining_depth=remaining_depth - 1)
        return [*left, *right]
    if isinstance(expr, ast.Constant) and expr.value is None:
        return []
    if isinstance(expr, ast.Name) and expr.id == "None":
        return []
    return [expr]


def _is_unbounded_subscript(arm: ast.Subscript) -> bool:
    root = dotted_expr_name(arm.value).rsplit(".", maxsplit=1)[-1]
    if root == "Annotated":
        return False
    if root in _UNBOUNDED_CONTAINER_NAMES:
        return True
    return (
        root == "tuple"
        and isinstance(arm.slice, ast.Tuple)
        and any(isinstance(elt, ast.Constant) and elt.value is Ellipsis for elt in arm.slice.elts)
    )


def _is_unbounded_model_type(expr: ast.expr) -> bool:
    """Return True iff ``expr`` is an unconstrained scalar or unbounded collection type."""
    all_raw = _UNBOUNDED_SCALAR_NAMES | _UNBOUNDED_CONTAINER_NAMES | {"tuple"}
    for arm in _unwrap_optional_arms(expr):
        if isinstance(arm, ast.Name) and arm.id in all_raw:
            return True
        if isinstance(arm, ast.Subscript) and _is_unbounded_subscript(arm):
            return True
    return False


def _check_model_field_bounds(unit: SourceUnit, cls_node: ast.ClassDef) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        fname = stmt.target.id
        ann_root = dotted_expr_name(stmt.annotation).rsplit(".", maxsplit=1)[-1]
        if fname == "model_config" or ann_root == "ClassVar":
            continue
        if not _field_call_has_bounds(stmt.value) and _is_unbounded_model_type(stmt.annotation):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-005",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=stmt.lineno,
                        column=stmt.col_offset,
                        symbol=f"{cls_node.name}.{fname}",
                    ),
                    message=f"Model field '{cls_node.name}.{fname}' uses an unbounded type.",
                    remediation=(
                        "Use a bounded Annotated alias or Field(min_length/max_length/ge/le)."
                    ),
                )
            )
    return discrepancies


def _check_public_param_bounds(unit: SourceUnit, ctx: CallableContext) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    all_args = [*ctx.node.args.posonlyargs, *ctx.node.args.args, *ctx.node.args.kwonlyargs]
    for arg in all_args:
        if arg.annotation is None:
            continue
        has_raw_scalar = any(
            isinstance(arm, ast.Name) and arm.id in _UNBOUNDED_SCALAR_NAMES
            for arm in _unwrap_optional_arms(arg.annotation)
        )
        if has_raw_scalar:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-005",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=arg.lineno,
                        column=arg.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=(
                        f"Public parameter '{arg.arg}' in '{ctx.qualname}' uses bare "
                        "unbounded scalar type."
                    ),
                    remediation="Use IdentifierStr, BoundedInt, PositiveInt, FiniteFloat, etc.",
                )
            )
    return discrepancies


@jezz_callable
def check_dbc_005(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-DBC-005 (bounded domain scalars, collections, & TypeAliases)."""
    if unit.is_test_file:
        return []
    discrepancies: list[RawDiscrepancy] = []
    for node in unit.tree.body:
        if (
            isinstance(node, ast.TypeAlias)
            and isinstance(node.value, ast.Name)
            and node.value.id in (_UNBOUNDED_SCALAR_NAMES | _UNBOUNDED_CONTAINER_NAMES)
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-005",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=node.lineno,
                        column=node.col_offset,
                        symbol=node.name.id,
                    ),
                    message=f"TypeAlias '{node.name.id}' aliases bare unbounded '{node.value.id}'.",
                    remediation="Wrap alias target in Annotated[..., Field(...)].",
                )
            )
    for cls_node in unit.classes:
        if is_pydantic_model_class(cls_node):
            discrepancies.extend(_check_model_field_bounds(unit, cls_node))
    for ctx in unit.callables:
        if _requires_public_breaker(ctx, unit.file_path):
            discrepancies.extend(_check_public_param_bounds(unit, ctx))
    return discrepancies
