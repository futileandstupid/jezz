"""Article IV (JBC-COG-006): Subpackage quotient DAG, floorplates, portals & fire-walls."""

import ast
import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Final

import icontract
from pydantic import BaseModel, Field

from jezz.codes.ast_graph import build_workspace_ast_graph
from jezz.codes.ast_utils import RawDiscrepancy, SourceSpan, SourceUnit, is_type_checking_guard
from jezz.codes.complexity import PermitProfile
from jezz.codes.dep_graph import find_tarjan_cycles
from jezz.codes.polyglot_ir import (
    InspectedUnit,
    PolyglotImport,
    PolyglotUnit,
    SupportedLanguage,
    detect_language_for_path,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_FROZENSET_VALIDATOR,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    FiniteFloat,
    IdentifierStr,
    LoopBudget,
    NonNegativeInt,
    PositiveInt,
    jezz_callable,
)

MAX_COMPARTMENT_MODULES_LIMIT: Final[int] = 35
STRICT_COMPARTMENT_MODULES_LIMIT: Final[int] = 25
MAX_PACKAGE_MODULES_LIMIT: Final[int] = 100
STRICT_PACKAGE_MODULES_LIMIT: Final[int] = 75
MIN_PORTAL_RATIO_COMPARTMENT_SIZE: Final[int] = 10
MAX_COMPARTMENT_PORTAL_RATIO: Final[float] = 0.65
STRICT_COMPARTMENT_PORTAL_RATIO: Final[float] = 0.55

_WORKSPACE_MEMBER_CONTAINERS: Final[frozenset[str]] = frozenset(
    {"packages", "apps", "crates", "libs", "services"}
)
_DEEP_INTERNAL_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|/|:)(?:_internal|__private)(?:/|::|$)"
)


class CompartmentLimits(BaseModel):
    """Effective JBC-COG-006 floorplate and portal-ratio ceilings for a PermitProfile."""

    model_config = STRICT_FROZEN_CONFIG

    max_compartment_modules: PositiveInt = MAX_COMPARTMENT_MODULES_LIMIT
    max_package_modules: PositiveInt = MAX_PACKAGE_MODULES_LIMIT
    min_portal_ratio_size: PositiveInt = MIN_PORTAL_RATIO_COMPARTMENT_SIZE
    max_portal_ratio: Annotated[FiniteFloat, Field(gt=0.0, le=1.0)] = MAX_COMPARTMENT_PORTAL_RATIO


class UnitPlacement(BaseModel):
    """Canonical workspace member, package, and compartment coordinates for a source unit."""

    model_config = STRICT_FROZEN_CONFIG

    file_path: IdentifierStr
    member_id: IdentifierStr
    package_key: IdentifierStr
    compartment_dirs: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32),
    ] = ()
    compartment_key: BoundedText = ""


class CompartmentMetrics(BaseModel):
    """Structural floorplate, portal-ratio, and fan-in measurements for one compartment."""

    model_config = STRICT_FROZEN_CONFIG

    compartment_key: IdentifierStr
    package_key: IdentifierStr
    member_id: IdentifierStr
    direct_module_count: NonNegativeInt
    subtree_module_count: NonNegativeInt
    external_portal_count: NonNegativeInt
    portal_ratio: Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]
    inbound_external_importers: NonNegativeInt
    internal_edge_count: NonNegativeInt


class PackageFloorplate(BaseModel):
    """Total non-test production module count inside one workspace package."""

    model_config = STRICT_FROZEN_CONFIG

    package_key: IdentifierStr
    member_id: IdentifierStr
    module_count: NonNegativeInt


class WorkspaceCompartmentSummary(BaseModel):
    """Whole-workspace compartment and package floorplate summary over production units."""

    model_config = STRICT_FROZEN_CONFIG

    compartments: Annotated[
        tuple[CompartmentMetrics, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=4096),
    ] = ()
    packages: Annotated[
        tuple[PackageFloorplate, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=1024),
    ] = ()


@dataclass(frozen=True)
class _WorkspaceTopology:
    """Internal precomputed production unit graph and placement index."""

    prod_units: tuple[InspectedUnit, ...]
    placements: Mapping[str, UnitPlacement]
    file_outgoing: Mapping[str, frozenset[str]]
    mod_key_by_file: Mapping[str, str]
    units_by_file: Mapping[str, InspectedUnit]


