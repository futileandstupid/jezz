"""Permits, variances, watchdog attestations, and Certificates of Occupancy for Pillar 2."""

import hashlib
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

import icontract
from pydantic import BaseModel, Field, model_validator

from jezz.codes.ast_utils import SourceSpan
from jezz.codes.codebook import CODE_BOOK, codes_for_triggers
from jezz.codes.complexity import (
    CodeThresholdOverrides,
    PermitProfile,
    ResolvedComplexityLimits,
    resolve_complexity_limits,
)
from jezz.codes.taxonomy import CodeSeverity, CodeTrigger
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    BoundedInt,
    BoundedText,
    FiniteFloat,
    IdentifierStr,
    NonNegativeInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
)

type PermitIdStr = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"),
]
type SealDigestStr = Annotated[
    str,
    Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"),
]


class CodeVariance(BaseModel):
    """Authority-signed permit waiving a non-LIFE_SAFETY code on a bounded file/symbol scope."""

    model_config = STRICT_FROZEN_CONFIG

    variance_id: PermitIdStr
    code_id: IdentifierStr
    file_path: IdentifierStr
    symbol: Annotated[str, Field(max_length=256)] = ""
    authorized_by: Annotated[str, Field(min_length=2, max_length=128)]
    justification: Annotated[str, Field(min_length=15, max_length=1024)]
    expires_on: date | None = None

    @model_validator(mode="after")
    def _validate_waivable_code(self) -> Self:
        code = CODE_BOOK.get(self.code_id)
        if code is None:
            msg = f"Unknown BuildingCode '{self.code_id}' in CodeVariance '{self.variance_id}'."
            raise ValueError(msg)
        if not code.variance_permitted:
            msg = (
                f"Code '{self.code_id}' ({code.severity}) is non-waivable; "
                "no CodeVariance may be granted."
            )
            raise ValueError(msg)
        return self

    @jezz_callable
    def covers_span(self, code_id: IdentifierStr, span: SourceSpan) -> bool:
        """Return True iff this variance targets ``code_id`` at ``span`` regardless of expiry."""
        if self.code_id != code_id or self.file_path != span.file_path:
            return False
        return not self.symbol or self.symbol == span.symbol

    @jezz_callable
    def is_expired(self, reference_date: date) -> bool:
        """Return True iff ``expires_on`` is set and ``reference_date`` is strictly after it."""
        return self.expires_on is not None and reference_date > self.expires_on

    @jezz_callable
    def matches(
        self,
        code_id: IdentifierStr,
        span: SourceSpan,
        *,
        reference_date: date | None = None,
    ) -> bool:
        """Return True iff this variance covers ``code_id`` at ``span`` and has not expired."""
        if not self.covers_span(code_id, span):
            return False
        return reference_date is None or not self.is_expired(reference_date)


