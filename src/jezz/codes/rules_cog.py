"""Inspectors for Article IV (COG — Complexity, Dispatch Tables & Class Duty Isolation)."""

import ast
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import Final

from jezz.codes.ast_utils import (
    GuardedSourceUnit,
    RawDiscrepancy,
    SourceSpan,
    SourceUnit,
    is_type_checking_guard,
)
from jezz.codes.cohesion import (
    MAX_BEHAVIORAL_FIELDS_LIMIT,
    MAX_CLASS_WMC_LIMIT,
    MAX_LCOM4_COMPONENTS,
    MAX_PUBLIC_METHODS_LIMIT,
    ClassCohesionMetrics,
    compute_class_cohesion,
)
from jezz.codes.complexity import (
    ResolvedComplexityLimits,
    compute_callable_metrics,
)
from jezz.codes.dep_graph import find_tarjan_cycles
from jezz.guards import IdentifierStr, LoopBudget, RecursionBudget, jezz_callable

_MAX_ELIF_BRANCHES: Final[int] = 1
_DEFAULT_LIMITS: Final[ResolvedComplexityLimits] = ResolvedComplexityLimits()


@jezz_callable
def check_cog_001(
    unit: GuardedSourceUnit,
    *,
    limits: ResolvedComplexityLimits | None = None,
) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-COG-001 (Cognitive Complexity ceiling)."""
    eff = limits or _DEFAULT_LIMITS
    cc_limit = eff.max_cognitive_complexity
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        metrics = compute_callable_metrics(
            ctx.node,
            qualname=ctx.qualname,
            is_method=ctx.enclosing_class is not None,
        )
        if metrics.cognitive_complexity > cc_limit:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-001",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=metrics.line,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=(
                        f"Callable '{ctx.qualname}' has Cognitive Complexity "
                        f"{metrics.cognitive_complexity} (limit {cc_limit})."
                    ),
                    remediation="Decompose nested branches/loops into focused helper functions.",
                )
            )
    return discrepancies


@jezz_callable
def check_cog_002(
    unit: GuardedSourceUnit,
    *,
    limits: ResolvedComplexityLimits | None = None,
) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-COG-002 (nesting, parameter arity, and statement span)."""
    eff = limits or _DEFAULT_LIMITS
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        m = compute_callable_metrics(
            ctx.node,
            qualname=ctx.qualname,
            is_method=ctx.enclosing_class is not None,
        )
        if m.max_nesting_depth > eff.max_nesting_depth:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-002",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=m.line,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=(
                        f"Callable '{ctx.qualname}' has control-flow nesting depth "
                        f"{m.max_nesting_depth} (limit {eff.max_nesting_depth})."
                    ),
                    remediation="Use early guard clauses or extract nested control blocks.",
                )
            )
        if m.parameter_count > eff.max_parameter_count:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-002",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=m.line,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=(
                        f"Callable '{ctx.qualname}' takes {m.parameter_count} parameters "
                        f"(limit {eff.max_parameter_count})."
                    ),
                    remediation="Group cohesive parameters into a frozen Pydantic model.",
                )
            )
        if m.statement_count > eff.max_statement_count:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-002",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=m.line,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=(
                        f"Callable '{ctx.qualname}' spans {m.statement_count} AST statements "
                        f"(limit {eff.max_statement_count})."
                    ),
                    remediation="Split multi-step procedure into single-purpose helpers.",
                )
            )
    return discrepancies


