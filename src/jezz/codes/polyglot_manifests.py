"""Ecosystem manifest parsers (package.json, Cargo.toml, go.mod) for JBC-VRF-003."""

import re
import tomllib
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Annotated, Final

from pydantic import TypeAdapter, ValidationError

from jezz.codes.polyglot_ir import EcosystemManifest
from jezz.guards import (
    WIRE_FROZENSET_VALIDATOR,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    LoopBudget,
    jezz_callable,
    read_bounded_text,
)

_PACKAGE_JSON_ADAPTER: Final[TypeAdapter[dict[BoundedText, object]]] = TypeAdapter(
    dict[BoundedText, object]
)
_GO_MODULE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*module\s+([^\s/]+(?:/[^\s]+)*)")
_GO_SINGLE_REQ_RE: Final[re.Pattern[str]] = re.compile(r"^\s*require\s+([^\s(]+)\s+v[^\s]+")
_GO_REPLACE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*replace\s+([^\s=(]+)\s*(?:v[^\s]+\s*)?=>")
_GO_BLOCK_ITEM_RE: Final[re.Pattern[str]] = re.compile(r"^\s*([^\s/]+(?:/[^\s]+)+)\s+v[^\s]+")

TS_BUILTIN_MODULES: Final[frozenset[str]] = frozenset(
    {
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "diagnostics_channel",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "stream",
        "string_decoder",
        "sys",
        "test",
        "timers",
        "tls",
        "trace_events",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "wasi",
        "worker_threads",
        "zlib",
    }
)

RUST_BUILTIN_CRATES: Final[frozenset[str]] = frozenset(
    {"std", "core", "alloc", "proc_macro", "test", "crate", "self", "super"}
)

GO_STDLIB_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "C",
        "archive",
        "bufio",
        "builtin",
        "bytes",
        "cmp",
        "compress",
        "container",
        "context",
        "crypto",
        "database",
        "debug",
        "embed",
        "encoding",
        "errors",
        "expvar",
        "flag",
        "fmt",
        "go",
        "hash",
        "html",
        "image",
        "index",
        "io",
        "iter",
        "log",
        "maps",
        "math",
        "mime",
        "net",
        "os",
        "path",
        "plugin",
        "reflect",
        "regexp",
        "runtime",
        "slices",
        "sort",
        "strconv",
        "strings",
        "structs",
        "sync",
        "syscall",
        "testing",
        "text",
        "time",
        "unicode",
        "unique",
        "unsafe",
        "weak",
    }
)


@jezz_callable
def extract_npm_package_root(specifier: BoundedText) -> BoundedText:
    """Extract canonical npm package root (``@scope/pkg`` or ``pkg``) from an import specifier."""
    trimmed = specifier.strip()
    if not trimmed or trimmed.startswith((".", "/")):
        return ""
    if trimmed.startswith(("node:", "bun:")):
        return trimmed
    parts = trimmed.split("/")
    if trimmed.startswith("@") and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"[:256]
    return parts[0][:256]


def _extract_json_keys(section: object) -> set[str]:
    if not isinstance(section, dict):
        return set()
    return {str(key)[:256] for key in section if isinstance(key, str) and key}


def _expand_types_companions(dev_keys: set[str]) -> set[str]:
    expanded = set(dev_keys)
    for key in dev_keys:
        if key.startswith("@types/") and len(key) > 7:
            tail = key[7:]
            if "__" in tail:
                scope, pkg = tail.split("__", maxsplit=1)
                expanded.add(f"@{scope}/{pkg}")
            else:
                expanded.add(tail)
    return expanded


def _parse_single_package_json(pkg_target: ContainedPath) -> tuple[set[str], set[str], set[str]]:
    try:
        raw_text = read_bounded_text(pkg_target, max_bytes=524_288)
        data = _PACKAGE_JSON_ADAPTER.validate_json(raw_text)
    except OSError, ValidationError, ValueError:
        return set(), set(), set()
    run_roots = (
        _extract_json_keys(data.get("dependencies"))
        | _extract_json_keys(data.get("peerDependencies"))
        | _extract_json_keys(data.get("optionalDependencies"))
    )
    dev_roots = _expand_types_companions(_extract_json_keys(data.get("devDependencies")))
    ws_roots: set[str] = set()
    raw_name = data.get("name")
    if isinstance(raw_name, str) and raw_name:
        ws_roots.add(raw_name[:256])
    return run_roots, dev_roots, ws_roots


