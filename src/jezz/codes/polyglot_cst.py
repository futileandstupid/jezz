"""Shared bounded tree-sitter CST traversal, SonarSource complexity, and security scanners."""

import re
from collections.abc import Iterator, Sequence
from collections.abc import Set as AbstractSet
from typing import Annotated, Final

import tree_sitter as ts
from pydantic import BaseModel, Field

from jezz.codes.ast_utils import CommentToken, RawDiscrepancy, SourceSpan
from jezz.codes.polyglot_ir import GuardedTsNode
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_FROZENSET_VALIDATOR,
    BoundedText,
    IdentifierStr,
    LoopBudget,
    NonNegativeInt,
    jezz_callable,
    jezz_generator,
)

_MAX_CST_WALK_STEPS: Final[int] = 250_000
_MAX_ELIF_LINKS: Final[int] = 1

_SENSITIVE_IDENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|_)(?:password|passwd|secret|api_key|apikey|access_token|auth_token|"
    r"private_key|client_secret|bearer_token|ssn)(?:$|_)|"
    r"(?:Password|Passwd|Secret|ApiKey|APIKey|AccessToken|AuthToken|PrivateKey|"
    r"ClientSecret|BearerToken)$",
)
_NON_SECRET_PREFIXES: Final[tuple[str, ...]] = (
    "method_",
    "rpc_",
    "route_",
    "event_",
    "metric_",
)
_NON_SECRET_SUFFIXES: Final[tuple[str, ...]] = (
    "_env",
    "_env_var",
    "_var",
    "_header",
    "_key_name",
    "_field_name",
    "_param_name",
    "_id",
    "_ids",
    "_prefix",
    "_get",
    "_store",
    "_delete",
    "_list",
    "_count",
    "_type",
    "_kind",
    "_status",
    "_mode",
    "_path",
)
_CREDENTIAL_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
)
_HARDCODED_FS_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r"/(?:Users|home|root|tmp|var|etc|opt|private|Volumes|mnt|srv|workspace)(?:/|$)|"
    r"~/|"
    r"[A-Za-z]:[\\/]"
    r")"
)
_POLYGLOT_DEFERRED_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?://+|/\*+|\*+|#)\s*(?:"
    r"(?:TODO|FIXME|XXX|HACK)(?:\([^)]*\))?(?:\s*:|\s+(?!list\b|items?\b|panel\b|widget\b)|$)|"
    r"(?i:todo|fixme|xxx|hack|placeholder)(?:\([^)]*\))?\s*:"
    r")"
)


class CstComplexitySpec(BaseModel):
    """Language CST node-type configuration for SonarSource Cognitive Complexity."""

    model_config = STRICT_FROZEN_CONFIG

    nesting_branch_types: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=64),
    ]
    else_clause_types: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=32),
    ] = frozenset()
    if_types: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=32),
    ] = frozenset()
    binary_expr_types: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=32),
    ] = frozenset()
    boolean_operators: Annotated[
        frozenset[BoundedText],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=16),
    ] = frozenset({"&&", "||", "??"})
    statement_types: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=128),
    ]
    scope_boundary_types: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=64),
    ]


class CstConditionalSpec(BaseModel):
    """Language CST node-type configuration for JBC-COG-004 conditional hygiene."""

    model_config = STRICT_FROZEN_CONFIG

    if_type: IdentifierStr
    consequence_field: IdentifierStr = "consequence"
    alternative_field: IdentifierStr = "alternative"
    terminal_jump_types: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=32),
    ]


@jezz_callable
def node_text(node: GuardedTsNode) -> BoundedText:
    """Decode UTF-8 text of ``node`` bounded safely to ``BoundedText`` capacity."""
    raw = node.text
    if raw is None:
        return ""
    return raw.decode("utf-8", errors="replace")[:65_536]


@jezz_callable
def node_span(
    file_path: IdentifierStr,
    node: GuardedTsNode,
    symbol: BoundedText = "",
) -> SourceSpan:
    """Build a 1-based line, 0-based column ``SourceSpan`` from a ``tree_sitter.Node``."""
    return SourceSpan(
        file_path=file_path,
        line=max(1, node.start_point.row + 1),
        column=max(0, node.start_point.column),
        symbol=symbol[:256],
    )


