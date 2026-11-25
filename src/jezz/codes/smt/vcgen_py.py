"""Python AST and shared contract-directive VCGen into quantifier-free SMT IR (Track 27)."""

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import Field

from jezz.codes.ast_utils import (
    CallableContext,
    CommentToken,
    GuardedSourceUnit,
    SourceUnit,
    dotted_expr_name,
)
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.codes.smt.ir import (
    ContractObligation,
    SmtAssign,
    SmtAssume,
    SmtBinary,
    SmtBinaryOp,
    SmtBranch,
    SmtConstBool,
    SmtConstInt,
    SmtConstReal,
    SmtConstStr,
    SmtExpr,
    SmtIte,
    SmtLen,
    SmtReturn,
    SmtSort,
    SmtStmt,
    SmtUnary,
    SmtUnaryOp,
    SmtVar,
    SymbolicVar,
)
from jezz.guards import (
    BoundedText,
    IdentifierStr,
    LineNumber,
    LoopBudget,
    jezz_callable,
)

_MAX_AST_DEPTH: Final[int] = 64
_MAX_STMT_STEPS: Final[int] = 512
_DIRECTIVE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:#|//+|/\*+|\*+)\s*@(requires|ensures)\s*:\s*(.+?)(?:\*/)?$"
)
_LOGICAL_NOT_RE: Final[re.Pattern[str]] = re.compile(r"(?<![=!<>])!(?!=)")


@dataclass(frozen=True)
class TypeBoundSpec:
    """Resolved SMT sort and static value/length bounds for a scalar type annotation."""

    sort: SmtSort
    gt: float | int | None = None
    ge: float | int | None = None
    lt: float | int | None = None
    le: float | int | None = None
    min_len: int | None = None
    max_len: int | None = None
    has_explicit_bound: bool = False


_BUILTIN_TYPE_SPECS: Final[Mapping[str, TypeBoundSpec]] = MappingProxyType(
    {
        "int": TypeBoundSpec(sort=SmtSort.INT),
        "StrictInt": TypeBoundSpec(sort=SmtSort.INT),
        "float": TypeBoundSpec(sort=SmtSort.REAL),
        "StrictFloat": TypeBoundSpec(sort=SmtSort.REAL),
        "FiniteFloat": TypeBoundSpec(sort=SmtSort.REAL),
        "bool": TypeBoundSpec(sort=SmtSort.BOOL),
        "StrictBool": TypeBoundSpec(sort=SmtSort.BOOL),
        "str": TypeBoundSpec(sort=SmtSort.STRING),
        "StrictStr": TypeBoundSpec(sort=SmtSort.STRING),
        "PositiveInt": TypeBoundSpec(sort=SmtSort.INT, gt=0, has_explicit_bound=True),
        "NonNegativeInt": TypeBoundSpec(sort=SmtSort.INT, ge=0, has_explicit_bound=True),
        "BoundedInt": TypeBoundSpec(
            sort=SmtSort.INT, ge=-1_000_000_000, le=1_000_000_000, has_explicit_bound=True
        ),
        "LineNumber": TypeBoundSpec(sort=SmtSort.INT, ge=1, le=10_000_000, has_explicit_bound=True),
        "ColumnOffset": TypeBoundSpec(sort=SmtSort.INT, ge=0, le=100_000, has_explicit_bound=True),
        "SymbolicIntLiteral": TypeBoundSpec(
            sort=SmtSort.INT, ge=-(10**18), le=10**18, has_explicit_bound=True
        ),
        "BoundedSolverTimeoutMs": TypeBoundSpec(
            sort=SmtSort.INT, ge=10, le=30_000, has_explicit_bound=True
        ),
        "PercentageFloat": TypeBoundSpec(
            sort=SmtSort.REAL, ge=0.0, le=1.0, has_explicit_bound=True
        ),
        "GateTimeoutSec": TypeBoundSpec(
            sort=SmtSort.REAL, gt=0.0, le=600.0, has_explicit_bound=True
        ),
        "IdentifierStr": TypeBoundSpec(
            sort=SmtSort.STRING, min_len=1, max_len=256, has_explicit_bound=True
        ),
        "ShortText": TypeBoundSpec(
            sort=SmtSort.STRING, min_len=1, max_len=1024, has_explicit_bound=True
        ),
        "BoundedText": TypeBoundSpec(sort=SmtSort.STRING, min_len=0, max_len=65_536),
        "SourceCodeText": TypeBoundSpec(sort=SmtSort.STRING, min_len=0, max_len=5_000_000),
    }
)