class CodePermit(BaseModel):
    """Bound schedule of mandatory BuildingCodes issued at BHV-03 BIND_CODES."""

    model_config = STRICT_FROZEN_CONFIG

    permit_id: PermitIdStr
    active_triggers: Annotated[frozenset[CodeTrigger], Field(min_length=1, max_length=5)]
    bound_code_ids: Annotated[frozenset[IdentifierStr], Field(min_length=1, max_length=256)]
    variances: Annotated[tuple[CodeVariance, ...], Field(max_length=256)] = ()
    profile: PermitProfile = PermitProfile.STANDARD
    thresholds: CodeThresholdOverrides = CodeThresholdOverrides()

    @model_validator(mode="after")
    def _enforce_permit_completeness(self) -> Self:
        if CodeTrigger.UNIVERSAL not in self.active_triggers:
            msg = "CodePermit.active_triggers must always include CodeTrigger.UNIVERSAL."
            raise ValueError(msg)

        unknown = self.bound_code_ids - set(CODE_BOOK)
        if unknown:
            msg = f"CodePermit references unknown BuildingCode IDs: {sorted(unknown)}"
            raise ValueError(msg)

        required_ids = {c.code_id for c in codes_for_triggers(self.active_triggers)}
        missing = required_ids - self.bound_code_ids
        if missing:
            msg = f"CodePermit omits mandatory codes for active triggers: {sorted(missing)}"
            raise ValueError(msg)

        seen_variances: set[str] = set()
        for var in self.variances:
            if var.variance_id in seen_variances:
                msg = f"Duplicate variance_id '{var.variance_id}' in permit '{self.permit_id}'."
                raise ValueError(msg)
            seen_variances.add(var.variance_id)
        return self

    @classmethod
    @jezz_callable
    def for_triggers(
        cls,
        permit_id: PermitIdStr,
        triggers: AbstractSet[CodeTrigger] = frozenset(),
        *,
        extra_code_ids: AbstractSet[IdentifierStr] = frozenset(),
        variances: Sequence[CodeVariance] = (),
        profile: PermitProfile = PermitProfile.STANDARD,
        thresholds: CodeThresholdOverrides | None = None,
    ) -> CodePermit:
        """Construct a valid ``CodePermit`` binding all codes mandated by ``triggers``."""
        effective_triggers = frozenset(triggers) | {CodeTrigger.UNIVERSAL}
        mandated = {c.code_id for c in codes_for_triggers(effective_triggers)}
        return CodePermit(
            permit_id=permit_id,
            active_triggers=effective_triggers,
            bound_code_ids=frozenset(mandated | set(extra_code_ids)),
            variances=tuple(variances),
            profile=profile,
            thresholds=thresholds or CodeThresholdOverrides(),
        )

    @classmethod
    @jezz_callable
    def full_book(
        cls,
        permit_id: PermitIdStr = "PERMIT-FULL",
        *,
        variances: Sequence[CodeVariance] = (),
        profile: PermitProfile = PermitProfile.STANDARD,
        thresholds: CodeThresholdOverrides | None = None,
    ) -> CodePermit:
        """Construct a ``CodePermit`` binding every provision in ``CODE_BOOK``."""
        return CodePermit(
            permit_id=permit_id,
            active_triggers=frozenset(CodeTrigger),
            bound_code_ids=frozenset(CODE_BOOK),
            variances=tuple(variances),
            profile=profile,
            thresholds=thresholds or CodeThresholdOverrides(),
        )

    @jezz_callable
    def effective_complexity_limits(self) -> ResolvedComplexityLimits:
        """Return effective JBC-COG-001/002 numeric thresholds for this permit."""
        return resolve_complexity_limits(self.profile, self.thresholds)

    @jezz_callable
    def find_variance(
        self,
        code_id: IdentifierStr,
        span: SourceSpan,
        *,
        reference_date: date | None = None,
    ) -> CodeVariance | None:
        """Return the first active ``CodeVariance`` matching ``(code_id, span)``, if any."""
        for variance in self.variances:
            if variance.matches(code_id, span, reference_date=reference_date):
                return variance
        return None

    @jezz_callable
    def find_expired_variance(
        self,
        code_id: IdentifierStr,
        span: SourceSpan,
        *,
        reference_date: date,
    ) -> CodeVariance | None:
        """Return the first expired ``CodeVariance`` targeting ``(code_id, span)``, if any."""
        for variance in self.variances:
            if variance.covers_span(code_id, span) and variance.is_expired(reference_date):
                return variance
        return None


class GateKind(StrEnum):
    """External verification gate categories attested prior to final occupancy."""

    STATIC_TYPES = "static_types"
    DYNAMIC_SUITE = "dynamic_suite"


