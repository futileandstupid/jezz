"""Pillar 4: Frozen schemas, execution-board bindings, and runtime budgets for Pydantic AI."""

import ast
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from jezz.agents.caching import (
    CoercedPromptCacheMode,
    PromptCacheMode,
    compute_cache_hit_ratio,
    compute_effective_billed_input_tokens,
    compute_input_cost_savings_ratio,
)
from jezz.behaviors.graph import BehaviorGraph, GraphVerificationReport
from jezz.behaviors.runtime import ExecutionStep
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.permits import CertificateOfOccupancy, CodePermit, InspectionReport
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    IdentifierStr,
    NonNegativeInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
)
from jezz.methodology.dossier import DossierAuditReport
from jezz.methodology.mandate import EngineeringMandate, ModuleWriteScope


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
    HALTED_FOR_PLAN_APPROVAL = "halted_for_plan_approval"
    ESCALATED_AFTER_RED_TAG = "escalated_after_red_tag"


class PceWorkflowMode(StrEnum):
    """Execution topology mode enacted by ``PceWorkflowOrchestrator``."""

    GREENFIELD = "greenfield"
    RENOVATE = "renovate"


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


class ScopeDecompositionPlan(BaseModel):
    """Structured output of BHV-09 DECOMPOSE partitioning scopes over the workspace AST graph."""

    model_config = STRICT_FROZEN_CONFIG

    write_scopes: Annotated[
        tuple[ModuleWriteScope, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    decomposition_rationale: ShortText


class EpistemicObservation(BaseModel):
    """Structured output of read-only epistemic sensing (BHV-04 INSPECT_CODE)."""

    model_config = STRICT_FROZEN_CONFIG

    inspected_files: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ]
    discovered_symbols: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ]
    summary: ShortText


class EnvironmentObservation(BaseModel):
    """Structured output of substrate and manifest sensing (BHV-05 INSPECT_ENV)."""

    model_config = STRICT_FROZEN_CONFIG

    python_version: IdentifierStr
    declared_runtime_roots: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    declared_dev_roots: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    missing_dependencies: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    prior_sealed_mandates: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ] = ()
    summary: ShortText


class ConsultationBrief(BaseModel):
    """Structured output of normative reference retrieval (BHV-06 CONSULT)."""

    model_config = STRICT_FROZEN_CONFIG

    citations: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    normative_constraints: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    summary: ShortText


class CuratedMemoryDigest(BaseModel):
    """Structured output of working-memory compaction (BHV-07 CURATE_MEMORY)."""

    model_config = STRICT_FROZEN_CONFIG

    compacted_observations: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32),
    ] = ()
    retained_invariants: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=128),
    ]
    retained_falsified_paths: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=512),
    ] = ()
    architectural_notes: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32),
    ] = ()
    summary: ShortText


class SealedMandateMemoryEntry(BaseModel):
    """Longitudinal workspace ledger entry for a single certified PCE mandate."""

    model_config = STRICT_FROZEN_CONFIG

    mandate_id: IdentifierStr
    title: ShortText
    artifact_digest: IdentifierStr
    invariants: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ] = ()
    contract_symbols: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()


class WorkspaceMemoryLedger(BaseModel):
    """Persistent cross-mandate architectural and negation ledger at ``.jezz/memory.json``."""

    model_config = STRICT_FROZEN_CONFIG

    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    sealed_mandates: Annotated[
        tuple[SealedMandateMemoryEntry, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    architectural_notes: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ] = ()
    falsified_paths: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=512),
    ] = ()


class ProvisionPlan(BaseModel):
    """Structured output of dependency provisioning validation (BHV-13 PROVISION)."""

    model_config = STRICT_FROZEN_CONFIG

    approved_specs: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32),
    ] = ()
    dev_specs: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=32),
    ] = ()
    rationale: ShortText

    @model_validator(mode="after")
    def _require_non_empty_specs(self) -> Self:
        if not self.approved_specs and not self.dev_specs:
            msg = "ProvisionPlan requires at least one approved runtime or dev dependency spec."
            raise ValueError(msg)
        return self


class ProvisionReceipt(BaseModel):
    """Deterministic substrate receipt returned after executing ``ProvisionPort``."""

    model_config = STRICT_FROZEN_CONFIG

    provisioned_specs: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ]
    updated_files: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=16),
    ]
    summary: ShortText


class RemediationHypothesis(BaseModel):
    """Structured output of BHV-08 HYPOTHESIZE diagnosing RED_TAG citations after REVERT."""

    model_config = STRICT_FROZEN_CONFIG

    root_causes: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    cited_code_ids: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    remediation_plan: ShortText


