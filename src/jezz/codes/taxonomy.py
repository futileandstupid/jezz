"""Pillar 2: Statutory schema, hazard triggers, and verbatim standards lineage for JBC v1.1."""

import re
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import BaseModel, Field, model_validator

from jezz.behaviors.taxonomy import BehaviorKind
from jezz.guards import STRICT_FROZEN_CONFIG

_NORMATIVE_MODAL_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bSHALL(?:\s+NOT)?\b")

ALLOWED_ENFORCEMENT_BEHAVIORS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.VERIFY_STATIC,
        BehaviorKind.VERIFY_DYNAMIC,
        BehaviorKind.CERTIFY,
    }
)


class CodeArticle(StrEnum):
    """Top-level statutory divisions (Articles) of the Jezz Software Building Code (JBC)."""

    DBC = "DBC"  # Article I: Design by Contract, Signature Breakers & Bounded Domains
    FLT = "FLT"  # Article II: Fault Isolation, Termination & Resource Envelopes
    SEC = "SEC"  # Article III: Trust Boundaries, Ingress/Egress & Sink Containment
    COG = "COG"  # Article IV: Complexity, Dispatch Tables & Class Duty Isolation
    OBS = "OBS"  # Article V: Observability, State Hygiene & Determinism
    VRF = "VRF"  # Article VI: Anti-Theatre Verification, Grounding & Watchdog Attestation


class CodeSeverity(StrEnum):
    """Statutory hazard classification for a BuildingCode provision.

    Every severity blocks issuance of a Certificate of Occupancy unless waived by an
    authority-signed CodeVariance; ``LIFE_SAFETY`` provisions forbid variances entirely.
    """

    LIFE_SAFETY = "life_safety"
    STRUCTURAL = "structural"
    WORKMANSHIP = "workmanship"


class CodeTrigger(StrEnum):
    """Occupancy and hazard conditions that bind BuildingCodes to a work scope."""

    UNIVERSAL = "universal"
    CONSTRAINED_DOMAIN = "constrained_domain"
    EXTERNAL_BOUNDARY = "external_boundary"
    SENSITIVE_CONTEXT = "sensitive_context"
    VERIFICATION_SUITE = "verification_suite"


class InspectionMethod(StrEnum):
    """Deterministic verification mechanism used by the BuildingInspector."""

    AST_STATIC = "ast_static"
    MODULE_GRAPH = "module_graph"
    TOKEN_LEXICAL = "token_lexical"
    GATE_ATTESTATION = "gate_attestation"


class CodeOrigin(StrEnum):
    """Provenance distinguishing established human-era standards from net-new agentic codes."""

    ESTABLISHED_STANDARD = "established_standard"
    EXTENDED_HYBRID = "extended_hybrid"
    NET_NEW_AGENTIC = "net_new_agentic"


class StandardBody(StrEnum):
    """External engineering standards bodies and canonical software safety lineages."""

    ISO_IEC_25010 = "ISO/IEC 25010"
    IEEE_1016 = "IEEE 1016"
    IEEE_29148 = "IEEE 29148"
    MEYER_DBC = "Design by Contract (Meyer)"
    NASA_JPL_P10 = "NASA JPL Power of 10"
    MITRE_CWE = "MITRE CWE"
    SEI_CERT = "SEI CERT"
    OWASP_ASVS = "OWASP ASVS"
    OWASP_API_TOP_10 = "OWASP API Security Top 10"
    SONAR_COMPLEXITY = "Sonar Cognitive Complexity"
    PARNAS_MODULARITY = "Parnas Modularity"
    MCCONNELL_DISPATCH = "McConnell Table-Driven Methods"
    CHIDAMBER_KEMERER = "Chidamber & Kemerer OO Metrics"
    HITZ_MONTAZERI_LCOM4 = "Hitz & Montazeri LCOM4"
    JEZZ_AGENTIC = "Jezz Agentic Safety"


class StandardReference(BaseModel):
    """Explicit normative lineage anchor with optional verified verbatim primary-source quote."""

    model_config = STRICT_FROZEN_CONFIG

    body: StandardBody
    clause: Annotated[str, Field(min_length=2, max_length=128)]
    summary: Annotated[str, Field(min_length=10, max_length=512)]
    quote: Annotated[str, Field(min_length=10, max_length=2048)] | None = None
    source_url: Annotated[str, Field(min_length=8, max_length=512)] | None = None


def _validate_origin_references(
    code_id: str,
    origin: CodeOrigin,
    refs: tuple[StandardReference, ...],
) -> None:
    """Verify that standard_refs match the declared CodeOrigin provenance."""
    bodies = {ref.body for ref in refs}
    if origin == CodeOrigin.NET_NEW_AGENTIC and StandardBody.JEZZ_AGENTIC not in bodies:
        msg = f"{code_id} is NET_NEW_AGENTIC and must include a JEZZ_AGENTIC reference."
        raise ValueError(msg)
    if origin != CodeOrigin.NET_NEW_AGENTIC and bodies == {StandardBody.JEZZ_AGENTIC}:
        msg = f"{code_id} ({origin}) must cite at least one external standard body."
        raise ValueError(msg)


class BuildingCode(BaseModel):
    """A single enforceable provision in the Jezz Software Building Code (JBC)."""

    model_config = STRICT_FROZEN_CONFIG

    code_id: Annotated[
        str,
        Field(
            min_length=11,
            max_length=11,
            pattern=r"^JBC-(?:DBC|FLT|SEC|COG|OBS|VRF)-(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})$",
        ),
    ]
    article: CodeArticle
    title: Annotated[str, Field(min_length=10, max_length=160)]
    normative_rule: Annotated[str, Field(min_length=30, max_length=1024)]
    hazard: Annotated[str, Field(min_length=20, max_length=1024)]
    pass_criteria: Annotated[str, Field(min_length=20, max_length=1024)]
    severity: CodeSeverity
    variance_permitted: bool
    triggers: Annotated[frozenset[CodeTrigger], Field(min_length=1, max_length=5)]
    enforced_at: Annotated[frozenset[BehaviorKind], Field(min_length=1, max_length=3)]
    inspection_method: InspectionMethod
    origin: CodeOrigin
    standard_refs: Annotated[tuple[StandardReference, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _enforce_statutory_invariants(self) -> Self:
        article_prefix = self.code_id.split("-")[1]
        if article_prefix != self.article.value:
            msg = f"Code ID '{self.code_id}' prefix mismatches article '{self.article}'."
            raise ValueError(msg)

        if _NORMATIVE_MODAL_PATTERN.search(self.normative_rule) is None:
            msg = f"{self.code_id} normative_rule must use statutory 'SHALL' or 'SHALL NOT'."
            raise ValueError(msg)

        if not (self.enforced_at <= ALLOWED_ENFORCEMENT_BEHAVIORS):
            msg = (
                f"{self.code_id} enforced_at must be a subset of "
                f"{sorted(ALLOWED_ENFORCEMENT_BEHAVIORS)}."
            )
            raise ValueError(msg)

        if self.severity == CodeSeverity.LIFE_SAFETY and self.variance_permitted:
            msg = f"{self.code_id} is LIFE_SAFETY; variances are strictly prohibited."
            raise ValueError(msg)

        _validate_origin_references(self.code_id, self.origin, self.standard_refs)
        return self
