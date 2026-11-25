"""Polyglot CST (TypeScript, Rust, Go) and unified workspace SMT VCGen (Track 27)."""

import dataclasses
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Annotated, Final

import tree_sitter as ts
import tree_sitter_go
import tree_sitter_rust
import tree_sitter_typescript
from pydantic import Field

from jezz.codes.ast_utils import SourceUnit
from jezz.codes.polyglot_cst import iter_cst_nodes, node_text
from jezz.codes.polyglot_ir import (
    GuardedPolyglotUnit,
    InspectedUnit,
    PolyglotCallable,
    PolyglotUnit,
    SupportedLanguage,
    parse_cst_root,
)
from jezz.codes.smt.ir import (
    BoundedSolverTimeoutMs,
    ContractObligation,
    SmtAssert,
    SmtAssign,
    SmtAssume,
    SmtBinary,
    SmtBinaryOp,
    SmtBranch,
    SmtConstInt,
    SmtExpr,
    SmtIte,
    SmtProofReport,
    SmtReturn,
    SmtSort,
    SmtStmt,
    SmtUnary,
    SmtUnaryOp,
    SymbolicVar,
)
from jezz.codes.smt.solver import prove_obligations
from jezz.codes.smt.vcgen_py import (
    TypeBoundSpec,
    build_bound_exprs,
    extract_comment_directives,
    extract_python_unit_obligations,
    parse_contract_directive_expr,
)
from jezz.guards import IdentifierStr, LoopBudget, jezz_callable

_MAX_CST_DEPTH: Final[int] = 64
_MAX_STMT_BUDGET: Final[int] = 512

_TS_LANG: Final[ts.Language] = ts.Language(tree_sitter_typescript.language_typescript())
_TSX_LANG: Final[ts.Language] = ts.Language(tree_sitter_typescript.language_tsx())
_RS_LANG: Final[ts.Language] = ts.Language(tree_sitter_rust.language())
_GO_LANG: Final[ts.Language] = ts.Language(tree_sitter_go.language())

_POLY_TYPE_SPECS: Final[Mapping[str, TypeBoundSpec]] = MappingProxyType(
    {
        "number": TypeBoundSpec(sort=SmtSort.REAL),
        "bigint": TypeBoundSpec(sort=SmtSort.INT),
        "boolean": TypeBoundSpec(sort=SmtSort.BOOL),
        "bool": TypeBoundSpec(sort=SmtSort.BOOL),
        "string": TypeBoundSpec(sort=SmtSort.STRING),
        "String": TypeBoundSpec(sort=SmtSort.STRING),
        "&str": TypeBoundSpec(sort=SmtSort.STRING),
        "str": TypeBoundSpec(sort=SmtSort.STRING),
        "i8": TypeBoundSpec(sort=SmtSort.INT, ge=-128, le=127, has_explicit_bound=True),
        "i16": TypeBoundSpec(sort=SmtSort.INT, ge=-32_768, le=32_767, has_explicit_bound=True),
        "i32": TypeBoundSpec(
            sort=SmtSort.INT, ge=-2_147_483_648, le=2_147_483_647, has_explicit_bound=True
        ),
        "i64": TypeBoundSpec(sort=SmtSort.INT),
        "i128": TypeBoundSpec(sort=SmtSort.INT),
        "isize": TypeBoundSpec(sort=SmtSort.INT),
        "u8": TypeBoundSpec(sort=SmtSort.INT, ge=0, le=255, has_explicit_bound=True),
        "u16": TypeBoundSpec(sort=SmtSort.INT, ge=0, le=65_535, has_explicit_bound=True),
        "u32": TypeBoundSpec(sort=SmtSort.INT, ge=0, le=4_294_967_295, has_explicit_bound=True),
        "u64": TypeBoundSpec(sort=SmtSort.INT, ge=0, has_explicit_bound=True),
        "u128": TypeBoundSpec(sort=SmtSort.INT, ge=0, has_explicit_bound=True),
        "usize": TypeBoundSpec(sort=SmtSort.INT, ge=0, has_explicit_bound=True),
        "f32": TypeBoundSpec(sort=SmtSort.REAL),
        "f64": TypeBoundSpec(sort=SmtSort.REAL),
        "int": TypeBoundSpec(sort=SmtSort.INT),
        "int8": TypeBoundSpec(sort=SmtSort.INT, ge=-128, le=127, has_explicit_bound=True),
        "int16": TypeBoundSpec(sort=SmtSort.INT, ge=-32_768, le=32_767, has_explicit_bound=True),
        "int32": TypeBoundSpec(
            sort=SmtSort.INT, ge=-2_147_483_648, le=2_147_483_647, has_explicit_bound=True
        ),
        "int64": TypeBoundSpec(sort=SmtSort.INT),
        "uint": TypeBoundSpec(sort=SmtSort.INT, ge=0, has_explicit_bound=True),
        "uint8": TypeBoundSpec(sort=SmtSort.INT, ge=0, le=255, has_explicit_bound=True),
        "byte": TypeBoundSpec(sort=SmtSort.INT, ge=0, le=255, has_explicit_bound=True),
        "uint16": TypeBoundSpec(sort=SmtSort.INT, ge=0, le=65_535, has_explicit_bound=True),
        "uint32": TypeBoundSpec(sort=SmtSort.INT, ge=0, le=4_294_967_295, has_explicit_bound=True),
        "uint64": TypeBoundSpec(sort=SmtSort.INT, ge=0, has_explicit_bound=True),
        "float32": TypeBoundSpec(sort=SmtSort.REAL),
        "float64": TypeBoundSpec(sort=SmtSort.REAL),
    }
)

