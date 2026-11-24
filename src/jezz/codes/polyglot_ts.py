"""TypeScript (.ts/.tsx) tree-sitter CST extractor for JBC v1.2 inspection."""

import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Final

import tree_sitter as ts
import tree_sitter_typescript

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
from jezz.codes.polyglot_manifests import extract_npm_package_root
from jezz.codes.polyglot_ts_hazards import (
    TS_RAW_FS_SINKS,
    TS_SCOPE_BOUNDARIES,
    scan_typescript_cst_hazards,
)
from jezz.guards import IdentifierStr, SourceCodeText, jezz_callable

_TS_LANGUAGE: Final[ts.Language] = ts.Language(tree_sitter_typescript.language_typescript())
_TSX_LANGUAGE: Final[ts.Language] = ts.Language(tree_sitter_typescript.language_tsx())

_TS_SCOPE_BOUNDARIES: Final[frozenset[str]] = TS_SCOPE_BOUNDARIES

_TS_COMPLEXITY_SPEC: Final[CstComplexitySpec] = CstComplexitySpec(
    nesting_branch_types=frozenset(
        {
            "if_statement",
            "switch_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "catch_clause",
            "ternary_expression",
        }
    ),
    else_clause_types=frozenset({"else_clause"}),
    if_types=frozenset({"if_statement"}),
    binary_expr_types=frozenset({"binary_expression"}),
    boolean_operators=frozenset({"&&", "||", "??"}),
    statement_types=frozenset(
        {
            "expression_statement",
            "lexical_declaration",
            "variable_declaration",
            "return_statement",
            "throw_statement",
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "switch_statement",
            "try_statement",
            "break_statement",
            "continue_statement",
        }
    ),
    scope_boundary_types=_TS_SCOPE_BOUNDARIES,
)

_TS_CONDITIONAL_SPEC: Final[CstConditionalSpec] = CstConditionalSpec(
    if_type="if_statement",
    consequence_field="consequence",
    alternative_field="alternative",
    terminal_jump_types=frozenset(
        {"return_statement", "throw_statement", "break_statement", "continue_statement"}
    ),
)

_TS_SUPPRESSION_RE: Final[str] = (
    r"@ts-ignore\b|@ts-expect-error\b|@ts-nocheck\b|eslint-disable\b|biome-ignore\b"
)
_TS_BAG_RE: Final[re.Pattern[str]] = re.compile(
    r"\bRecord<\s*string\s*,\s*(?:any|unknown|object)\s*>|^\s*object\s*$"
)
_TS_UNBOUNDED_TYPE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:string|number|Array<.+>|.+\[]|Map<.+>|Set<.+>)$"
)
_TS_FUEL_NAMES: Final[frozenset[str]] = frozenset(
    {"maxDepth", "remainingDepth", "fuel", "depthBudget", "budget", "max_depth"}
)
_TS_GUARD_CALLEES: Final[frozenset[str]] = frozenset(
    {
        "assert",
        "invariant",
        "Array.isArray",
        "Number.isFinite",
        "Number.isInteger",
        "Number.isSafeInteger",
    }
)
_TS_GUARD_TAILS: Final[frozenset[str]] = frozenset({"parse", "safeParse", "validate"})
_TS_STRIP_EXT_RE: Final[re.Pattern[str]] = re.compile(r"\.(?:tsx|ts|jsx|js|mjs)$")
_TS_TEST_ASSERT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:expect|expectTypeOf|assert\w*|verify\w*|check\w*)\s*[<.(]|\brejects\.|\bthrows\("
)
_INLINE_VARIANCE_RE: Final[re.Pattern[str]] = re.compile(
    r"([<,][ \t]*)(in[ \t]+out|out|in)(?=[ \t]+[A-Za-z_$][A-Za-z0-9_$]*\s*(?:extends\b|=|,|>))"
)
_LINE_VARIANCE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^([ \t]*)(in[ \t]+out|out|in)(?=[ \t]+[A-Za-z_$][A-Za-z0-9_$]*\s*(?:extends\b|=|,|>))"
)
_EXPORT_TYPE_STAR_RE: Final[re.Pattern[str]] = re.compile(
    r"\bexport[ \t]+type[ \t]+\*([ \t]+(?:as[ \t]+\w+[ \t]+)?from\b)"
)
_ASI_NEXT_GENERIC_RE: Final[re.Pattern[str]] = re.compile(
    r"([^\s;,{(\[|&?:=/])((?:\n[ \t]*(?://[^\n]*|/\*[^\n]*\*/)?){0,6}\n[ \t]*<\s*[A-Za-z_$])"
)


