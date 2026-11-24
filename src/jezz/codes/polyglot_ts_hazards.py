"""TypeScript (.ts/.tsx) CST hazard scanners for JBC v1.2 inspection."""

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

TS_SCOPE_BOUNDARIES: Final[frozenset[str]] = frozenset(
    {
        "function_declaration",
        "method_definition",
        "arrow_function",
        "function_expression",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
    }
)

_TS_SQL_METHODS: Final[frozenset[str]] = frozenset({"query", "execute", "raw", "exec"})
TS_RAW_FS_SINKS: Final[frozenset[str]] = frozenset(
    {
        "fs.readFileSync",
        "fs.readFile",
        "fs.promises.readFile",
        "readFileSync",
        "readFile",
        "fs.unlinkSync",
        "fs.unlink",
        "fs.rmSync",
        "fs.rm",
    }
)
_TS_UNANCHORED_PATH_CALLS: Final[frozenset[str]] = frozenset({"path.resolve", "path.normalize"})

type _TsHazardScanner = Callable[[str, ts.Node, bool], list[RawDiscrepancy]]


def _type_node_has_any(type_node: ts.Node | None) -> bool:
    if type_node is None:
        return False
    return any(
        sub.type == "predefined_type" and node_text(sub).strip() == "any"
        for sub in iter_cst_nodes(type_node)
    )


def _ts_callee_name(call_node: ts.Node) -> str:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return ""
    return re.sub(r"\s+", "", node_text(fn))[:256]


def _check_ts_catch_node(file_path: str, node: ts.Node, _is_test: bool) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    body = node.child_by_field_name("body")
    if body is None or not body.named_children:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-001",
                location=node_span(file_path, node),
                message="Empty 'catch' block silently discards runtime fault.",
                remediation="Propagate error, rethrow with '{ cause }', or log context.",
            )
        )
    for sub in iter_local_cst_nodes(node, TS_SCOPE_BOUNDARIES):
        if sub.type == "throw_statement":
            txt = node_text(sub)
            if "new Error" in txt and "cause" not in txt:
                out.append(
                    RawDiscrepancy(
                        code_id="JBC-FLT-002",
                        location=node_span(file_path, sub),
                        message="Replacement Error thrown in catch block without '{ cause }'.",
                        remediation="Preserve error chain via 'new Error(msg, { cause: err })'.",
                    )
                )
    return out


def _check_ts_throw_node(file_path: str, node: ts.Node, is_test: bool) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if not is_test:
        low = node_text(node).lower()
        if "not implemented" in low or "todo" in low:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-VRF-002",
                    location=node_span(file_path, node),
                    message="Unimplemented stub throws placeholder Error in production module.",
                    remediation="Implement full contract logic or declare abstract method.",
                )
            )
    if not node.named_children:
        return out
    thrown = node.named_children[0]
    txt = node_text(thrown).strip()
    if thrown.type in {"string", "number", "true", "false"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=node_span(file_path, node),
                message="Throwing primitive literal instead of an Error instance.",
                remediation="Throw a typed Error subclass with a diagnostic message.",
            )
        )
    elif txt in {"new Error()", 'new Error("")', "new Error('')"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=node_span(file_path, node),
                message="Error constructed and thrown with an empty diagnostic message.",
                remediation="Provide an actionable error message naming the failed invariant.",
            )
        )
    return out


def _check_ts_while_node(file_path: str, node: ts.Node, _is_test: bool) -> list[RawDiscrepancy]:
    body = node.child_by_field_name("body")
    if body is not None and ".tick(" in node_text(body):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-FLT-004",
            location=node_span(file_path, node),
            message="While loop omits a deterministic '.tick()' step budget guard.",
            remediation="Guard condition loops with an explicit LoopBudget.tick() ceiling.",
        )
    ]


def _check_ts_for_node(file_path: str, node: ts.Node, _is_test: bool) -> list[RawDiscrepancy]:
    if node.child_by_field_name("condition") is not None:
        return []
    body = node.child_by_field_name("body")
    if body is not None and ".tick(" in node_text(body):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-FLT-004",
            location=node_span(file_path, node),
            message="Unbounded 'for (;;)' loop omits a '.tick()' step budget guard.",
            remediation="Bound loop iterations explicitly or tick a step budget.",
        )
    ]


def _check_ts_debugger_node(file_path: str, node: ts.Node, _is_test: bool) -> list[RawDiscrepancy]:
    return [
        RawDiscrepancy(
            code_id="JBC-OBS-001",
            location=node_span(file_path, node),
            message="Interactive 'debugger' trap left in TypeScript source.",
            remediation="Remove debugger statement before certification.",
        )
    ]


def _check_ts_as_node(file_path: str, node: ts.Node, _is_test: bool) -> list[RawDiscrepancy]:
    if not _type_node_has_any(node):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-DBC-002",
            location=node_span(file_path, node),
            message="Type assertion 'as any' erases static type safety.",
            remediation="Narrow unknown values via type guards or strict schemas.",
        )
    ]