_CALLABLE_NODE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "function_declaration",
        "method_definition",
        "function_item",
        "method_declaration",
    }
)
_ASSIGN_CST_TYPES: Final[frozenset[str]] = frozenset(
    {
        "lexical_declaration",
        "variable_declaration",
        "let_declaration",
        "short_var_declaration",
        "var_declaration",
        "assignment_statement",
    }
)
_ZOD_SCHEMA_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:const|export\s+const)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*z\s*\.\s*object\(\{([\s\S]*?)}\)"
)
_ZOD_FIELD_RE: Final[re.Pattern[str]] = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*z\.(number|string)\(\)([^\n,;]*)"
)
_ZOD_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"\.(min|max|gt|lt|positive|nonnegative|int)\(\s*(-?[0-9][0-9_]*(?:\.[0-9_]+)?)?\s*\)"
)
_LEN_CALL_RE: Final[re.Pattern[str]] = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.(?:length|len\(\))")
_MATH_FN_RE: Final[re.Pattern[str]] = re.compile(r"\bMath\.(abs|min|max)\(")
_RS_WRAP_RE: Final[re.Pattern[str]] = re.compile(
    r"^Result<\s*([^,>]+)\s*,.*>$|^Option<\s*(.+)\s*>$"
)
_GO_TUPLE_RET_RE: Final[re.Pattern[str]] = re.compile(
    r"^\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s+)?([^,)]+),\s*error\s*\)$"
)
_GO_ERR_RETURN_RE: Final[re.Pattern[str]] = re.compile(r",\s*(?:err|Err)[A-Za-z0-9_]*\s*$")


@dataclass(frozen=True)
class _CstLowerCtx:
    language: SupportedLanguage
    loop: LoopBudget
    is_tail_expr: bool


def _select_ts_lang(unit: PolyglotUnit) -> ts.Language:
    if unit.language == SupportedLanguage.RUST:
        return _RS_LANG
    if unit.language == SupportedLanguage.GO:
        return _GO_LANG
    suffix = PurePosixPath(unit.file_path).suffix.lower()
    return _TSX_LANG if suffix == ".tsx" else _TS_LANG


def _resolve_poly_type_spec(raw_type: str, *, is_return: bool = False) -> TypeBoundSpec | None:
    cleaned = raw_type.strip().removeprefix(":").removeprefix("->").strip()
    if not cleaned:
        return None
    if is_return:
        rs_match = _RS_WRAP_RE.match(cleaned)
        if rs_match is not None:
            cleaned = (rs_match.group(1) or rs_match.group(2) or "").strip()
        go_match = _GO_TUPLE_RET_RE.match(cleaned)
        if go_match is not None:
            cleaned = go_match.group(1).strip()
    return _POLY_TYPE_SPECS.get(cleaned)