def _discover_npm_workspace_manifests(
    resolved_root: Path,
) -> tuple[set[str], set[str], set[str]]:
    run_roots: set[str] = set()
    dev_roots: set[str] = set()
    ws_roots: set[str] = set()
    for group_dir in ("packages", "apps"):
        contained_group = ContainedPath.from_parts(resolved_root, group_dir)
        if not contained_group.resolved_path.is_dir():
            continue
        for child in sorted(contained_group.resolved_path.iterdir())[:128]:
            if not child.is_dir():
                continue
            pkg_file = ContainedPath.from_parts(
                resolved_root, f"{group_dir}/{child.name}/package.json"
            )
            if not pkg_file.resolved_path.is_file():
                continue
            m_run, m_dev, m_ws = _parse_single_package_json(pkg_file)
            run_roots |= m_run
            dev_roots |= m_dev
            ws_roots |= m_ws
    return run_roots, dev_roots, ws_roots


@jezz_callable
def load_package_json_manifest(repo_root: Path) -> EcosystemManifest:
    """Parse ``package.json`` (including monorepo ``packages/*`` and ``apps/*`` members)."""
    resolved_root = repo_root.resolve()
    pkg_target = ContainedPath.from_parts(resolved_root, "package.json")
    run_roots, dev_roots, ws_roots = (
        _parse_single_package_json(pkg_target)
        if pkg_target.resolved_path.is_file()
        else (set(), set(), set())
    )
    m_run, m_dev, m_ws = _discover_npm_workspace_manifests(resolved_root)
    merged_run = run_roots | m_run
    merged_dev = (dev_roots | m_dev) - merged_run
    return EcosystemManifest(
        runtime_roots=frozenset(merged_run),
        dev_roots=frozenset(merged_dev),
        workspace_roots=frozenset(ws_roots | m_ws),
    )


def _normalize_crate_ident(raw_name: str) -> str:
    return raw_name.strip().replace("-", "_")[:256]


def _extract_cargo_dep_table(section: object) -> set[str]:
    if not isinstance(section, dict):
        return set()
    crates: set[str] = set()
    for key, spec in section.items():
        if not isinstance(key, str) or not key:
            continue
        crates.add(_normalize_crate_ident(key))
        if isinstance(spec, dict):
            renamed = spec.get("package")
            if isinstance(renamed, str) and renamed:
                crates.add(_normalize_crate_ident(renamed))
    return crates


def _extract_cargo_target_deps(targets_tbl: object) -> tuple[set[str], set[str]]:
    if not isinstance(targets_tbl, dict):
        return set(), set()
    run_roots: set[str] = set()
    dev_roots: set[str] = set()
    for cfg_tbl in targets_tbl.values():
        if isinstance(cfg_tbl, dict):
            run_roots |= _extract_cargo_dep_table(cfg_tbl.get("dependencies"))
            dev_roots |= _extract_cargo_dep_table(cfg_tbl.get("dev-dependencies"))
            dev_roots |= _extract_cargo_dep_table(cfg_tbl.get("build-dependencies"))
    return run_roots, dev_roots


def _extract_cargo_crate_self_roots(data: dict[str, object]) -> set[str]:
    roots: set[str] = set()
    pkg = data.get("package")
    if isinstance(pkg, dict):
        name = pkg.get("name")
        if isinstance(name, str) and name:
            roots.add(_normalize_crate_ident(name))
    lib = data.get("lib")
    if isinstance(lib, dict):
        lib_name = lib.get("name")
        if isinstance(lib_name, str) and lib_name:
            roots.add(_normalize_crate_ident(lib_name))
    return roots


def _parse_single_cargo_toml(cargo_target: ContainedPath) -> tuple[set[str], set[str], set[str]]:
    try:
        data = tomllib.loads(read_bounded_text(cargo_target, max_bytes=524_288))
    except OSError, tomllib.TOMLDecodeError, ValueError:
        return set(), set(), set()
    ws_tbl = data.get("workspace")
    ws_deps = ws_tbl.get("dependencies") if isinstance(ws_tbl, dict) else None
    t_run, t_dev = _extract_cargo_target_deps(data.get("target"))
    run_roots = (
        _extract_cargo_dep_table(data.get("dependencies"))
        | _extract_cargo_dep_table(ws_deps)
        | t_run
    )
    dev_roots = (
        _extract_cargo_dep_table(data.get("dev-dependencies"))
        | _extract_cargo_dep_table(data.get("build-dependencies"))
        | t_dev
    )
    ws_roots = _extract_cargo_crate_self_roots(data)
    return run_roots, dev_roots, ws_roots


def _discover_cargo_member_manifests(
    resolved_root: Path,
    ws_tbl: object,
) -> tuple[set[str], set[str], set[str]]:
    if not isinstance(ws_tbl, dict):
        return set(), set(), set()
    raw_members = ws_tbl.get("members")
    if not isinstance(raw_members, list):
        return set(), set(), set()
    run_roots: set[str] = set()
    dev_roots: set[str] = set()
    ws_roots: set[str] = set()
    for member in raw_members[:128]:
        if not isinstance(member, str) or "*" in member:
            continue
        member_toml = ContainedPath.from_parts(resolved_root, f"{member.strip('/')}/Cargo.toml")
        if not member_toml.resolved_path.is_file():
            continue
        m_run, m_dev, m_ws = _parse_single_cargo_toml(member_toml)
        run_roots |= m_run
        dev_roots |= m_dev
        ws_roots |= m_ws
    return run_roots, dev_roots, ws_roots


