"""Deterministic AST import, call, topological layer, and symbol-slice graph over SourceUnits."""

import ast
from collections import deque
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Final

import icontract
from pydantic import BaseModel, Field

from jezz.codes.ast_utils import (
    CallableContext,
    SourceUnit,
    dotted_expr_name,
    is_type_checking_guard,
    iter_local_scope_nodes,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    IdentifierStr,
    LineNumber,
    LoopBudget,
    NonNegativeInt,
    PositiveInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
)

_MAX_SLICE_LINES: Final[int] = 240
_MAX_CALLEES_PER_SYMBOL: Final[int] = 128


class SymbolNodeKind(StrEnum):
    """Structural category of a compiled symbol in ``WorkspaceAstGraph``."""

    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    TYPE_ALIAS = "type_alias"


class SymbolGraphEntry(BaseModel):
    """Compiled workspace symbol with file span, signature summary, and outgoing call edges."""

    model_config = STRICT_FROZEN_CONFIG

    qualname: IdentifierStr
    module_name: BoundedText
    file_path: IdentifierStr
    kind: SymbolNodeKind
    start_line: LineNumber
    end_line: LineNumber
    signature_summary: ShortText
    calls: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=512),
    ] = ()


class ModuleGraphEntry(BaseModel):
    """Module vertex in the workspace import DAG with topological layer and symbol inventory."""

    model_config = STRICT_FROZEN_CONFIG

    file_path: IdentifierStr
    module_name: BoundedText
    imports: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    imported_by: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    topological_layer: NonNegativeInt = 0
    public_symbols: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=512),
    ] = ()


def _matches_call_target(callee: str, target_symbol: str) -> bool:
    if callee == target_symbol:
        return True
    tail = callee.rsplit(".", maxsplit=1)[-1]
    target_tail = target_symbol.rsplit(".", maxsplit=1)[-1]
    if "." in target_symbol:
        return callee.endswith(f".{target_symbol}")
    return tail == target_tail


class WorkspaceAstGraph(BaseModel):
    """Deterministic whole-workspace import, call, and symbol property graph."""

    model_config = STRICT_FROZEN_CONFIG

    modules: Annotated[
        tuple[ModuleGraphEntry, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=4096),
    ] = ()
    symbols: Annotated[
        tuple[SymbolGraphEntry, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32_768),
    ] = ()
    has_import_cycles: bool = False

    @jezz_callable
    def find_callers(
        self,
        target_symbol: IdentifierStr,
    ) -> Annotated[tuple[SymbolGraphEntry, ...], Field(max_length=4096)]:
        """Return all workspace symbols whose AST body invokes ``target_symbol``."""
        matched = [
            sym
            for sym in self.symbols
            if any(_matches_call_target(callee, target_symbol) for callee in sym.calls)
        ]
        return tuple(matched[:4096])

    @jezz_callable
    def find_symbols(
        self,
        symbol_query: IdentifierStr,
    ) -> Annotated[tuple[SymbolGraphEntry, ...], Field(max_length=1024)]:
        """Return symbols matching ``symbol_query`` by exact qualname, tail, or module qualname."""
        matched = [
            sym
            for sym in self.symbols
            if sym.qualname == symbol_query
            or sym.qualname.rsplit(".", maxsplit=1)[-1] == symbol_query
            or f"{sym.module_name}.{sym.qualname}" == symbol_query
        ]
        return tuple(matched[:1024])

    @jezz_callable
    def module_neighbors(self, file_or_module: IdentifierStr) -> ModuleGraphEntry | None:
        """Look up a ``ModuleGraphEntry`` by relative ``file_path`` or dotted ``module_name``."""
        for entry in self.modules:
            if file_or_module in (entry.file_path, entry.module_name):
                return entry
        return None

    @jezz_callable
    def to_compact_brief(self, *, max_modules: PositiveInt = 32) -> BoundedText:
        """Render a high-density topological summary of workspace modules, imports, and symbols."""
        if not self.modules:
            return "Workspace AST Graph: 0 Python modules."
        ordered = sorted(self.modules, key=lambda m: (m.topological_layer, m.file_path))
        lines = [
            f"Workspace AST Graph ({len(self.modules)} module(s), "
            f"{len(self.symbols)} symbol(s), cycles={self.has_import_cycles}):"
        ]
        for mod in ordered[:max_modules]:
            deps = list(mod.imports[:8])
            syms = list(mod.public_symbols[:10])
            lines.append(
                f"- [L{mod.topological_layer}] {mod.file_path} ({mod.module_name}): "
                f"imports={deps}, symbols={syms}"
            )
        extra = len(ordered) - max_modules
        if extra > 0:
            lines.append(f"- ... (+{extra} additional module(s))")
        return "\n".join(lines)[:60_000]


def _start_line_with_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    if node.decorator_list:
        return min(node.lineno, *(dec.lineno for dec in node.decorator_list))
    return node.lineno


