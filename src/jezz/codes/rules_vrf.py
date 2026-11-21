"""Inspectors for Article VI (VRF — Anti-Theatre Verification, Grounding & Watchdogs)."""

import ast
import re
import sys
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import Final

from jezz.codes.ast_utils import (
    CallableContext,
    RawDiscrepancy,
    SourceSpan,
    SourceUnit,
    dotted_expr_name,
    is_abstract_or_overload,
    is_node_visitor_class,
    iter_local_scope_nodes,
)
from jezz.codes.complexity import strip_leading_docstring
from jezz.codes.permits import GateAttestation, GateKind
from jezz.guards import IdentifierStr, jezz_callable

_DEFERRED_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^#\s*(?:"
    r"(?:TODO|FIXME|XXX|HACK)(?:\([^)]*\))?(?:\s*:|\s+(?!list\b|items?\b|panel\b|widget\b)|$)|"
    r"(?i:todo|fixme|xxx|hack|placeholder)(?:\([^)]*\))?\s*:"
    r")"
)
_SUPPRESSION_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"#\s*(?:noqa|type:\s*ignore|pyright:\s*ignore|pragma:\s*no\s*cover)\b",
    re.IGNORECASE,
)
_SKIP_MARKERS: Final[frozenset[str]] = frozenset({"pytest.mark.skip", "unittest.skip", "mark.skip"})


def _vacuous_assert_reason(test: ast.expr) -> str | None:
    if isinstance(test, ast.Tuple):
        return "Assertion expression is a tuple literal (always evaluates to True in Python)."
    if isinstance(test, ast.Constant | ast.List | ast.Dict | ast.Set):
        return "Assertion expression is a constant or collection literal."
    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        right = test.comparators[0]
        if isinstance(test.left, ast.Constant) and isinstance(right, ast.Constant):
            return "Assertion compares two constant literals."
        if ast.dump(test.left) == ast.dump(right):
            return "Assertion compares identical expressions (reflexive tautology)."
    return None


def _callable_has_real_verification(ctx: CallableContext) -> bool:
    for node in iter_local_scope_nodes(ctx.node):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            tail = dotted_expr_name(node.func).rsplit(".", maxsplit=1)[-1].lstrip("_")
            if tail in {"raises", "warns", "fail", "assert"} or tail.startswith(
                ("assert_", "verify_", "validate_", "check_", "expect_")
            ):
                return True
    return False


@jezz_callable
def check_vrf_001(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect test ``unit`` for JBC-VRF-001 (anti-theatre & non-tautological assertions)."""
    if not unit.is_test_file:
        return []
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        if ctx.node.name.startswith("test_") and not _callable_has_real_verification(ctx):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-VRF-001",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=ctx.node.lineno,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=f"Test '{ctx.qualname}' contains zero assertions or raises checks.",
                    remediation="Assert concrete postconditions or expected exceptions.",
                )
            )
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Assert):
            reason = _vacuous_assert_reason(node.test)
            if reason is not None:
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-VRF-001",
                        location=SourceSpan(
                            file_path=unit.file_path, line=node.lineno, column=node.col_offset
                        ),
                        message=reason,
                        remediation="Assert a falsifiable behavioral outcome on the target unit.",
                    )
                )
    return discrepancies


def _is_stub_body(stmts: Sequence[ast.stmt]) -> bool:
    if len(stmts) != 1:
        return False
    only = stmts[0]
    if isinstance(only, ast.Pass):
        return True
    if isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant):
        return only.value.value is Ellipsis
    if isinstance(only, ast.Raise) and only.exc is not None:
        tail = dotted_expr_name(only.exc).rsplit(".", maxsplit=1)[-1]
        return tail == "NotImplementedError"
    return False


@jezz_callable
def check_vrf_002(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-VRF-002 (no hollow stubs or deferred TODO/FIXME comments)."""
    discrepancies: list[RawDiscrepancy] = []
    if not unit.is_test_file:
        for ctx in unit.callables:
            is_visitor_hook = (
                ctx.enclosing_class is not None
                and is_node_visitor_class(ctx.enclosing_class)
                and ctx.node.name.startswith("visit_")
            )
            if is_abstract_or_overload(ctx) or is_visitor_hook:
                continue
            if _is_stub_body(strip_leading_docstring(ctx.node.body)):
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-VRF-002",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=ctx.node.lineno,
                            column=ctx.node.col_offset,
                            symbol=ctx.qualname,
                        ),
                        message=f"Non-abstract callable '{ctx.qualname}' is an unimplemented stub.",
                        remediation="Implement callable contract or mark @abstractmethod.",
                    )
                )
    for comment in unit.comments:
        if _DEFERRED_COMMENT_RE.search(comment.text) is not None:
            preview = comment.text.strip()[:200]
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-VRF-002",
                    location=SourceSpan(
                        file_path=unit.file_path, line=comment.line, column=comment.column
                    ),
                    message=f"Deferred placeholder comment '{preview}' in source.",
                    remediation="Complete deferred work or track external scope via ELICIT/FRAME.",
                )
            )
    return discrepancies


