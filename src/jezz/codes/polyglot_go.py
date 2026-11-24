"""Go (.go) tree-sitter CST extractor for JBC v1.2 inspection."""

import re
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final

import tree_sitter as ts
import tree_sitter_go

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
    node_span,
    node_text,
    scan_cst_string_hazards,
    scan_deferred_comments,
    scan_suppression_comments,
    unquote_cst_string,
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
from jezz.codes.polyglot_manifests import GO_STDLIB_ROOTS
from jezz.guards import IdentifierStr, LoopBudget, SourceCodeText, jezz_callable

_GO_LANGUAGE: Final[ts.Language] = ts.Language(tree_sitter_go.language())

_GO_SCOPE_BOUNDARIES: Final[frozenset[str]] = frozenset(
    {"function_declaration", "method_declaration", "func_literal"}
)

_GO_COMPLEXITY_SPEC: Final[CstComplexitySpec] = CstComplexitySpec(
    nesting_branch_types=frozenset(
        {
            "if_statement",
            "expression_switch_statement",
            "type_switch_statement",
            "select_statement",
            "for_statement",
        }
    ),
    else_clause_types=frozenset(),
    if_types=frozenset({"if_statement"}),
    binary_expr_types=frozenset({"binary_expression"}),
    boolean_operators=frozenset({"&&", "||"}),
    statement_types=frozenset(
        {
            "short_var_declaration",
            "assignment_statement",
            "var_declaration",
            "return_statement",
            "expression_statement",
            "if_statement",
            "for_statement",
            "expression_switch_statement",
            "type_switch_statement",
            "select_statement",
            "go_statement",
            "defer_statement",
            "send_statement",
            "inc_statement",
            "dec_statement",
            "break_statement",
            "continue_statement",
        }
    ),
    scope_boundary_types=_GO_SCOPE_BOUNDARIES,
)

_GO_CONDITIONAL_SPEC: Final[CstConditionalSpec] = CstConditionalSpec(
    if_type="if_statement",
    consequence_field="consequence",
    alternative_field="alternative",
    terminal_jump_types=frozenset({"return_statement", "break_statement", "continue_statement"}),
)

_GO_SUPPRESSION_RE: Final[str] = r"//\s*(?:nolint\b|lint:ignore\b)"
_GO_ANY_RE: Final[re.Pattern[str]] = re.compile(r"\bany\b|\binterface\s*\{\s*}")
_GO_BAG_RE: Final[re.Pattern[str]] = re.compile(
    r"\bmap\s*\[\s*string\s*]\s*(?:any|interface\s*\{\s*})"
)
_GO_UNBOUNDED_TYPE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:string|\[]\s*byte|\[]\S+|map\[.+].+|float32|float64)$"
)
_GO_FUEL_NAMES: Final[frozenset[str]] = frozenset(
    {"maxDepth", "remainingDepth", "fuel", "depthBudget", "budget"}
)
_GO_GUARD_CALL_TAILS: Final[frozenset[str]] = frozenset(
    {"Validate", "Decode", "Parse", "Check", "Verify", "IsNaN", "IsInf"}
)
_GO_RAW_FS_CALLS: Final[frozenset[str]] = frozenset(
    {"os.Open", "os.ReadFile", "os.WriteFile", "os.Remove", "os.RemoveAll", "os.Create"}
)
_GO_CLOSE_REQUIRED_CALLS: Final[frozenset[str]] = frozenset({"os.Open", "os.Create", "sql.Open"})
_GO_UNBOUNDED_READ_CALLS: Final[frozenset[str]] = frozenset(
    {"io.ReadAll", "ioutil.ReadAll", "os.ReadFile"}
)

type _GoNodeScanner = Callable[[str, ts.Node, bool], list[RawDiscrepancy]]


def _derive_go_module_name(file_path: str, pkg_name: str) -> str:
    parent = PurePosixPath(file_path).parent.as_posix()
    if parent and parent != ".":
        return parent[:256]
    return (pkg_name or "main")[:256]


def _extract_go_package_name(root: ts.Node) -> str:
    for child in root.named_children:
        if child.type != "package_clause":
            continue
        for sub in child.named_children:
            if sub.type == "package_identifier":
                return node_text(sub).strip()[:256]
    return ""


def _check_go_internal_breach(curr_dir: str, import_spec: str) -> tuple[str, ...]:
    if "/internal/" not in import_spec and not import_spec.endswith("/internal"):
        return ()
    idx = import_spec.find("/internal")
    owner_prefix = import_spec[:idx].rsplit("/", maxsplit=1)[-1]
    if not owner_prefix:
        return ()
    curr_parts = () if curr_dir in {"", "."} else tuple(curr_dir.split("/"))
    if owner_prefix in curr_parts or "internal" in curr_parts or curr_dir in {"", "."}:
        return ()
    return (import_spec[idx + 1 :][:256],)


