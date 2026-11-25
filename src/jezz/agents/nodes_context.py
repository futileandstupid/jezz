"""Shared run context, prompt assembly, and epistemic helpers for PCE graph nodes."""

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_graph import GraphRunContext

from jezz.agents.caching import CachedUserPrompt, partition_cached_user_prompt
from jezz.agents.memory import format_memory_prompt_context, load_workspace_memory
from jezz.agents.polyglot_prompts import (
    code_fence_for_path,
    detect_workspace_languages,
    is_polyglot_test_path,
)
from jezz.agents.provision import InProcessProvisionRunner, ProvisionPort
from jezz.agents.schemas import (
    AgentInvocationTelemetry,
    ConstructionPatchBundle,
    ElicitationTicket,
    EnvironmentObservation,
    FilePatchArtifact,
    PceRunStatus,
    PceRuntimeBudget,
    PceWorkflowMode,
    PceWorkflowResult,
    WorkspaceMemoryLedger,
)
from jezz.agents.specialists import PceModelSuite
from jezz.agents.workspace import DynamicGatePort, JezzAgentDeps, WorkspaceSandbox
from jezz.behaviors.graph import verify_behavior_graph
from jezz.behaviors.runtime import (
    AgentExecutionState,
    AtomicBehaviorGraphNode,
    ExecutionStep,
    trace_to_behavior_graph,
)
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.ast_graph import build_workspace_ast_graph
from jezz.codes.inspector import BuildingInspector
from jezz.codes.permits import CodePermit, GateAttestation, InspectionReport
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.guards import (
    BoundedText,
    BudgetExceededError,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    LoopBudget,
    ShortText,
    SourceCodeText,
    jezz_callable,
)
from jezz.methodology.dossier import extract_compiled_symbols
from jezz.methodology.lot_line import assess_mandate_lot_line
from jezz.methodology.mandate import (
    DependencyRequirement,
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
)

IMPL_CHECKPOINT_ID = "checkpoint_impl"

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
    lot_line_reviewed: bool = False
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


type GuardedPceDeps = Annotated[PceGraphDeps, InstanceGuard(PceGraphDeps)]
type GuardedGraphCtx = Annotated[
    GraphRunContext[AgentExecutionState, PceGraphDeps],
    InstanceGuard(GraphRunContext),
]
type GuardedPceNode = Annotated[
    AtomicBehaviorGraphNode[PceGraphDeps, PceWorkflowResult],
    InstanceGuard(AtomicBehaviorGraphNode),
]


@jezz_callable
def require_mandate(deps: GuardedPceDeps) -> EngineeringMandate:
    """Return active ``EngineeringMandate`` from ``deps`` or raise ``RuntimeError``."""
    if deps.mandate is None:
        msg = "EngineeringMandate must be established at BHV-01 FRAME before downstream nodes."
        raise RuntimeError(msg)
    return deps.mandate


@jezz_callable
def make_agent_deps(
    deps: GuardedPceDeps,
    *,
    scope: ModuleWriteScope | None = None,
    scoped_invariants: Annotated[tuple[DomainInvariant, ...], Field(max_length=256)] | None = None,
    prior_falsified_paths: Annotated[tuple[ShortText, ...], Field(max_length=256)] = (),
) -> Annotated[JezzAgentDeps, InstanceGuard(JezzAgentDeps)]:
    """Construct ``JezzAgentDeps`` scoped optionally to ``scope`` and ``scoped_invariants``."""
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


@jezz_callable
def apply_patches_to_state(
    state: AgentExecutionState,
    sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
    patches: Annotated[Sequence[FilePatchArtifact], Field(max_length=256)],
) -> None:
    """Write authorized ``patches`` into ``sandbox`` and record paths in ``state``."""
    for patch in patches:
        contained = sandbox.write_authorized_file(patch.file_path, patch.source_code)
        state.workspace.modified_artifacts.add(contained.relative_posix)


def _existing_authorized_files(sandbox: WorkspaceSandbox) -> list[str]:
    return [
        rel
        for rel in sorted(sandbox.authorized_files)
        if ContainedPath.from_parts(sandbox.repo_root, rel).resolved_path.is_file()
    ]


