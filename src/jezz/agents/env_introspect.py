"""Safe runtime environment and local polyglot package API signature introspection (BHV-05/06)."""

import importlib
import importlib.metadata
import inspect
import json
import re
import sys
import tomllib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Annotated, Final, TypeAliasType

from pydantic import BaseModel, Field, TypeAdapter

from jezz.codes.polyglot_go import parse_go_unit
from jezz.codes.polyglot_ir import PolyglotUnit, SupportedLanguage
from jezz.codes.polyglot_rust import parse_rust_unit
from jezz.codes.polyglot_ts import parse_typescript_unit
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    LoopBudget,
    ShortText,
    jezz_callable,
    read_bounded_text,
)

_DOTTED_MODULE_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_DTS_AMBIENT_FN_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*export\s+declare\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*(\([^;\n]*\)(?:\s*:\s*[^;\n]+)?)",
    re.MULTILINE,
)
_DTS_AMBIENT_CLASS_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*export\s+declare\s+(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_GO_MOD_HEADER_RE: Final[re.Pattern[str]] = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
_GO_REQUIRE_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:require\s+)?([A-Za-z0-9._/-]+\.[A-Za-z0-9._/-]+)\s+(v\S+)",
    re.MULTILINE,
)
_JSON_MAP_ADAPTER: Final[TypeAdapter[dict[str, object]]] = TypeAdapter(dict[str, object])
_TS_FALLBACK_ENTRIES: Final[tuple[str, ...]] = (
    "index.d.ts",
    "dist/index.d.ts",
    "index.ts",
    "src/index.ts",
)
_BLOCKED_MODULE_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "antigravity",
        "idlelib",
        "pty",
        "this",
        "tkinter",
        "turtle",
        "turtledemo",
        "webbrowser",
    }
)


class PackageApiSummary(BaseModel):
    """Structured signature inventory of an installed or workspace-local package module."""

    model_config = STRICT_FROZEN_CONFIG

    module_path: IdentifierStr
    distribution_version: BoundedText = "stdlib-or-local"
    exported_classes: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    exported_callables: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    member_signatures: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ] = ()

    @jezz_callable
    def to_compact_text(self) -> BoundedText:
        """Render a concise summary suitable for specialist agent tool consumption."""
        lines = [
            f"Module `{self.module_path}` (version: {self.distribution_version}):",
            f"- Classes: {list(self.exported_classes[:24])}",
            f"- Callables: {list(self.exported_callables[:24])}",
        ]
        if self.member_signatures:
            lines.append("- Signatures:")
            lines.extend(f"  - {sig}" for sig in self.member_signatures)
        return "\n".join(lines)[:32_768]


@dataclass
class _ApiCollector:
    max_members: int
    explicit_all: bool
    effective_filter: str | None
    classes: list[str] = field(default_factory=list)
    callables: list[str] = field(default_factory=list)
    signatures: list[str] = field(default_factory=list)


@dataclass
class _PolyglotExportBuffer:
    classes: list[str] = field(default_factory=list)
    callables: list[str] = field(default_factory=list)
    signatures: list[str] = field(default_factory=list)


def _validate_safe_module_path(module_path: str) -> str:
    if _DOTTED_MODULE_RE.fullmatch(module_path) is None:
        msg = f"Invalid dotted Python module path '{module_path}'."
        raise ValueError(msg)
    root = module_path.split(".", maxsplit=1)[0]
    if root in _BLOCKED_MODULE_ROOTS:
        msg = f"Refusing side-effectful module '{module_path}' during API introspection."
        raise ValueError(msg)
    return root


def _walk_attr_chain(root_obj: object, attrs: Sequence[str]) -> bool:
    curr = root_obj
    for attr in attrs:
        if not hasattr(curr, attr):
            return False
        curr = getattr(curr, attr)
    return True


