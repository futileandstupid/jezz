"""Directed behavior task graphs, Mermaid rendering, and monotone dataflow verification."""

from collections import deque
from collections.abc import Mapping
from enum import StrEnum
from typing import Self

import icontract
from pydantic import BaseModel, ConfigDict, Field, model_validator

from jezz.behaviors.taxonomy import BEHAVIOR_BASIS, BehaviorKind
from jezz.behaviors.transitions import (
    MUTATE_KINDS,
    TERMINAL_KINDS,
    explain_transition,
)


class BehaviorNode(BaseModel):
    """A vertex in a coding-agent task graph bound to a single atomic behavior."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    kind: BehaviorKind
    label: str = Field(min_length=1)
    rationale: str = ""


class BehaviorEdge(BaseModel):
    """A directed transition between two BehaviorNodes, optionally guarded by a condition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    condition: str = ""


class GraphViolationCode(StrEnum):
    """Canonical violation codes for invalid behavior task graphs."""

    MISSING_ENTRY_NODE = "missing_entry_node"
    INVALID_ENTRY_KIND = "invalid_entry_kind"
    DANGLING_EDGE_ENDPOINT = "dangling_edge_endpoint"
    SELF_LOOP = "self_loop"
    ILLEGAL_TRANSITION = "illegal_transition"
    UNREACHABLE_NODE = "unreachable_node"
    INVALID_TERMINAL_KIND = "invalid_terminal_kind"
    NO_TERMINAL_PATH = "no_terminal_path"
    UNBOUND_CODES = "unbound_codes"
    MISSING_CHECKPOINT = "missing_checkpoint"
    UNVERIFIED_STATIC_MUTATION = "unverified_static_mutation"
    UNVERIFIED_DYNAMIC_MUTATION = "unverified_dynamic_mutation"
    UNRECONCILED_DELEGATION = "unreconciled_delegation"


class GraphViolation(BaseModel):
    """A concrete structural or dataflow law violation detected in a BehaviorGraph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: GraphViolationCode
    message: str
    node_id: str | None = None
    edge: tuple[str, str] | None = None


class NodeFlowFacts(BaseModel):
    """Abstract interpretation lattice element tracked at each node entry/exit.

    - ``codes_bound`` and ``checkpoint_active`` are MUST facts (meet = logical AND).
    - ``may_be_dirty_static``, ``may_be_dirty_dynamic``, and ``unreconciled_delegation``
      are MAY facts (join = logical OR).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    codes_bound: bool
    checkpoint_active: bool
    may_be_dirty_static: bool
    may_be_dirty_dynamic: bool
    unreconciled_delegation: bool

    @classmethod
    def top_identity(cls) -> Self:
        """Identity element for unvisited lattice predecessors (True for MUST, False for MAY)."""
        return cls(
            codes_bound=True,
            checkpoint_active=True,
            may_be_dirty_static=False,
            may_be_dirty_dynamic=False,
            unreconciled_delegation=False,
        )

    @classmethod
    def entry_initial(cls) -> Self:
        """Initial abstract state prior to executing the entry node."""
        return cls(
            codes_bound=False,
            checkpoint_active=False,
            may_be_dirty_static=False,
            may_be_dirty_dynamic=False,
            unreconciled_delegation=False,
        )

    def combine(self, other: NodeFlowFacts) -> NodeFlowFacts:
        """Lattice confluence operator across incoming control-flow edges."""
        return NodeFlowFacts(
            codes_bound=self.codes_bound and other.codes_bound,
            checkpoint_active=self.checkpoint_active and other.checkpoint_active,
            may_be_dirty_static=self.may_be_dirty_static or other.may_be_dirty_static,
            may_be_dirty_dynamic=self.may_be_dirty_dynamic or other.may_be_dirty_dynamic,
            unreconciled_delegation=(self.unreconciled_delegation or other.unreconciled_delegation),
        )