class _RuntimeImportVisitor(ast.NodeVisitor):
    """Depth-bounded collector of runtime imports, wildcards, and _private imports."""

    def __init__(self) -> None:
        self.imported_targets: list[str] = []
        self.wildcards: list[ast.ImportFrom] = []
        self.private_imports: list[tuple[ast.ImportFrom, str]] = []
        self._budget = RecursionBudget(max_depth=200)

    def visit(self, node: ast.AST) -> None:
        with self._budget:
            super().visit(node)

    def visit_If(self, node: ast.If) -> None:
        if not is_type_checking_guard(node.test):
            for stmt in node.body:
                self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imported_targets.append(alias.name)

    def _record_from_aliases(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.wildcards.append(node)
                continue
            is_dunder = alias.name.startswith("__") and alias.name.endswith("__")
            if alias.name.startswith("_") and not is_dunder:
                self.private_imports.append((node, alias.name))
            if node.level == 0 and node.module:
                self.imported_targets.append(f"{node.module}.{alias.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self.imported_targets.append(node.module)
        self._record_from_aliases(node)


def _format_bounded_list(items: Sequence[str], *, limit: int = 6, sep: str = ", ") -> str:
    if len(items) <= limit:
        return sep.join(items)
    shown = sep.join(items[:limit])
    return f"{shown}{sep}... (+{len(items) - limit} more)"


def _resolve_relative_import_base(unit: SourceUnit, level: int) -> str:
    base_parts = (
        unit.module_name.split(".")
        if unit.file_path.endswith("__init__.py")
        else unit.module_name.split(".")[:-1]
    )
    up_steps = max(0, level - 1)
    if up_steps > len(base_parts):
        return ""
    kept = base_parts[: len(base_parts) - up_steps]
    return ".".join(kept)


def _is_intra_package_submodule_import(
    unit: SourceUnit,
    imp_node: ast.ImportFrom,
    priv_name: str,
    known_mods: set[str],
) -> bool:
    if not unit.module_name:
        return False
    unit_root = unit.module_name.split(".", maxsplit=1)[0]
    if imp_node.level == 0 and imp_node.module:
        target_mod = f"{imp_node.module}.{priv_name}"
        return target_mod in known_mods and imp_node.module.split(".", maxsplit=1)[0] == unit_root
    if imp_node.level >= 1:
        rel_base = _resolve_relative_import_base(unit, imp_node.level)
        prefix = (
            f"{rel_base}.{imp_node.module}"
            if (rel_base and imp_node.module)
            else (rel_base or imp_node.module or "")
        )
        candidate = f"{prefix}.{priv_name}" if prefix else priv_name
        return candidate in known_mods and candidate.split(".", maxsplit=1)[0] == unit_root
    return False


def _scan_cog_003_units(
    units: Sequence[SourceUnit],
) -> tuple[list[RawDiscrepancy], dict[str, SourceUnit], dict[str, frozenset[str]]]:
    discrepancies: list[RawDiscrepancy] = []
    units_by_mod = {u.module_name: u for u in units if u.module_name}
    known_mods = set(units_by_mod)
    graph: dict[str, frozenset[str]] = {}

    for unit in units:
        visitor = _RuntimeImportVisitor()
        visitor.visit(unit.tree)
        for wc in visitor.wildcards:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-003",
                    location=SourceSpan(
                        file_path=unit.file_path, line=wc.lineno, column=wc.col_offset
                    ),
                    message=f"Wildcard import 'from {wc.module or '.'} import *' pollutes scope.",
                    remediation="Import explicit public symbol names.",
                )
            )
        for imp_node, priv_name in visitor.private_imports:
            if _is_intra_package_submodule_import(unit, imp_node, priv_name, known_mods):
                continue
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-003",
                    location=SourceSpan(
                        file_path=unit.file_path, line=imp_node.lineno, column=imp_node.col_offset
                    ),
                    message=(
                        f"Cross-module import of private symbol '{priv_name}' breaches "
                        "sealed module enclosure."
                    ),
                    remediation="Promote symbol to a @jezz_callable public API or keep internal.",
                )
            )
        if unit.module_name:
            deps = {
                target
                for target in visitor.imported_targets
                if target in known_mods and target != unit.module_name
            }
            graph[unit.module_name] = frozenset(deps)
    return discrepancies, units_by_mod, graph


@jezz_callable
def check_cog_003(units: Sequence[GuardedSourceUnit]) -> list[RawDiscrepancy]:
    """Inspect ``units`` for JBC-COG-003 (acyclic graph, no wildcards, no _private imports)."""
    discrepancies, units_by_mod, graph = _scan_cog_003_units(units)
    for cycle in find_tarjan_cycles(graph):
        anchor_unit = units_by_mod[cycle[0]]
        cycle_text = _format_bounded_list(cycle, limit=8, sep=" <-> ")
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-COG-003",
                location=SourceSpan(file_path=anchor_unit.file_path, line=1, symbol=cycle[0]),
                message=f"Cyclic runtime module dependency detected: {cycle_text}.",
                remediation="Break import cycle by extracting shared types into a lower layer.",
            )
        )
    return discrepancies


@jezz_callable
def check_cog_003_for_paths(
    units: Sequence[GuardedSourceUnit],
    target_paths: AbstractSet[IdentifierStr],
) -> list[RawDiscrepancy]:
    """Run whole-graph JBC-COG-003 over ``units`` and filter findings touching ``target_paths``."""
    per_unit, units_by_mod, graph = _scan_cog_003_units(units)
    discrepancies = [d for d in per_unit if d.location.file_path in target_paths]
    for cycle in find_tarjan_cycles(graph):
        touched_mods = [mod for mod in cycle if units_by_mod[mod].file_path in target_paths]
        if not touched_mods:
            continue
        anchor_mod = touched_mods[0]
        anchor_unit = units_by_mod[anchor_mod]
        cycle_text = _format_bounded_list(cycle, limit=8, sep=" <-> ")
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-COG-003",
                location=SourceSpan(file_path=anchor_unit.file_path, line=1, symbol=anchor_mod),
                message=f"Cyclic runtime module dependency detected: {cycle_text}.",
                remediation="Break import cycle by extracting shared types into a lower layer.",
            )
        )
    return discrepancies


def _count_elif_links(head: ast.If) -> int:
    count = 0
    current = head
    budget = LoopBudget(max_steps=1024)
    while len(current.orelse) == 1:
        budget.tick()
        nxt = current.orelse[0]
        if not isinstance(nxt, ast.If):
            break
        count += 1
        current = nxt
    return count


