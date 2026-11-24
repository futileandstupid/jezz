"""Rust (.rs) CST hazard scanners for JBC v1.2 inspection."""

import re
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from types import MappingProxyType
from typing import Annotated, Final

import tree_sitter as ts
from pydantic import Field

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
from jezz.guards import IdentifierStr, LoopBudget, NonNegativeInt, jezz_callable

RS_SCOPE_BOUNDARIES: Final[frozenset[str]] = frozenset(
    {"function_item", "closure_expression", "struct_item", "impl_item", "trait_item", "mod_item"}
)

_RS_SUPPRESSION_RE: Final[str] = r"#!?\[\s*allow\s*\("
_RS_UNBOUNDED_FS_CALLS: Final[frozenset[str]] = frozenset(
    {"std::fs::read_to_string", "fs::read_to_string", "std::fs::read", "fs::read"}
)
RS_RAW_PATH_SINKS: Final[frozenset[str]] = frozenset(
    {
        "std::fs::read_to_string",
        "fs::read_to_string",
        "std::fs::read",
        "fs::read",
        "std::fs::remove_file",
        "fs::remove_file",
        "std::fs::remove_dir_all",
        "fs::remove_dir_all",
        "File::open",
        "std::fs::File::open",
    }
)
_RS_UNANCHORED_PATH_CTORS: Final[frozenset[str]] = frozenset(
    {"Path::new", "std::path::Path::new", "PathBuf::from", "std::path::PathBuf::from"}
)

type _RsNodeScanner = Callable[[str, ts.Node, bool], list[RawDiscrepancy]]


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


def _check_rs_unsafe_node(file_path: str, node: ts.Node, _in_test: bool) -> list[RawDiscrepancy]:
    return [
        RawDiscrepancy(
            code_id="JBC-SEC-001",
            location=node_span(file_path, node),
            message="Unwaived 'unsafe' block bypasses Rust borrow/memory safety guarantees.",
            remediation="Eliminate unsafe block or register a narrow safety-justified variance.",
        )
    ]


def _check_rs_loop_node(file_path: str, node: ts.Node, _in_test: bool) -> list[RawDiscrepancy]:
    body = node.child_by_field_name("body")
    if body is not None and ".tick(" in node_text(body):
        return []
    label = node.type.replace("_expression", "")
    return [
        RawDiscrepancy(
            code_id="JBC-FLT-004",
            location=node_span(file_path, node),
            message=f"Rust '{label}' omits a '.tick()' step budget.",
            remediation="Use a bounded iterator range or tick an explicit step budget.",
        )
    ]


def _check_rs_let_node(file_path: str, node: ts.Node, in_test: bool) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    pat = node.child_by_field_name("pattern")
    val = node.child_by_field_name("value")
    if (
        not in_test
        and pat is not None
        and node_text(pat).strip() == "_"
        and val is not None
        and val.type == "call_expression"
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-001",
                location=node_span(file_path, node),
                message="Wildcard 'let _ = call()' silently discards Result/error outcome.",
                remediation="Propagate error via '?' or match explicitly on Ok/Err.",
            )
        )
    if pat is not None and val is not None and val.type in {"string_literal", "raw_string_literal"}:
        ident = node_text(pat).replace("mut ", "").strip()
        lit = unquote_cst_string(node_text(val))
        if ident and lit and is_sensitive_identifier(ident) and lit.lower() != ident.lower():
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-002",
                    location=node_span(file_path, node, ident),
                    message=f"Sensitive identifier '{ident}' assigned a hardcoded literal.",
                    remediation="Load secret at runtime rather than embedding string literals.",
                )
            )
    return out


def _check_rs_static_node(file_path: str, node: ts.Node, _in_test: bool) -> list[RawDiscrepancy]:
    if not any(c.type == "mutable_specifier" for c in node.children):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-OBS-002",
            location=node_span(file_path, node),
            message="'static mut' introduces unsynchronized global mutable state.",
            remediation="Thread explicit owned/borrowed state rather than static mut.",
        )
    ]


def _check_rs_attr_node(file_path: str, node: ts.Node, _in_test: bool) -> list[RawDiscrepancy]:
    txt = node_text(node).strip()
    if re.search(_RS_SUPPRESSION_RE, txt) is None:
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-VRF-004",
            location=node_span(file_path, node),
            message=f"Compiler/Clippy lint suppression '{txt[:120]}' requires variance.",
            remediation="Resolve underlying lint diagnostic or attach a signed CodeVariance.",
        )
    ]


def _check_rs_test_macro_tautology(span: SourceSpan, full: str) -> list[RawDiscrepancy]:
    if full in {"assert!(true)", "assert!(false)"}:
        return [
            RawDiscrepancy(
                code_id="JBC-VRF-001",
                location=span,
                message=f"Vacuous test assertion '{full}'.",
                remediation="Assert a falsifiable behavioral property of the target unit.",
            )
        ]
    eq_m = re.match(r"^assert_eq!\(([^,]+),([^,)]+)\)$", full)
    if eq_m is not None and eq_m.group(1) == eq_m.group(2):
        return [
            RawDiscrepancy(
                code_id="JBC-VRF-001",
                location=span,
                message=f"Reflexive tautology '{full[:80]}' compares identical expressions.",
                remediation="Compare computed result against independent expected value.",
            )
        ]
    return []


