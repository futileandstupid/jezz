"""Hexagonal LanguageAdapterPort registry decoupling JBC v1.2 across Python, TS, Rust, and Go."""

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final, Protocol, runtime_checkable

from pydantic import Field

from jezz.codes.ast_utils import SourceUnit, parse_source_unit
from jezz.codes.polyglot_go import parse_go_unit
from jezz.codes.polyglot_ir import (
    InspectedUnit,
    PolyglotUnit,
    SupportedLanguage,
    detect_language_for_path,
    is_supported_source_path,
)
from jezz.codes.polyglot_rust import parse_rust_unit
from jezz.codes.polyglot_ts import parse_typescript_unit
from jezz.codes.taxonomy import CodeTrigger
from jezz.guards import IdentifierStr, InstanceGuard, SourceCodeText, jezz_callable

type _PolyglotParserFn = Callable[[IdentifierStr, SourceCodeText], PolyglotUnit]


@dataclass(frozen=True)
class _TreeSitterSpec:
    """Cohesive adapter configuration binding language, extensions, and CST parser."""

    language: SupportedLanguage
    extensions: frozenset[str]
    parser_fn: _PolyglotParserFn


@runtime_checkable
class LanguageAdapterPort(Protocol):
    """Language-agnostic port for parsing source units and detecting JBC hazard triggers."""

    @property
    def language(self) -> SupportedLanguage:
        """Return canonical language identifier."""

    @property
    def extensions(self) -> frozenset[str]:
        """Return lowercase file extensions governed by this adapter."""

    def parse_unit(self, file_path: IdentifierStr, source: SourceCodeText) -> InspectedUnit:
        """Parse ``source`` into a ``SourceUnit`` or ``PolyglotUnit`` (or raise ``SyntaxError``)."""

    def detect_triggers(self, unit: InspectedUnit) -> frozenset[CodeTrigger]:
        """Detect statutory ``CodeTrigger`` categories present in ``unit``."""


type GuardedLanguageAdapterPort = Annotated[
    LanguageAdapterPort,
    InstanceGuard(LanguageAdapterPort),
]


def _detect_python_unit_triggers(unit: SourceUnit) -> frozenset[CodeTrigger]:
    triggers: set[CodeTrigger] = {CodeTrigger.UNIVERSAL}
    if unit.is_test_file:
        triggers.add(CodeTrigger.VERIFICATION_SUITE)
    if unit.classes:
        triggers.add(CodeTrigger.CONSTRAINED_DOMAIN)
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div | ast.FloorDiv | ast.Mod):
            triggers.add(CodeTrigger.CONSTRAINED_DOMAIN)
        if isinstance(node, ast.Call):
            triggers.add(CodeTrigger.EXTERNAL_BOUNDARY)
    return frozenset(triggers)


def _detect_polyglot_unit_triggers(unit: PolyglotUnit) -> frozenset[CodeTrigger]:
    triggers: set[CodeTrigger] = {CodeTrigger.UNIVERSAL}
    if unit.is_test_file or any(fn.is_test_callable for fn in unit.callables):
        triggers.add(CodeTrigger.VERIFICATION_SUITE)
    if unit.types or unit.has_arithmetic_div:
        triggers.add(CodeTrigger.CONSTRAINED_DOMAIN)
    if unit.has_external_calls or unit.imports:
        triggers.add(CodeTrigger.EXTERNAL_BOUNDARY)
    return frozenset(triggers)


class PythonLanguageAdapter:
    """Stdlib ``ast`` + ``tokenize`` language adapter for Python ``.py`` units."""

    @jezz_callable
    def __init__(self) -> None:
        self._extensions: tuple[SupportedLanguage, frozenset[str]] = (
            SupportedLanguage.PYTHON,
            frozenset({".py"}),
        )

    @property
    def language(self) -> SupportedLanguage:
        """Return ``SupportedLanguage.PYTHON``."""
        return self._extensions[0]

    @property
    def extensions(self) -> frozenset[str]:
        """Return ``{'.py'}``."""
        return self._extensions[1]

    @jezz_callable
    def parse_unit(self, file_path: IdentifierStr, source: SourceCodeText) -> SourceUnit:
        """Parse Python ``source`` via stdlib ``ast`` + ``tokenize``."""
        if self._extensions[0] != SupportedLanguage.PYTHON:
            msg = f"Unexpected adapter state: {self._extensions[0]}"
            raise ValueError(msg)
        return parse_source_unit(file_path, source)

    @jezz_callable
    def detect_triggers(self, unit: InspectedUnit) -> frozenset[CodeTrigger]:
        """Detect statutory hazard triggers for ``unit``."""
        if not isinstance(unit, SourceUnit):
            msg = f"{self._extensions[0]} adapter requires a Python SourceUnit."
            raise TypeError(msg)
        return _detect_python_unit_triggers(unit)