@jezz_callable
def load_cargo_toml_manifest(repo_root: Path) -> EcosystemManifest:
    """Parse ``Cargo.toml`` (including Cargo workspace members) for crate dependency roots."""
    resolved_root = repo_root.resolve()
    cargo_target = ContainedPath.from_parts(resolved_root, "Cargo.toml")
    if not cargo_target.resolved_path.is_file():
        return EcosystemManifest()
    try:
        raw_data = tomllib.loads(read_bounded_text(cargo_target, max_bytes=524_288))
    except OSError, tomllib.TOMLDecodeError, ValueError:
        return EcosystemManifest()
    run_roots, dev_roots, ws_roots = _parse_single_cargo_toml(cargo_target)
    m_run, m_dev, m_ws = _discover_cargo_member_manifests(resolved_root, raw_data.get("workspace"))
    merged_run = run_roots | m_run
    merged_dev = (dev_roots | m_dev) - merged_run
    return EcosystemManifest(
        runtime_roots=frozenset(merged_run),
        dev_roots=frozenset(merged_dev),
        workspace_roots=frozenset(ws_roots | m_ws),
    )


def _strip_go_mod_comment(line: str) -> str:
    idx = line.find("//")
    return line[:idx].strip() if idx >= 0 else line.strip()


def _consume_top_go_directive(
    clean: str,
    current_prefix: str,
    requires: set[str],
) -> tuple[bool, str]:
    if clean.startswith("require") and clean.endswith("("):
        return True, current_prefix
    mod_match = _GO_MODULE_RE.match(clean)
    if mod_match is not None:
        return False, mod_match.group(1)[:256]
    req_match = _GO_SINGLE_REQ_RE.match(clean)
    if req_match is not None:
        requires.add(req_match.group(1)[:256])
        return False, current_prefix
    rep_match = _GO_REPLACE_RE.match(clean)
    if rep_match is not None:
        requires.add(rep_match.group(1)[:256])
    return False, current_prefix


def _parse_go_mod_lines(raw_text: str) -> tuple[str, set[str]]:
    module_prefix = ""
    requires: set[str] = set()
    in_require_block = False
    lines = raw_text.splitlines()
    budget = LoopBudget(max_steps=max(16, len(lines) + 4))
    idx = 0
    while idx < len(lines):
        budget.tick()
        clean = _strip_go_mod_comment(lines[idx])
        idx += 1
        if not clean:
            continue
        if in_require_block:
            if clean.startswith(")"):
                in_require_block = False
                continue
            item_match = _GO_BLOCK_ITEM_RE.match(clean)
            if item_match is not None:
                requires.add(item_match.group(1)[:256])
            continue
        in_require_block, module_prefix = _consume_top_go_directive(clean, module_prefix, requires)
    return module_prefix, requires


@jezz_callable
def load_go_mod_manifest(repo_root: Path) -> EcosystemManifest:
    """Parse ``go.mod`` module path and ``require`` / ``replace`` dependency directives."""
    resolved_root = repo_root.resolve()
    gomod_target = ContainedPath.from_parts(resolved_root, "go.mod")
    if not gomod_target.resolved_path.is_file():
        return EcosystemManifest()
    try:
        raw_text = read_bounded_text(gomod_target, max_bytes=524_288)
    except OSError, ValueError:
        return EcosystemManifest()
    module_prefix, requires = _parse_go_mod_lines(raw_text)
    ws_roots = frozenset({module_prefix}) if module_prefix else frozenset()
    return EcosystemManifest(
        runtime_roots=frozenset(requires),
        dev_roots=frozenset(),
        workspace_roots=ws_roots,
        module_prefix=module_prefix,
    )


@jezz_callable
def is_go_import_grounded(
    import_path: BoundedText,
    manifest: EcosystemManifest,
    extra_workspace_roots: Annotated[
        AbstractSet[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
    ] = frozenset(),
) -> bool:
    """Return True iff Go ``import_path`` belongs to stdlib, workspace module, or ``go.mod``."""
    trimmed = import_path.strip()
    if not trimmed:
        return True
    first_seg = trimmed.split("/", maxsplit=1)[0]
    if "." not in first_seg and first_seg in GO_STDLIB_ROOTS:
        return True
    ws_prefixes = set(manifest.workspace_roots) | set(extra_workspace_roots)
    if manifest.module_prefix:
        ws_prefixes.add(manifest.module_prefix)
    for prefix in ws_prefixes:
        if prefix and (trimmed == prefix or trimmed.startswith(f"{prefix}/")):
            return True
    for declared in manifest.runtime_roots | manifest.dev_roots:
        if trimmed == declared or trimmed.startswith(f"{declared}/"):
            return True
    return False