def _normalize_modern_ts_for_cst(source: str) -> str:
    bridged = _EXPORT_TYPE_STAR_RE.sub(r"export      *\1", source)
    bridged = _INLINE_VARIANCE_RE.sub(lambda m: m.group(1) + (" " * len(m.group(2))), bridged)
    bridged = _LINE_VARIANCE_RE.sub(lambda m: m.group(1) + (" " * len(m.group(2))), bridged)
    for _ in range(8):
        nxt = _ASI_NEXT_GENERIC_RE.sub(r"\1;\2", bridged)
        if nxt == bridged:
            break
        bridged = nxt
    return bridged


def _parse_ts_root_with_modern_bridge(
    ts_lang: ts.Language,
    file_path: str,
    source: str,
) -> ts.Node:
    parser = ts.Parser(ts_lang)
    fast_tree = parser.parse(source.encode("utf-8"))
    if not fast_tree.root_node.has_error:
        return fast_tree.root_node
    bridged = _normalize_modern_ts_for_cst(source)
    if bridged != source:
        bridged_tree = parser.parse(bridged.encode("utf-8"))
        if not bridged_tree.root_node.has_error:
            return bridged_tree.root_node
    return parse_cst_root(ts_lang, SupportedLanguage.TYPESCRIPT, file_path, source)


def _derive_ts_module_name(file_path: str) -> str:
    posix = PurePosixPath(file_path)
    parts = list(posix.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return ""
    stem = PurePosixPath(parts[-1]).stem
    if stem.endswith((".test", ".spec")):
        stem = stem.rsplit(".", maxsplit=1)[0]
    parts[-1] = stem
    if len(parts) > 1 and parts[-1] == "index":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)[:256]


def _is_ts_test_path(file_path: str) -> bool:
    low = file_path.lower()
    if low.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")):
        return True
    parts = set(PurePosixPath(low).parts[:-1])
    return bool(parts & {"__tests__", "tests", "test"})


def _step_rel_segment(stack: list[str], seg: str) -> None:
    if not seg or seg == ".":
        return
    if seg == "..":
        if stack:
            stack.pop()
        return
    stack.append(seg)


def _resolve_ts_relative_module(file_path: str, rel_spec: str) -> str:
    joined: list[str] = [p for p in PurePosixPath(file_path).parent.parts if p and p != "."]
    for seg in rel_spec.split("/"):
        _step_rel_segment(joined, seg)
    if joined and joined[0] == "src":
        joined = joined[1:]
    if not joined:
        return ""
    joined[-1] = _TS_STRIP_EXT_RE.sub("", joined[-1])
    if len(joined) > 1 and joined[-1] == "index":
        joined = joined[:-1]
    return ".".join(joined)[:256]


def _extract_private_import_names(stmt: ts.Node, spec: str) -> tuple[str, ...]:
    privs: list[str] = []
    tail_seg = spec.rsplit("/", maxsplit=1)[-1]
    if tail_seg.startswith("_") and not tail_seg.startswith("__"):
        privs.append(tail_seg[:256])
    for sub in iter_cst_nodes(stmt):
        if sub.type != "import_specifier":
            continue
        name_node = sub.child_by_field_name("name")
        ident = node_text(name_node).strip() if name_node is not None else ""
        if ident.startswith("_") and not ident.startswith("__"):
            privs.append(ident[:256])
    return tuple(dict.fromkeys(privs))[:64]


def _is_type_only_ts_import_line(source_lines: Sequence[str], row: int) -> bool:
    if 0 <= row < len(source_lines):
        return source_lines[row].lstrip().startswith(("import type", "export type"))
    return False


def _extract_ts_imports(
    file_path: str,
    root: ts.Node,
    source_lines: Sequence[str],
) -> tuple[list[PolyglotImport], set[int]]:
    imports: list[PolyglotImport] = []
    source_str_ids: set[int] = set()
    for node in root.named_children:
        if node.type not in {"import_statement", "export_statement"}:
            continue
        src_node = node.child_by_field_name("source")
        if src_node is None:
            continue
        source_str_ids.add(src_node.id)
        spec = unquote_cst_string(node_text(src_node))
        if not spec:
            continue
        row = node.start_point.row
        is_type_only = _is_type_only_ts_import_line(source_lines, row)
        is_rel = spec.startswith(".")
        resolved = _resolve_ts_relative_module(file_path, spec) if is_rel else ""
        root_pkg = "" if is_rel else extract_npm_package_root(spec)
        has_ns = any(
            n.type in {"namespace_import", "namespace_export"} for n in iter_cst_nodes(node)
        )
        is_export_star = node.type == "export_statement" and "*" in node_text(node).split("from")[0]
        imports.append(
            PolyglotImport(
                raw_target=spec,
                root_pkg=root_pkg,
                resolved_module=resolved,
                is_internal=is_rel,
                is_wildcard=(has_ns or is_export_star) and not is_type_only,
                private_symbols=_extract_private_import_names(node, spec),
                line=max(1, row + 1),
                column=max(0, node.start_point.column),
            )
        )
    return imports, source_str_ids


