"""Permit-gated polyglot dependency provisioning (``BHV-13 PROVISION``) and manifest reload."""

import importlib
import json
import re
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final, Protocol, runtime_checkable

import icontract
from pydantic import Field, TypeAdapter

from jezz.agents.polyglot_prompts import detect_workspace_languages
from jezz.agents.schemas import ProvisionReceipt
from jezz.agents.workspace import WorkspaceSandbox
from jezz.codes.inspector import (
    BuildingInspector,
    ManifestDependencies,
    normalize_pep508_to_import_root,
)
from jezz.codes.polyglot_ir import EcosystemManifest, PolyglotManifest, SupportedLanguage
from jezz.codes.polyglot_manifests import is_go_import_grounded
from jezz.guards import (
    BoundedText,
    ContainedPath,
    InstanceGuard,
    jezz_callable,
    read_bounded_text,
)
from jezz.methodology.mandate import DependencyRequirement, DependencyScope

_DEFAULT_PYPROJECT_HEADER: Final[str] = (
    '[project]\nname = "jezz-workspace"\nversion = "0.1.0"\ndependencies = []\n'
)
_DEFAULT_CARGO_TOML: Final[str] = (
    '[package]\nname = "jezz_workspace"\nversion = "0.1.0"\nedition = "2021"\n'
)
_DEFAULT_GO_MOD: Final[str] = "module example.com/jezzworkspace\n\ngo 1.23\n"
_PKG_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_JSON_DICT_ADAPTER: Final[TypeAdapter[dict[BoundedText, object]]] = TypeAdapter(
    dict[BoundedText, object]
)
_MANIFEST_FILES_BY_LANG: Final[Mapping[SupportedLanguage, tuple[str, ...]]] = MappingProxyType(
    {
        SupportedLanguage.PYTHON: ("pyproject.toml", "uv.lock"),
        SupportedLanguage.TYPESCRIPT: ("package.json", "package-lock.json", "pnpm-lock.yaml"),
        SupportedLanguage.RUST: ("Cargo.toml", "Cargo.lock"),
        SupportedLanguage.GO: ("go.mod", "go.sum"),
    }
)


def _spec_pkg_key(spec: str) -> str:
    match = _PKG_PREFIX_RE.match(spec)
    if match is None:
        return spec.lower()
    return re.sub(r"[-_.]+", "_", match.group(1)).lower()


def _is_dep_present_in_ecosystem(
    eco: EcosystemManifest,
    dep: DependencyRequirement,
    language: SupportedLanguage,
) -> bool:
    if language == SupportedLanguage.GO:
        return is_go_import_grounded(dep.effective_import_root(), eco)
    pool = (
        eco.runtime_roots
        if dep.scope == DependencyScope.RUNTIME
        else (eco.dev_roots | eco.runtime_roots)
    )
    candidates = {dep.effective_import_root(), dep.package}
    if language == SupportedLanguage.PYTHON:
        candidates.add(normalize_pep508_to_import_root(dep.pep508_spec()))
    return bool({c for c in candidates if c} & pool)


@jezz_callable
def compute_missing_dependencies(
    manifest: ManifestDependencies,
    requirements: Annotated[Sequence[DependencyRequirement], Field(max_length=32)],
    *,
    polyglot_manifest: PolyglotManifest | None = None,
    language: SupportedLanguage = SupportedLanguage.PYTHON,
) -> tuple[DependencyRequirement, ...]:
    """Return declared ``requirements`` whose import roots are absent from workspace manifests."""
    if language != SupportedLanguage.PYTHON and polyglot_manifest is not None:
        eco = polyglot_manifest.for_language(language)
        return tuple(
            dep for dep in requirements if not _is_dep_present_in_ecosystem(eco, dep, language)
        )
    py_eco = EcosystemManifest(
        runtime_roots=manifest.runtime_roots,
        dev_roots=manifest.dev_roots,
        workspace_roots=manifest.workspace_roots,
    )
    return tuple(
        dep
        for dep in requirements
        if not _is_dep_present_in_ecosystem(py_eco, dep, SupportedLanguage.PYTHON)
    )


def _merge_ecosystem_manifest(
    prior: EcosystemManifest,
    fresh: EcosystemManifest,
    extra_run: set[str],
    extra_dev: set[str],
) -> EcosystemManifest:
    return EcosystemManifest(
        runtime_roots=frozenset(prior.runtime_roots | fresh.runtime_roots | extra_run),
        dev_roots=frozenset(prior.dev_roots | fresh.dev_roots | extra_dev),
        workspace_roots=frozenset(prior.workspace_roots | fresh.workspace_roots),
        module_prefix=fresh.module_prefix or prior.module_prefix,
    )