def _check_ts_unary_node(file_path: str, node: ts.Node, _is_test: bool) -> list[RawDiscrepancy]:
    if not node_text(node).lstrip().startswith("void "):
        return []
    if not any(c.type == "call_expression" for c in node.named_children):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-FLT-005",
            location=node_span(file_path, node),
            message="Fire-and-forget 'void asyncCall()' detaches promise lifecycle.",
            remediation="Await promise within structured Promise.all / AbortSignal scope.",
        )
    ]


def _check_ts_assign_node(file_path: str, node: ts.Node, is_test: bool) -> list[RawDiscrepancy]:
    if is_test:
        return []
    left = node.child_by_field_name("left")
    if left is None or not node_text(left).startswith("process.env"):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-OBS-003",
            location=node_span(file_path, node),
            message="Direct mutation of process.env in production TypeScript code.",
            remediation="Pass explicit immutable configuration objects instead.",
        )
    ]


def _check_ts_declarator_node(
    file_path: str,
    node: ts.Node,
    _is_test: bool,
) -> list[RawDiscrepancy]:
    n_node = node.child_by_field_name("name")
    v_node = node.child_by_field_name("value")
    if n_node is None or v_node is None or v_node.type != "string":
        return []
    ident = node_text(n_node).strip()
    lit = unquote_cst_string(node_text(v_node))
    if not (ident and lit and is_sensitive_identifier(ident) and lit.lower() != ident.lower()):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-SEC-002",
            location=node_span(file_path, node, ident),
            message=f"Sensitive identifier '{ident}' assigned a hardcoded literal.",
            remediation="Inject credentials at runtime rather than embedding literals.",
        )
    ]


def _first_named_arg(args_node: ts.Node | None) -> ts.Node | None:
    if args_node is None or not args_node.named_children:
        return None
    return args_node.named_children[0]


def _is_ts_dynamic_sql_expr(expr: ts.Node) -> bool:
    txt = node_text(expr)
    return (expr.type == "template_string" and "${" in txt) or (
        expr.type == "binary_expression" and "+" in txt
    )


def _enclosing_ts_block(node: ts.Node) -> ts.Node | None:
    curr = node.parent
    budget = LoopBudget(max_steps=128)
    while curr is not None:
        budget.tick()
        if curr.type in {"statement_block", "program"}:
            return curr
        curr = curr.parent
    return None


def _is_ts_sql_alias_interpolated(call: ts.Node, first_arg: ts.Node) -> bool:
    if _is_ts_dynamic_sql_expr(first_arg):
        return True
    if first_arg.type != "identifier":
        return False
    ident = node_text(first_arg).strip()
    block = _enclosing_ts_block(call)
    if not ident or block is None:
        return False
    for sub in iter_local_cst_nodes(block, TS_SCOPE_BOUNDARIES):
        if sub.type == "variable_declarator":
            n_node = sub.child_by_field_name("name")
            v_node = sub.child_by_field_name("value")
            if (
                n_node is not None
                and v_node is not None
                and node_text(n_node).strip() == ident
                and _is_ts_dynamic_sql_expr(v_node)
            ):
                return True
    return False


def _check_ts_sql_or_exec_call(
    span: SourceSpan,
    call: ts.Node,
    callee: str,
    first_arg: ts.Node | None,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if callee in {"eval", "Function", "child_process.exec", "exec"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-001",
                location=span,
                message=f"Prohibited dynamic execution or shell sink '{callee}()'.",
                remediation="Use structured parsers or argument-array execFile/spawn.",
            )
        )
    tail = callee.rsplit(".", maxsplit=1)[-1]
    if (
        tail in _TS_SQL_METHODS
        and first_arg is not None
        and _is_ts_sql_alias_interpolated(call, first_arg)
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-001",
                location=span,
                message=f"SQL sink '.{tail}()' receives a dynamically interpolated query.",
                remediation="Use parameterized query placeholders with bound parameter arrays.",
            )
        )
    return out


def _check_ts_io_and_fetch_call(
    span: SourceSpan,
    callee: str,
    args_txt: str,
    first_arg: ts.Node | None,
    is_test: bool,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    raw_arg_types = {"string", "template_string", "identifier", "binary_expression"}
    if callee == "fetch":
        if "signal" not in args_txt and "AbortSignal" not in args_txt:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-003",
                    location=span,
                    message="Network call 'fetch()' omits an explicit AbortSignal timeout.",
                    remediation="Pass '{ signal: AbortSignal.timeout(ms) }' to fetch().",
                )
            )
        if not is_test and first_arg is not None and first_arg.type in raw_arg_types:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-005",
                    location=span,
                    message="Network sink 'fetch()' invoked with unvalidated raw URL string.",
                    remediation="Validate URL scheme/host via 'new URL(...)' allowlist first.",
                )
            )
    if (
        callee in {"execSync", "spawnSync", "child_process.execSync", "child_process.spawnSync"}
        and "timeout" not in args_txt
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-003",
                location=span,
                message=f"Blocking process call '{callee}()' omits an explicit timeout.",
                remediation="Pass '{ timeout: ms }' in options.",
            )
        )
    if not is_test and callee in TS_RAW_FS_SINKS:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-006",
                location=span,
                message=f"Unbounded file slurp '{callee}()' risks memory exhaustion (OOM).",
                remediation="Enforce a stat size cap or bounded byte reader before reading.",
            )
        )
        if first_arg is not None and first_arg.type in raw_arg_types:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-005",
                    location=span,
                    message=f"Filesystem sink '{callee}()' invoked with uncontained raw path.",
                    remediation="Canonicalize and verify containment under an anchor root.",
                )
            )
    return out