class GraphVerificationReport(BaseModel):
    """Complete static verification result for a BehaviorGraph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_name: str
    is_valid: bool
    violations: tuple[GraphViolation, ...]
    entry_facts: Mapping[str, NodeFlowFacts]


class BehaviorGraph(BaseModel):
    """Directed graph (DAG or cyclic state machine) over the 21 atomic agent behaviors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    entry_id: str = Field(min_length=1)
    nodes: tuple[BehaviorNode, ...] = Field(min_length=1)
    edges: tuple[BehaviorEdge, ...] = ()

    @model_validator(mode="after")
    def _ensure_unique_identifiers(self) -> Self:
        seen_nodes: set[str] = set()
        for node in self.nodes:
            if node.id in seen_nodes:
                msg = f"Duplicate BehaviorNode id '{node.id}' in graph '{self.name}'."
                raise ValueError(msg)
            seen_nodes.add(node.id)

        seen_edges: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            key = (edge.source, edge.target, edge.condition)
            if key in seen_edges:
                msg = f"Duplicate BehaviorEdge {key} in graph '{self.name}'."
                raise ValueError(msg)
            seen_edges.add(key)

        return self

    def node_map(self) -> dict[str, BehaviorNode]:
        """Return mapping from node ID to BehaviorNode."""
        return {node.id: node for node in self.nodes}

    def distinct_kinds(self) -> frozenset[BehaviorKind]:
        """Return the subset of atomic BehaviorKinds instantiated in this graph."""
        return frozenset(node.kind for node in self.nodes)

    def to_mermaid(self) -> str:
        """Render the BehaviorGraph as a GitHub-compatible Mermaid flowchart."""
        lines = ["flowchart TD"]
        for node in self.nodes:
            spec = BEHAVIOR_BASIS[node.kind]
            escaped = node.label.replace('"', "'")
            lines.append(f'    {node.id}["{spec.code} {node.kind.upper()}: {escaped}"]')
        for edge in self.edges:
            if edge.condition:
                cond = edge.condition.replace('"', "'")
                lines.append(f'    {edge.source} -->|"{cond}"| {edge.target}')
            else:
                lines.append(f"    {edge.source} --> {edge.target}")
        return "\n".join(lines)


_STATIC_DIRTYING_KINDS: frozenset[BehaviorKind] = frozenset(
    {
        BehaviorKind.SPECIFY,
        BehaviorKind.INSTRUMENT,
        BehaviorKind.SYNTHESIZE,
        BehaviorKind.REFACTOR,
    }
)

_DYNAMIC_DIRTYING_KINDS: frozenset[BehaviorKind] = frozenset(
    {
        BehaviorKind.PROVISION,
        BehaviorKind.INSTRUMENT,
        BehaviorKind.SYNTHESIZE,
        BehaviorKind.REFACTOR,
    }
)


def _transfer_facts(kind: BehaviorKind, incoming: NodeFlowFacts) -> NodeFlowFacts:
    """Monotone transfer function describing how ``kind`` transforms abstract flow facts."""
    codes_bound = True if kind == BehaviorKind.BIND_CODES else incoming.codes_bound
    checkpoint_active = True if kind == BehaviorKind.CHECKPOINT else incoming.checkpoint_active

    if kind in _STATIC_DIRTYING_KINDS:
        dirty_static = True
    elif kind in {BehaviorKind.VERIFY_STATIC, BehaviorKind.REVERT}:
        dirty_static = False
    else:
        dirty_static = incoming.may_be_dirty_static

    if kind in _DYNAMIC_DIRTYING_KINDS:
        dirty_dynamic = True
    elif kind in {BehaviorKind.VERIFY_DYNAMIC, BehaviorKind.REVERT}:
        dirty_dynamic = False
    else:
        dirty_dynamic = incoming.may_be_dirty_dynamic

    if kind == BehaviorKind.DELEGATE:
        unreconciled = True
    elif kind == BehaviorKind.RECONCILE:
        unreconciled = False
    else:
        unreconciled = incoming.unreconciled_delegation

    return NodeFlowFacts(
        codes_bound=codes_bound,
        checkpoint_active=checkpoint_active,
        may_be_dirty_static=dirty_static,
        may_be_dirty_dynamic=dirty_dynamic,
        unreconciled_delegation=unreconciled,
    )


