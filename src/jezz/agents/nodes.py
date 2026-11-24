"""Pillar 4: Typed pydantic-graph AtomicBehaviorGraphNodes enacting all 21 PCE behaviors."""

import asyncio
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, ClassVar, Final

import icontract
from pydantic_graph import End, GraphRunContext

from jezz.agents.caching import CachedUserPrompt, partition_cached_user_prompt
from jezz.agents.memory import (
    apply_curated_digest_to_state,
    build_consultation_reference_pack,
    compact_world_model_deterministically,
    format_memory_prompt_context,
    load_workspace_memory,
    record_certified_mandate_memory,
    save_workspace_memory,
)
from jezz.agents.polyglot_prompts import (
    code_fence_for_path,
    detect_workspace_languages,
    is_polyglot_test_path,
)
from jezz.agents.provision import (
    InProcessProvisionRunner,
    ProvisionPort,
    compute_missing_dependencies,
    refresh_inspector_after_provision,
)
from jezz.agents.schemas import (
    AgentInvocationTelemetry,
    ConstructionPatchBundle,
    CuratedMemoryDigest,
    ElicitationTicket,
    EnvironmentObservation,
    FilePatchArtifact,
    InstrumentationSuiteBundle,
    PceRunStatus,
    PceRuntimeBudget,
    PceWorkflowMode,
    PceWorkflowResult,
    WorkspaceMemoryLedger,
)
from jezz.agents.specialists import (
    PceModelSuite,
    build_consult_agent,
    build_curate_memory_agent,
    build_decompose_agent,
    build_frame_agent,
    build_hypothesize_agent,
    build_inspect_agent,
    build_inspect_env_agent,
    build_instrument_agent,
    build_provision_agent,
    build_refactor_agent,
    build_specify_agent,
    build_synthesize_agent,
    run_bounded_agent,
)
from jezz.agents.workspace import DynamicGatePort, JezzAgentDeps, WorkspaceSandbox
from jezz.behaviors.fibonacci import FIBONACCI_BASIS_CARDINALITY, is_fibonacci
from jezz.behaviors.graph import verify_behavior_graph
from jezz.behaviors.runtime import (
    AgentExecutionState,
    AtomicBehaviorGraphNode,
    ExecutionStep,
    trace_to_behavior_graph,
)
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.ast_graph import build_workspace_ast_graph
from jezz.codes.inspector import (
    BuildingInspector,
    bind_permit_to_state,
    certify_occupancy_in_state,
    compute_units_digest,
    record_inspection_to_state,
)
from jezz.codes.permits import CodePermit, GateAttestation, InspectionReport
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.codes.ratchet import (
    DifferentialScope,
    inspect_differential_final,
    inspect_differential_rough_in,
)
from jezz.guards import BudgetExceededError, ContainedPath, InstanceGuard, LoopBudget, jezz_callable
from jezz.methodology.dossier import (
    EngineeringDossier,
    audit_pce_dossier,
    extract_compiled_symbols,
)
from jezz.methodology.mandate import (
    DependencyRequirement,
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
    compute_scope_execution_waves,
)

_IMPL_CHECKPOINT_ID = "checkpoint_impl"

type ElicitationCallback = Callable[[EngineeringMandate], Sequence[str] | None]
type ProgressCallback = Callable[[BehaviorKind, str, str], None]


@dataclass(frozen=True)
class PceInteractionHooks:
    """Optional human-in-the-loop elicitation, live progress, and plan-approval hooks."""

    on_elicit: ElicitationCallback | None = None
    on_progress: ProgressCallback | None = None
    plan_only: bool = False


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
    provision_runner: ProvisionPort = field(default_factory=InProcessProvisionRunner)
    initial_inspector: BuildingInspector | None = None
    elicitation_loop: LoopBudget = field(default_factory=lambda: LoopBudget(max_steps=3))
    task_prompt: str = ""
    mandate: EngineeringMandate | None = None
    prior_draft_mandate: EngineeringMandate | None = None
    clarifications: tuple[str, ...] = ()
    elicitation_callback: ElicitationCallback | None = None
    progress_callback: ProgressCallback | None = None
    plan_only: bool = False
    mandate_was_framed: bool = False
    ast_graph_brief: str = ""
    baseline_red_tag_summary: str = ""
    memory_ledger: WorkspaceMemoryLedger = field(default_factory=WorkspaceMemoryLedger)
    memory_context: str = ""
    consultation_context: str = ""
    curated_notes: list[str] = field(default_factory=list)
    latest_report: InspectionReport | None = None
    attestations: tuple[GateAttestation, ...] = ()
    telemetry: list[AgentInvocationTelemetry] = field(default_factory=list)
    remediation_guidance: str = ""
    delegated_bundles: list[tuple[ModuleWriteScope, ConstructionPatchBundle]] = field(
        default_factory=list
    )


type RuntimeNodeClass = Annotated[
    type[AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]],
    InstanceGuard(type),
]


def _require_mandate(deps: PceGraphDeps) -> EngineeringMandate:
    if deps.mandate is None:
        msg = "EngineeringMandate must be established at BHV-01 FRAME before downstream nodes."
        raise RuntimeError(msg)
    return deps.mandate


