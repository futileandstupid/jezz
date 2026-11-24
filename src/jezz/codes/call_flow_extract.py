"""CST flow-fact dataclasses and polyglot (TS, Rust, Go) per-callable flow extraction."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final

import tree_sitter as ts
import tree_sitter_go
import tree_sitter_rust
import tree_sitter_typescript

from jezz.codes.polyglot_cst import iter_cst_nodes, iter_local_cst_nodes, node_text
from jezz.codes.polyglot_go_hazards import GO_RAW_FS_CALLS, GO_SCOPE_BOUNDARIES
from jezz.codes.polyglot_ir import (
    GuardedPolyglotUnit,
    PolyglotCallable,
    PolyglotImport,
    PolyglotUnit,
    SupportedLanguage,
    parse_cst_root,
)
from jezz.codes.polyglot_rust_hazards import RS_RAW_PATH_SINKS, RS_SCOPE_BOUNDARIES
from jezz.codes.polyglot_ts_hazards import TS_RAW_FS_SINKS, TS_SCOPE_BOUNDARIES
from jezz.guards import InstanceGuard, LoopBudget, jezz_callable

_TS_LANG: Final[ts.Language] = ts.Language(tree_sitter_typescript.language_typescript())
_TSX_LANG: Final[ts.Language] = ts.Language(tree_sitter_typescript.language_tsx())
_RS_LANG: Final[ts.Language] = ts.Language(tree_sitter_rust.language())
_GO_LANG: Final[ts.Language] = ts.Language(tree_sitter_go.language())

_TS_SQL_METHODS: Final[frozenset[str]] = frozenset({"query", "execute", "raw", "exec"})
_RS_SQL_METHODS: Final[frozenset[str]] = frozenset({"execute", "query"})
_GO_SQL_METHODS: Final[frozenset[str]] = frozenset({"Query", "QueryContext", "Exec", "ExecContext"})


@dataclass(frozen=True)
class ArgFlowFact:
    """Flow summary for one positional call argument inside a caller body."""

    referenced_params: frozenset[int]
    direct_param_index: int | None
    referenced_vars: frozenset[str]
    is_inline_dynamic_sql: bool
    is_inline_uncontained_path: bool
    nested_callees: tuple[str, ...]


@dataclass(frozen=True)
class CallSiteFlowFact:
    """Normalized call-expression descriptor extracted from AST or tree-sitter CST."""

    callee: str
    line: int
    column: int
    args: tuple[ArgFlowFact, ...]
    is_sql_sink: bool
    sql_query_arg_index: int
    is_path_sink: bool
    is_entry_level: bool


@dataclass(frozen=True)
class ExtractedCallableFlow:
    """Intra-callable flow seed used for whole-workspace fixed-point propagation."""

    file_path: str
    module_name: str
    language: SupportedLanguage
    name: str
    qualname: str
    enclosing_type: str
    is_test_file: bool
    is_public: bool
    is_abstract_or_stub: bool
    param_names: tuple[str, ...]
    untrusted_param_indices: frozenset[int]
    unbounded_param_names: frozenset[str]
    erased_or_bag_param_names: frozenset[str]
    all_params_typed: bool
    intra_guarded_param_names: frozenset[str]
    has_containment_guard: bool
    direct_guarded_params: frozenset[int]
    direct_sql_sink_params: frozenset[int]
    direct_path_sink_params: frozenset[int]
    direct_returns_dynamic_sql: bool
    direct_returns_uncontained_path: bool
    local_sql_vars: frozenset[str]
    local_uncontained_vars: frozenset[str]
    call_assigns: tuple[tuple[str, CallSiteFlowFact], ...]
    return_calls: tuple[str, ...]
    call_sites: tuple[CallSiteFlowFact, ...]
    import_aliases: Mapping[str, tuple[str, str]]


@dataclass(frozen=True)
class LocalFlowScope:
    """Cohesive local alias & taint context passed into call-site extractors."""

    var_params: Mapping[str, frozenset[int]]
    sql_vars: frozenset[str]
    untrusted_indices: frozenset[int]
    uncontained_vars: frozenset[str]
    has_containment: bool
    entry_ids: frozenset[int]


@dataclass
class MutableAliasState:
    """Mutable working accumulator for fixed-point local alias resolution."""

    var_params: dict[str, set[int]]
    sql_vars: set[str]
    uncontained_vars: set[str]
    untrusted_indices: frozenset[int]


type GuardedCallSiteSeq = Annotated[Sequence[CallSiteFlowFact], InstanceGuard(Sequence)]


@jezz_callable
def sink_param_indices(
    call_sites: GuardedCallSiteSeq,
    *,
    has_containment: bool,
    is_test_file: bool,
) -> tuple[frozenset[int], frozenset[int]]:
    """Collect parameter indices reaching direct SQL or uncontained filesystem/URL sinks."""
    sql_params = frozenset(
        p_idx
        for cs in call_sites
        if cs.is_sql_sink and len(cs.args) > cs.sql_query_arg_index
        for p_idx in cs.args[cs.sql_query_arg_index].referenced_params
    )
    path_params = (
        frozenset()
        if has_containment or is_test_file
        else frozenset(
            p_idx
            for cs in call_sites
            if cs.is_path_sink and cs.args
            for p_idx in cs.args[0].referenced_params
        )
    )
    return sql_params, path_params


def _cst_language_for_unit(unit: PolyglotUnit) -> ts.Language:
    if unit.language == SupportedLanguage.TYPESCRIPT:
        return _TSX_LANG if unit.file_path.lower().endswith(".tsx") else _TS_LANG
    if unit.language == SupportedLanguage.RUST:
        return _RS_LANG
    return _GO_LANG


def _scope_boundaries_for_lang(lang: SupportedLanguage) -> frozenset[str]:
    table: dict[SupportedLanguage, frozenset[str]] = {
        SupportedLanguage.TYPESCRIPT: TS_SCOPE_BOUNDARIES,
        SupportedLanguage.RUST: RS_SCOPE_BOUNDARIES,
        SupportedLanguage.GO: GO_SCOPE_BOUNDARIES,
    }
    return table[lang]


def _cst_idents(node: ts.Node | None) -> set[str]:
    if node is None:
        return set()
    return {node_text(sub) for sub in iter_cst_nodes(node) if sub.type == "identifier"}


def _cst_callee_name(lang: SupportedLanguage, call_node: ts.Node) -> str:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return ""
    if lang == SupportedLanguage.RUST and fn.type == "field_expression":
        val = fn.child_by_field_name("value")
        fld = fn.child_by_field_name("field")
        if val is not None and fld is not None:
            return f"{re.sub(r'\\s+', '', node_text(val))}.{node_text(fld).strip()}"[:256]
    return re.sub(r"\s+", "", node_text(fn))[:256]


def _is_go_dynamic_sql_subtree(node: ts.Node) -> bool:
    lit_types = {"interpreted_string_literal", "raw_string_literal"}
    for sub in iter_cst_nodes(node):
        if (
            sub.type == "call_expression"
            and _cst_callee_name(SupportedLanguage.GO, sub) == "fmt.Sprintf"
        ):
            return True
        if (
            sub.type == "binary_expression"
            and "+" in node_text(sub)
            and any(c.type in lit_types for c in sub.named_children)
        ):
            return True
    return False


def _is_cst_dynamic_sql(lang: SupportedLanguage, node: ts.Node) -> bool:
    txt = node_text(node)
    if lang == SupportedLanguage.TYPESCRIPT:
        has_str_lit = any(c.type in {"string", "template_string"} for c in node.named_children)
        return (node.type == "template_string" and "${" in txt) or (
            node.type == "binary_expression" and "+" in txt and has_str_lit
        )
    if lang == SupportedLanguage.RUST:
        return "format!(" in txt or (
            node.type == "macro_invocation" and txt.lstrip().startswith("format!")
        )
    return _is_go_dynamic_sql_subtree(node)


def _cst_has_containment(lang: SupportedLanguage, body: ts.Node | None) -> bool:
    if body is None:
        return False
    txt = node_text(body)
    markers: dict[SupportedLanguage, tuple[str, ...]] = {
        SupportedLanguage.TYPESCRIPT: (".startsWith(", "path.relative(", "new URL("),
        SupportedLanguage.RUST: (".starts_with(", ".canonicalize("),
        SupportedLanguage.GO: ("os.OpenRoot(", "strings.HasPrefix(", "filepath.Rel(", "url.Parse("),
    }
    return any(tok in txt for tok in markers[lang])


def _cst_is_sql_sink(lang: SupportedLanguage, callee: str) -> tuple[bool, int]:
    if "." not in callee:
        return False, 0
    tail = callee.rsplit(".", maxsplit=1)[-1]
    if lang == SupportedLanguage.TYPESCRIPT:
        return tail in _TS_SQL_METHODS, 0
    if lang == SupportedLanguage.RUST:
        return tail in _RS_SQL_METHODS, 0
    return tail in _GO_SQL_METHODS, (1 if tail in {"QueryContext", "ExecContext"} else 0)


def _cst_is_path_sink(lang: SupportedLanguage, callee: str) -> bool:
    if lang == SupportedLanguage.TYPESCRIPT:
        return callee in TS_RAW_FS_SINKS or callee == "fetch"
    if lang == SupportedLanguage.RUST:
        return callee in RS_RAW_PATH_SINKS
    return callee in GO_RAW_FS_CALLS


def _cst_assign_pair(node: ts.Node) -> tuple[set[str], ts.Node | None]:
    if node.type in {"variable_declarator", "var_spec"}:
        return _cst_idents(node.child_by_field_name("name")), node.child_by_field_name("value")
    if node.type == "let_declaration":
        return _cst_idents(node.child_by_field_name("pattern")), node.child_by_field_name("value")
    if node.type in {"assignment_expression", "short_var_declaration", "assignment_statement"}:
        return _cst_idents(node.child_by_field_name("left")), node.child_by_field_name("right")
    return set(), None


def _cst_direct_guard_idents(lang: SupportedLanguage, body: ts.Node | None) -> set[str]:
    if body is None:
        return set()
    guarded: set[str] = set()
    cond_types = {
        "if_statement",
        "if_expression",
        "switch_statement",
        "match_expression",
        "expression_switch_statement",
        "ternary_expression",
    }
    for sub in iter_local_cst_nodes(body, _scope_boundaries_for_lang(lang)):
        if sub.type in cond_types:
            cond = sub.child_by_field_name("condition") or sub.child_by_field_name("value")
            guarded |= _cst_idents(cond)
        elif lang == SupportedLanguage.RUST and sub.type == "macro_invocation":
            txt = re.sub(r"\s+", "", node_text(sub))
            if txt.startswith(("assert!(", "debug_assert!(", "ensure!(")) and txt not in {
                "assert!(true)",
                "assert!(false)",
            }:
                guarded |= _cst_idents(sub)
    return guarded


def _collect_cst_return_exprs(lang: SupportedLanguage, body: ts.Node | None) -> list[ts.Node]:
    if body is None:
        return []
    if lang == SupportedLanguage.TYPESCRIPT and body.type != "statement_block":
        return [body]
    exprs = [
        child
        for sub in iter_local_cst_nodes(body, _scope_boundaries_for_lang(lang))
        if sub.type in {"return_statement", "return_expression"}
        for child in sub.named_children
    ]
    if lang == SupportedLanguage.RUST and body.named_children:
        tail = body.named_children[-1]
        if tail.type not in {"expression_statement", "let_declaration", "return_expression"}:
            exprs.append(tail)
    return exprs


def _cst_entry_call_ids(body: ts.Node | None) -> frozenset[int]:
    if body is None:
        return frozenset()
    if body.type not in {"statement_block", "block"}:
        return frozenset(sub.id for sub in iter_cst_nodes(body) if sub.type == "call_expression")
    top_stmts = (
        body.named_children[0].named_children
        if len(body.named_children) == 1 and body.named_children[0].type == "statement_list"
        else body.named_children
    )
    allowed = {
        "expression_statement",
        "return_statement",
        "return_expression",
        "lexical_declaration",
        "variable_declaration",
        "let_declaration",
        "short_var_declaration",
        "call_expression",
    }
    return frozenset(
        sub.id
        for child in top_stmts
        if child.type in allowed
        for sub in iter_cst_nodes(child)
        if sub.type == "call_expression"
    )


def _build_cst_arg_fact(
    lang: SupportedLanguage,
    arg_node: ts.Node,
    scope: LocalFlowScope,
) -> ArgFlowFact:
    refs = _cst_idents(arg_node)
    ref_params = frozenset(idx for r in refs for idx in scope.var_params.get(r, ()))
    direct_idx: int | None = None
    direct_types = {"identifier", "reference_expression", "unary_expression"}
    if len(refs) == 1 and arg_node.type in direct_types:
        mapped = scope.var_params.get(next(iter(refs)), frozenset())
        if len(mapped) == 1:
            direct_idx = next(iter(mapped))
    nested = tuple(
        c_name
        for sub in iter_cst_nodes(arg_node)
        if sub.type == "call_expression" and (c_name := _cst_callee_name(lang, sub))
    )
    inline_uncontained = not scope.has_containment and (
        bool(ref_params & scope.untrusted_indices) or bool(refs & scope.uncontained_vars)
    )
    return ArgFlowFact(
        referenced_params=ref_params,
        direct_param_index=direct_idx,
        referenced_vars=frozenset(refs),
        is_inline_dynamic_sql=_is_cst_dynamic_sql(lang, arg_node),
        is_inline_uncontained_path=inline_uncontained,
        nested_callees=nested[:16],
    )


def _build_cst_call_site(
    lang: SupportedLanguage,
    call_node: ts.Node,
    scope: LocalFlowScope,
) -> CallSiteFlowFact:
    callee = _cst_callee_name(lang, call_node) or "<call>"
    args_node = call_node.child_by_field_name("arguments")
    raw_args = list(args_node.named_children[:16]) if args_node is not None else []
    arg_facts = tuple(_build_cst_arg_fact(lang, a, scope) for a in raw_args)
    is_sql, sql_idx = _cst_is_sql_sink(lang, callee)
    return CallSiteFlowFact(
        callee=callee,
        line=max(1, call_node.start_point.row + 1),
        column=max(0, call_node.start_point.column),
        args=arg_facts,
        is_sql_sink=is_sql,
        sql_query_arg_index=sql_idx,
        is_path_sink=_cst_is_path_sink(lang, callee),
        is_entry_level=(call_node.id in scope.entry_ids),
    )


def _ts_specifier_pair(spec: ts.Node) -> tuple[str, str]:
    orig = spec.child_by_field_name("name")
    al = spec.child_by_field_name("alias")
    orig_txt = node_text(orig).strip() if orig is not None else ""
    local = node_text(al).strip() if al is not None else orig_txt
    return local, orig_txt


def _record_ts_stmt_aliases(
    imp_stmt: ts.Node,
    resolved_mod: str,
    aliases: dict[str, tuple[str, str]],
) -> None:
    for spec in iter_cst_nodes(imp_stmt):
        if spec.type == "import_specifier":
            local, orig_txt = _ts_specifier_pair(spec)
            if local and orig_txt:
                aliases[local] = (resolved_mod, orig_txt)
        elif spec.type == "namespace_import" and (ids := list(_cst_idents(spec))):
            aliases[ids[-1]] = (resolved_mod, "")


def _extract_ts_import_aliases(
    unit: PolyglotUnit,
    root: ts.Node,
) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    stmts = [n for n in root.named_children if n.type in {"import_statement", "export_statement"}]
    for imp_stmt, imp_ir in zip(stmts, unit.imports, strict=False):
        if imp_ir.resolved_module:
            _record_ts_stmt_aliases(imp_stmt, imp_ir.resolved_module, aliases)
    return aliases


def _rs_symbols_from_raw_use(raw_target: str) -> tuple[str, ...]:
    if "{" not in raw_target:
        tail = raw_target.rsplit("::", maxsplit=1)[-1]
        return (tail,) if tail and tail != "*" else ()
    inside = raw_target.split("{", maxsplit=1)[1].rstrip("}")
    return tuple(
        sym for part in inside.split(",") if (sym := part.strip().split("::")[-1]) and sym != "*"
    )


def _extract_rs_import_aliases(imports: Sequence[PolyglotImport]) -> dict[str, tuple[str, str]]:
    return {
        sym: (imp_ir.resolved_module, sym)
        for imp_ir in imports
        if imp_ir.is_internal and imp_ir.resolved_module
        for sym in _rs_symbols_from_raw_use(imp_ir.raw_target)
    }


def _extract_go_import_aliases(imports: Sequence[PolyglotImport]) -> dict[str, tuple[str, str]]:
    return {
        pkg_tail: (imp_ir.raw_target, "")
        for imp_ir in imports
        if not imp_ir.is_internal and (pkg_tail := imp_ir.raw_target.rsplit("/", maxsplit=1)[-1])
    }


def _extract_poly_import_aliases(
    unit: PolyglotUnit,
    root: ts.Node,
) -> Mapping[str, tuple[str, str]]:
    if unit.language == SupportedLanguage.TYPESCRIPT:
        return MappingProxyType(_extract_ts_import_aliases(unit, root))
    if unit.language == SupportedLanguage.RUST:
        return MappingProxyType(_extract_rs_import_aliases(unit.imports))
    return MappingProxyType(_extract_go_import_aliases(unit.imports))


def _index_cst_callable_bodies(
    lang: SupportedLanguage,
    root: ts.Node,
) -> Mapping[tuple[int, int], ts.Node]:
    non_fn = {
        "class_declaration",
        "abstract_class_declaration",
        "struct_item",
        "impl_item",
        "trait_item",
        "mod_item",
    }
    fn_types = _scope_boundaries_for_lang(lang) - non_fn
    return MappingProxyType(
        {
            (max(1, n.start_point.row + 1), max(0, n.start_point.column)): body
            for n in iter_cst_nodes(root)
            if n.type in fn_types and (body := n.child_by_field_name("body")) is not None
        }
    )


def _step_cst_alias_pair(
    lang: SupportedLanguage,
    lhs_set: set[str],
    rhs: ts.Node,
    state: MutableAliasState,
) -> None:
    refs = _cst_idents(rhs)
    mapped = {idx for r in refs for idx in state.var_params.get(r, ())}
    is_sql = _is_cst_dynamic_sql(lang, rhs) or bool(refs & state.sql_vars)
    is_uncontained = bool(mapped & state.untrusted_indices) or bool(refs & state.uncontained_vars)
    for lhs in lhs_set:
        if mapped:
            state.var_params.setdefault(lhs, set()).update(mapped)
        if is_sql:
            state.sql_vars.add(lhs)
        if is_uncontained:
            state.uncontained_vars.add(lhs)


def _build_cst_flow_scope(
    lang: SupportedLanguage,
    body: ts.Node | None,
    param_names: tuple[str, ...],
    untrusted_indices: frozenset[int],
    has_containment: bool,
) -> LocalFlowScope:
    state = MutableAliasState(
        var_params={name: {idx} for idx, name in enumerate(param_names) if name != "_"},
        sql_vars=set(),
        uncontained_vars=set(),
        untrusted_indices=untrusted_indices,
    )
    if body is not None:
        pairs = [
            (lhs_set, rhs)
            for sub in iter_local_cst_nodes(body, _scope_boundaries_for_lang(lang))
            if (rhs := (pair := _cst_assign_pair(sub))[1]) is not None and (lhs_set := pair[0])
        ]
        budget = LoopBudget(max_steps=max(16, len(pairs) * 4 + 4))
        for _ in range(4):
            for lhs_set, rhs in pairs:
                budget.tick()
                _step_cst_alias_pair(lang, lhs_set, rhs, state)
    if has_containment:
        state.uncontained_vars.clear()
    frozen_vp = MappingProxyType({k: frozenset(v) for k, v in state.var_params.items()})
    return LocalFlowScope(
        var_params=frozen_vp,
        sql_vars=frozenset(state.sql_vars),
        untrusted_indices=untrusted_indices,
        uncontained_vars=frozenset(state.uncontained_vars),
        has_containment=has_containment,
        entry_ids=_cst_entry_call_ids(body),
    )


def _scan_cst_returns(
    lang: SupportedLanguage,
    body: ts.Node | None,
    scope: LocalFlowScope,
) -> tuple[bool, bool, tuple[str, ...]]:
    ret_dyn_sql = False
    ret_uncontained = False
    return_calls: list[str] = []
    for ret_expr in _collect_cst_return_exprs(lang, body):
        refs = _cst_idents(ret_expr)
        ret_params = {idx for r in refs for idx in scope.var_params.get(r, ())}
        if _is_cst_dynamic_sql(lang, ret_expr) or bool(refs & scope.sql_vars):
            ret_dyn_sql = True
        if not scope.has_containment and (
            bool(ret_params & scope.untrusted_indices) or bool(refs & scope.uncontained_vars)
        ):
            ret_uncontained = True
        for sub in iter_cst_nodes(ret_expr):
            if sub.type == "call_expression" and (callee := _cst_callee_name(lang, sub)):
                return_calls.append(callee)
    return ret_dyn_sql, ret_uncontained, tuple(return_calls)


def _extract_cst_call_assigns(
    lang: SupportedLanguage,
    local_nodes: Sequence[ts.Node],
    scope: LocalFlowScope,
) -> tuple[tuple[str, CallSiteFlowFact], ...]:
    out: list[tuple[str, CallSiteFlowFact]] = []
    for sub in local_nodes:
        lhs_set, rhs = _cst_assign_pair(sub)
        if not lhs_set or rhs is None:
            continue
        rhs_calls = [n for n in iter_cst_nodes(rhs) if n.type == "call_expression"]
        if rhs_calls:
            cs = _build_cst_call_site(lang, rhs_calls[0], scope)
            out.extend((lhs, cs) for lhs in lhs_set)
    return tuple(out)


def _extract_poly_callable_flow(
    unit: PolyglotUnit,
    fn: PolyglotCallable,
    body: ts.Node | None,
    import_aliases: Mapping[str, tuple[str, str]],
) -> ExtractedCallableFlow:
    lang = unit.language
    param_names = tuple(p.name for p in fn.params)
    untrusted_indices = frozenset(
        idx
        for idx, p in enumerate(fn.params)
        if p.name != "_" and any(tok in p.type_text.lower() for tok in ("str", "string", "path"))
    )
    has_containment = _cst_has_containment(lang, body)
    scope = _build_cst_flow_scope(lang, body, param_names, untrusted_indices, has_containment)
    direct_guarded = frozenset(
        idx for g in _cst_direct_guard_idents(lang, body) for idx in scope.var_params.get(g, ())
    )
    local_nodes = list(iter_local_cst_nodes(body, _scope_boundaries_for_lang(lang))) if body else []
    call_sites = tuple(
        _build_cst_call_site(lang, sub, scope)
        for sub in local_nodes
        if sub.type == "call_expression"
    )
    call_assigns = _extract_cst_call_assigns(lang, local_nodes, scope)
    ret_dyn_sql, ret_uncontained, return_calls = _scan_cst_returns(lang, body, scope)
    sql_sink_params, path_sink_params = sink_param_indices(
        call_sites, has_containment=has_containment, is_test_file=unit.is_test_file
    )
    intra_guarded = frozenset(
        p.name
        for idx, p in enumerate(fn.params)
        if idx in direct_guarded or (not p.is_unbounded_primitive and p.name != "_")
    )
    return ExtractedCallableFlow(
        file_path=unit.file_path,
        module_name=unit.module_name,
        language=lang,
        name=fn.name,
        qualname=fn.qualname,
        enclosing_type=fn.enclosing_type,
        is_test_file=unit.is_test_file,
        is_public=fn.is_public,
        is_abstract_or_stub=fn.is_abstract_or_stub,
        param_names=param_names,
        untrusted_param_indices=untrusted_indices,
        unbounded_param_names=frozenset(p.name for p in fn.params if p.is_unbounded_primitive),
        erased_or_bag_param_names=frozenset(
            p.name for p in fn.params if p.is_untyped_bag or p.has_any_erasure
        ),
        all_params_typed=all(p.has_type for p in fn.params),
        intra_guarded_param_names=intra_guarded,
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
def extract_polyglot_unit_flows(unit: GuardedPolyglotUnit) -> tuple[ExtractedCallableFlow, ...]:
    """Extract per-callable inter-procedural flow seeds from a TypeScript, Rust, or Go unit."""
    if not unit.callables:
        return ()
    root = parse_cst_root(_cst_language_for_unit(unit), unit.language, unit.file_path, unit.source)
    bodies = _index_cst_callable_bodies(unit.language, root)
    aliases = _extract_poly_import_aliases(unit, root)
    return tuple(
        _extract_poly_callable_flow(unit, fn, bodies.get((fn.start_line, fn.column)), aliases)
        for fn in unit.callables
    )