def _extract_go_imports(file_path: str, root: ts.Node) -> tuple[list[PolyglotImport], set[int]]:
    imports: list[PolyglotImport] = []
    import_str_ids: set[int] = set()
    curr_dir = PurePosixPath(file_path).parent.as_posix()

    for node in iter_cst_nodes(root):
        if node.type != "import_spec":
            continue
        path_node = node.child_by_field_name("path")
        if path_node is None:
            continue
        import_str_ids.add(path_node.id)
        spec = unquote_cst_string(node_text(path_node)).strip()
        if not spec:
            continue
        name_node = node.child_by_field_name("name")
        alias = node_text(name_node).strip() if name_node is not None else ""
        first_seg = spec.split("/", maxsplit=1)[0]
        is_stdlib = "." not in first_seg and first_seg in GO_STDLIB_ROOTS
        imports.append(
            PolyglotImport(
                raw_target=spec[:512],
                root_pkg="" if is_stdlib else first_seg[:256],
                resolved_module="",
                is_internal=is_stdlib,
                is_wildcard=(alias == "."),
                private_symbols=_check_go_internal_breach(curr_dir, spec),
                line=max(1, node.start_point.row + 1),
                column=max(0, node.start_point.column),
            )
        )
    return imports, import_str_ids


def _go_callee_name(call_node: ts.Node) -> str:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return ""
    return re.sub(r"\s+", "", node_text(fn))[:256]


def _extract_go_receiver_info(fn_node: ts.Node) -> tuple[str, str]:
    recv = fn_node.child_by_field_name("receiver")
    if recv is None or not recv.named_children:
        return "", ""
    decl = recv.named_children[0]
    n_node = decl.child_by_field_name("name")
    t_node = decl.child_by_field_name("type")
    r_var = node_text(n_node).strip() if n_node is not None else ""
    r_type = (
        node_text(t_node).lstrip("*").split("[", maxsplit=1)[0].strip()
        if t_node is not None
        else ""
    )
    return r_var, r_type


def _identifiers_in_go_node(node: ts.Node | None) -> set[str]:
    if node is None:
        return set()
    return {node_text(i) for i in iter_cst_nodes(node) if i.type == "identifier"}


def _record_go_guard_node(
    sub: ts.Node,
    guarded: set[str],
    deps: dict[str, set[str]],
) -> None:
    if sub.type in {"if_statement", "expression_switch_statement", "type_switch_statement"}:
        guarded |= _identifiers_in_go_node(sub.child_by_field_name("initializer"))
        cond = sub.child_by_field_name("condition") or sub.child_by_field_name("value")
        guarded |= _identifiers_in_go_node(cond)
        return
    if sub.type == "call_expression":
        tail = _go_callee_name(sub).rsplit(".", maxsplit=1)[-1]
        if tail in _GO_GUARD_CALL_TAILS:
            guarded |= _identifiers_in_go_node(sub)
        return
    if sub.type in {"short_var_declaration", "assignment_statement", "var_spec"}:
        lhs_node = sub.child_by_field_name("left") or sub.child_by_field_name("name")
        rhs_node = sub.child_by_field_name("right") or sub.child_by_field_name("value")
        rhs_ids = _identifiers_in_go_node(rhs_node)
        for lhs in _identifiers_in_go_node(lhs_node):
            deps.setdefault(lhs, set()).update(rhs_ids)


def _collect_go_guarded_identifiers(body_node: ts.Node | None) -> frozenset[str]:
    if body_node is None:
        return frozenset()
    guarded: set[str] = set()
    deps: dict[str, set[str]] = {}
    for sub in iter_local_cst_nodes(body_node, _GO_SCOPE_BOUNDARIES):
        _record_go_guard_node(sub, guarded, deps)
    for _ in range(4):
        extra = {src for lhs in list(guarded) for src in deps.get(lhs, ()) if src not in guarded}
        if not extra:
            break
        guarded |= extra
    return frozenset(guarded)


def _extract_go_params(
    params_node: ts.Node | None,
    guarded_idents: frozenset[str],
) -> tuple[PolyglotParam, ...]:
    if params_node is None:
        return ()
    out: list[PolyglotParam] = []
    for decl in params_node.named_children:
        if decl.type not in {"parameter_declaration", "variadic_parameter_declaration"}:
            continue
        t_node = decl.child_by_field_name("type")
        t_txt = node_text(t_node).strip() if t_node is not None else ""
        names = [
            node_text(c).strip()
            for i, c in enumerate(decl.children)
            if decl.field_name_for_child(i) == "name"
        ] or ["_"]
        for p_name in names:
            is_guarded = p_name == "_" or p_name in guarded_idents
            unb = (not is_guarded) and _GO_UNBOUNDED_TYPE_RE.match(t_txt) is not None
            out.append(
                PolyglotParam(
                    name=p_name[:256],
                    has_type=bool(t_txt),
                    type_text=t_txt[:256],
                    is_unbounded_primitive=unb,
                    is_untyped_bag=_GO_BAG_RE.search(t_txt) is not None,
                    has_any_erasure=_GO_ANY_RE.search(t_txt) is not None,
                    line=max(1, decl.start_point.row + 1),
                    column=max(0, decl.start_point.column),
                )
            )
    return tuple(out[:64])