def _trunc_int_div(lhs: SmtExpr, rhs: SmtExpr) -> SmtExpr:
    zero = SmtConstInt(value=0)
    neg_lhs = SmtUnary(op=SmtUnaryOp.NEG, operand=lhs)
    neg_rhs = SmtUnary(op=SmtUnaryOp.NEG, operand=rhs)
    pos_rhs_q = SmtIte(
        cond=SmtBinary(op=SmtBinaryOp.GE, left=lhs, right=zero),
        then_expr=SmtBinary(op=SmtBinaryOp.FLOORDIV, left=lhs, right=rhs),
        else_expr=SmtUnary(
            op=SmtUnaryOp.NEG,
            operand=SmtBinary(op=SmtBinaryOp.FLOORDIV, left=neg_lhs, right=rhs),
        ),
    )
    neg_rhs_q = SmtIte(
        cond=SmtBinary(op=SmtBinaryOp.GE, left=lhs, right=zero),
        then_expr=SmtUnary(
            op=SmtUnaryOp.NEG,
            operand=SmtBinary(op=SmtBinaryOp.FLOORDIV, left=lhs, right=neg_rhs),
        ),
        else_expr=SmtBinary(op=SmtBinaryOp.FLOORDIV, left=neg_lhs, right=neg_rhs),
    )
    return SmtIte(
        cond=SmtBinary(op=SmtBinaryOp.GE, left=rhs, right=zero),
        then_expr=pos_rhs_q,
        else_expr=neg_rhs_q,
    )


def _is_int_expr(expr: SmtExpr, *, remaining_depth: int = 16) -> bool:
    if remaining_depth <= 0:
        return False
    if isinstance(expr, SmtConstInt):
        return True
    if isinstance(expr, SmtUnary) and expr.op in {SmtUnaryOp.NEG, SmtUnaryOp.ABS}:
        return _is_int_expr(expr.operand, remaining_depth=remaining_depth - 1)
    if isinstance(expr, SmtBinary) and expr.op in {
        SmtBinaryOp.ADD,
        SmtBinaryOp.SUB,
        SmtBinaryOp.MUL,
        SmtBinaryOp.FLOORDIV,
        SmtBinaryOp.MOD,
    }:
        return _is_int_expr(expr.left, remaining_depth=remaining_depth - 1) and _is_int_expr(
            expr.right, remaining_depth=remaining_depth - 1
        )
    return getattr(expr, "sort", None) == SmtSort.INT


def _rewrite_poly_div(
    expr: SmtExpr,
    language: SupportedLanguage,
    *,
    remaining_depth: int = _MAX_CST_DEPTH,
) -> SmtExpr:
    if remaining_depth <= 0 or language not in {SupportedLanguage.RUST, SupportedLanguage.GO}:
        return expr
    if isinstance(expr, SmtUnary):
        return SmtUnary(
            op=expr.op,
            operand=_rewrite_poly_div(expr.operand, language, remaining_depth=remaining_depth - 1),
        )
    if isinstance(expr, SmtIte):
        return SmtIte(
            cond=_rewrite_poly_div(expr.cond, language, remaining_depth=remaining_depth - 1),
            then_expr=_rewrite_poly_div(
                expr.then_expr, language, remaining_depth=remaining_depth - 1
            ),
            else_expr=_rewrite_poly_div(
                expr.else_expr, language, remaining_depth=remaining_depth - 1
            ),
        )
    if not isinstance(expr, SmtBinary):
        return expr
    lhs = _rewrite_poly_div(expr.left, language, remaining_depth=remaining_depth - 1)
    rhs = _rewrite_poly_div(expr.right, language, remaining_depth=remaining_depth - 1)
    if expr.op == SmtBinaryOp.TRUEDIV and _is_int_expr(lhs) and _is_int_expr(rhs):
        return _trunc_int_div(lhs, rhs)
    return SmtBinary(op=expr.op, left=lhs, right=rhs)


def _parse_poly_expr_text(
    raw_text: str,
    var_sorts: Mapping[str, SmtSort],
    language: SupportedLanguage,
) -> SmtExpr | None:
    step = _LEN_CALL_RE.sub(r"len(\1)", raw_text.strip().rstrip(";"))
    step = _MATH_FN_RE.sub(r"\1(", step)
    parsed = parse_contract_directive_expr(step[:4096], var_sorts)
    return _rewrite_poly_div(parsed, language) if parsed is not None else None


def _unwrap_return_expr_text(raw_ret: str, language: SupportedLanguage) -> str:
    cleaned = raw_ret.strip().removeprefix("return").strip().rstrip(";")
    if language == SupportedLanguage.RUST and cleaned.startswith("Ok(") and cleaned.endswith(")"):
        return cleaned[3:-1].strip()
    if language == SupportedLanguage.GO and "," in cleaned:
        parts = [p.strip() for p in cleaned.rsplit(",", maxsplit=1)]
        if len(parts) == 2 and parts[1] == "nil":
            return parts[0]
    return cleaned


def _unwrap_single_block_expr(block: ts.Node) -> ts.Node | None:
    if block.type == "else_clause" and block.named_children:
        block = block.named_children[0]
    if block.type != "block":
        return block
    named = [c for c in block.named_children if "comment" not in c.type]
    return named[0] if len(named) == 1 else None


