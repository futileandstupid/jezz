"""Pydantic-Graph state contracts and runtime trace projection for Pillar 1."""

import itertools
from abc import ABC
from collections.abc import Sequence
from typing import Annotated, ClassVar, Never

import icontract
from pydantic import BaseModel, Field
from pydantic_graph import BaseNode, GraphRunContext

from jezz.behaviors.graph import (
    BehaviorEdge,
    BehaviorGraph,
    BehaviorNode,
    GraphVerificationReport,
    verify_behavior_graph,
)
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    STRICT_MUTABLE_CONFIG,
    BoundedText,
    IdentifierStr,
    ShortText,
    jezz_callable,
)

type StepIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"),
]


class ProblemState(BaseModel):
    """StateDomain.PROBLEM (P): framed objective, disambiguations, and bound code IDs."""

    model_config = STRICT_MUTABLE_CONFIG

    objective: BoundedText = ""
    ambiguities: list[ShortText] = Field(default_factory=list, max_length=256)
    bound_code_ids: set[IdentifierStr] = Field(default_factory=set, max_length=256)
    subtasks: list[ShortText] = Field(default_factory=list, max_length=256)


class WorldModelState(BaseModel):
    """StateDomain.WORLD_MODEL (W): grounded facts, causal hypotheses, and negative knowledge."""

    model_config = STRICT_MUTABLE_CONFIG

    observations: list[BoundedText] = Field(default_factory=list, max_length=2048)
    external_citations: list[ShortText] = Field(default_factory=list, max_length=512)
    active_hypotheses: list[ShortText] = Field(default_factory=list, max_length=256)
    falsified_paths: list[ShortText] = Field(default_factory=list, max_length=512)
    active_delegations: set[IdentifierStr] = Field(default_factory=set, max_length=128)


class WorkspaceState(BaseModel):
    """StateDomain.WORKSPACE (Omega): checkpoints and mutated artifact ledger."""

    model_config = STRICT_MUTABLE_CONFIG

    checkpoints: list[IdentifierStr] = Field(default_factory=list, max_length=256)
    modified_artifacts: set[IdentifierStr] = Field(default_factory=set, max_length=1024)
    sealed_commit: IdentifierStr | None = None


class EvidenceState(BaseModel):
    """StateDomain.EVIDENCE (E): static/dynamic verdicts and code-compliance seal."""

    model_config = STRICT_MUTABLE_CONFIG

    static_checks_passed: bool = False
    dynamic_checks_passed: bool = False
    verification_records: list[ShortText] = Field(default_factory=list, max_length=512)
    certified: bool = False


class ExecutionStep(BaseModel):
    """Immutable record of a single atomic behavior step executed at runtime."""

    model_config = STRICT_FROZEN_CONFIG

    step_id: StepIdentifier
    kind: BehaviorKind
    summary: ShortText


class AgentExecutionState(BaseModel):
    """Composite runtime state Sigma = (P, W, Omega, E) threaded through pydantic-graph."""

    model_config = STRICT_MUTABLE_CONFIG

    problem: ProblemState = Field(default_factory=ProblemState)
    world_model: WorldModelState = Field(default_factory=WorldModelState)
    workspace: WorkspaceState = Field(default_factory=WorkspaceState)
    evidence: EvidenceState = Field(default_factory=EvidenceState)
    trace: list[ExecutionStep] = Field(default_factory=list, max_length=10_000)

    @jezz_callable
    def record_step(self, step_id: StepIdentifier, kind: BehaviorKind, summary: ShortText) -> None:
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

    @jezz_callable
    def record_into_context(
        self,
        ctx: GraphRunContext[AgentExecutionState, DepsT],
        step_id: StepIdentifier,
        summary: ShortText,
    ) -> None:
        """Record this node's execution into the shared AgentExecutionState trace."""
        ctx.state.record_step(
            step_id=step_id,
            kind=self.behavior_kind,
            summary=summary,
        )


@icontract.require(lambda trace: len(trace) >= 1, "Trace must contain at least one step.")
@icontract.ensure(lambda trace, result: result.entry_id == trace[0].step_id)
@jezz_callable
def trace_to_behavior_graph(
    trace: Annotated[Sequence[ExecutionStep], Field(min_length=1, max_length=10_000)],
    *,
    name: IdentifierStr = "runtime_trace",
) -> BehaviorGraph:
    """Project an observed runtime ``ExecutionStep`` sequence into its induced BehaviorGraph."""
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
@icontract.ensure(lambda result: result.is_valid == (len(result.violations) == 0))
@jezz_callable
def verify_execution_trace(
    trace: Annotated[Sequence[ExecutionStep], Field(min_length=1, max_length=10_000)],
    *,
    name: IdentifierStr = "runtime_trace",
) -> GraphVerificationReport:
    """Verify that a recorded runtime trace obeys all Pillar 1 graph and dataflow invariants."""
    induced = trace_to_behavior_graph(trace, name=name)
    return verify_behavior_graph(induced)
