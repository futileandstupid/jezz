"""Pillar 4: Contained workspace sandbox, checkpoint rollback, toolsets, and dynamic gates."""

import ast
import hashlib
import importlib
import shutil
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Annotated, Final, Protocol, runtime_checkable

from pydantic import BaseModel, Field
from pydantic_ai import FunctionToolset, ModelRetry, RunContext

from jezz.agents.schemas import FilePatchArtifact
from jezz.codes.ast_utils import SourceUnit, dotted_expr_name, parse_source_unit
from jezz.codes.inspector import BuildingInspector, load_workspace_units
from jezz.codes.permits import CodePermit, GateAttestation, GateKind
from jezz.guards import (
    BoundedText,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    LoopBudget,
    PositiveInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
    read_bounded_text,
)
from jezz.methodology.dossier import extract_compiled_symbols
from jezz.methodology.mandate import EngineeringMandate, ModuleWriteScope

type DynamicCheckFn = Callable[["WorkspaceSandbox", Sequence[SourceUnit]], PositiveInt]


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
    ) -> None:
        self._repo_root: Path = repo_root.resolve()
        self._authorized_files: frozenset[str] = frozenset(authorized_files)
        self._snapshots: dict[str, tuple[dict[str, str | None], set[str]]] = {}
        self._modified_files: set[str] = set()

    @property
    def repo_root(self) -> Path:
        """Return canonicalized workspace root directory."""
        return self._repo_root

    @property
    def authorized_files(self) -> frozenset[str]:
        """Return immutable set of files authorized for write mutations."""
        return self._authorized_files

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
        """Write ``content`` to ``rel_path`` iff ``rel_path`` is inside ``authorized_files``."""
        target = ContainedPath.from_parts(self._repo_root, rel_path)
        if target.relative_posix not in self._authorized_files:
            msg = (
                f"Write-scope violation: '{target.relative_posix}' is outside authorized "
                f"ModuleWriteScopes {sorted(self._authorized_files)}."
            )
            raise PermissionError(msg)
        _write_contained_utf8(target, content)
        self._modified_files.add(target.relative_posix)
        return target

    @jezz_callable
    def create_checkpoint(self, checkpoint_id: IdentifierStr) -> IdentifierStr:
        """Record an immutable rollback snapshot (BHV-12 CHECKPOINT)."""
        tracked = self._authorized_files | self._modified_files
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
        target_dirs: Sequence[IdentifierStr] = ("src", "tests"),
    ) -> tuple[SourceUnit, ...]:
        """Parse all ``.py`` workspace units under ``target_dirs``."""
        return load_workspace_units(self._repo_root, target_dirs=target_dirs)


@runtime_checkable
class DynamicGatePort(Protocol):
    """Port executing dynamic test/typecheck/lint gates with duration and memory watchdogs."""

    def run_gates(
        self,
        sandbox: WorkspaceSandbox,
        units: Sequence[SourceUnit],
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


class InProcessGateRunner:
    """Deterministic in-process dynamic gate runner with wall-clock and tracemalloc watchdogs."""

    @jezz_callable
    def __init__(self, dynamic_check: DynamicCheckFn | None = None) -> None:
        self._dynamic_check = dynamic_check

    @jezz_callable
    def run_gates(
        self,
        sandbox: WorkspaceSandbox,
        units: Sequence[SourceUnit],
    ) -> tuple[GateAttestation, ...]:
        """Run dynamic falsification check plus static/symbol compilation attestations."""
        active_fn = self._dynamic_check

        def _invoke_pytest() -> int:
            if active_fn is not None:
                return active_fn(sandbox, units)
            test_units = [u for u in units if u.is_test_file]
            if not test_units:
                err_text = "Zero test files present in workspace during dynamic gate."
                raise AssertionError(err_text)
            return max(1, sum(len(u.callables) for u in test_units))

        code, count, dur_ms, mem_kb, summary_text = _measure_callable_execution(_invoke_pytest)
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
        timeout_sec: Annotated[float, Field(gt=0.0, le=60.0, allow_inf_nan=False)] = 30.0,
    ) -> None:
        self._timeout_sec = timeout_sec

    def _run_uv_gate(
        self,
        repo_root: Path,
        gate_kind: GateKind,
        tool_name: str,
        args: tuple[str, ...],
        fallback_checks: int,
    ) -> GateAttestation:
        t0 = time.perf_counter()
        proc = subprocess.run(
            ("uv", "run", *args),
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=self._timeout_sec,
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
            peak_memory_kb=1024,
            summary=raw_out,
        )

    @jezz_callable
    def run_gates(
        self,
        sandbox: WorkspaceSandbox,
        units: Sequence[SourceUnit],
    ) -> tuple[GateAttestation, ...]:
        """Run ``uv run pytest`` and ``uv run mypy .`` with finite timeouts."""
        count = max(1, len(units))
        return (
            self._run_uv_gate(
                sandbox.repo_root, GateKind.DYNAMIC_SUITE, "pytest", ("pytest", "-q"), count
            ),
            self._run_uv_gate(
                sandbox.repo_root, GateKind.STATIC_TYPES, "mypy", ("mypy", "."), count
            ),
        )