_PY_BIN_OPS: Final[Mapping[type[ast.operator], SmtBinaryOp]] = MappingProxyType(
    {
        ast.Add: SmtBinaryOp.ADD,
        ast.Sub: SmtBinaryOp.SUB,
        ast.Mult: SmtBinaryOp.MUL,
        ast.FloorDiv: SmtBinaryOp.FLOORDIV,
        ast.Div: SmtBinaryOp.TRUEDIV,
        ast.Mod: SmtBinaryOp.MOD,
    }
)
_PY_CMP_OPS: Final[Mapping[type[ast.cmpop], SmtBinaryOp]] = MappingProxyType(
    {
        ast.Eq: SmtBinaryOp.EQ,
        ast.NotEq: SmtBinaryOp.NE,
        ast.Lt: SmtBinaryOp.LT,
        ast.LtE: SmtBinaryOp.LE,
        ast.Gt: SmtBinaryOp.GT,
        ast.GtE: SmtBinaryOp.GE,
    }
)
_BOOL_RESULT_OPS: Final[frozenset[SmtBinaryOp]] = frozenset(
    {
        SmtBinaryOp.EQ,
        SmtBinaryOp.NE,
        SmtBinaryOp.LT,
        SmtBinaryOp.LE,
        SmtBinaryOp.GT,
        SmtBinaryOp.GE,
        SmtBinaryOp.AND,
        SmtBinaryOp.OR,
        SmtBinaryOp.IMPLIES,
        SmtBinaryOp.STR_PREFIX,
    }
)


def _const_num(val: float | int, sort: SmtSort) -> SmtExpr:
    if sort == SmtSort.REAL or isinstance(val, float):
        return SmtConstReal(value=float(val))
    return SmtConstInt(value=int(val))


@jezz_callable
def build_bound_exprs(var_name: IdentifierStr, spec: TypeBoundSpec) -> tuple[SmtExpr, ...]:
    """Materialize ``SmtExpr`` predicates encoding ``spec`` bounds on ``var_name``."""
    target: SmtExpr = SmtVar(name=var_name, sort=spec.sort)
    clauses: list[SmtExpr] = []
    num_pairs = (
        (SmtBinaryOp.GT, spec.gt),
        (SmtBinaryOp.GE, spec.ge),
        (SmtBinaryOp.LT, spec.lt),
        (SmtBinaryOp.LE, spec.le),
    )
    for op, bound in num_pairs:
        if bound is not None:
            clauses.append(SmtBinary(op=op, left=target, right=_const_num(bound, spec.sort)))
    if spec.min_len is not None:
        clauses.append(
            SmtBinary(
                op=SmtBinaryOp.GE,
                left=SmtLen(operand=target),
                right=SmtConstInt(value=spec.min_len),
            )
        )
    if spec.max_len is not None:
        clauses.append(
            SmtBinary(
                op=SmtBinaryOp.LE,
                left=SmtLen(operand=target),
                right=SmtConstInt(value=spec.max_len),
            )
        )
    return tuple(clauses)


def _eval_pow_literal(node: ast.BinOp, *, remaining_depth: int) -> int | None:
    base = _extract_numeric_literal(node.left, remaining_depth=remaining_depth - 1)
    exp = _extract_numeric_literal(node.right, remaining_depth=remaining_depth - 1)
    if not (isinstance(base, int) and isinstance(exp, int) and 0 <= exp <= 18):
        return None
    val = int(base**exp)
    return val if -(10**18) <= val <= 10**18 else None


def _extract_numeric_literal(node: ast.expr, *, remaining_depth: int = 16) -> int | float | None:
    if remaining_depth <= 0:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return None if isinstance(node.value, bool) else node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _extract_numeric_literal(node.operand, remaining_depth=remaining_depth - 1)
        return -inner if inner is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        return _eval_pow_literal(node, remaining_depth=remaining_depth - 1)
    return None