def _iter_absolute_import_roots(tree: ast.Module) -> list[tuple[str, int, int]]:
    roots: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append((alias.name.split(".", maxsplit=1)[0], node.lineno, node.col_offset))
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append((node.module.split(".", maxsplit=1)[0], node.lineno, node.col_offset))
    return roots


@jezz_callable
def check_vrf_003(
    unit: SourceUnit,
    *,
    runtime_roots: AbstractSet[IdentifierStr],
    dev_roots: AbstractSet[IdentifierStr],
    workspace_roots: AbstractSet[IdentifierStr],
) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-VRF-003 (epistemic import & declared dependency grounding)."""
    base_allowed = sys.stdlib_module_names | set(workspace_roots) | set(runtime_roots)
    allowed = base_allowed | set(dev_roots) if unit.is_test_file else base_allowed

    discrepancies: list[RawDiscrepancy] = []
    for root_pkg, line, col in _iter_absolute_import_roots(unit.tree):
        if root_pkg in allowed:
            continue
        span = SourceSpan(file_path=unit.file_path, line=line, column=col)
        if not unit.is_test_file and root_pkg in dev_roots:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-VRF-003",
                    location=span,
                    message=f"Production module imports dev-only package '{root_pkg}'.",
                    remediation=f"Promote '{root_pkg}' to [project].dependencies or remove import.",
                )
            )
            continue
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-VRF-003",
                location=span,
                message=(
                    f"Ungrounded or undeclared import '{root_pkg}' (absent from stdlib, "
                    "workspace, and pyproject.toml)."
                ),
                remediation="Provision declared dependency via 'uv add' or remove phantom API.",
            )
        )
    return discrepancies


@jezz_callable
def check_vrf_004(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-VRF-004 (unpermitted gate suppression comments & test skips)."""
    discrepancies: list[RawDiscrepancy] = []
    for comment in unit.comments:
        if _SUPPRESSION_COMMENT_RE.search(comment.text) is not None:
            preview = comment.text.strip()[:200]
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-VRF-004",
                    location=SourceSpan(
                        file_path=unit.file_path, line=comment.line, column=comment.column
                    ),
                    message=f"Gate-silencing directive '{preview}' requires variance.",
                    remediation="Fix underlying diagnostic or attach a signed CodeVariance.",
                )
            )
    for ctx in unit.callables:
        for dec in ctx.node.decorator_list:
            dec_name = dotted_expr_name(dec)
            if any(dec_name.endswith(marker) for marker in _SKIP_MARKERS):
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-VRF-004",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=dec.lineno,
                            column=dec.col_offset,
                            symbol=ctx.qualname,
                        ),
                        message=f"Skipped test '{ctx.qualname}' bypasses dynamic verification.",
                        remediation="Remove @skip or register a time-bounded CodeVariance.",
                    )
                )
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Call) and dotted_expr_name(node.func) == "pytest.skip":
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-VRF-004",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message="Runtime pytest.skip() call bypasses dynamic verification.",
                    remediation="Fix test fixture/environment or attach a signed CodeVariance.",
                )
            )
    return discrepancies


@jezz_callable
def check_vrf_005(attestations: Sequence[GateAttestation]) -> list[RawDiscrepancy]:
    """Verify JBC-VRF-005 (passing static type soundness gate attestation)."""
    has_passing = any(
        a.gate_kind == GateKind.STATIC_TYPES and a.passed and a.exit_code == 0 for a in attestations
    )
    if has_passing:
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-VRF-005",
            location=SourceSpan(file_path="<gate:static_types>", line=1),
            message="Missing or failed static type-verification GateAttestation (e.g., mypy).",
            remediation="Execute 'uv run mypy .' cleanly and attach passing GateAttestation.",
        )
    ]


def _dynamic_attestation_valid(att: GateAttestation) -> bool:
    return (
        att.gate_kind == GateKind.DYNAMIC_SUITE
        and att.passed
        and att.exit_code == 0
        and att.checks_executed >= 1
        and 0.0 < att.duration_ms <= att.max_duration_ms
        and 0 < att.peak_memory_kb <= att.max_memory_kb
    )


@jezz_callable
def check_vrf_006(attestations: Sequence[GateAttestation]) -> list[RawDiscrepancy]:
    """Verify JBC-VRF-006 (passing dynamic suite within duration & memory watchdog ceilings)."""
    if any(_dynamic_attestation_valid(a) for a in attestations):
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-VRF-006",
            location=SourceSpan(file_path="<gate:dynamic_suite>", line=1),
            message="Missing, failed, zero-test, or watchdog-exceeding dynamic GateAttestation.",
            remediation=(
                "Run 'uv run pytest' within duration/memory ceilings and attach GateAttestation."
            ),
        )
    ]
