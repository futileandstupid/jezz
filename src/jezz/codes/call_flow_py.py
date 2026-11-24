"""Python AST per-callable flow-fact extractor for inter-procedural JBC analysis."""

import ast
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from jezz.codes.ast_utils import (
    CallableContext,
    GuardedSourceUnit,
    SourceUnit,
    is_abstract_or_overload,
)
from jezz.codes.call_flow_extract import (
    ArgFlowFact,
    CallSiteFlowFact,
    ExtractedCallableFlow,
    LocalFlowScope,
    MutableAliasState,
    sink_param_indices,
)
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.guards import jezz_callable

_PY_SQL_METHODS: Final[frozenset[str]] = frozenset(
    {"execute", "executemany", "executescript", "raw"}
)
_PY_SAFE_ANCHOR_TYPES: Final[frozenset[str]] = frozenset(
    {"Path", "PurePath", "PurePosixPath", "ContainedPath", "HttpUrl", "AnyHttpUrl"}
)
_PY_RAW_PATH_OR_URL_SINKS: Final[frozenset[str]] = frozenset(
    {
        "open",
        "os.remove",
        "os.unlink",
        "shutil.rmtree",
        "urllib.request.urlopen",
        "requests.get",
        "requests.post",
        "httpx.get",
        "httpx.post",
    }
)
_PY_PATH_METHOD_SINKS: Final[frozenset[str]] = frozenset(
    {"open", "read_text", "read_bytes", "write_text", "write_bytes", "unlink", "rmdir"}
)
_PY_PATH_CTORS: Final[frozenset[str]] = frozenset(
    {"Path", "pathlib.Path", "PosixPath", "pathlib.PosixPath", "WindowsPath", "os.path.join"}
)


def _fast_dotted_expr(expr: ast.expr, *, remaining_depth: int = 24) -> str:
    if remaining_depth <= 0:
        return ""
    target = expr.value if isinstance(expr, ast.Subscript) else expr
    if isinstance(target, ast.Call):
        target = target.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        prefix = _fast_dotted_expr(target.value, remaining_depth=remaining_depth - 1)
        return f"{prefix}.{target.attr}" if prefix else target.attr
    return ""


def _py_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def _is_py_dynamic_sql(expr: ast.expr) -> bool:
    if isinstance(expr, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in expr.values)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add | ast.Mod):
        has_str = any(
            isinstance(sub, ast.Constant) and isinstance(sub.value, str)
            for sub in (expr.left, expr.right)
        )
        return has_str or isinstance(expr.op, ast.Mod)
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "format"
    )


def _is_py_sanitizer(expr: ast.expr) -> bool:
    if any(
        isinstance(sub, ast.Attribute) and sub.attr == "resolved_path" for sub in ast.walk(expr)
    ):
        return True
    if not isinstance(expr, ast.Call):
        return False
    callee = _fast_dotted_expr(expr.func)
    return callee.endswith(("ContainedPath.from_parts", "HttpUrl", "AnyHttpUrl"))


def _is_py_path_wrap(expr: ast.expr) -> bool:
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return True
    if not isinstance(expr, ast.Call):
        return False
    callee = _fast_dotted_expr(expr.func)
    return callee in _PY_PATH_CTORS or callee.endswith((".joinpath", ".resolve"))


def _resolve_relative_py_module(current_module: str, level: int, mod: str | None) -> str:
    if level <= 0:
        return mod or ""
    parts = current_module.split(".") if current_module else []
    prefix = parts[: max(0, len(parts) - level)]
    if mod:
        prefix.extend(mod.split("."))
    return ".".join(prefix)


def _record_py_import_node(
    current_mod: str,
    node: ast.AST,
    aliases: dict[str, tuple[str, str]],
) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            local = alias.asname or alias.name.split(".", maxsplit=1)[0]
            aliases[local] = (alias.name, "")
    elif isinstance(node, ast.ImportFrom):
        base = _resolve_relative_py_module(current_mod, node.level, node.module)
        for alias in node.names:
            if alias.name != "*":
                aliases[alias.asname or alias.name] = (base, alias.name)


