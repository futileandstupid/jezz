"""Deterministic AST/CST BlueprintGraph extractor over polyglot source units."""

import ast
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import Field

from jezz.codes.ast_graph import WorkspaceAstGraph, build_workspace_ast_graph
from jezz.codes.ast_utils import SourceUnit, is_type_checking_guard
from jezz.codes.blueprint.schemas import (
    BlueprintBus,
    BlueprintGraph,
    BlueprintRoom,
    CircuitBreakerNode,
    ExternalRiser,
    InvariantIdStr,
    InvariantPin,
    PerimeterPortPin,
    PortDirection,
    RiserKind,
    WallFireRating,
    build_blueprint_walls,
    compute_blueprint_netlist_digest,
    compute_room_ranks,
)
from jezz.codes.compartment import classify_unit_placement, resolve_compartment_limits
from jezz.codes.complexity import PermitProfile
from jezz.codes.polyglot_ir import (
    InspectedUnit,
    PolyglotUnit,
    SupportedLanguage,
    detect_language_for_path,
)
from jezz.codes.polyglot_manifests import GO_STDLIB_ROOTS, RUST_BUILTIN_CRATES, TS_BUILTIN_MODULES
from jezz.guards import IdentifierStr, LoopBudget, ShortText, jezz_callable

_PY_SUBPROCESS_ROOTS: Final[frozenset[str]] = frozenset({"subprocess", "pty"})
_PY_NETWORK_ROOTS: Final[frozenset[str]] = frozenset(
    {"aiohttp", "http", "httpx", "requests", "socket", "ssl", "urllib", "websockets"}
)
_PY_DB_ROOTS: Final[frozenset[str]] = frozenset({"asyncpg", "psycopg", "sqlalchemy", "sqlite3"})
_POLY_NET_SINKS: Final[frozenset[str]] = frozenset(
    {"http", "http2", "https", "net", "net/http", "reqwest", "hyper"}
)
_POLY_DB_SINKS: Final[frozenset[str]] = frozenset({"database/sql", "sqlx", "pg", "better-sqlite3"})
_TS_REEXPORT_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^\s*export\s+(?:type\s+)?\{([^}]+)\}"
)
_RUST_PUB_USE_BRACE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^\s*pub\s+use\s+[^{;]+\{([^}]+)\}"
)
_BREAKER_DECORATOR_TAILS: Final[frozenset[str]] = frozenset(
    {"jezz_callable", "jezz_generator", "require", "ensure", "invariant", "validate_call"}
)


@dataclass(frozen=True)
class _RoomScanFacts:
    room_id: str
    member_id: str
    language: SupportedLanguage
    wall_rating: WallFireRating
    module_count: int
    public_symbols: frozenset[str]
    non_private_mod_symbols: frozenset[str]
    barrel_exports: frozenset[str]
    guarded_symbols: frozenset[str]
    breakers: tuple[CircuitBreakerNode, ...]
    risers: tuple[ExternalRiser, ...]


def _unit_lang(unit: InspectedUnit) -> SupportedLanguage:
    return SupportedLanguage.PYTHON if isinstance(unit, SourceUnit) else unit.language


@jezz_callable
def resolve_unit_room_id(
    file_path: IdentifierStr,
    language: SupportedLanguage | None = None,
) -> tuple[IdentifierStr, IdentifierStr, WallFireRating]:
    """Map workspace-relative ``file_path`` to canonical ``(room_id, member_id, wall_rating)``."""
    eff_lang = language if language is not None else detect_language_for_path(file_path)
    pl = classify_unit_placement(file_path, eff_lang)
    if pl.member_id != ".":
        if pl.compartment_dirs:
            return (
                f"{pl.member_id}/{pl.compartment_dirs[0]}"[:256],
                pl.member_id,
                WallFireRating.COMPARTMENT_WALL,
            )
        return pl.member_id, pl.member_id, WallFireRating.WORKSPACE_FIREWALL
    pkg_name = pl.package_key.split(":", maxsplit=1)[1]
    if pkg_name == "root" and pl.compartment_dirs:
        depth = 2 if pl.compartment_dirs[0] in {"pkg", "internal", "cmd"} else 1
        return (
            "/".join(pl.compartment_dirs[:depth])[:256],
            pl.member_id,
            WallFireRating.COMPARTMENT_WALL,
        )
    if pl.compartment_dirs:
        return (
            f"{pkg_name}/{pl.compartment_dirs[0]}"[:256],
            pl.member_id,
            WallFireRating.COMPARTMENT_WALL,
        )
    return pkg_name, pl.member_id, WallFireRating.PACKAGE_LOBBY