@jezz_generator
def iter_cst_nodes(root: GuardedTsNode) -> Iterator[ts.Node]:
    """Yield ``root`` and all CST descendants in pre-order under a deterministic ``LoopBudget``."""
    stack: list[ts.Node] = [root]
    budget = LoopBudget(max_steps=_MAX_CST_WALK_STEPS)
    while stack:
        budget.tick()
        current = stack.pop()
        yield current
        if current.children:
            stack.extend(reversed(current.children))


@jezz_generator
def iter_local_cst_nodes(
    root: GuardedTsNode,
    scope_boundary_types: Annotated[frozenset[IdentifierStr], Field(max_length=64)],
) -> Iterator[ts.Node]:
    """Yield CST descendants of ``root`` without crossing nested callable/type boundaries."""
    stack: list[ts.Node] = list(reversed(root.children))
    budget = LoopBudget(max_steps=_MAX_CST_WALK_STEPS)
    while stack:
        budget.tick()
        current = stack.pop()
        yield current
        if current.type in scope_boundary_types:
            continue
        if current.children:
            stack.extend(reversed(current.children))


@jezz_callable
def extract_cst_comments(
    root: GuardedTsNode,
    comment_types: Annotated[frozenset[IdentifierStr], Field(max_length=16)],
) -> Annotated[tuple[CommentToken, ...], Field(max_length=50_000)]:
    """Extract all lexical comment tokens from ``root`` without matching string literals."""
    comments: list[CommentToken] = []
    for node in iter_cst_nodes(root):
        if node.type in comment_types:
            comments.append(
                CommentToken(
                    line=max(1, node.start_point.row + 1),
                    column=max(0, node.start_point.column),
                    text=node_text(node),
                )
            )
    return tuple(comments[:50_000])


@jezz_callable
def unquote_cst_string(raw_literal: BoundedText) -> BoundedText:
    """Strip outer string/raw-string delimiters from a TypeScript, Rust, or Go literal."""
    trimmed = raw_literal.strip()
    if trimmed.startswith(("r#", 'r"', "br#", 'br"')):
        hash_trimmed = trimmed.lstrip("br")
        hashes = len(hash_trimmed) - len(hash_trimmed.lstrip("#"))
        marker = "#" * hashes
        prefix_len = (2 if trimmed.startswith("br") else 1) + hashes + 1
        suffix = f'"{marker}'
        if trimmed.endswith(suffix) and len(trimmed) >= prefix_len + len(suffix):
            return trimmed[prefix_len : len(trimmed) - len(suffix)]
    if len(trimmed) >= 2 and trimmed[0] in {'"', "'", "`"} and trimmed[-1] == trimmed[0]:
        return trimmed[1:-1]
    return trimmed


def _binary_operator_token(node: ts.Node) -> str:
    op_node = node.child_by_field_name("operator")
    if op_node is not None:
        return node_text(op_node).strip()
    for child in node.children:
        if not child.is_named:
            txt = node_text(child).strip()
            if txt:
                return txt
    return ""


def _is_else_if_continuation(node: ts.Node, spec: CstComplexitySpec) -> bool:
    parent = node.parent
    if node.type not in spec.if_types or parent is None:
        return False
    if parent.type in spec.else_clause_types:
        return True
    return parent.type in spec.if_types and parent.child_by_field_name("alternative") == node


def _else_wraps_if(node: ts.Node, spec: CstComplexitySpec) -> bool:
    return any(child.type in spec.if_types for child in node.named_children)


def _boolean_op_increment(node: ts.Node, spec: CstComplexitySpec) -> int:
    if node.type not in spec.binary_expr_types:
        return 0
    op = _binary_operator_token(node)
    if op not in spec.boolean_operators:
        return 0
    parent = node.parent
    if (
        parent is not None
        and parent.type in spec.binary_expr_types
        and _binary_operator_token(parent) == op
    ):
        return 0
    return 1


def _score_cst_node(
    node: ts.Node,
    depth: int,
    spec: CstComplexitySpec,
) -> tuple[int, int, int]:
    """Return ``(cc_delta, child_depth, is_stmt)`` for one CST node."""
    is_stmt = 1 if node.type in spec.statement_types else 0
    if _is_else_if_continuation(node, spec):
        return 1, depth, is_stmt
    if node.type in spec.else_clause_types:
        if _else_wraps_if(node, spec):
            return 0, depth, 0
        return 1, depth, 0
    if node.type in spec.nesting_branch_types:
        return 1 + depth, depth + 1, is_stmt
    return _boolean_op_increment(node, spec), depth, is_stmt