def _has_go_boundary_contract(
    params: tuple[PolyglotParam, ...],
    guarded_idents: frozenset[str],
) -> bool:
    for param in params:
        if param.name == "_":
            continue
        if param.is_unbounded_primitive or not param.has_type:
            return False
        if (param.is_untyped_bag or param.has_any_erasure) and param.name not in guarded_idents:
            return False
    return True


def _visit_go_local_node(
    sub: ts.Node,
    recv_var: str,
    calls: dict[str, None],
    fields: set[str],
    guarded_vars: set[str],
) -> tuple[bool, bool]:
    if recv_var and sub.type == "identifier" and node_text(sub).strip() == recv_var:
        return True, False
    if sub.type == "if_statement":
        cond = sub.child_by_field_name("condition")
        if cond is not None:
            guarded_vars |= {node_text(i) for i in iter_cst_nodes(cond) if i.type == "identifier"}
        return False, False
    if sub.type == "selector_expression" and recv_var:
        op = sub.child_by_field_name("operand")
        fld = sub.child_by_field_name("field")
        if op is not None and node_text(op).strip() == recv_var and fld is not None:
            fields.add(node_text(fld).strip()[:256])
            return True, False
        return False, False
    if sub.type == "call_expression":
        callee = _go_callee_name(sub)
        if callee:
            calls[callee] = None
        return False, callee in _GO_RAW_FS_CALLS or callee.startswith(("http.", "exec."))
    return False, False


def _collect_go_body_facts(
    body_node: ts.Node | None,
    recv_var: str,
    param_names: frozenset[str],
) -> tuple[tuple[str, ...], frozenset[str], bool, bool, tuple[str, ...]]:
    if body_node is None:
        return (), frozenset(), False, False, ()
    calls: dict[str, None] = {}
    fields: set[str] = set()
    guarded_vars: set[str] = set()
    performs_io = False
    uses_recv = False
    for sub in iter_local_cst_nodes(body_node, _GO_SCOPE_BOUNDARIES):
        s_recv, s_io = _visit_go_local_node(sub, recv_var, calls, fields, guarded_vars)
        uses_recv = uses_recv or s_recv
        performs_io = performs_io or s_io
    divisors = _find_go_unguarded_divisors(body_node, param_names, guarded_vars)
    return tuple(calls)[:256], frozenset(fields), performs_io, uses_recv, divisors


def _find_go_unguarded_divisors(
    body_node: ts.Node,
    param_names: frozenset[str],
    guarded_vars: set[str],
) -> tuple[str, ...]:
    bad: list[str] = []
    for sub in iter_local_cst_nodes(body_node, _GO_SCOPE_BOUNDARIES):
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


def _go_has_naked_return(body_node: ts.Node | None, result_node: ts.Node | None) -> bool:
    if body_node is None or result_node is None:
        return False
    return any(
        sub.type == "return_statement" and not sub.named_children
        for sub in iter_local_cst_nodes(body_node, _GO_SCOPE_BOUNDARIES)
    )


def _extract_go_test_t_param(params: tuple[PolyglotParam, ...], is_test_fn: bool) -> str:
    if not is_test_fn or not params or params[0].name == "_":
        return ""
    return params[0].name


def _go_call_delegates_assertion(call: ts.Node, t_param: str) -> bool:
    callee = _go_callee_name(call)
    tail = callee.rsplit(".", maxsplit=1)[-1].lstrip("_").lower()
    if tail.startswith(("assert", "check", "verify", "expect", "require", "must")):
        return True
    if not t_param or callee.startswith(f"{t_param}."):
        return False
    args = call.child_by_field_name("arguments")
    if args is None:
        return False
    return any(
        c.type == "identifier" and node_text(c).strip() == t_param for c in args.named_children
    )


def _go_body_has_test_assertion(body_node: ts.Node | None, t_param: str) -> bool:
    if body_node is None:
        return False
    txt = node_text(body_node)
    markers = (
        ".Error(",
        ".Errorf(",
        ".Fatal(",
        ".Fatalf(",
        ".Fail(",
        ".FailNow(",
        "assert.",
        "require.",
    )
    if any(m in txt for m in markers):
        return True
    return any(
        sub.type == "call_expression" and _go_call_delegates_assertion(sub, t_param)
        for sub in iter_cst_nodes(body_node)
    )


