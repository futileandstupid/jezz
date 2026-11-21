"""Pillar 4: Frozen schemas, execution-board bindings, and runtime budgets for Pydantic AI."""

import ast
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, Field, model_validator

from jezz.behaviors.graph import BehaviorGraph, GraphVerificationReport
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.permits import CertificateOfOccupancy, CodePermit, InspectionReport
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    IdentifierStr,
    NonNegativeInt,
    ShortText,
    SourceCodeText,
)
from jezz.methodology.dossier import DossierAuditReport
from jezz.methodology.mandate import EngineeringMandate


class ExecutionEngineKind(StrEnum):
    """Runtime execution mechanism enacted by an atomic BehaviorKind."""

    PYDANTIC_AI_AGENT = "pydantic_ai_agent"
    DETERMINISTIC_ENGINE = "deterministic_engine"


class ToolsetAccessLevel(StrEnum):
    """Least-privilege workspace capability level granted to a behavior step."""

    NONE = "none"
    READ_ONLY = "read_only"
    SCOPED_WRITE = "scoped_write"


class PceRunStatus(StrEnum):
    """Terminal disposition of a Permit-and-Contract Engineering (PCE) workflow run."""

    OCCUPANCY_CERTIFIED = "occupancy_certified"
    HALTED_FOR_ELICITATION = "halted_for_elicitation"
    ESCALATED_AFTER_RED_TAG = "escalated_after_red_tag"


class BehaviorExecutionBinding(BaseModel):
    """Statutory runtime binding mapping one of the 21 BehaviorKinds to its engine and toolset."""

    model_config = STRICT_FROZEN_CONFIG

    behavior_kind: BehaviorKind
    engine_kind: ExecutionEngineKind
    toolset_access: ToolsetAccessLevel
    specialist_role: IdentifierStr
    rationale: ShortText


class FilePatchArtifact(BaseModel):
    """Syntactically validated Python source artifact targeting a single workspace file."""

    model_config = STRICT_FROZEN_CONFIG

    file_path: IdentifierStr
    source_code: SourceCodeText
    rationale: ShortText

    @model_validator(mode="after")
    def _enforce_valid_python_syntax(self) -> Self:
        if self.file_path.endswith(".py"):
            try:
                ast.parse(self.source_code, filename=self.file_path)
            except SyntaxError as exc:
                msg = (
                    f"Artifact '{self.file_path}' contains invalid Python syntax at "
                    f"line {exc.lineno}: {exc.msg}."
                )
                raise ValueError(msg) from exc
        return self


class FramedMandateProposal(BaseModel):
    """Structured output of BHV-01 FRAME converting raw user intent into an EngineeringMandate."""

    model_config = STRICT_FROZEN_CONFIG

    mandate: EngineeringMandate
    framing_notes: ShortText


class EpistemicObservation(BaseModel):
    """Structured output of read-only epistemic sensing (BHV-04 INSPECT_CODE)."""

    model_config = STRICT_FROZEN_CONFIG

    inspected_files: Annotated[tuple[IdentifierStr, ...], Field(max_length=128)]
    discovered_symbols: Annotated[tuple[IdentifierStr, ...], Field(max_length=256)]
    summary: ShortText


class RemediationHypothesis(BaseModel):
    """Structured output of BHV-08 HYPOTHESIZE diagnosing RED_TAG citations after REVERT."""

    model_config = STRICT_FROZEN_CONFIG

    root_causes: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=32)]
    cited_code_ids: Annotated[tuple[IdentifierStr, ...], Field(min_length=1, max_length=64)]
    remediation_plan: ShortText


class ContractSpecificationBundle(BaseModel):
    """Structured output of BHV-14 SPECIFY establishing Phase III types and contracts."""

    model_config = STRICT_FROZEN_CONFIG

    contract_patches: Annotated[tuple[FilePatchArtifact, ...], Field(min_length=1, max_length=32)]
    declared_symbols: Annotated[tuple[IdentifierStr, ...], Field(min_length=1, max_length=128)]
    summary: ShortText


class InstrumentationSuiteBundle(BaseModel):
    """Structured output of BHV-15 INSTRUMENT establishing Phase III falsification tests."""

    model_config = STRICT_FROZEN_CONFIG

    test_patches: Annotated[tuple[FilePatchArtifact, ...], Field(min_length=1, max_length=32)]
    covered_invariant_ids: Annotated[
        tuple[IdentifierStr, ...],
        Field(min_length=1, max_length=128),
    ]
    summary: ShortText


class ConstructionPatchBundle(BaseModel):
    """Structured output of BHV-16 SYNTHESIZE (or scoped BHV-10 DELEGATE slice)."""

    model_config = STRICT_FROZEN_CONFIG

    implementation_patches: Annotated[
        tuple[FilePatchArtifact, ...],
        Field(min_length=1, max_length=32),
    ]
    summary: ShortText


class AgentInvocationTelemetry(BaseModel):
    """Bounded token, tool-call, and request telemetry for a single Pydantic AI step."""

    model_config = STRICT_FROZEN_CONFIG

    behavior_kind: BehaviorKind
    specialist_role: IdentifierStr
    requests: NonNegativeInt
    tool_calls: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt


class PceRuntimeBudget(BaseModel):
    """Deterministic step, token, and wall-clock ceilings for PCE agent execution."""

    model_config = STRICT_FROZEN_CONFIG

    max_remediation_cycles: Annotated[int, Field(ge=1, le=10)] = 3
    agent_timeout_sec: Annotated[float, Field(gt=0.0, le=600.0, allow_inf_nan=False)] = 30.0
    request_limit_per_step: Annotated[int, Field(ge=1, le=32)] = 6
    tool_calls_limit_per_step: Annotated[int, Field(ge=0, le=64)] = 12
    total_tokens_limit_per_step: Annotated[int, Field(ge=128, le=1_000_000)] = 65_536


class PceWorkflowResult(BaseModel):
    """Final sealed or halted outcome of a Pydantic-Graph PCE runtime execution."""

    model_config = STRICT_FROZEN_CONFIG

    status: PceRunStatus
    mandate: EngineeringMandate
    permit: CodePermit
    induced_graph: BehaviorGraph
    graph_verification: GraphVerificationReport
    inspection_report: InspectionReport | None = None
    certificate: CertificateOfOccupancy | None = None
    dossier_audit: DossierAuditReport | None = None
    telemetry: Annotated[tuple[AgentInvocationTelemetry, ...], Field(max_length=256)] = ()
    falsified_paths: Annotated[tuple[ShortText, ...], Field(max_length=128)] = ()