@icontract.ensure(lambda requirements, result: isinstance(result, BuildingInspector))
@jezz_callable
def refresh_inspector_after_provision(
    repo_root: Path,
    prior_inspector: Annotated[BuildingInspector, InstanceGuard(BuildingInspector)],
    requirements: Annotated[Sequence[DependencyRequirement], Field(max_length=32)],
    *,
    language: SupportedLanguage = SupportedLanguage.PYTHON,
) -> Annotated[BuildingInspector, InstanceGuard(BuildingInspector)]:
    """Reload ``BuildingInspector`` across Python, npm, Cargo, and Go manifests after PROVISION."""
    importlib.invalidate_caches()
    fresh = BuildingInspector.from_workspace(repo_root.resolve())
    extra_run: set[str] = set()
    extra_dev: set[str] = set()
    for dep in requirements:
        roots = {dep.effective_import_root(), dep.package}
        if language == SupportedLanguage.PYTHON:
            roots.add(normalize_pep508_to_import_root(dep.pep508_spec()))
        clean_roots = {r for r in roots if r}
        if dep.scope == DependencyScope.RUNTIME:
            extra_run |= clean_roots
        else:
            extra_dev |= clean_roots

    prior_py = prior_inspector.manifest
    py_run = extra_run if language == SupportedLanguage.PYTHON else set()
    py_dev = extra_dev if language == SupportedLanguage.PYTHON else set()
    merged_py = ManifestDependencies(
        runtime_roots=frozenset(prior_py.runtime_roots | fresh.manifest.runtime_roots | py_run),
        dev_roots=frozenset(prior_py.dev_roots | fresh.manifest.dev_roots | py_dev),
        workspace_roots=frozenset(prior_py.workspace_roots | fresh.manifest.workspace_roots),
    )
    p_prior = prior_inspector.polyglot_manifest
    p_fresh = fresh.polyglot_manifest
    merged_poly = PolyglotManifest(
        python=_merge_ecosystem_manifest(p_prior.python, p_fresh.python, py_run, py_dev),
        typescript=_merge_ecosystem_manifest(
            p_prior.typescript,
            p_fresh.typescript,
            extra_run if language == SupportedLanguage.TYPESCRIPT else set(),
            extra_dev if language == SupportedLanguage.TYPESCRIPT else set(),
        ),
        rust=_merge_ecosystem_manifest(
            p_prior.rust,
            p_fresh.rust,
            extra_run if language == SupportedLanguage.RUST else set(),
            extra_dev if language == SupportedLanguage.RUST else set(),
        ),
        go=_merge_ecosystem_manifest(
            p_prior.go,
            p_fresh.go,
            extra_run if language == SupportedLanguage.GO else set(),
            extra_dev if language == SupportedLanguage.GO else set(),
        ),
    )
    return BuildingInspector(manifest=merged_py, polyglot_manifest=merged_poly)


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


def _read_or_default(sandbox: WorkspaceSandbox, rel_path: str, default_text: str) -> str:
    try:
        return sandbox.read_file(rel_path)
    except FileNotFoundError:
        return default_text


def _provision_python_in_process(
    sandbox: WorkspaceSandbox,
    requirements: Sequence[DependencyRequirement],
    *,
    touch_lockfile: bool,
) -> ProvisionReceipt:
    run_specs = [r.pep508_spec() for r in requirements if r.scope == DependencyScope.RUNTIME]
    dev_specs = [r.pep508_spec() for r in requirements if r.scope == DependencyScope.DEV]
    current = _read_or_default(sandbox, "pyproject.toml", _DEFAULT_PYPROJECT_HEADER)
    updated = _upsert_dev_deps_in_toml(_upsert_runtime_deps_in_toml(current, run_specs), dev_specs)
    tomllib.loads(updated)
    sandbox.write_authorized_file("pyproject.toml", updated)
    touched = ["pyproject.toml"]
    if touch_lockfile:
        lock_lines = "\n".join(f"# provisioned {r.pep508_spec()}" for r in requirements) + "\n"
        sandbox.write_authorized_file("uv.lock", lock_lines)
        touched.append("uv.lock")
    all_specs = tuple([*run_specs, *dev_specs])
    return ProvisionReceipt(
        provisioned_specs=all_specs,
        updated_files=tuple(touched),
        summary=f"Provisioned {len(all_specs)} Python dependency spec(s) in {touched}.",
    )