def _lower_rs_if_value_expr(
    node: ts.Node,
    var_sorts: Mapping[str, SmtSort],
    language: SupportedLanguage,
    *,
    remaining_depth: int,
) -> SmtExpr | None:
    if remaining_depth <= 0:
        return None
    c_n = node.child_by_field_name("condition")
    t_b = node.child_by_field_name("consequence")
    e_b = node.child_by_field_name("alternative")
    if c_n is None or t_b is None or e_b is None:
        return None
    t_inner = _unwrap_single_block_expr(t_b)
    e_inner = _unwrap_single_block_expr(e_b)
    if t_inner is None or e_inner is None:
        return None
    c = _lower_cst_expr_node(c_n, var_sorts, language, remaining_depth=remaining_depth - 1)
    t = _lower_cst_expr_node(t_inner, var_sorts, language, remaining_depth=remaining_depth - 1)
    e = _lower_cst_expr_node(e_inner, var_sorts, language, remaining_depth=remaining_depth - 1)
    return SmtIte(cond=c, then_expr=t, else_expr=e) if (c and t and e) else None


def _lower_cst_expr_node(
    node: ts.Node,
    var_sorts: Mapping[str, SmtSort],
    language: SupportedLanguage,
    *,
    remaining_depth: int = _MAX_CST_DEPTH,
) -> SmtExpr | None:
    if remaining_depth <= 0:
        return None
    if node.type == "parenthesized_expression" and node.named_children:
        return _lower_cst_expr_node(
            node.named_children[0], var_sorts, language, remaining_depth=remaining_depth - 1
        )
    if node.type == "ternary_expression":
        c_n = node.child_by_field_name("condition")
        t_n = node.child_by_field_name("consequence")
        e_n = node.child_by_field_name("alternative")
        if c_n is None or t_n is None or e_n is None:
            return None
        c = _lower_cst_expr_node(c_n, var_sorts, language, remaining_depth=remaining_depth - 1)
        t = _lower_cst_expr_node(t_n, var_sorts, language, remaining_depth=remaining_depth - 1)
        e = _lower_cst_expr_node(e_n, var_sorts, language, remaining_depth=remaining_depth - 1)
        return SmtIte(cond=c, then_expr=t, else_expr=e) if (c and t and e) else None
    if node.type == "if_expression":
        return _lower_rs_if_value_expr(
            node, var_sorts, language, remaining_depth=remaining_depth - 1
        )
    return _parse_poly_expr_text(node_text(node), var_sorts, language)


def _block_statement_children(block: ts.Node) -> list[ts.Node]:
    children = [c for c in block.named_children if "comment" not in c.type]
    if len(children) == 1 and children[0].type == "statement_list":
        return [c for c in children[0].named_children if "comment" not in c.type]
    return children


def _is_abort_or_err_block(block: ts.Node) -> tuple[bool, bool]:
    stmts = _block_statement_children(block)
    if len(stmts) != 1:
        return False, False
    only = stmts[0]
    txt = node_text(only).strip().rstrip(";")
    if only.type == "throw_statement" or txt.startswith("throw "):
        return True, False
    if txt.startswith("panic(") or txt.startswith("panic!("):
        return False, True
    if only.type in {"return_statement", "return_expression"} or txt.startswith("return "):
        is_err = (
            "Err(" in txt
            or _GO_ERR_RETURN_RE.search(txt) is not None
            or "fmt.Errorf" in txt
            or "errors.New" in txt
        )
        return is_err, False
    return False, False


def _extract_assign_cst(
    stmt: ts.Node,
    local_sorts: dict[str, SmtSort],
    language: SupportedLanguage,
    *,
    remaining_depth: int,
) -> SmtStmt | None:
    txt = node_text(stmt).strip().rstrip(";")
    cleaned = re.sub(r"^(?:const|let\s+mut|let|var)\s+", "", txt)
    sep = ":=" if ":=" in cleaned else ("=" if "=" in cleaned else "")
    if not sep:
        return None
    lhs_raw, rhs_raw = cleaned.split(sep, maxsplit=1)
    lhs_name = lhs_raw.split(":", maxsplit=1)[0].strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", lhs_name):
        return None
    rhs_nodes = [
        c for c in stmt.named_children if c.type in {"if_expression", "ternary_expression"}
    ]
    val = (
        _lower_cst_expr_node(
            rhs_nodes[0], local_sorts, language, remaining_depth=remaining_depth - 1
        )
        if rhs_nodes
        else _parse_poly_expr_text(rhs_raw, local_sorts, language)
    )
    if val is None:
        return None
    local_sorts[lhs_name] = SmtSort.INT if _is_int_expr(val) else SmtSort.REAL
    return SmtAssign(target=lhs_name, value=val)