def _check_ts_json_or_path_call(
    span: SourceSpan,
    call: ts.Node,
    callee: str,
    first_arg: ts.Node | None,
    is_test: bool,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if not is_test and callee == "JSON.parse":
        parent_node = call.parent
        parent_txt = node_text(parent_node) if parent_node is not None else ""
        stripped = parent_txt.replace("JSON.parse(", "")
        if ".parse(" not in stripped and ".safeParse(" not in parent_txt:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-004",
                    location=span,
                    message="Untyped 'JSON.parse()' produces unvalidated any-typed trees.",
                    remediation="Validate parsed payload immediately through a strict schema.",
                )
            )
    if (
        not is_test
        and callee in _TS_UNANCHORED_PATH_CALLS
        and first_arg is not None
        and first_arg.type == "string"
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-006",
                location=span,
                message=f"Path constructor '{callee}()' invoked with hardcoded literal.",
                remediation="Anchor paths from import.meta.url, process.cwd(), or root.",
            )
        )
    return out


def _check_ts_obs_vrf_call(
    span: SourceSpan,
    callee: str,
    first_arg: ts.Node | None,
    is_test: bool,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if not is_test and callee in {"console.log", "console.debug", "console.info"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-OBS-001",
                location=span,
                message=f"Unstructured '{callee}()' call in production TypeScript module.",
                remediation="Emit structured telemetry records or return typed domain values.",
            )
        )
    if callee == "Math.random":
        out.append(
            RawDiscrepancy(
                code_id="JBC-OBS-003",
                location=span,
                message="Unseeded 'Math.random()' compromises determinism and security.",
                remediation="Inject a seeded PRNG or use crypto.getRandomValues / randomBytes.",
            )
        )
    if callee in {"it.skip", "test.skip", "describe.skip"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-VRF-004",
                location=span,
                message=f"Skipped test '{callee}()' bypasses dynamic verification.",
                remediation="Remove .skip or register a time-bounded CodeVariance.",
            )
        )
    if callee.endswith(".catch") and first_arg is not None:
        cb_body = first_arg.child_by_field_name("body")
        if cb_body is not None and cb_body.type == "statement_block" and not cb_body.named_children:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-001",
                    location=span,
                    message="Promise '.catch(() => {})' silently swallows rejected fault.",
                    remediation="Handle rejection explicitly or propagate a chained Error.",
                )
            )
    return out


def _check_ts_call_node(file_path: str, call: ts.Node, is_test: bool) -> list[RawDiscrepancy]:
    callee = _ts_callee_name(call)
    args_node = call.child_by_field_name("arguments")
    args_txt = node_text(args_node) if args_node is not None else "()"
    first_arg = _first_named_arg(args_node)
    span = node_span(file_path, call)
    return [
        *_check_ts_sql_or_exec_call(span, call, callee, first_arg),
        *_check_ts_io_and_fetch_call(span, callee, args_txt, first_arg, is_test),
        *_check_ts_json_or_path_call(span, call, callee, first_arg, is_test),
        *_check_ts_obs_vrf_call(span, callee, first_arg, is_test),
    ]


_TS_NODE_SCANNERS: Final[Mapping[str, _TsHazardScanner]] = MappingProxyType(
    {
        "catch_clause": _check_ts_catch_node,
        "throw_statement": _check_ts_throw_node,
        "while_statement": _check_ts_while_node,
        "for_statement": _check_ts_for_node,
        "debugger_statement": _check_ts_debugger_node,
        "as_expression": _check_ts_as_node,
        "unary_expression": _check_ts_unary_node,
        "assignment_expression": _check_ts_assign_node,
        "variable_declarator": _check_ts_declarator_node,
        "call_expression": _check_ts_call_node,
    }
)


@jezz_callable
def scan_typescript_cst_hazards(
    file_path: IdentifierStr,
    root: GuardedTsNode,
    *,
    is_test: bool = False,
) -> list[RawDiscrepancy]:
    """Scan a TypeScript CST ``root`` for Articles I-VI node-level discrepancies."""
    out: list[RawDiscrepancy] = []
    for node in iter_cst_nodes(root):
        scanner = _TS_NODE_SCANNERS.get(node.type)
        if scanner is not None:
            out.extend(scanner(file_path, node, is_test))
    return out
