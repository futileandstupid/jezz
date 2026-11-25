"""Pillar 3 (PCE): EngineeringDossier and deterministic 5-phase polyglot occupancy auditor."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final

import icontract
from pydantic import BaseModel, Field

from jezz.behaviors.graph import BehaviorGraph, verify_behavior_graph
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.blueprint import BlueprintGraph, extract_blueprint
from jezz.codes.blueprint_lvs import verify_blueprint_lvs
from jezz.codes.inspector import compute_units_digest
from jezz.codes.permits import CertificateOfOccupancy, CodePermit, InspectionReport
from jezz.codes.polyglot_ir import InspectedUnit, PolyglotUnit
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
    BLUEPRINT_LVS_MISMATCH = "blueprint_lvs_mismatch"


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
    ratchet_scoped: bool = False
    target_blueprint: BlueprintGraph | None = None
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
    symbols.add(name[:256])
    rust_name = name.replace(".", "::")
    symbols.add(rust_name[:256])
    if prefix:
        dot_full = f"{prefix}{name}"
        symbols.add(dot_full[:256])
        symbols.add(dot_full.replace(".", "::")[:256])
    if src_prefix:
        symbols.add(f"{src_prefix}{name}"[:256])


def _extract_polyglot_unit_symbols(unit: PolyglotUnit, symbols: set[str]) -> None:
    norm_mod = unit.module_name.replace("/", ".")
    prefix = f"{norm_mod}." if norm_mod else ""
    leaf_mod = norm_mod.rsplit(".", maxsplit=1)[-1] if "." in norm_mod else ""
    leaf_prefix = f"{leaf_mod}." if leaf_mod else ""
    src_prefix = f"src.{prefix}" if (prefix and unit.file_path.startswith("src/")) else ""
    for decl in unit.types:
        _add_qualified_symbol(symbols, prefix, src_prefix, decl.name)
        if leaf_prefix:
            _add_qualified_symbol(symbols, leaf_prefix, "", decl.name)
    for fn in unit.callables:
        _add_qualified_symbol(symbols, prefix, src_prefix, fn.name)
        _add_qualified_symbol(symbols, prefix, src_prefix, fn.qualname)
        if leaf_prefix:
            _add_qualified_symbol(symbols, leaf_prefix, "", fn.name)
            _add_qualified_symbol(symbols, leaf_prefix, "", fn.qualname)


@jezz_callable
def extract_compiled_symbols(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
) -> frozenset[IdentifierStr]:
    """Extract all compiled type and callable symbols across Python, TS, Rust, and Go ``units``."""
    symbols: set[str] = set()
    for unit in units:
        if isinstance(unit, PolyglotUnit):
            _extract_polyglot_unit_symbols(unit, symbols)
            continue
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
        if sym in compiled_symbols or sym.replace("::", ".") in compiled_symbols:
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


def _check_blueprint_seal_and_lvs(
    dossier: EngineeringDossier,
    digest_units: Sequence[InspectedUnit],
) -> list[DossierViolation]:
    violations: list[DossierViolation] = []
    cert = dossier.certificate
    if cert is not None and cert.blueprint_netlist_digest is not None:
        actual_netlist = extract_blueprint(digest_units, profile=dossier.permit.profile)
        if cert.blueprint_netlist_digest != actual_netlist.netlist_digest:
            violations.append(
                DossierViolation(
                    code=DossierViolationCode.INVALID_OCCUPANCY_SEAL,
                    phase=PcePhase.OCCUPANCY,
                    message=(
                        "CertificateOfOccupancy blueprint_netlist_digest mismatches physical "
                        "boundary netlist."
                    ),
                    remediation="Re-issue CertificateOfOccupancy over current blueprint netlist.",
                )
            )
    if dossier.target_blueprint is not None:
        pinned_ids = {ip.invariant_id for ip in dossier.target_blueprint.invariant_pins}
        bindings = tuple(
            (inv.invariant_id, inv.verification_symbol)
            for inv in dossier.mandate.invariants
            if inv.invariant_id in pinned_ids
        )
        actual_bp = extract_blueprint(
            digest_units,
            profile=dossier.permit.profile,
            invariant_bindings=bindings,
        )
        lvs = verify_blueprint_lvs(actual_bp, dossier.target_blueprint)
        if not lvs.is_lvs_clean:
            first = lvs.defects[0]
            violations.append(
                DossierViolation(
                    code=DossierViolationCode.BLUEPRINT_LVS_MISMATCH,
                    phase=PcePhase.OCCUPANCY,
                    message=f"Blueprint LVS {first.kind} ({first.layer}): {first.message}",
                    remediation=first.remediation,
                )
            )
    return violations


def _check_inspection_and_occupancy(
    dossier: EngineeringDossier,
    units: Sequence[InspectedUnit],
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

    sealed_paths = set(dossier.mandate.authorized_files()) | set(dossier.mutated_files)
    digest_units = (
        tuple(u for u in units if u.file_path in sealed_paths)
        if dossier.ratchet_scoped
        else tuple(units)
    )
    expected_digest = compute_units_digest(digest_units) if digest_units else ""
    cert = dossier.certificate
    if (
        cert is None
        or cert.permit_id != dossier.permit.permit_id
        or cert.artifact_digest != expected_digest
        or not cert.verify_seal()
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
    violations.extend(_check_blueprint_seal_and_lvs(dossier, digest_units))
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
    units: Annotated[Sequence[InspectedUnit], Field(min_length=1, max_length=100_000)],
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