def _build_go_callable(fn_node: ts.Node, is_test_file: bool) -> PolyglotCallable:
    name_node = fn_node.child_by_field_name("name")
    fn_name = node_text(name_node).strip() if name_node is not None else "func"
    recv_var, recv_type = _extract_go_receiver_info(fn_node)
    qualname = f"{recv_type}.{fn_name}" if recv_type else fn_name
    is_test_fn = is_test_file and not recv_type and fn_name.startswith("Test")
    is_pub = fn_name[:1].isupper() and not is_test_fn and fn_name != "init"

    params_node = fn_node.child_by_field_name("parameters")
    body_node = fn_node.child_by_field_name("body")
    has_raw_params = params_node is not None and bool(params_node.named_children)
    guarded_idents = _collect_go_guarded_identifiers(body_node) if has_raw_params else frozenset()
    params = _extract_go_params(params_node, guarded_idents)
    param_names = frozenset(p.name for p in params if p.name != "_")
    t_param = _extract_go_test_t_param(params, is_test_fn)

    res_node = fn_node.child_by_field_name("result")
    res_txt = node_text(res_node).strip() if res_node is not None else "void"
    calls, fields, performs_io, uses_recv, divisors = _collect_go_body_facts(
        body_node, recv_var, param_names
    )
    is_rec = fn_name in calls or (bool(recv_var) and f"{recv_var}.{fn_name}" in calls)
    cc, depth, stmts = (
        compute_cst_callable_metrics(body_node, _GO_COMPLEXITY_SPEC, is_recursive=is_rec)
        if body_node is not None
        else (0, 0, 0)
    )
    arg_sig = ", ".join(f"{p.name} {p.type_text}" for p in params)
    recv_used = uses_recv or not bool(recv_type) or not bool(recv_var) or recv_var == "_"
    return PolyglotCallable(
        name=fn_name[:256],
        qualname=qualname[:256],
        enclosing_type=recv_type[:256],
        is_public=is_pub,
        is_method=bool(recv_type),
        is_receiver_used=recv_used,
        is_abstract_or_stub=body_node is None,
        is_test_callable=is_test_fn,
        params=params,
        has_return_type=True,
        return_type_text=res_txt[:256],
        return_has_any_erasure=_GO_ANY_RE.search(res_txt) is not None,
        return_is_untyped_bag=_GO_BAG_RE.search(res_txt) is not None,
        has_boundary_contract=_has_go_boundary_contract(params, guarded_idents),
        cognitive_complexity=cc,
        max_nesting_depth=depth,
        statement_count=stmts,
        start_line=max(1, fn_node.start_point.row + 1),
        end_line=max(1, fn_node.end_point.row + 1),
        column=max(0, fn_node.start_point.column),
        signature_summary=f"func {qualname}({arg_sig}) {res_txt}"[:512],
        calls=calls,
        accessed_fields=fields,
        performs_io=performs_io,
        is_recursive_unguarded=is_rec and not bool(param_names & _GO_FUEL_NAMES),
        has_mixed_or_missing_return=_go_has_naked_return(body_node, res_node),
        unguarded_divisors=divisors,
        has_test_assertion=_go_body_has_test_assertion(body_node, t_param) if is_test_fn else True,
    )


def _inspect_go_struct_field(
    f_decl: ts.Node,
    has_validator_method: bool,
) -> tuple[list[str], bool, list[str], list[str], bool]:
    t_node = f_decl.child_by_field_name("type")
    t_txt = node_text(t_node).strip() if t_node is not None else ""
    tag_node = f_decl.child_by_field_name("tag")
    tag_txt = node_text(tag_node) if tag_node is not None else ""
    is_json_wire = "json:" in tag_txt and 'json:"-"' not in tag_txt
    names = [
        node_text(c).strip()[:256]
        for i, c in enumerate(f_decl.children)
        if f_decl.field_name_for_child(i) == "name"
    ]
    has_any = _GO_ANY_RE.search(t_txt) is not None
    unb = (
        [n for n in names if n[:1].isupper()]
        if (is_json_wire and not has_validator_method and _GO_UNBOUNDED_TYPE_RE.match(t_txt))
        else []
    )
    sens = [
        n
        for n in names
        if n[:1].isupper()
        and is_sensitive_identifier(n)
        and 'json:"-"' not in tag_txt
        and "Secret" not in t_txt
    ]
    return names, has_any, unb, sens, is_json_wire