def _decorator_tail_names(decorators: Sequence[ast.expr]) -> set[str]:
    tails: set[str] = set()
    for dec in decorators:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            tails.add(target.id)
        elif isinstance(target, ast.Attribute):
            tails.add(target.attr)
    return tails


def _is_strict_pydantic_class(cls_node: ast.ClassDef) -> bool:
    for stmt in cls_node.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
        ):
            txt = ast.unparse(stmt.value)
            if "STRICT_FROZEN_CONFIG" in txt or "forbid" in txt:
                return True
    return "invariant" in _decorator_tail_names(cls_node.decorator_list)


def _dunder_all_expr(stmt: ast.stmt) -> ast.expr | None:
    if isinstance(stmt, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets
    ):
        return stmt.value
    if (
        isinstance(stmt, ast.AnnAssign)
        and isinstance(stmt.target, ast.Name)
        and stmt.target.id == "__all__"
    ):
        return stmt.value
    return None


def _extract_py_dunder_all(tree: ast.Module) -> set[str] | None:
    for stmt in tree.body:
        val = _dunder_all_expr(stmt)
        if isinstance(val, ast.List | ast.Tuple | ast.Set):
            return {
                elt.value[:256]
                for elt in val.elts
                if isinstance(elt, ast.Constant)
                and isinstance(elt.value, str)
                and elt.value
                and not elt.value.startswith("_")
            }
    return None


