"""Pillar 4: Pydantic-Graph PCE workflow builder and high-level PceWorkflowOrchestrator."""

from pathlib import Path
from typing import Annotated

from pydantic_graph import Graph, GraphBuilder

from jezz.agents.nodes import FrameNode, PceGraphDeps
from jezz.agents.schemas import PceRuntimeBudget, PceWorkflowResult
from jezz.agents.specialists import PceModelSuite
from jezz.agents.workspace import (
    DynamicGatePort,
    InProcessGateRunner,
    WorkspaceSandbox,
)
from jezz.behaviors.runtime import AgentExecutionState
from jezz.codes.inspector import BuildingInspector
from jezz.codes.permits import CodePermit
from jezz.guards import InstanceGuard, LoopBudget, NonEmptyStr, jezz_callable
from jezz.methodology.mandate import EngineeringMandate


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


class PceWorkflowOrchestrator:
    """Single-duty orchestrator executing PCE mandates and prompts via Pydantic AI + Graph."""

    @jezz_callable
    def __init__(
        self,
        models: Annotated[PceModelSuite, InstanceGuard(PceModelSuite)],
        *,
        inspector: BuildingInspector | None = None,
        gate_runner: Annotated[DynamicGatePort, InstanceGuard(DynamicGatePort)] | None = None,
        budget: PceRuntimeBudget | None = None,
    ) -> None:
        self._models = models
        self._inspector = inspector
        self._gate_runner: DynamicGatePort = gate_runner or InProcessGateRunner()
        self._budget = budget or PceRuntimeBudget()

    async def _run_workflow(
        self,
        repo_root: Path,
        *,
        mandate: EngineeringMandate | None,
        task_prompt: str,
        permit: CodePermit | None,
    ) -> PceWorkflowResult:
        resolved_root = repo_root.resolve()
        active_inspector = self._inspector or BuildingInspector.from_workspace(resolved_root)
        active_permit = permit or CodePermit.full_book(permit_id="PERMIT-PCE-RUNTIME")
        initial_authorized = mandate.authorized_files() if mandate is not None else frozenset()
        deps = PceGraphDeps(
            repo_root=resolved_root,
            sandbox=WorkspaceSandbox(resolved_root, initial_authorized),
            inspector=active_inspector,
            gate_runner=self._gate_runner,
            models=self._models,
            permit=active_permit,
            budget=self._budget,
            remediation_loop=LoopBudget(max_steps=self._budget.max_remediation_cycles),
            task_prompt=task_prompt,
            mandate=mandate,
        )
        workflow = build_pce_workflow_graph()
        state = AgentExecutionState()
        outcome: object = await workflow.run(state=state, deps=deps, inputs=FrameNode())
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
        """Execute an authored ``EngineeringMandate`` through all five PCE phases."""
        return await self._run_workflow(
            repo_root,
            mandate=mandate,
            task_prompt="",
            permit=permit,
        )

    @jezz_callable
    async def execute_prompt(
        self,
        repo_root: Path,
        task_prompt: NonEmptyStr,
        *,
        permit: CodePermit | None = None,
    ) -> PceWorkflowResult:
        """Frame ``task_prompt`` via BHV-01 FRAME and execute or halt at BHV-02 ELICIT."""
        return await self._run_workflow(
            repo_root,
            mandate=None,
            task_prompt=task_prompt,
            permit=permit,
        )