def _provision_npm_in_process(
    sandbox: WorkspaceSandbox,
    requirements: Sequence[DependencyRequirement],
) -> ProvisionReceipt:
    raw = _read_or_default(
        sandbox,
        "package.json",
        '{"name": "jezz-workspace", "version": "0.1.0"}\n',
    )
    data = dict(_JSON_DICT_ADAPTER.validate_json(raw))
    deps_raw = data.get("dependencies")
    dev_raw = data.get("devDependencies")
    deps_map = dict(deps_raw) if isinstance(deps_raw, dict) else {}
    dev_map = dict(dev_raw) if isinstance(dev_raw, dict) else {}
    specs: list[str] = []
    for req in requirements:
        ver = "".join(req.version_constraint.split()) or "*"
        target_map = deps_map if req.scope == DependencyScope.RUNTIME else dev_map
        target_map[req.package] = ver
        specs.append(req.ecosystem_spec("typescript"))
    if deps_map:
        data["dependencies"] = deps_map
    if dev_map:
        data["devDependencies"] = dev_map
    sandbox.write_authorized_file("package.json", json.dumps(data, indent=2) + "\n")
    return ProvisionReceipt(
        provisioned_specs=tuple(specs),
        updated_files=("package.json",),
        summary=f"Provisioned {len(specs)} npm dependency spec(s) in package.json.",
    )


def _append_cargo_section_entries(raw_toml: str, section: str, entries: Sequence[str]) -> str:
    if not entries:
        return raw_toml
    block = "\n".join(entries)
    header = f"[{section}]"
    if header in raw_toml:
        return raw_toml.replace(header, f"{header}\n{block}", 1)
    return f"{raw_toml.rstrip()}\n\n{header}\n{block}\n"


def _provision_cargo_in_process(
    sandbox: WorkspaceSandbox,
    requirements: Sequence[DependencyRequirement],
) -> ProvisionReceipt:
    current = _read_or_default(sandbox, "Cargo.toml", _DEFAULT_CARGO_TOML)
    parsed = tomllib.loads(current)
    raw_run = parsed.get("dependencies")
    raw_dev = parsed.get("dev-dependencies")
    run_existing = set(raw_run) if isinstance(raw_run, dict) else set()
    dev_existing = set(raw_dev) if isinstance(raw_dev, dict) else set()
    run_lines: list[str] = []
    dev_lines: list[str] = []
    specs: list[str] = []
    for req in requirements:
        ver = "".join(req.version_constraint.split()).lstrip("=") or "*"
        specs.append(req.ecosystem_spec("rust"))
        if req.scope == DependencyScope.RUNTIME and req.package not in run_existing:
            run_lines.append(f'{req.package} = "{ver}"')
        elif req.scope == DependencyScope.DEV and req.package not in dev_existing:
            dev_lines.append(f'{req.package} = "{ver}"')
    updated = _append_cargo_section_entries(current, "dependencies", run_lines)
    updated = _append_cargo_section_entries(updated, "dev-dependencies", dev_lines)
    tomllib.loads(updated)
    sandbox.write_authorized_file("Cargo.toml", updated)
    return ProvisionReceipt(
        provisioned_specs=tuple(specs),
        updated_files=("Cargo.toml",),
        summary=f"Provisioned {len(specs)} Cargo dependency spec(s) in Cargo.toml.",
    )


def _provision_go_in_process(
    sandbox: WorkspaceSandbox,
    requirements: Sequence[DependencyRequirement],
) -> ProvisionReceipt:
    current = _read_or_default(sandbox, "go.mod", _DEFAULT_GO_MOD)
    lines = [current.rstrip()]
    specs: list[str] = []
    for req in requirements:
        ver = "".join(req.version_constraint.split()).lstrip("=^~") or "v0.0.0"
        if not ver.startswith("v"):
            ver = f"v{ver}"
        specs.append(f"{req.package}@{ver}")
        if req.package not in current:
            lines.append(f"require {req.package} {ver}")
    sandbox.write_authorized_file("go.mod", "\n".join(lines) + "\n")
    return ProvisionReceipt(
        provisioned_specs=tuple(specs),
        updated_files=("go.mod",),
        summary=f"Provisioned {len(specs)} Go module requirement(s) in go.mod.",
    )


class InProcessProvisionRunner:
    """Deterministic offline provisioner updating Python, npm, Cargo, or Go manifests."""

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
        """Update the active ecosystem manifest deterministically via authorized sandbox writes."""
        langs = detect_workspace_languages(
            sandbox.repo_root,
            authorized_files=sandbox.authorized_files,
        )
        primary = langs[0]
        match primary:
            case SupportedLanguage.TYPESCRIPT:
                return _provision_npm_in_process(sandbox, requirements)
            case SupportedLanguage.RUST:
                return _provision_cargo_in_process(sandbox, requirements)
            case SupportedLanguage.GO:
                return _provision_go_in_process(sandbox, requirements)
            case _:
                return _provision_python_in_process(
                    sandbox,
                    requirements,
                    touch_lockfile=self._touch_lockfile,
                )


