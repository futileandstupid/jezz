"""Pillar 3 (PCE): EngineeringDossier and deterministic 5-phase occupancy auditor."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final

import icontract
from pydantic import BaseModel, Field

from jezz.behaviors.graph import BehaviorGraph, verify_behavior_graph
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.ast_utils import SourceUnit
from jezz.codes.inspector import compute_units_digest
from jezz.codes.permits import CertificateOfOccupancy, CodePermit, InspectionReport
from jezz.guards import STRICT_FROZEN_CONFIG, BoundedText, IdentifierStr, ShortText, jezz_callable
from jezz.methodology.mandate import EngineeringMandate, PcePhase

_PHASE_RANK: Final[Mapping[PcePhase, int]] = MappingProxyType(
    {
        PcePhase.MANDATE: 0,
        PcePhase.PERMIT: 1,
        PcePhase.CONTRACT: 2,
        PcePhase.CONSTRUCTION: 3,
        PcePhase.OCCUPANCY: 4,
    }
)
_PREDECESSOR_PHASE: Final[Mapping[PcePhase, PcePhase]] = MappingProxyType(
    {
        PcePhase.MANDATE: PcePhase.MANDATE,
        PcePhase.PERMIT: PcePhase.MANDATE,
        PcePhase.CONTRACT: PcePhase.PERMIT,
        PcePhase.CONSTRUCTION: PcePhase.CONTRACT,
        PcePhase.OCCUPANCY: PcePhase.CONSTRUCTION,
    }
)


class DossierViolationCode(StrEnum):
    """Canonical failure codes when an EngineeringDossier fails PCE phase-gate audit."""

    UNRESOLVED_AMBIGUITIES = "unresolved_ambiguities"
    INVALID_BEHAVIOR_GRAPH = "invalid_behavior_graph"
    MISSING_CONTRACT_STAGE = "missing_contract_stage"
    UNBOUND_INVARIANT_SYMBOL = "unbound_invariant_symbol"
    UNDECLARED_WRITE_MUTATION = "undeclared_write_mutation"
    UNINSPECTED_PERMIT_CODES = "uninspected_permit_codes"
    INSPECTION_RED_TAG = "inspection_red_tag"
    INVALID_OCCUPANCY_SEAL = "invalid_occupancy_seal"


class DossierViolation(BaseModel):
    """Single cited PCE methodology violation blocking occupancy."""

    model_config = STRICT_FROZEN_CONFIG

    code: DossierViolationCode
    phase: PcePhase
    message: ShortText
    remediation: ShortText


class EngineeringDossier(BaseModel):
    """Complete auditable work package uniting Mandate, Permit, BehaviorGraph, and Seal."""

    model_config = STRICT_FROZEN_CONFIG

    mandate: EngineeringMandate
    permit: CodePermit
    behavior_graph: BehaviorGraph
    mutated_files: Annotated[frozenset[IdentifierStr], Field(max_length=1024)] = frozenset()
    inspection_report: InspectionReport
    certificate: CertificateOfOccupancy | None = None


class DossierAuditReport(BaseModel):
    """Deterministic PCE audit verdict proving I |=_PCE M across all five phases."""

    model_config = STRICT_FROZEN_CONFIG

    mandate_id: IdentifierStr
    highest_verified_phase: PcePhase
    is_occupancy_sealed: bool
    verified_symbols: Annotated[tuple[IdentifierStr, ...], Field(max_length=256)]
    violations: Annotated[tuple[DossierViolation, ...], Field(max_length=512)]

    @jezz_callable
    def to_markdown(self) -> BoundedText:
        """Render a structured Markdown PCE Dossier Audit Report."""
        status = "OCCUPANCY_SEALED" if self.is_occupancy_sealed else "BLOCKED"
        lines = [
            f"# PCE Dossier Audit — `{self.mandate_id}`",
            f"- **Status:** `{status}`",
            f"- **Highest Verified Phase:** `{self.highest_verified_phase.upper()}`",
            f"- **Grounded Invariant Symbols:** `{len(self.verified_symbols)}`",
            f"- **Violations:** `{len(self.violations)}`",
        ]
        if self.violations:
            lines.extend(["", "## Cited Phase Violations"])
            for v in self.violations:
                lines.append(
                    f"- **[{v.phase.upper()}] `{v.code}`**: {v.message} — "
                    f"*Remediation:* {v.remediation}"
                )
        return "\n".join(lines)


def _add_qualified_symbol(
    symbols: set[str],
    prefix: str,
    src_prefix: str,
    name: str,
) -> None:
    symbols.add(name)
    if prefix:
        symbols.add(f"{prefix}{name}")
    if src_prefix:
        symbols.add(f"{src_prefix}{name}")


@jezz_callable
def extract_compiled_symbols(units: Sequence[SourceUnit]) -> frozenset[IdentifierStr]:
    """Extract all compiled class and callable symbols (short and module-qualified) in ``units``."""
    symbols: set[str] = set()
    for unit in units:
        prefix = f"{unit.module_name}." if unit.module_name else ""
        src_prefix = f"src.{prefix}" if (prefix and unit.file_path.startswith("src/")) else ""
        for cls_node in unit.classes:
            _add_qualified_symbol(symbols, prefix, src_prefix, cls_node.name)
        for ctx in unit.callables:
            _add_qualified_symbol(symbols, prefix, src_prefix, ctx.node.name)
            _add_qualified_symbol(symbols, prefix, src_prefix, ctx.qualname)
    return frozenset(symbols)


def _check_mandate_and_topology(dossier: EngineeringDossier) -> list[DossierViolation]:
    violations: list[DossierViolation] = []
    if dossier.mandate.requires_elicitation:
        violations.append(
            DossierViolation(
                code=DossierViolationCode.UNRESOLVED_AMBIGUITIES,
                phase=PcePhase.MANDATE,
                message=(
                    f"Mandate '{dossier.mandate.mandate_id}' retains "
                    f"{len(dossier.mandate.open_ambiguities)} unresolved ambiguity item(s)."
                ),
                remediation="Halt at BHV-02 ELICIT and resolve ambiguities via BHV-01 FRAME.",
            )
        )

    graph_report = verify_behavior_graph(dossier.behavior_graph)
    if not graph_report.is_valid:
        first_msg = graph_report.violations[0].message
        violations.append(
            DossierViolation(
                code=DossierViolationCode.INVALID_BEHAVIOR_GRAPH,
                phase=PcePhase.PERMIT,
                message=f"BehaviorGraph '{dossier.behavior_graph.name}' invalid: {first_msg}",
                remediation="Repair BehaviorGraph transitions and Kildall dominance laws.",
            )
        )

    kinds = dossier.behavior_graph.distinct_kinds()
    if BehaviorKind.SYNTHESIZE in kinds and not (
        kinds & {BehaviorKind.SPECIFY, BehaviorKind.INSTRUMENT}
    ):
        violations.append(
            DossierViolation(
                code=DossierViolationCode.MISSING_CONTRACT_STAGE,
                phase=PcePhase.CONTRACT,
                message="Graph executes SYNTHESIZE without SPECIFY or INSTRUMENT contract nodes.",
                remediation="Encode executable schemas/contracts (SPECIFY/INSTRUMENT) first.",
            )
        )
    return violations


def _expected_mandate_symbols(mandate: EngineeringMandate) -> tuple[str, ...]:
    ordered: list[str] = [inv.verification_symbol for inv in mandate.invariants]
    for scope in mandate.write_scopes:
        for sym in scope.contract_symbols:
            if sym not in ordered:
                ordered.append(sym)
    return tuple(ordered)


def _check_symbol_and_scope_grounding(
    dossier: EngineeringDossier,
    compiled_symbols: frozenset[str],
) -> tuple[list[str], list[DossierViolation]]:
    violations: list[DossierViolation] = []
    verified: list[str] = []

    for sym in _expected_mandate_symbols(dossier.mandate):
        if sym in compiled_symbols:
            verified.append(sym)
            continue
        violations.append(
            DossierViolation(
                code=DossierViolationCode.UNBOUND_INVARIANT_SYMBOL,
                phase=PcePhase.CONTRACT,
                message=f"Mandate contract/invariant symbol '{sym}' is absent from compiled AST.",
                remediation=f"Implement executable schema, callable, or test '{sym}' in source.",
            )
        )

    allowed_mutations = set(dossier.mandate.authorized_files()) | set(
        dossier.mandate.authorized_manifest_files()
    )
    unauthorized = sorted(dossier.mutated_files - allowed_mutations)
    if unauthorized:
        violations.append(
            DossierViolation(
                code=DossierViolationCode.UNDECLARED_WRITE_MUTATION,
                phase=PcePhase.CONSTRUCTION,
                message=f"Mutated file(s) outside declared ModuleWriteScopes: {unauthorized}.",
                remediation="Revert out-of-scope edits or amend mandate via FRAME/DECOMPOSE.",
            )
        )
    return verified, violations


def _check_inspection_and_occupancy(
    dossier: EngineeringDossier,
    units: Sequence[SourceUnit],
) -> list[DossierViolation]:
    violations: list[DossierViolation] = []
    uninspected = sorted(
        dossier.permit.bound_code_ids - set(dossier.inspection_report.effective_code_ids)
    )
    if uninspected:
        violations.append(
            DossierViolation(
                code=DossierViolationCode.UNINSPECTED_PERMIT_CODES,
                phase=PcePhase.OCCUPANCY,
                message=f"Permit codes omitted from InspectionReport: {uninspected}.",
                remediation="Run inspect_final() against the full bound CodePermit.",
            )
        )

    if not dossier.inspection_report.is_occupiable:
        violations.append(
            DossierViolation(
                code=DossierViolationCode.INSPECTION_RED_TAG,
                phase=PcePhase.OCCUPANCY,
                message=(
                    f"BuildingInspector issued RED_TAG with "
                    f"{len(dossier.inspection_report.unwaived_violations())} violation(s)."
                ),
                remediation="Execute BHV-20 REVERT, record falsified path, and remediate.",
            )
        )

    expected_digest = compute_units_digest(units)
    cert = dossier.certificate
    if (
        cert is None
        or cert.permit_id != dossier.permit.permit_id
        or cert.artifact_digest != expected_digest
    ):
        violations.append(
            DossierViolation(
                code=DossierViolationCode.INVALID_OCCUPANCY_SEAL,
                phase=PcePhase.OCCUPANCY,
                message="CertificateOfOccupancy is missing or mismatches permit/artifact digest.",
                remediation="Issue fresh CertificateOfOccupancy over current workspace units.",
            )
        )
    return violations


def _resolve_highest_phase(violations: Sequence[DossierViolation]) -> PcePhase:
    if not violations:
        return PcePhase.OCCUPANCY
    earliest_failed = min(violations, key=lambda v: _PHASE_RANK[v.phase]).phase
    return _PREDECESSOR_PHASE[earliest_failed]


@icontract.ensure(lambda result: result.is_occupancy_sealed == (len(result.violations) == 0))
@jezz_callable
def audit_pce_dossier(
    dossier: EngineeringDossier,
    units: Annotated[Sequence[SourceUnit], Field(min_length=1, max_length=100_000)],
) -> DossierAuditReport:
    """Verify all five Permit-and-Contract Engineering (PCE) gates on ``dossier``."""
    compiled = extract_compiled_symbols(units)
    verified_syms, contract_violations = _check_symbol_and_scope_grounding(dossier, compiled)
    all_violations = [
        *_check_mandate_and_topology(dossier),
        *contract_violations,
        *_check_inspection_and_occupancy(dossier, units),
    ]
    return DossierAuditReport(
        mandate_id=dossier.mandate.mandate_id,
        highest_verified_phase=_resolve_highest_phase(all_violations),
        is_occupancy_sealed=(len(all_violations) == 0),
        verified_symbols=tuple(verified_syms),
        violations=tuple(all_violations),
    )