class GateAttestation(BaseModel):
    """Empirical execution proof from an external verification tool with watchdog metrics."""

    model_config = STRICT_FROZEN_CONFIG

    gate_kind: GateKind
    tool_name: Annotated[str, Field(min_length=2, max_length=128)]
    passed: bool
    exit_code: BoundedInt
    checks_executed: NonNegativeInt = 0
    duration_ms: Annotated[FiniteFloat, Field(ge=0.0, le=3_600_000.0)] = 1.0
    max_duration_ms: Annotated[FiniteFloat, Field(gt=0.0, le=3_600_000.0)] = 60_000.0
    peak_memory_kb: Annotated[int, Field(ge=0, le=16_777_216)] = 1024
    max_memory_kb: Annotated[int, Field(ge=64, le=16_777_216)] = 524_288
    summary: ShortText

    @model_validator(mode="after")
    def _ensure_consistent_exit_code(self) -> Self:
        if self.passed and self.exit_code != 0:
            msg = f"GateAttestation '{self.tool_name}' cannot pass with exit_code={self.exit_code}."
            raise ValueError(msg)
        return self


class FindingDisposition(StrEnum):
    """Outcome of a single code discrepancy discovered during inspection."""

    VIOLATION = "violation"
    VARIANCE_WAIVED = "variance_waived"


class InspectionFinding(BaseModel):
    """Cited discrepancy against a specific BuildingCode provision."""

    model_config = STRICT_FROZEN_CONFIG

    code_id: IdentifierStr
    severity: CodeSeverity
    disposition: FindingDisposition
    location: SourceSpan
    message: ShortText
    remediation: ShortText
    variance_id: PermitIdStr | None = None

    @model_validator(mode="after")
    def _enforce_variance_pairing(self) -> Self:
        if self.code_id not in CODE_BOOK:
            msg = f"Unknown BuildingCode '{self.code_id}' in InspectionFinding."
            raise ValueError(msg)
        is_waived = self.disposition == FindingDisposition.VARIANCE_WAIVED
        has_variance = self.variance_id is not None
        if is_waived != has_variance:
            msg = "VARIANCE_WAIVED findings must carry a variance_id (and VIOLATION must not)."
            raise ValueError(msg)
        return self


class CodeSectionVerdict(BaseModel):
    """Compliance summary for a single cited BuildingCode section."""

    model_config = STRICT_FROZEN_CONFIG

    code_id: IdentifierStr
    passed: bool
    violation_count: NonNegativeInt
    waived_count: NonNegativeInt
    evidence_summary: ShortText

    @model_validator(mode="after")
    def _validate_pass_consistency(self) -> Self:
        if self.code_id not in CODE_BOOK:
            msg = f"Unknown BuildingCode '{self.code_id}' in CodeSectionVerdict."
            raise ValueError(msg)
        if self.passed != (self.violation_count == 0):
            msg = f"Section {self.code_id} passed={self.passed} contradicts violation_count."
            raise ValueError(msg)
        return self


class OccupancyRuling(StrEnum):
    """Final determination issued by the BuildingInspector."""

    APPROVED = "approved"
    APPROVED_WITH_VARIANCES = "approved_with_variances"
    RED_TAG = "red_tag"


def _expected_ruling(
    section_verdicts: Sequence[CodeSectionVerdict],
) -> OccupancyRuling:
    """Compute the canonical ``OccupancyRuling`` from per-section verdicts."""
    if any(not v.passed for v in section_verdicts):
        return OccupancyRuling.RED_TAG
    if any(v.waived_count > 0 for v in section_verdicts):
        return OccupancyRuling.APPROVED_WITH_VARIANCES
    return OccupancyRuling.APPROVED