def _run_checked_cmd(repo_root: Path, argv: tuple[str, ...], timeout_sec: float) -> None:
    proc = subprocess.run(
        argv,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        label = " ".join(argv[:2])
        detail = (proc.stderr.strip() or proc.stdout.strip() or f"{label} failed")[:300]
        msg = f"{label} failed (exit {proc.returncode}): {detail}"
        raise RuntimeError(msg)


def _snapshot_manifest_files(repo_root: Path, rel_paths: Sequence[str]) -> dict[str, str | None]:
    before: dict[str, str | None] = {}
    for rel in rel_paths:
        c_path = ContainedPath.from_parts(repo_root, rel)
        before[rel] = (
            read_bounded_text(c_path, max_bytes=2_097_152)
            if c_path.resolved_path.is_file()
            else None
        )
    return before


def _sync_manifest_mutations_to_sandbox(
    sandbox: WorkspaceSandbox,
    before: Mapping[str, str | None],
    rel_paths: Sequence[str],
) -> tuple[str, ...]:
    changed: list[str] = []
    for rel_path in rel_paths:
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
        tracked = _MANIFEST_FILES_BY_LANG[SupportedLanguage.PYTHON]
        if "pyproject.toml" not in sandbox.authorized_manifests:
            msg = "Provisioning denied: 'pyproject.toml' is not authorized by active mandate."
            raise PermissionError(msg)
        before = _snapshot_manifest_files(sandbox.repo_root, tracked)
        run_specs = tuple(
            r.pep508_spec() for r in requirements if r.scope == DependencyScope.RUNTIME
        )
        dev_specs = tuple(r.pep508_spec() for r in requirements if r.scope == DependencyScope.DEV)
        if run_specs:
            _run_checked_cmd(sandbox.repo_root, ("uv", "add", *run_specs), self._timeout_sec)
        if dev_specs:
            _run_checked_cmd(
                sandbox.repo_root, ("uv", "add", "--dev", *dev_specs), self._timeout_sec
            )
        updated_files = _sync_manifest_mutations_to_sandbox(sandbox, before, tracked)
        all_specs = (*run_specs, *dev_specs)
        return ProvisionReceipt(
            provisioned_specs=all_specs,
            updated_files=updated_files,
            summary=f"Executed uv add for {len(all_specs)} dependency spec(s).",
        )


def _execute_ecosystem_provision_cmds(
    repo_root: Path,
    language: SupportedLanguage,
    requirements: Sequence[DependencyRequirement],
    timeout_sec: float,
) -> tuple[str, ...]:
    eco = str(language)
    run_specs = tuple(
        r.ecosystem_spec(eco) for r in requirements if r.scope == DependencyScope.RUNTIME
    )
    dev_specs = tuple(r.ecosystem_spec(eco) for r in requirements if r.scope == DependencyScope.DEV)
    match language:
        case SupportedLanguage.TYPESCRIPT:
            if run_specs:
                _run_checked_cmd(repo_root, ("npm", "install", *run_specs), timeout_sec)
            if dev_specs:
                _run_checked_cmd(
                    repo_root, ("npm", "install", "--save-dev", *dev_specs), timeout_sec
                )
        case SupportedLanguage.RUST:
            if run_specs:
                _run_checked_cmd(repo_root, ("cargo", "add", *run_specs), timeout_sec)
            if dev_specs:
                _run_checked_cmd(repo_root, ("cargo", "add", "--dev", *dev_specs), timeout_sec)
        case SupportedLanguage.GO:
            for spec in (*run_specs, *dev_specs):
                _run_checked_cmd(repo_root, ("go", "get", spec), timeout_sec)
        case _:
            pass
    return *run_specs, *dev_specs


class PolyglotSubprocessProvisionRunner:
    """Composite subprocess ``ProvisionPort`` across Python, npm, Cargo, and Go."""

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
        """Provision ``requirements`` using the active workspace ecosystem's package manager."""
        langs = detect_workspace_languages(
            sandbox.repo_root,
            authorized_files=sandbox.authorized_files,
        )
        primary = langs[0]
        if primary == SupportedLanguage.PYTHON:
            return UvSubprocessProvisionRunner(
                timeout_sec=self._timeout_sec
            ).provision_dependencies(sandbox, requirements)
        tracked = _MANIFEST_FILES_BY_LANG[primary]
        primary_manifest = tracked[0]
        if primary_manifest not in sandbox.authorized_manifests:
            msg = f"Provisioning denied: '{primary_manifest}' is not authorized by active mandate."
            raise PermissionError(msg)
        before = _snapshot_manifest_files(sandbox.repo_root, tracked)
        all_specs = _execute_ecosystem_provision_cmds(
            sandbox.repo_root,
            primary,
            requirements,
            self._timeout_sec,
        )
        updated_files = _sync_manifest_mutations_to_sandbox(sandbox, before, tracked)
        return ProvisionReceipt(
            provisioned_specs=all_specs,
            updated_files=updated_files,
            summary=f"Provisioned {len(all_specs)} {primary} dependency spec(s).",
        )