def _extract_py_import_aliases(unit: SourceUnit) -> Mapping[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    for node in unit.all_nodes:
        _record_py_import_node(unit.module_name, node, aliases)
    return MappingProxyType(aliases)


def _extract_py_params(ctx: CallableContext) -> tuple[tuple[str, ...], frozenset[int]]:
    raw_args = [*ctx.node.args.posonlyargs, *ctx.node.args.args, *ctx.node.args.kwonlyargs]
    names: list[str] = []
    untrusted: set[int] = set()
    for idx, arg in enumerate(raw_args):
        if idx == 0 and ctx.enclosing_class is not None and arg.arg in {"self", "cls"}:
            continue
        p_idx = len(names)
        names.append(arg.arg)
        if not (_py_names(arg.annotation) & _PY_SAFE_ANCHOR_TYPES):
            untrusted.add(p_idx)
    return tuple(names), frozenset(untrusted)


def _py_assign_target_and_value(node: ast.AST) -> tuple[str, ast.expr | None]:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id, node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return "", None


def _step_py_alias_pair(lhs: str, rhs: ast.expr, state: MutableAliasState) -> None:
    refs = _py_names(rhs)
    mapped = {idx for r in refs for idx in state.var_params.get(r, ())}
    if mapped:
        state.var_params.setdefault(lhs, set()).update(mapped)
    if _is_py_dynamic_sql(rhs) or bool(refs & state.sql_vars):
        state.sql_vars.add(lhs)
    if _is_py_sanitizer(rhs):
        return
    has_untrusted = bool(mapped & state.untrusted_indices) or bool(refs & state.uncontained_vars)
    if has_untrusted and (_is_py_path_wrap(rhs) or isinstance(rhs, ast.Name)):
        state.uncontained_vars.add(lhs)


def _build_py_flow_scope(
    ctx: CallableContext,
    local_nodes: Sequence[ast.AST],
    param_names: tuple[str, ...],
    untrusted_indices: frozenset[int],
    has_containment: bool,
) -> LocalFlowScope:
    state = MutableAliasState(
        var_params={name: {idx} for idx, name in enumerate(param_names)},
        sql_vars=set(),
        uncontained_vars=set(),
        untrusted_indices=untrusted_indices,
    )
    pairs = [
        (lhs, rhs)
        for n in local_nodes
        if (rhs := (pair := _py_assign_target_and_value(n))[1]) is not None and (lhs := pair[0])
    ]
    for _ in range(4):
        for lhs, rhs in pairs:
            _step_py_alias_pair(lhs, rhs, state)
    if has_containment:
        state.uncontained_vars.clear()
    entry_ids = frozenset(
        id(sub)
        for stmt in ctx.node.body
        if isinstance(stmt, ast.Expr | ast.Return | ast.Assign | ast.AnnAssign)
        for sub in ast.walk(stmt)
        if isinstance(sub, ast.Call)
    )
    frozen_vp = MappingProxyType({k: frozenset(v) for k, v in state.var_params.items()})
    return LocalFlowScope(
        var_params=frozen_vp,
        sql_vars=frozenset(state.sql_vars),
        untrusted_indices=untrusted_indices,
        uncontained_vars=frozenset(state.uncontained_vars),
        has_containment=has_containment,
        entry_ids=entry_ids,
    )


def _build_py_arg_fact(expr: ast.expr, scope: LocalFlowScope) -> ArgFlowFact:
    refs = _py_names(expr)
    ref_params = frozenset(idx for r in refs for idx in scope.var_params.get(r, ()))
    direct_idx: int | None = None
    if isinstance(expr, ast.Name) and len(scope.var_params.get(expr.id, ())) == 1:
        direct_idx = next(iter(scope.var_params[expr.id]))
    nested = tuple(
        c_name
        for sub in ast.walk(expr)
        if isinstance(sub, ast.Call) and (c_name := _fast_dotted_expr(sub.func))
    )
    is_tainted = bool(ref_params & scope.untrusted_indices) or bool(refs & scope.uncontained_vars)
    inline_uncontained = (
        not scope.has_containment
        and not _is_py_sanitizer(expr)
        and is_tainted
        and (_is_py_path_wrap(expr) or isinstance(expr, ast.Name))
    )
    return ArgFlowFact(
        referenced_params=ref_params,
        direct_param_index=direct_idx,
        referenced_vars=frozenset(refs),
        is_inline_dynamic_sql=_is_py_dynamic_sql(expr),
        is_inline_uncontained_path=inline_uncontained,
        nested_callees=nested[:16],
    )


def _build_py_call_site(call: ast.Call, scope: LocalFlowScope) -> CallSiteFlowFact:
    callee = _fast_dotted_expr(call.func) or "<call>"
    is_sql = isinstance(call.func, ast.Attribute) and call.func.attr in _PY_SQL_METHODS
    if (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in _PY_PATH_METHOD_SINKS
        and not (
            isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "resolved_path"
        )
    ):
        return CallSiteFlowFact(
            callee=f".{call.func.attr}",
            line=call.lineno,
            column=call.col_offset,
            args=(_build_py_arg_fact(call.func.value, scope),),
            is_sql_sink=False,
            sql_query_arg_index=0,
            is_path_sink=True,
            is_entry_level=(id(call) in scope.entry_ids),
        )
    arg_facts = tuple(_build_py_arg_fact(a, scope) for a in call.args[:16])
    return CallSiteFlowFact(
        callee=callee[:256],
        line=call.lineno,
        column=call.col_offset,
        args=arg_facts,
        is_sql_sink=is_sql,
        sql_query_arg_index=0,
        is_path_sink=(callee in _PY_RAW_PATH_OR_URL_SINKS),
        is_entry_level=(id(call) in scope.entry_ids),
    )


def _py_body_has_containment(local_nodes: Sequence[ast.AST]) -> bool:
    for sub in local_nodes:
        if isinstance(sub, ast.Attribute) and sub.attr == "resolved_path":
            return True
        if isinstance(sub, ast.Call) and _fast_dotted_expr(sub.func).endswith(
            ("is_relative_to", "ContainedPath.from_parts", "HttpUrl", "AnyHttpUrl")
        ):
            return True
    return False


def _py_direct_guarded_params(
    ctx: CallableContext,
    local_nodes: Sequence[ast.AST],
    scope: LocalFlowScope,
) -> frozenset[int]:
    guarded_names: set[str] = set()
    for dec in ctx.node.decorator_list:
        guarded_names |= _py_names(dec)
    for sub in local_nodes:
        if isinstance(sub, ast.If | ast.Assert):
            guarded_names |= _py_names(sub.test)
        elif isinstance(sub, ast.Match):
            guarded_names |= _py_names(sub.subject)
    return frozenset(idx for g in guarded_names for idx in scope.var_params.get(g, ()))


def _scan_py_returns(
    local_nodes: Sequence[ast.AST],
    scope: LocalFlowScope,
) -> tuple[bool, bool, tuple[str, ...]]:
    ret_dyn_sql = False
    ret_uncontained = False
    return_calls: list[str] = []
    for sub in local_nodes:
        if not isinstance(sub, ast.Return) or sub.value is None:
            continue
        refs = _py_names(sub.value)
        ret_params = {idx for r in refs for idx in scope.var_params.get(r, ())}
        if _is_py_dynamic_sql(sub.value) or bool(refs & scope.sql_vars):
            ret_dyn_sql = True
        if (
            not scope.has_containment
            and not _is_py_sanitizer(sub.value)
            and (bool(ret_params & scope.untrusted_indices) or bool(refs & scope.uncontained_vars))
        ):
            ret_uncontained = True
        if isinstance(sub.value, ast.Call) and (callee := _fast_dotted_expr(sub.value.func)):
            return_calls.append(callee)
    return ret_dyn_sql, ret_uncontained, tuple(return_calls)


def _extract_py_callable_flow(
    unit: SourceUnit,
    ctx: CallableContext,
    import_aliases: Mapping[str, tuple[str, str]],
) -> ExtractedCallableFlow:
    param_names, untrusted_indices = _extract_py_params(ctx)
    local_nodes = ctx.local_nodes
    has_containment = ctx.qualname == "ContainedPath.from_parts" or _py_body_has_containment(
        local_nodes
    )
    scope = _build_py_flow_scope(ctx, local_nodes, param_names, untrusted_indices, has_containment)
    direct_guarded = _py_direct_guarded_params(ctx, local_nodes, scope)
    call_sites = tuple(
        _build_py_call_site(n, scope) for n in local_nodes if isinstance(n, ast.Call)
    )
    call_assigns = tuple(
        (lhs, _build_py_call_site(rhs, scope))
        for n in local_nodes
        if (lhs := (pair := _py_assign_target_and_value(n))[0])
        and isinstance(rhs := pair[1], ast.Call)
    )
    ret_dyn_sql, ret_uncontained, return_calls = _scan_py_returns(local_nodes, scope)
    sql_sink_params, path_sink_params = sink_param_indices(
        call_sites, has_containment=has_containment, is_test_file=unit.is_test_file
    )
    return ExtractedCallableFlow(
        file_path=unit.file_path,
        module_name=unit.module_name,
        language=SupportedLanguage.PYTHON,
        name=ctx.node.name,
        qualname=ctx.qualname,
        enclosing_type=ctx.enclosing_class.name if ctx.enclosing_class is not None else "",
        is_test_file=unit.is_test_file,
        is_public=not ctx.node.name.startswith("_"),
        is_abstract_or_stub=is_abstract_or_overload(ctx),
        param_names=param_names,
        untrusted_param_indices=untrusted_indices,
        unbounded_param_names=frozenset(),
        erased_or_bag_param_names=frozenset(),
        all_params_typed=True,
        intra_guarded_param_names=frozenset(param_names[i] for i in direct_guarded),
        has_containment_guard=has_containment,
        direct_guarded_params=direct_guarded,
        direct_sql_sink_params=sql_sink_params,
        direct_path_sink_params=path_sink_params,
        direct_returns_dynamic_sql=ret_dyn_sql,
        direct_returns_uncontained_path=ret_uncontained,
        local_sql_vars=scope.sql_vars,
        local_uncontained_vars=scope.uncontained_vars,
        call_assigns=call_assigns,
        return_calls=return_calls,
        call_sites=call_sites,
        import_aliases=import_aliases,
    )


@jezz_callable
def extract_python_unit_flows(unit: GuardedSourceUnit) -> tuple[ExtractedCallableFlow, ...]:
    """Extract per-callable inter-procedural flow seeds from a Python ``SourceUnit``."""
    aliases = _extract_py_import_aliases(unit)
    return tuple(_extract_py_callable_flow(unit, ctx, aliases) for ctx in unit.callables)