def _extract_assert_cst(
    stmt: ts.Node,
    local_sorts: Mapping[str, SmtSort],
    language: SupportedLanguage,
) -> SmtAssert | None:
    txt = node_text(stmt).strip().rstrip(";")
    match = re.match(
        r"^(?:debug_assert!|assert!|console\.assert|assert)\s*\((.+)\)$",
        txt,
        flags=re.DOTALL,
    )
    if match is None:
        return None
    arg_body = match.group(1).split(",", maxsplit=1)[0].strip()
    cond = _parse_poly_expr_text(arg_body, local_sorts, language)
    return SmtAssert(cond=cond) if cond is not None else None


def _lower_cst_else_branch(
    e_b: ts.Node | None,
    else_s: dict[str, SmtSort],
    ctx: _CstLowerCtx,
    *,
    remaining_depth: int,
) -> tuple[tuple[SmtStmt, ...] | None, int, str]:
    if e_b is None:
        return (), 0, ""
    inner = e_b.named_children[0] if (e_b.type == "else_clause" and e_b.named_children) else e_b
    if inner.type in {"if_statement", "if_expression"}:
        sub_br, sub_a, sub_err = _lower_cst_if(
            inner, else_s, ctx, remaining_depth=remaining_depth - 1
        )
        return ((sub_br,), sub_a, "") if sub_br is not None else (None, 0, sub_err)
    return _lower_cst_block(inner, else_s, ctx, remaining_depth=remaining_depth - 1)


def _lower_cst_if(
    stmt: ts.Node,
    local_sorts: dict[str, SmtSort],
    ctx: _CstLowerCtx,
    *,
    remaining_depth: int,
) -> tuple[SmtStmt | None, int, str]:
    if remaining_depth <= 0:
        return None, 0, "CST branch nesting exceeded symbolic depth budget."
    c_n = stmt.child_by_field_name("condition")
    t_b = stmt.child_by_field_name("consequence")
    e_b = stmt.child_by_field_name("alternative")
    if c_n is None or t_b is None:
        return None, 0, "Malformed conditional CST node."
    cond = _lower_cst_expr_node(c_n, local_sorts, ctx.language, remaining_depth=remaining_depth - 1)
    if cond is None:
        return None, 0, "Conditional expression uses unmodeled syntax."
    if e_b is None:
        is_err, is_panic = _is_abort_or_err_block(t_b)
        neg = SmtUnary(op=SmtUnaryOp.NOT, operand=cond)
        if is_err:
            return SmtAssume(cond=neg), 0, ""
        if is_panic:
            return SmtAssert(cond=neg), 1, ""
    then_s = dict(local_sorts)
    else_s = dict(local_sorts)
    t_stmts, t_asserts, t_err = _lower_cst_block(
        t_b, then_s, ctx, remaining_depth=remaining_depth - 1
    )
    if t_stmts is None:
        return None, 0, t_err
    e_stmts, e_asserts, e_err = _lower_cst_else_branch(
        e_b, else_s, ctx, remaining_depth=remaining_depth - 1
    )
    if e_stmts is None:
        return None, 0, e_err
    local_sorts.update(then_s)
    local_sorts.update(else_s)
    return SmtBranch(cond=cond, then_body=t_stmts, else_body=e_stmts), t_asserts + e_asserts, ""


def _lower_cst_return(
    stmt: ts.Node,
    local_sorts: Mapping[str, SmtSort],
    language: SupportedLanguage,
    *,
    remaining_depth: int,
) -> SmtStmt | None:
    if len(stmt.named_children) == 1 and language == SupportedLanguage.TYPESCRIPT:
        cst_val = _lower_cst_expr_node(
            stmt.named_children[0],
            local_sorts,
            language,
            remaining_depth=remaining_depth - 1,
        )
        if cst_val is not None:
            return SmtReturn(value=cst_val)
    unwrapped = _unwrap_return_expr_text(node_text(stmt), language)
    val = _parse_poly_expr_text(unwrapped, local_sorts, language)
    return SmtReturn(value=val) if val is not None else None