def _extract_go_struct_spec(
    spec_node: ts.Node,
    struct_node: ts.Node,
    validated_types: frozenset[str],
) -> PolyglotTypeDecl:
    name_node = spec_node.child_by_field_name("name")
    s_name = node_text(name_node).strip()[:256] if name_node is not None else "Struct"
    has_val = s_name in validated_types
    fields: list[str] = []
    unbounded: list[str] = []
    sensitive: list[str] = []
    any_field = False
    is_wire = False

    flist = next(
        (c for c in struct_node.named_children if c.type == "field_declaration_list"), None
    )
    for f_decl in flist.named_children if flist is not None else ():
        if f_decl.type != "field_declaration":
            continue
        f_names, f_any, f_unb, f_sens, f_wire = _inspect_go_struct_field(f_decl, has_val)
        fields.extend(f_names)
        any_field = any_field or f_any
        unbounded.extend(f_unb)
        sensitive.extend(f_sens)
        is_wire = is_wire or f_wire

    return PolyglotTypeDecl(
        name=s_name,
        kind="struct",
        is_public=s_name[:1].isupper(),
        is_schema_or_value=is_wire,
        is_strict_schema=True,
        fields=tuple(fields[:256]),
        sensitive_unredacted_fields=tuple(sensitive[:64]),
        unbounded_fields=tuple(unbounded[:128]),
        has_any_erasure_field=any_field,
        line=max(1, spec_node.start_point.row + 1),
        end_line=max(1, spec_node.end_point.row + 1),
        column=max(0, spec_node.start_point.column),
        signature_summary=f"type {s_name} struct {{{', '.join(fields[:8])}}}"[:512],
    )


def _extract_go_types_and_callables(
    root: ts.Node,
    is_test_file: bool,
) -> tuple[list[PolyglotTypeDecl], list[PolyglotCallable]]:
    callables = [
        _build_go_callable(node, is_test_file)
        for node in root.named_children
        if node.type in {"function_declaration", "method_declaration"}
    ]
    validated_types = frozenset(
        fn.enclosing_type
        for fn in callables
        if fn.enclosing_type and fn.name in {"Validate", "UnmarshalJSON"}
    )
    types: list[PolyglotTypeDecl] = []
    for node in iter_cst_nodes(root):
        if node.type != "type_spec":
            continue
        t_node = node.child_by_field_name("type")
        n_node = node.child_by_field_name("name")
        if t_node is None or n_node is None:
            continue
        if t_node.type == "struct_type":
            types.append(_extract_go_struct_spec(node, t_node, validated_types))
        else:
            t_name = node_text(n_node).strip()[:256]
            kind = "interface" if t_node.type == "interface_type" else "type_alias"
            types.append(
                PolyglotTypeDecl(
                    name=t_name,
                    kind=kind,
                    is_public=t_name[:1].isupper(),
                    has_any_erasure_field=_GO_ANY_RE.search(node_text(t_node)) is not None,
                    line=max(1, node.start_point.row + 1),
                    end_line=max(1, node.end_point.row + 1),
                    column=max(0, node.start_point.column),
                    signature_summary=f"type {t_name} {kind}",
                )
            )
    return types, callables


def _enclosing_go_func_body(node: ts.Node) -> ts.Node | None:
    curr = node.parent
    budget = LoopBudget(max_steps=128)
    while curr is not None:
        budget.tick()
        if curr.type in _GO_SCOPE_BOUNDARIES:
            return curr.child_by_field_name("body")
        curr = curr.parent
    return None


def _check_go_assign_node(file_path: str, node: ts.Node, is_test: bool) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or right is None:
        return out
    lhs_items = [node_text(c).strip() for c in left.named_children]
    has_call_rhs = any(c.type == "call_expression" for c in right.named_children)
    if not is_test and has_call_rhs and any(item == "_" for item in lhs_items):
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-001",
                location=node_span(file_path, node),
                message="Blank identifier '_' discards returned error/result from call.",
                remediation="Inspect and propagate returned error values explicitly.",
            )
        )
    if len(lhs_items) == 1 and len(right.named_children) == 1:
        r0 = right.named_children[0]
        if r0.type in {"interpreted_string_literal", "raw_string_literal"}:
            ident = lhs_items[0]
            lit = unquote_cst_string(node_text(r0))
            if ident and lit and is_sensitive_identifier(ident) and lit.lower() != ident.lower():
                out.append(
                    RawDiscrepancy(
                        code_id="JBC-SEC-002",
                        location=node_span(file_path, node, ident),
                        message=f"Sensitive identifier '{ident}' assigned a hardcoded literal.",
                        remediation="Inject secrets from runtime config rather than literals.",
                    )
                )
    return out


def _check_go_for_node(file_path: str, node: ts.Node, _is_test: bool) -> list[RawDiscrepancy]:
    if any(c.type in {"for_clause", "range_clause"} for c in node.named_children):
        return []
    body = node.child_by_field_name("body")
    if body is not None and (".Tick(" in node_text(body) or ".tick(" in node_text(body)):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-FLT-004",
            location=node_span(file_path, node),
            message="Condition/infinite 'for' loop omits a deterministic '.Tick()' step budget.",
            remediation="Use 'for range' / bounded index clause or tick a LoopBudget.",
        )
    ]


