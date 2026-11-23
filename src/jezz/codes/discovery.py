"""Canonical polyglot workspace source discovery, manifest enrichment, and permit helpers."""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Annotated, Final

from pydantic import Field

from jezz.codes.adapters import parse_any_source_unit
from jezz.codes.ast_utils import SourceUnit
from jezz.codes.inspector import BuildingInspector, ManifestDependencies
from jezz.codes.permits import CodePermit
from jezz.codes.polyglot_ir import (
    EcosystemManifest,
    InspectedUnit,
    PolyglotManifest,
    PolyglotUnit,
    SupportedLanguage,
    detect_language_for_path,
    is_supported_source_path,
)
from jezz.guards import (
    WIRE_FROZENSET_VALIDATOR,
    ContainedPath,
    IdentifierStr,
    SourceCodeText,
    jezz_callable,
    read_bounded_text,
)

_PRUNED_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "__pycache__",
        "__snapshots__",
        "venv",
        "env",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "target",
        "vendor",
        "coverage",
        "out",
        "testdata",
        "ui",
        "compile_fail",
    }
)

_PRIMARY_LAYOUT_DIRS: Final[tuple[str, ...]] = (
    "src",
    "crates",
    "packages",
    "apps",
    "cmd",
    "internal",
    "pkg",
)

_AUX_TEST_DIRS: Final[tuple[str, ...]] = ("tests", "test", "benches", "__tests__")


def _is_pruned_relative_path(rel_parts: Sequence[str]) -> bool:
    for part in rel_parts[:-1]:
        if part == ".":
            continue
        if part.startswith(".") or part.endswith(".egg-info") or part in _PRUNED_DIR_NAMES:
            return True
    return False


def _resolve_scan_dirs(resolved_root: Path, target_dirs: Sequence[str]) -> tuple[str, ...]:
    if target_dirs:
        return tuple(target_dirs)
    present_primary = [
        rel
        for rel in _PRIMARY_LAYOUT_DIRS
        if ContainedPath.from_parts(resolved_root, rel).resolved_path.is_dir()
    ]
    if not present_primary:
        return (".",)
    present_aux = [
        rel
        for rel in _AUX_TEST_DIRS
        if ContainedPath.from_parts(resolved_root, rel).resolved_path.is_dir()
    ]
    root_polyglot_files = [
        entry.name
        for entry in sorted(resolved_root.iterdir())
        if entry.is_file()
        and entry.suffix.lower() in {".go", ".rs", ".ts", ".tsx"}
        and is_supported_source_path(entry.name)
    ]
    return tuple([*present_primary, *present_aux, *root_polyglot_files])


def _matches_language_filter(
    rel_posix: str,
    languages: AbstractSet[SupportedLanguage] | None,
) -> bool:
    if not is_supported_source_path(rel_posix):
        return False
    if languages is None:
        return True
    lang = detect_language_for_path(rel_posix)
    return lang is not None and lang in languages


