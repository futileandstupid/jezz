"""Pillar 4: Typed pydantic-graph AtomicBehaviorGraphNodes enacting the 5-phase PCE lifecycle."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from pydantic_graph import End, GraphRunContext

from jezz.agents.schemas import (
    AgentInvocationTelemetry,
    ConstructionPatchBundle,
    ElicitationTicket,
    FilePatchArtifact,
    PceRunStatus,
    PceRuntimeBudget,
    PceWorkflowMode,
    PceWorkflowResult,
)
from jezz.agents.specialists import (
    PceModelSuite,
    build_frame_agent,
    build_hypothesize_agent,
    build_inspect_agent,
    build_instrument_agent,
    build_refactor_agent,
    build_specify_agent,
    build_synthesize_agent,
    run_bounded_agent,
)
from jezz.agents.workspace import DynamicGatePort, JezzAgentDeps, WorkspaceSandbox
from jezz.behaviors.graph import verify_behavior_graph
from jezz.behaviors.runtime import (
    AgentExecutionState,
    AtomicBehaviorGraphNode,
    ExecutionStep,
    trace_to_behavior_graph,
)
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.inspector import (
    BuildingInspector,
    bind_permit_to_state,
    certify_occupancy_in_state,
    compute_units_digest,
    record_inspection_to_state,
)
from jezz.codes.permits import CodePermit, GateAttestation, InspectionReport
from jezz.guards import BudgetExceededError, ContainedPath, LoopBudget
from jezz.methodology.dossier import (
    EngineeringDossier,
    audit_pce_dossier,
    extract_compiled_symbols,
)
from jezz.methodology.mandate import EngineeringMandate, ModuleWriteScope

_IMPL_CHECKPOINT_ID = "checkpoint_impl"

type ElicitationCallback = Callable[[EngineeringMandate], Sequence[str] | None]


@dataclass
class PceGraphDeps:
    """Mutable run-scoped dependencies threaded across PCE pydantic-graph nodes."""

    repo_root: Path
    sandbox: WorkspaceSandbox
    inspector: BuildingInspector
    gate_runner: DynamicGatePort
    models: PceModelSuite
    permit: CodePermit
    budget: PceRuntimeBudget
    remediation_loop: LoopBudget
    workflow_mode: PceWorkflowMode = PceWorkflowMode.GREENFIELD
    elicitation_loop: LoopBudget = field(default_factory=lambda: LoopBudget(max_steps=3))
    task_prompt: str = ""
    mandate: EngineeringMandate | None = None
    prior_draft_mandate: EngineeringMandate | None = None
    clarifications: tuple[str, ...] = ()
    elicitation_callback: ElicitationCallback | None = None
    baseline_red_tag_summary: str = ""
    latest_report: InspectionReport | None = None
    attestations: tuple[GateAttestation, ...] = ()
    telemetry: list[AgentInvocationTelemetry] = field(default_factory=list)
    remediation_guidance: str = ""
    delegated_bundles: list[tuple[ModuleWriteScope, ConstructionPatchBundle]] = field(
        default_factory=list
    )


def _require_mandate(deps: PceGraphDeps) -> EngineeringMandate:
    if deps.mandate is None:
        msg = "EngineeringMandate must be established at BHV-01 FRAME before downstream nodes."
        raise RuntimeError(msg)
    return deps.mandate


def _make_agent_deps(
    deps: PceGraphDeps,
    *,
    scope: ModuleWriteScope | None = None,
) -> JezzAgentDeps:
    active_sandbox = deps.sandbox.scoped_view(scope) if scope is not None else deps.sandbox
    return JezzAgentDeps(
        sandbox=active_sandbox,
        inspector=deps.inspector,
        permit=deps.permit,
        mandate=deps.mandate,
        active_scope=scope,
    )


def _apply_patches_to_state(
    state: AgentExecutionState,
    sandbox: WorkspaceSandbox,
    patches: Sequence[FilePatchArtifact],
) -> None:
    for patch in patches:
        contained = sandbox.write_authorized_file(patch.file_path, patch.source_code)
        state.workspace.modified_artifacts.add(contained.relative_posix)


def _existing_authorized_files(sandbox: WorkspaceSandbox) -> list[str]:
    return [
        rel
        for rel in sorted(sandbox.authorized_files)
        if ContainedPath.from_parts(sandbox.repo_root, rel).resolved_path.is_file()
    ]


def _render_inline_authorized_snapshots(
    sandbox: WorkspaceSandbox,
    existing_paths: Sequence[str],
    *,
    max_total_chars: int = 24_000,
) -> str:
    if not existing_paths:
        return ""
    sections: list[str] = []
    used = 0
    for rel in existing_paths:
        content = sandbox.read_file(rel)
        if used + len(content) > max_total_chars:
            return ""
        sections.append(f"### {rel}\n```python\n{content.rstrip()}\n```")
        used += len(content)
    return "\nExisting Authorized File Snapshots:\n" + "\n".join(sections)


def _format_mandate_prompt(
    mandate: EngineeringMandate,
    sandbox: WorkspaceSandbox,
    guidance: str = "",
) -> str:
    inv_lines = "; ".join(
        f"{inv.invariant_id} ({inv.verification_symbol}): {inv.statement}"
        for inv in mandate.invariants
    )
    scope_lines = "; ".join(
        f"{s.scope_id} -> files={sorted(s.owned_files)}, symbols={list(s.contract_symbols)}"
        for s in mandate.write_scopes
    )
    existing = _existing_authorized_files(sandbox)
    snapshots = _render_inline_authorized_snapshots(sandbox, existing)
    base = (
        f"Mandate {mandate.mandate_id}: {mandate.title}\n"
        f"Objective: {mandate.objective}\n"
        f"Invariants: {inv_lines}\n"
        f"Write Scopes: {scope_lines}\n"
        f"Existing Authorized Files On Disk: {existing}"
        f"{snapshots}"
    )
    if guidance:
        return f"{base}\nRemediation Guidance: {guidance}"
    return base


def _format_baseline_red_tags(report: InspectionReport) -> str:
    violations = report.unwaived_violations()
    if not violations:
        return "0 baseline JBC violations."
    lines = [
        f"- {f.code_id} ({f.location.file_path}:L{f.location.line}): "
        f"{f.message} -> Fix: {f.remediation}"
        for f in violations[:24]
    ]
    return f"{len(violations)} baseline JBC violation(s):\n" + "\n".join(lines)


def _render_brownfield_frame_context(deps: PceGraphDeps) -> str:
    if deps.workflow_mode != PceWorkflowMode.RENOVATE:
        return ""
    units = deps.sandbox.load_units()
    if not units:
        return ""
    files = [u.file_path for u in units]
    pub_syms = sorted(
        sym for sym in extract_compiled_symbols(units) if "." not in sym and not sym.startswith("_")
    )[:48]
    rough = deps.inspector.inspect_rough_in(units, permit=deps.permit)
    return (
        f"\nBrownfield Workspace Files: {files}\n"
        f"Existing Public Symbols: {pub_syms}\n"
        f"Baseline Inspection:\n{_format_baseline_red_tags(rough)}"
    )


def _build_frame_prompt(deps: PceGraphDeps) -> str:
    base = deps.task_prompt.strip() or "Frame engineering mandate."
    context = _render_brownfield_frame_context(deps)
    if deps.prior_draft_mandate is None or not deps.clarifications:
        return f"{base}{context}"[:60_000]
    answers = "\n".join(f"- {item}" for item in deps.clarifications)
    draft_json = deps.prior_draft_mandate.model_dump_json(indent=2)
    return (
        f"{base}{context}\n\n"
        f"Prior Draft EngineeringMandate (halted at BHV-02 ELICIT):\n{draft_json}\n\n"
        f"Authoritative Clarifications Resolving Open Ambiguities:\n{answers}"
    )[:60_000]


def _frame_step_id(trace: Sequence[ExecutionStep]) -> str:
    elicit_count = sum(1 for step in trace if step.kind == BehaviorKind.ELICIT)
    if elicit_count == 0:
        return "frame"
    if elicit_count == 1:
        return "reframe_resolved"
    return f"reframe_resolved_{elicit_count}"


def _try_in_process_elicitation(deps: PceGraphDeps) -> bool:
    callback = deps.elicitation_callback
    if callback is None:
        return False
    try:
        deps.elicitation_loop.tick()
    except BudgetExceededError:
        return False
    mandate = _require_mandate(deps)
    raw_answers: Sequence[str] | None = callback(mandate)
    if raw_answers is None:
        return False
    cleaned = tuple(ans.strip() for ans in raw_answers if ans.strip())
    if not cleaned:
        return False
    deps.prior_draft_mandate = mandate
    deps.clarifications = cleaned
    deps.mandate = None
    return True


def _has_authorized_tests(mandate: EngineeringMandate) -> bool:
    return any(rel.startswith(("tests/", "test/")) for rel in mandate.authorized_files())


def _implementation_scopes(mandate: EngineeringMandate) -> tuple[ModuleWriteScope, ...]:
    return tuple(
        scope
        for scope in mandate.write_scopes
        if any(not rel.startswith(("tests/", "test/")) for rel in scope.owned_files)
    )


def _assemble_halted_result(
    state: AgentExecutionState,
    deps: PceGraphDeps,
    status: PceRunStatus,
    *,
    ticket: ElicitationTicket | None = None,
) -> PceWorkflowResult:
    mandate = _require_mandate(deps)
    induced = trace_to_behavior_graph(state.trace, name=f"pce_{mandate.mandate_id}")
    verdict = verify_behavior_graph(induced)
    return PceWorkflowResult(
        status=status,
        workflow_mode=deps.workflow_mode,
        mandate=mandate,
        permit=deps.permit,
        induced_graph=induced,
        graph_verification=verdict,
        inspection_report=deps.latest_report,
        elicitation_ticket=ticket,
        telemetry=tuple(deps.telemetry),
        falsified_paths=tuple(state.world_model.falsified_paths),
    )


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
                build_frame_agent(ctx.deps.models.frame_model),
                behavior_kind=BehaviorKind.FRAME,
                prompt=_build_frame_prompt(ctx.deps),
                deps=_make_agent_deps(ctx.deps),
                budget=ctx.deps.budget,
            )
            ctx.deps.telemetry.append(tel)
            ctx.deps.mandate = proposal.mandate
            ctx.deps.prior_draft_mandate = None
            ctx.deps.clarifications = ()

        mandate = _require_mandate(ctx.deps)
        ctx.deps.sandbox = WorkspaceSandbox(ctx.deps.repo_root, mandate.authorized_files())
        ctx.state.problem.objective = mandate.objective
        ctx.state.problem.ambiguities = list(mandate.open_ambiguities)
        step_id = _frame_step_id(ctx.state.trace)
        self.record_into_context(ctx, step_id, f"Framed mandate {mandate.mandate_id}")

        if mandate.requires_elicitation:
            return ElicitNode(reason="Open domain ambiguities require human resolution.")
        return BindCodesNode()


@dataclass
class ElicitNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Authority Gate (BHV-02 ELICIT): Re-enter FRAME with answers or halt with ticket."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.ELICIT
    reason: str
    escalated_red_tag: bool = False

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> FrameNode | End[PceWorkflowResult]:
        elicit_idx = sum(1 for step in ctx.state.trace if step.kind == BehaviorKind.ELICIT) + 1
        step_id = "elicit" if elicit_idx == 1 else f"elicit_{elicit_idx}"
        self.record_into_context(ctx, step_id, self.reason[:240])
        if self.escalated_red_tag:
            return End(
                _assemble_halted_result(ctx.state, ctx.deps, PceRunStatus.ESCALATED_AFTER_RED_TAG)
            )
        if _try_in_process_elicitation(ctx.deps):
            return FrameNode()
        ticket = ElicitationTicket(
            workflow_mode=ctx.deps.workflow_mode,
            mandate=_require_mandate(ctx.deps),
            original_prompt=ctx.deps.task_prompt,
            prior_trace=tuple(ctx.state.trace),
            prior_telemetry=tuple(ctx.deps.telemetry),
        )
        return End(
            _assemble_halted_result(
                ctx.state,
                ctx.deps,
                PceRunStatus.HALTED_FOR_ELICITATION,
                ticket=ticket,
            )
        )


@dataclass
class BindCodesNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase II (BHV-03 BIND_CODES): Lock JBC v1.1 CodePermit into execution state."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.BIND_CODES

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InspectCodeNode:
        bind_permit_to_state(ctx.state, ctx.deps.permit)
        self.record_into_context(
            ctx,
            "bind_codes",
            f"Bound {len(ctx.deps.permit.bound_code_ids)} JBC code provisions",
        )
        return InspectCodeNode()


@dataclass
class InspectCodeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Epistemic Grounding (BHV-04 INSPECT_CODE): Read-only workspace observation."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.INSPECT_CODE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> DecomposeNode:
        mandate = _require_mandate(ctx.deps)
        if ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE:
            rough = ctx.deps.inspector.inspect_rough_in(
                ctx.deps.sandbox.load_units(),
                permit=ctx.deps.permit,
            )
            ctx.deps.baseline_red_tag_summary = _format_baseline_red_tags(rough)
            ctx.state.world_model.observations.append(ctx.deps.baseline_red_tag_summary[:1000])
        obs, tel = await run_bounded_agent(
            build_inspect_agent(ctx.deps.models.inspect_model),
            behavior_kind=BehaviorKind.INSPECT_CODE,
            prompt=_format_mandate_prompt(mandate, ctx.deps.sandbox),
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        ctx.state.world_model.observations.append(obs.summary)
        self.record_into_context(ctx, "inspect_code", obs.summary[:240])
        return DecomposeNode()


@dataclass
class DecomposeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Topology Partitioning (BHV-09 DECOMPOSE): Lock disjoint ModuleWriteScopes."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.DECOMPOSE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> CheckpointContractNode | CheckpointImplNode:
        mandate = _require_mandate(ctx.deps)
        ctx.state.problem.subtasks = [
            f"{s.scope_id}:{sorted(s.owned_files)}" for s in mandate.write_scopes
        ]
        self.record_into_context(
            ctx,
            "decompose",
            f"Partitioned {len(mandate.write_scopes)} disjoint write scope(s)",
        )
        if ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE and not _has_authorized_tests(
            mandate
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
    ) -> SpecifyNode | InstrumentNode:
        ckpt = ctx.deps.sandbox.create_checkpoint("checkpoint_contract")
        ctx.state.workspace.checkpoints.append(ckpt)
        self.record_into_context(ctx, "checkpoint_contract", "Captured pre-contract snapshot")
        if ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE:
            return InstrumentNode()
        return SpecifyNode()


@dataclass
class SpecifyNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase III (BHV-14 SPECIFY): Emit compiled schemas and @icontract obligations."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.SPECIFY

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InstrumentNode:
        mandate = _require_mandate(ctx.deps)
        bundle, tel = await run_bounded_agent(
            build_specify_agent(ctx.deps.models.specify_model),
            behavior_kind=BehaviorKind.SPECIFY,
            prompt=_format_mandate_prompt(mandate, ctx.deps.sandbox),
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.contract_patches)
        self.record_into_context(ctx, "specify", bundle.summary[:240])
        return InstrumentNode()


@dataclass
class InstrumentNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase III (BHV-15 INSTRUMENT): Emit pytest and Hypothesis falsification suites."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.INSTRUMENT

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> CheckpointImplNode:
        mandate = _require_mandate(ctx.deps)
        prompt = _format_mandate_prompt(mandate, ctx.deps.sandbox)
        if ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE:
            prompt = (
                f"{prompt}\nBrownfield Renovation Mode: Preserve/lock existing observable "
                "public behavior from src/... snapshots while defining every mandated "
                "verification_symbol under full JBC v1.1 test compliance."
            )[:60_000]
        bundle, tel = await run_bounded_agent(
            build_instrument_agent(ctx.deps.models.instrument_model),
            behavior_kind=BehaviorKind.INSTRUMENT,
            prompt=prompt,
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.test_patches)
        self.record_into_context(ctx, "instrument", bundle.summary[:240])
        return CheckpointImplNode()


@dataclass
class CheckpointImplNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Pre-Construction Snapshot (BHV-12 CHECKPOINT): Lock contracts before implementation."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CHECKPOINT

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> SynthesizeNode | RefactorNode | DelegateNode:
        mandate = _require_mandate(ctx.deps)
        ckpt = ctx.deps.sandbox.create_checkpoint(_IMPL_CHECKPOINT_ID)
        ctx.state.workspace.checkpoints.append(ckpt)
        self.record_into_context(ctx, "checkpoint_impl", "Captured pre-implementation snapshot")
        if len(_implementation_scopes(mandate)) > 1:
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
        mandate = _require_mandate(ctx.deps)
        bundle, tel = await run_bounded_agent(
            build_synthesize_agent(ctx.deps.models.synthesize_model),
            behavior_kind=BehaviorKind.SYNTHESIZE,
            prompt=_format_mandate_prompt(mandate, ctx.deps.sandbox, ctx.deps.remediation_guidance),
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.implementation_patches)
        self.record_into_context(ctx, "synthesize", bundle.summary[:240])
        return VerifyStaticNode()


def _format_refactor_prompt(deps: PceGraphDeps, mandate: EngineeringMandate) -> str:
    base = _format_mandate_prompt(mandate, deps.sandbox, deps.remediation_guidance)
    if deps.baseline_red_tag_summary:
        return f"{base}\nBaseline JBC RED_TAG Findings:\n{deps.baseline_red_tag_summary}"[:60_000]
    return base[:60_000]


@dataclass
class RefactorNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase IV Brownfield Renovation (BHV-17 REFACTOR): Behavior-preserving JBC remediation."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.REFACTOR

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> VerifyStaticNode:
        mandate = _require_mandate(ctx.deps)
        active_model = ctx.deps.models.refactor_model or ctx.deps.models.synthesize_model
        bundle, tel = await run_bounded_agent(
            build_refactor_agent(active_model),
            behavior_kind=BehaviorKind.REFACTOR,
            prompt=_format_refactor_prompt(ctx.deps, mandate),
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.refactored_patches)
        self.record_into_context(ctx, "refactor", bundle.summary[:240])
        return VerifyStaticNode()


async def _run_scoped_slice(
    deps: PceGraphDeps,
    mandate: EngineeringMandate,
    scope: ModuleWriteScope,
) -> tuple[ModuleWriteScope, ConstructionPatchBundle, AgentInvocationTelemetry]:
    slice_footer = (
        f"Active Slice Scope: {scope.scope_id} owning {sorted(scope.owned_files)} "
        f"for symbols {list(scope.contract_symbols)}."
    )
    if deps.workflow_mode == PceWorkflowMode.RENOVATE:
        ref_prompt = f"{_format_refactor_prompt(deps, mandate)}\n{slice_footer}"[:60_000]
        ref_model = deps.models.refactor_model or deps.models.synthesize_model
        ref_bundle, ref_tel = await run_bounded_agent(
            build_refactor_agent(ref_model),
            behavior_kind=BehaviorKind.DELEGATE,
            prompt=ref_prompt,
            deps=_make_agent_deps(deps, scope=scope),
            budget=deps.budget,
        )
        adapted = ConstructionPatchBundle(
            implementation_patches=ref_bundle.refactored_patches,
            summary=ref_bundle.summary,
        )
        return scope, adapted, ref_tel
    prompt = (
        f"{_format_mandate_prompt(mandate, deps.sandbox, deps.remediation_guidance)}\n"
        f"{slice_footer}"
    )
    bundle, tel = await run_bounded_agent(
        build_synthesize_agent(deps.models.synthesize_model),
        behavior_kind=BehaviorKind.DELEGATE,
        prompt=prompt,
        deps=_make_agent_deps(deps, scope=scope),
        budget=deps.budget,
    )
    return scope, bundle, tel


@dataclass
class DelegateNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Parallel Subagent Dispatch (BHV-10 DELEGATE): TaskGroup fan-out per ModuleWriteScope."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.DELEGATE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ReconcileNode:
        mandate = _require_mandate(ctx.deps)
        tasks: list[
            asyncio.Task[tuple[ModuleWriteScope, ConstructionPatchBundle, AgentInvocationTelemetry]]
        ] = []
        async with asyncio.TaskGroup() as tg:
            for scope in _implementation_scopes(mandate):
                ctx.state.world_model.active_delegations.add(scope.scope_id)
                tasks.append(tg.create_task(_run_scoped_slice(ctx.deps, mandate, scope)))

        ctx.deps.delegated_bundles = []
        for finished in tasks:
            scope, bundle, tel = finished.result()
            ctx.deps.telemetry.append(tel)
            ctx.deps.delegated_bundles.append((scope, bundle))

        self.record_into_context(
            ctx,
            "delegate",
            f"Executed {len(tasks)} parallel scoped synthesis worker(s)",
        )
        return ReconcileNode()


def _verify_and_flatten_delegated_patches(
    delegated: Sequence[tuple[ModuleWriteScope, ConstructionPatchBundle]],
) -> list[FilePatchArtifact]:
    seen_files: set[str] = set()
    merged: list[FilePatchArtifact] = []
    for scope, bundle in delegated:
        for patch in bundle.implementation_patches:
            if patch.file_path not in scope.owned_files:
                msg = (
                    f"Delegated worker '{scope.scope_id}' attempted unauthorized edit "
                    f"to '{patch.file_path}'."
                )
                raise PermissionError(msg)
            if patch.file_path in seen_files:
                msg = f"Write-set collision during RECONCILE on '{patch.file_path}'."
                raise ValueError(msg)
            seen_files.add(patch.file_path)
            merged.append(patch)
    return merged


@dataclass
class ReconcileNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Deterministic Synchronization (BHV-11 RECONCILE): Verify disjointness and commit."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.RECONCILE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> VerifyStaticNode:
        merged = _verify_and_flatten_delegated_patches(ctx.deps.delegated_bundles)
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, merged)
        ctx.state.world_model.active_delegations.clear()
        ctx.deps.delegated_bundles = []
        self.record_into_context(
            ctx,
            "reconcile",
            f"Reconciled {len(merged)} disjoint artifact patch(es)",
        )
        return VerifyStaticNode()


@dataclass
class VerifyStaticNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Rough-In Gate (BHV-18 VERIFY_STATIC): Run JBC v1.1 static/AST/cohesion inspection."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.VERIFY_STATIC

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> VerifyDynamicNode | RevertNode:
        units = ctx.deps.sandbox.load_units()
        report = ctx.deps.inspector.inspect_rough_in(units, permit=ctx.deps.permit)
        ctx.deps.latest_report = report
        record_inspection_to_state(ctx.state, report, stage="rough_in")
        self.record_into_context(
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
        report = ctx.deps.inspector.inspect_final(
            units,
            permit=ctx.deps.permit,
            attestations=attestations,
        )
        ctx.deps.latest_report = report
        record_inspection_to_state(ctx.state, report, stage="final")
        self.record_into_context(
            ctx,
            "verify_dynamic",
            f"Final ruling: {report.ruling} ({len(report.unwaived_violations())} violations)",
        )
        if not report.is_occupiable:
            return RevertNode(stage="final")
        return CertifyNode()


def _summarize_red_tag_findings(report: InspectionReport | None, stage: str) -> str:
    if report is None:
        return f"{stage}:unknown_red_tag"
    items = [
        f"{f.code_id}@{f.location.file_path}:{f.location.line} ({f.message})"
        for f in report.unwaived_violations()[:6]
    ]
    return f"{stage}_red_tag: " + "; ".join(items)


@dataclass
class RevertNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Mandatory Rollback (BHV-20 REVERT): Restore checkpoint and record negative knowledge."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.REVERT
    stage: str

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> HypothesizeNode | ElicitNode:
        restored = ctx.deps.sandbox.restore_checkpoint(_IMPL_CHECKPOINT_ID)
        ctx.state.workspace.modified_artifacts = set(restored)
        summary = _summarize_red_tag_findings(ctx.deps.latest_report, self.stage)
        ctx.state.world_model.falsified_paths.append(summary[:512])
        self.record_into_context(ctx, "revert", f"Reverted to {_IMPL_CHECKPOINT_ID} on RED_TAG")

        try:
            ctx.deps.remediation_loop.tick()
        except BudgetExceededError:
            return ElicitNode(
                reason=f"Remediation budget exhausted after {self.stage} RED_TAG.",
                escalated_red_tag=True,
            )
        return HypothesizeNode(red_tag_summary=summary)


@dataclass
class HypothesizeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Causal Remediation (BHV-08 HYPOTHESIZE): Diagnose RED_TAG before re-attempting edit."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.HYPOTHESIZE
    red_tag_summary: str

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> CheckpointImplNode:
        mandate = _require_mandate(ctx.deps)
        base_prompt = _format_mandate_prompt(mandate, ctx.deps.sandbox)
        prompt = f"{base_prompt}\nRED_TAG Citations: {self.red_tag_summary}"
        hypothesis, tel = await run_bounded_agent(
            build_hypothesize_agent(ctx.deps.models.hypothesize_model),
            behavior_kind=BehaviorKind.HYPOTHESIZE,
            prompt=prompt,
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        ctx.deps.remediation_guidance = hypothesis.remediation_plan
        ctx.state.world_model.active_hypotheses.append(hypothesis.remediation_plan)
        self.record_into_context(ctx, "hypothesize", hypothesis.remediation_plan[:240])
        return CheckpointImplNode()


@dataclass
class CertifyNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase V (BHV-21 CERTIFY): Issue SHA-256 CertificateOfOccupancy and audit PCE Dossier."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CERTIFY

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> End[PceWorkflowResult]:
        mandate = _require_mandate(ctx.deps)
        self.record_into_context(ctx, "certify", f"Certified occupancy for {mandate.mandate_id}")

        units = ctx.deps.sandbox.load_units()
        final_report = ctx.deps.inspector.inspect_final(
            units,
            permit=ctx.deps.permit,
            attestations=ctx.deps.attestations,
        )
        cert = certify_occupancy_in_state(
            ctx.state,
            final_report,
            artifact_digest=compute_units_digest(units),
        )
        induced = trace_to_behavior_graph(ctx.state.trace, name=f"pce_{mandate.mandate_id}")
        graph_verdict = verify_behavior_graph(induced)
        dossier = EngineeringDossier(
            mandate=mandate,
            permit=ctx.deps.permit,
            behavior_graph=induced,
            mutated_files=ctx.deps.sandbox.modified_files,
            inspection_report=final_report,
            certificate=cert,
        )
        audit = audit_pce_dossier(dossier, units)
        if not audit.is_occupancy_sealed:
            msg = f"PCE Dossier failed Phase V audit: {audit.violations[0].message}"
            raise ValueError(msg)

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
                telemetry=tuple(ctx.deps.telemetry),
                falsified_paths=tuple(ctx.state.world_model.falsified_paths),
            )
        )