def _check_go_goroutine_node(
    file_path: str,
    node: ts.Node,
    _is_test: bool,
) -> list[RawDiscrepancy]:
    enclosing_body = _enclosing_go_func_body(node)
    body_txt = node_text(enclosing_body) if enclosing_body is not None else ""
    if "WaitGroup" in body_txt or ".Add(" in body_txt or "errgroup" in body_txt:
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-FLT-005",
            location=node_span(file_path, node),
            message="Unstructured 'go' spawn lacks WaitGroup or errgroup lifecycle supervision.",
            remediation="Coordinate workers with errgroup.WithContext or sync.WaitGroup.",
        )
    ]


def _check_go_top_var_node(file_path: str, node: ts.Node, is_test: bool) -> list[RawDiscrepancy]:
    if is_test or node.parent is None or node.parent.type != "source_file":
        return []
    out: list[RawDiscrepancy] = []
    for spec in iter_cst_nodes(node):
        if spec.type != "var_spec":
            continue
        n_node = spec.child_by_field_name("name")
        v_name = node_text(n_node).strip() if n_node is not None else ""
        spec_txt = node_text(spec)
        if v_name == "_" or v_name.startswith("Err") or "errors.New" in spec_txt:
            continue
        out.append(
            RawDiscrepancy(
                code_id="JBC-OBS-002",
                location=node_span(file_path, spec, v_name),
                message=f"Package-level mutable 'var {v_name}' introduces global state.",
                remediation="Use 'const' or pass explicit dependency structs.",
            )
        )
    return out


def _check_go_unsafe_ptr_node(
    file_path: str,
    node: ts.Node,
    _is_test: bool,
) -> list[RawDiscrepancy]:
    if node_text(node).strip() != "unsafe.Pointer":
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-SEC-001",
            location=node_span(file_path, node),
            message="'unsafe.Pointer' bypasses Go type and memory safety.",
            remediation="Eliminate unsafe pointer cast or attach a narrow CodeVariance.",
        )
    ]


def _is_inside_go_err_if(node: ts.Node) -> bool:
    curr = node.parent
    budget = LoopBudget(max_steps=128)
    while curr is not None and curr.type not in _GO_SCOPE_BOUNDARIES:
        budget.tick()
        if curr.type == "if_statement":
            cond = curr.child_by_field_name("condition")
            if cond is not None and "err" in node_text(cond).lower() and "nil" in node_text(cond):
                return True
        curr = curr.parent
    return False


def _check_go_panic_or_err_call(
    span: SourceSpan,
    call: ts.Node,
    callee: str,
    args_txt: str,
    is_test: bool,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if not is_test and callee == "panic":
        low = args_txt.lower()
        code = "JBC-VRF-002" if ("not implemented" in low or "todo" in low) else "JBC-FLT-002"
        msg = (
            "Unimplemented 'panic(\"not implemented\")' stub in production Go code."
            if code == "JBC-VRF-002"
            else "'panic()' in library Go code crashes process instead of returning error."
        )
        out.append(
            RawDiscrepancy(
                code_id=code,
                location=span,
                message=msg,
                remediation="Return an explicit typed 'error' value to callers.",
            )
        )
    if callee == "errors.New" and args_txt.strip() in {'("")', "('')", "()"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=span,
                message="'errors.New(\"\")' constructed with an empty diagnostic message.",
                remediation="Provide an actionable message naming the violated invariant.",
            )
        )
    if (
        not is_test
        and callee == "fmt.Errorf"
        and "%w" not in args_txt
        and _is_inside_go_err_if(call)
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=span,
                message="'fmt.Errorf' inside error handler omits '%w' causal error wrapping.",
                remediation="Wrap the underlying cause with '%w' to preserve errors.Is/As.",
            )
        )
    return out


def _check_go_resource_call(
    span: SourceSpan,
    call: ts.Node,
    callee: str,
    args_txt: str,
    is_test: bool,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if callee in {"http.Get", "http.Post", "http.Head", "http.PostForm"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-003",
                location=span,
                message=f"'{callee}()' uses http.DefaultClient with zero timeout.",
                remediation="Use '&http.Client{Timeout: ...}' and NewRequestWithContext.",
            )
        )
    if not is_test and callee in _GO_CLOSE_REQUIRED_CALLS:
        fn_body = _enclosing_go_func_body(call)
        body_txt = node_text(fn_body) if fn_body is not None else ""
        if "defer " not in body_txt or ".Close(" not in body_txt:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-003",
                    location=span,
                    message=f"Resource '{callee}()' acquired without a 'defer ...Close()' guard.",
                    remediation="Register 'defer handle.Close()' immediately after error check.",
                )
            )
    if not is_test and callee in _GO_UNBOUNDED_READ_CALLS and "LimitReader" not in args_txt:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-006",
                location=span,
                message=f"Unbounded '{callee}()' slurp risks memory exhaustion (OOM).",
                remediation="Wrap stream in 'io.LimitReader(r, maxBytes)' before reading.",
            )
        )
    return out