def _make_agent_deps(
    deps: PceGraphDeps,
    *,
    scope: ModuleWriteScope | None = None,
    scoped_invariants: tuple[DomainInvariant, ...] | None = None,
    prior_falsified_paths: tuple[str, ...] = (),
) -> JezzAgentDeps:
    active_sandbox = deps.sandbox.scoped_view(scope) if scope is not None else deps.sandbox
    return JezzAgentDeps(
        sandbox=active_sandbox,
        inspector=deps.inspector,
        permit=deps.permit,
        mandate=deps.mandate,
        active_scope=scope,
        scoped_invariants=scoped_invariants,
        prior_falsified_paths=prior_falsified_paths,
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


def _active_languages(
    deps: PceGraphDeps,
    *,
    scope: ModuleWriteScope | None = None,
) -> frozenset[SupportedLanguage]:
    auth = (
        scope.owned_files
        if scope is not None
        else (
            deps.mandate.authorized_files()
            if deps.mandate is not None
            else deps.sandbox.authorized_files
        )
    )
    return frozenset(
        detect_workspace_languages(
            deps.repo_root,
            units=deps.sandbox.load_units(),
            authorized_files=auth,
        )
    )


def _primary_language(deps: PceGraphDeps) -> SupportedLanguage:
    auth = (
        deps.mandate.authorized_files()
        if deps.mandate is not None
        else deps.sandbox.authorized_files
    )
    langs = detect_workspace_languages(
        deps.repo_root,
        units=deps.sandbox.load_units(),
        authorized_files=auth,
    )
    return langs[0]


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
        fence = code_fence_for_path(rel)
        sections.append(f"### {rel}\n```{fence}\n{content.rstrip()}\n```")
        used += len(content)
    return "\nExisting Authorized File Snapshots:\n" + "\n".join(sections)


def _is_public_qualname(qualname: str) -> bool:
    parts = qualname.split(".")
    return all(not p.startswith("_") or p == "__init__" for p in parts)


def _render_upstream_src_context(
    sandbox: WorkspaceSandbox,
    *,
    max_chars: int = 12_000,
) -> str:
    src_units = [
        u
        for u in sandbox.load_units()
        if not u.is_test_file
        and not is_polyglot_test_path(u.file_path)
        and u.file_path not in sandbox.authorized_files
    ]
    if not src_units:
        return ""
    graph = build_workspace_ast_graph(src_units)
    lines: list[str] = ["\nUpstream Workspace Module Signatures (read-only imports):"]
    used = len(lines[0])
    for sym in graph.symbols:
        if not _is_public_qualname(sym.qualname):
            continue
        row = f"- {sym.module_name}.{sym.qualname}: {sym.signature_summary}"
        if used + len(row) + 1 > max_chars:
            break
        lines.append(row)
        used += len(row) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_mandate_prompt(
    mandate: EngineeringMandate,
    sandbox: WorkspaceSandbox,
    guidance: str = "",
    *,
    extra_context: str = "",
) -> str:
    inv_lines = "; ".join(
        f"{inv.invariant_id} ({inv.verification_symbol}): {inv.statement}"
        for inv in mandate.invariants
    )
    scope_lines = "; ".join(
        f"{s.scope_id}(deps={sorted(s.depends_on)}) -> "
        f"files={sorted(s.owned_files)}, symbols={list(s.contract_symbols)}"
        for s in mandate.write_scopes
    )
    dep_line = (
        f"\nDeclared Dependencies: {[d.pep508_spec() for d in mandate.required_dependencies]}"
        if mandate.required_dependencies
        else ""
    )
    existing = _existing_authorized_files(sandbox)
    snapshots = _render_inline_authorized_snapshots(sandbox, existing)
    upstream = _render_upstream_src_context(sandbox)
    base = (
        f"Mandate {mandate.mandate_id}: {mandate.title}\n"
        f"Objective: {mandate.objective}\n"
        f"Invariants: {inv_lines}\n"
        f"Write Scopes: {scope_lines}"
        f"{dep_line}\n"
        f"Existing Authorized Files On Disk: {existing}"
        f"{extra_context}"
        f"{upstream}"
        f"{snapshots}"
    )
    if guidance:
        return f"{base}\nRemediation Guidance: {guidance}"
    return base


def _format_deps_prompt(
    deps: PceGraphDeps,
    mandate: EngineeringMandate,
    *,
    include_guidance: bool = False,
) -> str:
    return _format_mandate_prompt(
        mandate,
        deps.sandbox,
        deps.remediation_guidance if include_guidance else "",
        extra_context=f"{deps.memory_context}{deps.consultation_context}",
    )


def _partition_deps_prompt(
    deps: PceGraphDeps,
    mandate: EngineeringMandate,
    *,
    suffix: str = "",
    include_guidance: bool = False,
) -> CachedUserPrompt:
    shared_prefix = _format_deps_prompt(deps, mandate, include_guidance=False)[:57_344]
    tail_parts: list[str] = []
    if include_guidance and deps.remediation_guidance:
        tail_parts.append(f"\nRemediation Guidance: {deps.remediation_guidance}")
    if suffix:
        tail_parts.append(suffix)
    combined_tail = "".join(tail_parts)[:8192]
    return partition_cached_user_prompt(
        shared_prefix,
        combined_tail,
        mode=deps.budget.prompt_cache_mode,
    )


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


def _render_workspace_frame_context(deps: PceGraphDeps) -> str:
    mem_ctx = format_memory_prompt_context(load_workspace_memory(deps.repo_root))
    units = deps.sandbox.load_units()
    if not units:
        return mem_ctx
    ast_graph = build_workspace_ast_graph(units)
    if deps.workflow_mode != PceWorkflowMode.RENOVATE:
        if not ast_graph.symbols:
            return mem_ctx
        return (
            f"{mem_ctx}\nExisting Workspace AST Graph:\n"
            f"{ast_graph.to_compact_brief(max_modules=24)}"
        )
    files = [u.file_path for u in units]
    pub_syms = sorted(
        sym for sym in extract_compiled_symbols(units) if "." not in sym and not sym.startswith("_")
    )[:48]
    rough = deps.inspector.inspect_rough_in(units, permit=deps.permit)
    return (
        f"{mem_ctx}\nBrownfield Workspace Files: {files}\n"
        f"Existing Public Symbols: {pub_syms}\n"
        f"{ast_graph.to_compact_brief(max_modules=24)}\n"
        f"Baseline Inspection:\n{_format_baseline_red_tags(rough)}"
    )


def _build_frame_prompt(deps: PceGraphDeps) -> str:
    base = deps.task_prompt.strip() or "Frame engineering mandate."
    context = _render_workspace_frame_context(deps)
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
    return any(is_polyglot_test_path(rel) for rel in mandate.authorized_files())


def _implementation_scopes(mandate: EngineeringMandate) -> tuple[ModuleWriteScope, ...]:
    return tuple(
        scope
        for scope in mandate.write_scopes
        if any(not is_polyglot_test_path(rel) for rel in scope.owned_files)
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
        memory_ledger=deps.memory_ledger,
        telemetry=tuple(deps.telemetry),
        falsified_paths=tuple(state.world_model.falsified_paths),
    )


def _record_pce_step(
    node: AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult],
    ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    step_id: str,
    summary: str,
) -> None:
    node.record_into_context(ctx, step_id, summary)
    if ctx.deps.progress_callback is not None:
        ctx.deps.progress_callback(node.behavior_kind, step_id, summary)


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
                    languages=_active_languages(ctx.deps),
                ),
                behavior_kind=BehaviorKind.FRAME,
                prompt=_build_frame_prompt(ctx.deps),
                deps=_make_agent_deps(ctx.deps),
                budget=ctx.deps.budget,
            )
            ctx.deps.telemetry.append(tel)
            ctx.deps.mandate = proposal.mandate
            ctx.deps.mandate_was_framed = True
            ctx.deps.prior_draft_mandate = None
            ctx.deps.clarifications = ()

        mandate = _require_mandate(ctx.deps)
        ctx.deps.sandbox = WorkspaceSandbox(
            ctx.deps.repo_root,
            mandate.authorized_files(),
            authorized_manifests=mandate.authorized_manifest_files(),
        )
        ctx.state.problem.objective = mandate.objective
        ctx.state.problem.ambiguities = list(mandate.open_ambiguities)
        step_id = _frame_step_id(ctx.state.trace)
        _record_pce_step(self, ctx, step_id, f"Framed mandate {mandate.mandate_id}")

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
        _record_pce_step(self, ctx, step_id, self.reason[:240])
        if self.escalated_red_tag:
            return End(
                _assemble_halted_result(ctx.state, ctx.deps, PceRunStatus.ESCALATED_AFTER_RED_TAG)
            )
        if self.awaiting_plan_approval:
            return End(
                _assemble_halted_result(
                    ctx.state,
                    ctx.deps,
                    PceRunStatus.HALTED_FOR_PLAN_APPROVAL,
                )
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
    """Phase II (BHV-03 BIND_CODES): Lock JBC v1.2 CodePermit into execution state."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.BIND_CODES

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InspectEnvNode:
        bind_permit_to_state(ctx.state, ctx.deps.permit)
        _record_pce_step(
            self,
            ctx,
            "bind_codes",
            f"Bound {len(ctx.deps.permit.bound_code_ids)} JBC code provisions",
        )
        return InspectEnvNode()


def _build_deterministic_env_observation(
    deps: PceGraphDeps,
    missing: Sequence[DependencyRequirement],
) -> EnvironmentObservation:
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    primary_lang = _primary_language(deps)
    eco = deps.inspector.polyglot_manifest.for_language(primary_lang)
    run_source = (
        deps.inspector.manifest.runtime_roots
        if primary_lang == SupportedLanguage.PYTHON
        else eco.runtime_roots
    )
    dev_source = (
        deps.inspector.manifest.dev_roots
        if primary_lang == SupportedLanguage.PYTHON
        else eco.dev_roots
    )
    run_roots = tuple(sorted(str(r) for r in run_source))[:256]
    dev_roots = tuple(sorted(str(r) for r in dev_source))[:256]
    missing_specs = tuple(d.pep508_spec() for d in missing)[:64]
    prior_ids = tuple(m.mandate_id for m in deps.memory_ledger.sealed_mandates)[:128]
    summary = (
        f"Lang={primary_lang} (Python {py_ver}); runtime_roots={len(run_roots)}, "
        f"dev_roots={len(dev_roots)}, missing_deps={list(missing_specs)}, "
        f"prior_mandates={len(prior_ids)}"
    )[:240]
    return EnvironmentObservation(
        python_version=py_ver,
        declared_runtime_roots=run_roots,
        declared_dev_roots=dev_roots,
        missing_dependencies=missing_specs,
        prior_sealed_mandates=prior_ids,
        summary=summary,
    )


@dataclass
class InspectEnvNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Substrate Grounding (BHV-05 INSPECT_ENV): Sense toolchain, manifests, and memory ledger."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.INSPECT_ENV

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InspectCodeNode:
        mandate = _require_mandate(ctx.deps)
        ctx.deps.memory_ledger = load_workspace_memory(ctx.deps.repo_root)
        ctx.deps.memory_context = format_memory_prompt_context(ctx.deps.memory_ledger)
        missing = compute_missing_dependencies(
            ctx.deps.inspector.manifest,
            mandate.required_dependencies,
            polyglot_manifest=ctx.deps.inspector.polyglot_manifest,
            language=_primary_language(ctx.deps),
        )
        baseline = _build_deterministic_env_observation(ctx.deps, missing)
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
                deps=_make_agent_deps(ctx.deps),
                budget=ctx.deps.budget,
            )
            ctx.deps.telemetry.append(tel)
        else:
            env_obs = baseline
        ctx.state.world_model.observations.append(env_obs.summary)
        _record_pce_step(self, ctx, "inspect_env", env_obs.summary[:240])
        return InspectCodeNode()


@dataclass
class InspectCodeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Epistemic Grounding (BHV-04 INSPECT_CODE): Read-only workspace observation."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.INSPECT_CODE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ConsultNode | DecomposeNode:
        mandate = _require_mandate(ctx.deps)
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
            ctx.deps.baseline_red_tag_summary = _format_baseline_red_tags(rough)
            ctx.state.world_model.observations.append(ctx.deps.baseline_red_tag_summary[:1000])
        raw_inspect_prompt = f"{_format_deps_prompt(ctx.deps, mandate)}\n{ctx.deps.ast_graph_brief}"
        inspect_prompt = raw_inspect_prompt[:60_000]
        obs, tel = await run_bounded_agent(
            build_inspect_agent(ctx.deps.models.inspect_model),
            behavior_kind=BehaviorKind.INSPECT_CODE,
            prompt=inspect_prompt,
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        ctx.state.world_model.observations.append(obs.summary)
        _record_pce_step(self, ctx, "inspect_code", obs.summary[:240])
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
        mandate = _require_mandate(ctx.deps)
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
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        ctx.state.world_model.external_citations.extend(brief.citations)
        ctx.state.world_model.observations.append(brief.summary)
        constraints = "\n".join(f"- {item}" for item in brief.normative_constraints)
        ctx.deps.consultation_context = f"\nNormative Consultation Constraints:\n{constraints}"
        _record_pce_step(self, ctx, "consult", brief.summary[:240])
        return DecomposeNode()


def _format_decompose_prompt(
    mandate: EngineeringMandate,
    ast_brief: str,
    observations: Sequence[str],
) -> str:
    obs_text = "\n".join(f"- {item}" for item in observations[-3:])
    return (
        f"Draft EngineeringMandate:\n{mandate.model_dump_json(indent=2)}\n\n"
        f"{ast_brief}\n\n"
        f"Epistemic Observations:\n{obs_text}"
    )[:60_000]


async def _maybe_refine_scopes_via_decompose(
    ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    mandate: EngineeringMandate,
) -> EngineeringMandate:
    ast_graph = build_workspace_ast_graph(ctx.deps.sandbox.load_units())
    if not ctx.deps.mandate_was_framed or (
        len(ast_graph.symbols) == 0 and len(mandate.write_scopes) <= 1
    ):
        return mandate
    active_model = ctx.deps.models.decompose_model or ctx.deps.models.frame_model
    plan, tel = await run_bounded_agent(
        build_decompose_agent(active_model, languages=_active_languages(ctx.deps)),
        behavior_kind=BehaviorKind.DECOMPOSE,
        prompt=_format_decompose_prompt(
            mandate,
            ctx.deps.ast_graph_brief or ast_graph.to_compact_brief(),
            ctx.state.world_model.observations,
        ),
        deps=_make_agent_deps(ctx.deps),
        budget=ctx.deps.budget,
    )
    ctx.deps.telemetry.append(tel)
    refined = EngineeringMandate.model_validate(
        {**mandate.model_dump(), "write_scopes": plan.write_scopes}
    )
    ctx.deps.mandate = refined
    ctx.deps.sandbox = WorkspaceSandbox(
        ctx.deps.repo_root,
        refined.authorized_files(),
        authorized_manifests=refined.authorized_manifest_files(),
    )
    return refined


@dataclass
class DecomposeNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Topology Partitioning (BHV-09 DECOMPOSE): Lock disjoint ModuleWriteScopes and DAG waves."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.DECOMPOSE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ElicitNode | CheckpointContractNode | CheckpointImplNode:
        mandate = await _maybe_refine_scopes_via_decompose(ctx, _require_mandate(ctx.deps))
        waves = mandate.execution_waves()
        ctx.state.problem.subtasks = [
            f"w{w_idx}:{s.scope_id}(deps={sorted(s.depends_on)}):{sorted(s.owned_files)}"
            for w_idx, wave in enumerate(waves)
            for s in wave
        ]
        _record_pce_step(
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
            and not _has_authorized_tests(mandate)
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
        mandate = _require_mandate(ctx.deps)
        ckpt = ctx.deps.sandbox.create_checkpoint("checkpoint_contract")
        ctx.state.workspace.checkpoints.append(ckpt)
        _record_pce_step(self, ctx, "checkpoint_contract", "Captured pre-contract snapshot")
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
        mandate = _require_mandate(ctx.deps)
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
            deps=_make_agent_deps(ctx.deps),
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
            language=_primary_language(ctx.deps),
        )
        _record_pce_step(self, ctx, "provision", receipt.summary[:240])
        if ctx.deps.workflow_mode == PceWorkflowMode.GREENFIELD:
            return SpecifyNode()
        if _has_authorized_tests(mandate):
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
        mandate = _require_mandate(ctx.deps)
        bundle, tel = await run_bounded_agent(
            build_specify_agent(
                ctx.deps.models.specify_model,
                languages=_active_languages(ctx.deps),
            ),
            behavior_kind=BehaviorKind.SPECIFY,
            prompt=_format_deps_prompt(ctx.deps, mandate),
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.contract_patches)
        _record_pce_step(self, ctx, "specify", bundle.summary[:240])
        return InstrumentNode()


def _test_owning_scopes(mandate: EngineeringMandate) -> tuple[ModuleWriteScope, ...]:
    return tuple(
        s for s in mandate.write_scopes if any(is_polyglot_test_path(p) for p in s.owned_files)
    )


def _clean_test_stem(rel_path: str) -> str:
    stem = Path(rel_path).stem.removeprefix("test_")
    for suffix in ("_test", ".test", ".spec"):
        stem = stem.removesuffix(suffix)
    return stem


def _scope_invariant_match_key(
    inv: DomainInvariant,
    scope: ModuleWriteScope,
    wave_idx_by_scope: dict[str, int],
) -> tuple[int, int, int]:
    wave_idx = wave_idx_by_scope.get(scope.scope_id, 0)
    if inv.verification_symbol in scope.contract_symbols:
        return 2, wave_idx, 100
    haystack = f"{inv.verification_symbol} {inv.statement}"
    sym_hits = sum(
        1 for sym in scope.contract_symbols if re.search(rf"\b{re.escape(sym)}\b", haystack)
    )
    if sym_hits > 0:
        return 1, wave_idx, sym_hits
    stems = {_clean_test_stem(p) for p in scope.owned_files}
    stem_hits = sum(1 for st in stems if st and st.lower() in inv.verification_symbol.lower())
    return 0, stem_hits, wave_idx


def _partition_invariants_by_test_scope(
    mandate: EngineeringMandate,
    test_scopes: Sequence[ModuleWriteScope],
) -> tuple[tuple[ModuleWriteScope, tuple[DomainInvariant, ...]], ...]:
    wave_idx_by_scope = {
        s.scope_id: w_idx for w_idx, wave in enumerate(mandate.execution_waves()) for s in wave
    }
    buckets: dict[str, list[DomainInvariant]] = {s.scope_id: [] for s in test_scopes}
    for inv in mandate.invariants:
        winner = max(
            test_scopes,
            key=lambda s: _scope_invariant_match_key(inv, s, wave_idx_by_scope),
        )
        buckets[winner.scope_id].append(inv)
    return tuple((s, tuple(buckets[s.scope_id])) for s in test_scopes if buckets[s.scope_id])


async def _run_scoped_instrument_slice(
    deps: PceGraphDeps,
    base_prompt: str,
    scope: ModuleWriteScope,
    invariants: tuple[DomainInvariant, ...],
) -> tuple[InstrumentationSuiteBundle, AgentInvocationTelemetry]:
    test_files = sorted(p for p in scope.owned_files if is_polyglot_test_path(p))
    inv_list = "; ".join(f"{i.invariant_id} ({i.verification_symbol})" for i in invariants)
    footer = (
        f"\nActive Instrumentation Scope: {scope.scope_id} owning test file(s) {test_files} ONLY.\n"
        f"Mandated Invariants For This Scope ONLY: {inv_list}.\n"
        f"Emit ONLY {test_files} defining those exact verification_symbol functions; other "
        "scopes' invariants are instrumented concurrently by sibling workers."
    )
    scoped_prompt = partition_cached_user_prompt(
        base_prompt[:57_344],
        footer[:8192],
        mode=deps.budget.prompt_cache_mode,
    )
    return await run_bounded_agent(
        build_instrument_agent(
            deps.models.instrument_model,
            languages=_active_languages(deps, scope=scope),
        ),
        behavior_kind=BehaviorKind.INSTRUMENT,
        prompt=scoped_prompt,
        deps=_make_agent_deps(deps, scope=scope, scoped_invariants=invariants),
        budget=deps.budget,
    )


async def _run_parallel_instrument_scopes(
    deps: PceGraphDeps,
    base_prompt: str,
    assignments: Sequence[tuple[ModuleWriteScope, tuple[DomainInvariant, ...]]],
) -> tuple[tuple[FilePatchArtifact, ...], str]:
    tasks: list[asyncio.Task[tuple[InstrumentationSuiteBundle, AgentInvocationTelemetry]]] = []
    async with asyncio.TaskGroup() as tg:
        for scope, invs in assignments:
            tasks.append(
                tg.create_task(_run_scoped_instrument_slice(deps, base_prompt, scope, invs))
            )
    patches: list[FilePatchArtifact] = []
    summaries: list[str] = []
    for finished in tasks:
        bundle, tel = finished.result()
        deps.telemetry.append(tel)
        patches.extend(bundle.test_patches)
        summaries.append(bundle.summary)
    return tuple(patches), " | ".join(summaries)


@dataclass
class InstrumentNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Phase III (BHV-15 INSTRUMENT): Emit pytest and Hypothesis falsification suites."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.INSTRUMENT

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> CheckpointImplNode:
        mandate = _require_mandate(ctx.deps)
        renovate_suffix = (
            "\nBrownfield Renovation Mode: Preserve/lock existing observable "
            "public behavior from src/... snapshots while defining every mandated "
            "verification_symbol under full JBC v1.2 test compliance."
            if ctx.deps.workflow_mode == PceWorkflowMode.RENOVATE
            else ""
        )
        test_scopes = _test_owning_scopes(mandate)
        assignments = (
            _partition_invariants_by_test_scope(mandate, test_scopes)
            if len(test_scopes) >= 2
            else ()
        )
        if len(assignments) >= 2:
            raw_base = _format_deps_prompt(ctx.deps, mandate, include_guidance=True)
            base_prompt = f"{raw_base}{renovate_suffix}"[:57_344]
            test_patches, summary = await _run_parallel_instrument_scopes(
                ctx.deps, base_prompt, assignments
            )
        else:
            bundle, tel = await run_bounded_agent(
                build_instrument_agent(
                    ctx.deps.models.instrument_model,
                    languages=_active_languages(ctx.deps),
                ),
                behavior_kind=BehaviorKind.INSTRUMENT,
                prompt=_partition_deps_prompt(
                    ctx.deps,
                    mandate,
                    suffix=renovate_suffix,
                    include_guidance=True,
                ),
                deps=_make_agent_deps(ctx.deps),
                budget=ctx.deps.budget,
            )
            ctx.deps.telemetry.append(tel)
            test_patches, summary = bundle.test_patches, bundle.summary
        ctx.deps.remediation_guidance = ""
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, test_patches)
        _record_pce_step(self, ctx, "instrument", summary[:240])
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
        _record_pce_step(self, ctx, "checkpoint_impl", "Captured pre-implementation snapshot")
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
            build_synthesize_agent(
                ctx.deps.models.synthesize_model,
                languages=_active_languages(ctx.deps),
            ),
            behavior_kind=BehaviorKind.SYNTHESIZE,
            prompt=_partition_deps_prompt(ctx.deps, mandate, include_guidance=True),
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.implementation_patches)
        _record_pce_step(self, ctx, "synthesize", bundle.summary[:240])
        return VerifyStaticNode()


def _refactor_baseline_suffix(deps: PceGraphDeps) -> str:
    if not deps.baseline_red_tag_summary:
        return ""
    return f"\nBaseline JBC RED_TAG Findings:\n{deps.baseline_red_tag_summary}"


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
            build_refactor_agent(active_model, languages=_active_languages(ctx.deps)),
            behavior_kind=BehaviorKind.REFACTOR,
            prompt=_partition_deps_prompt(
                ctx.deps,
                mandate,
                suffix=_refactor_baseline_suffix(ctx.deps),
                include_guidance=True,
            ),
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        _apply_patches_to_state(ctx.state, ctx.deps.sandbox, bundle.refactored_patches)
        _record_pce_step(self, ctx, "refactor", bundle.summary[:240])
        return VerifyStaticNode()


async def _run_scoped_slice(
    deps: PceGraphDeps,
    mandate: EngineeringMandate,
    scope: ModuleWriteScope,
) -> tuple[ModuleWriteScope, ConstructionPatchBundle, AgentInvocationTelemetry]:
    slice_footer = (
        f"\nActive Slice Scope: {scope.scope_id} owning {sorted(scope.owned_files)} "
        f"for symbols {list(scope.contract_symbols)}."
    )
    scoped_langs = _active_languages(deps, scope=scope)
    if deps.workflow_mode == PceWorkflowMode.RENOVATE:
        ref_suffix = f"{_refactor_baseline_suffix(deps)}{slice_footer}"
        ref_model = deps.models.refactor_model or deps.models.synthesize_model
        ref_bundle, ref_tel = await run_bounded_agent(
            build_refactor_agent(ref_model, languages=scoped_langs),
            behavior_kind=BehaviorKind.DELEGATE,
            prompt=_partition_deps_prompt(
                deps,
                mandate,
                suffix=ref_suffix,
                include_guidance=True,
            ),
            deps=_make_agent_deps(deps, scope=scope),
            budget=deps.budget,
        )
        adapted = ConstructionPatchBundle(
            implementation_patches=ref_bundle.refactored_patches,
            summary=ref_bundle.summary,
        )
        return scope, adapted, ref_tel
    bundle, tel = await run_bounded_agent(
        build_synthesize_agent(deps.models.synthesize_model, languages=scoped_langs),
        behavior_kind=BehaviorKind.DELEGATE,
        prompt=_partition_deps_prompt(
            deps,
            mandate,
            suffix=slice_footer,
            include_guidance=True,
        ),
        deps=_make_agent_deps(deps, scope=scope),
        budget=deps.budget,
    )
    return scope, bundle, tel


def _implementation_waves(
    mandate: EngineeringMandate,
) -> tuple[tuple[ModuleWriteScope, ...], ...]:
    impl_scopes = _implementation_scopes(mandate)
    impl_ids = {s.scope_id for s in impl_scopes}
    projected = tuple(
        ModuleWriteScope(
            scope_id=s.scope_id,
            owned_files=s.owned_files,
            contract_symbols=s.contract_symbols,
            depends_on=frozenset(s.depends_on & impl_ids),
        )
        for s in impl_scopes
    )
    return compute_scope_execution_waves(projected)


async def _run_single_delegation_wave(
    deps: PceGraphDeps,
    state: AgentExecutionState,
    mandate: EngineeringMandate,
    wave_scopes: Sequence[ModuleWriteScope],
) -> list[tuple[ModuleWriteScope, ConstructionPatchBundle]]:
    tasks: list[
        asyncio.Task[tuple[ModuleWriteScope, ConstructionPatchBundle, AgentInvocationTelemetry]]
    ] = []
    async with asyncio.TaskGroup() as tg:
        for scope in wave_scopes:
            state.world_model.active_delegations.add(scope.scope_id)
            tasks.append(tg.create_task(_run_scoped_slice(deps, mandate, scope)))
    wave_bundles: list[tuple[ModuleWriteScope, ConstructionPatchBundle]] = []
    for finished in tasks:
        scope, bundle, tel = finished.result()
        deps.telemetry.append(tel)
        wave_bundles.append((scope, bundle))
    return wave_bundles


@dataclass
class DelegateNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Topological Subagent Dispatch (BHV-10 DELEGATE): Wave-ordered TaskGroup fan-out."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.DELEGATE

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> ReconcileNode:
        mandate = _require_mandate(ctx.deps)
        waves = _implementation_waves(mandate)
        all_delegated: list[tuple[ModuleWriteScope, ConstructionPatchBundle]] = []
        for idx, wave_scopes in enumerate(waves):
            wave_bundles = await _run_single_delegation_wave(
                ctx.deps, ctx.state, mandate, wave_scopes
            )
            all_delegated.extend(wave_bundles)
            if idx + 1 < len(waves):
                _verify_and_flatten_delegated_patches(all_delegated)
                wave_patches = _verify_and_flatten_delegated_patches(wave_bundles)
                _apply_patches_to_state(ctx.state, ctx.deps.sandbox, wave_patches)

        ctx.deps.delegated_bundles = all_delegated
        _record_pce_step(
            self,
            ctx,
            "delegate",
            f"Executed {len(all_delegated)} scoped worker(s) across {len(waves)} wave(s)",
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
        _record_pce_step(
            self,
            ctx,
            "reconcile",
            f"Reconciled {len(merged)} disjoint artifact patch(es)",
        )
        return VerifyStaticNode()


def _active_ratchet_scope(deps: PceGraphDeps) -> DifferentialScope:
    mandate = _require_mandate(deps)
    scoped_paths = frozenset(set(mandate.authorized_files()) | set(deps.sandbox.modified_files))
    return DifferentialScope(changed_paths=scoped_paths)


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
                _active_ratchet_scope(ctx.deps),
                permit=ctx.deps.permit,
            ).report
            if ctx.deps.budget.ratchet_to_scope
            else ctx.deps.inspector.inspect_rough_in(units, permit=ctx.deps.permit)
        )
        ctx.deps.latest_report = report
        record_inspection_to_state(ctx.state, report, stage="rough_in")
        _record_pce_step(
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
                _active_ratchet_scope(ctx.deps),
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
        _record_pce_step(
            self,
            ctx,
            "verify_dynamic",
            f"Final ruling: {report.ruling} ({len(report.unwaived_violations())} violations)",
        )
        if not report.is_occupiable:
            return RevertNode(stage="final")
        return CertifyNode()


_MYPY_TEST_ERR_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)tests/[^\s:]+:\d+:\s*error:")
_MYPY_SRC_ERR_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)src/[^\s:]+:\d+:\s*error:")


def _summarize_red_tag_findings(report: InspectionReport | None, stage: str) -> str:
    if report is None:
        return f"{stage}:unknown_red_tag"
    items = [
        f"{f.code_id}@{f.location.file_path}:{f.location.line} ({f.message})"
        for f in report.unwaived_violations()[:6]
    ]
    return f"{stage}_red_tag: " + "; ".join(items)


def _capture_failed_src_snapshot(sandbox: WorkspaceSandbox) -> str:
    blocks: list[str] = []
    for rel in sorted(sandbox.modified_files):
        if is_polyglot_test_path(rel) or rel in sandbox.authorized_manifests:
            continue
        try:
            content = sandbox.read_file(rel)
        except FileNotFoundError, IsADirectoryError:
            continue
        blocks.append(f"--- FAILED CANDIDATE {rel} ---\n{content[:8000]}")
    return "\n".join(blocks)[:20_000]


def _finding_targets_tests_only(code_id: str, file_path: str, message: str) -> bool:
    if is_polyglot_test_path(file_path):
        return True
    if code_id == "JBC-VRF-005":
        return (
            _MYPY_TEST_ERR_RE.search(message) is not None
            and _MYPY_SRC_ERR_RE.search(message) is None
        )
    return False


def _is_test_only_static_red_tag(
    report: InspectionReport | None,
    mandate: EngineeringMandate,
) -> bool:
    if report is None or not _has_authorized_tests(mandate):
        return False
    violations = report.unwaived_violations()
    if not violations:
        return False
    return all(
        _finding_targets_tests_only(f.code_id, f.location.file_path, f.message) for f in violations
    )


@dataclass
class RevertNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Mandatory Rollback (BHV-20 REVERT): Restore checkpoint and record negative knowledge."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.REVERT
    stage: str

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> HypothesizeNode | ElicitNode:
        failed_excerpt = _capture_failed_src_snapshot(ctx.deps.sandbox)
        restored = ctx.deps.sandbox.restore_checkpoint(_IMPL_CHECKPOINT_ID)
        ctx.state.workspace.modified_artifacts = set(restored)
        summary = _summarize_red_tag_findings(ctx.deps.latest_report, self.stage)
        ctx.state.world_model.falsified_paths.append(summary[:512])
        _record_pce_step(self, ctx, "revert", f"Reverted to {_IMPL_CHECKPOINT_ID} on RED_TAG")

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
        mandate = _require_mandate(ctx.deps)
        base_prompt = _format_deps_prompt(ctx.deps, mandate)
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
            deps=_make_agent_deps(ctx.deps),
            budget=ctx.deps.budget,
        )
        ctx.deps.telemetry.append(tel)
        ctx.deps.remediation_guidance = hypothesis.remediation_plan
        ctx.state.world_model.active_hypotheses.append(hypothesis.remediation_plan)
        _record_pce_step(self, ctx, "hypothesize", hypothesis.remediation_plan[:240])
        return CurateMemoryNode()


async def _run_memory_compaction(
    ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    mandate: EngineeringMandate,
) -> CuratedMemoryDigest:
    if len(ctx.state.world_model.observations) < ctx.deps.budget.memory_compaction_threshold:
        return compact_world_model_deterministically(ctx.state, mandate)
    active_model = ctx.deps.models.curate_memory_model or ctx.deps.models.inspect_model
    inv_ids = [inv.invariant_id for inv in mandate.invariants]
    prompt = (
        f"Mandate {mandate.mandate_id} Active Invariant IDs (MUST retain all): {inv_ids}\n"
        f"Prior Falsified Paths (MUST retain verbatim): {ctx.state.world_model.falsified_paths}\n"
        f"Active Hypotheses: {ctx.state.world_model.active_hypotheses}\n"
        f"Raw Observations ({len(ctx.state.world_model.observations)}): "
        f"{ctx.state.world_model.observations}"
    )[:60_000]
    digest, tel = await run_bounded_agent(
        build_curate_memory_agent(active_model),
        behavior_kind=BehaviorKind.CURATE_MEMORY,
        prompt=prompt,
        deps=_make_agent_deps(
            ctx.deps,
            prior_falsified_paths=tuple(ctx.state.world_model.falsified_paths),
        ),
        budget=ctx.deps.budget,
    )
    ctx.deps.telemetry.append(tel)
    return digest


@dataclass
class CurateMemoryNode(AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult]):
    """Working-Memory Compaction (BHV-07 CURATE_MEMORY): Evict stale noise, keep negations."""

    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CURATE_MEMORY

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, PceGraphDeps],
    ) -> InstrumentNode | CheckpointImplNode:
        mandate = _require_mandate(ctx.deps)
        digest = await _run_memory_compaction(ctx, mandate)
        apply_curated_digest_to_state(ctx.state, mandate, digest)
        ctx.deps.curated_notes.extend(digest.architectural_notes)
        _record_pce_step(self, ctx, "curate_memory", digest.summary[:240])
        if _is_test_only_static_red_tag(ctx.deps.latest_report, mandate):
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
        mandate = _require_mandate(ctx.deps)
        _record_pce_step(self, ctx, "certify", f"Certified occupancy for {mandate.mandate_id}")

        units = ctx.deps.sandbox.load_units()
        is_ratchet = ctx.deps.budget.ratchet_to_scope
        ratchet_scope = _active_ratchet_scope(ctx.deps)
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
