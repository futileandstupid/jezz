"""Language-agnostic AST/CST intermediate representation for polyglot JBC v1.2 inspection."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Annotated, Final

import tree_sitter as ts
from pydantic import BaseModel, Field

from jezz.codes.ast_utils import CommentToken, RawDiscrepancy, SourceUnit
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_FROZENSET_VALIDATOR,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    ColumnOffset,
    IdentifierStr,
    InstanceGuard,
    LineNumber,
    LoopBudget,
    NonNegativeInt,
    SourceCodeText,
    jezz_callable,
)

_MAX_CST_ERROR_SCAN_NODES: Final[int] = 250_000

type GuardedTsLanguage = Annotated[ts.Language, InstanceGuard(ts.Language)]
type GuardedTsNode = Annotated[ts.Node, InstanceGuard(ts.Node)]


class SupportedLanguage(StrEnum):
    """Canonical source languages governed by the Polyglot JBC Inspector."""

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    RUST = "rust"
    GO = "go"


SUPPORTED_SOURCE_EXTENSIONS: Final[Mapping[str, SupportedLanguage]] = MappingProxyType(
    {
        ".py": SupportedLanguage.PYTHON,
        ".ts": SupportedLanguage.TYPESCRIPT,
        ".tsx": SupportedLanguage.TYPESCRIPT,
        ".rs": SupportedLanguage.RUST,
        ".go": SupportedLanguage.GO,
    }
)

LANGUAGE_DISPLAY_NAMES: Final[Mapping[SupportedLanguage, str]] = MappingProxyType(
    {
        SupportedLanguage.PYTHON: "Python",
        SupportedLanguage.TYPESCRIPT: "TypeScript",
        SupportedLanguage.RUST: "Rust",
        SupportedLanguage.GO: "Go",
    }
)


@jezz_callable
def detect_language_for_path(file_path: IdentifierStr) -> SupportedLanguage | None:
    """Return the ``SupportedLanguage`` for ``file_path``, or ``None`` if unsupported."""
    suffix = PurePosixPath(file_path).suffix.lower()
    return SUPPORTED_SOURCE_EXTENSIONS.get(suffix)


@jezz_callable
def is_supported_source_path(
    file_path: IdentifierStr,
    *,
    include_ambient_dts: bool = False,
) -> bool:
    """Return True iff ``file_path`` is an inspectable source module in a supported language."""
    lowered = file_path.lower()
    if not include_ambient_dts and lowered.endswith(".d.ts"):
        return False
    return detect_language_for_path(file_path) is not None


class EcosystemManifest(BaseModel):
    """Declared runtime, dev, and internal workspace roots for one language ecosystem."""

    model_config = STRICT_FROZEN_CONFIG

    runtime_roots: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=4096),
    ] = frozenset()
    dev_roots: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=4096),
    ] = frozenset()
    workspace_roots: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=1024),
    ] = frozenset()
    module_prefix: BoundedText = ""


class PolyglotManifest(BaseModel):
    """Multi-ecosystem dependency manifest spanning Python, TypeScript, Rust, and Go."""

    model_config = STRICT_FROZEN_CONFIG

    python: EcosystemManifest = EcosystemManifest()
    typescript: EcosystemManifest = EcosystemManifest()
    rust: EcosystemManifest = EcosystemManifest()
    go: EcosystemManifest = EcosystemManifest()

    @jezz_callable
    def for_language(self, language: SupportedLanguage) -> EcosystemManifest:
        """Return the ``EcosystemManifest`` corresponding to ``language``."""
        table: dict[SupportedLanguage, EcosystemManifest] = {
            SupportedLanguage.PYTHON: self.python,
            SupportedLanguage.TYPESCRIPT: self.typescript,
            SupportedLanguage.RUST: self.rust,
            SupportedLanguage.GO: self.go,
        }
        return table[language]


class PolyglotParam(BaseModel):
    """Normalized parameter descriptor extracted from a TypeScript, Rust, or Go callable."""

    model_config = STRICT_FROZEN_CONFIG

    name: IdentifierStr
    has_type: bool = True
    type_text: BoundedText = ""
    has_mutable_default: bool = False
    is_unbounded_primitive: bool = False
    is_untyped_bag: bool = False
    has_any_erasure: bool = False
    line: LineNumber = 1
    column: ColumnOffset = 0


class PolyglotCallable(BaseModel):
    """Normalized callable descriptor supporting Articles I, II, IV, and VI checks."""

    model_config = STRICT_FROZEN_CONFIG

    name: IdentifierStr
    qualname: IdentifierStr
    enclosing_type: BoundedText = ""
    is_public: bool = False
    is_method: bool = False
    is_receiver_used: bool = True
    is_abstract_or_stub: bool = False
    is_test_callable: bool = False
    params: Annotated[
        tuple[PolyglotParam, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    has_return_type: bool = True
    return_type_text: BoundedText = ""
    return_has_any_erasure: bool = False
    return_is_untyped_bag: bool = False
    has_boundary_contract: bool = True
    cognitive_complexity: NonNegativeInt = 0
    max_nesting_depth: NonNegativeInt = 0
    statement_count: NonNegativeInt = 0
    start_line: LineNumber = 1
    end_line: LineNumber = 1
    column: ColumnOffset = 0
    signature_summary: BoundedText = ""
    calls: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    accessed_fields: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=128),
    ] = frozenset()
    performs_io: bool = False
    is_recursive_unguarded: bool = False
    has_mixed_or_missing_return: bool = False
    unguarded_divisors: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32),
    ] = ()
    vacuous_contracts: Annotated[
        tuple[BoundedText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=16),
    ] = ()
    has_test_assertion: bool = True


class PolyglotTypeDecl(BaseModel):
    """Normalized class, struct, interface, enum, or schema declaration."""

    model_config = STRICT_FROZEN_CONFIG

    name: IdentifierStr
    kind: IdentifierStr
    is_public: bool = False
    is_schema_or_value: bool = False
    is_strict_schema: bool = True
    fields: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    sensitive_unredacted_fields: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    unbounded_fields: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ] = ()
    has_any_erasure_field: bool = False
    line: LineNumber = 1
    end_line: LineNumber = 1
    column: ColumnOffset = 0
    signature_summary: BoundedText = ""


class PolyglotImport(BaseModel):
    """Normalized import/use declaration supporting JBC-COG-003 and JBC-VRF-003."""

    model_config = STRICT_FROZEN_CONFIG

    raw_target: BoundedText
    root_pkg: BoundedText = ""
    resolved_module: BoundedText = ""
    is_internal: bool = False
    is_wildcard: bool = False
    private_symbols: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    line: LineNumber = 1
    column: ColumnOffset = 0


@dataclass(frozen=True)
class PolyglotUnit:
    """Parsed TypeScript, Rust, or Go source module ready for JBC inspection."""

    language: SupportedLanguage
    file_path: str
    module_name: str
    source: str
    comments: tuple[CommentToken, ...]
    is_test_file: bool
    callables: tuple[PolyglotCallable, ...]
    types: tuple[PolyglotTypeDecl, ...]
    imports: tuple[PolyglotImport, ...]
    hazards: tuple[RawDiscrepancy, ...]
    has_external_calls: bool = False
    has_arithmetic_div: bool = False


type InspectedUnit = SourceUnit | PolyglotUnit


def _first_syntax_error_node(root: ts.Node) -> ts.Node | None:
    stack: list[ts.Node] = [root]
    budget = LoopBudget(max_steps=_MAX_CST_ERROR_SCAN_NODES)
    while stack:
        budget.tick()
        current = stack.pop()
        if current.is_error or current.is_missing:
            return current
        if current.has_error:
            stack.extend(reversed(current.children))
    return None


@jezz_callable
def parse_cst_root(
    ts_language: GuardedTsLanguage,
    language_kind: SupportedLanguage,
    file_path: IdentifierStr,
    source: SourceCodeText,
) -> GuardedTsNode:
    """Parse ``source`` via ``tree_sitter`` and fail closed with ``SyntaxError`` on CST errors."""
    parser = ts.Parser(ts_language)
    tree = parser.parse(source.encode("utf-8"))
    root = tree.root_node
    if not root.has_error:
        return root
    err_node = _first_syntax_error_node(root) or root
    line_no = err_node.start_point.row + 1
    col_no = err_node.start_point.column + 1
    lines = source.splitlines()
    line_text = lines[line_no - 1] if 1 <= line_no <= len(lines) else ""
    lang_label = LANGUAGE_DISPLAY_NAMES[language_kind]
    snippet = (err_node.text or b"").decode("utf-8", "replace")[:32]
    detail = f"invalid {lang_label} syntax near '{snippet}'"
    raise SyntaxError(detail, (file_path, line_no, col_no, line_text))