@jezz_callable
def active_languages(
    deps: GuardedPceDeps,
    *,
    scope: ModuleWriteScope | None = None,
) -> frozenset[SupportedLanguage]:
    """Return active ``SupportedLanguage`` set across workspace or ``scope``."""
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


@jezz_callable
def primary_language(deps: GuardedPceDeps) -> SupportedLanguage:
    """Return primary ``SupportedLanguage`` governing ``deps``."""
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


@jezz_callable
def format_deps_prompt(
    deps: GuardedPceDeps,
    mandate: EngineeringMandate,
    *,
    include_guidance: bool = False,
) -> SourceCodeText:
    """Render shared Mandate + inline workspace snapshot prompt text."""
    return _format_mandate_prompt(
        mandate,
        deps.sandbox,
        deps.remediation_guidance if include_guidance else "",
        extra_context=f"{deps.memory_context}{deps.consultation_context}",
    )[:524_288]


@jezz_callable
def partition_deps_prompt(
    deps: GuardedPceDeps,
    mandate: EngineeringMandate,
    *,
    suffix: BoundedText = "",
    include_guidance: bool = False,
) -> CachedUserPrompt:
    """Partition Mandate + snapshot prefix and guidance/slice suffix around a CachePoint."""
    shared_prefix = format_deps_prompt(deps, mandate, include_guidance=False)[:57_344]
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


@jezz_callable
def format_baseline_red_tags(report: InspectionReport) -> BoundedText:
    """Summarize baseline unwaived JBC violations for brownfield renovation prompts."""
    violations = report.unwaived_violations()
    if not violations:
        return "0 baseline JBC violations."
    lines = [
        f"- {f.code_id} ({f.location.file_path}:L{f.location.line}): "
        f"{f.message} -> Fix: {f.remediation}"
        for f in violations[:24]
    ]
    return (f"{len(violations)} baseline JBC violation(s):\n" + "\n".join(lines))[:65_536]


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
        f"Baseline Inspection:\n{format_baseline_red_tags(rough)}"
    )


@jezz_callable
def build_frame_prompt(deps: GuardedPceDeps) -> BoundedText:
    """Assemble the BHV-01 FRAME prompt including workspace AST and optional clarifications."""
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


@jezz_callable
def frame_step_id(
    trace: Annotated[Sequence[ExecutionStep], Field(max_length=4096)],
) -> IdentifierStr:
    """Compute canonical ``step_id`` for BHV-01 FRAME across initial and resumed turns."""
    elicit_count = sum(1 for step in trace if step.kind == BehaviorKind.ELICIT)
    if elicit_count == 0:
        return "frame"
    if elicit_count == 1:
        return "reframe_resolved"
    return f"reframe_resolved_{elicit_count}"


@jezz_callable
def try_in_process_elicitation(deps: GuardedPceDeps) -> bool:
    """Invoke ``deps.elicitation_callback`` if configured and within ``elicitation_loop``."""
    callback = deps.elicitation_callback
    if callback is None:
        return False
    try:
        deps.elicitation_loop.tick()
    except BudgetExceededError:
        return False
    mandate = require_mandate(deps)
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


@jezz_callable
def evaluate_preflight_lot_line(
    state: AgentExecutionState,
    deps: GuardedPceDeps,
    mandate: EngineeringMandate,
) -> ShortText | None:
    """Evaluate JBC-COG-006 lot-line shear and return an elicitation prompt when unapproved."""
    assessment = assess_mandate_lot_line(
        mandate,
        deps.sandbox.load_units(),
        manifest=deps.inspector.manifest,
        polyglot_manifest=deps.inspector.polyglot_manifest,
        profile=deps.permit.profile,
    )
    state.world_model.observations.append(
        f"LotLine({assessment.disposition}): {assessment.rationale}"[:512]
    )
    already_decomposed = any(step.kind == BehaviorKind.DECOMPOSE for step in state.trace)
    if (
        not assessment.requires_boundary_review
        or not deps.mandate_was_framed
        or deps.lot_line_reviewed
        or already_decomposed
    ):
        return None

    deps.lot_line_reviewed = True
    question = (
        f"Lot-line shear ({assessment.disposition}): {assessment.rationale} "
        "Confirm compartment/package boundary placement."
    )[:512]
    if question not in mandate.open_ambiguities:
        updated = mandate.model_copy(
            update={"open_ambiguities": (*mandate.open_ambiguities, question)}
        )
        deps.mandate = updated
        state.problem.ambiguities = list(updated.open_ambiguities)
    return question