def _type_node_has_any(type_node: ts.Node | None) -> bool:
    if type_node is None:
        return False
    return any(
        sub.type == "predefined_type" and node_text(sub).strip() == "any"
        for sub in iter_cst_nodes(type_node)
    )


def _is_ts_mutable_default(val_node: ts.Node | None) -> bool:
    if val_node is None:
        return False
    if val_node.type in {"array", "object"}:
        return True
    return val_node.type == "new_expression" and any(
        k in node_text(val_node) for k in ("Map", "Set")
    )


def _ts_callee_name(call_node: ts.Node) -> str:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return ""
    return re.sub(r"\s+", "", node_text(fn))[:256]


def _collect_identifiers_in_node(node: ts.Node | None) -> set[str]:
    if node is None:
        return set()
    return {node_text(i) for i in iter_cst_nodes(node) if i.type == "identifier"}


def _is_ts_guard_call(sub: ts.Node) -> bool:
    callee = _ts_callee_name(sub)
    tail = callee.rsplit(".", maxsplit=1)[-1]
    if callee not in _TS_GUARD_CALLEES and tail not in _TS_GUARD_TAILS:
        return False
    args = sub.child_by_field_name("arguments")
    return args is None or node_text(args).strip() not in {"(true)", "(false)"}


def _is_ts_typeof_or_instanceof(sub: ts.Node) -> bool:
    if sub.type == "unary_expression":
        return node_text(sub).lstrip().startswith("typeof ")
    if sub.type == "binary_expression":
        op = sub.child_by_field_name("operator")
        return op is not None and node_text(op).strip() == "instanceof"
    return False


def _record_ts_guard_node(
    sub: ts.Node,
    guarded: set[str],
    deps: dict[str, set[str]],
) -> None:
    if sub.type in {"if_statement", "ternary_expression", "switch_statement"}:
        cond = sub.child_by_field_name("condition") or sub.child_by_field_name("value")
        guarded |= _collect_identifiers_in_node(cond)
        return
    if _is_ts_typeof_or_instanceof(sub) or (
        sub.type == "call_expression" and _is_ts_guard_call(sub)
    ):
        guarded |= _collect_identifiers_in_node(sub)
        return
    if sub.type in {"variable_declarator", "assignment_expression"}:
        lhs_node = sub.child_by_field_name("name") or sub.child_by_field_name("left")
        rhs_node = sub.child_by_field_name("value") or sub.child_by_field_name("right")
        rhs_ids = _collect_identifiers_in_node(rhs_node)
        for lhs in _collect_identifiers_in_node(lhs_node):
            deps.setdefault(lhs, set()).update(rhs_ids)


def _collect_ts_guarded_identifiers(body_node: ts.Node | None) -> frozenset[str]:
    if body_node is None:
        return frozenset()
    guarded: set[str] = set()
    deps: dict[str, set[str]] = {}
    for sub in iter_local_cst_nodes(body_node, _TS_SCOPE_BOUNDARIES):
        _record_ts_guard_node(sub, guarded, deps)
    for _ in range(4):
        extra = {src for lhs in list(guarded) for src in deps.get(lhs, ()) if src not in guarded}
        if not extra:
            break
        guarded |= extra
    return frozenset(guarded)


def _extract_ts_param(
    param_node: ts.Node,
    guarded_idents: frozenset[str],
) -> PolyglotParam | None:
    pattern = param_node.child_by_field_name("pattern")
    ident_node = (
        pattern
        if pattern is not None
        else (param_node.named_children[0] if param_node.named_children else None)
    )
    if ident_node is None:
        return None
    raw_name = node_text(ident_node).strip().lstrip(".")[:256] or "arg"
    type_ann = param_node.child_by_field_name("type")
    clean_type = node_text(type_ann).lstrip(":").strip() if type_ann is not None else ""
    is_guarded = raw_name in guarded_idents
    unbounded = (
        (not is_guarded)
        and bool(clean_type)
        and _TS_UNBOUNDED_TYPE_RE.match(clean_type) is not None
    )
    return PolyglotParam(
        name=raw_name,
        has_type=type_ann is not None,
        type_text=clean_type[:256],
        has_mutable_default=_is_ts_mutable_default(param_node.child_by_field_name("value")),
        is_unbounded_primitive=unbounded,
        is_untyped_bag=bool(clean_type and _TS_BAG_RE.search(clean_type)),
        has_any_erasure=_type_node_has_any(type_ann),
        line=max(1, param_node.start_point.row + 1),
        column=max(0, param_node.start_point.column),
    )