def _import_module_or_parent_symbol(
    module_path: str,
    symbol_filter: str | None,
) -> tuple[ModuleType, str, str | None]:
    importlib.invalidate_caches()
    try:
        return importlib.import_module(module_path), module_path, symbol_filter
    except ModuleNotFoundError as root_exc:
        parts = module_path.split(".")
        budget = LoopBudget(max_steps=16)
        idx = len(parts) - 1
        while idx >= 1:
            budget.tick()
            candidate_mod = ".".join(parts[:idx])
            tail_attrs = parts[idx:]
            try:
                mod = importlib.import_module(candidate_mod)
            except ModuleNotFoundError:
                idx -= 1
                continue
            if _walk_attr_chain(mod, tail_attrs):
                return mod, candidate_mod, symbol_filter or tail_attrs[-1]
            raise root_exc
        raise


def _resolve_dist_version(root_pkg: str) -> str:
    candidates = (root_pkg, root_pkg.replace("_", "-"))
    for name in candidates:
        try:
            return importlib.metadata.version(name)[:128]
        except importlib.metadata.PackageNotFoundError:
            continue
    return "stdlib-or-local"


def _public_member_names(mod: ModuleType) -> tuple[list[str], bool]:
    namespace = vars(mod)
    dunder_all: object = namespace.get("__all__")
    if isinstance(dunder_all, Sequence) and not isinstance(dunder_all, str | bytes):
        explicit = [item for item in dunder_all if isinstance(item, str) and item]
        if explicit:
            return sorted(dict.fromkeys(explicit)), True
    own: list[str] = []
    foreign: list[str] = []
    for name in sorted(k for k in namespace if not k.startswith("_")):
        obj = namespace.get(name)
        is_own = isinstance(obj, TypeAliasType) or getattr(obj, "__module__", None) == mod.__name__
        (own if is_own else foreign).append(name)
    return [*own, *foreign], False


def _format_member_signature(name: str, obj: object) -> str | None:
    if isinstance(obj, TypeAliasType):
        return f"type {name} = {obj.__value__}"[:512]
    try:
        sig = inspect.signature(obj) if callable(obj) else None
    except ValueError, TypeError:
        return f"{name}(...)"
    if sig is None:
        return None
    kind_prefix = "class " if isinstance(obj, type) else "def "
    return f"{kind_prefix}{name}{sig}"[:512]


def _matches_symbol_filter(name: str, symbol_filter: str | None) -> bool:
    if symbol_filter is None:
        return True
    needle = symbol_filter.lower()
    return needle in name.lower()


def _own_class_method_signatures(
    cls_name: str,
    cls_obj: type[object],
    effective_filter: str | None,
) -> list[str]:
    cls_matched = _matches_symbol_filter(cls_name, effective_filter)
    out: list[str] = []
    for attr_name, raw_attr in sorted(vars(cls_obj).items()):
        if attr_name.startswith("_") or isinstance(raw_attr, property):
            continue
        qual = f"{cls_name}.{attr_name}"
        if not (cls_matched or _matches_symbol_filter(qual, effective_filter)):
            continue
        bound = getattr(cls_obj, attr_name, None)
        if not callable(bound):
            continue
        formatted = _format_member_signature(qual, bound)
        if formatted is not None:
            out.append(formatted)
    return out


def _append_bounded_sigs(target: list[str], additions: Sequence[str], max_members: int) -> None:
    rem = max_members - len(target)
    if rem > 0:
        target.extend(additions[:rem])


def _record_class_member(
    mod: ModuleType,
    name: str,
    cls_obj: type[object],
    collector: _ApiCollector,
) -> None:
    name_hit = _matches_symbol_filter(name, collector.effective_filter)
    is_own = collector.explicit_all or getattr(cls_obj, "__module__", None) == mod.__name__
    method_sigs = (
        _own_class_method_signatures(name, cls_obj, collector.effective_filter) if is_own else []
    )
    if not name_hit and not method_sigs:
        return
    collector.classes.append(name)
    cls_sigs: list[str] = []
    if name_hit:
        head = _format_member_signature(name, cls_obj)
        if head is not None:
            cls_sigs.append(head)
    cls_sigs.extend(method_sigs)
    _append_bounded_sigs(collector.signatures, cls_sigs, collector.max_members)


