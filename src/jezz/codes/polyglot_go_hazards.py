"""Go (.go) CST hazard scanners for JBC v1.2 inspection."""

import re
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

import tree_sitter as ts

from jezz.codes.ast_utils import RawDiscrepancy, SourceSpan
from jezz.codes.polyglot_cst import (
    is_sensitive_identifier,
    iter_cst_nodes,
    iter_local_cst_nodes,
    node_span,
    node_text,
    unquote_cst_string,
)
from jezz.codes.polyglot_ir import GuardedTsNode
from jezz.guards import IdentifierStr, LoopBudget, jezz_callable

GO_SCOPE_BOUNDARIES: Final[frozenset[str]] = frozenset(
    {"function_declaration", "method_declaration", "func_literal"}
)

GO_RAW_FS_CALLS: Final[frozenset[str]] = frozenset(
    {"os.Open", "os.ReadFile", "os.WriteFile", "os.Remove", "os.RemoveAll", "os.Create"}
)
_GO_CLOSE_REQUIRED_CALLS: Final[frozenset[str]] = frozenset({"os.Open", "os.Create", "sql.Open"})
_GO_UNBOUNDED_READ_CALLS: Final[frozenset[str]] = frozenset(
    {"io.ReadAll", "ioutil.ReadAll", "os.ReadFile"}
)

type _GoNodeScanner = Callable[[str, ts.Node, bool], list[RawDiscrepancy]]


def _go_callee_name(call_node: ts.Node) -> str:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return ""
    return re.sub(r"\s+", "", node_text(fn))[:256]


def _identifiers_in_go_node(node: ts.Node | None) -> set[str]:
    if node is None:
        return set()
    return {node_text(i) for i in iter_cst_nodes(node) if i.type == "identifier"}


def _enclosing_go_func_body(node: ts.Node) -> ts.Node | None:
    curr = node.parent
    budget = LoopBudget(max_steps=128)
    while curr is not None:
        budget.tick()
        if curr.type in GO_SCOPE_BOUNDARIES:
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
    while curr is not None and curr.type not in GO_SCOPE_BOUNDARIES:
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
    for sub in iter_local_cst_nodes(body, GO_SCOPE_BOUNDARIES):
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
    if callee in GO_RAW_FS_CALLS and first_arg.type in raw_types:
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


@jezz_callable
def scan_go_cst_hazards(
    file_path: IdentifierStr,
    root: GuardedTsNode,
    *,
    is_test: bool = False,
) -> list[RawDiscrepancy]:
    """Scan a Go CST ``root`` for Articles I-VI node-level discrepancies."""
    out: list[RawDiscrepancy] = []
    for node in iter_cst_nodes(root):
        scanner = _GO_NODE_SCANNERS.get(node.type)
        if scanner is not None:
            out.extend(scanner(file_path, node, is_test))
    return out