def _has_ts_boundary_contract(
    params: tuple[PolyglotParam, ...],
    guarded_idents: frozenset[str],
) -> bool:
    for param in params:
        if not param.has_type or param.is_unbounded_primitive:
            return False
        if (param.is_untyped_bag or param.has_any_erasure) and param.name not in guarded_idents:
            return False
    return True


def _record_ts_call_fact(
    call_node: ts.Node,
    calls: dict[str, None],
    guarded_vars: set[str],
    vacuous: list[str],
) -> bool:
    callee = _ts_callee_name(call_node)
    if callee:
        calls[callee] = None
    performs_io = callee in TS_RAW_FS_SINKS or callee in {"fetch", "execSync", "spawnSync"}
    if callee not in {"assert", "invariant", "console.assert"}:
        return performs_io
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return performs_io
    if node_text(args).strip() in {"(true)", "(false)"}:
        vacuous.append(f"Contract '{callee}' uses a constant boolean literal")
        return performs_io
    guarded_vars |= _collect_identifiers_in_node(args)
    return performs_io


def _extract_this_field(member_node: ts.Node) -> str:
    obj = member_node.child_by_field_name("object")
    prop = member_node.child_by_field_name("property")
    if obj is not None and obj.type == "this" and prop is not None:
        return node_text(prop).strip()[:256]
    return ""


def _visit_ts_local_node(
    sub: ts.Node,
    calls: dict[str, None],
    fields: set[str],
    guarded_vars: set[str],
    vacuous: list[str],
) -> tuple[bool, bool]:
    if sub.type == "this":
        return True, False
    if sub.type == "if_statement":
        guarded_vars |= _collect_identifiers_in_node(sub.child_by_field_name("condition"))
        return False, False
    if sub.type == "member_expression":
        fld = _extract_this_field(sub)
        if fld:
            fields.add(fld)
        return False, False
    if sub.type == "call_expression":
        return False, _record_ts_call_fact(sub, calls, guarded_vars, vacuous)
    return False, False