def _lower_cst_single_stmt(
    stmt: ts.Node,
    local_sorts: dict[str, SmtSort],
    ctx: _CstLowerCtx,
    *,
    is_last: bool,
    remaining_depth: int,
) -> tuple[SmtStmt | None, int, str]:
    assert_stmt = _extract_assert_cst(stmt, local_sorts, ctx.language)
    if assert_stmt is not None:
        return assert_stmt, 1, ""
    unwrapped_stmt = (
        stmt.named_children[0]
        if stmt.type == "expression_statement" and len(stmt.named_children) == 1
        else stmt
    )
    if unwrapped_stmt.type in {"if_statement", "if_expression"}:
        branch_ctx = dataclasses.replace(ctx, is_tail_expr=(ctx.is_tail_expr and is_last))
        return _lower_cst_if(
            unwrapped_stmt, local_sorts, branch_ctx, remaining_depth=remaining_depth - 1
        )
    if unwrapped_stmt.type in {"return_statement", "return_expression"}:
        ret_stmt = _lower_cst_return(
            unwrapped_stmt, local_sorts, ctx.language, remaining_depth=remaining_depth - 1
        )
        return (ret_stmt, 0, "") if ret_stmt is not None else (None, 0, "Opaque return value.")
    if stmt.type in _ASSIGN_CST_TYPES:
        asg = _extract_assign_cst(
            stmt, local_sorts, ctx.language, remaining_depth=remaining_depth - 1
        )
        return (asg, 0, "") if asg is not None else (None, 0, "Opaque variable binding.")
    raw = node_text(stmt).strip()
    if (
        ctx.is_tail_expr
        and is_last
        and ctx.language == SupportedLanguage.RUST
        and not raw.endswith(";")
    ):
        val = _parse_poly_expr_text(
            _unwrap_return_expr_text(raw, ctx.language), local_sorts, ctx.language
        )
        if val is not None:
            return SmtReturn(value=val), 0, ""
    return None, 0, f"CST statement '{stmt.type}' is outside decidable pure fragment."


def _lower_cst_block(
    block: ts.Node,
    local_sorts: dict[str, SmtSort],
    ctx: _CstLowerCtx,
    *,
    remaining_depth: int,
) -> tuple[tuple[SmtStmt, ...] | None, int, str]:
    if remaining_depth <= 0:
        return None, 0, "CST nesting exceeded symbolic depth budget."
    children = _block_statement_children(block)
    out: list[SmtStmt] = []
    total_asserts = 0
    for idx, child in enumerate(children):
        ctx.loop.tick()
        lowered, a_cnt, err = _lower_cst_single_stmt(
            child,
            local_sorts,
            ctx,
            is_last=(idx == len(children) - 1),
            remaining_depth=remaining_depth - 1,
        )
        if lowered is None:
            return None, 0, err
        out.append(lowered)
        total_asserts += a_cnt
    return tuple(out), total_asserts, ""


def _collect_leading_cst_guards(stmts: Sequence[SmtStmt]) -> list[SmtExpr]:
    guards: list[SmtExpr] = []
    for st in stmts:
        if not isinstance(st, SmtAssume):
            break
        guards.append(st.cond)
    return guards


def _index_callable_bodies(root: ts.Node) -> dict[int, ts.Node]:
    by_line: dict[int, ts.Node] = {}
    for node in iter_cst_nodes(root):
        if node.type not in _CALLABLE_NODE_TYPES:
            continue
        body = node.child_by_field_name("body")
        if body is not None:
            by_line[node.start_point.row + 1] = body
    return by_line


def _collect_poly_params(
    fn: PolyglotCallable,
) -> tuple[list[SymbolicVar], dict[str, SmtSort], list[SmtExpr], str]:
    variables: list[SymbolicVar] = []
    var_sorts: dict[str, SmtSort] = {}
    type_pres: list[SmtExpr] = []
    unmodeled = ""
    for p in fn.params:
        spec = _resolve_poly_type_spec(p.type_text)
        if spec is None:
            unmodeled = p.name
            continue
        variables.append(SymbolicVar(name=p.name, sort=spec.sort))
        var_sorts[p.name] = spec.sort
        type_pres.extend(build_bound_exprs(p.name, spec))
    return variables, var_sorts, type_pres, unmodeled


