"""Pillar 4: Pydantic-Graph PCE workflow builder and high-level PceWorkflowOrchestrator."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_graph import Graph, GraphBuilder

from jezz.agents.nodes import ElicitationCallback, FrameNode, PceGraphDeps, PceInteractionHooks
from jezz.agents.schemas import (
    ElicitationTicket,
    PceRuntimeBudget,
    PceWorkflowMode,
    PceWorkflowResult,
)
from jezz.agents.specialists import PceModelSuite
from jezz.agents.workspace import (
    DynamicGatePort,
    InProcessGateRunner,
    WorkspaceSandbox,
)
from jezz.behaviors.runtime import AgentExecutionState
from jezz.codes.inspector import BuildingInspector
from jezz.codes.permits import CodePermit
from jezz.guards import (
    InstanceGuard,
    LoopBudget,
    NonEmptyStr,
    ShortText,
    jezz_callable,
)
from jezz.methodology.mandate import EngineeringMandate


@dataclass(frozen=True)
class _WorkflowLaunchSpec:
    """Internal launch configuration passed into ``PceWorkflowOrchestrator._run_workflow``."""

    repo_root: Path
    workflow_mode: PceWorkflowMode = PceWorkflowMode.GREENFIELD
    mandate: EngineeringMandate | None = None
    task_prompt: str = ""
    permit: CodePermit | None = None
    ticket: ElicitationTicket | None = None
    clarifications: tuple[str, ...] = ()


@jezz_callable
def build_pce_workflow_graph() -> Annotated[Graph, InstanceGuard(Graph)]:
    """Construct the compiled pydantic-graph state machine for the 5-phase PCE lifecycle."""
    builder = GraphBuilder(
        state_type=AgentExecutionState,
        deps_type=PceGraphDeps,
        input_type=FrameNode,
        output_type=PceWorkflowResult,
    )
    builder.add(builder.edge_from(builder.start_node).to(FrameNode))
    builder.add_reachable(FrameNode)
    return builder.build()


def _coerce_interaction_hooks(
    raw: ElicitationCallback | PceInteractionHooks | None,
) -> PceInteractionHooks:
    if isinstance(raw, PceInteractionHooks):
        return raw
    return PceInteractionHooks(on_elicit=raw)


def _rollback_aborted_sandbox(sandbox: WorkspaceSandbox) -> None:
    for ckpt_id in ("checkpoint_contract", "checkpoint_impl"):
        if sandbox.has_checkpoint(ckpt_id):
            sandbox.restore_checkpoint(ckpt_id)
            return


class PceWorkflowOrchestrator:
    """Single-duty orchestrator executing greenfield, brownfield, and resumable PCE runs."""

    @jezz_callable
    def __init__(
        self,
        models: Annotated[PceModelSuite, InstanceGuard(PceModelSuite)],
        *,
        inspector: BuildingInspector | None = None,
        gate_runner: Annotated[DynamicGatePort, InstanceGuard(DynamicGatePort)] | None = None,
        budget: PceRuntimeBudget | None = None,
        elicitation_callback: ElicitationCallback | PceInteractionHooks | None = None,
    ) -> None:
        self._models = models
        self._inspector = inspector
        self._gate_runner: DynamicGatePort = gate_runner or InProcessGateRunner()
        self._budget = budget or PceRuntimeBudget()
        self._interaction: PceInteractionHooks = _coerce_interaction_hooks(elicitation_callback)

    async def _run_workflow(self, spec: _WorkflowLaunchSpec) -> PceWorkflowResult:
        resolved_root = spec.repo_root.resolve()
        active_inspector = self._inspector or BuildingInspector.from_workspace(resolved_root)
        active_permit = spec.permit or CodePermit.full_book(permit_id="PERMIT-PCE-RUNTIME")
        initial_authorized = (
            spec.mandate.authorized_files() if spec.mandate is not None else frozenset()
        )
        prior_draft = spec.ticket.mandate if spec.ticket is not None else None
        prior_telemetry = list(spec.ticket.prior_telemetry) if spec.ticket is not None else []
        deps = PceGraphDeps(
            repo_root=resolved_root,
            sandbox=WorkspaceSandbox(resolved_root, initial_authorized),
            inspector=active_inspector,
            gate_runner=self._gate_runner,
            models=self._models,
            permit=active_permit,
            budget=self._budget,
            remediation_loop=LoopBudget(max_steps=self._budget.max_remediation_cycles),
            workflow_mode=spec.workflow_mode,
            elicitation_loop=LoopBudget(max_steps=self._budget.max_elicitation_rounds),
            task_prompt=spec.task_prompt,
            mandate=spec.mandate,
            prior_draft_mandate=prior_draft,
            clarifications=spec.clarifications,
            elicitation_callback=self._interaction.on_elicit,
            progress_callback=self._interaction.on_progress,
            plan_only=self._interaction.plan_only,
            telemetry=prior_telemetry,
        )
        workflow = build_pce_workflow_graph()
        state = AgentExecutionState()
        if spec.ticket is not None:
            state.trace.extend(spec.ticket.prior_trace)
        try:
            outcome: object = await workflow.run(state=state, deps=deps, inputs=FrameNode())
        except BaseException:
            _rollback_aborted_sandbox(deps.sandbox)
            raise
        if not isinstance(outcome, PceWorkflowResult):
            msg = f"Unexpected workflow output type: {type(outcome).__name__}."
            raise RuntimeError(msg)
        return outcome

    @jezz_callable
    async def execute_mandate(
        self,
        repo_root: Path,
        mandate: EngineeringMandate,
        *,
        permit: CodePermit | None = None,
    ) -> PceWorkflowResult:
        """Execute a greenfield ``EngineeringMandate`` through all five PCE phases."""
        return await self._run_workflow(
            _WorkflowLaunchSpec(
                repo_root=repo_root,
                workflow_mode=PceWorkflowMode.GREENFIELD,
                mandate=mandate,
                permit=permit,
            )
        )

    @jezz_callable
    async def execute_prompt(
        self,
        repo_root: Path,
        task_prompt: NonEmptyStr,
        *,
        permit: CodePermit | None = None,
    ) -> PceWorkflowResult:
        """Frame ``task_prompt`` via BHV-01 FRAME and execute greenfield synthesis or ELICIT."""
        return await self._run_workflow(
            _WorkflowLaunchSpec(
                repo_root=repo_root,
                workflow_mode=PceWorkflowMode.GREENFIELD,
                task_prompt=task_prompt,
                permit=permit,
            )
        )

    @jezz_callable
    async def renovate_mandate(
        self,
        repo_root: Path,
        mandate: EngineeringMandate,
        *,
        permit: CodePermit | None = None,
    ) -> PceWorkflowResult:
        """Renovate a brownfield workspace under ``mandate`` via BHV-17 REFACTOR."""
        return await self._run_workflow(
            _WorkflowLaunchSpec(
                repo_root=repo_root,
                workflow_mode=PceWorkflowMode.RENOVATE,
                mandate=mandate,
                permit=permit,
            )
        )

    @jezz_callable
    async def renovate_prompt(
        self,
        repo_root: Path,
        task_prompt: NonEmptyStr,
        *,
        permit: CodePermit | None = None,
    ) -> PceWorkflowResult:
        """Frame ``task_prompt`` against existing workspace code and renovate via BHV-17."""
        return await self._run_workflow(
            _WorkflowLaunchSpec(
                repo_root=repo_root,
                workflow_mode=PceWorkflowMode.RENOVATE,
                task_prompt=task_prompt,
                permit=permit,
            )
        )

    @jezz_callable
    async def resume_elicitation(
        self,
        repo_root: Path,
        ticket: ElicitationTicket,
        clarifications: Annotated[Sequence[ShortText], Field(min_length=1, max_length=64)],
        *,
        permit: CodePermit | None = None,
    ) -> PceWorkflowResult:
        """Resume a halted ``ElicitationTicket`` across ``ELICIT -> FRAME`` with answers."""
        cleaned = tuple(item.strip() for item in clarifications if item.strip())
        if not cleaned:
            msg = "At least one non-empty clarification is required to resume BHV-02 ELICIT."
            raise ValueError(msg)
        return await self._run_workflow(
            _WorkflowLaunchSpec(
                repo_root=repo_root,
                workflow_mode=ticket.workflow_mode,
                task_prompt=ticket.original_prompt,
                permit=permit,
                ticket=ticket,
                clarifications=cleaned,
            )
        )