class InspectionReport(BaseModel):
    """Complete Building Inspection ledger across all bound and auto-triggered codes."""

    model_config = STRICT_FROZEN_CONFIG

    permit_id: PermitIdStr
    ruling: OccupancyRuling
    auto_detected_triggers: Annotated[frozenset[CodeTrigger], Field(min_length=1, max_length=5)]
    effective_code_ids: Annotated[frozenset[IdentifierStr], Field(min_length=1, max_length=256)]
    section_verdicts: Annotated[tuple[CodeSectionVerdict, ...], Field(min_length=1, max_length=256)]
    findings: Annotated[tuple[InspectionFinding, ...], Field(max_length=1_000_000)] = ()

    @model_validator(mode="after")
    def _validate_report_integrity(self) -> Self:
        verdict_ids = {v.code_id for v in self.section_verdicts}
        if verdict_ids != self.effective_code_ids:
            msg = "section_verdicts must cover effective_code_ids 1-to-1."
            raise ValueError(msg)

        expected = _expected_ruling(self.section_verdicts)
        if self.ruling != expected:
            msg = f"Ruling '{self.ruling}' mismatches computed verdict '{expected}'."
            raise ValueError(msg)
        return self

    @property
    def is_occupiable(self) -> bool:
        """Return True iff zero unwaived violations exist (no RED_TAG)."""
        return self.ruling != OccupancyRuling.RED_TAG

    @jezz_callable
    def unwaived_violations(self) -> tuple[InspectionFinding, ...]:
        """Return all blocking VIOLATION findings."""
        return tuple(f for f in self.findings if f.disposition == FindingDisposition.VIOLATION)

    @jezz_callable
    def to_markdown(
        self,
        *,
        max_findings: Annotated[int, Field(ge=1, le=5_000)] = 200,
    ) -> SourceCodeText:
        """Render a structured Markdown Building Inspection Report."""
        lines = [
            f"# Building Inspection Report — `{self.permit_id}`",
            f"- **Ruling:** `{self.ruling.upper()}`",
            f"- **Codes Inspected:** `{len(self.section_verdicts)}`",
            f"- **Unwaived Violations:** `{len(self.unwaived_violations())}`",
            "",
            "## Section Ledger",
            "| Code | Status | Violations | Waived | Evidence |",
            "| :--- | :--- | :---: | :---: | :--- |",
        ]
        for v in self.section_verdicts:
            status = "PASS" if v.passed else "FAIL"
            lines.append(
                f"| `{v.code_id}` | **{status}** | {v.violation_count} | "
                f"{v.waived_count} | {v.evidence_summary} |"
            )
        if self.findings:
            lines.extend(["", "## Cited Findings"])
            for f in self.findings[:max_findings]:
                loc = f"{f.location.file_path}:{f.location.line}"
                lines.append(
                    f"- **[{f.disposition.upper()}] `{f.code_id}`** (`{loc}`): "
                    f"{f.message} — *Remediation:* {f.remediation}"
                )
            omitted = len(self.findings) - max_findings
            if omitted > 0:
                lines.append(
                    f"- *... and {omitted} additional finding(s) omitted; "
                    "use `--format json` for the complete ledger.*"
                )
        return "\n".join(lines)


class RedTagError(RuntimeError):
    """Raised when attempting to issue a Certificate of Occupancy on a failed inspection."""

    @jezz_callable
    def __init__(self, message: ShortText, report: InspectionReport) -> None:
        super().__init__(message)
        self.report = report


def _build_seal_commitments(
    blueprint_netlist_digest: SealDigestStr | None,
    smt_proof_digest: SealDigestStr | None,
    proved_deductive_count: int,
) -> tuple[BoundedText, ...]:
    items: list[str] = []
    if blueprint_netlist_digest is not None:
        items.append(blueprint_netlist_digest)
    if smt_proof_digest is not None:
        items.append(f"smt:{smt_proof_digest}:{proved_deductive_count}")
    return tuple(items)


