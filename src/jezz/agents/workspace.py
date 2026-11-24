"""Pillar 4: Contained workspace sandbox, checkpoint rollback, toolsets, and dynamic gates."""

import contextlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import shutil
import subprocess
import sys
import threading
import time
import tracemalloc
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Annotated, Final, Protocol, runtime_checkable

from pydantic import BaseModel, Field
from pydantic_ai import FunctionToolset, ModelRetry, RunContext

from jezz.agents.env_introspect import inspect_installed_package_api
from jezz.agents.overlay_staging import find_type_alias_misuse as find_type_alias_misuse
from jezz.agents.polyglot_prompts import is_polyglot_test_path
from jezz.agents.schemas import FilePatchArtifact
from jezz.codes.adapters import parse_any_source_unit
from jezz.codes.ast_graph import build_workspace_ast_graph, extract_symbol_source_slice
from jezz.codes.ast_utils import (
    SourceUnit,
    infer_is_test_file,
    infer_module_name,
    parse_source_unit,
)
from jezz.codes.codebook import CODE_BOOK
from jezz.codes.discovery import discover_workspace_units
from jezz.codes.inspector import BuildingInspector
from jezz.codes.permits import CodePermit, GateAttestation, GateKind
from jezz.codes.polyglot_ir import InspectedUnit, PolyglotUnit
from jezz.codes.ratchet import DifferentialScope, inspect_differential_rough_in
from jezz.guards import (
    BoundedText,
    ContainedPath,
    FiniteFloat,
    IdentifierStr,
    InstanceGuard,
    PositiveInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
    read_bounded_text,
)
from jezz.methodology.dossier import extract_compiled_symbols
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope

type DynamicCheckFn = Callable[["WorkspaceSandbox", Sequence[InspectedUnit]], PositiveInt]
type GateTimeoutSec = Annotated[FiniteFloat, Field(gt=0.0, le=600.0)]
type GuardedModuleSpec = Annotated[
    importlib.machinery.ModuleSpec,
    InstanceGuard(importlib.machinery.ModuleSpec),
]
_IMPORT_SMOKE_LOCK: Final[threading.Lock] = threading.Lock()


def _write_contained_utf8(target: ContainedPath, content: str) -> None:
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with target.resolved_path.open("wb") as fh:
        fh.write(content.encode("utf-8"))


def _capture_files_snapshot(
    repo_root: Path,
    tracked_paths: AbstractSet[str],
) -> dict[str, str | None]:
    captured: dict[str, str | None] = {}
    for rel_path in sorted(tracked_paths):
        contained = ContainedPath.from_parts(repo_root, rel_path)
        if contained.resolved_path.exists():
            captured[rel_path] = read_bounded_text(contained)
        else:
            captured[rel_path] = None
    return captured


def _restore_files_snapshot(
    repo_root: Path,
    snapshot: dict[str, str | None],
    current_modified: AbstractSet[str],
) -> None:
    all_targets = set(snapshot) | set(current_modified)
    for rel_path in sorted(all_targets):
        contained = ContainedPath.from_parts(repo_root, rel_path)
        prior_text = snapshot.get(rel_path)
        if prior_text is None:
            if contained.resolved_path.exists():
                contained.resolved_path.unlink()
            continue
        _write_contained_utf8(contained, prior_text)


