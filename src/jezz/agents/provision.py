"""Permit-gated dependency provisioning (``BHV-13 PROVISION``) and manifest reload."""

import importlib
import re
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final, Protocol, runtime_checkable

import icontract
from pydantic import Field

from jezz.agents.schemas import ProvisionReceipt
from jezz.agents.workspace import WorkspaceSandbox
from jezz.codes.inspector import (
    BuildingInspector,
    ManifestDependencies,
    normalize_pep508_to_import_root,
)
from jezz.guards import (
    ContainedPath,
    InstanceGuard,
    jezz_callable,
    read_bounded_text,
)
from jezz.methodology.mandate import DependencyRequirement, DependencyScope

_DEFAULT_PYPROJECT_HEADER: Final[str] = (
    '[project]\nname = "jezz-workspace"\nversion = "0.1.0"\ndependencies = []\n'
)
_PKG_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _spec_pkg_key(spec: str) -> str:
    match = _PKG_PREFIX_RE.match(spec)
    if match is None:
        return spec.lower()
    return re.sub(r"[-_.]+", "_", match.group(1)).lower()


@jezz_callable
def compute_missing_dependencies(
    manifest: ManifestDependencies,
    requirements: Annotated[Sequence[DependencyRequirement], Field(max_length=32)],
) -> tuple[DependencyRequirement, ...]:
    """Return declared ``requirements`` whose import roots are absent from ``manifest``."""
    missing: list[DependencyRequirement] = []
    for dep in requirements:
        root = dep.effective_import_root()
        alias_root = normalize_pep508_to_import_root(dep.pep508_spec())
        candidates = {r for r in (root, alias_root) if r}
        pool = (
            manifest.runtime_roots
            if dep.scope == DependencyScope.RUNTIME
            else (manifest.dev_roots | manifest.runtime_roots)
        )
        if not (candidates & pool):
            missing.append(dep)
    return tuple(missing)


@icontract.ensure(lambda requirements, result: isinstance(result, BuildingInspector))
@jezz_callable
def refresh_inspector_after_provision(
    repo_root: Path,
    prior_inspector: Annotated[BuildingInspector, InstanceGuard(BuildingInspector)],
    requirements: Annotated[Sequence[DependencyRequirement], Field(max_length=32)],
) -> Annotated[BuildingInspector, InstanceGuard(BuildingInspector)]:
    """Reload ``BuildingInspector`` from ``pyproject.toml`` after ``BHV-13 PROVISION``."""
    importlib.invalidate_caches()
    fresh = BuildingInspector.from_workspace(repo_root.resolve())
    extra_run: set[str] = set()
    extra_dev: set[str] = set()
    for dep in requirements:
        roots = {
            r
            for r in (
                dep.effective_import_root(),
                normalize_pep508_to_import_root(dep.pep508_spec()),
            )
            if r
        }
        if dep.scope == DependencyScope.RUNTIME:
            extra_run |= roots
        else:
            extra_dev |= roots
    prior = prior_inspector.manifest
    merged = ManifestDependencies(
        runtime_roots=frozenset(prior.runtime_roots | fresh.manifest.runtime_roots | extra_run),
        dev_roots=frozenset(prior.dev_roots | fresh.manifest.dev_roots | extra_dev),
        workspace_roots=frozenset(prior.workspace_roots | fresh.manifest.workspace_roots),
    )
    return BuildingInspector(manifest=merged)


@runtime_checkable
class ProvisionPort(Protocol):
    """Port enacting authorized ``BHV-13 PROVISION`` manifest/substrate updates."""

    def provision_dependencies(
        self,
        sandbox: WorkspaceSandbox,
        requirements: Sequence[DependencyRequirement],
    ) -> ProvisionReceipt:
        """Apply ``requirements`` inside ``sandbox`` and return a ``ProvisionReceipt``."""
        ...


def _format_toml_str_array(items: Sequence[str]) -> str:
    quoted = ", ".join(f'"{item}"' for item in items)
    return f"[{quoted}]"


def _merge_unique_specs(existing: Sequence[str], additions: Sequence[str]) -> list[str]:
    seen = {_spec_pkg_key(item) for item in existing}
    merged = list(existing)
    for spec in additions:
        key = _spec_pkg_key(spec)
        if key not in seen:
            merged.append(spec)
            seen.add(key)
    return merged


def _upsert_runtime_deps_in_toml(raw_toml: str, specs: Sequence[str]) -> str:
    if not specs:
        return raw_toml
    parsed = tomllib.loads(raw_toml)
    proj = parsed.get("project")
    existing = (
        [s for s in proj.get("dependencies", []) if isinstance(s, str)]
        if isinstance(proj, dict) and isinstance(proj.get("dependencies"), list)
        else []
    )
    arr_str = _format_toml_str_array(_merge_unique_specs(existing, specs))
    dep_re = re.compile(r"(?ms)^(\s*dependencies\s*=\s*)\[[^]]*]")
    if dep_re.search(raw_toml) is not None:
        return dep_re.sub(rf"\g<1>{arr_str}", raw_toml, count=1)
    if "[project]" in raw_toml:
        return raw_toml.replace("[project]\n", f"[project]\ndependencies = {arr_str}\n", 1)
    return f"{raw_toml.rstrip()}\n\n[project]\ndependencies = {arr_str}\n"


