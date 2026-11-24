"""Python PEP 695 TypeAliasType misuse static guard for staged candidate units."""

import ast
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import Annotated, Final

from pydantic import Field

from jezz.codes.ast_utils import SourceUnit, dotted_expr_name
from jezz.codes.polyglot_ir import InspectedUnit
from jezz.guards import ShortText, jezz_callable

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
    candidate_units: Annotated[Sequence[InspectedUnit], Field(max_length=4096)],
    workspace_units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)] = (),
) -> ShortText | None:
    """Detect runtime-fatal calls or subscripts on Python PEP 695 TypeAliasType definitions."""
    py_candidates = [u for u in candidate_units if isinstance(u, SourceUnit)]
    if not py_candidates:
        return None
    py_workspace = [u for u in workspace_units if isinstance(u, SourceUnit)]
    all_aliases, nongeneric_aliases = _collect_known_type_aliases((*py_workspace, *py_candidates))
    for unit in py_candidates:
        issue = _scan_unit_type_alias_misuse(unit, all_aliases, nongeneric_aliases)
        if issue is not None:
            return issue
    return None