def _record_module_member(
    mod: ModuleType,
    name: str,
    obj: object,
    collector: _ApiCollector,
) -> None:
    if isinstance(obj, type) and issubclass(obj, object):
        _record_class_member(mod, name, obj, collector)
        return
    if not _matches_symbol_filter(name, collector.effective_filter):
        return
    if callable(obj):
        collector.callables.append(name)
    formatted = _format_member_signature(name, obj)
    if formatted is not None:
        _append_bounded_sigs(collector.signatures, (formatted,), collector.max_members)


@jezz_callable
def inspect_installed_package_api(
    module_path: IdentifierStr,
    *,
    symbol_filter: IdentifierStr | None = None,
    max_members: Annotated[int, Field(ge=1, le=64)] = 32,
) -> PackageApiSummary:
    """Introspect exported classes, callables, and signatures of an installed Python module."""
    root_pkg = _validate_safe_module_path(module_path)
    mod, resolved_mod_path, effective_filter = _import_module_or_parent_symbol(
        module_path,
        symbol_filter,
    )
    version = _resolve_dist_version(root_pkg)
    names, explicit_all = _public_member_names(mod)
    collector = _ApiCollector(
        max_members=max_members,
        explicit_all=explicit_all,
        effective_filter=effective_filter,
    )
    namespace = vars(mod)

    for name in names:
        if len(name) > 256:
            continue
        obj = namespace.get(name)
        if obj is not None:
            _record_module_member(mod, name, obj, collector)

    return PackageApiSummary(
        module_path=resolved_mod_path,
        distribution_version=version,
        exported_classes=tuple(collector.classes[:256]),
        exported_callables=tuple(collector.callables[:256]),
        member_signatures=tuple(collector.signatures[:max_members]),
    )


def _dedupe_identifiers(items: Sequence[str], limit: int) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in items:
        cleaned = raw.strip()[:256]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
            if len(ordered) >= limit:
                break
    return tuple(ordered)


def _collect_unit_exports(
    unit: PolyglotUnit,
    symbol_filter: str | None,
    buf: _PolyglotExportBuffer,
) -> None:
    for decl in unit.types:
        if decl.is_public and _matches_symbol_filter(decl.name, symbol_filter):
            buf.classes.append(decl.name)
            buf.signatures.append((decl.signature_summary or f"{decl.kind} {decl.name}")[:512])
    for fn in unit.callables:
        if (
            fn.is_public
            and not fn.is_test_callable
            and _matches_symbol_filter(fn.qualname, symbol_filter)
        ):
            buf.callables.append(fn.qualname)
            buf.signatures.append((fn.signature_summary or fn.qualname)[:512])


def _summarize_polyglot_units(
    module_path: str,
    version: str,
    units: Sequence[PolyglotUnit],
    *,
    symbol_filter: str | None,
    max_members: int,
    seed: _PolyglotExportBuffer | None = None,
) -> PackageApiSummary:
    buf = seed if seed is not None else _PolyglotExportBuffer()
    for unit in units:
        _collect_unit_exports(unit, symbol_filter, buf)
    return PackageApiSummary(
        module_path=module_path[:256],
        distribution_version=version[:128] or "local",
        exported_classes=_dedupe_identifiers(buf.classes, 256),
        exported_callables=_dedupe_identifiers(buf.callables, 256),
        member_signatures=tuple(dict.fromkeys(s for s in buf.signatures if s))[:max_members],
    )


@contextmanager
def _with_venv_site_packages(repo_root: Path) -> Iterator[None]:
    venv_dir = ContainedPath.from_parts(repo_root.resolve(), ".venv").resolved_path
    site_dirs: list[str] = []
    if venv_dir.is_dir():
        for cand in (
            *venv_dir.glob("lib/python*/site-packages"),
            *venv_dir.glob("Lib/site-packages"),
        ):
            cand_str = str(cand.resolve())
            if cand.is_dir() and cand_str not in sys.path:
                site_dirs.append(cand_str)
    for item in reversed(site_dirs):
        sys.path.insert(0, item)
    try:
        yield
    finally:
        for item in site_dirs:
            if item in sys.path:
                sys.path.remove(item)