def _check_rs_macro_node(file_path: str, node: ts.Node, in_test: bool) -> list[RawDiscrepancy]:
    m_node = node.child_by_field_name("macro")
    m_name = node_text(m_node).strip() if m_node is not None else ""
    full = re.sub(r"\s+", "", node_text(node))
    span = node_span(file_path, node)
    out: list[RawDiscrepancy] = []

    if m_name == "dbg" or (not in_test and m_name in {"println", "eprintln"}):
        out.append(
            RawDiscrepancy(
                code_id="JBC-OBS-001",
                location=span,
                message=f"Unstructured '{m_name}!' macro in production Rust module.",
                remediation="Emit structured tracing events or return typed Result values.",
            )
        )
    if not in_test and m_name in {"todo", "unimplemented"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-VRF-002",
                location=span,
                message=f"Unimplemented '{m_name}!()' stub macro in production Rust code.",
                remediation="Implement complete contract behavior before occupancy.",
            )
        )
    if not in_test and m_name == "panic" and full in {"panic!()", 'panic!("")'}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=span,
                message="Empty 'panic!()' omits actionable failure diagnostics.",
                remediation="Return a typed Err(...) or provide invariant failure details.",
            )
        )
    if in_test and m_name in {"assert", "assert_eq"}:
        out.extend(_check_rs_test_macro_tautology(span, full))
    return out


def _check_rs_call_err_handling(
    span: SourceSpan,
    callee: str,
    args_txt: str,
    in_test: bool,
) -> list[RawDiscrepancy]:
    if in_test or "." not in callee:
        return []
    out: list[RawDiscrepancy] = []
    tail = callee.rsplit(".", maxsplit=1)[-1].rsplit("::", maxsplit=1)[-1]
    if tail in {"unwrap", "expect"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-DBC-003",
                location=span,
                message=f"'.{tail}()' in production Rust converts Option/Result into panic.",
                remediation="Propagate via '?' or handle None/Err branches exhaustively.",
            )
        )
    if tail == "ok" and args_txt == "()":
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-001",
                location=span,
                message="Result '.ok()' silently discards error payload without logging/chaining.",
                remediation="Propagate error via '?' or inspect/wrap the Err variant.",
            )
        )
    if tail == "map_err" and "|_|" in args_txt:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=span,
                message="'.map_err(|_| ...)' discards underlying source error cause.",
                remediation="Preserve causal chain by wrapping source error in #[source] variant.",
            )
        )
    return out


def _enclosing_rs_fn_body(node: ts.Node) -> ts.Node | None:
    curr = node.parent
    budget = LoopBudget(max_steps=128)
    while curr is not None:
        budget.tick()
        if curr.type in {"function_item", "closure_expression"}:
            return curr.child_by_field_name("body")
        curr = curr.parent
    return None


def _rs_arg_is_formatted_sql_alias(call: ts.Node, args_txt: str) -> bool:
    if "format!(" in args_txt:
        return True
    args_node = call.child_by_field_name("arguments")
    if args_node is None or not args_node.named_children:
        return False
    first_ids = _identifiers_in_rs_node(args_node.named_children[0])
    body = _enclosing_rs_fn_body(call)
    if not first_ids or body is None:
        return False
    for sub in iter_local_cst_nodes(body, RS_SCOPE_BOUNDARIES):
        if sub.type == "let_declaration":
            lhs_ids = _identifiers_in_rs_node(sub.child_by_field_name("pattern"))
            val = sub.child_by_field_name("value")
            if (lhs_ids & first_ids) and val is not None and "format!(" in node_text(val):
                return True
    return False


def _check_rs_call_injection(
    span: SourceSpan,
    call: ts.Node,
    callee: str,
    args_txt: str,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    if callee in {"std::mem::transmute", "mem::transmute"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-001",
                location=span,
                message="Prohibited 'mem::transmute' reinterprets raw bits unsafely.",
                remediation="Use safe TryFrom/From conversions or checked byte parsers.",
            )
        )
    if callee.endswith((".execute", ".query")) and _rs_arg_is_formatted_sql_alias(call, args_txt):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-001",
                location=span,
                message="SQL sink receives dynamically formatted query via 'format!(...)'.",
                remediation="Use parameterized SQL placeholders and bound parameters.",
            )
        )
    if callee in {"Command::new", "std::process::Command::new"}:
        parent2 = call.parent.parent if call.parent is not None else None
        chain_txt = node_text(parent2) if parent2 is not None else args_txt
        if ('"sh"' in args_txt or '"bash"' in args_txt) and '"-c"' in chain_txt:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-001",
                    location=span,
                    message='Shell call \'Command::new("sh").arg("-c")\' enables injection.',
                    remediation="Invoke target binary directly with explicit argument vectors.",
                )
            )
    return out