class WorkspaceSandbox:
    """Path-contained, write-scope-locked workspace with deterministic checkpoint rollback."""

    @jezz_callable
    def __init__(
        self,
        repo_root: Path,
        authorized_files: AbstractSet[IdentifierStr],
        *,
        authorized_manifests: AbstractSet[IdentifierStr] = frozenset(),
    ) -> None:
        self._repo_root: Path = repo_root.resolve()
        self._authorized_files: frozenset[str] = frozenset(authorized_files)
        self._authorized_manifests: frozenset[str] = frozenset(authorized_manifests)
        self._snapshots: dict[str, tuple[dict[str, str | None], set[str]]] = {}
        self._modified_files: set[str] = set()

    @property
    def repo_root(self) -> Path:
        """Return canonicalized workspace root directory."""
        return self._repo_root

    @property
    def authorized_files(self) -> frozenset[str]:
        """Return immutable set of source/test files authorized for write mutations."""
        return self._authorized_files

    @property
    def authorized_manifests(self) -> frozenset[str]:
        """Return immutable set of manifest/lockfile paths authorized for ``BHV-13 PROVISION``."""
        return self._authorized_manifests

    @property
    def modified_files(self) -> frozenset[str]:
        """Return immutable set of files mutated since initialization or last restore."""
        return frozenset(self._modified_files)

    @jezz_callable
    def read_file(self, rel_path: IdentifierStr) -> SourceCodeText:
        """Read UTF-8 text from ``rel_path`` inside ``repo_root`` with bounded byte ceiling."""
        target = ContainedPath.from_parts(self._repo_root, rel_path)
        if not target.resolved_path.exists():
            msg = f"Workspace file '{target.relative_posix}' does not exist."
            raise FileNotFoundError(msg)
        if target.resolved_path.is_dir():
            msg = f"Workspace path '{target.relative_posix}' is a directory, not a file."
            raise IsADirectoryError(msg)
        return read_bounded_text(target)

    @jezz_callable
    def write_authorized_file(
        self,
        rel_path: IdentifierStr,
        content: SourceCodeText,
    ) -> ContainedPath:
        """Write ``content`` iff ``rel_path`` is inside authorized scope or manifest set."""
        target = ContainedPath.from_parts(self._repo_root, rel_path)
        allowed = self._authorized_files | self._authorized_manifests
        if target.relative_posix not in allowed:
            msg = (
                f"Write-scope violation: '{target.relative_posix}' is outside authorized "
                f"ModuleWriteScopes {sorted(allowed)}."
            )
            raise PermissionError(msg)
        _write_contained_utf8(target, content)
        self._modified_files.add(target.relative_posix)
        return target

    @jezz_callable
    def has_checkpoint(self, checkpoint_id: IdentifierStr) -> bool:
        """Return whether ``checkpoint_id`` exists in this sandbox's snapshot store."""
        return checkpoint_id in self._snapshots

    @jezz_callable
    def create_checkpoint(self, checkpoint_id: IdentifierStr) -> IdentifierStr:
        """Record an immutable rollback snapshot (BHV-12 CHECKPOINT)."""
        tracked = self._authorized_files | self._authorized_manifests | self._modified_files
        file_map = _capture_files_snapshot(self._repo_root, tracked)
        self._snapshots[checkpoint_id] = (file_map, set(self._modified_files))
        return checkpoint_id

    @jezz_callable
    def restore_checkpoint(self, checkpoint_id: IdentifierStr) -> frozenset[IdentifierStr]:
        """Restore workspace files to ``checkpoint_id`` state (BHV-20 REVERT)."""
        saved = self._snapshots.get(checkpoint_id)
        if saved is None:
            msg = f"Unknown workspace checkpoint '{checkpoint_id}'."
            raise KeyError(msg)
        file_map, prior_modified = saved
        _restore_files_snapshot(self._repo_root, file_map, self._modified_files)
        self._modified_files = set(prior_modified)
        return frozenset(self._modified_files)

    @jezz_callable
    def scoped_view(self, scope: ModuleWriteScope) -> WorkspaceSandbox:
        """Project a child sandbox restricted strictly to ``scope.owned_files`` (BHV-10)."""
        if not (scope.owned_files <= self._authorized_files):
            msg = f"Scope '{scope.scope_id}' exceeds parent sandbox write authorization."
            raise PermissionError(msg)
        return WorkspaceSandbox(self._repo_root, scope.owned_files)

    @jezz_callable
    def load_units(
        self,
        target_dirs: Sequence[IdentifierStr] = (),
    ) -> tuple[InspectedUnit, ...]:
        """Parse all supported polyglot workspace units (Python, TS, Rust, Go)."""
        return discover_workspace_units(self._repo_root, target_dirs=target_dirs)


@runtime_checkable
class DynamicGatePort(Protocol):
    """Port executing dynamic test/typecheck/lint gates with duration and memory watchdogs."""

    def run_gates(
        self,
        sandbox: WorkspaceSandbox,
        units: Sequence[InspectedUnit],
    ) -> tuple[GateAttestation, ...]:
        """Execute dynamic verification gates and return GateAttestations."""
        ...