def _consume_py_init_stmt(stmt: ast.stmt, exports: set[str]) -> None:
    if isinstance(stmt, ast.ImportFrom):
        for alias in stmt.names:
            bound = alias.asname or alias.name
            if bound != "*" and not bound.startswith("_"):
                exports.add(bound[:256])
        return
    if isinstance(stmt, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and not (
        stmt.name.startswith("_")
    ):
        exports.add(stmt.name[:256])


def _extract_py_init_exports(unit: SourceUnit) -> set[str]:
    explicit_all = _extract_py_dunder_all(unit.tree)
    if explicit_all is not None:
        return explicit_all
    exports: set[str] = set()
    for stmt in unit.tree.body:
        _consume_py_init_stmt(stmt, exports)
    return exports


def _extract_brace_specifiers(raw_items: str) -> set[str]:
    names: set[str] = set()
    for token in raw_items.split(","):
        cleaned = token.strip().removeprefix("type ").strip()
        if not cleaned:
            continue
        exported = cleaned.split(" as ")[-1].strip()
        if exported and exported != "self" and not exported.startswith("_"):
            names.add(exported[:256])
    return names


def _extract_polyglot_barrel_exports(unit: PolyglotUnit) -> set[str]:
    fname = PurePosixPath(unit.file_path).name
    stem = PurePosixPath(unit.file_path).stem
    is_barrel = (unit.language == SupportedLanguage.TYPESCRIPT and stem == "index") or (
        unit.language == SupportedLanguage.RUST and fname in {"lib.rs", "mod.rs"}
    )
    if not is_barrel:
        return set()
    exports = {d.name for d in unit.types if d.is_public} | {
        fn.name for fn in unit.callables if fn.is_public and not fn.is_method
    }
    pattern = (
        _TS_REEXPORT_BLOCK_RE
        if unit.language == SupportedLanguage.TYPESCRIPT
        else _RUST_PUB_USE_BRACE_RE
    )
    for match in pattern.finditer(unit.source):
        exports |= _extract_brace_specifiers(match.group(1))
    return exports


def _collect_py_unit_breakers_and_symbols(
    room_id: str, unit: SourceUnit
) -> tuple[set[str], set[str], list[CircuitBreakerNode]]:
    pub_syms: set[str] = set()
    guarded: set[str] = set()
    breakers: list[CircuitBreakerNode] = []
    for cls_node in unit.classes:
        if cls_node.name.startswith("_"):
            continue
        name = cls_node.name[:256]
        pub_syms.add(name)
        if _is_strict_pydantic_class(cls_node):
            guarded.add(name)
            breakers.append(
                CircuitBreakerNode(
                    breaker_id=f"{room_id}::cb:{name}"[:256],
                    room_id=room_id,
                    symbol_name=name,
                    guard_kind="strict_schema",
                )
            )
    for ctx in unit.callables:
        if ctx.in_type_checking or ctx.enclosing_class is not None or ctx.node.name.startswith("_"):
            continue
        fn_name = ctx.node.name[:256]
        pub_syms.add(fn_name)
        matched = _decorator_tail_names(ctx.node.decorator_list) & _BREAKER_DECORATOR_TAILS
        if matched:
            kind = "jezz_callable" if "jezz_callable" in matched else sorted(matched)[0]
            guarded.add(fn_name)
            breakers.append(
                CircuitBreakerNode(
                    breaker_id=f"{room_id}::cb:{fn_name}"[:256],
                    room_id=room_id,
                    symbol_name=fn_name,
                    guard_kind=kind[:256],
                )
            )
    return pub_syms, guarded, breakers


def _collect_poly_type_breakers(
    room_id: str, unit: PolyglotUnit
) -> tuple[set[str], set[str], list[CircuitBreakerNode]]:
    pub_syms: set[str] = set()
    guarded: set[str] = set()
    breakers: list[CircuitBreakerNode] = []
    for decl in unit.types:
        if not decl.is_public:
            continue
        pub_syms.add(decl.name)
        if not decl.is_schema_or_value or decl.is_strict_schema:
            guarded.add(decl.name)
        if decl.is_schema_or_value and decl.is_strict_schema:
            breakers.append(
                CircuitBreakerNode(
                    breaker_id=f"{room_id}::cb:{decl.name}"[:256],
                    room_id=room_id,
                    symbol_name=decl.name,
                    guard_kind="strict_schema",
                )
            )
    return pub_syms, guarded, breakers


def _collect_poly_unit_breakers_and_symbols(
    room_id: str, unit: PolyglotUnit
) -> tuple[set[str], set[str], list[CircuitBreakerNode]]:
    pub_syms, guarded, breakers = _collect_poly_type_breakers(room_id, unit)
    for fn in unit.callables:
        if not fn.is_public or fn.is_method or fn.is_test_callable:
            continue
        pub_syms.add(fn.name)
        if fn.has_boundary_contract and not fn.is_abstract_or_stub:
            guarded.add(fn.name)
            breakers.append(
                CircuitBreakerNode(
                    breaker_id=f"{room_id}::cb:{fn.name}"[:256],
                    room_id=room_id,
                    symbol_name=fn.name,
                    guard_kind="boundary_guard",
                )
            )
    return pub_syms, guarded, breakers


def _classify_py_riser(root_mod: str, ws_py_roots: frozenset[str]) -> RiserKind | None:
    if not root_mod or root_mod in ws_py_roots:
        return None
    if root_mod in _PY_SUBPROCESS_ROOTS:
        return RiserKind.SUBPROCESS_IO
    if root_mod in _PY_NETWORK_ROOTS:
        return RiserKind.NETWORK_IO
    if root_mod in _PY_DB_ROOTS:
        return RiserKind.DATABASE_IO
    return RiserKind.EXTERNAL_PACKAGE if root_mod not in sys.stdlib_module_names else None


def _iter_py_runtime_import_stmts(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    stmts: list[ast.Import | ast.ImportFrom] = []
    stack: list[ast.AST] = list(reversed(tree.body))
    budget = LoopBudget(max_steps=50_000)
    while stack:
        budget.tick()
        node = stack.pop()
        if isinstance(node, ast.If):
            if not is_type_checking_guard(node.test):
                stack.extend(reversed(node.body))
            stack.extend(reversed(node.orelse))
            continue
        if isinstance(node, ast.Import | ast.ImportFrom):
            stmts.append(node)
            continue
        if isinstance(node, ast.Try | ast.With | ast.AsyncWith):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
    return stmts


def _collect_py_unit_risers(
    unit: SourceUnit, ws_py_roots: frozenset[str]
) -> set[tuple[RiserKind, str]]:
    found: set[tuple[RiserKind, str]] = set()
    for stmt in _iter_py_runtime_import_stmts(unit.tree):
        if isinstance(stmt, ast.Import):
            roots = [alias.name.split(".", maxsplit=1)[0] for alias in stmt.names]
        elif stmt.level == 0 and stmt.module:
            roots = [stmt.module.split(".", maxsplit=1)[0]]
        else:
            roots = []
        for root_mod in roots:
            kind = _classify_py_riser(root_mod, ws_py_roots)
            if kind is not None:
                found.add((kind, root_mod[:256]))
    return found


def _classify_poly_pkg_riser(
    lang: SupportedLanguage, raw_target: str, target: str, ws_poly_roots: frozenset[str]
) -> tuple[RiserKind, str] | None:
    if lang == SupportedLanguage.TYPESCRIPT and target not in TS_BUILTIN_MODULES:
        return RiserKind.EXTERNAL_PACKAGE, target[:256]
    if lang == SupportedLanguage.RUST:
        crate = raw_target.split("::", maxsplit=1)[0]
        if crate and crate not in RUST_BUILTIN_CRATES and crate not in ws_poly_roots:
            return RiserKind.EXTERNAL_PACKAGE, crate[:256]
        return None
    if lang == SupportedLanguage.GO:
        first = raw_target.split("/", maxsplit=1)[0]
        is_ws = any(raw_target == r or raw_target.startswith(f"{r}/") for r in ws_poly_roots)
        if not is_ws and ("." in first or first not in GO_STDLIB_ROOTS):
            return RiserKind.EXTERNAL_PACKAGE, raw_target[:256]
    return None


_POLY_RAW_IO_SINKS: Final[Mapping[str, RiserKind]] = MappingProxyType(
    {
        "os/exec": RiserKind.SUBPROCESS_IO,
        "child_process": RiserKind.SUBPROCESS_IO,
        "net/http": RiserKind.NETWORK_IO,
        "database/sql": RiserKind.DATABASE_IO,
        "fs": RiserKind.FILESYSTEM_IO,
    }
)


def _classify_poly_import_riser(
    lang: SupportedLanguage, raw_target: str, root_pkg: str, ws_poly_roots: frozenset[str]
) -> tuple[RiserKind, str] | None:
    clean_raw = raw_target.removeprefix("node:").strip()
    target = (root_pkg or clean_raw).removeprefix("node:").strip()
    if not target or target in ws_poly_roots:
        return None
    if clean_raw.startswith("std::process"):
        return RiserKind.SUBPROCESS_IO, "std::process"
    if clean_raw.startswith("std::fs"):
        return RiserKind.FILESYSTEM_IO, "std::fs"
    raw_hit = _POLY_RAW_IO_SINKS.get(clean_raw) or _POLY_RAW_IO_SINKS.get(target)
    if raw_hit is not None:
        return raw_hit, (clean_raw if clean_raw in _POLY_RAW_IO_SINKS else target)[:256]
    if target in _POLY_NET_SINKS:
        return RiserKind.NETWORK_IO, target[:256]
    if target in _POLY_DB_SINKS:
        return RiserKind.DATABASE_IO, target[:256]
    return _classify_poly_pkg_riser(lang, clean_raw, target, ws_poly_roots)


def _collect_poly_unit_risers(
    unit: PolyglotUnit, ws_poly_roots: frozenset[str]
) -> set[tuple[RiserKind, str]]:
    found: set[tuple[RiserKind, str]] = set()
    for imp in unit.imports:
        if imp.is_internal and unit.language != SupportedLanguage.GO:
            continue
        classified = _classify_poly_import_riser(
            unit.language, imp.raw_target, imp.root_pkg, ws_poly_roots
        )
        if classified is not None:
            found.add(classified)
    if any("fetch" in fn.calls for fn in unit.callables):
        found.add((RiserKind.NETWORK_IO, "fetch"))
    return found


def _is_direct_room_unit(unit: InspectedUnit, lang: SupportedLanguage) -> bool:
    pl = classify_unit_placement(unit.file_path, lang)
    if pl.member_id == "." and pl.package_key.endswith(":root"):
        depth = (
            2
            if (pl.compartment_dirs and pl.compartment_dirs[0] in {"pkg", "internal", "cmd"})
            else 1
        )
        return len(pl.compartment_dirs) <= depth
    return len(pl.compartment_dirs) <= 1


def _scan_unit_for_room(
    room_id: str,
    unit: InspectedUnit,
    is_direct: bool,
    ws_py_roots: frozenset[str],
    ws_poly_roots: frozenset[str],
) -> tuple[
    set[str],
    set[str],
    set[str],
    list[CircuitBreakerNode],
    set[tuple[RiserKind, str]],
]:
    if isinstance(unit, SourceUnit):
        u_pub, u_guard, u_cbs = _collect_py_unit_breakers_and_symbols(room_id, unit)
        barrels = (
            _extract_py_init_exports(unit)
            if is_direct and PurePosixPath(unit.file_path).name == "__init__.py"
            else set()
        )
        return u_pub, u_guard, barrels, u_cbs, _collect_py_unit_risers(unit, ws_py_roots)
    u_pub, u_guard, u_cbs = _collect_poly_unit_breakers_and_symbols(room_id, unit)
    barrels = _extract_polyglot_barrel_exports(unit) if is_direct else set()
    return u_pub, u_guard, barrels, u_cbs, _collect_poly_unit_risers(unit, ws_poly_roots)


def _scan_single_room(
    room_id: str,
    room_units: Sequence[InspectedUnit],
    ws_py_roots: frozenset[str],
    ws_poly_roots: frozenset[str],
) -> _RoomScanFacts:
    first = room_units[0]
    lang = _unit_lang(first)
    _, member_id, wall_rating = resolve_unit_room_id(first.file_path, lang)
    pub_syms: set[str] = set()
    non_priv_syms: set[str] = set()
    barrel_syms: set[str] = set()
    guarded_syms: set[str] = set()
    breaker_map: dict[str, CircuitBreakerNode] = {}
    riser_pairs: set[tuple[RiserKind, str]] = set()
    direct_cnt = 0

    for unit in room_units:
        is_direct = _is_direct_room_unit(unit, _unit_lang(unit))
        direct_cnt += int(is_direct)
        stem = PurePosixPath(unit.file_path).stem
        u_pub, u_guard, u_barrels, u_cbs, u_risers = _scan_unit_for_room(
            room_id, unit, is_direct, ws_py_roots, ws_poly_roots
        )
        pub_syms |= u_pub
        if not (stem.startswith("_") and stem != "__init__"):
            non_priv_syms |= u_pub
        barrel_syms |= u_barrels
        guarded_syms |= u_guard
        riser_pairs |= u_risers
        for cb in u_cbs:
            breaker_map.setdefault(cb.symbol_name, cb)

    risers = tuple(
        ExternalRiser(
            riser_id=f"{room_id}=>riser:{kind.value}:{target}"[:256],
            room_id=room_id,
            kind=kind,
            target_name=target,
        )
        for kind, target in sorted(riser_pairs, key=lambda item: (item[0].value, item[1]))
    )
    return _RoomScanFacts(
        room_id=room_id,
        member_id=member_id,
        language=lang,
        wall_rating=wall_rating,
        module_count=direct_cnt if direct_cnt > 0 else len(room_units),
        public_symbols=frozenset(pub_syms),
        non_private_mod_symbols=frozenset(non_priv_syms),
        barrel_exports=frozenset(barrel_syms),
        guarded_symbols=frozenset(guarded_syms),
        breakers=tuple(breaker_map[k] for k in sorted(breaker_map)),
        risers=risers,
    )


def _lookup_py_mod_room(dotted_mod: str, room_by_py_mod: Mapping[str, str]) -> str | None:
    curr = dotted_mod
    budget = LoopBudget(max_steps=32)
    while curr:
        budget.tick()
        hit = room_by_py_mod.get(curr)
        if hit is not None:
            return hit
        if "." not in curr:
            return None
        curr = curr.rsplit(".", maxsplit=1)[0]
    return None


def _record_py_from_alias(
    module_name: str,
    alias_name: str,
    src_room: str,
    dst_room: str,
    room_by_py_mod: Mapping[str, str],
    coupled: dict[str, set[str]],
) -> None:
    if alias_name == "*" or alias_name.startswith("_"):
        return
    sub_room = _lookup_py_mod_room(f"{module_name}.{alias_name}", room_by_py_mod)
    if sub_room is not None and sub_room != src_room:
        coupled.setdefault(sub_room, set())
        return
    coupled.setdefault(dst_room, set()).add(alias_name[:256])


def _consume_py_cross_stmt(
    stmt: ast.Import | ast.ImportFrom,
    src_room: str,
    room_by_py_mod: Mapping[str, str],
    coupled: dict[str, set[str]],
) -> None:
    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            dst = _lookup_py_mod_room(alias.name, room_by_py_mod)
            if dst is not None and dst != src_room:
                coupled.setdefault(dst, set())
        return
    if stmt.level != 0 or not stmt.module:
        return
    dst_room = _lookup_py_mod_room(stmt.module, room_by_py_mod)
    if dst_room is None or dst_room == src_room:
        return
    coupled.setdefault(dst_room, set())
    for alias in stmt.names:
        _record_py_from_alias(stmt.module, alias.name, src_room, dst_room, room_by_py_mod, coupled)


def _extract_py_cross_room_symbols(
    unit: SourceUnit, src_room: str, room_by_py_mod: Mapping[str, str]
) -> dict[str, set[str]]:
    coupled: dict[str, set[str]] = {}
    for stmt in _iter_py_runtime_import_stmts(unit.tree):
        _consume_py_cross_stmt(stmt, src_room, room_by_py_mod, coupled)
    return coupled


def _index_files_and_py_mods(
    prod_units: Sequence[InspectedUnit], room_by_file: Mapping[str, str]
) -> tuple[
    dict[tuple[SupportedLanguage, str], list[str]], dict[str, str], dict[str, InspectedUnit]
]:
    files_by_lang_mod: dict[tuple[SupportedLanguage, str], list[str]] = {}
    room_by_py_mod: dict[str, str] = {}
    units_by_file = {u.file_path: u for u in prod_units}
    for u in prod_units:
        if u.module_name:
            files_by_lang_mod.setdefault((_unit_lang(u), u.module_name), []).append(u.file_path)
            if isinstance(u, SourceUnit):
                room_by_py_mod[u.module_name] = room_by_file[u.file_path]
    return files_by_lang_mod, room_by_py_mod, units_by_file


def _collect_entry_bus_targets(
    src_room: str,
    lang: SupportedLanguage,
    imported_mods: Sequence[str],
    files_by_lang_mod: Mapping[tuple[SupportedLanguage, str], list[str]],
    room_by_file: Mapping[str, str],
    bus_ports: dict[tuple[str, str], set[str]],
) -> None:
    for mod in imported_mods:
        for dst_fp in files_by_lang_mod.get((lang, mod), ()):
            dst_room = room_by_file[dst_fp]
            if dst_room != src_room:
                bus_ports.setdefault((src_room, dst_room), set())


def _collect_raw_bus_ports(
    prod_units: Sequence[InspectedUnit],
    ast_graph: WorkspaceAstGraph,
    room_by_file: Mapping[str, str],
) -> dict[tuple[str, str], set[str]]:
    files_by_lang_mod, room_by_py_mod, units_by_file = _index_files_and_py_mods(
        prod_units, room_by_file
    )
    bus_ports: dict[tuple[str, str], set[str]] = {}
    for entry in ast_graph.modules:
        src_unit = units_by_file[entry.file_path]
        src_room = room_by_file[entry.file_path]
        _collect_entry_bus_targets(
            src_room,
            _unit_lang(src_unit),
            entry.imports,
            files_by_lang_mod,
            room_by_file,
            bus_ports,
        )
        if isinstance(src_unit, SourceUnit):
            for dst_room, syms in _extract_py_cross_room_symbols(
                src_unit, src_room, room_by_py_mod
            ).items():
                bus_ports.setdefault((src_room, dst_room), set()).update(syms)
    return bus_ports


def _enrich_bus_calls(
    ast_graph: WorkspaceAstGraph,
    room_by_file: Mapping[str, str],
    room_facts: Mapping[str, _RoomScanFacts],
    bus_ports: dict[tuple[str, str], set[str]],
) -> dict[tuple[str, str], int]:
    calls_by_room: dict[str, list[str]] = {}
    for sym in ast_graph.symbols:
        r_id = room_by_file.get(sym.file_path)
        if r_id is not None:
            calls_by_room.setdefault(r_id, []).extend(sym.calls)

    bus_calls: dict[tuple[str, str], int] = {}
    for (src_room, dst_room), ports in bus_ports.items():
        dst_pub = room_facts[dst_room].public_symbols
        hits = 0
        for callee in calls_by_room.get(src_room, ()):
            tail = callee.replace("::", ".").rsplit(".", maxsplit=1)[-1]
            if tail in dst_pub:
                ports.add(tail[:256])
                hits += 1
        bus_calls[(src_room, dst_room)] = hits
    return bus_calls


def _build_cross_room_buses(
    prod_units: Sequence[InspectedUnit],
    ast_graph: WorkspaceAstGraph,
    room_by_file: Mapping[str, str],
    room_facts: Mapping[str, _RoomScanFacts],
) -> tuple[BlueprintBus, ...]:
    bus_ports = _collect_raw_bus_ports(prod_units, ast_graph, room_by_file)
    bus_calls = _enrich_bus_calls(ast_graph, room_by_file, room_facts, bus_ports)
    buses: list[BlueprintBus] = []
    for src_room, dst_room in sorted(bus_ports):
        ports_sorted = tuple(sorted(bus_ports[(src_room, dst_room)]))[:256]
        dst_facts = room_facts[dst_room]
        known_coupled = [s for s in ports_sorted if s in dst_facts.public_symbols]
        guarded = all(s in dst_facts.guarded_symbols for s in known_coupled)
        buses.append(
            BlueprintBus(
                bus_id=f"{src_room}->{dst_room}"[:256],
                source_room=src_room,
                target_room=dst_room,
                port_symbols=ports_sorted,
                call_count=bus_calls.get((src_room, dst_room), 0),
                has_breaker_guard=guarded,
            )
        )
    return tuple(buses)


def _select_room_inbound_symbols(
    facts: _RoomScanFacts, coupled_inbound: set[str], inv_syms: set[str], total_rooms: int
) -> set[str]:
    if facts.language == SupportedLanguage.GO:
        return set(facts.non_private_mod_symbols) | coupled_inbound | inv_syms
    if facts.barrel_exports:
        return set(facts.barrel_exports) | coupled_inbound | inv_syms
    if coupled_inbound or inv_syms:
        return coupled_inbound | inv_syms
    return set(facts.non_private_mod_symbols) if total_rooms == 1 else set()


def _resolve_invariant_pins(
    ast_graph: WorkspaceAstGraph,
    room_by_file: Mapping[str, str],
    invariant_bindings: Sequence[tuple[IdentifierStr, IdentifierStr]],
) -> tuple[InvariantPin, ...]:
    if not invariant_bindings:
        return ()
    pins: list[InvariantPin] = []
    seen: set[tuple[str, str, str]] = set()
    for inv_id, ver_sym in invariant_bindings:
        matches = ast_graph.find_symbols(ver_sym)
        if not matches:
            continue
        chosen = sorted(matches, key=lambda s: (s.file_path, s.start_line))[0]
        owner_room = room_by_file.get(chosen.file_path)
        if owner_room is None:
            continue
        sym_tail = chosen.qualname.split(".", maxsplit=1)[0][:256]
        key = (inv_id, owner_room, sym_tail)
        if key not in seen:
            seen.add(key)
            pins.append(
                InvariantPin(invariant_id=inv_id, room_id=owner_room, verification_symbol=sym_tail)
            )
    pins.sort(key=lambda p: (p.invariant_id, p.room_id, p.verification_symbol))
    return tuple(pins)


def _assemble_rooms_and_pins(
    room_facts: Mapping[str, _RoomScanFacts],
    buses: Sequence[BlueprintBus],
    inv_pins: Sequence[InvariantPin],
    max_modules: int,
) -> tuple[
    tuple[BlueprintRoom, ...],
    tuple[PerimeterPortPin, ...],
    tuple[CircuitBreakerNode, ...],
    tuple[ExternalRiser, ...],
]:
    ranks = compute_room_ranks(tuple(room_facts), buses)
    inbound_by_room: dict[str, set[str]] = {r_id: set() for r_id in room_facts}
    for b in buses:
        inbound_by_room[b.target_room].update(b.port_symbols)
    inv_by_room: dict[str, set[str]] = {r_id: set() for r_id in room_facts}
    for ip in inv_pins:
        inv_by_room[ip.room_id].add(ip.verification_symbol)

    rooms: list[BlueprintRoom] = []
    port_pins: list[PerimeterPortPin] = []
    breakers: list[CircuitBreakerNode] = []
    risers: list[ExternalRiser] = []
    total = len(room_facts)

    for r_id in sorted(room_facts):
        facts = room_facts[r_id]
        rooms.append(
            BlueprintRoom(
                room_id=r_id,
                label=r_id.split("/")[-1][:256] or r_id,
                member_id=facts.member_id,
                language=facts.language,
                wall_rating=facts.wall_rating,
                module_count=facts.module_count,
                max_modules=max_modules,
                public_symbol_count=len(facts.public_symbols),
                breaker_count=len(facts.breakers),
                topological_rank=ranks[r_id],
            )
        )
        breakers.extend(facts.breakers)
        risers.extend(facts.risers)
        for sym in sorted(
            _select_room_inbound_symbols(facts, inbound_by_room[r_id], inv_by_room[r_id], total)
        ):
            has_cb = sym in facts.guarded_symbols or sym not in facts.public_symbols
            port_pins.append(
                PerimeterPortPin(
                    pin_id=f"{r_id}::in:{sym}"[:256],
                    room_id=r_id,
                    symbol_name=sym,
                    direction=PortDirection.INBOUND_PORT,
                    has_circuit_breaker=has_cb,
                )
            )
    for b in buses:
        port_pins.append(
            PerimeterPortPin(
                pin_id=f"{b.source_room}::out:{b.target_room}"[:256],
                room_id=b.source_room,
                symbol_name=f"out->{b.target_room}"[:256],
                direction=PortDirection.OUTBOUND_PORT,
                has_circuit_breaker=b.has_breaker_guard,
            )
        )
    return tuple(rooms), tuple(port_pins), tuple(breakers), tuple(risers)


@jezz_callable
def extract_blueprint(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
    *,
    title: ShortText = "Workspace Blueprint",
    profile: PermitProfile = PermitProfile.STANDARD,
    invariant_bindings: Annotated[
        Sequence[tuple[InvariantIdStr, IdentifierStr]], Field(max_length=1024)
    ] = (),
) -> BlueprintGraph:
    """Deterministically extract canonical BlueprintGraph pi(Omega) from source units."""
    prod_units = tuple(sorted((u for u in units if not u.is_test_file), key=lambda u: u.file_path))
    if not prod_units:
        empty_digest = compute_blueprint_netlist_digest((), (), (), (), ())
        return BlueprintGraph(title=title, profile=profile, netlist_digest=empty_digest)

    limits = resolve_compartment_limits(profile)
    ws_py_roots = frozenset(
        u.module_name.split(".", maxsplit=1)[0]
        for u in prod_units
        if isinstance(u, SourceUnit) and u.module_name
    )
    ws_poly_roots = frozenset(
        u.module_name.split(".", maxsplit=1)[0].split("/", maxsplit=1)[0]
        for u in prod_units
        if isinstance(u, PolyglotUnit) and u.module_name
    )

    units_by_room: dict[str, list[InspectedUnit]] = {}
    room_by_file: dict[str, str] = {}
    for u in prod_units:
        room_id, _, _ = resolve_unit_room_id(u.file_path, _unit_lang(u))
        room_by_file[u.file_path] = room_id
        units_by_room.setdefault(room_id, []).append(u)

    room_facts = {
        r_id: _scan_single_room(r_id, units_by_room[r_id], ws_py_roots, ws_poly_roots)
        for r_id in sorted(units_by_room)
    }
    ast_graph = build_workspace_ast_graph(prod_units)
    buses = _build_cross_room_buses(prod_units, ast_graph, room_by_file, room_facts)
    inv_pins = _resolve_invariant_pins(ast_graph, room_by_file, invariant_bindings)
    rooms, port_pins, breakers, risers = _assemble_rooms_and_pins(
        room_facts, buses, inv_pins, limits.max_compartment_modules
    )
    walls = build_blueprint_walls(rooms, buses)
    digest = compute_blueprint_netlist_digest(rooms, port_pins, buses, risers, inv_pins)
    return BlueprintGraph(
        title=title,
        profile=profile,
        rooms=rooms,
        walls=walls,
        port_pins=port_pins,
        circuit_breakers=breakers,
        buses=buses,
        external_risers=risers,
        invariant_pins=inv_pins,
        netlist_digest=digest,
    )
