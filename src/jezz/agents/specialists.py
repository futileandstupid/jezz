"""Pillar 4: 21-behavior execution board and bounded Pydantic AI specialist agents."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final

import icontract
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.models import Model

from jezz.agents.schemas import (
    AgentInvocationTelemetry,
    BehaviorExecutionBinding,
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    ExecutionEngineKind,
    FilePatchArtifact,
    FramedMandateProposal,
    InstrumentationSuiteBundle,
    PceRuntimeBudget,
    RemediationHypothesis,
    ToolsetAccessLevel,
)
from jezz.agents.workspace import (
    JezzAgentDeps,
    build_readonly_toolset,
    build_scoped_write_toolset,
)
from jezz.behaviors.fibonacci import FIBONACCI_BASIS_CARDINALITY, is_fibonacci
from jezz.behaviors.taxonomy import BEHAVIOR_BASIS, BehaviorKind, WorkspaceEffect
from jezz.codes.ast_utils import SourceUnit, parse_source_unit
from jezz.guards import InstanceGuard, NonEmptyStr, jezz_callable
from jezz.methodology.dossier import extract_compiled_symbols

DETERMINISTIC_BEHAVIOR_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.BIND_CODES,
        BehaviorKind.RECONCILE,
        BehaviorKind.CHECKPOINT,
        BehaviorKind.VERIFY_STATIC,
        BehaviorKind.VERIFY_DYNAMIC,
        BehaviorKind.REVERT,
        BehaviorKind.CERTIFY,
    }
)

BEHAVIOR_EXECUTION_BOARD: Final[Mapping[BehaviorKind, BehaviorExecutionBinding]] = MappingProxyType(
    {
        BehaviorKind.FRAME: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.FRAME,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="mandate_framer",
            rationale="Translates user intent into a typed anti-Agile EngineeringMandate.",
        ),
        BehaviorKind.ELICIT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.ELICIT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="ambiguity_elicitor",
            rationale="Halts construction to request human disambiguation on open ambiguities.",
        ),
        BehaviorKind.BIND_CODES: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.BIND_CODES,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="permit_issuer",
            rationale="Zero-entropy JBC v1.1 CodePermit and hazard-trigger binder.",
        ),
        BehaviorKind.INSPECT_CODE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSPECT_CODE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="code_inspector",
            rationale="Grounds WorldModelState via read-only AST and file inspection tools.",
        ),
        BehaviorKind.INSPECT_ENV: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSPECT_ENV,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="env_inspector",
            rationale="Reads runtime toolchain and manifest metadata without mutation capability.",
        ),
        BehaviorKind.CONSULT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CONSULT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="standards_consultant",
            rationale="Queries primary technical references and standards citations.",
        ),
        BehaviorKind.CURATE_MEMORY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CURATE_MEMORY,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="memory_curator",
            rationale="Compacts grounded observations and negative knowledge in WorldModelState.",
        ),
        BehaviorKind.HYPOTHESIZE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.HYPOTHESIZE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="causal_diagnostician",
            rationale="Diagnoses RED_TAG citations after REVERT to form a remediation plan.",
        ),
        BehaviorKind.DECOMPOSE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.DECOMPOSE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="scope_architect",
            rationale="Partitions work across pairwise-disjoint ModuleWriteScopes.",
        ),
        BehaviorKind.DELEGATE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.DELEGATE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="delegation_coordinator",
            rationale="Dispatches parallel TaskGroup subagents over disjoint ModuleWriteScopes.",
        ),
        BehaviorKind.RECONCILE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.RECONCILE,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="scope_reconciler",
            rationale="Verifies pairwise disjointness of delegated write sets before commit.",
        ),
        BehaviorKind.CHECKPOINT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CHECKPOINT,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="snapshot_keeper",
            rationale="Captures deterministic pre-mutation workspace snapshots.",
        ),
        BehaviorKind.PROVISION: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.PROVISION,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="dependency_provisioner",
            rationale="Updates declared manifest dependencies within authorized scope.",
        ),
        BehaviorKind.SPECIFY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.SPECIFY,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="contract_specifier",
            rationale="Authors Phase III strict Pydantic schemas and @icontract obligations.",
        ),
        BehaviorKind.INSTRUMENT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSTRUMENT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="falsification_instrumenter",
            rationale="Authors Phase III pytest and Hypothesis falsification suites.",
        ),
        BehaviorKind.SYNTHESIZE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.SYNTHESIZE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="code_synthesizer",
            rationale="Synthesizes Phase IV implementation satisfying locked contracts.",
        ),
        BehaviorKind.REFACTOR: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.REFACTOR,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="structural_refactorer",
            rationale="Applies behavior-preserving structural transformations within scope.",
        ),
        BehaviorKind.VERIFY_STATIC: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.VERIFY_STATIC,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="rough_in_inspector",
            rationale="Runs deterministic JBC v1.1 AST, complexity, cohesion, and security gates.",
        ),
        BehaviorKind.VERIFY_DYNAMIC: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.VERIFY_DYNAMIC,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="final_gate_inspector",
            rationale="Executes dynamic tests, typecheck, lint, and runtime budget watchdogs.",
        ),
        BehaviorKind.REVERT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.REVERT,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="rollback_enforcer",
            rationale="Restores clean checkpoint on RED_TAG and records falsified paths.",
        ),
        BehaviorKind.CERTIFY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CERTIFY,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="occupancy_certifier",
            rationale="Issues SHA-256 CertificateOfOccupancy and audits PCE Dossier.",
        ),
    }
)


@icontract.ensure(
    lambda result: is_fibonacci(len(result)) and len(result) == FIBONACCI_BASIS_CARDINALITY
)
@jezz_callable
def validate_execution_board_invariants() -> tuple[BehaviorExecutionBinding, ...]:
    """Verify 21-behavior coverage, write-capability isolation, and zero-entropy gate locks."""
    if set(BEHAVIOR_EXECUTION_BOARD) != set(BehaviorKind):
        msg = "BEHAVIOR_EXECUTION_BOARD must map all 21 BehaviorKinds bijectively."
        raise ValueError(msg)

    for kind, binding in BEHAVIOR_EXECUTION_BOARD.items():
        if binding.behavior_kind != kind:
            msg = f"Board key '{kind}' mismatches binding.behavior_kind '{binding.behavior_kind}'."
            raise ValueError(msg)
        is_mutate = BEHAVIOR_BASIS[kind].workspace_effect == WorkspaceEffect.MUTATE
        has_write = binding.toolset_access == ToolsetAccessLevel.SCOPED_WRITE
        if is_mutate != has_write:
            msg = (
                f"Capability violation on '{kind}': SCOPED_WRITE ({has_write}) must match "
                f"WorkspaceEffect.MUTATE ({is_mutate})."
            )
            raise ValueError(msg)
        if kind in DETERMINISTIC_BEHAVIOR_KINDS and (
            binding.engine_kind != ExecutionEngineKind.DETERMINISTIC_ENGINE
            or binding.toolset_access != ToolsetAccessLevel.NONE
        ):
            msg = f"Gate behavior '{kind}' must run as a toolless DETERMINISTIC_ENGINE."
            raise ValueError(msg)

    return tuple(BEHAVIOR_EXECUTION_BOARD.values())


@dataclass(frozen=True)
class PceModelSuite:
    """Pluggable Pydantic AI models assigned to each generative PCE specialist role."""

    frame_model: Model
    inspect_model: Model
    specify_model: Model
    instrument_model: Model
    synthesize_model: Model
    hypothesize_model: Model


type GuardedModel = Annotated[Model, InstanceGuard(Model)]
type GuardedAgent[OutT] = Annotated[Agent[JezzAgentDeps, OutT], InstanceGuard(Agent)]


@jezz_callable
def uniform_model_suite(
    model: GuardedModel,
) -> Annotated[PceModelSuite, InstanceGuard(PceModelSuite)]:
    """Bind a single Pydantic AI ``Model`` across all generative PCE specialist roles."""
    return PceModelSuite(
        frame_model=model,
        inspect_model=model,
        specify_model=model,
        instrument_model=model,
        synthesize_model=model,
        hypothesize_model=model,
    )


def _enforce_patch_scope_and_parse(
    patches: Sequence[FilePatchArtifact],
    allowed_files: frozenset[str],
) -> tuple[SourceUnit, ...]:
    units: list[SourceUnit] = []
    for patch in patches:
        if patch.file_path not in allowed_files:
            msg = (
                f"Patch target '{patch.file_path}' is outside authorized write scope "
                f"{sorted(allowed_files)}."
            )
            raise ModelRetry(msg)
        units.append(parse_source_unit(patch.file_path, patch.source_code))
    return tuple(units)


def _parse_and_rough_in_check(
    deps: JezzAgentDeps,
    patches: Sequence[FilePatchArtifact],
    stage_label: str,
) -> tuple[SourceUnit, ...]:
    units = _enforce_patch_scope_and_parse(patches, deps.sandbox.authorized_files)
    report = deps.inspector.inspect_rough_in(units, permit=deps.permit)
    if not report.is_occupiable:
        cited = ", ".join(f"{f.code_id}:{f.message}" for f in report.unwaived_violations()[:5])
        raise ModelRetry(f"{stage_label} failed JBC rough-in: {cited}")
    return units


def _validate_specify_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: ContractSpecificationBundle,
) -> ContractSpecificationBundle:
    units = _parse_and_rough_in_check(
        ctx.deps,
        bundle.contract_patches,
        "Phase III contracts",
    )
    compiled = extract_compiled_symbols(units)
    missing_declared = [sym for sym in bundle.declared_symbols if sym not in compiled]
    if missing_declared:
        raise ModelRetry(
            f"Contract patches omit symbols listed in declared_symbols: {missing_declared}."
        )
    return bundle


def _validate_instrument_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: InstrumentationSuiteBundle,
) -> InstrumentationSuiteBundle:
    for patch in bundle.test_patches:
        if not patch.file_path.startswith("tests/"):
            raise ModelRetry(f"Instrumentation file '{patch.file_path}' must reside under tests/.")
    _parse_and_rough_in_check(
        ctx.deps,
        bundle.test_patches,
        "Phase III falsification suite",
    )
    return bundle


def _combined_workspace_symbols(
    deps: JezzAgentDeps,
    patch_units: Sequence[SourceUnit],
) -> frozenset[str]:
    overridden = {u.file_path for u in patch_units}
    existing = [u for u in deps.sandbox.load_units() if u.file_path not in overridden]
    return extract_compiled_symbols((*existing, *patch_units))


def _validate_synthesize_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: ConstructionPatchBundle,
) -> ConstructionPatchBundle:
    allowed = (
        ctx.deps.active_scope.owned_files
        if ctx.deps.active_scope is not None
        else ctx.deps.sandbox.authorized_files
    )
    patch_units = _enforce_patch_scope_and_parse(bundle.implementation_patches, allowed)
    if ctx.deps.mandate is not None:
        compiled = _combined_workspace_symbols(ctx.deps, patch_units)
        expected = (
            list(ctx.deps.active_scope.contract_symbols)
            if ctx.deps.active_scope is not None
            else [
                *[inv.verification_symbol for inv in ctx.deps.mandate.invariants],
                *[sym for s in ctx.deps.mandate.write_scopes for sym in s.contract_symbols],
            ]
        )
        missing = [sym for sym in expected if sym not in compiled]
        if missing:
            raise ModelRetry(f"Implementation omits mandated symbol(s): {missing}.")
    return bundle


@jezz_callable
def build_frame_agent(model: GuardedModel) -> GuardedAgent[FramedMandateProposal]:
    """Build BHV-01 FRAME specialist producing typed anti-Agile EngineeringMandates."""
    return Agent[JezzAgentDeps, FramedMandateProposal](
        model,
        output_type=FramedMandateProposal,
        deps_type=JezzAgentDeps,
        name="jezz_mandate_framer",
        retries=2,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "Convert the engineering request into a concise EngineeringMandate. "
            "Never emit Agile user stories or Markdown checkboxes; populate open_ambiguities "
            "whenever domain constraints remain under-determined."
        ),
    )


@jezz_callable
def build_inspect_agent(model: GuardedModel) -> GuardedAgent[EpistemicObservation]:
    """Build BHV-04 INSPECT_CODE read-only epistemic specialist."""
    return Agent[JezzAgentDeps, EpistemicObservation](
        model,
        output_type=EpistemicObservation,
        deps_type=JezzAgentDeps,
        name="jezz_code_inspector",
        retries=2,
        toolsets=(build_readonly_toolset(),),
        instructions="Inspect workspace files read-only and summarize grounded symbols.",
    )


@jezz_callable
def build_specify_agent(model: GuardedModel) -> GuardedAgent[ContractSpecificationBundle]:
    """Build BHV-14 SPECIFY specialist with @output_validator JBC rough-in breaker."""
    agent = Agent[JezzAgentDeps, ContractSpecificationBundle](
        model,
        output_type=ContractSpecificationBundle,
        deps_type=JezzAgentDeps,
        name="jezz_contract_specifier",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=(
            "Author Phase III strict Pydantic schemas, bounded Annotated types, @jezz_callable "
            "signatures, and @icontract obligations satisfying all JBC v1.1 building codes."
        ),
    )
    agent.output_validator(_validate_specify_output)
    return agent


@jezz_callable
def build_instrument_agent(model: GuardedModel) -> GuardedAgent[InstrumentationSuiteBundle]:
    """Build BHV-15 INSTRUMENT specialist enforcing non-tautological falsification suites."""
    agent = Agent[JezzAgentDeps, InstrumentationSuiteBundle](
        model,
        output_type=InstrumentationSuiteBundle,
        deps_type=JezzAgentDeps,
        name="jezz_falsification_instrumenter",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=(
            "Author pytest and Hypothesis falsification tests bound to Mandate invariants. "
            "Never emit assert True theatre or gate suppression comments."
        ),
    )
    agent.output_validator(_validate_instrument_output)
    return agent


@jezz_callable
def build_synthesize_agent(model: GuardedModel) -> GuardedAgent[ConstructionPatchBundle]:
    """Build BHV-16 SYNTHESIZE specialist constrained to authorized ModuleWriteScopes."""
    agent = Agent[JezzAgentDeps, ConstructionPatchBundle](
        model,
        output_type=ConstructionPatchBundle,
        deps_type=JezzAgentDeps,
        name="jezz_code_synthesizer",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=(
            "Synthesize Phase IV implementation within your authorized ModuleWriteScope "
            "without weakening Phase III contracts or violating JBC v1.1."
        ),
    )
    agent.output_validator(_validate_synthesize_output)
    return agent


@jezz_callable
def build_hypothesize_agent(model: GuardedModel) -> GuardedAgent[RemediationHypothesis]:
    """Build BHV-08 HYPOTHESIZE diagnostician analyzing RED_TAG findings post-REVERT."""
    return Agent[JezzAgentDeps, RemediationHypothesis](
        model,
        output_type=RemediationHypothesis,
        deps_type=JezzAgentDeps,
        name="jezz_causal_diagnostician",
        retries=2,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "Diagnose the cited JBC RED_TAG findings and formulate a concrete structural "
            "remediation plan before re-entering construction."
        ),
    )


@jezz_callable
async def run_bounded_agent[OutputT](
    agent: GuardedAgent[OutputT],
    *,
    behavior_kind: BehaviorKind,
    prompt: NonEmptyStr,
    deps: Annotated[JezzAgentDeps, InstanceGuard(JezzAgentDeps)],
    budget: PceRuntimeBudget,
) -> tuple[Annotated[OutputT, InstanceGuard(object)], AgentInvocationTelemetry]:
    """Execute ``agent`` under wall-clock timeout and UsageLimits token/step breakers."""
    limits = UsageLimits(
        request_limit=budget.request_limit_per_step,
        tool_calls_limit=budget.tool_calls_limit_per_step,
        total_tokens_limit=budget.total_tokens_limit_per_step,
    )
    async with asyncio.timeout(budget.agent_timeout_sec):
        result = await agent.run(prompt, deps=deps, usage_limits=limits)

    usage = result.usage
    binding = BEHAVIOR_EXECUTION_BOARD[behavior_kind]
    telemetry = AgentInvocationTelemetry(
        behavior_kind=behavior_kind,
        specialist_role=binding.specialist_role,
        requests=max(0, usage.requests),
        tool_calls=max(0, usage.tool_calls),
        input_tokens=max(0, usage.input_tokens),
        output_tokens=max(0, usage.output_tokens),
    )
    return result.output, telemetry