def _apply_field_call_kwargs(base: TypeBoundSpec, call: ast.Call) -> TypeBoundSpec:
    kw_vals: dict[str, int | float] = {}
    for kw in call.keywords:
        if kw.arg is None:
            continue
        lit = _extract_numeric_literal(kw.value)
        if lit is not None:
            kw_vals[kw.arg] = lit
    if not kw_vals:
        return base
    min_l = kw_vals.get("min_length")
    max_l = kw_vals.get("max_length")
    return TypeBoundSpec(
        sort=base.sort,
        gt=kw_vals.get("gt", base.gt),
        ge=kw_vals.get("ge", base.ge),
        lt=kw_vals.get("lt", base.lt),
        le=kw_vals.get("le", base.le),
        min_len=int(min_l) if min_l is not None else base.min_len,
        max_len=int(max_l) if max_l is not None else base.max_len,
        has_explicit_bound=True,
    )


def _resolve_annotated_subscript(
    slice_node: ast.expr,
    aliases: Mapping[str, TypeBoundSpec],
    *,
    remaining_depth: int,
) -> TypeBoundSpec | None:
    if remaining_depth <= 0 or not isinstance(slice_node, ast.Tuple) or not slice_node.elts:
        return None
    base = _resolve_py_type_spec(slice_node.elts[0], aliases, remaining_depth=remaining_depth - 1)
    if base is None:
        return None
    cur = base
    for meta in slice_node.elts[1:]:
        if isinstance(meta, ast.Call) and dotted_expr_name(meta.func).endswith("Field"):
            cur = _apply_field_call_kwargs(cur, meta)
    return cur


def _resolve_py_type_spec(
    ann: ast.expr | None,
    aliases: Mapping[str, TypeBoundSpec],
    *,
    remaining_depth: int = 16,
) -> TypeBoundSpec | None:
    if remaining_depth <= 0 or ann is None or isinstance(ann, ast.BinOp):
        return None
    if isinstance(ann, ast.Subscript):
        head = dotted_expr_name(ann.value).rsplit(".", maxsplit=1)[-1]
        if head != "Annotated":
            return None
        return _resolve_annotated_subscript(ann.slice, aliases, remaining_depth=remaining_depth - 1)
    tail = dotted_expr_name(ann).rsplit(".", maxsplit=1)[-1]
    return aliases.get(tail) or _BUILTIN_TYPE_SPECS.get(tail)


def _collect_module_type_aliases(tree: ast.Module) -> dict[str, TypeBoundSpec]:
    aliases: dict[str, TypeBoundSpec] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.TypeAlias):
            spec = _resolve_py_type_spec(stmt.value, aliases)
            if spec is not None:
                aliases[stmt.name.id] = spec
    return aliases


def _normalize_directive_syntax(raw: str) -> str:
    step = raw.replace("&&", " and ").replace("||", " or ")
    step = step.replace("===", "==").replace("!==", "!=")
    step = re.sub(r"\btrue\b", "True", step)
    step = re.sub(r"\bfalse\b", "False", step)
    return _LOGICAL_NOT_RE.sub(" not ", step)


@jezz_callable
def extract_comment_directives(
    comments: Annotated[Sequence[CommentToken], Field(max_length=100_000)],
    start_line: LineNumber,
    end_line: LineNumber,
) -> tuple[tuple[BoundedText, ...], tuple[BoundedText, ...]]:
    """Extract ``(@requires, @ensures)`` expressions attached above or inside a symbol."""
    reqs: list[str] = []
    ens: list[str] = []
    window_start = max(1, start_line - 6)
    for tok in comments:
        if not (window_start <= tok.line <= end_line):
            continue
        match = _DIRECTIVE_RE.match(tok.text.strip())
        if match is None:
            continue
        if match.group(1) == "requires":
            reqs.append(match.group(2).strip()[:2048])
        else:
            ens.append(match.group(2).strip()[:2048])
    return tuple(reqs), tuple(ens)


def _lower_isinstance_bool(
    call: ast.Call,
    var_sorts: Mapping[str, SmtSort],
) -> SmtExpr | None:
    if len(call.args) != 2 or not isinstance(call.args[0], ast.Name):
        return None
    if dotted_expr_name(call.args[1]) != "bool":
        return None
    sort = var_sorts.get(call.args[0].id)
    if sort in {SmtSort.INT, SmtSort.REAL, SmtSort.STRING}:
        return SmtConstBool(value=False)
    return None


