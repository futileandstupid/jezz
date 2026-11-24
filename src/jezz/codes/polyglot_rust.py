"""Rust (.rs) tree-sitter CST extractor for JBC v1.2 inspection."""

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final

import tree_sitter as ts
import tree_sitter_rust

from jezz.codes.ast_utils import RawDiscrepancy, SourceSpan
from jezz.codes.polyglot_cst import (
    CstComplexitySpec,
    CstConditionalSpec,
    check_cst_cog_004_conditionals,
    compute_cst_callable_metrics,
    extract_cst_comments,
    is_sensitive_identifier,
    iter_cst_nodes,
    iter_local_cst_nodes,
    node_text,
    scan_cst_string_hazards,
    scan_deferred_comments,
)
from jezz.codes.polyglot_ir import (
    PolyglotCallable,
    PolyglotImport,
    PolyglotParam,
    PolyglotTypeDecl,
    PolyglotUnit,
    SupportedLanguage,
    parse_cst_root,
)
from jezz.codes.polyglot_rust_hazards import (
    RS_RAW_PATH_SINKS,
    RS_SCOPE_BOUNDARIES,
    scan_rust_cst_hazards,
)
from jezz.guards import IdentifierStr, LoopBudget, SourceCodeText, jezz_callable

_RS_LANGUAGE: Final[ts.Language] = ts.Language(tree_sitter_rust.language())

_RS_SCOPE_BOUNDARIES: Final[frozenset[str]] = RS_SCOPE_BOUNDARIES

_RS_COMPLEXITY_SPEC: Final[CstComplexitySpec] = CstComplexitySpec(
    nesting_branch_types=frozenset(
        {
            "if_expression",
            "match_expression",
            "for_expression",
            "while_expression",
            "loop_expression",
        }
    ),
    else_clause_types=frozenset({"else_clause"}),
    if_types=frozenset({"if_expression"}),
    binary_expr_types=frozenset({"binary_expression"}),
    boolean_operators=frozenset({"&&", "||"}),
    statement_types=frozenset(
        {
            "let_declaration",
            "expression_statement",
            "return_expression",
            "if_expression",
            "match_expression",
            "for_expression",
            "while_expression",
            "loop_expression",
            "break_expression",
            "continue_expression",
        }
    ),
    scope_boundary_types=_RS_SCOPE_BOUNDARIES,
)

_RS_CONDITIONAL_SPEC: Final[CstConditionalSpec] = CstConditionalSpec(
    if_type="if_expression",
    consequence_field="consequence",
    alternative_field="alternative",
    terminal_jump_types=frozenset({"return_expression", "break_expression", "continue_expression"}),
)

_RS_ANY_RE: Final[re.Pattern[str]] = re.compile(
    r"\bdyn\s+(?:std::any::)?Any\b|\bstd::any::Any\b|\*(?:const|mut)\s+(?:std::ffi::)?c_void\b"
)
_RS_BAG_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:serde_json::)?Value\b|\b(?:HashMap|BTreeMap)<\s*String\s*,\s*(?:serde_json::)?Value\s*>"
)
_RS_UNBOUNDED_TYPE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:&?\s*str|String|Vec<.+>|&\[.+]|(?:HashMap|BTreeMap)<.+>|f32|f64)$"
)
_RS_FUEL_NAMES: Final[frozenset[str]] = frozenset(
    {"max_depth", "remaining_depth", "fuel", "depth_budget", "budget"}
)
_RS_GUARD_MACROS: Final[frozenset[str]] = frozenset(
    {
        "assert",
        "assert_eq",
        "assert_ne",
        "debug_assert",
        "debug_assert_eq",
        "debug_assert_ne",
        "ensure",
        "bail",
    }
)
_RS_GUARD_CALL_TAILS: Final[frozenset[str]] = frozenset(
    {
        "validate",
        "is_empty",
        "is_finite",
        "is_nan",
        "is_infinite",
        "checked_add",
        "checked_sub",
        "checked_mul",
        "checked_div",
        "checked_rem",
        "saturating_add",
        "saturating_sub",
        "clamp",
        "parse",
        "try_from",
        "from_str",
    }
)
_RS_KIND_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {"enum_item": "enum", "trait_item": "interface", "type_item": "type_alias"}
)
_RS_TEST_ASSERT_MACRO_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:assert\w*|debug_assert\w*|matches|panic|unreachable|eq\w*|check\w*|verify\w*)!"
)
_RS_TEST_HELPER_PREFIXES: Final[tuple[str, ...]] = (
    "assert",
    "check",
    "verify",
    "validate",
    "expect",
    "should",
    "test_",
)


