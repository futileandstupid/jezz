"""Safe runtime environment and installed third-party API signature introspection (BHV-05/06)."""

import importlib
import importlib.metadata
import inspect
import re
from collections.abc import Sequence
from types import ModuleType
from typing import Annotated, Final

from pydantic import BaseModel, Field

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    IdentifierStr,
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


def _validate_safe_module_path(module_path: str) -> str:
    if _DOTTED_MODULE_RE.fullmatch(module_path) is None:
        msg = f"Invalid dotted Python module path '{module_path}'."
        raise ValueError(msg)
    root = module_path.split(".", maxsplit=1)[0]
    if root in _BLOCKED_MODULE_ROOTS:
        msg = f"Refusing side-effectful module '{module_path}' during API introspection."
        raise ValueError(msg)
    return root


def _resolve_dist_version(root_pkg: str) -> str:
    candidates = (root_pkg, root_pkg.replace("_", "-"))
    for name in candidates:
        try:
            return importlib.metadata.version(name)[:128]
        except importlib.metadata.PackageNotFoundError:
            continue
    return "stdlib-or-local"


def _public_member_names(mod: ModuleType) -> list[str]:
    dunder_all: object = vars(mod).get("__all__")
    if isinstance(dunder_all, Sequence) and not isinstance(dunder_all, str | bytes):
        explicit = [item for item in dunder_all if isinstance(item, str) and item]
        if explicit:
            return sorted(dict.fromkeys(explicit))
    return sorted(name for name in vars(mod) if not name.startswith("_"))


def _format_member_signature(name: str, obj: object) -> str | None:
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


@jezz_callable
def inspect_installed_package_api(
    module_path: IdentifierStr,
    *,
    symbol_filter: IdentifierStr | None = None,
    max_members: Annotated[int, Field(ge=1, le=64)] = 32,
) -> PackageApiSummary:
    """Introspect exported classes, callables, and signatures of an installed Python module."""
    root_pkg = _validate_safe_module_path(module_path)
    mod = importlib.import_module(module_path)
    version = _resolve_dist_version(root_pkg)

    classes: list[str] = []
    callables: list[str] = []
    signatures: list[str] = []
    namespace = vars(mod)

    for name in _public_member_names(mod):
        if not _matches_symbol_filter(name, symbol_filter) or len(name) > 256:
            continue
        obj = namespace.get(name)
        if obj is None:
            continue
        if isinstance(obj, type):
            classes.append(name)
        elif callable(obj):
            callables.append(name)
        if len(signatures) < max_members:
            formatted = _format_member_signature(name, obj)
            if formatted is not None:
                signatures.append(formatted)

    return PackageApiSummary(
        module_path=module_path,
        distribution_version=version,
        exported_classes=tuple(classes[:256]),
        exported_callables=tuple(callables[:256]),
        member_signatures=tuple(signatures[:max_members]),
    )