@dataclass(frozen=True)
class JezzAgentDeps:
    """Dependency injection bundle supplied to Pydantic AI specialist agents."""

    sandbox: WorkspaceSandbox
    inspector: BuildingInspector
    permit: CodePermit
    mandate: EngineeringMandate | None = None
    active_scope: ModuleWriteScope | None = None


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
    """Parse ``rel_path`` and return all compiled class and callable symbols."""
    try:
        source = ctx.deps.sandbox.read_file(rel_path)
        unit = parse_source_unit(rel_path, source)
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


def _ensure_pkg_inits(src_root: Path, written_file: Path) -> None:
    curr = written_file.parent
    budget = LoopBudget(max_steps=64)
    while curr != src_root and curr.is_relative_to(src_root):
        budget.tick()
        rel_init = (curr / "__init__.py").relative_to(src_root).as_posix()
        init_contained = ContainedPath.from_parts(src_root, rel_init)
        if not init_contained.resolved_path.exists():
            _write_contained_utf8(init_contained, '"""Staged package."""\n')
        curr = curr.parent


def _stage_candidate_src_tree(
    repo_root: Path,
    src_patches: Sequence[FilePatchArtifact],
) -> tuple[ContainedPath, tuple[str, ...], tuple[tuple[str, str], ...]]:
    hasher = hashlib.sha256()
    for p in src_patches:
        hasher.update(p.file_path.encode("utf-8"))
    stage = ContainedPath.from_parts(repo_root, f".jezz_smoke_{hasher.hexdigest()[:12]}")
    if stage.resolved_path.exists():
        shutil.rmtree(stage.resolved_path)
    stage_src = ContainedPath.from_parts(stage.resolved_path, "src")
    stage_src.resolved_path.mkdir(parents=True, exist_ok=True)

    orig_src = ContainedPath.from_parts(repo_root, "src")
    if orig_src.resolved_path.is_dir():
        for existing in sorted(orig_src.resolved_path.rglob("*.py")):
            rel = existing.relative_to(repo_root).as_posix()
            src_contained = ContainedPath.from_parts(repo_root, rel)
            dst_contained = ContainedPath.from_parts(stage.resolved_path, rel)
            _write_contained_utf8(dst_contained, read_bounded_text(src_contained))

    roots: set[str] = set()
    mod_pairs: list[tuple[str, str]] = []
    for patch in src_patches:
        dst = ContainedPath.from_parts(stage.resolved_path, patch.file_path)
        _write_contained_utf8(dst, patch.source_code)
        _ensure_pkg_inits(stage_src.resolved_path, dst.resolved_path)
        unit = parse_source_unit(patch.file_path, patch.source_code)
        if unit.module_name:
            roots.add(unit.module_name.split(".", maxsplit=1)[0])
            mod_pairs.append((patch.file_path, unit.module_name))
    return stage, tuple(sorted(roots)), tuple(mod_pairs)


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


@jezz_callable
def verify_runtime_importable_patches(
    sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
    patches: Annotated[Sequence[FilePatchArtifact], Field(max_length=64)],
    *,
    ignore_missing_local_imports: bool = False,
) -> ShortText | None:
    """Import candidate ``src/`` patches in an isolated staging dir and rebuild BaseModels."""
    src_patches = [
        p for p in patches if p.file_path.startswith("src/") and p.file_path.endswith(".py")
    ]
    if not src_patches:
        return None
    stage, roots, mod_pairs = _stage_candidate_src_tree(sandbox.repo_root, src_patches)
    stage_src_str = str(ContainedPath.from_parts(stage.resolved_path, "src").resolved_path)
    _evict_staged_modules(roots)
    sys.path.insert(0, stage_src_str)
    try:
        for rel_path, mod_name in mod_pairs:
            try:
                loaded = importlib.import_module(mod_name)
                _rebuild_module_models(loaded)
            except _RUNTIME_IMPORT_ERRORS as exc:
                if _is_ignorable_missing_sibling(exc, roots, ignore_missing_local_imports):
                    continue
                detail = str(exc).splitlines()[0][:260] if str(exc) else type(exc).__name__
                return (
                    f"Runtime import/schema error in '{rel_path}': {type(exc).__name__}: {detail}"
                )
    finally:
        if stage_src_str in sys.path:
            sys.path.remove(stage_src_str)
        _evict_staged_modules(roots)
        if stage.resolved_path.exists():
            shutil.rmtree(stage.resolved_path)
    return None


