"""Pillar 4: Typed pydantic-graph AtomicBehaviorGraphNodes enacting all 21 PCE behaviors."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, ClassVar, Final

import icontract
from pydantic_graph import End, GraphRunContext

from jezz.agents.memory import (
    apply_curated_digest_to_state,
    build_consultation_reference_pack,
    format_memory_prompt_context,
    load_workspace_memory,
    record_certified_mandate_memory,
    save_workspace_memory,
)
from jezz.agents.nodes_context import (
    IMPL_CHECKPOINT_ID,
    active_languages,
    apply_patches_to_state,
    assemble_halted_result,
    build_deterministic_env_observation,
    build_frame_prompt,
    format_baseline_red_tags,
    format_deps_prompt,
    frame_step_id,
    has_authorized_tests,
    implementation_scopes,
    make_agent_deps,
    partition_deps_prompt,
    primary_language,
    record_pce_step,
    require_mandate,
    try_in_process_elicitation,
)
from jezz.agents.nodes_context import (
    ElicitationCallback as ElicitationCallback,
)
from jezz.agents.nodes_context import (
    PceGraphDeps as PceGraphDeps,
)
from jezz.agents.nodes_context import (
    PceInteractionHooks as PceInteractionHooks,
)
from jezz.agents.nodes_context import (
    ProgressCallback as ProgressCallback,
)
from jezz.agents.nodes_exec import (
    active_ratchet_scope,
    capture_failed_src_snapshot,
    execute_delegation_waves,
    execute_instrument_step,
    is_test_only_static_red_tag,
    maybe_refine_scopes_via_decompose,
    refactor_baseline_suffix,
    refresh_local_workspace_roots,
    run_memory_compaction,
    summarize_red_tag_findings,
    verify_and_flatten_delegated_patches,
)
from jezz.agents.provision import (
    compute_missing_dependencies,
    refresh_inspector_after_provision,
)
from jezz.agents.schemas import (
    ElicitationTicket,
    PceRunStatus,
    PceWorkflowMode,
    PceWorkflowResult,
)
from jezz.agents.specialists import (
    build_consult_agent,
    build_frame_agent,
    build_hypothesize_agent,
    build_inspect_agent,
    build_inspect_env_agent,
    build_provision_agent,
    build_refactor_agent,
    build_specify_agent,
    build_synthesize_agent,
    run_bounded_agent,
)
from jezz.agents.workspace import WorkspaceSandbox
from jezz.behaviors.fibonacci import FIBONACCI_BASIS_CARDINALITY, is_fibonacci
from jezz.behaviors.graph import verify_behavior_graph
from jezz.behaviors.runtime import (
    AgentExecutionState,
    AtomicBehaviorGraphNode,
    trace_to_behavior_graph,
)
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.ast_graph import build_workspace_ast_graph
from jezz.codes.inspector import (
    bind_permit_to_state,
    certify_occupancy_in_state,
    compute_units_digest,
    record_inspection_to_state,
)
from jezz.codes.ratchet import (
    DifferentialScope,
    inspect_differential_final,
    inspect_differential_rough_in,
)
from jezz.guards import BudgetExceededError, InstanceGuard, jezz_callable
from jezz.methodology.dossier import EngineeringDossier, audit_pce_dossier

type RuntimeNodeClass = Annotated[
    type[AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]],
    InstanceGuard(type),
]


@dataclass
class FrameNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase I (BHV-01 FRAME): Establish typed EngineeringMandate and check ambiguities."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.FRAME

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ElicitNode | BindCodesNode:
        if ctx.deps.mandate is None:
            proposal, tel = await run_bounded_agent(
                build_frame_agent(
                    ctx.deps.models.frame_model,
                    languages=active_languages(ctx.deps),
                ),
                behavior_kind=BehaviorKind.FRAME,
                prompt=build_frame_prompt(ctx.deps),
                deps=make_agent_deps(ctx.deps),
                budget=ctx.deps.budget,
            )
            ctx.deps.telemetry.append(tel)
            ctx.deps.mandate = proposal.mandate
            ctx.deps.mandate_was_framed = True
            ctx.deps.prior_draft_mandate = None
            ctx.deps.clarifications = ()

        mandate = require_mandate(ctx.deps)
        ctx.deps.sandbox = WorkspaceSandbox(
            ctx.deps.repo_root,
            mandate.authorized_files(),
            authorized_manifests=mandate.authorized_manifest_files(),
        )
        ctx.state.problem.objective = mandate.objective
        ctx.state.problem.ambiguities = list(mandate.open_ambiguities)
        step_id = frame_step_id(ctx.state.trace)
        record_pce_step(self, ctx, step_id, f"Framed mandate {mandate.mandate_id}")

        if mandate.requires_elicitation:
            return ElicitNode(reason="Open domain ambiguities require human resolution.")
        return BindCodesNode()


@dataclass
class ElicitNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Authority Gate (BHV-02 ELICIT): Re-enter FRAME with answers or halt with ticket."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.ELICIT
    reason: str
    escalated_red_tag: bool = False
    awaiting_plan_approval: bool = False

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> FrameNode | End[PceWorkflowResult]:
        elicit_idx = sum(1 for step in ctx.state.trace if step.kind == BehaviorKind.ELICIT) + 1
        step_id = "elicit" if elicit_idx == 1 else f"elicit_{elicit_idx}"
        record_pce_step(self, ctx, step_id, self.reason[:240])
        if self.escalated_red_tag:
            return End(
                assemble_halted_result(ctx.state, ctx.deps, PceRunStatus.ESCALATED_AFTER_RED_TAG)
            )
        if self.awaiting_plan_approval:
            return End(
                assemble_halted_result(
                    ctx.state,
                    ctx.deps,
                    PceRunStatus.HALTED_FOR_PLAN_APPROVAL,
                )
            )
        if try_in_process_elicitation(ctx.deps):
            return FrameNode()
        ticket = ElicitationTicket(
            workflow_mode=ctx.deps.workflow_mode,
            mandate=require_mandate(ctx.deps),
            original_prompt=ctx.deps.task_prompt,
            prior_trace=tuple(ctx.state.trace),
            prior_telemetry=tuple(ctx.deps.telemetry),
        )
        return End(
            assemble_halted_result(
                ctx.state,
                ctx.deps,
                PceRunStatus.HALTED_FOR_ELICITATION,
                ticket=ticket,
            )
        )


@dataclass
class BindCodesNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase II (BHV-03 BIND_CODES): Lock JBC v1.2 CodePermit into execution state."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.BIND_CODES

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InspectEnvNode:
        bind_permit_to_state(ctx.state, ctx.deps.permit)
        record_pce_step(
            self,
            ctx,
            "bind_codes",
            f"Bound {len(ctx.deps.permit.bound_code_ids)} JBC code provisions",
        )
        return InspectEnvNode()


@dataclass
class InspectEnvNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Substrate Grounding (BHV-05 INSPECT_ENV): Sense toolchain, manifests, and memory ledger."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.INSPECT_ENV

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InspectCodeNode:
        mandate = require_mandate(ctx.deps)
        ctx.deps.memory_ledger = load_workspace_memory(ctx.deps.repo_root)
        ctx.deps.memory_context = format_memory_prompt_context(ctx.deps.memory_ledger)
        missing = compute_missing_dependencies(
            ctx.deps.inspector.manifest,
            mandate.required_dependencies,
            polyglot_manifest=ctx.deps.inspector.polyglot_manifest,
            language=primary_language(ctx.deps),
        )
        baseline = build_deterministic_env_observation(ctx.deps, missing)
        if mandate.required_dependencies:
            active_model = ctx.deps.models.inspect_env_model or ctx.deps.models.inspect_model
            env_prompt = (
                f"Mandate {mandate.mandate_id} Required Dependencies: "
                f"{[d.pep508_spec() for d in mandate.required_dependencies]}\n"
                f"Substrate Snapshot: {baseline.model_dump_json()}"
            )[:60_000]
            env_obs, tel = await run_bounded_agent(
                build_inspect_env_agent(active_model),
                behavior_kind=BehaviorKind.INSPECT_ENV,
                prompt=env_prompt,
                deps=make_agent_deps(ctx.deps),
                budget=ctx.deps.budget,
            )
            ctx.deps.telemetry.append(tel)
        else:
            env_obs = baseline
        ctx.state.world_model.observations.append(env_obs.summary)
        record_pce_step(self, ctx, "inspect_env", env_obs.summary[:240])
        return InspectCodeNode()


@dataclass
class InspectCodeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Epistemic Grounding (BHV-04 INSPECT_CODE): Read-only workspace observation."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.INSPECT_CODE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ConsultNode | DecomposeNode:
        mandate = require_mandate(ctx.deps)
        units = ctx.deps.sandbox.load_units()
        ast_graph = build_workspace_ast_graph(units)
        ctx.deps.ast_graph_brief = ast_graph.to_compact_brief(max_modules=32)
        if ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE:
            rough = (
                inspect_differential_rough_in(
                    ctx.deps.inspector,
                    units,
                    DifferentialScope(changed_paths=frozenset(mandate.authorized_files())),
                    permit=ctx.deps.permit,
                ).report
                if ctx.deps.budget.ratchet_to_scope
                else ctx.deps.inspector.inspect_rough_in(units, permit=ctx.deps.permit)
            )
            ctx.deps.baseline_red_tag_summary = format_baseline_red_tags(rough)
            ctx.state.world_model.observations.append(ctx.deps.baseline_red_tag_summary[:1000])
        raw_inspect_prompt = f"{format_deps_prompt(ctx.deps, mandate)}\n{ctx.deps.ast_graph_brief}"
        inspect_prompt = raw_inspect_prompt[:60_000]
        obs, tel = await run_bounded_agent(
            build_inspect_agent(ctx.deps.models.inspect_model),
            behavior_kind=BehaviorKind.INSPECT_CODE,
            prompt=inspect_prompt,
            deps=make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        ctx.state.world_model.observations.append(obs.summary)
        record_pce_step(self, ctx, "inspect_code", obs.summary[:240])
        if mandate.consultation_targets:
            return ConsultNode()
        return DecomposeNode()


@dataclass
class ConsultNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Normative Grounding (BHV-06 CONSULT): Anchor standards, JBC clauses, and external APIs."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CONSULT

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> DecomposeNode:
        mandate = require_mandate(ctx.deps)
        ref_pack = build_consultation_reference_pack(
            ctx.deps.repo_root,
            mandate.consultation_targets,
        )
        prompt = (
            f"Mandate {mandate.mandate_id}: {mandate.objective}\n"
            f"Consultation Targets: {list(mandate.consultation_targets)}\n\n"
            f"Resolved Reference Pack:\n{ref_pack}"
        )[:60_000]
        active_model = ctx.deps.models.consult_model or ctx.deps.models.inspect_model
        brief, tel = await run_bounded_agent(
            build_consult_agent(active_model),
            behavior_kind=BehaviorKind.CONSULT,
            prompt=prompt,
            deps=make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        ctx.state.world_model.external_citations.extend(brief.citations)
        ctx.state.world_model.observations.append(brief.summary)
        constraints = "\n".join(f"- {item}" for item in brief.normative_constraints)
        ctx.deps.consultation_context = f"\nNormative Consultation Constraints:\n{constraints}"
        record_pce_step(self, ctx, "consult", brief.summary[:240])
        return DecomposeNode()


@dataclass
class DecomposeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Topology Partitioning (BHV-09 DECOMPOSE): Lock disjoint ModuleWriteScopes and DAG waves."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.DECOMPOSE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ElicitNode | CheckpointContractNode | CheckpointImplNode:
        mandate = await maybe_refine_scopes_via_decompose(ctx, require_mandate(ctx.deps))
        waves = mandate.execution_waves()
        ctx.state.problem.subtasks = [
            f"w{w_idx}:{s.scope_id}(deps={sorted(s.depends_on)}):{sorted(s.owned_files)}"
            for w_idx, wave in enumerate(waves)
            for s in wave
        ]
        record_pce_step(
            self,
            ctx,
            "decompose",
            f"Partitioned {len(mandate.write_scopes)} scope(s) across {len(waves)} wave(s)",
        )
        if ctx.deps.plan_only:
            return ElicitNode(
                reason="EngineeringMandate and write-scope plan ready for human sign-off.",
                awaiting_plan_approval=True,
            )
        if (
            ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE
            and not has_authorized_tests(mandate)
            and not mandate.required_dependencies
        ):
            return CheckpointImplNode()
        return CheckpointContractNode()


@dataclass
class CheckpointContractNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Pre-Contract Snapshot (BHV-12 CHECKPOINT): Capture clean baseline before Phase III."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CHECKPOINT

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ProvisionNode | SpecifyNode | InstrumentNode:
        mandate = require_mandate(ctx.deps)
        ckpt = ctx.deps.sandbox.create_checkpoint("checkpoint_contract")
        ctx.state.workspace.checkpoints.append(ckpt)
        record_pce_step(self, ctx, "checkpoint_contract", "Captured pre-contract snapshot")
        if mandate.required_dependencies:
            return ProvisionNode()
        if ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE:
            return InstrumentNode()
        return SpecifyNode()


@dataclass
class ProvisionNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Substrate Mutation (BHV-13 PROVISION): Provision declared dependencies and reload JBC."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.PROVISION

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> SpecifyNode | InstrumentNode | CheckpointImplNode:
        mandate = require_mandate(ctx.deps)
        active_model = ctx.deps.models.provision_model or ctx.deps.models.synthesize_model
        prov_prompt = (
            f"Mandate {mandate.mandate_id}: {mandate.objective}\n"
            f"Declared required_dependencies: "
            f"{[d.model_dump(mode='json') for d in mandate.required_dependencies]}"
        )[:60_000]
        _, tel = await run_bounded_agent(
            build_provision_agent(active_model),
            behavior_kind=BehaviorKind.PROVISION,
            prompt=prov_prompt,
            deps=make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        receipt = ctx.deps.provision_runner.provision_dependencies(
            ctx.deps.sandbox,
            mandate.required_dependencies,
        )
        ctx.state.workspace.modified_artifacts.update(receipt.updated_files)
        ctx.deps.inspector = refresh_inspector_after_provision(
            ctx.deps.repo_root,
            ctx.deps.inspector,
            mandate.required_dependencies,
            language=primary_language(ctx.deps),
        )
        record_pce_step(self, ctx, "provision", receipt.summary[:240])
        if ctx.deps.workflow_mode == PceWorkflowMode.GREENFIELD:
            return SpecifyNode()
        if has_authorized_tests(mandate):
            return InstrumentNode()
        return CheckpointImplNode()


@dataclass
class SpecifyNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase III (BHV-14 SPECIFY): Emit compiled schemas and @icontract obligations."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.SPECIFY

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InstrumentNode:
        mandate = require_mandate(ctx.deps)
        bundle, tel = await run_bounded_agent(
            build_specify_agent(
                ctx.deps.models.specify_model,
                languages=active_languages(ctx.deps),
            ),
            behavior_kind=BehaviorKind.SPECIFY,
            prompt=format_deps_prompt(ctx.deps, mandate),
            deps=make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.contract_patches)
        refresh_local_workspace_roots(ctx.deps)
        record_pce_step(self, ctx, "specify", bundle.summary[:240])
        return InstrumentNode()


@dataclass
class InstrumentNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase III (BHV-15 INSTRUMENT): Emit pytest and Hypothesis falsification suites."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.INSTRUMENT

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> CheckpointImplNode:
        mandate = require_mandate(ctx.deps)
        test_patches, summary = await execute_instrument_step(ctx.deps, mandate)
        ctx.deps.remediation_guidance = ""
        apply_patches_to_state(ctx.state, ctx.deps.sandbox, test_patches)
        record_pce_step(self, ctx, "instrument", summary[:240])
        return CheckpointImplNode()


@dataclass
class CheckpointImplNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Pre-Construction Snapshot (BHV-12 CHECKPOINT): Lock contracts before implementation."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CHECKPOINT

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> SynthesizeNode | RefactorNode | DelegateNode:
        mandate = require_mandate(ctx.deps)
        ckpt = ctx.deps.sandbox.create_checkpoint(IMPL_CHECKPOINT_ID)
        ctx.state.workspace.checkpoints.append(ckpt)
        record_pce_step(self, ctx, "checkpoint_impl", "Captured pre-implementation snapshot")
        if len(implementation_scopes(mandate)) > 1:
            return DelegateNode()
        if ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE:
            return RefactorNode()
        return SynthesizeNode()


@dataclass
class SynthesizeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase IV (BHV-16 SYNTHESIZE): Synthesize implementation satisfying locked contracts."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.SYNTHESIZE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> VerifyStaticNode:
        mandate = require_mandate(ctx.deps)
        bundle, tel = await run_bounded_agent(
            build_synthesize_agent(
                ctx.deps.models.synthesize_model,
                languages=active_languages(ctx.deps),
            ),
            behavior_kind=BehaviorKind.SYNTHESIZE,
            prompt=partition_deps_prompt(ctx.deps, mandate, include_guidance=True),
            deps=make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.implementation_patches)
        record_pce_step(self, ctx, "synthesize", bundle.summary[:240])
        return VerifyStaticNode()


@dataclass
class RefactorNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase IV Brownfield Renovation (BHV-17 REFACTOR): Behavior-preserving JBC remediation."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.REFACTOR

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> VerifyStaticNode:
        mandate = require_mandate(ctx.deps)
        active_model = ctx.deps.models.refactor_model or ctx.deps.models.synthesize_model
        bundle, tel = await run_bounded_agent(
            build_refactor_agent(active_model, languages=active_languages(ctx.deps)),
            behavior_kind=BehaviorKind.REFACTOR,
            prompt=partition_deps_prompt(
                ctx.deps,
                mandate,
                suffix=refactor_baseline_suffix(ctx.deps),
                include_guidance=True,
            ),
            deps=make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.refactored_patches)
        record_pce_step(self, ctx, "refactor", bundle.summary[:240])
        return VerifyStaticNode()


@dataclass
class DelegateNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Topological Subagent Dispatch (BHV-10 DELEGATE): Wave-ordered TaskGroup fan-out."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.DELEGATE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ReconcileNode:
        mandate = require_mandate(ctx.deps)
        all_delegated, wave_count = await execute_delegation_waves(
            ctx.deps,
            ctx.state,
            mandate,
        )
        ctx.deps.delegated_bundles = all_delegated
        record_pce_step(
            self,
            ctx,
            "delegate",
            f"Executed {len(all_delegated)} scoped worker(s) across {wave_count} wave(s)",
        )
        return ReconcileNode()


@dataclass
class ReconcileNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Deterministic Synchronization (BHV-11 RECONCILE): Verify disjointness and commit."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.RECONCILE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> VerifyStaticNode:
        merged = verify_and_flatten_delegated_patches(ctx.deps.delegated_bundles)
        apply_patches_to_state(ctx.state, ctx.deps.sandbox, merged)
        ctx.state.world_model.active_delegations.clear()
        ctx.deps.delegated_bundles = []
        record_pce_step(
            self,
            ctx,
            "reconcile",
            f"Reconciled {len(merged)} disjoint artifact patch(es)",
        )
        return VerifyStaticNode()


@dataclass
class VerifyStaticNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Rough-In Gate (BHV-18 VERIFY_STATIC): Run JBC v1.2 static/AST/cohesion inspection."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.VERIFY_STATIC

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> VerifyDynamicNode | RevertNode:
        units = ctx.deps.sandbox.load_units()
        report = (
            inspect_differential_rough_in(
                ctx.deps.inspector,
                units,
                active_ratchet_scope(ctx.deps),
                permit=ctx.deps.permit,
            ).report
            if ctx.deps.budget.ratchet_to_scope
            else ctx.deps.inspector.inspect_rough_in(units, permit=ctx.deps.permit)
        )
        ctx.deps.latest_report = report
        record_inspection_to_state(ctx.state, report, stage="rough_in")
        record_pce_step(
            self,
            ctx,
            "verify_static",
            f"Rough-in ruling: {report.ruling} ({len(report.unwaived_violations())} violations)",
        )
        if not report.is_occupiable:
            return RevertNode(stage="rough_in")
        return VerifyDynamicNode()


@dataclass
class VerifyDynamicNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Final Gate (BHV-19 VERIFY_DYNAMIC): Execute dynamic gates and resource watchdogs."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.VERIFY_DYNAMIC

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> CertifyNode | RevertNode:
        units = ctx.deps.sandbox.load_units()
        attestations = ctx.deps.gate_runner.run_gates(ctx.deps.sandbox, units)
        ctx.deps.attestations = attestations
        report = (
            inspect_differential_final(
                ctx.deps.inspector,
                units,
                active_ratchet_scope(ctx.deps),
                permit=ctx.deps.permit,
                attestations=attestations,
            ).report
            if ctx.deps.budget.ratchet_to_scope
            else ctx.deps.inspector.inspect_final(
                units,
                permit=ctx.deps.permit,
                attestations=attestations,
            )
        )
        ctx.deps.latest_report = report
        record_inspection_to_state(ctx.state, report, stage="final")
        record_pce_step(
            self,
            ctx,
            "verify_dynamic",
            f"Final ruling: {report.ruling} ({len(report.unwaived_violations())} violations)",
        )
        if not report.is_occupiable:
            return RevertNode(stage="final")
        return CertifyNode()


@dataclass
class RevertNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Mandatory Rollback (BHV-20 REVERT): Restore checkpoint and record negative knowledge."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.REVERT
    stage: str

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> HypothesizeNode | ElicitNode:
        failed_excerpt = capture_failed_src_snapshot(ctx.deps.sandbox)
        restored = ctx.deps.sandbox.restore_checkpoint(IMPL_CHECKPOINT_ID)
        ctx.state.workspace.modified_artifacts = set(restored)
        summary = summarize_red_tag_findings(ctx.deps.latest_report, self.stage)
        ctx.state.world_model.falsified_paths.append(summary[:512])
        record_pce_step(self, ctx, "revert", f"Reverted to {IMPL_CHECKPOINT_ID} on RED_TAG")

        try:
            ctx.deps.remediation_loop.tick()
        except BudgetExceededError:
            if ctx.deps.sandbox.has_checkpoint("checkpoint_contract"):
                clean_files = ctx.deps.sandbox.restore_checkpoint("checkpoint_contract")
                ctx.state.workspace.modified_artifacts = set(clean_files)
                if ctx.deps.initial_inspector is not None:
                    ctx.deps.inspector = ctx.deps.initial_inspector
            return ElicitNode(
                reason=f"Remediation budget exhausted after {self.stage} RED_TAG.",
                escalated_red_tag=True,
            )
        return HypothesizeNode(
            red_tag_summary=summary,
            failed_candidate_excerpt=failed_excerpt,
        )


@dataclass
class HypothesizeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Causal Remediation (BHV-08 HYPOTHESIZE): Diagnose RED_TAG before re-attempting edit."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.HYPOTHESIZE
    red_tag_summary: str
    failed_candidate_excerpt: str = ""

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> CurateMemoryNode:
        mandate = require_mandate(ctx.deps)
        base_prompt = format_deps_prompt(ctx.deps, mandate)
        cand_sec = (
            f"\nFailed Candidate Snapshot (prior to REVERT):\n{self.failed_candidate_excerpt}"
            if self.failed_candidate_excerpt
            else ""
        )
        prompt = f"{base_prompt}{cand_sec}\nRED_TAG Citations: {self.red_tag_summary}"[:60_000]
        hypothesis, tel = await run_bounded_agent(
            build_hypothesize_agent(ctx.deps.models.hypothesize_model),
            behavior_kind=BehaviorKind.HYPOTHESIZE,
            prompt=prompt,
            deps=make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        ctx.deps.remediation_guidance = hypothesis.remediation_plan
        ctx.state.world_model.active_hypotheses.append(hypothesis.remediation_plan)
        record_pce_step(self, ctx, "hypothesize", hypothesis.remediation_plan[:240])
        return CurateMemoryNode()


@dataclass
class CurateMemoryNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Working-Memory Compaction (BHV-07 CURATE_MEMORY): Evict stale noise, keep negations."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CURATE_MEMORY

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InstrumentNode | CheckpointImplNode:
        mandate = require_mandate(ctx.deps)
        digest = await run_memory_compaction(ctx, mandate)
        apply_curated_digest_to_state(ctx.state, mandate, digest)
        ctx.deps.curated_notes.extend(digest.architectural_notes)
        record_pce_step(self, ctx, "curate_memory", digest.summary[:240])
        if is_test_only_static_red_tag(ctx.deps.latest_report, mandate):
            return InstrumentNode()
        return CheckpointImplNode()


@dataclass
class CertifyNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase V (BHV-21 CERTIFY): Issue SHA-256 CertificateOfOccupancy and audit PCE Dossier."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CERTIFY

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> End[PceWorkflowResult]:
        mandate = require_mandate(ctx.deps)
        record_pce_step(self, ctx, "certify", f"Certified occupancy for {mandate.mandate_id}")

        units = ctx.deps.sandbox.load_units()
        is_ratchet = ctx.deps.budget.ratchet_to_scope
        ratchet_scope = active_ratchet_scope(ctx.deps)
        final_report = (
            inspect_differential_final(
                ctx.deps.inspector,
                units,
                ratchet_scope,
                permit=ctx.deps.permit,
                attestations=ctx.deps.attestations,
            ).report
            if is_ratchet
            else ctx.deps.inspector.inspect_final(
                units,
                permit=ctx.deps.permit,
                attestations=ctx.deps.attestations,
            )
        )
        digest_units = (
            tuple(u for u in units if u.file_path in ratchet_scope.changed_paths)
            if is_ratchet
            else units
        )
        cert = certify_occupancy_in_state(
            ctx.state,
            final_report,
            artifact_digest=compute_units_digest(digest_units),
        )
        induced = trace_to_behavior_graph(ctx.state.trace, name=f"pce_{mandate.mandate_id}")
        graph_verdict = verify_behavior_graph(induced)
        dossier = EngineeringDossier(
            mandate=mandate,
            permit=ctx.deps.permit,
            behavior_graph=induced,
            mutated_files=ctx.deps.sandbox.modified_files,
            ratchet_scoped=is_ratchet,
            inspection_report=final_report,
            certificate=cert,
        )
        audit = audit_pce_dossier(dossier, units)
        if not audit.is_occupancy_sealed:
            msg = f"PCE Dossier failed Phase V audit: {audit.violations[0].message}"
            raise ValueError(msg)

        updated_ledger = record_certified_mandate_memory(
            ctx.deps.memory_ledger,
            mandate,
            artifact_digest=cert.artifact_digest,
            architectural_notes=tuple(ctx.deps.curated_notes),
            falsified_paths=tuple(ctx.state.world_model.falsified_paths),
        )
        if ctx.deps.budget.persist_workspace_memory:
            save_workspace_memory(ctx.deps.repo_root, updated_ledger)

        return End(
            PceWorkflowResult(
                status=PceRunStatus.OCCUPANCY_CERTIFIED,
                workflow_mode=ctx.deps.workflow_mode,
                mandate=mandate,
                permit=ctx.deps.permit,
                induced_graph=induced,
                graph_verification=graph_verdict,
                inspection_report=final_report,
                certificate=cert,
                dossier_audit=audit,
                memory_ledger=updated_ledger,
                telemetry=tuple(ctx.deps.telemetry),
                falsified_paths=tuple(ctx.state.world_model.falsified_paths),
            )
        )


def _build_behavior_node_map() -> Mapping[BehaviorKind, tuple[RuntimeNodeClass, ...]]:
    mapping: dict[BehaviorKind, tuple[RuntimeNodeClass, ...]] = {
        BehaviorKind.FRAME: (FrameNode,),
        BehaviorKind.ELICIT: (ElicitNode,),
        BehaviorKind.BIND_CODES: (BindCodesNode,),
        BehaviorKind.INSPECT_CODE: (InspectCodeNode,),
        BehaviorKind.INSPECT_ENV: (InspectEnvNode,),
        BehaviorKind.CONSULT: (ConsultNode,),
        BehaviorKind.CURATE_MEMORY: (CurateMemoryNode,),
        BehaviorKind.HYPOTHESIZE: (HypothesizeNode,),
        BehaviorKind.DECOMPOSE: (DecomposeNode,),
        BehaviorKind.DELEGATE: (DelegateNode,),
        BehaviorKind.RECONCILE: (ReconcileNode,),
        BehaviorKind.CHECKPOINT: (CheckpointContractNode, CheckpointImplNode),
        BehaviorKind.PROVISION: (ProvisionNode,),
        BehaviorKind.SPECIFY: (SpecifyNode,),
        BehaviorKind.INSTRUMENT: (InstrumentNode,),
        BehaviorKind.SYNTHESIZE: (SynthesizeNode,),
        BehaviorKind.REFACTOR: (RefactorNode,),
        BehaviorKind.VERIFY_STATIC: (VerifyStaticNode,),
        BehaviorKind.VERIFY_DYNAMIC: (VerifyDynamicNode,),
        BehaviorKind.REVERT: (RevertNode,),
        BehaviorKind.CERTIFY: (CertifyNode,),
    }
    return MappingProxyType(mapping)


PCE_BEHAVIOR_NODE_MAP: Final[Mapping[BehaviorKind, tuple[RuntimeNodeClass, ...]]] = (
    _build_behavior_node_map()
)


@icontract.ensure(
    lambda result: is_fibonacci(len(result)) and len(result) == FIBONACCI_BASIS_CARDINALITY
)
@jezz_callable
def validate_runtime_node_coverage() -> Mapping[BehaviorKind, tuple[RuntimeNodeClass, ...]]:
    """Verify that all 21 (F_8) BehaviorKinds have concrete AtomicBehaviorGraphNode classes."""
    if set(PCE_BEHAVIOR_NODE_MAP) != set(BehaviorKind):
        missing = sorted(set(BehaviorKind) - set(PCE_BEHAVIOR_NODE_MAP))
        msg = f"PCE_BEHAVIOR_NODE_MAP omits BehaviorKind(s): {missing}."
        raise ValueError(msg)
    for kind, node_classes in PCE_BEHAVIOR_NODE_MAP.items():
        if not node_classes:
            msg = f"BehaviorKind '{kind}' maps to zero AtomicBehaviorGraphNode classes."
            raise ValueError(msg)
        for cls in node_classes:
            if not issubclass(cls, AtomicBehaviorGraphNode) or cls.behavior_kind != kind:
                msg = f"Node class '{cls.__name__}' mismatches expected BehaviorKind '{kind}'."
                raise ValueError(msg)
    return PCE_BEHAVIOR_NODE_MAP