def _run_kildall_dataflow(
    entry_id: str,
    reachable: set[str],
    nodes_by_id: Mapping[str, BehaviorNode],
    predecessors: Mapping[str, list[str]],
    successors: Mapping[str, list[str]],
) -> dict[str, NodeFlowFacts]:
    """Compute fixed-point entry FlowFacts for all reachable vertices."""
    in_facts: dict[str, NodeFlowFacts] = {nid: NodeFlowFacts.top_identity() for nid in reachable}
    out_facts: dict[str, NodeFlowFacts] = {nid: NodeFlowFacts.top_identity() for nid in reachable}

    worklist: deque[str] = deque(reachable)
    in_queue: set[str] = set(reachable)

    while worklist:
        nid = worklist.popleft()
        in_queue.remove(nid)

        combined = (
            NodeFlowFacts.entry_initial() if nid == entry_id else NodeFlowFacts.top_identity()
        )
        for pred in predecessors[nid]:
            if pred in reachable:
                combined = combined.combine(out_facts[pred])

        new_out = _transfer_facts(nodes_by_id[nid].kind, combined)
        if combined != in_facts[nid] or new_out != out_facts[nid]:
            in_facts[nid] = combined
            out_facts[nid] = new_out
            for succ in successors[nid]:
                if succ in reachable and succ not in in_queue:
                    worklist.append(succ)
                    in_queue.add(succ)

    return in_facts