def _collect_ts_local_facts(
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
    uses_this = False

    for sub in iter_local_cst_nodes(body_node, _TS_SCOPE_BOUNDARIES):
        saw_this, saw_io = _visit_ts_local_node(sub, calls, fields, guarded_vars, vacuous)
        uses_this = uses_this or saw_this
        performs_io = performs_io or saw_io

    divisors = _find_ts_unguarded_divisors(body_node, param_names, guarded_vars)
    return tuple(calls)[:256], frozenset(fields), performs_io, uses_this, divisors, tuple(vacuous)


def _find_ts_unguarded_divisors(
    body_node: ts.Node,
    param_names: frozenset[str],
    guarded_vars: set[str],
) -> tuple[str, ...]:
    bad: list[str] = []
    for sub in iter_local_cst_nodes(body_node, _TS_SCOPE_BOUNDARIES):
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


def _ts_has_mixed_or_missing_return(
    body_node: ts.Node | None,
    ret_type_text: str,
    is_ctor: bool,
) -> bool:
    if body_node is None or is_ctor or body_node.type != "statement_block":
        return False
    returns = [
        n
        for n in iter_local_cst_nodes(body_node, _TS_SCOPE_BOUNDARIES)
        if n.type == "return_statement"
    ]
    has_val = any(len(r.named_children) > 0 for r in returns)
    has_bare = any(len(r.named_children) == 0 for r in returns)
    if has_val and has_bare:
        return True
    norm_ret = ret_type_text.strip()
    if norm_ret in {"", "void", "Promise<void>", "never", "undefined"} or "| undefined" in norm_ret:
        return False
    named_stmts = body_node.named_children
    if not named_stmts:
        return True
    if named_stmts[-1].type in {"return_statement", "throw_statement"}:
        return False
    return not has_val


def _build_ts_callable(
    fn_node: ts.Node,
    name: str,
    enclosing_type: str,
    is_public: bool,
    is_method: bool,
    is_abstract: bool,
) -> PolyglotCallable:
    qualname = f"{enclosing_type}.{name}" if enclosing_type else name
    params_node = fn_node.child_by_field_name("parameters")
    body_node = fn_node.child_by_field_name("body")
    raw_param_nodes = (
        [
            c
            for c in params_node.named_children
            if c.type in {"required_parameter", "optional_parameter"}
        ]
        if params_node is not None
        else []
    )
    guarded_idents = _collect_ts_guarded_identifiers(body_node) if raw_param_nodes else frozenset()
    params = tuple(
        p for raw in raw_param_nodes if (p := _extract_ts_param(raw, guarded_idents)) is not None
    )
    param_names = frozenset(p.name for p in params)
    ret_node = fn_node.child_by_field_name("return_type")
    ret_text = node_text(ret_node).lstrip(":").strip() if ret_node is not None else ""
    is_ctor = name == "constructor"

    calls, fields, io_flag, uses_this, divisors, vacuous = _collect_ts_local_facts(
        body_node, param_names
    )
    is_rec = name in calls or f"this.{name}" in calls
    cc, depth, stmts = (
        compute_cst_callable_metrics(body_node, _TS_COMPLEXITY_SPEC, is_recursive=is_rec)
        if body_node is not None
        else (0, 0, 0)
    )
    arg_sig = ", ".join(f"{p.name}: {p.type_text}" for p in params)
    sig = f"function {qualname}({arg_sig}): {ret_text or 'void'}"
    return PolyglotCallable(
        name=name[:256],
        qualname=qualname[:256],
        enclosing_type=enclosing_type[:256],
        is_public=is_public,
        is_method=is_method and not is_ctor,
        is_receiver_used=uses_this or not is_method or is_ctor,
        is_abstract_or_stub=is_abstract,
        params=params[:64],
        has_return_type=is_ctor or ret_node is not None,
        return_type_text=ret_text[:256],
        return_has_any_erasure=_type_node_has_any(ret_node),
        return_is_untyped_bag=bool(ret_text and _TS_BAG_RE.search(ret_text)),
        has_boundary_contract=_has_ts_boundary_contract(params, guarded_idents),
        cognitive_complexity=cc,
        max_nesting_depth=depth,
        statement_count=stmts,
        start_line=max(1, fn_node.start_point.row + 1),
        end_line=max(1, fn_node.end_point.row + 1),
        column=max(0, fn_node.start_point.column),
        signature_summary=sig[:512],
        calls=calls,
        accessed_fields=fields,
        performs_io=io_flag,
        is_recursive_unguarded=is_rec and not bool(param_names & _TS_FUEL_NAMES),
        has_mixed_or_missing_return=_ts_has_mixed_or_missing_return(body_node, ret_text, is_ctor),
        unguarded_divisors=divisors,
        vacuous_contracts=vacuous,
    )


def _inspect_ts_field_member(member: ts.Node) -> tuple[str, bool, bool]:
    f_name_node = member.child_by_field_name("name")
    f_name = node_text(f_name_node).strip()[:256] if f_name_node is not None else ""
    f_type = member.child_by_field_name("type")
    f_type_txt = node_text(f_type) if f_type is not None else ""
    is_sens = (
        bool(f_name)
        and not f_name.startswith("#")
        and is_sensitive_identifier(f_name)
        and "Secret" not in f_type_txt
        and "Redacted" not in f_type_txt
    )
    return f_name, _type_node_has_any(f_type), is_sens


def _inspect_ts_method_member(
    member: ts.Node,
    cls_name: str,
    is_exported: bool,
    is_abs_cls: bool,
) -> PolyglotCallable:
    m_name_node = member.child_by_field_name("name")
    m_name = node_text(m_name_node).strip() if m_name_node is not None else "method"
    mod_txt = node_text(member).split("(")[0]
    is_static = "static " in mod_txt
    is_priv = "private " in mod_txt or m_name.startswith(("_", "#"))
    is_abs = is_abs_cls and member.type == "abstract_method_signature"
    return _build_ts_callable(
        member, m_name, cls_name, is_exported and not is_priv, not is_static, is_abs
    )


def _extract_ts_class(
    cls_node: ts.Node,
    is_exported: bool,
) -> tuple[PolyglotTypeDecl, list[PolyglotCallable]]:
    name_node = cls_node.child_by_field_name("name")
    cls_name = node_text(name_node).strip()[:256] if name_node is not None else "AnonymousClass"
    body = cls_node.child_by_field_name("body")
    fields: list[str] = []
    sensitive: list[str] = []
    any_field = False
    methods: list[PolyglotCallable] = []
    is_abs_cls = cls_node.type == "abstract_class_declaration"

    for member in body.named_children if body is not None else ():
        if member.type == "public_field_definition":
            f_name, has_any, is_sens = _inspect_ts_field_member(member)
            if f_name:
                fields.append(f_name)
            any_field = any_field or has_any
            if is_sens:
                sensitive.append(f_name)
        elif member.type in {"method_definition", "abstract_method_signature"}:
            methods.append(_inspect_ts_method_member(member, cls_name, is_exported, is_abs_cls))

    decl = PolyglotTypeDecl(
        name=cls_name,
        kind="class",
        is_public=is_exported,
        fields=tuple(fields[:256]),
        sensitive_unredacted_fields=tuple(sensitive[:64]),
        has_any_erasure_field=any_field,
        line=max(1, cls_node.start_point.row + 1),
        end_line=max(1, cls_node.end_point.row + 1),
        column=max(0, cls_node.start_point.column),
        signature_summary=f"class {cls_name} {{{', '.join(fields[:8])}}}"[:512],
    )
    return decl, methods


def _extract_ts_interface(iface_node: ts.Node, is_exported: bool) -> PolyglotTypeDecl:
    name_node = iface_node.child_by_field_name("name")
    name = node_text(name_node).strip()[:256] if name_node is not None else "Interface"
    body = iface_node.child_by_field_name("body")
    fields: list[str] = []
    sensitive: list[str] = []
    any_field = False
    for prop in body.named_children if body is not None else ():
        if prop.type != "property_signature":
            continue
        f_name, has_any, is_sens = _inspect_ts_field_member(prop)
        if f_name:
            fields.append(f_name)
        any_field = any_field or has_any
        if is_sens:
            sensitive.append(f_name)
    return PolyglotTypeDecl(
        name=name,
        kind="interface",
        is_public=is_exported,
        fields=tuple(fields[:256]),
        sensitive_unredacted_fields=tuple(sensitive[:64]),
        has_any_erasure_field=any_field,
        line=max(1, iface_node.start_point.row + 1),
        end_line=max(1, iface_node.end_point.row + 1),
        column=max(0, iface_node.start_point.column),
        signature_summary=f"interface {name} {{{', '.join(fields[:8])}}}"[:512],
    )


def _zod_field_is_unbounded(expr_text: str) -> bool:
    compact = re.sub(r"\s+", "", expr_text)
    str_bounds = (".min(", ".max(", ".length(", ".regex(", ".uuid(", ".email(")
    num_bounds = (
        ".int(",
        ".finite(",
        ".min(",
        ".max(",
        ".gt(",
        ".gte(",
        ".lt(",
        ".lte(",
        ".positive(",
        ".nonnegative(",
    )
    if "z.string()" in compact and not any(b in compact for b in str_bounds):
        return True
    if "z.number()" in compact and not any(b in compact for b in num_bounds):
        return True
    return "z.array(" in compact and not any(
        b in compact for b in (".max(", ".length(", ".nonempty(")
    )


def _inspect_zod_pair(pair_node: ts.Node) -> tuple[str, bool, bool]:
    k_node = pair_node.child_by_field_name("key")
    v_node = pair_node.child_by_field_name("value")
    if k_node is None or v_node is None:
        return "", False, False
    key = unquote_cst_string(node_text(k_node)).strip()[:256]
    val_txt = node_text(v_node)
    is_sens = (
        bool(key)
        and is_sensitive_identifier(key)
        and "transform" not in val_txt
        and "brand" not in val_txt
    )
    return key, _zod_field_is_unbounded(val_txt), is_sens


def _collect_zod_fields(val_node: ts.Node) -> tuple[list[str], list[str], list[str]]:
    fields: list[str] = []
    unbounded: list[str] = []
    sensitive: list[str] = []
    for sub in iter_cst_nodes(val_node):
        if sub.type != "pair":
            continue
        key, is_unb, is_sens = _inspect_zod_pair(sub)
        if not key or key in fields:
            continue
        fields.append(key)
        if is_unb:
            unbounded.append(key)
        if is_sens:
            sensitive.append(key)
    return fields, unbounded, sensitive


def _extract_zod_schema_decl(
    declarator: ts.Node,
    is_exported: bool,
) -> PolyglotTypeDecl | None:
    name_node = declarator.child_by_field_name("name")
    val_node = declarator.child_by_field_name("value")
    if name_node is None or val_node is None:
        return None
    full_txt = re.sub(r"\s+", "", node_text(val_node))
    if not ("z.object(" in full_txt or "z.strictObject(" in full_txt):
        return None
    schema_name = node_text(name_node).strip()[:256]
    is_strict = "z.strictObject(" in full_txt or ".strict()" in full_txt
    fields, unbounded, sensitive = _collect_zod_fields(val_node)
    return PolyglotTypeDecl(
        name=schema_name,
        kind="struct",
        is_public=is_exported,
        is_schema_or_value=True,
        is_strict_schema=is_strict,
        fields=tuple(fields[:256]),
        sensitive_unredacted_fields=tuple(sensitive[:64]),
        unbounded_fields=tuple(unbounded[:128]),
        line=max(1, declarator.start_point.row + 1),
        end_line=max(1, declarator.end_point.row + 1),
        column=max(0, declarator.start_point.column),
        signature_summary=f"schema {schema_name} {{{', '.join(fields[:8])}}}"[:512],
    )


def _consume_ts_var_decl(
    stmt: ts.Node,
    is_exp: bool,
    types: list[PolyglotTypeDecl],
    callables: list[PolyglotCallable],
    hazards: list[RawDiscrepancy],
) -> None:
    is_mut = stmt.type == "variable_declaration" or node_text(stmt).lstrip().startswith("let ")
    for decl in stmt.named_children:
        if decl.type != "variable_declarator":
            continue
        n_node = decl.child_by_field_name("name")
        v_node = decl.child_by_field_name("value")
        var_name = node_text(n_node).strip() if n_node is not None else ""
        if is_mut and var_name:
            hazards.append(
                RawDiscrepancy(
                    code_id="JBC-OBS-002",
                    location=SourceSpan(
                        file_path="<pending>",
                        line=max(1, stmt.start_point.row + 1),
                        column=max(0, stmt.start_point.column),
                        symbol=var_name[:256],
                    ),
                    message=f"Module-level mutable variable '{var_name}' introduces global state.",
                    remediation="Use 'const' with immutable values or pass explicit state objects.",
                )
            )
        zod_decl = _extract_zod_schema_decl(decl, is_exp)
        if zod_decl is not None:
            types.append(zod_decl)
            continue
        if (
            v_node is not None
            and v_node.type in {"arrow_function", "function_expression"}
            and var_name
        ):
            callables.append(_build_ts_callable(v_node, var_name, "", is_exp, False, False))


def _consume_ts_top_stmt(
    stmt: ts.Node,
    is_exp: bool,
    types: list[PolyglotTypeDecl],
    callables: list[PolyglotCallable],
    hazards: list[RawDiscrepancy],
) -> None:
    if stmt.type in {"class_declaration", "abstract_class_declaration"}:
        decl, methods = _extract_ts_class(stmt, is_exp)
        types.append(decl)
        callables.extend(methods)
        return
    if stmt.type == "interface_declaration":
        types.append(_extract_ts_interface(stmt, is_exp))
        return
    if stmt.type in {"enum_declaration", "type_alias_declaration"}:
        n_node = stmt.child_by_field_name("name")
        if n_node is not None:
            t_name = node_text(n_node).strip()[:256]
            kind = "enum" if stmt.type == "enum_declaration" else "type_alias"
            types.append(
                PolyglotTypeDecl(
                    name=t_name,
                    kind=kind,
                    is_public=is_exp,
                    has_any_erasure_field=_type_node_has_any(stmt),
                    line=max(1, stmt.start_point.row + 1),
                    end_line=max(1, stmt.end_point.row + 1),
                    column=max(0, stmt.start_point.column),
                    signature_summary=f"{kind} {t_name}",
                )
            )
        return
    if stmt.type == "function_declaration":
        n_node = stmt.child_by_field_name("name")
        f_name = node_text(n_node).strip() if n_node is not None else "anonymous"
        callables.append(_build_ts_callable(stmt, f_name, "", is_exp, False, False))
        return
    if stmt.type in {"lexical_declaration", "variable_declaration"}:
        _consume_ts_var_decl(stmt, is_exp, types, callables, hazards)


def _extract_top_level_ts_items(
    root: ts.Node,
) -> tuple[list[PolyglotTypeDecl], list[PolyglotCallable], list[RawDiscrepancy]]:
    types: list[PolyglotTypeDecl] = []
    callables: list[PolyglotCallable] = []
    hazards: list[RawDiscrepancy] = []
    for top in root.named_children:
        is_exp = top.type == "export_statement"
        stmt = top.child_by_field_name("declaration") if is_exp else top
        if stmt is not None:
            _consume_ts_top_stmt(stmt, is_exp, types, callables, hazards)
    return types, callables, hazards


def _parse_one_ts_test_call(node: ts.Node) -> PolyglotCallable | None:
    args = node.child_by_field_name("arguments")
    if args is None or len(args.named_children) < 2:
        return None
    label = unquote_cst_string(node_text(args.named_children[0])).strip()
    safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_") or "test_case"
    cb_txt = node_text(args.named_children[1])
    has_assert = _TS_TEST_ASSERT_RE.search(cb_txt) is not None
    return PolyglotCallable(
        name=safe_name[:256],
        qualname=safe_name[:256],
        is_test_callable=True,
        has_test_assertion=has_assert,
        start_line=max(1, node.start_point.row + 1),
        end_line=max(1, node.end_point.row + 1),
        column=max(0, node.start_point.column),
        signature_summary=f"test('{label[:64]}')"[:512],
    )


def _check_ts_tautological_expect(file_path: str, node: ts.Node) -> RawDiscrepancy | None:
    full = re.sub(r"\s+", "", node_text(node))
    m = re.match(r"^expect\((.+)\)\.(?:toBe|toEqual|toStrictEqual)\((.+)\)$", full)
    if m is None or m.group(1) != m.group(2):
        return None
    return RawDiscrepancy(
        code_id="JBC-VRF-001",
        location=node_span(file_path, node),
        message=f"Tautological assertion '{full[:80]}' compares identical values.",
        remediation="Assert a falsifiable behavioral outcome on the target unit.",
    )


def _extract_ts_test_blocks(
    file_path: str,
    root: ts.Node,
) -> tuple[list[PolyglotCallable], list[RawDiscrepancy]]:
    tests: list[PolyglotCallable] = []
    hazards: list[RawDiscrepancy] = []
    for node in iter_cst_nodes(root):
        if node.type != "call_expression":
            continue
        callee = _ts_callee_name(node)
        if callee in {"it", "test"}:
            parsed = _parse_one_ts_test_call(node)
            if parsed is not None:
                tests.append(parsed)
        if callee.endswith((".toBe", ".toEqual", ".toStrictEqual")):
            taut = _check_ts_tautological_expect(file_path, node)
            if taut is not None:
                hazards.append(taut)
    return tests, hazards


@jezz_callable
def parse_typescript_unit(
    file_path: IdentifierStr,
    source: SourceCodeText,
) -> PolyglotUnit:
    """Parse a TypeScript ``.ts`` or ``.tsx`` module into a normalized ``PolyglotUnit``."""
    ts_lang = _TSX_LANGUAGE if file_path.lower().endswith(".tsx") else _TS_LANGUAGE
    root = _parse_ts_root_with_modern_bridge(ts_lang, file_path, source)
    is_test = _is_ts_test_path(file_path)
    comments = extract_cst_comments(root, frozenset({"comment"}))
    imports, import_str_ids = _extract_ts_imports(file_path, root, source.splitlines())
    types, callables, top_hazards = _extract_top_level_ts_items(root)

    patched_top = [
        h.model_copy(update={"location": h.location.model_copy(update={"file_path": file_path})})
        for h in top_hazards
    ]
    hazards: list[RawDiscrepancy] = [
        *patched_top,
        *scan_cst_string_hazards(
            file_path,
            root,
            frozenset({"string", "template_string"}),
            skip_node_ids=frozenset(import_str_ids),
        ),
        *scan_deferred_comments(file_path, comments),
        *scan_suppression_comments(file_path, comments, _TS_SUPPRESSION_RE),
        *check_cst_cog_004_conditionals(file_path, root, _TS_CONDITIONAL_SPEC),
        *scan_typescript_cst_hazards(file_path, root, is_test=is_test),
    ]
    if is_test:
        test_fns, test_hazards = _extract_ts_test_blocks(file_path, root)
        callables.extend(test_fns)
        hazards.extend(test_hazards)

    has_calls = any(n.type == "call_expression" for n in iter_cst_nodes(root))
    has_div = any(
        n.type == "binary_expression"
        and (op := n.child_by_field_name("operator")) is not None
        and node_text(op).strip() in {"/", "%"}
        for n in iter_cst_nodes(root)
    )
    return PolyglotUnit(
        language=SupportedLanguage.TYPESCRIPT,
        file_path=file_path,
        module_name=_derive_ts_module_name(file_path),
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