def _check_rs_call_resources(
    span: SourceSpan,
    call: ts.Node,
    callee: str,
    in_test: bool,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    tail = callee.rsplit(".", maxsplit=1)[-1].rsplit("::", maxsplit=1)[-1]
    if callee in {"reqwest::get", "TcpStream::connect", "std::net::TcpStream::connect"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-003",
                location=span,
                message=f"Blocking/network call '{callee}()' omits an explicit timeout.",
                remediation="Use Client::builder().timeout(...) or connect_timeout(...).",
            )
        )
    if callee in {"std::mem::forget", "mem::forget"}:
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-003",
                location=span,
                message="'mem::forget()' intentionally leaks RAII Drop cleanup.",
                remediation="Allow deterministic Drop execution at scope exit.",
            )
        )
    if (
        callee in {"tokio::spawn", "thread::spawn", "std::thread::spawn"}
        and call.parent is not None
        and call.parent.type == "expression_statement"
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-005",
                location=span,
                message=f"Detached '{callee}()' drops JoinHandle without supervision.",
                remediation="Track tasks inside a tokio::task::JoinSet or thread::scope.",
            )
        )
    is_slurp = callee in _RS_UNBOUNDED_FS_CALLS or tail in {"read_to_end", "read_to_string"}
    if not in_test and is_slurp and ".take(" not in node_text(call):
        out.append(
            RawDiscrepancy(
                code_id="JBC-FLT-006",
                location=span,
                message=f"Unbounded read '{callee}()' risks OOM on oversized inputs.",
                remediation="Cap reader via 'reader.take(MAX_BYTES)' before reading.",
            )
        )
    return out


def _check_rs_call_paths_and_env(
    span: SourceSpan,
    callee: str,
    args_node: ts.Node | None,
    in_test: bool,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    first_arg = args_node.named_children[0] if args_node and args_node.named_children else None
    if (
        not in_test
        and callee in RS_RAW_PATH_SINKS
        and first_arg is not None
        and first_arg.type
        in {"string_literal", "raw_string_literal", "identifier", "macro_invocation"}
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-005",
                location=span,
                message=f"Filesystem sink '{callee}()' invoked with uncontained raw path.",
                remediation="Canonicalize and verify path prefix containment before access.",
            )
        )
    if (
        not in_test
        and callee in _RS_UNANCHORED_PATH_CTORS
        and first_arg is not None
        and first_arg.type in {"string_literal", "raw_string_literal"}
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-006",
                location=span,
                message=f"Path constructor '{callee}()' invoked with hardcoded literal.",
                remediation="Derive paths from env::current_dir() or CARGO_MANIFEST_DIR.",
            )
        )
    env_mut_calls = {
        "std::env::set_var",
        "env::set_var",
        "std::env::remove_var",
        "env::remove_var",
    }
    if callee in {"rand::random", "thread_rng"} or (not in_test and callee in env_mut_calls):
        out.append(
            RawDiscrepancy(
                code_id="JBC-OBS-003",
                location=span,
                message=f"Global nondeterminism or process-env mutation via '{callee}()'.",
                remediation="Inject seeded StdRng or immutable config structs instead.",
            )
        )
    return out


def _check_rs_call_node(file_path: str, call: ts.Node, in_test: bool) -> list[RawDiscrepancy]:
    callee = _rs_callee_name(call)
    args_node = call.child_by_field_name("arguments")
    args_txt = node_text(args_node) if args_node is not None else "()"
    span = node_span(file_path, call)
    return [
        *_check_rs_call_err_handling(span, callee, args_txt, in_test),
        *_check_rs_call_injection(span, call, callee, args_txt),
        *_check_rs_call_resources(span, call, callee, in_test),
        *_check_rs_call_paths_and_env(span, callee, args_node, in_test),
    ]


_RS_NODE_SCANNERS: Final[Mapping[str, _RsNodeScanner]] = MappingProxyType(
    {
        "unsafe_block": _check_rs_unsafe_node,
        "loop_expression": _check_rs_loop_node,
        "while_expression": _check_rs_loop_node,
        "let_declaration": _check_rs_let_node,
        "static_item": _check_rs_static_node,
        "attribute_item": _check_rs_attr_node,
        "inner_attribute_item": _check_rs_attr_node,
        "macro_invocation": _check_rs_macro_node,
        "call_expression": _check_rs_call_node,
    }
)


@jezz_callable
def scan_rust_cst_hazards(
    file_path: IdentifierStr,
    root: GuardedTsNode,
    test_ids: Annotated[AbstractSet[NonNegativeInt], Field(max_length=250_000)],
    *,
    unit_is_test: bool = False,
) -> list[RawDiscrepancy]:
    """Scan a Rust CST ``root`` for Articles I-VI node-level discrepancies."""
    out: list[RawDiscrepancy] = []
    for node in iter_cst_nodes(root):
        scanner = _RS_NODE_SCANNERS.get(node.type)
        if scanner is not None:
            in_test = unit_is_test or (node.id in test_ids)
            out.extend(scanner(file_path, node, in_test))
    return out