class ContractSpecificationBundle(BaseModel):
    """Structured output of BHV-14 SPECIFY establishing Phase III types and contracts."""

    model_config = STRICT_FROZEN_CONFIG

    contract_patches: Annotated[
        tuple[FilePatchArtifact, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    declared_symbols: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=128),
    ]
    summary: ShortText


class InstrumentationSuiteBundle(BaseModel):
    """Structured output of BHV-15 INSTRUMENT establishing Phase III falsification tests."""

    model_config = STRICT_FROZEN_CONFIG

    test_patches: Annotated[
        tuple[FilePatchArtifact, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    covered_invariant_ids: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=128),
    ]
    summary: ShortText


class ConstructionPatchBundle(BaseModel):
    """Structured output of BHV-16 SYNTHESIZE (or scoped BHV-10 DELEGATE slice)."""

    model_config = STRICT_FROZEN_CONFIG

    implementation_patches: Annotated[
        tuple[FilePatchArtifact, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    summary: ShortText


class RefactorPatchBundle(BaseModel):
    """Structured output of BHV-17 REFACTOR applying semantics-preserving renovations."""

    model_config = STRICT_FROZEN_CONFIG

    refactored_patches: Annotated[
        tuple[FilePatchArtifact, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    preserved_symbols: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=256),
    ]
    summary: ShortText


class AgentInvocationTelemetry(BaseModel):
    """Bounded token, cache, tool-call, and request telemetry for a single Pydantic AI step."""

    model_config = STRICT_FROZEN_CONFIG

    behavior_kind: BehaviorKind
    specialist_role: IdentifierStr
    model_id: IdentifierStr = "unknown"
    requests: NonNegativeInt
    tool_calls: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_read_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0

    @property
    def cache_hit_ratio(self) -> float:
        """Return normalized fraction of ``input_tokens`` served from prompt cache."""
        return compute_cache_hit_ratio(self.input_tokens, self.cache_read_tokens)

    @property
    def effective_billed_input_tokens(self) -> float:
        """Return uncached-equivalent billed input tokens accounting for read/write weights."""
        return compute_effective_billed_input_tokens(
            self.input_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )

    @property
    def input_cost_savings_ratio(self) -> float:
        """Return fractional billed-input cost reduction relative to uncached input tokens."""
        return compute_input_cost_savings_ratio(
            self.input_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )


class ElicitationTicket(BaseModel):
    """Serializable suspension state emitted when BHV-02 ELICIT halts for human answers."""

    model_config = STRICT_FROZEN_CONFIG

    workflow_mode: PceWorkflowMode = PceWorkflowMode.GREENFIELD
    mandate: EngineeringMandate
    original_prompt: BoundedText = ""
    prior_trace: Annotated[
        tuple[ExecutionStep, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=1024),
    ]
    prior_telemetry: Annotated[
        tuple[AgentInvocationTelemetry, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()

    @model_validator(mode="after")
    def _enforce_elicitation_frontier(self) -> Self:
        if not self.mandate.requires_elicitation:
            msg = (
                f"ElicitationTicket for '{self.mandate.mandate_id}' requires non-empty "
                "open_ambiguities."
            )
            raise ValueError(msg)
        if self.prior_trace[-1].kind != BehaviorKind.ELICIT:
            msg = (
                "ElicitationTicket.prior_trace must end at BHV-02 ELICIT so resumption "
                "traverses ELICIT -> FRAME."
            )
            raise ValueError(msg)
        return self


def _coerce_behavior_budget_keys(raw_value: object) -> object:
    if not isinstance(raw_value, dict):
        return raw_value
    coerced: dict[BehaviorKind, object] = {}
    for key, val in raw_value.items():
        bhv = key if isinstance(key, BehaviorKind) else BehaviorKind(str(key))
        coerced[bhv] = val
    return coerced


_BEHAVIOR_BUDGET_KEY_VALIDATOR = BeforeValidator(_coerce_behavior_budget_keys)


class PceRuntimeBudget(BaseModel):
    """Deterministic step, token, cache, and wall-clock ceilings for PCE agent execution."""

    model_config = STRICT_FROZEN_CONFIG

    max_remediation_cycles: Annotated[int, Field(ge=1, le=10)] = 3
    max_elicitation_rounds: Annotated[int, Field(ge=1, le=8)] = 3
    memory_compaction_threshold: Annotated[int, Field(ge=1, le=256)] = 6
    persist_workspace_memory: bool = True
    ratchet_to_scope: bool = False
    prompt_cache_mode: CoercedPromptCacheMode = PromptCacheMode.AUTO
    agent_timeout_sec: Annotated[float, Field(gt=0.0, le=600.0, allow_inf_nan=False)] = 30.0
    request_limit_per_step: Annotated[int, Field(ge=1, le=64)] = 6
    tool_calls_limit_per_step: Annotated[int, Field(ge=0, le=128)] = 12
    total_tokens_limit_per_step: Annotated[int, Field(ge=128, le=1_000_000)] = 65_536
    phase_request_overrides: Annotated[
        dict[BehaviorKind, Annotated[int, Field(ge=1, le=64)]],
        _BEHAVIOR_BUDGET_KEY_VALIDATOR,
        Field(max_length=21),
    ] = Field(default_factory=dict)

    @jezz_callable
    def request_limit_for(
        self,
        kind: BehaviorKind,
    ) -> Annotated[int, Field(ge=1, le=64)]:
        """Return the phase-scoped request limit for ``kind``, falling back to the step default."""
        return self.phase_request_overrides.get(kind, self.request_limit_per_step)

    @classmethod
    @jezz_callable
    def production_defaults(
        cls,
        *,
        agent_timeout_sec: Annotated[float, Field(gt=0.0, le=600.0, allow_inf_nan=False)] = 240.0,
        prompt_cache_mode: PromptCacheMode = PromptCacheMode.AUTO,
        ratchet_to_scope: bool = False,
    ) -> PceRuntimeBudget:
        """Construct phase-scoped production ceilings separating epistemic and synthesis phases."""
        return cls(
            ratchet_to_scope=ratchet_to_scope,
            prompt_cache_mode=prompt_cache_mode,
            agent_timeout_sec=agent_timeout_sec,
            request_limit_per_step=18,
            tool_calls_limit_per_step=64,
            total_tokens_limit_per_step=524_288,
            phase_request_overrides={
                BehaviorKind.SPECIFY: 28,
                BehaviorKind.INSTRUMENT: 36,
                BehaviorKind.SYNTHESIZE: 36,
                BehaviorKind.REFACTOR: 36,
                BehaviorKind.DELEGATE: 36,
            },
        )


class PceWorkflowResult(BaseModel):
    """Final sealed or halted outcome of a Pydantic-Graph PCE runtime execution."""

    model_config = STRICT_FROZEN_CONFIG

    status: PceRunStatus
    workflow_mode: PceWorkflowMode = PceWorkflowMode.GREENFIELD
    mandate: EngineeringMandate
    permit: CodePermit
    induced_graph: BehaviorGraph
    graph_verification: GraphVerificationReport
    inspection_report: InspectionReport | None = None
    certificate: CertificateOfOccupancy | None = None
    dossier_audit: DossierAuditReport | None = None
    elicitation_ticket: ElicitationTicket | None = None
    memory_ledger: WorkspaceMemoryLedger | None = None
    telemetry: Annotated[tuple[AgentInvocationTelemetry, ...], Field(max_length=256)] = ()
    falsified_paths: Annotated[tuple[ShortText, ...], Field(max_length=128)] = ()

    @property
    def total_input_tokens(self) -> int:
        """Return summed ``input_tokens`` across all recorded specialist invocations."""
        return sum(t.input_tokens for t in self.telemetry)

    @property
    def total_output_tokens(self) -> int:
        """Return summed ``output_tokens`` across all recorded specialist invocations."""
        return sum(t.output_tokens for t in self.telemetry)

    @property
    def total_cache_read_tokens(self) -> int:
        """Return summed ``cache_read_tokens`` across all recorded specialist invocations."""
        return sum(t.cache_read_tokens for t in self.telemetry)

    @property
    def total_cache_write_tokens(self) -> int:
        """Return summed ``cache_write_tokens`` across all recorded specialist invocations."""
        return sum(t.cache_write_tokens for t in self.telemetry)

    @property
    def cache_hit_ratio(self) -> float:
        """Return aggregate ``cache_read_tokens / input_tokens`` across the workflow run."""
        return compute_cache_hit_ratio(self.total_input_tokens, self.total_cache_read_tokens)

    @property
    def effective_billed_input_tokens(self) -> float:
        """Return aggregate uncached-equivalent billed input tokens across the workflow run."""
        return compute_effective_billed_input_tokens(
            self.total_input_tokens,
            self.total_cache_read_tokens,
            self.total_cache_write_tokens,
        )