@jezz_callable
def compute_certificate_seal(
    permit_id: PermitIdStr,
    ruling: OccupancyRuling,
    cited_code_ids: Annotated[Sequence[IdentifierStr], Field(min_length=1, max_length=256)],
    exercised_variance_ids: Annotated[Sequence[PermitIdStr], Field(max_length=256)],
    artifact_digest: Annotated[str, Field(min_length=8, max_length=128)],
    *,
    extra_commitments: Annotated[Sequence[BoundedText], Field(max_length=8)] = (),
) -> SealDigestStr:
    """Compute canonical SHA-256 inspector seal over occupancy verdict and commitments."""
    parts: list[str] = [
        permit_id,
        ruling.value,
        ",".join(cited_code_ids),
        ",".join(exercised_variance_ids),
        artifact_digest,
        *extra_commitments,
    ]
    payload = "|".join(parts)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CertificateOfOccupancy(BaseModel):
    """Cryptographically sealed compliance certificate emitted at BHV-21 CERTIFY."""

    model_config = STRICT_FROZEN_CONFIG

    permit_id: PermitIdStr
    ruling: OccupancyRuling
    cited_code_ids: Annotated[tuple[IdentifierStr, ...], Field(min_length=1, max_length=256)]
    exercised_variance_ids: Annotated[tuple[PermitIdStr, ...], Field(max_length=256)] = ()
    artifact_digest: Annotated[str, Field(min_length=8, max_length=128)]
    blueprint_netlist_digest: SealDigestStr | None = None
    smt_proof_digest: SealDigestStr | None = None
    proved_deductive_count: NonNegativeInt = 0
    inspector_seal: SealDigestStr

    @model_validator(mode="after")
    def _forbid_red_tag_certificate(self) -> Self:
        if self.ruling == OccupancyRuling.RED_TAG:
            msg = "CertificateOfOccupancy can never be instantiated with RED_TAG ruling."
            raise ValueError(msg)
        return self

    @jezz_callable
    def verify_seal(self) -> bool:
        """Return True iff ``inspector_seal`` matches the recomputed SHA-256 certificate payload."""
        expected = compute_certificate_seal(
            self.permit_id,
            self.ruling,
            self.cited_code_ids,
            self.exercised_variance_ids,
            self.artifact_digest,
            extra_commitments=_build_seal_commitments(
                self.blueprint_netlist_digest,
                self.smt_proof_digest,
                self.proved_deductive_count,
            ),
        )
        return self.inspector_seal == expected


@icontract.require(lambda artifact_digest: len(artifact_digest.strip()) >= 8)
@icontract.ensure(lambda result: result.ruling != OccupancyRuling.RED_TAG)
@jezz_callable
def issue_certificate(
    report: InspectionReport,
    *,
    artifact_digest: Annotated[str, Field(min_length=8, max_length=128)],
    blueprint_netlist_digest: SealDigestStr | None = None,
    smt_proof_digest: SealDigestStr | None = None,
    proved_deductive_count: NonNegativeInt = 0,
) -> CertificateOfOccupancy:
    """Issue a sealed ``CertificateOfOccupancy`` if approved, else raise ``RedTagError``."""
    if not report.is_occupiable:
        failing = [v.code_id for v in report.section_verdicts if not v.passed]
        msg = (
            f"RED TAG on permit '{report.permit_id}': occupancy denied due to violations in "
            f"{failing}."
        )
        raise RedTagError(msg, report)

    cited_codes = tuple(sorted(report.effective_code_ids))
    exercised_variances = tuple(
        sorted({f.variance_id for f in report.findings if f.variance_id is not None})
    )
    seal_value = compute_certificate_seal(
        report.permit_id,
        report.ruling,
        cited_codes,
        exercised_variances,
        artifact_digest,
        extra_commitments=_build_seal_commitments(
            blueprint_netlist_digest,
            smt_proof_digest,
            proved_deductive_count,
        ),
    )
    return CertificateOfOccupancy(
        permit_id=report.permit_id,
        ruling=report.ruling,
        cited_code_ids=cited_codes,
        exercised_variance_ids=exercised_variances,
        artifact_digest=artifact_digest,
        blueprint_netlist_digest=blueprint_netlist_digest,
        smt_proof_digest=smt_proof_digest,
        proved_deductive_count=proved_deductive_count,
        inspector_seal=seal_value,
    )