@icontract.ensure(
    lambda result: result.is_valid == (len(result.violations) == 0),
    "is_valid must hold iff zero violations are reported.",
)
def verify_behavior_graph(graph: BehaviorGraph) -> GraphVerificationReport:
    """Statically verify topological, adjacency, and monotone dataflow invariants on ``graph``."""
    violations: list[GraphViolation] = []
    nodes_by_id = graph.node_map()

    if graph.entry_id not in nodes_by_id:
        violations.append(
            GraphViolation(
                code=GraphViolationCode.MISSING_ENTRY_NODE,
                message=f"entry_id '{graph.entry_id}' does not exist in graph nodes.",
                node_id=graph.entry_id,
            )
        )
        return GraphVerificationReport(
            graph_name=graph.name,
            is_valid=False,
            violations=tuple(violations),
            entry_facts={},
        )

    entry_node = nodes_by_id[graph.entry_id]
    if entry_node.kind != BehaviorKind.FRAME:
        violations.append(
            GraphViolation(
                code=GraphViolationCode.INVALID_ENTRY_KIND,
                message=(
                    f"Entry node '{entry_node.id}' has kind '{entry_node.kind}'; "
                    "every task graph must enter at FRAME."
                ),
                node_id=entry_node.id,
            )
        )

    successors: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    predecessors: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}

    for edge in graph.edges:
        missing_src = edge.source not in nodes_by_id
        missing_dst = edge.target not in nodes_by_id
        if missing_src or missing_dst:
            violations.append(
                GraphViolation(
                    code=GraphViolationCode.DANGLING_EDGE_ENDPOINT,
                    message=(
                        f"Edge ({edge.source} -> {edge.target}) references undefined node ID."
                    ),
                    edge=(edge.source, edge.target),
                )
            )
            continue

        if edge.source == edge.target:
            violations.append(
                GraphViolation(
                    code=GraphViolationCode.SELF_LOOP,
                    message=f"Node '{edge.source}' contains a forbidden self-loop.",
                    node_id=edge.source,
                    edge=(edge.source, edge.target),
                )
            )
            continue

        src_kind = nodes_by_id[edge.source].kind
        dst_kind = nodes_by_id[edge.target].kind
        verdict = explain_transition(src_kind, dst_kind)
        if not verdict.allowed:
            violations.append(
                GraphViolation(
                    code=GraphViolationCode.ILLEGAL_TRANSITION,
                    message=f"Edge ({edge.source} -> {edge.target}): {verdict.reason}",
                    edge=(edge.source, edge.target),
                )
            )

        successors[edge.source].append(edge.target)
        predecessors[edge.target].append(edge.source)

    # Forward reachability from entry_id
    reachable: set[str] = {graph.entry_id}
    fwd_queue: deque[str] = deque([graph.entry_id])
    while fwd_queue:
        curr = fwd_queue.popleft()
        for nxt in successors[curr]:
            if nxt not in reachable:
                reachable.add(nxt)
                fwd_queue.append(nxt)

    for node in graph.nodes:
        if node.id not in reachable:
            violations.append(
                GraphViolation(
                    code=GraphViolationCode.UNREACHABLE_NODE,
                    message=(
                        f"Node '{node.id}' ({node.kind}) is unreachable "
                        f"from entry '{graph.entry_id}'."
                    ),
                    node_id=node.id,
                )
            )

    # Terminal sink checks & reverse co-reachability
    valid_sinks: list[str] = []
    for nid in sorted(reachable):
        if not successors[nid]:
            n_kind = nodes_by_id[nid].kind
            if n_kind in TERMINAL_KINDS:
                valid_sinks.append(nid)
            else:
                violations.append(
                    GraphViolation(
                        code=GraphViolationCode.INVALID_TERMINAL_KIND,
                        message=(
                            f"Sink node '{nid}' terminates at '{n_kind}'; "
                            "valid task sinks must be CERTIFY or ELICIT."
                        ),
                        node_id=nid,
                    )
                )

    co_reachable: set[str] = set(valid_sinks)
    rev_queue: deque[str] = deque(valid_sinks)
    while rev_queue:
        curr = rev_queue.popleft()
        for prev in predecessors[curr]:
            if prev in reachable and prev not in co_reachable:
                co_reachable.add(prev)
                rev_queue.append(prev)

    for node in graph.nodes:
        if node.id in reachable and node.id not in co_reachable:
            violations.append(
                GraphViolation(
                    code=GraphViolationCode.NO_TERMINAL_PATH,
                    message=(
                        f"Reachable node '{node.id}' ({node.kind}) has no directed path "
                        "to a valid terminal sink (CERTIFY or ELICIT)."
                    ),
                    node_id=node.id,
                )
            )

    # Kildall monotone dataflow invariants across all reachable vertices
    entry_facts = _run_kildall_dataflow(
        entry_id=graph.entry_id,
        reachable=reachable,
        nodes_by_id=nodes_by_id,
        predecessors=predecessors,
        successors=successors,
    )

    codes_required_kinds = MUTATE_KINDS | {BehaviorKind.DELEGATE, BehaviorKind.CERTIFY}
    checkpoint_required_kinds = MUTATE_KINDS | {BehaviorKind.REVERT}

    for node in graph.nodes:
        if node.id not in reachable:
            continue
        facts = entry_facts[node.id]

        if node.kind in codes_required_kinds and not facts.codes_bound:
            violations.append(
                GraphViolation(
                    code=GraphViolationCode.UNBOUND_CODES,
                    message=(
                        f"Node '{node.id}' ({node.kind}) is reachable without an upstream "
                        "BIND_CODES gate on every incoming path."
                    ),
                    node_id=node.id,
                )
            )

        if node.kind in checkpoint_required_kinds and not facts.checkpoint_active:
            violations.append(
                GraphViolation(
                    code=GraphViolationCode.MISSING_CHECKPOINT,
                    message=(
                        f"Node '{node.id}' ({node.kind}) is reachable without an upstream "
                        "CHECKPOINT on every incoming path."
                    ),
                    node_id=node.id,
                )
            )

        if node.kind == BehaviorKind.CERTIFY:
            if facts.may_be_dirty_static:
                violations.append(
                    GraphViolation(
                        code=GraphViolationCode.UNVERIFIED_STATIC_MUTATION,
                        message=(
                            f"CERTIFY node '{node.id}' is reachable with unverified static "
                            "mutations (missing VERIFY_STATIC or REVERT after edit)."
                        ),
                        node_id=node.id,
                    )
                )
            if facts.may_be_dirty_dynamic:
                violations.append(
                    GraphViolation(
                        code=GraphViolationCode.UNVERIFIED_DYNAMIC_MUTATION,
                        message=(
                            f"CERTIFY node '{node.id}' is reachable with unverified dynamic "
                            "mutations (missing VERIFY_DYNAMIC or REVERT after edit)."
                        ),
                        node_id=node.id,
                    )
                )
            if facts.unreconciled_delegation:
                violations.append(
                    GraphViolation(
                        code=GraphViolationCode.UNRECONCILED_DELEGATION,
                        message=(
                            f"CERTIFY node '{node.id}' is reachable with dangling DELEGATE "
                            "branches not synchronized via RECONCILE."
                        ),
                        node_id=node.id,
                    )
                )

    return GraphVerificationReport(
        graph_name=graph.name,
        is_valid=len(violations) == 0,
        violations=tuple(violations),
        entry_facts=entry_facts,
    )
