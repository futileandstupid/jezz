"""Safe runtime environment and installed third-party API signature introspection (BHV-05/06)."""

import importlib
import importlib.metadata
import inspect
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import ModuleType
from typing import Annotated, Final, TypeAliasType

from pydantic import BaseModel, Field

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    IdentifierStr,
    LoopBudget,
    ShortText,
    jezz_callable,
)

_DOTTED_MODULE_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
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
    """Structured signature inventory of an installed Python module in the active environment."""

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