class TreeSitterLanguageAdapter:
    """``tree-sitter`` CST language adapter for TypeScript, Rust, or Go units."""

    @jezz_callable
    def __init__(
        self,
        language: SupportedLanguage,
        extensions: Annotated[frozenset[IdentifierStr], Field(min_length=1, max_length=8)],
        parser_fn: _PolyglotParserFn,
    ) -> None:
        self._spec = _TreeSitterSpec(
            language=language,
            extensions=extensions,
            parser_fn=parser_fn,
        )

    @property
    def language(self) -> SupportedLanguage:
        """Return canonical ``SupportedLanguage``."""
        return self._spec.language

    @property
    def extensions(self) -> frozenset[str]:
        """Return supported file extensions."""
        return self._spec.extensions

    @jezz_callable
    def parse_unit(self, file_path: IdentifierStr, source: SourceCodeText) -> PolyglotUnit:
        """Parse ``source`` into a normalized ``PolyglotUnit``."""
        return self._spec.parser_fn(file_path, source)

    @jezz_callable
    def detect_triggers(self, unit: InspectedUnit) -> frozenset[CodeTrigger]:
        """Detect statutory hazard triggers for ``unit``."""
        if not isinstance(unit, PolyglotUnit) or unit.language != self._spec.language:
            msg = f"Expected PolyglotUnit({self._spec.language}), received {type(unit).__name__}."
            raise TypeError(msg)
        return _detect_polyglot_unit_triggers(unit)


def _build_adapter_registry() -> Mapping[SupportedLanguage, LanguageAdapterPort]:
    registry: dict[SupportedLanguage, LanguageAdapterPort] = {
        SupportedLanguage.PYTHON: PythonLanguageAdapter(),
        SupportedLanguage.TYPESCRIPT: TreeSitterLanguageAdapter(
            SupportedLanguage.TYPESCRIPT,
            frozenset({".ts", ".tsx"}),
            parse_typescript_unit,
        ),
        SupportedLanguage.RUST: TreeSitterLanguageAdapter(
            SupportedLanguage.RUST,
            frozenset({".rs"}),
            parse_rust_unit,
        ),
        SupportedLanguage.GO: TreeSitterLanguageAdapter(
            SupportedLanguage.GO,
            frozenset({".go"}),
            parse_go_unit,
        ),
    }
    return MappingProxyType(registry)


LANGUAGE_ADAPTERS: Final[Mapping[SupportedLanguage, LanguageAdapterPort]] = (
    _build_adapter_registry()
)


@jezz_callable
def get_language_adapter(language: SupportedLanguage) -> GuardedLanguageAdapterPort:
    """Return the registered ``LanguageAdapterPort`` for ``language``."""
    return LANGUAGE_ADAPTERS[language]


@jezz_callable
def adapter_for_path(
    file_path: IdentifierStr,
    *,
    include_ambient_dts: bool = False,
) -> GuardedLanguageAdapterPort | None:
    """Return the ``LanguageAdapterPort`` matching ``file_path``'s extension, or ``None``."""
    if not is_supported_source_path(file_path, include_ambient_dts=include_ambient_dts):
        return None
    lang = detect_language_for_path(file_path)
    if lang is None:
        return None
    return LANGUAGE_ADAPTERS[lang]


@jezz_callable
def parse_any_source_unit(file_path: IdentifierStr, source: SourceCodeText) -> InspectedUnit:
    """Dispatch ``file_path`` to its registered language adapter and return an ``InspectedUnit``."""
    adapter = adapter_for_path(file_path, include_ambient_dts=True)
    if adapter is None:
        msg = (
            f"Unsupported source extension for '{file_path}'; expected '.py', '.ts', '.tsx', "
            "'.rs', or '.go'."
        )
        raise ValueError(msg)
    return adapter.parse_unit(file_path, source)


@jezz_callable
def detect_unit_triggers(unit: InspectedUnit) -> frozenset[CodeTrigger]:
    """Detect statutory ``CodeTrigger`` categories across any ``InspectedUnit``."""
    if isinstance(unit, SourceUnit):
        return _detect_python_unit_triggers(unit)
    return _detect_polyglot_unit_triggers(unit)