def _extract_poly_directives(
    unit: PolyglotUnit,
    fn: PolyglotCallable,
    var_sorts: Mapping[str, SmtSort],
) -> tuple[list[SmtExpr], list[SmtExpr], bool, bool, bool]:
    req_dirs, ens_dirs = extract_comment_directives(unit.comments, fn.start_line, fn.end_line)
    dir_pres: list[SmtExpr] = []
    dir_posts: list[SmtExpr] = []
    op_pre = False
    op_post = False
    for r_txt in req_dirs:
        pr = _parse_poly_expr_text(r_txt, var_sorts, unit.language)
        if pr is None:
            op_pre = True
        else:
            dir_pres.append(pr)
    for e_txt in ens_dirs:
        pe = _parse_poly_expr_text(e_txt, var_sorts, unit.language)
        if pe is None:
            op_post = True
        else:
            dir_posts.append(pe)
    return dir_pres, dir_posts, bool(req_dirs or ens_dirs), op_pre, op_post


def _select_poly_body_lowering(
    language: SupportedLanguage,
    body_node: ts.Node | None,
    param_sorts: Mapping[str, SmtSort],
    unmodeled: str,
    op_pre: bool,
) -> tuple[tuple[SmtStmt, ...] | None, int, str]:
    if unmodeled:
        return None, 0, f"Parameter '{unmodeled}' uses composite/non-scalar type."
    if op_pre:
        return None, 0, "Precondition directive uses opaque syntax; deferred to PBT."
    if body_node is None:
        return None, 0, "Callable CST body unavailable."
    ctx = _CstLowerCtx(
        language=language,
        loop=LoopBudget(max_steps=_MAX_STMT_BUDGET),
        is_tail_expr=(language == SupportedLanguage.RUST),
    )
    return _lower_cst_block(body_node, dict(param_sorts), ctx, remaining_depth=_MAX_CST_DEPTH)


def _build_poly_callable_obligation(
    unit: PolyglotUnit,
    fn: PolyglotCallable,
    body_node: ts.Node | None,
) -> ContractObligation | None:
    if fn.is_test_callable or fn.is_abstract_or_stub:
        return None
    variables, var_sorts, type_pres, unmodeled = _collect_poly_params(fn)
    param_only = dict(var_sorts)
    ret_spec = _resolve_poly_type_spec(fn.return_type_text, is_return=True)
    if ret_spec is not None:
        variables.append(SymbolicVar(name="result", sort=ret_spec.sort))
        var_sorts["result"] = ret_spec.sort

    dir_pres, dir_posts, has_dirs, op_pre, op_post = _extract_poly_directives(unit, fn, var_sorts)
    body_stmts, assert_cnt, opaque_reason = _select_poly_body_lowering(
        unit.language, body_node, param_only, unmodeled, op_pre
    )
    has_ret_bound = ret_spec is not None and ret_spec.has_explicit_bound
    if not (has_dirs or assert_cnt > 0 or has_ret_bound):
        return None

    ret_posts = list(build_bound_exprs("result", ret_spec)) if ret_spec is not None else []
    lead_guards = _collect_leading_cst_guards(body_stmts or ())
    return ContractObligation(
        obligation_id=f"{unit.file_path}::{fn.qualname}",
        file_path=unit.file_path,
        line=fn.start_line,
        language=unit.language,
        symbol=fn.qualname,
        variables=tuple(variables),
        preconditions=tuple([*type_pres, *lead_guards, *dir_pres]),
        postconditions=tuple([*ret_posts, *dir_posts]),
        assertion_count=assert_cnt,
        body_stmts=body_stmts,
        has_opaque_postcondition=op_post,
        opaque_reason=opaque_reason[:2048],
    )


def _apply_zod_numeric_op(spec: TypeBoundSpec, op: str, num: float) -> TypeBoundSpec:
    updates: dict[str, Callable[[TypeBoundSpec, float], TypeBoundSpec]] = {
        "min": lambda s, v: dataclasses.replace(s, ge=v, has_explicit_bound=True),
        "max": lambda s, v: dataclasses.replace(s, le=v, has_explicit_bound=True),
        "gt": lambda s, v: dataclasses.replace(s, gt=v, has_explicit_bound=True),
        "lt": lambda s, v: dataclasses.replace(s, lt=v, has_explicit_bound=True),
    }
    fn = updates.get(op)
    return fn(spec, num) if fn is not None else spec


def _apply_zod_modifier(
    spec: TypeBoundSpec,
    op: str,
    arg: str | None,
    *,
    is_str: bool,
) -> TypeBoundSpec:
    if not is_str and op in {"int", "positive", "nonnegative"}:
        no_arg_map: dict[str, TypeBoundSpec] = {
            "int": dataclasses.replace(spec, sort=SmtSort.INT),
            "positive": dataclasses.replace(spec, gt=0.0, has_explicit_bound=True),
            "nonnegative": dataclasses.replace(spec, ge=0.0, has_explicit_bound=True),
        }
        return no_arg_map[op]
    if arg is None:
        return spec
    num = float(arg.replace("_", ""))
    if is_str and op in {"min", "max"}:
        return (
            dataclasses.replace(spec, min_len=int(num), has_explicit_bound=True)
            if op == "min"
            else dataclasses.replace(spec, max_len=int(num), has_explicit_bound=True)
        )
    return _apply_zod_numeric_op(spec, op, num)