@jezz_callable
def compute_cst_callable_metrics(
    body_node: GuardedTsNode,
    spec: CstComplexitySpec,
    *,
    is_recursive: bool = False,
) -> tuple[NonNegativeInt, NonNegativeInt, NonNegativeInt]:
    """Compute ``(cognitive_complexity, max_nesting_depth, statement_count)`` iteratively."""
    cc = 1 if is_recursive else 0
    max_depth = 0
    stmt_count = 0
    root_depth: int = 0
    stack: list[tuple[ts.Node, int]] = [
        (child, root_depth) for child in reversed(body_node.children)
    ]
    budget = LoopBudget(max_steps=_MAX_CST_WALK_STEPS)
    while stack:
        budget.tick()
        curr, depth = stack.pop()
        if curr.type in spec.scope_boundary_types:
            continue
        delta, next_depth, stmt_inc = _score_cst_node(curr, depth, spec)
        cc += delta
        stmt_count += stmt_inc
        if next_depth > max_depth:
            max_depth = next_depth
        for child in reversed(curr.children):
            stack.append((child, next_depth))
    return cc, max_depth, stmt_count


def _unwrap_else_if(alt_node: ts.Node, if_type: str) -> ts.Node | None:
    if alt_node.type == if_type:
        return alt_node
    for child in alt_node.named_children:
        if child.type == if_type:
            return child
    return None


def _count_cst_elif_links(head: ts.Node, spec: CstConditionalSpec) -> tuple[int, set[int]]:
    count = 0
    child_if_ids: set[int] = set()
    current = head
    budget = LoopBudget(max_steps=1024)
    while True:
        budget.tick()
        alt = current.child_by_field_name(spec.alternative_field)
        if alt is None:
            break
        nxt_if = _unwrap_else_if(alt, spec.if_type)
        if nxt_if is None:
            break
        count += 1
        child_if_ids.add(nxt_if.id)
        current = nxt_if
    return count, child_if_ids


def _last_named_statement(block: ts.Node) -> ts.Node | None:
    named = block.named_children
    if not named:
        return None
    last = named[-1]
    if last.type == "statement_list" and last.named_children:
        return last.named_children[-1]
    return last


def _consequence_ends_in_jump(if_node: ts.Node, spec: CstConditionalSpec) -> bool:
    consequence = if_node.child_by_field_name(spec.consequence_field)
    if consequence is None:
        return False
    last_stmt = _last_named_statement(consequence)
    if last_stmt is None:
        return False
    if last_stmt.type in spec.terminal_jump_types:
        return True
    if last_stmt.type == "expression_statement" and last_stmt.named_children:
        inner = last_stmt.named_children[0]
        if inner.type == "macro_invocation" and node_text(inner).startswith("panic!"):
            return True
    return last_stmt.type == "macro_invocation" and node_text(last_stmt).startswith("panic!")


@jezz_callable
def check_cst_cog_004_conditionals(
    file_path: IdentifierStr,
    root: GuardedTsNode,
    spec: CstConditionalSpec,
) -> list[RawDiscrepancy]:
    """Check ``root`` for JBC-COG-004 (<=1 else-if branch & no redundant else after jump)."""
    if_nodes = [n for n in iter_cst_nodes(root) if n.type == spec.if_type]
    seen_elif_children: set[int] = set()
    discrepancies: list[RawDiscrepancy] = []
    for node in if_nodes:
        span = node_span(file_path, node)
        if node.id not in seen_elif_children:
            elif_count, child_ids = _count_cst_elif_links(node, spec)
            seen_elif_children |= child_ids
            if elif_count > _MAX_ELIF_LINKS:
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-COG-004",
                        location=span,
                        message=(
                            f"Conditional chain has {elif_count} 'else if' branches "
                            f"(limit {_MAX_ELIF_LINKS})."
                        ),
                        remediation=(
                            "Refactor to an immutable dispatch table or exhaustive switch/match."
                        ),
                    )
                )
        alt = node.child_by_field_name(spec.alternative_field)
        if alt is not None and _consequence_ends_in_jump(node, spec):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-004",
                    location=span,
                    message="Redundant 'else'/'else if' block after terminal jump statement.",
                    remediation="Flatten conditional into early guard clauses.",
                )
            )
    return discrepancies