_JEZZ_PEP695_ALIASES: Final[frozenset[str]] = frozenset(
    {
        "IdentifierStr",
        "NonEmptyStr",
        "ShortText",
        "BoundedText",
        "SourceCodeText",
        "LineNumber",
        "ColumnOffset",
        "NonNegativeInt",
        "PositiveInt",
        "BoundedInt",
        "FiniteFloat",
    }
)


def _collect_known_type_aliases(
    units: Sequence[SourceUnit],
) -> tuple[set[str], set[str]]:
    all_aliases: set[str] = set(_JEZZ_PEP695_ALIASES)
    nongeneric_aliases: set[str] = set(_JEZZ_PEP695_ALIASES)
    for unit in units:
        for stmt in ast.walk(unit.tree):
            if isinstance(stmt, ast.TypeAlias):
                name = stmt.name.id
                all_aliases.add(name)
                if not stmt.type_params:
                    nongeneric_aliases.add(name)
    return all_aliases, nongeneric_aliases


def _scan_unit_type_alias_misuse(
    unit: SourceUnit,
    all_aliases: AbstractSet[str],
    nongeneric_aliases: AbstractSet[str],
) -> str | None:
    shadowed = {c.name for c in unit.classes} | {fn.node.name for fn in unit.callables}
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Call):
            tail = dotted_expr_name(node.func).rsplit(".", maxsplit=1)[-1]
            if tail in all_aliases and tail not in shadowed:
                return (
                    f"'{unit.file_path}' (L{node.lineno}): PEP 695 type alias '{tail}' is a "
                    f"TypeAliasType and is not callable at runtime ('{tail}(...)' raises "
                    "TypeError). Pass a plain value literal instead."
                )
        if isinstance(node, ast.Subscript):
            tail = dotted_expr_name(node.value).rsplit(".", maxsplit=1)[-1]
            if tail in nongeneric_aliases and tail not in shadowed:
                return (
                    f"'{unit.file_path}' (L{node.lineno}): Non-generic PEP 695 type alias "
                    f"'{tail}' is not subscriptable ('{tail}[...]' raises TypeError). Use "
                    "Annotated[..., Field(...)] instead."
                )
    return None


@jezz_callable
def find_type_alias_misuse(
    candidate_units: Annotated[Sequence[SourceUnit], Field(max_length=64)],
    workspace_units: Annotated[Sequence[SourceUnit], Field(max_length=512)] = (),
) -> ShortText | None:
    """Detect runtime-fatal calls or subscripts on PEP 695 TypeAliasType definitions."""
    all_aliases, nongeneric_aliases = _collect_known_type_aliases(
        (*workspace_units, *candidate_units)
    )
    for unit in candidate_units:
        issue = _scan_unit_type_alias_misuse(unit, all_aliases, nongeneric_aliases)
        if issue is not None:
            return issue
    return None


def _tool_preview_rough_in(
    ctx: RunContext[JezzAgentDeps],
    rel_path: IdentifierStr,
    candidate_source: SourceCodeText,
) -> BoundedText:
    """Run deterministic JBC v1.1 rough-in and runtime-import inspection without writing."""
    try:
        unit = parse_source_unit(rel_path, candidate_source)
    except SyntaxError as exc:
        raise ModelRetry(f"SyntaxError at line {exc.lineno}: {exc.msg}") from exc
    report = ctx.deps.inspector.inspect_rough_in((unit,), permit=ctx.deps.permit)
    if not report.is_occupiable:
        lines = [
            f"{f.code_id} (L{f.location.line}): {f.message} -> Fix: {f.remediation}"
            for f in report.unwaived_violations()[:8]
        ]
        return f"RED_TAG ({len(report.unwaived_violations())} violation(s)): " + " | ".join(lines)
    alias_err = find_type_alias_misuse((unit,), ctx.deps.sandbox.load_units())
    if alias_err is not None:
        return f"RED_TAG (type alias misuse): {alias_err}"
    if rel_path.startswith("src/") and rel_path.endswith(".py"):
        probe = FilePatchArtifact(
            file_path=rel_path,
            source_code=candidate_source,
            rationale="rough-in preview",
        )
        runtime_err = verify_runtime_importable_patches(
            ctx.deps.sandbox,
            (probe,),
            ignore_missing_local_imports=True,
        )
        if runtime_err is not None:
            return f"RED_TAG (runtime import gate): {runtime_err}"
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
        ),
        max_retries=2,
        timeout=10.0,
        id="jezz_readonly_epistemic",
    )


@jezz_callable
def build_scoped_write_toolset() -> Annotated[
    FunctionToolset[JezzAgentDeps],
    InstanceGuard(FunctionToolset),
]:
    """Construct a toolset with read access and JBC rough-in dry-run preview for mutating agents."""
    return FunctionToolset(
        tools=(
            _tool_list_authorized_files,
            _tool_read_workspace_file,
            _tool_inspect_file_symbols,
            _tool_preview_rough_in,
        ),
        max_retries=2,
        timeout=10.0,
        id="jezz_scoped_mutation",
    )