@jezz_callable
def has_authorized_tests(mandate: EngineeringMandate) -> bool:
    """Return True iff ``mandate`` authorizes at least one polyglot test file."""
    return any(is_polyglot_test_path(rel) for rel in mandate.authorized_files())


@jezz_callable
def implementation_scopes(mandate: EngineeringMandate) -> tuple[ModuleWriteScope, ...]:
    """Return ``ModuleWriteScope`` entries owning at least one non-test source module."""
    return tuple(
        scope
        for scope in mandate.write_scopes
        if any(not is_polyglot_test_path(rel) for rel in scope.owned_files)
    )


@jezz_callable
def assemble_halted_result(
    state: AgentExecutionState,
    deps: GuardedPceDeps,
    status: PceRunStatus,
    *,
    ticket: ElicitationTicket | None = None,
) -> PceWorkflowResult:
    """Build a halted or escalated ``PceWorkflowResult`` with verified induced BehaviorGraph."""
    mandate = require_mandate(deps)
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


@jezz_callable
def record_pce_step(
    node: GuardedPceNode,
    ctx: GuardedGraphCtx,
    step_id: IdentifierStr,
    summary: BoundedText,
) -> None:
    """Record ``step_id`` into ``ctx.state`` and notify ``progress_callback`` if attached."""
    node.record_into_context(ctx, step_id, summary)
    if ctx.deps.progress_callback is not None:
        ctx.deps.progress_callback(node.behavior_kind, step_id, summary)


@jezz_callable
def build_deterministic_env_observation(
    deps: GuardedPceDeps,
    missing: Annotated[Sequence[DependencyRequirement], Field(max_length=128)],
) -> EnvironmentObservation:
    """Synthesize deterministic ``EnvironmentObservation`` from active workspace manifests."""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    langs = sorted(active_languages(deps), key=str)
    prim_lang = primary_language(deps)
    run_pool: set[str] = set()
    dev_pool: set[str] = set()
    for lang in langs:
        eco = deps.inspector.polyglot_manifest.for_language(lang)
        run_pool |= (
            set(deps.inspector.manifest.runtime_roots)
            if lang == SupportedLanguage.PYTHON
            else set(eco.runtime_roots)
        )
        dev_pool |= (
            set(deps.inspector.manifest.dev_roots)
            if lang == SupportedLanguage.PYTHON
            else set(eco.dev_roots)
        )
    run_roots = tuple(sorted(run_pool))[:256]
    dev_roots = tuple(sorted(dev_pool))[:256]
    missing_specs = tuple(d.ecosystem_spec(str(d.infer_ecosystem(prim_lang))) for d in missing)[:64]
    prior_ids = tuple(m.mandate_id for m in deps.memory_ledger.sealed_mandates)[:128]
    lang_label = "+".join(str(lang) for lang in langs)
    summary = (
        f"Lang={lang_label} (Python {py_ver}); runtime_roots={len(run_roots)}, "
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


@jezz_callable
def format_decompose_prompt(
    mandate: EngineeringMandate,
    ast_brief: BoundedText,
    observations: Annotated[Sequence[BoundedText], Field(max_length=512)],
) -> BoundedText:
    """Render BHV-09 DECOMPOSE prompt combining draft mandate, AST graph, and observations."""
    obs_text = "\n".join(f"- {item}" for item in observations[-3:])
    return (
        f"Draft EngineeringMandate:\n{mandate.model_dump_json(indent=2)}\n\n"
        f"{ast_brief}\n\n"
        f"Epistemic Observations:\n{obs_text}"
    )[:60_000]