def _collect_local_callees(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for sub in iter_local_scope_nodes(fn_node):
        if not isinstance(sub, ast.Call):
            continue
        dotted = dotted_expr_name(sub.func)
        if dotted and len(dotted) <= 256 and dotted not in seen:
            seen[dotted] = None
            if len(seen) >= _MAX_CALLEES_PER_SYMBOL:
                break
    return tuple(seen)


def _format_arg_item(arg: ast.arg, *, prefix: str = "", suffix: str = "") -> str:
    ann = f": {ast.unparse(arg.annotation)}" if arg.annotation is not None else ""
    return f"{prefix}{arg.arg}{ann}{suffix}"


def _format_callable_signature(ctx: CallableContext) -> str:
    fn = ctx.node
    prefix = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
    arg_parts = [_format_arg_item(a) for a in fn.args.posonlyargs + fn.args.args]
    if fn.args.vararg is not None:
        arg_parts.append(_format_arg_item(fn.args.vararg, prefix="*"))
    arg_parts.extend(_format_arg_item(a, suffix="=...") for a in fn.args.kwonlyargs)
    if fn.args.kwarg is not None:
        arg_parts.append(_format_arg_item(fn.args.kwarg, prefix="**"))
    ret_str = f" -> {ast.unparse(fn.returns)}" if fn.returns is not None else ""
    raw = f"{prefix} {ctx.qualname}({', '.join(arg_parts)}){ret_str}"
    return raw[:512]


def _format_class_member_item(stmt: ast.stmt) -> str | None:
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        name = stmt.target.id
        if not name.startswith("_") and name != "model_config":
            return f"{name}: {ast.unparse(stmt.annotation)}"
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        first_target = stmt.targets[0]
        if isinstance(first_target, ast.Name):
            name = first_target.id
            if not name.startswith("_") and name != "model_config":
                return f"{name}={ast.unparse(stmt.value)}"
    return None


def _format_class_signature(cls_node: ast.ClassDef) -> str:
    bases = [ast.unparse(b) for b in cls_node.bases[:4]]
    base_str = f"({', '.join(bases)})" if bases else ""
    members = [
        m for stmt in cls_node.body[:16] if (m := _format_class_member_item(stmt)) is not None
    ]
    member_str = f" {{{', '.join(members[:12])}}}" if members else ""
    return f"class {cls_node.name}{base_str}{member_str}"[:512]


def _extract_unit_symbols(unit: SourceUnit) -> list[SymbolGraphEntry]:
    entries: list[SymbolGraphEntry] = []
    for cls_node in unit.classes:
        start_ln = _start_line_with_decorators(cls_node)
        entries.append(
            SymbolGraphEntry(
                qualname=cls_node.name[:256],
                module_name=unit.module_name,
                file_path=unit.file_path,
                kind=SymbolNodeKind.CLASS,
                start_line=start_ln,
                end_line=cls_node.end_lineno or start_ln,
                signature_summary=_format_class_signature(cls_node),
            )
        )
    for stmt in unit.tree.body:
        if isinstance(stmt, ast.TypeAlias):
            entries.append(
                SymbolGraphEntry(
                    qualname=stmt.name.id[:256],
                    module_name=unit.module_name,
                    file_path=unit.file_path,
                    kind=SymbolNodeKind.TYPE_ALIAS,
                    start_line=stmt.lineno,
                    end_line=stmt.end_lineno or stmt.lineno,
                    signature_summary=f"type {stmt.name.id} = {ast.unparse(stmt.value)}"[:512],
                )
            )
    for ctx in unit.callables:
        if ctx.in_type_checking:
            continue
        start_ln = _start_line_with_decorators(ctx.node)
        kind = SymbolNodeKind.METHOD if ctx.enclosing_class is not None else SymbolNodeKind.FUNCTION
        entries.append(
            SymbolGraphEntry(
                qualname=ctx.qualname[:256],
                module_name=unit.module_name,
                file_path=unit.file_path,
                kind=kind,
                start_line=start_ln,
                end_line=ctx.node.end_lineno or start_ln,
                signature_summary=_format_callable_signature(ctx),
                calls=_collect_local_callees(ctx.node),
            )
        )
    return entries


def _resolve_relative_import(
    current_module: str,
    level: int,
    target_mod: str | None,
) -> str:
    if level <= 0:
        return target_mod or ""
    parts = current_module.split(".") if current_module else []
    keep = max(0, len(parts) - level)
    prefix = ".".join(parts[:keep])
    if prefix and target_mod:
        return f"{prefix}.{target_mod}"
    return prefix or (target_mod or "")


def _iter_runtime_import_candidates(unit: SourceUnit) -> list[str]:
    candidates: list[str] = []
    stack: list[ast.AST] = list(reversed(unit.tree.body))
    budget = LoopBudget(max_steps=100_000)
    while stack:
        budget.tick()
        node = stack.pop()
        if isinstance(node, ast.If):
            if not is_type_checking_guard(node.test):
                stack.extend(reversed(node.body))
            stack.extend(reversed(node.orelse))
            continue
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
            continue
        if isinstance(node, ast.ImportFrom):
            base = _resolve_relative_import(unit.module_name, node.level, node.module)
            if base:
                candidates.append(base)
                candidates.extend(
                    f"{base}.{alias.name}" for alias in node.names if alias.name != "*"
                )
            continue
        if isinstance(node, ast.Try | ast.With | ast.AsyncWith | ast.For | ast.AsyncFor):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
    return candidates


def _match_known_module(candidate: str, known_modules: frozenset[str]) -> str:
    probe = candidate
    budget = LoopBudget(max_steps=32)
    while probe:
        budget.tick()
        if probe in known_modules:
            return probe
        if "." not in probe:
            return ""
        probe = probe.rsplit(".", maxsplit=1)[0]
    return ""


def _build_import_edges(
    units: Sequence[SourceUnit],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    known = frozenset(u.module_name for u in units if u.module_name)
    outgoing: dict[str, set[str]] = {u.module_name: set() for u in units if u.module_name}
    incoming: dict[str, set[str]] = {u.module_name: set() for u in units if u.module_name}
    for unit in units:
        if not unit.module_name:
            continue
        for raw in _iter_runtime_import_candidates(unit):
            matched = _match_known_module(raw, known)
            if matched and matched != unit.module_name:
                outgoing[unit.module_name].add(matched)
                incoming[matched].add(unit.module_name)
    return outgoing, incoming


def _compute_topological_layers(
    outgoing: Mapping[str, set[str]],
    incoming: Mapping[str, set[str]],
) -> tuple[dict[str, int], bool]:
    rem_deps: dict[str, int] = {mod: len(deps) for mod, deps in outgoing.items()}
    layers: dict[str, int] = {mod: 0 for mod in outgoing}
    queue: deque[str] = deque(sorted(mod for mod, deg in rem_deps.items() if deg == 0))
    visited = 0
    budget = LoopBudget(max_steps=max(32, len(outgoing) * 8))
    while queue:
        budget.tick()
        curr = queue.popleft()
        visited += 1
        next_layer = layers[curr] + 1
        for dependent in sorted(incoming.get(curr, set())):
            layers[dependent] = max(layers[dependent], next_layer)
            rem_deps[dependent] -= 1
            if rem_deps[dependent] == 0:
                queue.append(dependent)
    has_cycles = visited < len(outgoing)
    return layers, has_cycles


@icontract.ensure(lambda units, result: len(result.modules) == len(units))
@jezz_callable
def build_workspace_ast_graph(
    units: Annotated[Sequence[SourceUnit], Field(max_length=4096)],
) -> WorkspaceAstGraph:
    """Build a deterministic import, call, and topological-layer graph across ``units``."""
    outgoing, incoming = _build_import_edges(units)
    layers, has_cycles = _compute_topological_layers(outgoing, incoming)
    all_symbols: list[SymbolGraphEntry] = []
    module_entries: list[ModuleGraphEntry] = []

    for unit in sorted(units, key=lambda u: u.file_path):
        unit_syms = _extract_unit_symbols(unit)
        all_symbols.extend(unit_syms)
        pub_names = tuple(
            sym.qualname
            for sym in unit_syms
            if "." not in sym.qualname and not sym.qualname.startswith("_")
        )
        mod_key = unit.module_name
        module_entries.append(
            ModuleGraphEntry(
                file_path=unit.file_path,
                module_name=mod_key,
                imports=tuple(sorted(outgoing.get(mod_key, set())))[:256],
                imported_by=tuple(sorted(incoming.get(mod_key, set())))[:256],
                topological_layer=layers.get(mod_key, 0),
                public_symbols=pub_names[:512],
            )
        )

    return WorkspaceAstGraph(
        modules=tuple(module_entries),
        symbols=tuple(all_symbols[:32_768]),
        has_import_cycles=has_cycles,
    )


def _format_line_window(unit: SourceUnit, entry: SymbolGraphEntry) -> str:
    src_lines = unit.source.splitlines()
    lo = max(1, entry.start_line)
    hi = min(len(src_lines), entry.end_line, lo + _MAX_SLICE_LINES - 1)
    snippet = "\n".join(src_lines[lo - 1 : hi])
    return f"# {entry.file_path}:L{lo}-L{hi} ({entry.kind} {entry.qualname})\n{snippet}\n"


@jezz_callable
def extract_symbol_source_slice(
    units: Annotated[Sequence[SourceUnit], Field(max_length=4096)],
    symbol_query: IdentifierStr,
    *,
    file_path: IdentifierStr | None = None,
) -> SourceCodeText:
    """Extract the exact AST source lines for ``symbol_query`` without loading full files."""
    filtered_units = (
        tuple(u for u in units if u.file_path == file_path) if file_path is not None else units
    )
    graph = build_workspace_ast_graph(filtered_units)
    matches = graph.find_symbols(symbol_query)
    if not matches:
        scope_note = f" in '{file_path}'" if file_path is not None else ""
        return f"# NOT_FOUND: Symbol '{symbol_query}' was not found{scope_note}.\n"
    by_path = {u.file_path: u for u in filtered_units}
    rendered: list[str] = []
    for entry in matches[:4]:
        unit = by_path.get(entry.file_path)
        if unit is not None:
            rendered.append(_format_line_window(unit, entry))
    return "\n".join(rendered)[:65_536]