def _derive_rs_module_name(file_path: str) -> str:
    posix = PurePosixPath(file_path)
    parts = list(posix.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    elif len(parts) >= 3 and parts[0] == "crates" and parts[2] == "src":
        parts = [parts[1].replace("-", "_"), *parts[3:]]
    if not parts:
        return ""
    parts[-1] = PurePosixPath(parts[-1]).stem
    if parts[-1] in {"lib", "main"} and len(parts) == 1:
        return "crate"
    if parts[-1] == "mod" and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(p for p in parts if p)[:256]


def _is_rs_test_path(file_path: str) -> bool:
    parts = set(PurePosixPath(file_path.lower()).parts[:-1])
    if parts & {"tests", "benches"}:
        return True
    return PurePosixPath(file_path.lower()).stem.endswith("_test")


def _preceding_attributes(item: ts.Node) -> tuple[str, ...]:
    attrs: list[str] = []
    curr = item.prev_named_sibling
    budget = LoopBudget(max_steps=64)
    while curr is not None:
        budget.tick()
        if curr.type != "attribute_item":
            break
        attrs.append(node_text(curr).strip())
        curr = curr.prev_named_sibling
    attrs.reverse()
    return tuple(attrs)


def _collect_cfg_test_node_ids(root: ts.Node) -> set[int]:
    test_ids: set[int] = set()
    for node in iter_cst_nodes(root):
        if node.type not in {"mod_item", "function_item"}:
            continue
        attrs = _preceding_attributes(node)
        is_test_item = any(
            "cfg(test)" in a or re.search(r"#\[(?:\w+::)*test\b", a) is not None for a in attrs
        )
        if is_test_item:
            for sub in iter_cst_nodes(node):
                test_ids.add(sub.id)
    return test_ids


def _enclosing_inline_mod_names(node: ts.Node) -> tuple[str, ...]:
    names: list[str] = []
    curr = node.parent
    budget = LoopBudget(max_steps=64)
    while curr is not None:
        budget.tick()
        name_node = curr.child_by_field_name("name") if curr.type == "mod_item" else None
        mod_name = node_text(name_node).strip() if name_node is not None else ""
        if mod_name:
            names.append(mod_name)
        curr = curr.parent
    names.reverse()
    return tuple(names)


def _effective_rs_module(current_mod: str, inline_mods: tuple[str, ...]) -> str:
    if not inline_mods:
        return current_mod
    prefix = () if current_mod in {"", "crate"} else tuple(current_mod.split("."))
    return ".".join((*prefix, *inline_mods))[:256]


def _drop_trailing_type_segment(segs: list[str]) -> list[str]:
    if len(segs) >= 2 and segs[-1][:1].isupper():
        return segs[:-1]
    return segs


def _resolve_rs_internal_module(current_mod: str, use_path: str) -> str:
    cleaned = use_path.split("::{")[0].strip()
    segs = [s for s in cleaned.split("::") if s and s != "*"]
    if not segs:
        return ""
    if segs[0] == "crate":
        return ".".join(_drop_trailing_type_segment(segs[1:]))[:256] or "crate"
    base = [] if current_mod in {"", "crate"} else current_mod.split(".")
    idx = 0
    budget = LoopBudget(max_steps=32)
    while idx < len(segs) and segs[idx] in {"self", "super"}:
        budget.tick()
        if segs[idx] == "super" and base:
            base = base[:-1]
        idx += 1
    tail = _drop_trailing_type_segment(segs[idx:])
    return ".".join([*base, *tail])[:256] or "crate"


def _extract_rs_private_use_names(use_txt: str) -> tuple[str, ...]:
    tokens = re.findall(r"::(_[A-Za-z0-9_]+)\b", use_txt)
    brace_tokens = re.findall(r"[{,\s](_[A-Za-z0-9_]+)\b", use_txt)
    privs = [t[:256] for t in (*tokens, *brace_tokens) if not t.startswith("__")]
    return tuple(dict.fromkeys(privs))[:64]


def _extract_rs_imports(
    current_mod: str,
    root: ts.Node,
    test_ids: set[int],
) -> list[PolyglotImport]:
    imports: list[PolyglotImport] = []
    for node in iter_cst_nodes(root):
        if node.type != "use_declaration" or node.id in test_ids:
            continue
        arg = node.child_by_field_name("argument")
        if arg is None:
            continue
        raw = re.sub(r"\s+", "", node_text(arg)).lstrip(":")
        if not raw:
            continue
        head = raw.split("::", maxsplit=1)[0].split("{", maxsplit=1)[0]
        is_internal = head in {"crate", "self", "super"}
        lex_mod = _effective_rs_module(current_mod, _enclosing_inline_mod_names(node))
        resolved = _resolve_rs_internal_module(lex_mod, raw) if is_internal else ""
        is_wc = any(sub.type == "use_wildcard" for sub in iter_cst_nodes(node))
        imports.append(
            PolyglotImport(
                raw_target=raw[:512],
                root_pkg="" if is_internal else head[:256],
                resolved_module=resolved,
                is_internal=is_internal,
                is_wildcard=is_wc,
                private_symbols=_extract_rs_private_use_names(raw),
                line=max(1, node.start_point.row + 1),
                column=max(0, node.start_point.column),
            )
        )
    return imports


def _rs_callee_name(call_node: ts.Node) -> str:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return ""
    if fn.type == "field_expression":
        val = fn.child_by_field_name("value")
        fld = fn.child_by_field_name("field")
        if val is not None and fld is not None:
            lhs = re.sub(r"\s+", "", node_text(val))
            return f"{lhs}.{node_text(fld).strip()}"[:256]
    return re.sub(r"\s+", "", node_text(fn))[:256]


def _identifiers_in_rs_node(node: ts.Node | None) -> set[str]:
    if node is None:
        return set()
    return {node_text(i) for i in iter_cst_nodes(node) if i.type == "identifier"}


def _is_non_vacuous_rs_guard_macro(sub: ts.Node) -> bool:
    m_node = sub.child_by_field_name("macro")
    m_name = node_text(m_node).strip() if m_node is not None else ""
    if m_name not in _RS_GUARD_MACROS:
        return False
    compact = re.sub(r"\s+", "", node_text(sub))
    return compact not in {f"{m_name}!(true)", f"{m_name}!(false)"}


def _record_rs_guard_node(
    sub: ts.Node,
    guarded: set[str],
    deps: dict[str, set[str]],
) -> None:
    if sub.type in {"if_expression", "match_expression"}:
        cond = sub.child_by_field_name("condition") or sub.child_by_field_name("value")
        guarded |= _identifiers_in_rs_node(cond)
        return
    if sub.type == "try_expression" or (
        sub.type == "macro_invocation" and _is_non_vacuous_rs_guard_macro(sub)
    ):
        guarded |= _identifiers_in_rs_node(sub)
        return
    if sub.type == "call_expression":
        tail = _rs_callee_name(sub).rsplit(".", maxsplit=1)[-1].rsplit("::", maxsplit=1)[-1]
        if tail in _RS_GUARD_CALL_TAILS:
            guarded |= _identifiers_in_rs_node(sub)
        return
    if sub.type in {"let_declaration", "assignment_expression"}:
        lhs_node = sub.child_by_field_name("pattern") or sub.child_by_field_name("left")
        rhs_node = sub.child_by_field_name("value") or sub.child_by_field_name("right")
        rhs_ids = _identifiers_in_rs_node(rhs_node)
        for lhs in _identifiers_in_rs_node(lhs_node):
            deps.setdefault(lhs, set()).update(rhs_ids)


def _collect_rs_guarded_identifiers(body_node: ts.Node | None) -> frozenset[str]:
    if body_node is None:
        return frozenset()
    guarded: set[str] = set()
    deps: dict[str, set[str]] = {}
    for sub in iter_local_cst_nodes(body_node, _RS_SCOPE_BOUNDARIES):
        _record_rs_guard_node(sub, guarded, deps)
    for _ in range(4):
        extra = {src for lhs in list(guarded) for src in deps.get(lhs, ()) if src not in guarded}
        if not extra:
            break
        guarded |= extra
    return frozenset(guarded)


def _extract_rs_param(
    param_node: ts.Node,
    guarded_idents: frozenset[str],
) -> PolyglotParam | None:
    pat = param_node.child_by_field_name("pattern")
    t_node = param_node.child_by_field_name("type")
    if pat is None or t_node is None:
        return None
    p_name = node_text(pat).replace("mut ", "").strip().lstrip("&")[:256] or "arg"
    t_txt = node_text(t_node).strip()
    is_guarded = p_name in guarded_idents
    unbounded = (not is_guarded) and _RS_UNBOUNDED_TYPE_RE.match(t_txt) is not None
    return PolyglotParam(
        name=p_name,
        has_type=True,
        type_text=t_txt[:256],
        is_unbounded_primitive=unbounded,
        is_untyped_bag=_RS_BAG_RE.search(t_txt) is not None,
        has_any_erasure=_RS_ANY_RE.search(t_txt) is not None,
        line=max(1, param_node.start_point.row + 1),
        column=max(0, param_node.start_point.column),
    )


def _record_rs_macro_fact(
    sub: ts.Node,
    guarded_vars: set[str],
    vacuous: list[str],
) -> None:
    m_node = sub.child_by_field_name("macro")
    m_name = node_text(m_node).strip() if m_node is not None else ""
    if m_name not in {"assert", "debug_assert"}:
        return
    full = re.sub(r"\s+", "", node_text(sub))
    if full in {f"{m_name}!(true)", f"{m_name}!(false)"}:
        vacuous.append(f"Macro '{m_name}!' uses a constant boolean literal")
        return
    guarded_vars |= _identifiers_in_rs_node(sub)


def _record_rs_call_fact(
    sub: ts.Node,
    calls: dict[str, None],
    guarded_vars: set[str],
) -> bool:
    callee = _rs_callee_name(sub)
    if callee:
        calls[callee] = None
    if callee.endswith((".checked_div", ".checked_rem")):
        guarded_vars |= _identifiers_in_rs_node(sub.child_by_field_name("arguments"))
    return callee in RS_RAW_PATH_SINKS


def _visit_rs_local_node(
    sub: ts.Node,
    calls: dict[str, None],
    fields: set[str],
    guarded_vars: set[str],
    vacuous: list[str],
) -> tuple[bool, bool]:
    if sub.type == "self":
        return True, False
    if sub.type in {"if_expression", "match_expression"}:
        cond = sub.child_by_field_name("condition") or sub.child_by_field_name("value")
        guarded_vars |= _identifiers_in_rs_node(cond)
        return False, False
    if sub.type == "field_expression":
        val = sub.child_by_field_name("value")
        fld = sub.child_by_field_name("field")
        if val is not None and node_text(val).strip() == "self" and fld is not None:
            fields.add(node_text(fld).strip()[:256])
            return True, False
        return False, False
    if sub.type == "call_expression":
        return False, _record_rs_call_fact(sub, calls, guarded_vars)
    if sub.type == "macro_invocation":
        _record_rs_macro_fact(sub, guarded_vars, vacuous)
    return False, False


def _collect_rs_body_facts(
    body_node: ts.Node | None,
    param_names: frozenset[str],
) -> tuple[tuple[str, ...], frozenset[str], bool, bool, tuple[str, ...], tuple[str, ...]]:
    if body_node is None:
        return (), frozenset(), False, False, (), ()
    calls: dict[str, None] = {}
    fields: set[str] = set()
    guarded_vars: set[str] = set()
    vacuous: list[str] = []
    performs_io = False
    uses_self = False
    for sub in iter_local_cst_nodes(body_node, _RS_SCOPE_BOUNDARIES):
        s_self, s_io = _visit_rs_local_node(sub, calls, fields, guarded_vars, vacuous)
        uses_self = uses_self or s_self
        performs_io = performs_io or s_io
    divisors = _find_rs_unguarded_divisors(body_node, param_names, guarded_vars)
    return tuple(calls)[:256], frozenset(fields), performs_io, uses_self, divisors, tuple(vacuous)


def _find_rs_unguarded_divisors(
    body_node: ts.Node,
    param_names: frozenset[str],
    guarded_vars: set[str],
) -> tuple[str, ...]:
    bad: list[str] = []
    for sub in iter_local_cst_nodes(body_node, _RS_SCOPE_BOUNDARIES):
        if sub.type != "binary_expression":
            continue
        op = sub.child_by_field_name("operator")
        right = sub.child_by_field_name("right")
        if op is None or right is None or node_text(op).strip() not in {"/", "%"}:
            continue
        r_name = node_text(right).strip()
        if r_name in param_names and r_name not in guarded_vars and r_name not in bad:
            bad.append(r_name[:256])
    return tuple(bad[:32])


def _rs_call_delegates_assertion(callee: str) -> bool:
    tail = callee.rsplit(".", maxsplit=1)[-1].rsplit("::", maxsplit=1)[-1].lstrip("_")
    return tail == "test" or tail.startswith(_RS_TEST_HELPER_PREFIXES)


def _rs_body_has_test_assertion(body_node: ts.Node | None, calls: tuple[str, ...]) -> bool:
    if body_node is None:
        return False
    txt = node_text(body_node)
    if ".unwrap_err(" in txt or _RS_TEST_ASSERT_MACRO_RE.search(txt) is not None:
        return True
    return any(_rs_call_delegates_assertion(c) for c in calls)


def _extract_rs_params_and_self(
    params_node: ts.Node | None,
    body_node: ts.Node | None,
) -> tuple[bool, frozenset[str], tuple[PolyglotParam, ...]]:
    if params_node is None:
        return False, frozenset(), ()
    has_self = any(c.type == "self_parameter" for c in params_node.named_children)
    raw_params = [c for c in params_node.named_children if c.type == "parameter"]
    guarded_idents = _collect_rs_guarded_identifiers(body_node) if raw_params else frozenset()
    params = tuple(
        p for raw in raw_params if (p := _extract_rs_param(raw, guarded_idents)) is not None
    )
    return has_self, guarded_idents, params


def _has_rs_boundary_contract(
    params: tuple[PolyglotParam, ...],
    guarded_idents: frozenset[str],
) -> bool:
    for param in params:
        if param.is_unbounded_primitive:
            return False
        if (param.is_untyped_bag or param.has_any_erasure) and param.name not in guarded_idents:
            return False
    return True


def _build_rs_callable(
    fn_node: ts.Node,
    enclosing_type: str,
    in_trait: bool,
    is_test_fn: bool,
    has_should_panic: bool = False,
) -> PolyglotCallable:
    name_node = fn_node.child_by_field_name("name")
    fn_name = node_text(name_node).strip() if name_node is not None else "fn"
    qualname = f"{enclosing_type}.{fn_name}" if enclosing_type else fn_name
    is_pub = (
        any(c.type == "visibility_modifier" for c in fn_node.named_children) and fn_name != "main"
    )
    body_node = fn_node.child_by_field_name("body")
    has_self, guarded_idents, params = _extract_rs_params_and_self(
        fn_node.child_by_field_name("parameters"), body_node
    )
    param_names = frozenset(p.name for p in params)
    ret_node = fn_node.child_by_field_name("return_type")
    ret_txt = node_text(ret_node).strip() if ret_node is not None else "()"

    calls, fields, performs_io, uses_self, divisors, vacuous = _collect_rs_body_facts(
        body_node, param_names
    )
    is_rec = fn_name in calls or f"self.{fn_name}" in calls or f"Self::{fn_name}" in calls
    cc, depth, stmts = (
        compute_cst_callable_metrics(body_node, _RS_COMPLEXITY_SPEC, is_recursive=is_rec)
        if body_node is not None
        else (0, 0, 0)
    )
    arg_sig = ", ".join(f"{p.name}: {p.type_text}" for p in params)
    has_assert = (
        has_should_panic or _rs_body_has_test_assertion(body_node, calls) if is_test_fn else True
    )
    return PolyglotCallable(
        name=fn_name[:256],
        qualname=qualname[:256],
        enclosing_type=enclosing_type[:256],
        is_public=is_pub and not is_test_fn,
        is_method=has_self,
        is_receiver_used=uses_self or not has_self,
        is_abstract_or_stub=in_trait or body_node is None,
        is_test_callable=is_test_fn,
        params=params[:64],
        has_return_type=True,
        return_type_text=ret_txt[:256],
        return_has_any_erasure=_RS_ANY_RE.search(ret_txt) is not None,
        return_is_untyped_bag=_RS_BAG_RE.search(ret_txt) is not None,
        has_boundary_contract=_has_rs_boundary_contract(params, guarded_idents),
        cognitive_complexity=cc,
        max_nesting_depth=depth,
        statement_count=stmts,
        start_line=max(1, fn_node.start_point.row + 1),
        end_line=max(1, fn_node.end_point.row + 1),
        column=max(0, fn_node.start_point.column),
        signature_summary=f"fn {qualname}({arg_sig}) -> {ret_txt}"[:512],
        calls=calls,
        accessed_fields=fields,
        performs_io=performs_io,
        is_recursive_unguarded=is_rec and not bool(param_names & _RS_FUEL_NAMES),
        unguarded_divisors=divisors,
        vacuous_contracts=vacuous if not is_test_fn else (),
        has_test_assertion=has_assert,
    )


def _inspect_rs_struct_field(
    field_node: ts.Node,
    derives_deserialize: bool,
    derives_debug: bool,
) -> tuple[str, bool, bool, bool]:
    f_name_node = field_node.child_by_field_name("name")
    f_type_node = field_node.child_by_field_name("type")
    f_name = node_text(f_name_node).strip()[:256] if f_name_node is not None else ""
    f_type = node_text(f_type_node).strip() if f_type_node is not None else ""
    f_attrs = " ".join(_preceding_attributes(field_node))
    has_serde_guard = any(
        k in f_attrs for k in ("deserialize_with", "try_from", "validate", "skip")
    )
    is_unb = (
        derives_deserialize
        and not has_serde_guard
        and _RS_UNBOUNDED_TYPE_RE.match(f_type) is not None
    )
    is_sens = (
        bool(f_name)
        and is_sensitive_identifier(f_name)
        and derives_debug
        and "Secret" not in f_type
        and "Redacted" not in f_type
        and "skip" not in f_attrs
    )
    return f_name, _RS_ANY_RE.search(f_type) is not None, is_unb, is_sens


def _extract_rs_struct(struct_node: ts.Node) -> PolyglotTypeDecl:
    name_node = struct_node.child_by_field_name("name")
    s_name = node_text(name_node).strip()[:256] if name_node is not None else "Struct"
    is_pub = any(c.type == "visibility_modifier" for c in struct_node.named_children)
    attrs = " ".join(_preceding_attributes(struct_node))
    derives_deser = bool(re.search(r"derive\([^)]*\bDeserialize\b", attrs))
    derives_debug = bool(re.search(r"derive\([^)]*\bDebug\b", attrs))
    deny_unknown = "deny_unknown_fields" in attrs

    body = struct_node.child_by_field_name("body")
    fields: list[str] = []
    unbounded: list[str] = []
    sensitive: list[str] = []
    any_field = False
    for f_decl in body.named_children if body is not None else ():
        if f_decl.type != "field_declaration":
            continue
        f_name, has_any, is_unb, is_sens = _inspect_rs_struct_field(
            f_decl, derives_deser, derives_debug
        )
        if f_name:
            fields.append(f_name)
        any_field = any_field or has_any
        if is_unb and f_name:
            unbounded.append(f_name)
        if is_sens:
            sensitive.append(f_name)

    return PolyglotTypeDecl(
        name=s_name,
        kind="struct",
        is_public=is_pub,
        is_schema_or_value=derives_deser,
        is_strict_schema=not derives_deser or deny_unknown,
        fields=tuple(fields[:256]),
        sensitive_unredacted_fields=tuple(sensitive[:64]),
        unbounded_fields=tuple(unbounded[:128]),
        has_any_erasure_field=any_field,
        line=max(1, struct_node.start_point.row + 1),
        end_line=max(1, struct_node.end_point.row + 1),
        column=max(0, struct_node.start_point.column),
        signature_summary=f"struct {s_name} {{{', '.join(fields[:8])}}}"[:512],
    )


def _enclosing_rs_impl_or_trait(fn_node: ts.Node) -> tuple[str, bool]:
    parent = fn_node.parent
    if parent is None or parent.type != "declaration_list" or parent.parent is None:
        return "", False
    owner = parent.parent
    if owner.type == "impl_item":
        is_trait_impl = owner.child_by_field_name("trait") is not None
        t_node = owner.child_by_field_name("type")
        t_name = node_text(t_node).split("<", maxsplit=1)[0].strip() if t_node is not None else ""
        return ("" if is_trait_impl else t_name), is_trait_impl
    if owner.type == "trait_item":
        n_node = owner.child_by_field_name("name")
        return (node_text(n_node).strip() if n_node is not None else ""), True
    return "", False


def _has_should_panic_attr(attrs: tuple[str, ...]) -> bool:
    return any(re.search(r"#\[should_panic\b", a) is not None for a in attrs)


def _extract_one_rs_fn_node(
    node: ts.Node,
    unit_is_test: bool,
    test_ids: set[int],
) -> tuple[PolyglotCallable, list[RawDiscrepancy]]:
    attrs = _preceding_attributes(node)
    is_test_attr = any(re.search(r"#\[(?:\w+::)*test\b", a) is not None for a in attrs)
    is_ignored = any(re.search(r"#\[ignore\b", a) is not None for a in attrs)
    enclosing, in_trait = _enclosing_rs_impl_or_trait(node)
    in_test_ctx = unit_is_test or is_test_attr or (node.id in test_ids)
    fn_obj = _build_rs_callable(
        node, enclosing, in_trait, is_test_attr, _has_should_panic_attr(attrs)
    )
    hazards: list[RawDiscrepancy] = []
    if is_ignored:
        hazards.append(
            RawDiscrepancy(
                code_id="JBC-VRF-004",
                location=SourceSpan(
                    file_path="<pending>",
                    line=fn_obj.start_line,
                    column=fn_obj.column,
                    symbol=fn_obj.qualname,
                ),
                message=f"Ignored Rust test '{fn_obj.qualname}' bypasses dynamic verification.",
                remediation="Remove #[ignore] or register a time-bounded CodeVariance.",
            )
        )
    if is_test_attr and not unit_is_test and not fn_obj.has_test_assertion:
        hazards.append(
            RawDiscrepancy(
                code_id="JBC-VRF-001",
                location=SourceSpan(
                    file_path="<pending>",
                    line=fn_obj.start_line,
                    column=fn_obj.column,
                    symbol=fn_obj.qualname,
                ),
                message=f"Test '{fn_obj.qualname}' contains zero assertions or failure checks.",
                remediation="Assert concrete behavioral postconditions via assert!/assert_eq!.",
            )
        )
    if in_test_ctx and not is_test_attr:
        fn_obj = fn_obj.model_copy(update={"is_public": False})
    return fn_obj, hazards


def _extract_rs_nominal_item(node: ts.Node) -> PolyglotTypeDecl | None:
    if node.type == "struct_item":
        return _extract_rs_struct(node)
    kind = _RS_KIND_MAP.get(node.type)
    if kind is None:
        return None
    n_node = node.child_by_field_name("name")
    if n_node is None:
        return None
    t_name = node_text(n_node).strip()[:256]
    return PolyglotTypeDecl(
        name=t_name,
        kind=kind,
        is_public=any(c.type == "visibility_modifier" for c in node.named_children),
        has_any_erasure_field=_RS_ANY_RE.search(node_text(node)) is not None,
        line=max(1, node.start_point.row + 1),
        end_line=max(1, node.end_point.row + 1),
        column=max(0, node.start_point.column),
        signature_summary=f"{kind} {t_name}",
    )


def _extract_rs_types_and_callables(
    root: ts.Node,
    unit_is_test: bool,
    test_ids: set[int],
) -> tuple[list[PolyglotTypeDecl], list[PolyglotCallable], list[RawDiscrepancy]]:
    types: list[PolyglotTypeDecl] = []
    callables: list[PolyglotCallable] = []
    hazards: list[RawDiscrepancy] = []

    for node in iter_cst_nodes(root):
        if node.type in {"function_item", "function_signature_item"}:
            fn_item, fn_hazards = _extract_one_rs_fn_node(node, unit_is_test, test_ids)
            callables.append(fn_item)
            hazards.extend(fn_hazards)
            continue
        if node.id in test_ids:
            continue
        decl = _extract_rs_nominal_item(node)
        if decl is not None:
            types.append(decl)
    return types, callables, hazards


@jezz_callable
def parse_rust_unit(
    file_path: IdentifierStr,
    source: SourceCodeText,
) -> PolyglotUnit:
    """Parse a Rust ``.rs`` module into a normalized ``PolyglotUnit``."""
    root = parse_cst_root(_RS_LANGUAGE, SupportedLanguage.RUST, file_path, source)
    is_test = _is_rs_test_path(file_path)
    mod_name = _derive_rs_module_name(file_path)
    test_ids = _collect_cfg_test_node_ids(root)
    comments = extract_cst_comments(root, frozenset({"line_comment", "block_comment"}))
    imports = _extract_rs_imports(mod_name, root, test_ids)
    types, callables, fn_hazards = _extract_rs_types_and_callables(root, is_test, test_ids)

    patched_fn_hazards = [
        h.model_copy(update={"location": h.location.model_copy(update={"file_path": file_path})})
        for h in fn_hazards
    ]
    hazards: list[RawDiscrepancy] = [
        *patched_fn_hazards,
        *scan_cst_string_hazards(
            file_path,
            root,
            frozenset({"string_literal", "raw_string_literal"}),
            skip_node_ids=frozenset(test_ids) if not is_test else frozenset(),
        ),
        *scan_deferred_comments(file_path, comments),
        *check_cst_cog_004_conditionals(file_path, root, _RS_CONDITIONAL_SPEC),
        *scan_rust_cst_hazards(file_path, root, frozenset(test_ids), unit_is_test=is_test),
    ]
    has_calls = any(n.type in {"call_expression", "macro_invocation"} for n in iter_cst_nodes(root))
    has_div = any(
        n.type == "binary_expression"
        and (op := n.child_by_field_name("operator")) is not None
        and node_text(op).strip() in {"/", "%"}
        for n in iter_cst_nodes(root)
    )
    return PolyglotUnit(
        language=SupportedLanguage.RUST,
        file_path=file_path,
        module_name=mod_name,
        source=source,
        comments=comments,
        is_test_file=is_test,
        callables=tuple(callables),
        types=tuple(types),
        imports=tuple(imports),
        hazards=tuple(hazards),
        has_external_calls=has_calls,
        has_arithmetic_div=has_div,
    )