def _lower_minmax_call(
    fn_name: str,
    args: Sequence[ast.expr],
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    if len(args) != 2:
        return None
    lhs = _lower_py_ast_expr(args[0], var_sorts, remaining_depth=remaining_depth - 1)
    rhs = _lower_py_ast_expr(args[1], var_sorts, remaining_depth=remaining_depth - 1)
    if lhs is None or rhs is None:
        return None
    cmp_op = SmtBinaryOp.LE if fn_name == "min" else SmtBinaryOp.GE
    return SmtIte(cond=SmtBinary(op=cmp_op, left=lhs, right=rhs), then_expr=lhs, else_expr=rhs)


def _lower_startswith_call(
    call: ast.Call,
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "startswith"):
        return None
    if len(call.args) != 1:
        return None
    recv = _lower_py_ast_expr(call.func.value, var_sorts, remaining_depth=remaining_depth - 1)
    pref = _lower_py_ast_expr(call.args[0], var_sorts, remaining_depth=remaining_depth - 1)
    if recv is None or pref is None:
        return None
    return SmtBinary(op=SmtBinaryOp.STR_PREFIX, left=recv, right=pref)


def _lower_builtin_call(
    call: ast.Call,
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    if remaining_depth <= 0:
        return None
    fn_name = dotted_expr_name(call.func)
    if fn_name == "isinstance":
        return _lower_isinstance_bool(call, var_sorts)
    if fn_name in {"len", "abs"} and len(call.args) == 1:
        arg = _lower_py_ast_expr(call.args[0], var_sorts, remaining_depth=remaining_depth - 1)
        if arg is None:
            return None
        return SmtLen(operand=arg) if fn_name == "len" else SmtUnary(op=SmtUnaryOp.ABS, operand=arg)
    if fn_name in {"min", "max"}:
        return _lower_minmax_call(
            fn_name, call.args, var_sorts, remaining_depth=remaining_depth - 1
        )
    return _lower_startswith_call(call, var_sorts, remaining_depth=remaining_depth - 1)


def _lower_py_compare(
    node: ast.Compare,
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    if remaining_depth <= 0 or len(node.ops) != len(node.comparators) or not node.ops:
        return None
    terms: list[SmtExpr] = []
    left_ast = node.left
    for op_node, right_ast in zip(node.ops, node.comparators, strict=True):
        op_kind = _PY_CMP_OPS.get(type(op_node))
        lhs = _lower_py_ast_expr(left_ast, var_sorts, remaining_depth=remaining_depth - 1)
        rhs = _lower_py_ast_expr(right_ast, var_sorts, remaining_depth=remaining_depth - 1)
        if op_kind is None or lhs is None or rhs is None:
            return None
        terms.append(SmtBinary(op=op_kind, left=lhs, right=rhs))
        left_ast = right_ast
    acc = terms[0]
    for nxt in terms[1:]:
        acc = SmtBinary(op=SmtBinaryOp.AND, left=acc, right=nxt)
    return acc


def _lower_py_boolop(
    node: ast.BoolOp,
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    if remaining_depth <= 0 or not node.values:
        return None
    op = SmtBinaryOp.AND if isinstance(node.op, ast.And) else SmtBinaryOp.OR
    valid: list[SmtExpr] = []
    for item_node in node.values:
        low = _lower_py_ast_expr(item_node, var_sorts, remaining_depth=remaining_depth - 1)
        if low is None:
            return None
        valid.append(low)
    acc = valid[0]
    for nxt in valid[1:]:
        acc = SmtBinary(op=op, left=acc, right=nxt)
    return acc


def _lower_py_constant(node: ast.Constant) -> SmtExpr | None:
    if isinstance(node.value, bool):
        return SmtConstBool(value=node.value)
    if isinstance(node.value, int):
        return SmtConstInt(value=node.value) if -(10**18) <= node.value <= 10**18 else None
    if isinstance(node.value, float):
        return SmtConstReal(value=node.value)
    if isinstance(node.value, str):
        return SmtConstStr(value=node.value[:65_536])
    return None


def _lower_py_unary(
    node: ast.UnaryOp,
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    inner = _lower_py_ast_expr(node.operand, var_sorts, remaining_depth=remaining_depth - 1)
    if inner is None:
        return None
    if isinstance(node.op, ast.Not):
        return SmtUnary(op=SmtUnaryOp.NOT, operand=inner)
    if isinstance(node.op, ast.USub):
        return SmtUnary(op=SmtUnaryOp.NEG, operand=inner)
    return inner if isinstance(node.op, ast.UAdd) else None


def _lower_py_binop(
    node: ast.BinOp,
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    if remaining_depth <= 0:
        return None
    op = _PY_BIN_OPS.get(type(node.op))
    if op is None:
        return None
    lhs = _lower_py_ast_expr(node.left, var_sorts, remaining_depth=remaining_depth - 1)
    rhs = _lower_py_ast_expr(node.right, var_sorts, remaining_depth=remaining_depth - 1)
    if lhs is None or rhs is None:
        return None
    is_str_add = op == SmtBinaryOp.ADD and _infer_expr_sort(lhs) == SmtSort.STRING
    return SmtBinary(op=SmtBinaryOp.STR_CONCAT if is_str_add else op, left=lhs, right=rhs)


def _lower_py_ifexp(
    node: ast.IfExp,
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    c = _lower_py_ast_expr(node.test, var_sorts, remaining_depth=remaining_depth - 1)
    t = _lower_py_ast_expr(node.body, var_sorts, remaining_depth=remaining_depth - 1)
    e = _lower_py_ast_expr(node.orelse, var_sorts, remaining_depth=remaining_depth - 1)
    if c is None or t is None or e is None:
        return None
    return SmtIte(cond=c, then_expr=t, else_expr=e)


def _lower_py_ast_expr(
    node: ast.expr,
    var_sorts: Mapping[str, SmtSort],
    *,
    remaining_depth: int = _MAX_AST_DEPTH,
) -> SmtExpr | None:
    if remaining_depth <= 0:
        return None
    if isinstance(node, ast.Constant):
        return _lower_py_constant(node)
    if isinstance(node, ast.Name):
        sort = var_sorts.get(node.id)
        return SmtVar(name=node.id, sort=sort) if sort is not None else None
    if isinstance(node, ast.UnaryOp):
        return _lower_py_unary(node, var_sorts, remaining_depth=remaining_depth - 1)
    if isinstance(node, ast.BinOp):
        return _lower_py_binop(node, var_sorts, remaining_depth=remaining_depth - 1)
    if isinstance(node, ast.BoolOp):
        return _lower_py_boolop(node, var_sorts, remaining_depth=remaining_depth - 1)
    if isinstance(node, ast.Compare):
        return _lower_py_compare(node, var_sorts, remaining_depth=remaining_depth - 1)
    if isinstance(node, ast.IfExp):
        return _lower_py_ifexp(node, var_sorts, remaining_depth=remaining_depth - 1)
    if isinstance(node, ast.Call):
        return _lower_builtin_call(node, var_sorts, remaining_depth=remaining_depth - 1)
    return None


@jezz_callable
def parse_contract_directive_expr(
    raw_expr: BoundedText,
    var_sorts: Annotated[Mapping[IdentifierStr, SmtSort], Field(max_length=256)],
) -> SmtExpr | None:
    """Parse a normalized ``@requires``/``@ensures`` expression safely without ``eval``."""
    normalized = _normalize_directive_syntax(raw_expr)
    try:
        parsed = ast.parse(normalized, mode="eval")
    except SyntaxError:
        return None
    return _lower_py_ast_expr(parsed.body, var_sorts, remaining_depth=_MAX_AST_DEPTH)


def _infer_expr_sort(expr: SmtExpr) -> SmtSort:
    if isinstance(expr, SmtConstBool):
        return SmtSort.BOOL
    if isinstance(expr, SmtUnary) and expr.op == SmtUnaryOp.NOT:
        return SmtSort.BOOL
    if isinstance(expr, SmtConstReal):
        return SmtSort.REAL
    if isinstance(expr, SmtConstStr):
        return SmtSort.STRING
    if isinstance(expr, SmtVar):
        return expr.sort
    if isinstance(expr, SmtBinary):
        if expr.op in _BOOL_RESULT_OPS:
            return SmtSort.BOOL
        if expr.op == SmtBinaryOp.TRUEDIV:
            return SmtSort.REAL
        if expr.op == SmtBinaryOp.STR_CONCAT:
            return SmtSort.STRING
    return SmtSort.INT


def _lower_py_if_stmt(
    stmt: ast.If,
    local_sorts: dict[str, SmtSort],
    loop: LoopBudget,
    *,
    remaining_depth: int,
) -> tuple[SmtStmt | None, str]:
    cond = _lower_py_ast_expr(stmt.test, local_sorts, remaining_depth=remaining_depth - 1)
    if cond is None:
        return None, "Branch condition uses unmodeled expression."
    if len(stmt.body) == 1 and isinstance(stmt.body[0], ast.Raise) and not stmt.orelse:
        return SmtAssume(cond=SmtUnary(op=SmtUnaryOp.NOT, operand=cond)), ""
    then_s = dict(local_sorts)
    else_s = dict(local_sorts)
    then_stmts, t_err = _lower_py_stmt_block(
        stmt.body, then_s, loop, remaining_depth=remaining_depth - 1
    )
    if then_stmts is None:
        return None, t_err
    else_stmts, e_err = _lower_py_stmt_block(
        stmt.orelse, else_s, loop, remaining_depth=remaining_depth - 1
    )
    if else_stmts is None:
        return None, e_err
    local_sorts.update(then_s)
    local_sorts.update(else_s)
    return SmtBranch(cond=cond, then_body=then_stmts, else_body=else_stmts), ""


def _extract_assign_pair(stmt: ast.stmt) -> tuple[str, ast.expr] | None:
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        return stmt.targets[0].id, stmt.value
    if (
        isinstance(stmt, ast.AnnAssign)
        and isinstance(stmt.target, ast.Name)
        and stmt.value is not None
    ):
        return stmt.target.id, stmt.value
    return None


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _lower_py_single_stmt(
    stmt: ast.stmt,
    local_sorts: dict[str, SmtSort],
    loop: LoopBudget,
    *,
    remaining_depth: int,
) -> tuple[SmtStmt | None, str, bool]:
    if _is_docstring_stmt(stmt):
        return None, "", True
    assign_pair = _extract_assign_pair(stmt)
    if assign_pair is not None:
        target_name, rhs_ast = assign_pair
        val = _lower_py_ast_expr(rhs_ast, local_sorts, remaining_depth=remaining_depth - 1)
        if val is None:
            return None, f"Assignment to '{target_name}' uses opaque expression.", False
        local_sorts[target_name] = _infer_expr_sort(val)
        return SmtAssign(target=target_name, value=val), "", False
    if isinstance(stmt, ast.Return):
        if stmt.value is None:
            return None, "Bare return without value is outside symbolic fragment.", False
        val = _lower_py_ast_expr(stmt.value, local_sorts, remaining_depth=remaining_depth - 1)
        if val is None:
            return None, "Return uses opaque expression.", False
        return SmtReturn(value=val), "", False
    if isinstance(stmt, ast.If):
        br, err = _lower_py_if_stmt(stmt, local_sorts, loop, remaining_depth=remaining_depth - 1)
        return br, err, False
    return None, f"Statement '{type(stmt).__name__}' is outside decidable pure fragment.", False


def _lower_py_stmt_block(
    stmts: Sequence[ast.stmt],
    local_sorts: dict[str, SmtSort],
    loop: LoopBudget,
    *,
    remaining_depth: int,
) -> tuple[tuple[SmtStmt, ...] | None, str]:
    if remaining_depth <= 0:
        return None, "Function nesting exceeded symbolic depth budget."
    out: list[SmtStmt] = []
    for stmt in stmts:
        loop.tick()
        lowered, err, skip = _lower_py_single_stmt(
            stmt, local_sorts, loop, remaining_depth=remaining_depth - 1
        )
        if skip:
            continue
        if lowered is None:
            return None, err
        out.append(lowered)
    return tuple(out), ""


@dataclass
class _ContractAcc:
    pres: list[SmtExpr]
    posts: list[SmtExpr]
    has_contract: bool = False
    opaque_pre: bool = False
    opaque_post: bool = False


def _record_contract_expr(acc: _ContractAcc, expr: SmtExpr | None, *, is_req: bool) -> None:
    if expr is None:
        if is_req:
            acc.opaque_pre = True
        else:
            acc.opaque_post = True
        return
    if is_req:
        acc.pres.append(expr)
    else:
        acc.posts.append(expr)


def _extract_icontract_decorators(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    var_sorts: Mapping[str, SmtSort],
) -> _ContractAcc:
    acc = _ContractAcc(pres=[], posts=[])
    for dec in fn_node.decorator_list:
        if not isinstance(dec, ast.Call) or not dec.args:
            continue
        dname = dotted_expr_name(dec.func)
        if dname not in {"icontract.require", "require", "icontract.ensure", "ensure"}:
            continue
        acc.has_contract = True
        is_req = dname.endswith("require")
        first = dec.args[0]
        expr = _lower_py_ast_expr(first.body, var_sorts) if isinstance(first, ast.Lambda) else None
        _record_contract_expr(acc, expr, is_req=is_req)
    return acc


def _apply_comment_directives(
    acc: _ContractAcc,
    comments: Sequence[CommentToken],
    start_line: int,
    end_line: int,
    var_sorts: Mapping[str, SmtSort],
) -> None:
    req_dirs, ens_dirs = extract_comment_directives(comments, max(1, start_line), max(1, end_line))
    if req_dirs or ens_dirs:
        acc.has_contract = True
    for raw_r in req_dirs:
        _record_contract_expr(acc, parse_contract_directive_expr(raw_r, var_sorts), is_req=True)
    for raw_e in ens_dirs:
        _record_contract_expr(acc, parse_contract_directive_expr(raw_e, var_sorts), is_req=False)


def _extract_leading_raise_preconditions(
    stmts: Sequence[ast.stmt],
    param_sorts: Mapping[str, SmtSort],
) -> list[SmtExpr]:
    extracted: list[SmtExpr] = []
    for stmt in stmts:
        if _is_docstring_stmt(stmt):
            continue
        is_guard_raise = (
            isinstance(stmt, ast.If)
            and len(stmt.body) == 1
            and isinstance(stmt.body[0], ast.Raise)
            and not stmt.orelse
        )
        if not is_guard_raise or not isinstance(stmt, ast.If):
            break
        cond = _lower_py_ast_expr(stmt.test, param_sorts)
        if cond is None:
            break
        extracted.append(SmtUnary(op=SmtUnaryOp.NOT, operand=cond))
    return extracted


def _collect_py_params(
    ctx: CallableContext,
    aliases: Mapping[str, TypeBoundSpec],
) -> tuple[list[SymbolicVar], dict[str, SmtSort], list[SmtExpr], str]:
    fn = ctx.node
    all_args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    if ctx.enclosing_class is not None and all_args and all_args[0].arg in {"self", "cls"}:
        all_args = all_args[1:]
    variables: list[SymbolicVar] = []
    var_sorts: dict[str, SmtSort] = {}
    type_pres: list[SmtExpr] = []
    unmodeled = ""
    for arg in all_args:
        spec = _resolve_py_type_spec(arg.annotation, aliases)
        if spec is None:
            unmodeled = arg.arg
            continue
        variables.append(SymbolicVar(name=arg.arg, sort=spec.sort))
        var_sorts[arg.arg] = spec.sort
        type_pres.extend(build_bound_exprs(arg.arg, spec))
    return variables, var_sorts, type_pres, unmodeled


def _select_py_body_lowering(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    param_sorts: Mapping[str, SmtSort],
    unmodeled_param: str,
    opaque_pre: bool,
) -> tuple[tuple[SmtStmt, ...] | None, str]:
    if unmodeled_param:
        return None, f"Parameter '{unmodeled_param}' has composite/non-scalar type."
    if fn.args.vararg is not None or fn.args.kwarg is not None:
        return None, "Variadic *args/**kwargs callable verified via PBT."
    if opaque_pre:
        return None, "Precondition uses opaque helper predicate; deferred to PBT."
    return _lower_py_stmt_block(
        fn.body,
        dict(param_sorts),
        LoopBudget(max_steps=_MAX_STMT_STEPS),
        remaining_depth=_MAX_AST_DEPTH,
    )


def _build_py_callable_obligation(
    unit: SourceUnit,
    ctx: CallableContext,
    aliases: Mapping[str, TypeBoundSpec],
) -> ContractObligation | None:
    fn = ctx.node
    variables, var_sorts, type_pres, unmodeled = _collect_py_params(ctx, aliases)
    param_only_sorts = dict(var_sorts)
    ret_spec = _resolve_py_type_spec(fn.returns, aliases)
    if ret_spec is not None:
        variables.append(SymbolicVar(name="result", sort=ret_spec.sort))
        var_sorts["result"] = ret_spec.sort

    acc = _extract_icontract_decorators(fn, var_sorts)
    end_ln = getattr(fn, "end_lineno", fn.lineno) or fn.lineno
    _apply_comment_directives(acc, unit.comments, fn.lineno, end_ln, var_sorts)
    has_ret_bound = (
        ret_spec is not None and ret_spec.has_explicit_bound and ret_spec.sort != SmtSort.STRING
    )
    if not (acc.has_contract or has_ret_bound):
        return None

    ret_posts = list(build_bound_exprs("result", ret_spec)) if ret_spec is not None else []
    guard_pres = _extract_leading_raise_preconditions(fn.body, param_only_sorts)
    body_stmts, opaque_reason = _select_py_body_lowering(
        fn, param_only_sorts, unmodeled, acc.opaque_pre
    )
    return ContractObligation(
        obligation_id=f"{unit.file_path}::{ctx.qualname}",
        file_path=unit.file_path,
        line=max(1, fn.lineno),
        language=SupportedLanguage.PYTHON,
        symbol=ctx.qualname,
        variables=tuple(variables),
        preconditions=tuple([*type_pres, *guard_pres, *acc.pres]),
        postconditions=tuple([*ret_posts, *acc.posts]),
        body_stmts=body_stmts,
        has_opaque_postcondition=acc.opaque_post,
        opaque_reason=opaque_reason[:2048],
    )


def _extract_single_model_field(
    stmt: ast.stmt,
    aliases: Mapping[str, TypeBoundSpec],
) -> tuple[SymbolicVar, tuple[SmtExpr, ...], bool] | None:
    if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
        return None
    fname = stmt.target.id
    if fname == "model_config" or fname.startswith("_"):
        return None
    spec = _resolve_py_type_spec(stmt.annotation, aliases)
    if spec is None:
        return None
    if isinstance(stmt.value, ast.Call) and dotted_expr_name(stmt.value.func).endswith("Field"):
        spec = _apply_field_call_kwargs(spec, stmt.value)
    exprs = build_bound_exprs(fname, spec)
    if not exprs:
        return None
    return SymbolicVar(name=fname, sort=spec.sort), exprs, spec.has_explicit_bound


def _extract_single_pydantic_schema(
    unit: SourceUnit,
    cls_node: ast.ClassDef,
    aliases: Mapping[str, TypeBoundSpec],
) -> ContractObligation | None:
    base_tails = {dotted_expr_name(b).rsplit(".", maxsplit=1)[-1] for b in cls_node.bases}
    if "BaseModel" not in base_tails:
        return None
    vars_out: list[SymbolicVar] = []
    bounds_out: list[SmtExpr] = []
    has_explicit = False
    for stmt in cls_node.body:
        extracted = _extract_single_model_field(stmt, aliases)
        if extracted is None:
            continue
        sym_var, exprs, is_exp = extracted
        vars_out.append(sym_var)
        bounds_out.extend(exprs)
        has_explicit = has_explicit or is_exp
    if not (has_explicit and bounds_out):
        return None
    return ContractObligation(
        obligation_id=f"{unit.file_path}::{cls_node.name}",
        file_path=unit.file_path,
        line=max(1, cls_node.lineno),
        language=SupportedLanguage.PYTHON,
        symbol=cls_node.name,
        variables=tuple(vars_out),
        preconditions=tuple(bounds_out),
        postconditions=(),
        body_stmts=None,
        opaque_reason="Schema domain non-vacuity verified; invariants checked via PBT.",
    )


@jezz_callable
def extract_python_unit_obligations(
    unit: GuardedSourceUnit,
) -> tuple[ContractObligation, ...]:
    """Extract schema and callable ``ContractObligation`` items from a Python ``SourceUnit``."""
    if unit.is_test_file:
        return ()
    aliases = _collect_module_type_aliases(unit.tree)
    obligations: list[ContractObligation] = []
    for cls_node in unit.classes:
        schema_ob = _extract_single_pydantic_schema(unit, cls_node, aliases)
        if schema_ob is not None:
            obligations.append(schema_ob)
    for ctx in unit.callables:
        if ctx.in_type_checking:
            continue
        ob = _build_py_callable_obligation(unit, ctx, aliases)
        if ob is not None:
            obligations.append(ob)
    return tuple(obligations)