def _extract_dts_ambient_exports(
    source: str,
    symbol_filter: str | None,
) -> _PolyglotExportBuffer:
    classes = [
        m.group(1)
        for m in _DTS_AMBIENT_CLASS_RE.finditer(source)
        if _matches_symbol_filter(m.group(1), symbol_filter)
    ]
    callables: list[str] = []
    sigs: list[str] = [f"class {name}" for name in classes]
    for match in _DTS_AMBIENT_FN_RE.finditer(source):
        fn_name = match.group(1)
        if _matches_symbol_filter(fn_name, symbol_filter):
            callables.append(fn_name)
            sigs.append(f"function {fn_name}{match.group(2).strip()}"[:512])
    return _PolyglotExportBuffer(classes=classes, callables=callables, signatures=sigs)


def _load_npm_entry_candidates(pkg_dir: Path) -> tuple[str, list[str]]:
    pkg_json = ContainedPath.from_parts(pkg_dir, "package.json")
    if not pkg_json.resolved_path.is_file():
        return "local", list(_TS_FALLBACK_ENTRIES)
    data = _JSON_MAP_ADAPTER.validate_json(read_bounded_text(pkg_json, max_bytes=262_144))
    raw_ver = data.get("version")
    version = raw_ver if isinstance(raw_ver, str) and raw_ver.strip() else "local"
    entries: list[str] = []
    for key in ("types", "typings", "module", "main"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            entries.append(val.strip().removeprefix("./"))
    entries.extend(_TS_FALLBACK_ENTRIES)
    return version, entries


def _inspect_npm_package(
    repo_root: Path,
    package_spec: str,
    symbol_filter: str | None,
    max_members: int,
) -> PackageApiSummary:
    pkg_dir = ContainedPath.from_parts(
        repo_root.resolve(),
        f"node_modules/{package_spec}",
    ).resolved_path
    if not pkg_dir.is_dir():
        msg = f"TypeScript package '{package_spec}' not found in workspace node_modules."
        raise ValueError(msg)
    version, candidates = _load_npm_entry_candidates(pkg_dir)
    for rel_entry in dict.fromkeys(candidates):
        try:
            entry_cp = ContainedPath.from_parts(pkg_dir, rel_entry)
        except ValueError:
            continue
        if not entry_cp.resolved_path.is_file():
            continue
        src = read_bounded_text(entry_cp, max_bytes=524_288)
        seed = _extract_dts_ambient_exports(src, symbol_filter)
        units: tuple[PolyglotUnit, ...] = ()
        if rel_entry.endswith((".ts", ".tsx")):
            try:
                units = (parse_typescript_unit(entry_cp.relative_posix, src),)
            except SyntaxError:
                units = ()
        return _summarize_polyglot_units(
            package_spec,
            version,
            units,
            symbol_filter=symbol_filter,
            max_members=max_members,
            seed=seed,
        )
    return PackageApiSummary(module_path=package_spec, distribution_version=version)


def _find_local_rust_crate_dir(repo_root: Path, crate_spec: str) -> Path | None:
    norm = crate_spec.replace("-", "_")
    hyphen = crate_spec.replace("_", "-")
    rel_candidates = (
        ".",
        f"crates/{crate_spec}",
        f"crates/{hyphen}",
        f"crates/{norm}",
        crate_spec,
        hyphen,
        f"vendor/{crate_spec}",
        f"vendor/{hyphen}",
    )
    for rel in dict.fromkeys(rel_candidates):
        try:
            cand_dir = ContainedPath.from_parts(repo_root, rel).resolved_path
            cargo_cp = ContainedPath.from_parts(cand_dir, "Cargo.toml")
        except ValueError:
            continue
        if not cargo_cp.resolved_path.is_file():
            continue
        try:
            parsed = tomllib.loads(read_bounded_text(cargo_cp, max_bytes=262_144))
        except tomllib.TOMLDecodeError:
            continue
        pkg_tbl = parsed.get("package")
        pkg_name = pkg_tbl.get("name") if isinstance(pkg_tbl, dict) else None
        if isinstance(pkg_name, str) and pkg_name.replace("-", "_") == norm:
            return cand_dir
    return None


def _extract_cargo_dep_version(spec: object) -> str:
    if isinstance(spec, str):
        return spec[:128]
    if isinstance(spec, dict) and isinstance(spec.get("version"), str):
        return str(spec["version"])[:128]
    return "declared"


def _find_in_cargo_dep_table(sec: object, crate_norm: str) -> str | None:
    if not isinstance(sec, dict):
        return None
    for key, spec in sec.items():
        if isinstance(key, str) and key.replace("-", "_") == crate_norm:
            return _extract_cargo_dep_version(spec)
    return None


def _lookup_cargo_declared_version(repo_root: Path, crate_norm: str) -> str | None:
    cargo_cp = ContainedPath.from_parts(repo_root, "Cargo.toml")
    if not cargo_cp.resolved_path.is_file():
        return None
    try:
        parsed = tomllib.loads(read_bounded_text(cargo_cp, max_bytes=262_144))
    except tomllib.TOMLDecodeError:
        return None
    ws = parsed.get("workspace")
    ws_deps = ws.get("dependencies") if isinstance(ws, dict) else None
    for sec in (parsed.get("dependencies"), parsed.get("dev-dependencies"), ws_deps):
        found = _find_in_cargo_dep_table(sec, crate_norm)
        if found is not None:
            return found
    return None


def _inspect_rust_crate(
    repo_root: Path,
    crate_spec: str,
    symbol_filter: str | None,
    max_members: int,
) -> PackageApiSummary:
    resolved_root = repo_root.resolve()
    norm = crate_spec.replace("-", "_")
    crate_dir = _find_local_rust_crate_dir(resolved_root, crate_spec)
    if crate_dir is None:
        declared_ver = _lookup_cargo_declared_version(resolved_root, norm)
        if declared_ver is None:
            msg = f"Rust crate '{crate_spec}' not found in workspace or Cargo.toml."
            raise ValueError(msg)
        return PackageApiSummary(module_path=norm, distribution_version=declared_ver)
    cargo_data = tomllib.loads(
        read_bounded_text(ContainedPath.from_parts(crate_dir, "Cargo.toml"), max_bytes=262_144)
    )
    pkg_tbl = cargo_data.get("package")
    raw_ver = pkg_tbl.get("version") if isinstance(pkg_tbl, dict) else None
    version = raw_ver if isinstance(raw_ver, str) else "workspace"
    lib_tbl = cargo_data.get("lib")
    lib_rel = (
        lib_tbl.get("path")
        if isinstance(lib_tbl, dict) and isinstance(lib_tbl.get("path"), str)
        else "src/lib.rs"
    )
    units: list[PolyglotUnit] = []
    for entry_rel in dict.fromkeys((str(lib_rel), "src/lib.rs", "src/main.rs")):
        entry_cp = ContainedPath.from_parts(crate_dir, entry_rel)
        if entry_cp.resolved_path.is_file():
            src = read_bounded_text(entry_cp, max_bytes=524_288)
            units.append(parse_rust_unit(entry_cp.relative_posix, src))
            break
    return _summarize_polyglot_units(
        norm,
        version,
        units,
        symbol_filter=symbol_filter,
        max_members=max_members,
    )


def _read_go_mod_metadata(repo_root: Path) -> tuple[str, dict[str, str]]:
    gomod_cp = ContainedPath.from_parts(repo_root, "go.mod")
    if not gomod_cp.resolved_path.is_file():
        return "", {}
    text = read_bounded_text(gomod_cp, max_bytes=262_144)
    mod_match = _GO_MOD_HEADER_RE.search(text)
    prefix = mod_match.group(1) if mod_match is not None else ""
    versions = {m.group(1): m.group(2) for m in _GO_REQUIRE_LINE_RE.finditer(text)}
    return prefix, versions


def _resolve_go_package_dir(repo_root: Path, pkg_spec: str, module_prefix: str) -> Path | None:
    rel_candidates: list[str] = [f"vendor/{pkg_spec}", pkg_spec]
    if module_prefix and pkg_spec == module_prefix:
        rel_candidates.insert(0, ".")
    elif module_prefix and pkg_spec.startswith(f"{module_prefix}/"):
        rel_candidates.insert(0, pkg_spec.removeprefix(f"{module_prefix}/"))
    for rel in dict.fromkeys(rel_candidates):
        try:
            cand = ContainedPath.from_parts(repo_root, rel).resolved_path
        except ValueError:
            continue
        if cand.is_dir() and any(
            f.is_file() and not f.name.endswith("_test.go") for f in cand.glob("*.go")
        ):
            return cand
    return None


def _lookup_go_declared_version(pkg_spec: str, versions: dict[str, str]) -> str | None:
    if pkg_spec in versions:
        return versions[pkg_spec]
    for mod_path, ver in versions.items():
        if pkg_spec.startswith(f"{mod_path}/"):
            return ver
    return None


def _inspect_go_package(
    repo_root: Path,
    pkg_spec: str,
    symbol_filter: str | None,
    max_members: int,
) -> PackageApiSummary:
    resolved_root = repo_root.resolve()
    mod_prefix, versions = _read_go_mod_metadata(resolved_root)
    declared_ver = _lookup_go_declared_version(pkg_spec, versions)
    pkg_dir = _resolve_go_package_dir(resolved_root, pkg_spec, mod_prefix)
    if pkg_dir is None:
        if declared_ver is None:
            msg = f"Go package '{pkg_spec}' not found in workspace, vendor/, or go.mod."
            raise ValueError(msg)
        return PackageApiSummary(module_path=pkg_spec, distribution_version=declared_ver)
    go_files = sorted(
        f for f in pkg_dir.glob("*.go") if f.is_file() and not f.name.endswith("_test.go")
    )[:16]
    units: list[PolyglotUnit] = []
    for go_file in go_files:
        cp = ContainedPath.from_parts(pkg_dir, go_file.name)
        units.append(parse_go_unit(go_file.name, read_bounded_text(cp, max_bytes=524_288)))
    return _summarize_polyglot_units(
        pkg_spec,
        declared_ver or "workspace",
        units,
        symbol_filter=symbol_filter,
        max_members=max_members,
    )


@jezz_callable
def inspect_polyglot_package_api(
    repo_root: Path,
    language: SupportedLanguage,
    package_spec: IdentifierStr,
    *,
    symbol_filter: IdentifierStr | None = None,
    max_members: Annotated[int, Field(ge=1, le=64)] = 32,
) -> PackageApiSummary:
    """Deterministic offline API introspection across Python, TypeScript, Rust, and Go packages."""
    cleaned = package_spec.strip()
    match language:
        case SupportedLanguage.PYTHON:
            with _with_venv_site_packages(repo_root):
                return inspect_installed_package_api(
                    cleaned,
                    symbol_filter=symbol_filter,
                    max_members=max_members,
                )
        case SupportedLanguage.TYPESCRIPT:
            try:
                return _inspect_npm_package(repo_root, cleaned, symbol_filter, max_members)
            except json.JSONDecodeError as exc:
                msg = f"Malformed package.json for TypeScript package '{cleaned}'."
                raise ValueError(msg) from exc
        case SupportedLanguage.RUST:
            return _inspect_rust_crate(repo_root, cleaned, symbol_filter, max_members)
        case SupportedLanguage.GO:
            return _inspect_go_package(repo_root, cleaned, symbol_filter, max_members)
        case _:
            msg = f"Unsupported ecosystem '{language}' for package API introspection."
            raise ValueError(msg)
