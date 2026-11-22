"""Canonical workspace source discovery, manifest enrichment, and permit loading helpers."""

from collections.abc import Sequence
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Annotated, Final

from pydantic import Field

from jezz.codes.ast_utils import SourceUnit, parse_source_unit
from jezz.codes.inspector import BuildingInspector, ManifestDependencies
from jezz.codes.permits import CodePermit
from jezz.guards import (
    ContainedPath,
    IdentifierStr,
    SourceCodeText,
    jezz_callable,
    read_bounded_text,
)

_PRUNED_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "__pycache__",
        "venv",
        "env",
        "build",
        "dist",
        "node_modules",
        "site-packages",
    }
)


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
    src_candidate = ContainedPath.from_parts(resolved_root, "src")
    if src_candidate.resolved_path.is_dir():
        return "src", "tests", "test"
    return (".",)


def _collect_python_files(resolved_root: Path, scan_dirs: Sequence[str]) -> list[Path]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for rel_dir in scan_dirs:
        contained_dir = ContainedPath.from_parts(resolved_root, rel_dir)
        if not contained_dir.resolved_path.exists():
            continue
        candidates = (
            [contained_dir.resolved_path]
            if contained_dir.resolved_path.is_file()
            else sorted(contained_dir.resolved_path.rglob("*.py"))
        )
        for py_path in candidates:
            rel_parts = py_path.relative_to(resolved_root).parts
            if py_path not in seen and not _is_pruned_relative_path(rel_parts):
                seen.add(py_path)
                ordered.append(py_path)
    return ordered


@jezz_callable
def discover_workspace_units(
    repo_root: Path,
    *,
    target_dirs: Annotated[Sequence[IdentifierStr], Field(max_length=64)] = (),
) -> tuple[SourceUnit, ...]:
    """Discover and parse Python ``SourceUnit`` files under ``repo_root`` safely."""
    resolved_root = repo_root.resolve()
    if not resolved_root.is_dir():
        msg = f"Workspace directory does not exist: '{repo_root}'."
        raise FileNotFoundError(msg)
    scan_dirs = _resolve_scan_dirs(resolved_root, target_dirs)
    units: list[SourceUnit] = []
    for py_path in _collect_python_files(resolved_root, scan_dirs):
        rel_posix = py_path.relative_to(resolved_root).as_posix()
        contained = ContainedPath.from_parts(resolved_root, rel_posix)
        source = read_bounded_text(contained)
        units.append(parse_source_unit(contained.relative_posix, source))
    return tuple(units)


@jezz_callable
def require_workspace_units(
    repo_path: Path,
    target_dirs: Annotated[Sequence[IdentifierStr], Field(max_length=64)] = (),
) -> tuple[Path, tuple[SourceUnit, ...]]:
    """Discover workspace units under ``repo_path`` or raise ``ValueError`` if empty."""
    resolved_repo = repo_path.resolve()
    units = discover_workspace_units(resolved_repo, target_dirs=target_dirs)
    if not units:
        msg = f"Zero Python source files discovered under '{repo_path}'."
        raise ValueError(msg)
    return resolved_repo, units


def _extract_local_unit_roots(units: Sequence[SourceUnit]) -> set[str]:
    roots: set[str] = set()
    for unit in units:
        if not unit.module_name or unit.is_test_file:
            continue
        top = unit.module_name.split(".", maxsplit=1)[0]
        if top.isidentifier():
            roots.add(top)
            continue
        stem = PurePosixPath(unit.file_path).stem
        if stem.isidentifier():
            roots.add(stem)
    return roots


@jezz_callable
def build_workspace_inspector(
    resolved_repo: Path,
    units: Sequence[SourceUnit],
) -> BuildingInspector:
    """Build a ``BuildingInspector`` enriched with local workspace package roots."""
    base = BuildingInspector.from_workspace(resolved_repo)
    local_roots = _extract_local_unit_roots(units)
    manifest = ManifestDependencies(
        runtime_roots=base.manifest.runtime_roots,
        dev_roots=base.manifest.dev_roots,
        workspace_roots=frozenset(base.manifest.workspace_roots | local_roots),
    )
    return BuildingInspector(manifest=manifest)


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