def _collect_elif_child_ids(nodes: Sequence[ast.AST]) -> set[int]:
    ids: set[int] = set()
    for node in nodes:
        if isinstance(node, ast.If) and len(node.orelse) == 1:
            first_else = node.orelse[0]
            if isinstance(first_else, ast.If):
                ids.add(id(first_else))
    return ids


def _is_mutable_dispatch_dict(stmt: ast.stmt) -> tuple[bool, int, int]:
    val = stmt.value if isinstance(stmt, ast.Assign | ast.AnnAssign) else None
    if not isinstance(val, ast.Dict) or not val.values:
        return False, 1, 0
    has_callable_ref = any(
        isinstance(v, ast.Name | ast.Attribute | ast.Lambda) for v in val.values if v is not None
    )
    return has_callable_ref, stmt.lineno, stmt.col_offset


@jezz_callable
def check_cog_004(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-COG-004 (dispatch tables, <=1 elif, no post-jump else)."""
    discrepancies: list[RawDiscrepancy] = []
    elif_child_ids = _collect_elif_child_ids(unit.all_nodes)

    for node in unit.all_nodes:
        if not isinstance(node, ast.If):
            continue
        if id(node) not in elif_child_ids:
            elif_count = _count_elif_links(node)
            if elif_count > _MAX_ELIF_BRANCHES:
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-COG-004",
                        location=SourceSpan(
                            file_path=unit.file_path, line=node.lineno, column=node.col_offset
                        ),
                        message=(
                            f"Conditional chain has {elif_count} 'elif' branches "
                            f"(limit {_MAX_ELIF_BRANCHES})."
                        ),
                        remediation="Refactor to a MappingProxyType dispatch table or match.",
                    )
                )
        if (
            node.orelse
            and node.body
            and isinstance(node.body[-1], ast.Return | ast.Raise | ast.Break | ast.Continue)
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-004",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message="Redundant 'else'/'elif' block after terminal jump statement.",
                    remediation="Flatten conditional into early guard clauses.",
                )
            )

    for stmt in unit.tree.body:
        is_mut_board, line, col = _is_mutable_dispatch_dict(stmt)
        if is_mut_board:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-004",
                    location=SourceSpan(file_path=unit.file_path, line=line, column=col),
                    message="Module-level callable dispatch dict is mutable.",
                    remediation="Wrap dispatch table in types.MappingProxyType({...}).",
                )
            )
    return discrepancies


def _evaluate_class_metrics(
    file_path: str,
    col: int,
    m: ClassCohesionMetrics,
) -> list[RawDiscrepancy]:
    span = SourceSpan(file_path=file_path, line=m.line, column=col, symbol=m.class_name)
    discrepancies: list[RawDiscrepancy] = []
    if m.is_schema_or_value and m.performs_external_io:
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=f"Value/schema class '{m.class_name}' performs external I/O.",
                remediation="Keep BaseModel/value schemas pure and move I/O to a service/function.",
            )
        )
    if m.wmc > MAX_CLASS_WMC_LIMIT:
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=f"Class '{m.class_name}' has WMC {m.wmc} (limit {MAX_CLASS_WMC_LIMIT}).",
                remediation="Decompose excess class complexity into focused collaborator units.",
            )
        )
    if m.is_schema_or_value:
        return discrepancies
    if m.lcom4 > MAX_LCOM4_COMPONENTS:
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=(
                    f"Class '{m.class_name}' has LCOM4 = {m.lcom4} disconnected state islands "
                    f"(limit {MAX_LCOM4_COMPONENTS})."
                ),
                remediation="Split disconnected method/field duties into separate classes.",
            )
        )
    if m.stateless_methods:
        joined = _format_bounded_list(m.stateless_methods, limit=6)
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=f"Class '{m.class_name}' contains stateless instance method(s): {joined}.",
                remediation="Extract methods that never touch 'self' to module-level functions.",
            )
        )
    if m.instance_field_count > MAX_BEHAVIORAL_FIELDS_LIMIT:
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=(
                    f"Class '{m.class_name}' holds {m.instance_field_count} instance attributes "
                    f"(limit {MAX_BEHAVIORAL_FIELDS_LIMIT})."
                ),
                remediation="Reduce collaborator fan-out by decomposing sub-services.",
            )
        )
    if m.public_method_count > MAX_PUBLIC_METHODS_LIMIT:
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=(
                    f"Class '{m.class_name}' exposes {m.public_method_count} public methods "
                    f"(limit {MAX_PUBLIC_METHODS_LIMIT})."
                ),
                remediation="Split broad class interface into single-duty capabilities.",
            )
        )
    return discrepancies


@jezz_callable
def check_cog_005(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-COG-005 (LCOM4 == 1, role purity, & class panel capacity)."""
    discrepancies: list[RawDiscrepancy] = []
    for cls_node in unit.classes:
        if unit.is_test_file and cls_node.name.startswith("Test"):
            continue
        metrics = compute_class_cohesion(cls_node)
        discrepancies.extend(_evaluate_class_metrics(unit.file_path, cls_node.col_offset, metrics))
    return discrepancies