def _parse_zod_field_spec(kind: str, chain: str) -> TypeBoundSpec | None:
    is_str = kind == "string"
    cur = TypeBoundSpec(sort=SmtSort.STRING if is_str else SmtSort.REAL)
    for m in _ZOD_CALL_RE.finditer(chain):
        cur = _apply_zod_modifier(cur, m.group(1), m.group(2), is_str=is_str)
    return cur if cur.has_explicit_bound else None


def _extract_zod_schema_obligations(unit: PolyglotUnit) -> list[ContractObligation]:
    if unit.language != SupportedLanguage.TYPESCRIPT:
        return []
    out: list[ContractObligation] = []
    for match in _ZOD_SCHEMA_RE.finditer(unit.source):
        schema_name, body = match.group(1), match.group(2)
        line_no = unit.source[: match.start()].count("\n") + 1
        vars_out: list[SymbolicVar] = []
        bounds_out: list[SmtExpr] = []
        for f_match in _ZOD_FIELD_RE.finditer(body):
            fname, fkind, fchain = f_match.group(1), f_match.group(2), f_match.group(3)
            spec = _parse_zod_field_spec(fkind, fchain)
            if spec is None:
                continue
            vars_out.append(SymbolicVar(name=fname, sort=spec.sort))
            bounds_out.extend(build_bound_exprs(fname, spec))
        if bounds_out:
            out.append(
                ContractObligation(
                    obligation_id=f"{unit.file_path}::{schema_name}",
                    file_path=unit.file_path,
                    line=max(1, line_no),
                    language=SupportedLanguage.TYPESCRIPT,
                    symbol=schema_name,
                    variables=tuple(vars_out),
                    preconditions=tuple(bounds_out),
                    postconditions=(),
                    body_stmts=None,
                    opaque_reason="Zod schema domain non-vacuity verified.",
                )
            )
    return out


@jezz_callable
def extract_polyglot_unit_obligations(
    unit: GuardedPolyglotUnit,
) -> tuple[ContractObligation, ...]:
    """Extract schema and callable ``ContractObligation`` items from a TS, Rust, or Go unit."""
    if unit.is_test_file:
        return ()
    root = parse_cst_root(_select_ts_lang(unit), unit.language, unit.file_path, unit.source)
    bodies = _index_callable_bodies(root)
    obligations: list[ContractObligation] = _extract_zod_schema_obligations(unit)
    for fn in unit.callables:
        ob = _build_poly_callable_obligation(unit, fn, bodies.get(fn.start_line))
        if ob is not None:
            obligations.append(ob)
    return tuple(obligations)


@jezz_callable
def extract_unit_obligations(unit: InspectedUnit) -> tuple[ContractObligation, ...]:
    """Extract all decidable/partially-decidable ``ContractObligation`` items from ``unit``."""
    if isinstance(unit, SourceUnit):
        return extract_python_unit_obligations(unit)
    return extract_polyglot_unit_obligations(unit)


@jezz_callable
def extract_workspace_obligations(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
    *,
    symbol_filter: IdentifierStr | None = None,
) -> tuple[ContractObligation, ...]:
    """Extract sorted ``ContractObligation`` items across all production ``units``."""
    collected: list[ContractObligation] = []
    for unit in sorted(units, key=lambda item: item.file_path):
        for ob in extract_unit_obligations(unit):
            if symbol_filter is not None and symbol_filter not in {ob.symbol, ob.obligation_id}:
                continue
            collected.append(ob)
    collected.sort(key=lambda item: item.obligation_id)
    return tuple(collected)


@jezz_callable
def prove_workspace_contracts(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
    *,
    symbol_filter: IdentifierStr | None = None,
    timeout_ms: BoundedSolverTimeoutMs = 500,
    clock: Callable[[], float] = time.perf_counter,
) -> SmtProofReport:
    """Extract and prove all polyglot ``ContractObligation`` items across ``units`` via Z3."""
    obligations = extract_workspace_obligations(units, symbol_filter=symbol_filter)
    return prove_obligations(obligations, timeout_ms=timeout_ms, clock=clock)
