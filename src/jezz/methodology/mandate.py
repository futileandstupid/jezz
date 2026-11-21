"""Pillar 3 (PCE): Anti-Agile, anti-theatre engineering mandates and disjoint write scopes."""

import re
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import BaseModel, Field, model_validator

from jezz.guards import STRICT_FROZEN_CONFIG, IdentifierStr, ShortText, jezz_callable

METHODOLOGY_NAME: Final[str] = "Permit-and-Contract Engineering"
METHODOLOGY_ACRONYM: Final[str] = "PCE"

_USER_STORY_THEATRE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bas\s+an?\s+[^,\n]{1,60},\s*i\s+want\b",
    re.IGNORECASE,
)
_CHECKBOX_THEATRE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n)\s*[-*]\s*\[[ xX]]",
)
_AGILE_CEREMONY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:story\s+points?|sprint\s+velocity|scrum\s+ceremony|backlog\s+grooming|"
    r"spec-driven\s+development)\b",
    re.IGNORECASE,
)
_WEASEL_MODAL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:should|ideally|maybe|might|nice\s+to\s+have|try\s+to)\b",
    re.IGNORECASE,
)


class PcePhase(StrEnum):
    """Five statutory phases of Permit-and-Contract Engineering (PCE)."""

    MANDATE = "mandate"
    PERMIT = "permit"
    CONTRACT = "contract"
    CONSTRUCTION = "construction"
    OCCUPANCY = "occupancy"


@jezz_callable
def validate_engineering_prose(text: ShortText, *, field_label: IdentifierStr) -> None:
    """Reject Agile persona user stories, ceremony jargon, and Markdown checkbox theatre."""
    if _USER_STORY_THEATRE_RE.search(text) is not None:
        msg = (
            f"{field_label} contains Agile persona user-story theatre ('As a ..., I want ...'); "
            "state an objective system constraint instead."
        )
        raise ValueError(msg)
    if _CHECKBOX_THEATRE_RE.search(text) is not None:
        msg = (
            f"{field_label} contains Markdown checkbox theatre ('- [ ]'); "
            "task topology belongs in a verified BehaviorGraph."
        )
        raise ValueError(msg)
    if _AGILE_CEREMONY_RE.search(text) is not None:
        msg = f"{field_label} references prohibited Agile/SDD ceremony terminology."
        raise ValueError(msg)


class DomainInvariant(BaseModel):
    """Falsifiable domain obligation bound directly to a compiled AST verification symbol."""

    model_config = STRICT_FROZEN_CONFIG

    invariant_id: Annotated[
        str,
        Field(min_length=7, max_length=16, pattern=r"^INV-[0-9]{3,6}$"),
    ]
    statement: ShortText
    verification_symbol: IdentifierStr

    @model_validator(mode="after")
    def _enforce_falsifiable_wording(self) -> Self:
        validate_engineering_prose(self.statement, field_label=f"Invariant {self.invariant_id}")
        if _WEASEL_MODAL_RE.search(self.statement) is not None:
            msg = (
                f"Invariant '{self.invariant_id}' uses non-normative weasel wording "
                "(should/might/ideally); state a strict falsifiable condition."
            )
            raise ValueError(msg)
        return self


class ModuleWriteScope(BaseModel):
    """Disjoint file-ownership envelope and contract surface for an engineering unit."""

    model_config = STRICT_FROZEN_CONFIG

    scope_id: IdentifierStr
    owned_files: Annotated[frozenset[IdentifierStr], Field(min_length=1, max_length=256)]
    contract_symbols: Annotated[tuple[IdentifierStr, ...], Field(min_length=1, max_length=128)]


def _verify_disjoint_scopes(scopes: tuple[ModuleWriteScope, ...]) -> None:
    seen_ids: set[str] = set()
    claimed_files: dict[str, str] = {}
    for scope in scopes:
        if scope.scope_id in seen_ids:
            msg = f"Duplicate ModuleWriteScope id '{scope.scope_id}'."
            raise ValueError(msg)
        seen_ids.add(scope.scope_id)
        for file_path in sorted(scope.owned_files):
            prior_owner = claimed_files.get(file_path)
            if prior_owner is not None:
                msg = (
                    f"Write-scope collision on '{file_path}' between '{prior_owner}' "
                    f"and '{scope.scope_id}'."
                )
                raise ValueError(msg)
            claimed_files[file_path] = scope.scope_id


class EngineeringMandate(BaseModel):
    """Phase I PCE artifact replacing prose PRDs, user stories, and tasks.md checklists."""

    model_config = STRICT_FROZEN_CONFIG

    mandate_id: Annotated[
        str,
        Field(min_length=5, max_length=64, pattern=r"^MND-[A-Za-z0-9_-]+$"),
    ]
    title: ShortText
    objective: ShortText
    invariants: Annotated[tuple[DomainInvariant, ...], Field(min_length=1, max_length=128)]
    write_scopes: Annotated[tuple[ModuleWriteScope, ...], Field(min_length=1, max_length=64)]
    out_of_scope: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()
    open_ambiguities: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def _enforce_mandate_integrity(self) -> Self:
        validate_engineering_prose(self.title, field_label="Mandate.title")
        validate_engineering_prose(self.objective, field_label="Mandate.objective")

        seen_invariants: set[str] = set()
        for inv in self.invariants:
            if inv.invariant_id in seen_invariants:
                msg = f"Duplicate invariant_id '{inv.invariant_id}' in '{self.mandate_id}'."
                raise ValueError(msg)
            seen_invariants.add(inv.invariant_id)

        _verify_disjoint_scopes(self.write_scopes)
        return self

    @property
    def requires_elicitation(self) -> bool:
        """Return True iff unresolved ambiguities require halting at BHV-02 ELICIT."""
        return len(self.open_ambiguities) > 0

    @jezz_callable
    def authorized_files(self) -> frozenset[IdentifierStr]:
        """Return the union of all file paths authorized for mutation across write scopes."""
        combined: set[str] = set()
        for scope in self.write_scopes:
            combined |= set(scope.owned_files)
        return frozenset(combined)