@jezz_callable
def is_sensitive_identifier(name: BoundedText) -> bool:
    """Return True iff ``name`` denotes a secret, credential, token, or private key."""
    cleaned = name.strip().lstrip("_")
    low = cleaned.lower()
    if not low or low.startswith(_NON_SECRET_PREFIXES) or low.endswith(_NON_SECRET_SUFFIXES):
        return False
    return _SENSITIVE_IDENT_RE.search(cleaned) is not None


@jezz_callable
def contains_credential_literal(value: BoundedText) -> bool:
    """Return True iff ``value`` matches a PEM private key or high-confidence API token prefix."""
    return _CREDENTIAL_LITERAL_RE.search(value) is not None


@jezz_callable
def is_hardcoded_host_path(value: BoundedText) -> bool:
    """Return True iff ``value`` is a hardcoded OS/host filesystem path literal (JBC-SEC-006)."""
    return (
        "\n" not in value and "://" not in value and _HARDCODED_FS_PATH_RE.search(value) is not None
    )


@jezz_callable
def scan_cst_string_hazards(
    file_path: IdentifierStr,
    root: GuardedTsNode,
    string_types: Annotated[frozenset[IdentifierStr], Field(max_length=16)],
    *,
    skip_node_ids: Annotated[AbstractSet[NonNegativeInt], WIRE_FROZENSET_VALIDATOR] = frozenset(),
) -> list[RawDiscrepancy]:
    """Scan CST string literals for JBC-SEC-002 credentials and JBC-SEC-006 host paths."""
    discrepancies: list[RawDiscrepancy] = []
    for node in iter_cst_nodes(root):
        if node.type not in string_types or node.id in skip_node_ids:
            continue
        val = unquote_cst_string(node_text(node))
        if not val:
            continue
        span = node_span(file_path, node)
        if contains_credential_literal(val):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-002",
                    location=span,
                    message="Source contains a hardcoded credential or private-key literal.",
                    remediation="Load secrets from a vault or redacted runtime secret provider.",
                )
            )
        if is_hardcoded_host_path(val):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-006",
                    location=span,
                    message=f"Hardcoded host/OS filesystem path literal '{val[:64]}'.",
                    remediation=(
                        "Derive paths dynamically from runtime anchors, module URLs, or "
                        "temporary/injected workspace roots."
                    ),
                )
            )
    return discrepancies


@jezz_callable
def scan_deferred_comments(
    file_path: IdentifierStr,
    comments: Annotated[Sequence[CommentToken], Field(max_length=50_000)],
) -> list[RawDiscrepancy]:
    """Scan lexical comments for JBC-VRF-002 deferred TODO/FIXME/XXX/HACK placeholders."""
    discrepancies: list[RawDiscrepancy] = []
    for comment in comments:
        if _POLYGLOT_DEFERRED_COMMENT_RE.search(comment.text.strip()) is not None:
            preview = comment.text.strip()[:200]
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-VRF-002",
                    location=SourceSpan(
                        file_path=file_path,
                        line=comment.line,
                        column=comment.column,
                    ),
                    message=f"Deferred placeholder comment '{preview}' in source.",
                    remediation="Complete deferred work or track external scope via ELICIT/FRAME.",
                )
            )
    return discrepancies


@jezz_callable
def scan_suppression_comments(
    file_path: IdentifierStr,
    comments: Annotated[Sequence[CommentToken], Field(max_length=50_000)],
    suppression_pattern: BoundedText,
) -> list[RawDiscrepancy]:
    """Scan lexical comments for JBC-VRF-004 gate-silencing directives."""
    compiled = re.compile(suppression_pattern, re.IGNORECASE)
    discrepancies: list[RawDiscrepancy] = []
    for comment in comments:
        if compiled.search(comment.text) is not None:
            preview = comment.text.strip()[:200]
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-VRF-004",
                    location=SourceSpan(
                        file_path=file_path,
                        line=comment.line,
                        column=comment.column,
                    ),
                    message=f"Gate-silencing directive '{preview}' requires variance.",
                    remediation="Fix underlying diagnostic or attach a signed CodeVariance.",
                )
            )
    return discrepancies