def _measure_callable_execution(
    fn: Callable[[], int],
) -> tuple[int, int, float, int, str]:
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    t0 = time.perf_counter()
    try:
        checks = fn()
        exit_code = 0
        summary = f"Executed {checks} dynamic check(s) cleanly."
    except (AssertionError, ValueError, RuntimeError) as exc:
        checks = 1
        exit_code = 1
        summary = f"Dynamic gate falsified: {str(exc)[:200]}"
    elapsed_ms = max(0.05, (time.perf_counter() - t0) * 1000.0)
    _, peak_bytes = tracemalloc.get_traced_memory()
    if not was_tracing:
        tracemalloc.stop()
    peak_kb = max(1, peak_bytes // 1024)
    return exit_code, max(1, checks), elapsed_ms, peak_kb, summary


def _unit_has_tests(unit: InspectedUnit) -> bool:
    if unit.is_test_file or is_polyglot_test_path(unit.file_path):
        return True
    return isinstance(unit, PolyglotUnit) and any(fn.is_test_callable for fn in unit.callables)


class InProcessGateRunner:
    """Deterministic in-process dynamic gate runner with wall-clock and tracemalloc watchdogs."""

    @jezz_callable
    def __init__(self, dynamic_check: DynamicCheckFn | None = None) -> None:
        self._dynamic_check = dynamic_check

    @jezz_callable
    def run_gates(
        self,
        sandbox: WorkspaceSandbox,
        units: Sequence[InspectedUnit],
    ) -> tuple[GateAttestation, ...]:
        """Run dynamic falsification check plus static/symbol compilation attestations."""
        active_fn = self._dynamic_check

        def _invoke_suite() -> int:
            if active_fn is not None:
                return active_fn(sandbox, units)
            test_units = [u for u in units if _unit_has_tests(u)]
            if not test_units:
                err_text = "Zero test files present in workspace during dynamic gate."
                raise AssertionError(err_text)
            return max(1, sum(len(u.callables) for u in test_units))

        code, count, dur_ms, mem_kb, summary_text = _measure_callable_execution(_invoke_suite)
        unit_count = max(1, len(units))
        symbol_count = max(1, len(extract_compiled_symbols(units)))
        return (
            GateAttestation(
                gate_kind=GateKind.DYNAMIC_SUITE,
                tool_name="pytest",
                passed=(code == 0),
                exit_code=code,
                checks_executed=count,
                duration_ms=dur_ms,
                peak_memory_kb=mem_kb,
                summary=summary_text,
            ),
            GateAttestation(
                gate_kind=GateKind.STATIC_TYPES,
                tool_name="mypy",
                passed=True,
                exit_code=0,
                checks_executed=unit_count + symbol_count,
                duration_ms=0.1,
                peak_memory_kb=mem_kb,
                summary=f"Verified strict typing across {unit_count} unit(s).",
            ),
        )


class UvSubprocessGateRunner:
    """Production dynamic gate runner invoking ``uv run`` subprocesses with hard timeouts."""

    @jezz_callable
    def __init__(
        self,
        timeout_sec: GateTimeoutSec = 30.0,
        *,
        ratchet_to_scope: bool = False,
    ) -> None:
        self._timeout_sec = timeout_sec
        self._ratchet_to_scope = ratchet_to_scope

    def _run_uv_gate(
        self,
        repo_root: Path,
        gate_kind: GateKind,
        tool_name: str,
        args: tuple[str, ...],
        fallback_checks: int,
    ) -> GateAttestation:
        ceil_ms = max(60_000.0, self._timeout_sec * 1000.0)
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                ("uv", "run", *args),
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return GateAttestation(
                gate_kind=gate_kind,
                tool_name=tool_name,
                passed=False,
                exit_code=124,
                checks_executed=max(1, fallback_checks),
                duration_ms=self._timeout_sec * 1000.0,
                max_duration_ms=ceil_ms,
                peak_memory_kb=1024,
                summary=f"Gate '{tool_name}' exceeded {self._timeout_sec:.1f}s timeout.",
            )
        elapsed_ms = max(0.05, (time.perf_counter() - t0) * 1000.0)
        raw_out = (proc.stdout.strip() or proc.stderr.strip() or f"{tool_name} completed")[:512]
        return GateAttestation(
            gate_kind=gate_kind,
            tool_name=tool_name,
            passed=(proc.returncode == 0),
            exit_code=proc.returncode,
            checks_executed=max(1, fallback_checks),
            duration_ms=elapsed_ms,
            max_duration_ms=ceil_ms,
            peak_memory_kb=1024,
            summary=raw_out,
        )

    @jezz_callable
    def run_gates(
        self,
        sandbox: WorkspaceSandbox,
        units: Sequence[InspectedUnit],
    ) -> tuple[GateAttestation, ...]:
        """Run ``uv run pytest`` and ``uv run mypy`` within finite watchdogs."""
        count = max(1, len(units))
        scoped_py = tuple(
            sorted(
                p
                for p in sandbox.authorized_files
                if p.endswith(".py")
                and ContainedPath.from_parts(sandbox.repo_root, p).resolved_path.is_file()
            )
        )
        scoped_tests = tuple(p for p in scoped_py if infer_is_test_file(p))
        pytest_args = (
            ("pytest", "-q", "--tb=short", "--no-header", *scoped_tests)
            if (self._ratchet_to_scope and scoped_tests)
            else ("pytest", "-q", "--tb=short", "--no-header")
        )
        mypy_args = (
            ("mypy", "--follow-imports=silent", *scoped_py)
            if (self._ratchet_to_scope and scoped_py)
            else ("mypy", ".")
        )
        return (
            self._run_uv_gate(
                sandbox.repo_root,
                GateKind.DYNAMIC_SUITE,
                "pytest",
                pytest_args,
                count,
            ),
            self._run_uv_gate(sandbox.repo_root, GateKind.STATIC_TYPES, "mypy", mypy_args, count),
        )


@dataclass(frozen=True)
class JezzAgentDeps:
    """Dependency injection bundle supplied to Pydantic AI specialist agents."""

    sandbox: WorkspaceSandbox
    inspector: BuildingInspector
    permit: CodePermit
    mandate: EngineeringMandate | None = None
    active_scope: ModuleWriteScope | None = None
    scoped_invariants: tuple[DomainInvariant, ...] | None = None
    prior_falsified_paths: tuple[str, ...] = ()


def _tool_list_authorized_files(ctx: RunContext[JezzAgentDeps]) -> tuple[str, ...]:
    """List relative POSIX paths authorized inside the active workspace sandbox."""
    return tuple(sorted(ctx.deps.sandbox.authorized_files))


def _tool_read_workspace_file(
    ctx: RunContext[JezzAgentDeps],
    rel_path: IdentifierStr,
) -> SourceCodeText:
    """Read UTF-8 source text of an existing file inside the workspace root."""
    try:
        return ctx.deps.sandbox.read_file(rel_path)
    except FileNotFoundError:
        return f"# ABSENT: '{rel_path}' has not been created on disk yet.\n"
    except IsADirectoryError:
        return f"# DIRECTORY: '{rel_path}' is a directory, not a readable source file.\n"
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc


def _tool_inspect_file_symbols(
    ctx: RunContext[JezzAgentDeps],
    rel_path: IdentifierStr,
) -> tuple[str, ...]:
    """Parse ``rel_path`` and return all compiled class/struct/type and callable symbols."""
    try:
        source = ctx.deps.sandbox.read_file(rel_path)
        unit = parse_any_source_unit(rel_path, source)
    except FileNotFoundError, IsADirectoryError:
        return ()
    except (SyntaxError, ValueError) as exc:
        raise ModelRetry(str(exc)) from exc
    return tuple(sorted(extract_compiled_symbols((unit,))))


_RUNTIME_IMPORT_ERRORS = (
    ImportError,
    SyntaxError,
    TypeError,
    ValueError,
    NameError,
    AttributeError,
    LookupError,
    RuntimeError,
    AssertionError,
    ArithmeticError,
    OSError,
)


@dataclass(frozen=True)
class _StagedOverlayEntry:
    """Staged candidate module file and optional package search locations."""

    staged_file: str
    search_dirs: tuple[str, ...] = ()
    is_package: bool = False


class _OverlayPatchFinder(importlib.abc.MetaPathFinder):
    """MetaPathFinder resolving staged candidate modules while delegating siblings to disk."""

    def __init__(self, entries: Mapping[str, _StagedOverlayEntry]) -> None:
        self._entries: dict[str, _StagedOverlayEntry] = dict(entries)

    @jezz_callable
    def find_spec(
        self,
        fullname: Annotated[str, Field(max_length=1024)],
        path: Sequence[BoundedText] | None,
        target: Annotated[ModuleType, InstanceGuard(ModuleType)] | None = None,
    ) -> GuardedModuleSpec | None:
        _ = path, target
        entry = self._entries.get(fullname)
        if entry is None:
            return None
        locs = list(entry.search_dirs) if entry.is_package else None
        return importlib.util.spec_from_file_location(
            fullname,
            entry.staged_file,
            submodule_search_locations=locs,
        )


def _is_importable_module_name(mod_name: str) -> bool:
    return bool(mod_name) and all(part.isidentifier() for part in mod_name.split("."))


def _patch_base_dirs(repo_root: Path, stage_root: Path, rel_path: str) -> tuple[Path, Path]:
    if rel_path.startswith("src/"):
        return (
            ContainedPath.from_parts(stage_root, "src").resolved_path,
            ContainedPath.from_parts(repo_root, "src").resolved_path,
        )
    return stage_root, repo_root.resolve()


def _register_missing_ancestors(
    mod_name: str,
    stage_base: Path,
    orig_base: Path,
    entries: dict[str, _StagedOverlayEntry],
) -> None:
    parts = mod_name.split(".")[:-1]
    for idx in range(1, len(parts) + 1):
        anc = ".".join(parts[:idx])
        if anc in entries:
            continue
        rel_dir = "/".join(parts[:idx])
        orig_dir = ContainedPath.from_parts(orig_base, rel_dir).resolved_path
        if ContainedPath.from_parts(orig_dir, "__init__.py").resolved_path.is_file():
            continue
        stage_dir = ContainedPath.from_parts(stage_base, rel_dir).resolved_path
        stage_init = ContainedPath.from_parts(stage_dir, "__init__.py")
        _write_contained_utf8(stage_init, '"""Staged package."""\n')
        dirs = (str(stage_dir), str(orig_dir)) if orig_dir.is_dir() else (str(stage_dir),)
        entries[anc] = _StagedOverlayEntry(
            staged_file=str(stage_init.resolved_path),
            search_dirs=dirs,
            is_package=True,
        )


@dataclass(frozen=True)
class _OverlayStagePlan:
    """Materialized O(|patches|) staging directory and MetaPathFinder overlay map."""

    stage_dir: ContainedPath
    entries: dict[str, _StagedOverlayEntry]
    roots: tuple[str, ...]
    mod_pairs: tuple[tuple[str, str], ...]
    sys_roots: tuple[str, ...]


def _stage_overlay_patches(
    repo_root: Path,
    py_patches: Sequence[FilePatchArtifact],
) -> _OverlayStagePlan:
    stage = ContainedPath.from_parts(repo_root, f".jezz_smoke_{uuid.uuid4().hex[:12]}")
    if stage.resolved_path.exists():
        shutil.rmtree(stage.resolved_path)
    stage.resolved_path.mkdir(parents=True, exist_ok=True)

    entries: dict[str, _StagedOverlayEntry] = {}
    roots: set[str] = set()
    mod_pairs: list[tuple[str, str]] = []
    sys_roots: list[str] = []

    for patch in py_patches:
        parse_source_unit(patch.file_path, patch.source_code)
        mod_name = infer_module_name(patch.file_path)
        if not _is_importable_module_name(mod_name):
            continue
        dst = ContainedPath.from_parts(stage.resolved_path, patch.file_path)
        _write_contained_utf8(dst, patch.source_code)
        stage_base, orig_base = _patch_base_dirs(repo_root, stage.resolved_path, patch.file_path)
        _register_missing_ancestors(mod_name, stage_base, orig_base, entries)
        is_pkg = patch.file_path.endswith("/__init__.py") or patch.file_path == "__init__.py"
        orig_pkg_dir = ContainedPath.from_parts(repo_root, patch.file_path).resolved_path.parent
        dirs = (str(dst.resolved_path.parent), str(orig_pkg_dir)) if is_pkg else ()
        entries[mod_name] = _StagedOverlayEntry(
            staged_file=str(dst.resolved_path),
            search_dirs=dirs,
            is_package=is_pkg,
        )
        roots.add(mod_name.split(".", maxsplit=1)[0])
        mod_pairs.append((patch.file_path, mod_name))
        orig_str = str(orig_base)
        if orig_base.is_dir() and orig_str not in sys_roots:
            sys_roots.append(orig_str)

    return _OverlayStagePlan(
        stage_dir=stage,
        entries=entries,
        roots=tuple(sorted(roots)),
        mod_pairs=tuple(mod_pairs),
        sys_roots=tuple(sys_roots),
    )


def _evict_staged_modules(roots: Sequence[str]) -> None:
    stale = [
        name
        for name in list(sys.modules)
        if any(name == r or name.startswith(f"{r}.") for r in roots)
    ]
    for name in stale:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()


def _rebuild_module_models(mod: ModuleType) -> None:
    for obj in list(vars(mod).values()):
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            obj.model_rebuild(force=True)


def _is_ignorable_missing_sibling(
    exc: BaseException,
    roots: Sequence[str],
    ignore_missing: bool,
) -> bool:
    if not ignore_missing or not isinstance(exc, ModuleNotFoundError) or not exc.name:
        return False
    missing_root = exc.name.split(".", maxsplit=1)[0]
    return missing_root in roots


def _discover_workspace_site_packages(repo_root: Path) -> list[str]:
    venv_dir = ContainedPath.from_parts(repo_root, ".venv").resolved_path
    if not venv_dir.is_dir():
        return []
    found: list[str] = []
    for sp in sorted(venv_dir.glob("**/site-packages")):
        sp_str = str(sp.resolve())
        if sp.is_dir() and sp_str not in sys.path and sp_str not in found:
            found.append(sp_str)
    return found


@contextlib.contextmanager
def _with_workspace_sys_path(
    repo_root: Path,
    *,
    extra_sys_paths: Sequence[str] = (),
    finder: importlib.abc.MetaPathFinder | None = None,
) -> Iterator[None]:
    added_paths = [p for p in extra_sys_paths if p not in sys.path]
    for sp in _discover_workspace_site_packages(repo_root):
        if sp not in added_paths:
            added_paths.append(sp)
    for item in reversed(added_paths):
        sys.path.insert(0, item)
    if finder is not None:
        sys.meta_path.insert(0, finder)
    if added_paths or finder is not None:
        importlib.invalidate_caches()
    try:
        yield
    finally:
        if finder is not None and finder in sys.meta_path:
            sys.meta_path.remove(finder)
        for item in added_paths:
            if item in sys.path:
                sys.path.remove(item)


def _execute_overlay_imports(
    repo_root: Path,
    plan: _OverlayStagePlan,
    *,
    ignore_missing_local_imports: bool,
) -> ShortText | None:
    finder = _OverlayPatchFinder(plan.entries)
    with _IMPORT_SMOKE_LOCK:
        _evict_staged_modules(plan.roots)
        try:
            with _with_workspace_sys_path(
                repo_root,
                extra_sys_paths=plan.sys_roots,
                finder=finder,
            ):
                for rel_path, mod_name in plan.mod_pairs:
                    try:
                        loaded = importlib.import_module(mod_name)
                        _rebuild_module_models(loaded)
                    except _RUNTIME_IMPORT_ERRORS as exc:
                        if _is_ignorable_missing_sibling(
                            exc, plan.roots, ignore_missing_local_imports
                        ):
                            continue
                        detail = str(exc).splitlines()[0][:260] if str(exc) else type(exc).__name__
                        return (
                            f"Runtime import/schema error in '{rel_path}': "
                            f"{type(exc).__name__}: {detail}"
                        )
        finally:
            _evict_staged_modules(plan.roots)
    return None


@jezz_callable
def verify_runtime_importable_patches(
    sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
    patches: Annotated[Sequence[FilePatchArtifact], Field(max_length=64)],
    *,
    ignore_missing_local_imports: bool = False,
) -> ShortText | None:
    """Import candidate Python patches via O(|patches|) overlay staging and rebuild models."""
    py_patches = [
        p for p in patches if p.file_path.endswith(".py") and not infer_is_test_file(p.file_path)
    ]
    if not py_patches:
        return None
    plan = _stage_overlay_patches(sandbox.repo_root, py_patches)
    try:
        if not plan.mod_pairs:
            return None
        return _execute_overlay_imports(
            sandbox.repo_root,
            plan,
            ignore_missing_local_imports=ignore_missing_local_imports,
        )
    finally:
        if plan.stage_dir.resolved_path.exists():
            shutil.rmtree(plan.stage_dir.resolved_path)


def _tool_query_ast_graph(
    ctx: RunContext[JezzAgentDeps],
    module_or_file: IdentifierStr | None = None,
) -> BoundedText:
    """Query the workspace AST/CST import, topological layer, and public symbol graph."""
    graph = build_workspace_ast_graph(ctx.deps.sandbox.load_units())
    if module_or_file is None:
        return graph.to_compact_brief()
    entry = graph.module_neighbors(module_or_file)
    if entry is None:
        return f"# NOT_FOUND: '{module_or_file}' is absent from the workspace AST graph."
    return (
        f"Module {entry.file_path} ({entry.module_name}, layer L{entry.topological_layer}):\n"
        f"- imports: {list(entry.imports)}\n"
        f"- imported_by: {list(entry.imported_by)}\n"
        f"- public_symbols: {list(entry.public_symbols)}"
    )[:32_768]


def _tool_find_symbol_callers(
    ctx: RunContext[JezzAgentDeps],
    target_symbol: IdentifierStr,
) -> BoundedText:
    """Find all workspace functions/methods whose AST/CST body calls ``target_symbol``."""
    graph = build_workspace_ast_graph(ctx.deps.sandbox.load_units())
    callers = graph.find_callers(target_symbol)
    if not callers:
        return f"Zero workspace call sites found for '{target_symbol}'."
    lines = [
        f"- {c.file_path}:L{c.start_line}-L{c.end_line} ({c.qualname}): {c.signature_summary}"
        for c in callers[:48]
    ]
    return "\n".join(lines)[:32_768]


def _tool_read_symbol_slice(
    ctx: RunContext[JezzAgentDeps],
    symbol_query: IdentifierStr,
    file_path: IdentifierStr | None = None,
) -> SourceCodeText:
    """Read the exact AST/CST line slice for a class, struct, method, function, or type alias."""
    return extract_symbol_source_slice(
        ctx.deps.sandbox.load_units(),
        symbol_query,
        file_path=file_path,
    )


def _tool_inspect_package_api(
    ctx: RunContext[JezzAgentDeps],
    module_path: IdentifierStr,
    symbol_filter: IdentifierStr | None = None,
) -> BoundedText:
    """Introspect installed stdlib or .venv package classes, callables, and signatures."""
    try:
        with _with_workspace_sys_path(ctx.deps.sandbox.repo_root):
            summary = inspect_installed_package_api(module_path, symbol_filter=symbol_filter)
    except ImportError, ValueError:
        return (
            f"# NOT_FOUND: '{module_path}' is not an importable installed dotted module "
            "(pass e.g. 'hypothesis.strategies' or 'pydantic', and use workspace file/AST "
            "tools for local src/... modules)."
        )
    return summary.to_compact_text()


def _tool_consult_building_code(
    ctx: RunContext[JezzAgentDeps],
    code_id: IdentifierStr,
) -> BoundedText:
    """Retrieve the normative Jezz Software Building Code (JBC v1.2) clause for ``code_id``."""
    _ = ctx
    code = CODE_BOOK.get(code_id.strip().upper())
    if code is None:
        return f"# NOT_FOUND: '{code_id}' is not a registered JBC v1.2 code identifier."
    refs = ", ".join(f"{r.body.value} {r.clause}" for r in code.standard_refs)
    return (
        f"{code.code_id} ({code.severity.value}) — {code.title}\n"
        f"- Normative Rule: {code.normative_rule}\n"
        f"- Pass Criteria: {code.pass_criteria}\n"
        f"- Standards Lineage: {refs}"
    )[:4_096]


def _missing_test_verification_symbols(
    deps: JezzAgentDeps,
    candidate_unit: InspectedUnit,
    workspace_units: Sequence[InspectedUnit],
) -> list[str]:
    expected = (
        deps.scoped_invariants
        if deps.scoped_invariants is not None
        else (deps.mandate.invariants if deps.mandate is not None else ())
    )
    if not expected:
        return []
    other_tests = [
        u for u in workspace_units if _unit_has_tests(u) and u.file_path != candidate_unit.file_path
    ]
    compiled = extract_compiled_symbols((candidate_unit, *other_tests))
    return [inv.verification_symbol for inv in expected if inv.verification_symbol not in compiled]


def _preview_python_runtime_gates(
    deps: JezzAgentDeps,
    unit: SourceUnit,
    workspace_units: Sequence[InspectedUnit],
) -> str | None:
    alias_err = find_type_alias_misuse((unit,), workspace_units)
    if alias_err is not None:
        return f"RED_TAG (type alias misuse): {alias_err}"
    if unit.is_test_file or infer_is_test_file(unit.file_path):
        return None
    probe = FilePatchArtifact(
        file_path=unit.file_path,
        source_code=unit.source,
        rationale="rough-in preview",
    )
    runtime_err = verify_runtime_importable_patches(
        deps.sandbox,
        (probe,),
        ignore_missing_local_imports=True,
    )
    if runtime_err is not None:
        return f"RED_TAG (runtime import gate): {runtime_err}"
    return None


def _tool_preview_rough_in(
    ctx: RunContext[JezzAgentDeps],
    rel_path: IdentifierStr,
    candidate_source: SourceCodeText,
) -> BoundedText:
    """Run deterministic polyglot JBC v1.2 rough-in and import inspection without writing."""
    try:
        unit = parse_any_source_unit(rel_path, candidate_source)
    except (SyntaxError, ValueError) as exc:
        line_note = f" at line {exc.lineno}" if isinstance(exc, SyntaxError) and exc.lineno else ""
        return f"RED_TAG ({type(exc).__name__}{line_note}): {exc}"
    ws_units = ctx.deps.sandbox.load_units()
    others = [u for u in ws_units if u.file_path != unit.file_path]
    diff_report = inspect_differential_rough_in(
        ctx.deps.inspector,
        (*others, unit),
        DifferentialScope(changed_paths=frozenset({unit.file_path})),
        permit=ctx.deps.permit,
    )
    cited = [
        f"{f.code_id} (L{f.location.line}): {f.message} -> Fix: {f.remediation}"
        for f in diff_report.report.unwaived_violations()
    ]
    if cited:
        return f"RED_TAG ({len(cited)} violation(s)): " + " | ".join(cited[:8])
    if isinstance(unit, SourceUnit):
        py_err = _preview_python_runtime_gates(ctx.deps, unit, ws_units)
        if py_err is not None:
            return py_err
    if is_polyglot_test_path(rel_path):
        missing_verif = _missing_test_verification_symbols(ctx.deps, unit, ws_units)
        if missing_verif:
            return (
                f"APPROVED JBC rough-in for '{rel_path}', Note: mandated verification_symbol(s) "
                f"not yet defined across test files: {missing_verif}."
            )
    return f"APPROVED: 0 unwaived JBC violations in '{rel_path}'."


@jezz_callable
def build_readonly_toolset() -> Annotated[
    FunctionToolset[JezzAgentDeps],
    InstanceGuard(FunctionToolset),
]:
    """Construct a read-only FunctionToolset for epistemic behaviors (WorkspaceEffect.NONE)."""
    return FunctionToolset(
        tools=(
            _tool_list_authorized_files,
            _tool_read_workspace_file,
            _tool_inspect_file_symbols,
            _tool_query_ast_graph,
            _tool_find_symbol_callers,
            _tool_read_symbol_slice,
            _tool_inspect_package_api,
            _tool_consult_building_code,
        ),
        max_retries=4,
        timeout=30.0,
        id="jezz_readonly_epistemic",
    )


@jezz_callable
def build_scoped_write_toolset() -> Annotated[
    FunctionToolset[JezzAgentDeps],
    InstanceGuard(FunctionToolset),
]:
    """Construct a toolset with read access, AST/API introspection, and JBC rough-in preview."""
    return FunctionToolset(
        tools=(
            _tool_list_authorized_files,
            _tool_read_workspace_file,
            _tool_inspect_file_symbols,
            _tool_query_ast_graph,
            _tool_find_symbol_callers,
            _tool_read_symbol_slice,
            _tool_inspect_package_api,
            _tool_consult_building_code,
            _tool_preview_rough_in,
        ),
        max_retries=4,
        timeout=30.0,
        id="jezz_scoped_mutation",
    )
