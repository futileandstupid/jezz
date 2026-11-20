"""Pydantic-Graph state contracts and runtime trace projection for Pillar 1."""

import itertools
from abc import ABC
from collections.abc import Sequence
from typing import ClassVar, Never

import icontract
from pydantic import BaseModel, ConfigDict, Field
from pydantic_graph import BaseNode, GraphRunContext

from jezz.behaviors.graph import (
    BehaviorEdge,
    BehaviorGraph,
    BehaviorNode,
    GraphVerificationReport,
    verify_behavior_graph,
)
from jezz.behaviors.taxonomy import BehaviorKind


class ProblemState(BaseModel):
    """StateDomain.PROBLEM (P): framed objective, disambiguations, and bound code IDs."""

    model_config = ConfigDict(extra="forbid")

    objective: str = ""
    ambiguities: list[str] = Field(default_factory=list)
    bound_code_ids: set[str] = Field(default_factory=set)
    subtasks: list[str] = Field(default_factory=list)


class WorldModelState(BaseModel):
    """StateDomain.WORLD_MODEL (W): grounded facts, causal hypotheses, and negative knowledge."""

    model_config = ConfigDict(extra="forbid")

    observations: list[str] = Field(default_factory=list)
    external_citations: list[str] = Field(default_factory=list)
    active_hypotheses: list[str] = Field(default_factory=list)
    falsified_paths: list[str] = Field(default_factory=list)
    active_delegations: set[str] = Field(default_factory=set)


class WorkspaceState(BaseModel):
    """StateDomain.WORKSPACE (Omega): checkpoints and mutated artifact ledger."""

    model_config = ConfigDict(extra="forbid")

    checkpoints: list[str] = Field(default_factory=list)
    modified_artifacts: set[str] = Field(default_factory=set)
    sealed_commit: str | None = None


class EvidenceState(BaseModel):
    """StateDomain.EVIDENCE (E): static/dynamic verdicts and code-compliance seal."""

    model_config = ConfigDict(extra="forbid")

    static_checks_passed: bool = False
    dynamic_checks_passed: bool = False
    verification_records: list[str] = Field(default_factory=list)
    certified: bool = False


class ExecutionStep(BaseModel):
    """Immutable record of a single atomic behavior step executed at runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    kind: BehaviorKind
    summary: str = Field(min_length=1)


class AgentExecutionState(BaseModel):
    """Composite runtime state Sigma = (P, W, Omega, E) threaded through pydantic-graph."""

    model_config = ConfigDict(extra="forbid")

    problem: ProblemState = Field(default_factory=ProblemState)
    world_model: WorldModelState = Field(default_factory=WorldModelState)
    workspace: WorkspaceState = Field(default_factory=WorkspaceState)
    evidence: EvidenceState = Field(default_factory=EvidenceState)
    trace: list[ExecutionStep] = Field(default_factory=list)

    def record_step(self, step_id: str, kind: BehaviorKind, summary: str) -> None:
        """Append a behavior transition to the runtime trace."""
        self.trace.append(ExecutionStep(step_id=step_id, kind=kind, summary=summary))


class AtomicBehaviorGraphNode[DepsT = None, OutputT = Never](
    BaseNode[AgentExecutionState, DepsT, OutputT],
    ABC,
):
    """Typed pydantic-graph BaseNode tagging concrete subclasses with an atomic BehaviorKind."""

    behavior_kind: ClassVar[BehaviorKind]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if "behavior_kind" in cls.__dict__:
            raw_kind: object = cls.__dict__["behavior_kind"]
            if not isinstance(raw_kind, BehaviorKind):
                msg = f"{cls.__name__}.behavior_kind must be an instance of BehaviorKind."
                raise TypeError(msg)

    def record_into_context(
        self,
        ctx: GraphRunContext[AgentExecutionState, DepsT],
        step_id: str,
        summary: str,
    ) -> None:
        """Record this node's execution into the shared AgentExecutionState trace."""
        ctx.state.record_step(
            step_id=step_id,
            kind=self.behavior_kind,
            summary=summary,
        )


@icontract.require(lambda trace: len(trace) >= 1, "Trace must contain at least one step.")
def trace_to_behavior_graph(
    trace: Sequence[ExecutionStep],
    *,
    name: str = "runtime_trace",
) -> BehaviorGraph:
    """Project an observed runtime ``ExecutionStep`` sequence into its induced BehaviorGraph.

    Repeated visits to the same ``step_id`` (e.g., cyclic retry loops) collapse onto a single
    vertex while preserving all traversed directed transitions.
    """
    nodes_by_id: dict[str, BehaviorNode] = {}
    ordered_nodes: list[BehaviorNode] = []
    edges_seen: set[tuple[str, str]] = set()
    ordered_edges: list[BehaviorEdge] = []

    for step in trace:
        existing = nodes_by_id.get(step.step_id)
        if existing is None:
            node = BehaviorNode(id=step.step_id, kind=step.kind, label=step.summary)
            nodes_by_id[step.step_id] = node
            ordered_nodes.append(node)
        elif existing.kind != step.kind:
            msg = (
                f"Step ID '{step.step_id}' reused with conflicting BehaviorKinds: "
                f"{existing.kind} vs {step.kind}."
            )
            raise ValueError(msg)

    for prev, curr in itertools.pairwise(trace):
        pair = (prev.step_id, curr.step_id)
        if pair not in edges_seen:
            edges_seen.add(pair)
            ordered_edges.append(BehaviorEdge(source=prev.step_id, target=curr.step_id))

    return BehaviorGraph(
        name=name,
        description="Induced behavior graph from runtime execution trace.",
        entry_id=trace[0].step_id,
        nodes=tuple(ordered_nodes),
        edges=tuple(ordered_edges),
    )


@icontract.require(lambda trace: len(trace) >= 1)
def verify_execution_trace(
    trace: Sequence[ExecutionStep],
    *,
    name: str = "runtime_trace",
) -> GraphVerificationReport:
    """Verify that a recorded runtime trace obeys all Pillar 1 graph and dataflow invariants."""
    induced = trace_to_behavior_graph(trace, name=name)
    return verify_behavior_graph(induced)