def _iter_dir_candidates(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*") if p.is_file())


def _collect_source_files(
    resolved_root: Path,
    scan_dirs: Sequence[str],
    languages: AbstractSet[SupportedLanguage] | None,
) -> list[Path]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for rel_dir in scan_dirs:
        contained_dir = ContainedPath.from_parts(resolved_root, rel_dir)
        if not contained_dir.resolved_path.exists():
            continue
        for candidate in _iter_dir_candidates(contained_dir.resolved_path):
            if candidate in seen:
                continue
            rel_path = candidate.relative_to(resolved_root)
            rel_posix = rel_path.as_posix()
            if _is_pruned_relative_path(rel_path.parts):
                continue
            if _matches_language_filter(rel_posix, languages):
                seen.add(candidate)
                ordered.append(candidate)
    ordered.sort(key=lambda p: p.relative_to(resolved_root).as_posix())
    return ordered


@jezz_callable
def discover_workspace_units(
    repo_root: Path,
    *,
    target_dirs: Annotated[Sequence[IdentifierStr], Field(max_length=64)] = (),
    languages: Annotated[AbstractSet[SupportedLanguage], WIRE_FROZENSET_VALIDATOR] | None = None,
) -> tuple[InspectedUnit, ...]:
    """Discover and parse all supported source units (Python, TS, Rust, Go) under ``repo_root``."""
    resolved_root = repo_root.resolve()
    if not resolved_root.is_dir():
        msg = f"Workspace directory does not exist: '{repo_root}'."
        raise FileNotFoundError(msg)
    scan_dirs = _resolve_scan_dirs(resolved_root, target_dirs)
    units: list[InspectedUnit] = []
    for src_path in _collect_source_files(resolved_root, scan_dirs, languages):
        rel_posix = src_path.relative_to(resolved_root).as_posix()
        contained = ContainedPath.from_parts(resolved_root, rel_posix)
        source = read_bounded_text(contained)
        units.append(parse_any_source_unit(contained.relative_posix, source))
    return tuple(units)


@jezz_callable
def require_workspace_units(
    repo_path: Path,
    target_dirs: Annotated[Sequence[IdentifierStr], Field(max_length=64)] = (),
    *,
    languages: Annotated[AbstractSet[SupportedLanguage], WIRE_FROZENSET_VALIDATOR] | None = None,
) -> tuple[Path, tuple[InspectedUnit, ...]]:
    """Discover workspace units under ``repo_path`` or raise ``ValueError`` if empty."""
    resolved_repo = repo_path.resolve()
    units = discover_workspace_units(
        resolved_repo,
        target_dirs=target_dirs,
        languages=languages,
    )
    if not units:
        msg = (
            "Zero Python source files or polyglot units (.py, .ts, .tsx, .rs, .go) "
            f"discovered under '{repo_path}'."
        )
        raise ValueError(msg)
    return resolved_repo, units


def _extract_local_python_roots(units: Sequence[InspectedUnit]) -> set[str]:
    roots: set[str] = set()
    for unit in units:
        if not isinstance(unit, SourceUnit) or not unit.module_name or unit.is_test_file:
            continue
        top = unit.module_name.split(".", maxsplit=1)[0]
        if top.isidentifier():
            roots.add(top)
            continue
        stem = PurePosixPath(unit.file_path).stem
        if stem.isidentifier():
            roots.add(stem)
    return roots


def _extract_local_polyglot_roots(
    units: Sequence[InspectedUnit],
    language: SupportedLanguage,
) -> set[str]:
    roots: set[str] = set()
    for unit in units:
        if not isinstance(unit, PolyglotUnit) or unit.language != language or unit.is_test_file:
            continue
        if unit.module_name:
            top = unit.module_name.split(".", maxsplit=1)[0].split("/", maxsplit=1)[0]
            if top:
                roots.add(top[:256])
    return roots


def _enrich_eco(base_eco: EcosystemManifest, extra_roots: set[str]) -> EcosystemManifest:
    if not extra_roots:
        return base_eco
    return EcosystemManifest(
        runtime_roots=base_eco.runtime_roots,
        dev_roots=base_eco.dev_roots,
        workspace_roots=frozenset(base_eco.workspace_roots | extra_roots),
        module_prefix=base_eco.module_prefix,
    )


@jezz_callable
def build_workspace_inspector(
    resolved_repo: Path,
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
) -> BuildingInspector:
    """Build a ``BuildingInspector`` enriched with local Python, TS, Rust, and Go roots."""
    base = BuildingInspector.from_workspace(resolved_repo)
    local_py = _extract_local_python_roots(units)
    py_manifest = ManifestDependencies(
        runtime_roots=base.manifest.runtime_roots,
        dev_roots=base.manifest.dev_roots,
        workspace_roots=frozenset(base.manifest.workspace_roots | local_py),
    )
    poly_base = base.polyglot_manifest
    poly_manifest = PolyglotManifest(
        python=_enrich_eco(poly_base.python, local_py),
        typescript=_enrich_eco(
            poly_base.typescript,
            _extract_local_polyglot_roots(units, SupportedLanguage.TYPESCRIPT),
        ),
        rust=_enrich_eco(
            poly_base.rust,
            _extract_local_polyglot_roots(units, SupportedLanguage.RUST),
        ),
        go=_enrich_eco(
            poly_base.go,
            _extract_local_polyglot_roots(units, SupportedLanguage.GO),
        ),
    )
    return BuildingInspector(manifest=py_manifest, polyglot_manifest=poly_manifest)


@jezz_callable
def read_contained_json(target: ContainedPath) -> SourceCodeText:
    """Read UTF-8 JSON text from ``target`` or raise ``FileNotFoundError`` if missing."""
    if not target.resolved_path.is_file():
        msg = f"File does not exist: '{target.relative_posix}'."
        raise FileNotFoundError(msg)
    return read_bounded_text(target)


@jezz_callable
def load_contained_permit(permit_target: ContainedPath | None) -> CodePermit | None:
    """Validate an optional ``CodePermit`` JSON file from ``permit_target``."""
    if permit_target is None:
        return None
    return CodePermit.model_validate_json(read_contained_json(permit_target))


@jezz_callable
def parse_optional_iso_date(raw_date: IdentifierStr | None) -> date | None:
    """Parse an optional ``YYYY-MM-DD`` ISO-8601 date string."""
    if raw_date is None:
        return None
    return date.fromisoformat(raw_date)