def _go_arg_is_dynamic_sql_alias(call: ts.Node, args_txt: str) -> bool:
    if "fmt.Sprintf(" in args_txt or "+" in args_txt:
        return True
    args_node = call.child_by_field_name("arguments")
    body = _enclosing_go_func_body(call)
    if args_node is None or body is None:
        return False
    arg_ids = _identifiers_in_go_node(args_node)
    for sub in iter_local_cst_nodes(body, _GO_SCOPE_BOUNDARIES):
        if sub.type in {"short_var_declaration", "assignment_statement"}:
            lhs = _identifiers_in_go_node(sub.child_by_field_name("left"))
            rhs = sub.child_by_field_name("right")
            rhs_txt = node_text(rhs) if rhs is not None else ""
            if (lhs & arg_ids) and ("fmt.Sprintf(" in rhs_txt or "+" in rhs_txt):
                return True
    return False


def _check_go_injection_call(
    span: SourceSpan,
    call: ts.Node,
    callee: str,
    args_txt: str,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if (
        callee in {"exec.Command", "exec.CommandContext"}
        and ('"sh"' in args_txt or '"bash"' in args_txt)
        and '"-c"' in args_txt
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-001",
                location=span,
                message=f"Shell call '{callee}(..., \"-c\", ...)' enables command injection.",
                remediation="Execute target binary directly with discrete argument strings.",
            )
        )
    if callee.endswith(
        (".Query", ".QueryContext", ".Exec", ".ExecContext")
    ) and _go_arg_is_dynamic_sql_alias(call, args_txt):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-001",
                location=span,
                message="SQL sink receives dynamically concatenated/Sprintf query.",
                remediation="Use parameterized SQL placeholders ('?' / '$1') and bound args.",
            )
        )
    return out


def _check_go_wire_json_call(
    span: SourceSpan,
    call: ts.Node,
    callee: str,
    args_txt: str,
    is_test: bool,
) -> list[RawDiscrepancy]:
    if is_test:
        return []
    out: list[RawDiscrepancy] = []
    if callee == "json.NewDecoder":
        fn_body = _enclosing_go_func_body(call)
        body_txt = node_text(fn_body) if fn_body is not None else ""
        if "DisallowUnknownFields(" not in body_txt:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-003",
                    location=span,
                    message="'json.NewDecoder' used without '.DisallowUnknownFields()'.",
                    remediation="Call 'dec.DisallowUnknownFields()' before 'dec.Decode(&dst)'.",
                )
            )
    if callee == "json.Unmarshal" and (
        "map[string]" in args_txt or "any" in args_txt or "interface{}" in args_txt
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-004",
                location=span,
                message="'json.Unmarshal' decodes into untyped map/any target.",
                remediation="Decode JSON via a strict typed struct with DisallowUnknownFields.",
            )
        )
    return out


def _check_go_fs_and_path_call(
    span: SourceSpan,
    callee: str,
    first_arg: ts.Node | None,
    is_test: bool,
) -> list[RawDiscrepancy]:
    if is_test or first_arg is None:
        return []
    out: list[RawDiscrepancy] = []
    raw_types = {
        "interpreted_string_literal",
        "raw_string_literal",
        "identifier",
        "binary_expression",
    }
    if callee in _GO_RAW_FS_CALLS and first_arg.type in raw_types:
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-005",
                location=span,
                message=f"Filesystem sink '{callee}()' invoked with uncontained raw path.",
                remediation="Use os.OpenRoot / canonical root containment before file access.",
            )
        )
    if callee in {"filepath.Abs", "path.Join", "filepath.Join"} and first_arg.type in {
        "interpreted_string_literal",
        "raw_string_literal",
    }:
        lit = unquote_cst_string(node_text(first_arg))
        if lit.startswith(("/", "~")):
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-006",
                    location=span,
                    message=f"Path helper '{callee}()' anchored on hardcoded literal '{lit[:48]}'.",
                    remediation="Anchor paths from os.Getwd(), os.UserConfigDir(), or root.",
                )
            )
    return out


def _check_go_obs_and_vrf_call(
    span: SourceSpan,
    callee: str,
    is_test: bool,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if not is_test and callee in {"fmt.Print", "fmt.Println", "fmt.Printf", "println", "print"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-OBS-001",
                location=span,
                message=f"Unstructured '{callee}()' output in production Go module.",
                remediation="Emit structured records via 'log/slog' or return typed values.",
            )
        )
    prng_or_env = {"rand.Int", "rand.Intn", "rand.Float64", "os.Setenv", "os.Unsetenv"}
    if not is_test and callee in prng_or_env:
        out.append(
            RawDiscrepancy(
                code_id="JBC-OBS-003",
                location=span,
                message=f"Global PRNG or process-env mutation via '{callee}()'.",
                remediation="Inject seeded math/rand/v2 source or immutable config structs.",
            )
        )
    if callee.endswith((".Skip", ".Skipf", ".SkipNow")):
        out.append(
            RawDiscrepancy(
                code_id="JBC-VRF-004",
                location=span,
                message=f"Skipped Go test '{callee}()' bypasses dynamic verification.",
                remediation="Remove t.Skip or register a time-bounded CodeVariance.",
            )
        )
    return out