def _upsert_dev_deps_in_toml(raw_toml: str, specs: Sequence[str]) -> str:
    if not specs:
        return raw_toml
    parsed = tomllib.loads(raw_toml)
    groups = parsed.get("dependency-groups")
    existing = (
        [s for s in groups.get("dev", []) if isinstance(s, str)]
        if isinstance(groups, dict) and isinstance(groups.get("dev"), list)
        else []
    )
    arr_str = _format_toml_str_array(_merge_unique_specs(existing, specs))
    dev_re = re.compile(r"(?ms)(\[dependency-groups][^[]*?^\s*dev\s*=\s*)\[[^]]*]")
    if dev_re.search(raw_toml) is not None:
        return dev_re.sub(rf"\g<1>{arr_str}", raw_toml, count=1)
    if "[dependency-groups]" in raw_toml:
        return raw_toml.replace(
            "[dependency-groups]\n",
            f"[dependency-groups]\ndev = {arr_str}\n",
            1,
        )
    return f"{raw_toml.rstrip()}\n\n[dependency-groups]\ndev = {arr_str}\n"


def _read_existing_manifest_or_default(sandbox: WorkspaceSandbox) -> str:
    try:
        return sandbox.read_file("pyproject.toml")
    except FileNotFoundError:
        return _DEFAULT_PYPROJECT_HEADER


class InProcessProvisionRunner:
    """Deterministic offline provisioner updating ``pyproject.toml`` inside ``sandbox``."""

    @jezz_callable
    def __init__(self, *, touch_lockfile: bool = False) -> None:
        self._touch_lockfile = touch_lockfile

    @jezz_callable
    def provision_dependencies(
        self,
        sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
        requirements: Annotated[
            Sequence[DependencyRequirement],
            Field(min_length=1, max_length=32),
        ],
    ) -> ProvisionReceipt:
        """Update ``pyproject.toml`` deterministically via authorized sandbox writes."""
        run_specs = [r.pep508_spec() for r in requirements if r.scope == DependencyScope.RUNTIME]
        dev_specs = [r.pep508_spec() for r in requirements if r.scope == DependencyScope.DEV]
        current = _read_existing_manifest_or_default(sandbox)
        updated = _upsert_runtime_deps_in_toml(current, run_specs)
        updated = _upsert_dev_deps_in_toml(updated, dev_specs)
        tomllib.loads(updated)
        sandbox.write_authorized_file("pyproject.toml", updated)
        touched = ["pyproject.toml"]
        if self._touch_lockfile:
            lock_lines = "\n".join(f"# provisioned {r.pep508_spec()}" for r in requirements) + "\n"
            sandbox.write_authorized_file("uv.lock", lock_lines)
            touched.append("uv.lock")
        all_specs = tuple([*run_specs, *dev_specs])
        return ProvisionReceipt(
            provisioned_specs=all_specs,
            updated_files=tuple(touched),
            summary=f"Provisioned {len(all_specs)} dependency spec(s) in {touched}.",
        )


def _run_uv_add_cmd(repo_root: Path, args: tuple[str, ...], timeout_sec: float) -> None:
    proc = subprocess.run(
        ("uv", "add", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip() or "uv add failed")[:300]
        msg = f"uv add failed (exit {proc.returncode}): {detail}"
        raise RuntimeError(msg)


def _sync_manifest_mutations_to_sandbox(
    sandbox: WorkspaceSandbox,
    before: dict[str, str | None],
) -> tuple[str, ...]:
    changed: list[str] = []
    for rel_path in ("pyproject.toml", "uv.lock"):
        contained = ContainedPath.from_parts(sandbox.repo_root, rel_path)
        if not contained.resolved_path.is_file():
            continue
        after_text = read_bounded_text(contained, max_bytes=2_097_152)
        if before.get(rel_path) != after_text:
            sandbox.write_authorized_file(rel_path, after_text)
            changed.append(rel_path)
    return tuple(changed)


class UvSubprocessProvisionRunner:
    """Production provisioner invoking ``uv add`` arg-list subprocesses with timeouts."""

    @jezz_callable
    def __init__(
        self,
        timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 60.0,
    ) -> None:
        self._timeout_sec = timeout_sec

    @jezz_callable
    def provision_dependencies(
        self,
        sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
        requirements: Annotated[
            Sequence[DependencyRequirement],
            Field(min_length=1, max_length=32),
        ],
    ) -> ProvisionReceipt:
        """Execute ``uv add`` inside ``sandbox.repo_root`` when manifest mutation is authorized."""
        if "pyproject.toml" not in sandbox.authorized_manifests:
            msg = "Provisioning denied: 'pyproject.toml' is not authorized by active mandate."
            raise PermissionError(msg)
        before: dict[str, str | None] = {}
        for rel in ("pyproject.toml", "uv.lock"):
            c_path = ContainedPath.from_parts(sandbox.repo_root, rel)
            before[rel] = (
                read_bounded_text(c_path, max_bytes=2_097_152)
                if c_path.resolved_path.is_file()
                else None
            )
        run_specs = tuple(
            r.pep508_spec() for r in requirements if r.scope == DependencyScope.RUNTIME
        )
        dev_specs = tuple(r.pep508_spec() for r in requirements if r.scope == DependencyScope.DEV)
        if run_specs:
            _run_uv_add_cmd(sandbox.repo_root, run_specs, self._timeout_sec)
        if dev_specs:
            _run_uv_add_cmd(sandbox.repo_root, ("--dev", *dev_specs), self._timeout_sec)
        updated_files = _sync_manifest_mutations_to_sandbox(sandbox, before)
        all_specs = (*run_specs, *dev_specs)
        return ProvisionReceipt(
            provisioned_specs=all_specs,
            updated_files=updated_files,
            summary=f"Executed uv add for {len(all_specs)} dependency spec(s).",
        )