@icontract.ensure(lambda result: 0.0 < result.max_portal_ratio <= 1.0)
@jezz_callable
def resolve_compartment_limits(
    profile: PermitProfile = PermitProfile.STANDARD,
) -> CompartmentLimits:
    """Resolve effective JBC-COG-006 numeric thresholds for ``profile``."""
    if profile == PermitProfile.STRICT_INTERNAL:
        return CompartmentLimits(
            max_compartment_modules=STRICT_COMPARTMENT_MODULES_LIMIT,
            max_package_modules=STRICT_PACKAGE_MODULES_LIMIT,
            min_portal_ratio_size=MIN_PORTAL_RATIO_COMPARTMENT_SIZE,
            max_portal_ratio=STRICT_COMPARTMENT_PORTAL_RATIO,
        )
    return CompartmentLimits()


def _split_member_parts(parts: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    if len(parts) >= 3 and parts[0] in _WORKSPACE_MEMBER_CONTAINERS:
        return f"{parts[0]}/{parts[1]}", tuple(parts[2:])
    return ".", tuple(parts)


def _python_pkg_and_dirs(file_dir: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    if not file_dir:
        return "root", ()
    if file_dir[0] == "src":
        if len(file_dir) >= 2:
            return file_dir[1], tuple(file_dir[2:])
        return "src", ()
    return file_dir[0], tuple(file_dir[1:])


@jezz_callable
def classify_unit_placement(
    file_path: IdentifierStr,
    language: SupportedLanguage | None = None,
) -> UnitPlacement:
    """Map workspace-relative ``file_path`` to its member, package, and compartment hierarchy."""
    clean = file_path.strip("/")
    parts = [seg for seg in clean.split("/") if seg]
    member_id, inner = _split_member_parts(parts)
    file_dir = inner[:-1]
    eff_lang = language if language is not None else detect_language_for_path(clean)

    if eff_lang == SupportedLanguage.PYTHON:
        pkg_name, comp_dirs = _python_pkg_and_dirs(file_dir)
    else:
        rel_dir = file_dir[1:] if (file_dir and file_dir[0] == "src") else file_dir
        pkg_name = member_id.split("/")[-1] if member_id != "." else "root"
        comp_dirs = tuple(rel_dir)

    package_key = f"{member_id}:{pkg_name}"
    comp_key = f"{package_key}/{'/'.join(comp_dirs)}" if comp_dirs else ""
    return UnitPlacement(
        file_path=clean,
        member_id=member_id,
        package_key=package_key,
        compartment_dirs=comp_dirs,
        compartment_key=comp_key,
    )


def _unit_language(unit: InspectedUnit) -> SupportedLanguage:
    return SupportedLanguage.PYTHON if isinstance(unit, SourceUnit) else unit.language


def _unit_module_key(unit: InspectedUnit) -> str:
    lang = _unit_language(unit)
    ident = unit.module_name if unit.module_name else unit.file_path
    return f"{lang}:{ident}"


def _resolve_entry_file_targets(
    file_path: str,
    lang: SupportedLanguage,
    imported_mods: Sequence[str],
    files_by_lang_mod: Mapping[tuple[SupportedLanguage, str], list[str]],
) -> frozenset[str]:
    targets = {
        dst_path
        for mod in imported_mods
        for dst_path in files_by_lang_mod.get((lang, mod), ())
        if dst_path != file_path
    }
    return frozenset(targets)


def _build_workspace_topology(units: Sequence[InspectedUnit]) -> _WorkspaceTopology:
    prod = tuple(sorted((u for u in units if not u.is_test_file), key=lambda u: u.file_path))
    placements = {
        u.file_path: classify_unit_placement(u.file_path, _unit_language(u)) for u in prod
    }
    units_by_file = {u.file_path: u for u in prod}
    mod_key_by_file = {u.file_path: _unit_module_key(u) for u in prod}

    files_by_lang_mod: dict[tuple[SupportedLanguage, str], list[str]] = {}
    for u in prod:
        if u.module_name:
            files_by_lang_mod.setdefault((_unit_language(u), u.module_name), []).append(u.file_path)

    ast_graph = build_workspace_ast_graph(prod)
    outgoing = {
        entry.file_path: _resolve_entry_file_targets(
            entry.file_path,
            _unit_language(units_by_file[entry.file_path]),
            entry.imports,
            files_by_lang_mod,
        )
        for entry in ast_graph.modules
    }
    return _WorkspaceTopology(
        prod_units=prod,
        placements=placements,
        file_outgoing=outgoing,
        mod_key_by_file=mod_key_by_file,
        units_by_file=units_by_file,
    )


def _collect_subtree_portals(
    subtree_files: AbstractSet[str],
    topo: _WorkspaceTopology,
) -> tuple[set[str], set[str]]:
    portal_mods: set[str] = set()
    external_importers: set[str] = set()
    for src_fp, dst_files in topo.file_outgoing.items():
        if src_fp in subtree_files:
            continue
        hit_targets = dst_files & subtree_files
        if hit_targets:
            external_importers.add(topo.mod_key_by_file[src_fp])
            portal_mods.update(topo.mod_key_by_file[t] for t in hit_targets)
    return portal_mods, external_importers


def _compute_single_compartment_metrics(
    comp_key: str,
    pkg_key: str,
    prefix: tuple[str, ...],
    pkg_files: Sequence[str],
    topo: _WorkspaceTopology,
) -> CompartmentMetrics:
    plen = len(prefix)
    subtree_files = {
        fp for fp in pkg_files if topo.placements[fp].compartment_dirs[:plen] == prefix
    }
    direct_mods = {
        topo.mod_key_by_file[fp]
        for fp in subtree_files
        if topo.placements[fp].compartment_dirs == prefix
    }
    subtree_mods = {topo.mod_key_by_file[fp] for fp in subtree_files}
    portal_mods, external_importers = _collect_subtree_portals(subtree_files, topo)
    internal_edges = sum(
        1
        for src_fp in subtree_files
        for dst_fp in topo.file_outgoing.get(src_fp, frozenset())
        if dst_fp in subtree_files and topo.mod_key_by_file[dst_fp] != topo.mod_key_by_file[src_fp]
    )
    denom = max(1, len(subtree_mods))
    member_id = pkg_key.split(":", maxsplit=1)[0]
    return CompartmentMetrics(
        compartment_key=comp_key,
        package_key=pkg_key,
        member_id=member_id,
        direct_module_count=len(direct_mods),
        subtree_module_count=len(subtree_mods),
        external_portal_count=len(portal_mods),
        portal_ratio=round(len(portal_mods) / denom, 6),
        inbound_external_importers=len(external_importers),
        internal_edge_count=internal_edges,
    )


@jezz_callable
def analyze_workspace_compartments(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
) -> WorkspaceCompartmentSummary:
    """Compute deterministic compartment and package floorplate metrics over ``units``."""
    topo = _build_workspace_topology(units)
    files_by_pkg: dict[str, list[str]] = {}
    prefixes_by_pkg: dict[str, set[tuple[str, ...]]] = {}
    for fp, pl in topo.placements.items():
        files_by_pkg.setdefault(pl.package_key, []).append(fp)
        for depth in range(1, len(pl.compartment_dirs) + 1):
            prefixes_by_pkg.setdefault(pl.package_key, set()).add(pl.compartment_dirs[:depth])

    comp_metrics: list[CompartmentMetrics] = []
    pkg_floorplates: list[PackageFloorplate] = []
    for pkg_key in sorted(files_by_pkg):
        pkg_files = files_by_pkg[pkg_key]
        pkg_mods = {topo.mod_key_by_file[fp] for fp in pkg_files}
        pkg_floorplates.append(
            PackageFloorplate(
                package_key=pkg_key,
                member_id=pkg_key.split(":", maxsplit=1)[0],
                module_count=len(pkg_mods),
            )
        )
        for prefix in sorted(prefixes_by_pkg.get(pkg_key, set())):
            comp_key = f"{pkg_key}/{'/'.join(prefix)}"
            comp_metrics.append(
                _compute_single_compartment_metrics(comp_key, pkg_key, prefix, pkg_files, topo)
            )
    return WorkspaceCompartmentSummary(
        compartments=tuple(comp_metrics),
        packages=tuple(pkg_floorplates),
    )


def _vertex_for_prefix(pl: UnitPlacement, prefix: tuple[str, ...]) -> str | None:
    plen = len(prefix)
    if len(pl.compartment_dirs) < plen or pl.compartment_dirs[:plen] != prefix:
        return None
    if len(pl.compartment_dirs) == plen:
        return f"file:{pl.file_path}"
    child = "/".join((*prefix, pl.compartment_dirs[plen]))
    return f"comp:{pl.package_key}/{child}"


def _display_quotient_vertex(vertex: str) -> str:
    if vertex.startswith("comp:"):
        return vertex.removeprefix("comp:").removeprefix(".:")
    return vertex.removeprefix("file:")


def _build_prefix_quotient_graph(
    pkg_files: Sequence[str],
    prefix: tuple[str, ...],
    topo: _WorkspaceTopology,
) -> tuple[dict[str, list[str]], dict[str, frozenset[str]]]:
    vertex_files: dict[str, list[str]] = {}
    file_to_vertex: dict[str, str] = {}
    for fp in pkg_files:
        vtx = _vertex_for_prefix(topo.placements[fp], prefix)
        if vtx is not None:
            file_to_vertex[fp] = vtx
            vertex_files.setdefault(vtx, []).append(fp)

    if not any(v.startswith("comp:") for v in vertex_files):
        return {}, {}

    q_edges: dict[str, set[str]] = {v: set() for v in vertex_files}
    for src_fp, src_vtx in file_to_vertex.items():
        for dst_fp in topo.file_outgoing.get(src_fp, frozenset()):
            dst_vtx = file_to_vertex.get(dst_fp)
            if dst_vtx is not None and dst_vtx != src_vtx:
                q_edges[src_vtx].add(dst_vtx)
    return vertex_files, {k: frozenset(v) for k, v in q_edges.items()}


def _check_pkg_prefix_quotient_cycles(
    pkg_files: Sequence[str],
    prefix: tuple[str, ...],
    topo: _WorkspaceTopology,
    target_paths: AbstractSet[str],
) -> list[RawDiscrepancy]:
    vertex_files, frozen_q = _build_prefix_quotient_graph(pkg_files, prefix, topo)
    if not frozen_q:
        return []
    findings: list[RawDiscrepancy] = []
    for scc in find_tarjan_cycles(frozen_q):
        if not any(v.startswith("comp:") for v in scc):
            continue
        touched = sorted(fp for v in scc for fp in vertex_files[v] if fp in target_paths)
        if not touched:
            continue
        cycle_label = " <-> ".join(_display_quotient_vertex(v) for v in scc[:8])
        findings.append(
            RawDiscrepancy(
                code_id="JBC-COG-006",
                location=SourceSpan(file_path=touched[0], line=1, symbol=scc[0][:256]),
                message=f"Cyclic subpackage compartment dependency in G/~: {cycle_label}.",
                remediation=(
                    "Extract shared schemas/contracts into a lower sink compartment so "
                    "subpackage quotient graph G/~ forms a strict DAG."
                ),
            )
        )
    return findings


def _build_member_quotient_graph(
    topo: _WorkspaceTopology,
) -> tuple[dict[str, list[str]], dict[str, frozenset[str]]]:
    member_files: dict[str, list[str]] = {}
    for fp, pl in topo.placements.items():
        member_files.setdefault(pl.member_id, []).append(fp)
    if len(member_files) <= 1:
        return {}, {}
    m_edges: dict[str, set[str]] = {m: set() for m in member_files}
    for src_fp, dst_files in topo.file_outgoing.items():
        src_m = topo.placements[src_fp].member_id
        m_edges[src_m].update(
            topo.placements[d].member_id for d in dst_files if topo.placements[d].member_id != src_m
        )
    return member_files, {k: frozenset(v) for k, v in m_edges.items()}


def _check_workspace_member_cycles(
    topo: _WorkspaceTopology,
    target_paths: AbstractSet[str],
) -> list[RawDiscrepancy]:
    member_files, frozen_m = _build_member_quotient_graph(topo)
    if not frozen_m:
        return []
    findings: list[RawDiscrepancy] = []
    for scc in find_tarjan_cycles(frozen_m):
        touched = sorted(fp for m in scc for fp in member_files[m] if fp in target_paths)
        if not touched:
            continue
        label = " <-> ".join(scc[:8])
        findings.append(
            RawDiscrepancy(
                code_id="JBC-COG-006",
                location=SourceSpan(file_path=touched[0], line=1, symbol=scc[0][:256]),
                message=f"Cyclic workspace member dependency detected: {label}.",
                remediation="Enforce a monotone DAG across multi-package workspace members.",
            )
        )
    return findings


def _check_quotient_acyclicity(
    topo: _WorkspaceTopology,
    target_paths: AbstractSet[str],
) -> list[RawDiscrepancy]:
    files_by_pkg: dict[str, list[str]] = {}
    parent_prefixes: dict[str, set[tuple[str, ...]]] = {}
    for fp, pl in topo.placements.items():
        files_by_pkg.setdefault(pl.package_key, []).append(fp)
        for depth in range(len(pl.compartment_dirs)):
            parent_prefixes.setdefault(pl.package_key, set()).add(pl.compartment_dirs[:depth])

    findings = _check_workspace_member_cycles(topo, target_paths)
    for pkg_key in sorted(files_by_pkg):
        for prefix in sorted(parent_prefixes.get(pkg_key, set())):
            findings.extend(
                _check_pkg_prefix_quotient_cycles(files_by_pkg[pkg_key], prefix, topo, target_paths)
            )
    return findings


def _check_floorplate_and_portals(
    summary: WorkspaceCompartmentSummary,
    topo: _WorkspaceTopology,
    limits: CompartmentLimits,
    target_paths: AbstractSet[str],
) -> list[RawDiscrepancy]:
    findings: list[RawDiscrepancy] = []
    for pkg in summary.packages:
        if pkg.module_count <= limits.max_package_modules:
            continue
        pkg_touched = sorted(
            fp
            for fp, pl in topo.placements.items()
            if pl.package_key == pkg.package_key and fp in target_paths
        )
        if pkg_touched:
            findings.append(
                RawDiscrepancy(
                    code_id="JBC-COG-006",
                    location=SourceSpan(file_path=pkg_touched[0], line=1, symbol=pkg.package_key),
                    message=(
                        f"Package '{pkg.package_key.removeprefix('.:')}' contains "
                        f"{pkg.module_count} production modules "
                        f"(limit {limits.max_package_modules})."
                    ),
                    remediation=(
                        "Split cohesive upward consumer satellites or downward bedrock kernels "
                        "into separate workspace member packages."
                    ),
                )
            )
    findings.extend(
        _check_compartment_metrics_limits(summary.compartments, topo, limits, target_paths)
    )
    return findings


def _touched_files_for_compartment(
    comp: CompartmentMetrics,
    topo: _WorkspaceTopology,
    target_paths: AbstractSet[str],
) -> list[str]:
    prefix_str = (
        comp.compartment_key.split("/", maxsplit=1)[1] if "/" in comp.compartment_key else ""
    )
    prefix = tuple(prefix_str.split("/")) if prefix_str else ()
    plen = len(prefix)
    subtree = {
        fp
        for fp, pl in topo.placements.items()
        if pl.package_key == comp.package_key and pl.compartment_dirs[:plen] == prefix
    }
    direct_touched = sorted(fp for fp in subtree if fp in target_paths)
    if direct_touched:
        return direct_touched
    return sorted(
        src_fp
        for src_fp, dsts in topo.file_outgoing.items()
        if src_fp in target_paths and src_fp not in subtree and (dsts & subtree)
    )


def _check_compartment_metrics_limits(
    compartments: Sequence[CompartmentMetrics],
    topo: _WorkspaceTopology,
    limits: CompartmentLimits,
    target_paths: AbstractSet[str],
) -> list[RawDiscrepancy]:
    findings: list[RawDiscrepancy] = []
    for comp in compartments:
        label = comp.compartment_key.removeprefix(".:")
        touched = _touched_files_for_compartment(comp, topo, target_paths)
        if not touched:
            continue
        if comp.direct_module_count > limits.max_compartment_modules:
            findings.append(
                RawDiscrepancy(
                    code_id="JBC-COG-006",
                    location=SourceSpan(file_path=touched[0], line=1, symbol=label[:256]),
                    message=(
                        f"Compartment '{label}' contains {comp.direct_module_count} direct "
                        f"production modules (limit {limits.max_compartment_modules})."
                    ),
                    remediation=(
                        "Partition oversized compartment into cohesive child subpackages or "
                        "extract a satellite package."
                    ),
                )
            )
        if (
            comp.subtree_module_count >= limits.min_portal_ratio_size
            and comp.portal_ratio > limits.max_portal_ratio
        ):
            findings.append(
                RawDiscrepancy(
                    code_id="JBC-COG-006",
                    location=SourceSpan(file_path=touched[0], line=1, symbol=label[:256]),
                    message=(
                        f"Compartment '{label}' exposes {comp.external_portal_count}/"
                        f"{comp.subtree_module_count} modules externally "
                        f"(portal ratio {comp.portal_ratio:.2f} > {limits.max_portal_ratio:.2f})."
                    ),
                    remediation=(
                        "Encapsulate internal helper modules behind narrow public boundary "
                        "modules rather than porous folder splits."
                    ),
                )
            )
    return findings


def _resolve_relative_posix_target(file_path: str, raw_target: str) -> tuple[bool, tuple[str, ...]]:
    parent_posix = PurePosixPath(file_path).parent.as_posix()
    stack = [seg for seg in parent_posix.split("/") if seg and seg != "."]
    budget = LoopBudget(max_steps=128)
    for seg in raw_target.split("/"):
        budget.tick()
        if not seg or seg == ".":
            continue
        if seg == "..":
            if not stack:
                return True, ()
            stack.pop()
            continue
        stack.append(seg)
    return False, tuple(stack)


def _go_owner_matches_unit_dir(owner_tail: str, unit_dir: str, owner_leaf: str) -> bool:
    if unit_dir == owner_tail or unit_dir.startswith(f"{owner_tail}/"):
        return True
    if f"/{owner_tail}/" in f"/{unit_dir}/" or unit_dir.endswith(f"/{owner_tail}"):
        return True
    return owner_leaf in {s for s in unit_dir.split("/") if s}


def _check_go_internal_visibility(unit: PolyglotUnit, imp: PolyglotImport) -> RawDiscrepancy | None:
    segs = [s for s in imp.raw_target.strip("/").split("/") if s]
    if "internal" not in segs:
        return None
    idx = segs.index("internal")
    owner_tail = "/".join(segs[:idx])
    if not owner_tail:
        return None
    unit_dir = PurePosixPath(unit.file_path).parent.as_posix().strip("/")
    if _go_owner_matches_unit_dir(owner_tail, unit_dir, segs[idx - 1]):
        return None
    return RawDiscrepancy(
        code_id="JBC-COG-006",
        location=SourceSpan(file_path=unit.file_path, line=imp.line, column=imp.column),
        message=(
            f"Go import '{imp.raw_target}' breaches internal package fire-wall "
            f"outside '{owner_tail}'."
        ),
        remediation="Import exported package APIs rather than sibling internal/ packages.",
    )


def _member_slugs_index(placements: Mapping[str, UnitPlacement]) -> dict[str, str]:
    slugs: dict[str, str] = {}
    for pl in placements.values():
        if pl.member_id == ".":
            continue
        raw_slug = pl.member_id.split("/")[1]
        slugs[raw_slug] = pl.member_id
        slugs[raw_slug.replace("-", "_")] = pl.member_id
    return slugs


def _targets_other_member_src(
    raw_target: str,
    src_member: str,
    member_slugs: Mapping[str, str],
) -> str | None:
    cleaned = raw_target.strip("/")
    for slug, owner_member in member_slugs.items():
        if owner_member == src_member:
            continue
        if (
            cleaned.startswith(f"{slug}/src/")
            or cleaned == f"{slug}/src"
            or f"/{slug}/src/" in cleaned
            or cleaned.endswith(f"/{slug}/src")
        ):
            return owner_member
    return None


def _check_single_polyglot_import_firewall(
    unit: PolyglotUnit,
    imp: PolyglotImport,
    src_member: str,
    member_slugs: Mapping[str, str],
) -> RawDiscrepancy | None:
    span = SourceSpan(file_path=unit.file_path, line=imp.line, column=imp.column)
    if imp.raw_target.startswith("."):
        escaped_root, resolved_parts = _resolve_relative_posix_target(
            unit.file_path, imp.raw_target
        )
        dst_member, _ = _split_member_parts(resolved_parts)
        if escaped_root or dst_member != src_member:
            return RawDiscrepancy(
                code_id="JBC-COG-006",
                location=span,
                message=(
                    f"Relative import '{imp.raw_target}' escapes workspace member "
                    f"'{src_member}' boundary."
                ),
                remediation="Import sibling workspace packages via declared package name.",
            )
        return None
    if unit.language == SupportedLanguage.GO:
        go_hit = _check_go_internal_visibility(unit, imp)
        if go_hit is not None:
            return go_hit
    other_src = _targets_other_member_src(imp.raw_target, src_member, member_slugs)
    if other_src is not None or _DEEP_INTERNAL_PATH_RE.search(imp.raw_target) is not None:
        return RawDiscrepancy(
            code_id="JBC-COG-006",
            location=span,
            message=(
                f"Import '{imp.raw_target}' penetrates private/src interior of "
                "another workspace package."
            ),
            remediation="Import only public root exports declared by the target package.",
        )
    return None


def _check_polyglot_unit_firewalls(
    unit: PolyglotUnit,
    src_member: str,
    member_slugs: Mapping[str, str],
) -> list[RawDiscrepancy]:
    return [
        hit
        for imp in unit.imports
        if (hit := _check_single_polyglot_import_firewall(unit, imp, src_member, member_slugs))
        is not None
    ]


def _push_non_typechecking_if(node: ast.If, stack: list[ast.AST]) -> None:
    if not is_type_checking_guard(node.test):
        stack.extend(reversed(node.body))
    stack.extend(reversed(node.orelse))


def _iter_python_runtime_imports(unit: SourceUnit) -> list[ast.Import | ast.ImportFrom]:
    collected: list[ast.Import | ast.ImportFrom] = []
    stack: list[ast.AST] = list(reversed(unit.tree.body))
    budget = LoopBudget(max_steps=50_000)
    while stack:
        budget.tick()
        node = stack.pop()
        if isinstance(node, ast.If):
            _push_non_typechecking_if(node, stack)
            continue
        if isinstance(node, ast.Import | ast.ImportFrom):
            collected.append(node)
            continue
        if isinstance(node, ast.Try | ast.With | ast.AsyncWith | ast.For | ast.AsyncFor):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
    return collected


def _has_private_submodule_segment(dotted: str) -> bool:
    parts = dotted.split(".")
    return any(seg.startswith("_") and seg != "__init__" for seg in parts[1:])


def _python_stmt_dotted_targets(stmt: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(stmt, ast.Import):
        return [alias.name for alias in stmt.names]
    if stmt.level == 0 and stmt.module:
        return [stmt.module]
    return []


def _check_python_stmt_firewall(
    stmt: ast.Import | ast.ImportFrom,
    file_path: str,
    base_depth: int,
    src_member: str,
    py_pkg_owners: Mapping[str, set[str]],
) -> list[RawDiscrepancy]:
    span = SourceSpan(file_path=file_path, line=stmt.lineno, column=stmt.col_offset)
    if isinstance(stmt, ast.ImportFrom) and stmt.level >= 1 and (stmt.level - 1) > base_depth:
        return [
            RawDiscrepancy(
                code_id="JBC-COG-006",
                location=span,
                message="Relative import climbs above top-level Python package boundary.",
                remediation="Use absolute imports across package boundaries.",
            )
        ]
    findings: list[RawDiscrepancy] = []
    for dotted in _python_stmt_dotted_targets(stmt):
        root = dotted.split(".", maxsplit=1)[0]
        owners = py_pkg_owners.get(root, set())
        if owners and src_member not in owners and _has_private_submodule_segment(dotted):
            findings.append(
                RawDiscrepancy(
                    code_id="JBC-COG-006",
                    location=span,
                    message=(
                        f"Cross-member import '{dotted}' penetrates private submodule "
                        f"of package '{root}'."
                    ),
                    remediation=f"Import public symbols exported by '{root}' instead.",
                )
            )
    return findings


def _check_python_unit_firewalls(
    unit: SourceUnit,
    u_pl: UnitPlacement,
    py_pkg_owners: Mapping[str, set[str]],
) -> list[RawDiscrepancy]:
    base_depth = len(u_pl.compartment_dirs)
    out: list[RawDiscrepancy] = []
    for stmt in _iter_python_runtime_imports(unit):
        out.extend(
            _check_python_stmt_firewall(
                stmt,
                unit.file_path,
                base_depth,
                u_pl.member_id,
                py_pkg_owners,
            )
        )
    return out


def _index_python_package_owners(
    units: Sequence[InspectedUnit],
    placements: Mapping[str, UnitPlacement],
) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = {}
    for u in units:
        if isinstance(u, SourceUnit) and not u.is_test_file and u.module_name:
            pl = placements.get(u.file_path)
            if pl is not None:
                root_pkg = pl.package_key.split(":", maxsplit=1)[1]
                owners.setdefault(root_pkg, set()).add(pl.member_id)
                owners.setdefault(u.module_name.split(".", maxsplit=1)[0], set()).add(pl.member_id)
    return owners


def _check_workspace_firewalls(
    units: Sequence[InspectedUnit],
    placements: Mapping[str, UnitPlacement],
    target_paths: AbstractSet[str],
) -> list[RawDiscrepancy]:
    member_slugs = _member_slugs_index(placements)
    py_pkg_owners = _index_python_package_owners(units, placements)
    findings: list[RawDiscrepancy] = []
    for u in units:
        if u.file_path not in target_paths:
            continue
        u_pl = classify_unit_placement(u.file_path, _unit_language(u))
        if isinstance(u, SourceUnit):
            findings.extend(_check_python_unit_firewalls(u, u_pl, py_pkg_owners))
        else:
            findings.extend(_check_polyglot_unit_firewalls(u, u_pl.member_id, member_slugs))
    return findings


@jezz_callable
def check_cog_006_for_paths(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
    target_paths: Annotated[AbstractSet[IdentifierStr], WIRE_FROZENSET_VALIDATOR],
    *,
    profile: PermitProfile = PermitProfile.STANDARD,
) -> list[RawDiscrepancy]:
    """Run whole-workspace JBC-COG-006 and return discrepancies touching ``target_paths``."""
    if not units or not target_paths:
        return []
    limits = resolve_compartment_limits(profile)
    topo = _build_workspace_topology(units)
    summary = analyze_workspace_compartments(units)
    findings = [
        *_check_quotient_acyclicity(topo, target_paths),
        *_check_floorplate_and_portals(summary, topo, limits, target_paths),
        *_check_workspace_firewalls(units, topo.placements, target_paths),
    ]
    findings.sort(key=lambda d: (d.location.file_path, d.location.line, d.message))
    return findings


@jezz_callable
def check_cog_006(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
    *,
    profile: PermitProfile = PermitProfile.STANDARD,
) -> list[RawDiscrepancy]:
    """Run whole-workspace JBC-COG-006 inspection across all ``units``."""
    all_paths = frozenset(u.file_path for u in units)
    return check_cog_006_for_paths(units, all_paths, profile=profile)


__all__ = [
    "MAX_COMPARTMENT_MODULES_LIMIT",
    "MAX_COMPARTMENT_PORTAL_RATIO",
    "MAX_PACKAGE_MODULES_LIMIT",
    "MIN_PORTAL_RATIO_COMPARTMENT_SIZE",
    "STRICT_COMPARTMENT_MODULES_LIMIT",
    "STRICT_COMPARTMENT_PORTAL_RATIO",
    "STRICT_PACKAGE_MODULES_LIMIT",
    "CompartmentLimits",
    "CompartmentMetrics",
    "PackageFloorplate",
    "UnitPlacement",
    "WorkspaceCompartmentSummary",
    "analyze_workspace_compartments",
    "check_cog_006",
    "check_cog_006_for_paths",
    "classify_unit_placement",
    "resolve_compartment_limits",
]