def _check_go_call_node(file_path: str, call: ts.Node, is_test: bool) -> list[RawDiscrepancy]:
    callee = _go_callee_name(call)
    args_node = call.child_by_field_name("arguments")
    args_txt = node_text(args_node) if args_node is not None else "()"
    first_arg = args_node.named_children[0] if args_node and args_node.named_children else None
    span = node_span(file_path, call)
    return [
        *_check_go_panic_or_err_call(span, call, callee, args_txt, is_test),
        *_check_go_resource_call(span, call, callee, args_txt, is_test),
        *_check_go_injection_call(span, call, callee, args_txt),
        *_check_go_wire_json_call(span, call, callee, args_txt, is_test),
        *_check_go_fs_and_path_call(span, callee, first_arg, is_test),
        *_check_go_obs_and_vrf_call(span, callee, is_test),
    ]


def _check_go_test_tautology_if(
    file_path: str,
    node: ts.Node,
    is_test: bool,
) -> list[RawDiscrepancy]:
    if not is_test:
        return []
    cond = node.child_by_field_name("condition")
    if cond is None or cond.type != "binary_expression":
        return []
    left = cond.child_by_field_name("left")
    right = cond.child_by_field_name("right")
    op = cond.child_by_field_name("operator")
    if left is None or right is None or op is None:
        return []
    if (
        node_text(op).strip() in {"==", "!="}
        and node_text(left).strip() == node_text(right).strip()
    ):
        return [
            RawDiscrepancy(
                code_id="JBC-VRF-001",
                location=node_span(file_path, cond),
                message=f"Reflexive tautological comparison '{node_text(cond).strip()[:64]}'.",
                remediation="Compare actual output against an independent expected value.",
            )
        ]
    return []


_GO_NODE_SCANNERS: Final[Mapping[str, _GoNodeScanner]] = MappingProxyType(
    {
        "assignment_statement": _check_go_assign_node,
        "short_var_declaration": _check_go_assign_node,
        "for_statement": _check_go_for_node,
        "go_statement": _check_go_goroutine_node,
        "var_declaration": _check_go_top_var_node,
        "qualified_type": _check_go_unsafe_ptr_node,
        "call_expression": _check_go_call_node,
        "if_statement": _check_go_test_tautology_if,
    }
)


def _scan_go_cst_hazards(file_path: str, root: ts.Node, is_test: bool) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    for node in iter_cst_nodes(root):
        scanner = _GO_NODE_SCANNERS.get(node.type)
        if scanner is not None:
            out.extend(scanner(file_path, node, is_test))
    return out


def _collect_go_field_tag_ids(root: ts.Node) -> set[int]:
    ids: set[int] = set()
    for node in iter_cst_nodes(root):
        if node.type == "field_declaration":
            tag = node.child_by_field_name("tag")
            if tag is not None:
                ids.add(tag.id)
    return ids


@jezz_callable
def parse_go_unit(
    file_path: IdentifierStr,
    source: SourceCodeText,
) -> PolyglotUnit:
    """Parse a Go ``.go`` source module into a normalized ``PolyglotUnit``."""
    root = parse_cst_root(_GO_LANGUAGE, SupportedLanguage.GO, file_path, source)
    is_test = file_path.lower().endswith("_test.go")
    pkg_name = _extract_go_package_name(root)
    mod_name = _derive_go_module_name(file_path, pkg_name)
    comments = extract_cst_comments(root, frozenset({"comment"}))
    imports, import_str_ids = _extract_go_imports(file_path, root)
    types, callables = _extract_go_types_and_callables(root, is_test)

    tag_ids = _collect_go_field_tag_ids(root)
    hazards: list[RawDiscrepancy] = [
        *scan_cst_string_hazards(
            file_path,
            root,
            frozenset({"interpreted_string_literal", "raw_string_literal"}),
            skip_node_ids=frozenset(import_str_ids | tag_ids),
        ),
        *scan_deferred_comments(file_path, comments),
        *scan_suppression_comments(file_path, comments, _GO_SUPPRESSION_RE),
        *check_cst_cog_004_conditionals(file_path, root, _GO_CONDITIONAL_SPEC),
        *_scan_go_cst_hazards(file_path, root, is_test),
    ]
    has_calls = any(n.type == "call_expression" for n in iter_cst_nodes(root))
    has_div = any(
        n.type == "binary_expression"
        and (op := n.child_by_field_name("operator")) is not None
        and node_text(op).strip() in {"/", "%"}
        for n in iter_cst_nodes(root)
    )
    return PolyglotUnit(
        language=SupportedLanguage.GO,
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
