"""Directed behavior task graphs, Mermaid rendering, and monotone dataflow verification."""

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final, Self

import icontract
from pydantic import BaseModel, BeforeValidator, Field, model_validator

from jezz.behaviors.taxonomy import BEHAVIOR_BASIS, BehaviorKind
from jezz.behaviors.transitions import (
    MUTATE_KINDS,
    TERMINAL_KINDS,
    explain_transition,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    IdentifierStr,
    LoopBudget,
    ShortText,
    jezz_callable,
)

type NodeIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"),
]


def _coerce_wire_behavior_kind(raw_value: object) -> object:
    if isinstance(raw_value, BehaviorKind) or not isinstance(raw_value, str):
        return raw_value
    return BehaviorKind(raw_value)


_BEHAVIOR_KIND_VALIDATOR: Final[BeforeValidator] = BeforeValidator(_coerce_wire_behavior_kind)


class BehaviorNode(BaseModel):
    """A vertex in a coding-agent task graph bound to a single atomic behavior."""

    model_config = STRICT_FROZEN_CONFIG

    id: NodeIdentifier
    kind: Annotated[BehaviorKind, _BEHAVIOR_KIND_VALIDATOR]
    label: ShortText
    rationale: BoundedText = ""


class BehaviorEdge(BaseModel):
    """A directed transition between two BehaviorNodes, optionally guarded by a condition."""

    model_config = STRICT_FROZEN_CONFIG

    source: IdentifierStr
    target: IdentifierStr
    condition: BoundedText = ""


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

    model_config = STRICT_FROZEN_CONFIG

    code: GraphViolationCode
    message: ShortText
    node_id: IdentifierStr | None = None
    edge: tuple[IdentifierStr, IdentifierStr] | None = None


class NodeFlowFacts(BaseModel):
    """Abstract interpretation lattice element tracked at each node entry/exit."""

    model_config = STRICT_FROZEN_CONFIG

    codes_bound: bool
    checkpoint_active: bool
    may_be_dirty_static: bool
    may_be_dirty_dynamic: bool
    unreconciled_delegation: bool

    @classmethod
    @jezz_callable
    def top_identity(cls) -> NodeFlowFacts:
        """Identity element for unvisited lattice predecessors (True for MUST, False for MAY)."""
        return NodeFlowFacts(
            codes_bound=True,
            checkpoint_active=True,
            may_be_dirty_static=False,
            may_be_dirty_dynamic=False,
            unreconciled_delegation=False,
        )

    @classmethod
    @jezz_callable
    def entry_initial(cls) -> NodeFlowFacts:
        """Initial abstract state prior to executing the entry node."""
        return NodeFlowFacts(
            codes_bound=False,
            checkpoint_active=False,
            may_be_dirty_static=False,
            may_be_dirty_dynamic=False,
            unreconciled_delegation=False,
        )

    @jezz_callable
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

    model_config = STRICT_FROZEN_CONFIG

    graph_name: IdentifierStr
    is_valid: bool
    violations: Annotated[tuple[GraphViolation, ...], Field(max_length=4096)]
    entry_facts: Annotated[Mapping[IdentifierStr, NodeFlowFacts], Field(max_length=4096)]


class BehaviorGraph(BaseModel):
    """Directed graph (DAG or cyclic state machine) over the 21 atomic agent behaviors."""

    model_config = STRICT_FROZEN_CONFIG

    name: IdentifierStr
    description: BoundedText = ""
    entry_id: IdentifierStr
    nodes: Annotated[
        tuple[BehaviorNode, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=2048),
    ]
    edges: Annotated[
        tuple[BehaviorEdge, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=8192),
    ] = ()

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

    @jezz_callable
    def node_map(self) -> Mapping[IdentifierStr, BehaviorNode]:
        """Return immutable mapping from node ID to BehaviorNode."""
        return MappingProxyType({node.id: node for node in self.nodes})

    @jezz_callable
    def distinct_kinds(self) -> frozenset[BehaviorKind]:
        """Return the subset of atomic BehaviorKinds instantiated in this graph."""
        return frozenset(node.kind for node in self.nodes)

    @jezz_callable
    def to_mermaid(self) -> BoundedText:
        """Render the BehaviorGraph as a GitHub-compatible Mermaid flowchart."""
        lines = ["flowchart TD"]
        for node in self.nodes:
            spec = BEHAVIOR_BASIS[node.kind]
            escaped = node.label.replace('"', "'")
            lines.append(f'    {node.id}["{spec.code} {node.kind.upper()}: {escaped}"]')
        for edge in self.edges:
            lines.append(_format_mermaid_edge(edge))
        return "\n".join(lines)


def _format_mermaid_edge(edge: BehaviorEdge) -> str:
    if edge.condition:
        cond = edge.condition.replace('"', "'")
        return f'    {edge.source} -->|"{cond}"| {edge.target}'
    return f"    {edge.source} --> {edge.target}"


_STATIC_DIRTYING_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.SPECIFY,
        BehaviorKind.INSTRUMENT,
        BehaviorKind.SYNTHESIZE,
        BehaviorKind.REFACTOR,
    }
)

_DYNAMIC_DIRTYING_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.PROVISION,
        BehaviorKind.INSTRUMENT,
        BehaviorKind.SYNTHESIZE,
        BehaviorKind.REFACTOR,
    }
)

_CODES_REQUIRED_KINDS: Final[frozenset[BehaviorKind]] = MUTATE_KINDS | {
    BehaviorKind.DELEGATE,
    BehaviorKind.CERTIFY,
}

_CHECKPOINT_REQUIRED_KINDS: Final[frozenset[BehaviorKind]] = MUTATE_KINDS | {BehaviorKind.REVERT}


def _next_dirty_static(kind: BehaviorKind, prior: bool) -> bool:
    if kind in _STATIC_DIRTYING_KINDS:
        return True
    if kind in {BehaviorKind.VERIFY_STATIC, BehaviorKind.REVERT}:
        return False
    return prior


def _next_dirty_dynamic(kind: BehaviorKind, prior: bool) -> bool:
    if kind in _DYNAMIC_DIRTYING_KINDS:
        return True
    if kind in {BehaviorKind.VERIFY_DYNAMIC, BehaviorKind.REVERT}:
        return False
    return prior


def _next_unreconciled(kind: BehaviorKind, prior: bool) -> bool:
    if kind == BehaviorKind.DELEGATE:
        return True
    if kind == BehaviorKind.RECONCILE:
        return False
    return prior


def _transfer_facts(kind: BehaviorKind, incoming: NodeFlowFacts) -> NodeFlowFacts:
    """Monotone transfer function describing how ``kind`` transforms abstract flow facts."""
    return NodeFlowFacts(
        codes_bound=(True if kind == BehaviorKind.BIND_CODES else incoming.codes_bound),
        checkpoint_active=(True if kind == BehaviorKind.CHECKPOINT else incoming.checkpoint_active),
        may_be_dirty_static=_next_dirty_static(kind, incoming.may_be_dirty_static),
        may_be_dirty_dynamic=_next_dirty_dynamic(kind, incoming.may_be_dirty_dynamic),
        unreconciled_delegation=_next_unreconciled(kind, incoming.unreconciled_delegation),
    )


def _join_predecessor_facts(
    nid: str,
    entry_id: str,
    reachable: AbstractSet[str],
    predecessors: Mapping[str, Sequence[str]],
    out_facts: Mapping[str, NodeFlowFacts],
) -> NodeFlowFacts:
    """Combine outgoing lattice facts across all reachable incoming edges for ``nid``."""
    combined = NodeFlowFacts.entry_initial() if nid == entry_id else NodeFlowFacts.top_identity()
    for pred in predecessors[nid]:
        if pred in reachable:
            combined = combined.combine(out_facts[pred])
    return combined


def _enqueue_successors(
    candidates: Iterable[str],
    reachable: AbstractSet[str],
    worklist: deque[str],
    in_queue: set[str],
) -> None:
    """Schedule reachable successor nodes not currently queued in the Kildall worklist."""
    for succ in candidates:
        if succ in reachable and succ not in in_queue:
            worklist.append(succ)
            in_queue.add(succ)


def _run_kildall_dataflow(
    entry_id: str,
    reachable: AbstractSet[str],
    nodes_by_id: Mapping[str, BehaviorNode],
    predecessors: Mapping[str, Sequence[str]],
    successors: Mapping[str, Sequence[str]],
) -> dict[str, NodeFlowFacts]:
    """Compute fixed-point entry FlowFacts for all reachable vertices."""
    in_facts: dict[str, NodeFlowFacts] = {nid: NodeFlowFacts.top_identity() for nid in reachable}
    out_facts: dict[str, NodeFlowFacts] = {nid: NodeFlowFacts.top_identity() for nid in reachable}

    worklist: deque[str] = deque(reachable)
    in_queue: set[str] = set(reachable)
    budget = LoopBudget(max_steps=max(32, len(reachable) * 16))

    while worklist:
        budget.tick()
        nid = worklist.popleft()
        in_queue.remove(nid)

        combined = _join_predecessor_facts(nid, entry_id, reachable, predecessors, out_facts)
        new_out = _transfer_facts(nodes_by_id[nid].kind, combined)
        if combined != in_facts[nid] or new_out != out_facts[nid]:
            in_facts[nid] = combined
            out_facts[nid] = new_out
            _enqueue_successors(successors[nid], reachable, worklist, in_queue)

    return in_facts


def _bfs_reachable(
    seeds: Iterable[str],
    adjacency: Mapping[str, Sequence[str]],
    *,
    allowed: AbstractSet[str] | None = None,
) -> set[str]:
    """Traverse directed ``adjacency`` edges from ``seeds`` within optional ``allowed`` domain."""
    visited: set[str] = set(seeds)
    queue: deque[str] = deque(seeds)
    budget = LoopBudget(max_steps=max(32, len(adjacency) * 4 + 16))
    while queue:
        budget.tick()
        curr = queue.popleft()
        for nxt in adjacency[curr]:
            if (allowed is None or nxt in allowed) and nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return visited


def _validate_single_edge(
    edge: BehaviorEdge,
    nodes_by_id: Mapping[str, BehaviorNode],
) -> tuple[bool, GraphViolation | None]:
    """Validate structural endpoints and local adjacency legality for ``edge``."""
    if edge.source not in nodes_by_id or edge.target not in nodes_by_id:
        return (
            False,
            GraphViolation(
                code=GraphViolationCode.DANGLING_EDGE_ENDPOINT,
                message=f"Edge ({edge.source} -> {edge.target}) references undefined node ID.",
                edge=(edge.source, edge.target),
            ),
        )
    if edge.source == edge.target:
        return (
            False,
            GraphViolation(
                code=GraphViolationCode.SELF_LOOP,
                message=f"Node '{edge.source}' contains a forbidden self-loop.",
                node_id=edge.source,
                edge=(edge.source, edge.target),
            ),
        )

    verdict = explain_transition(nodes_by_id[edge.source].kind, nodes_by_id[edge.target].kind)
    if not verdict.allowed:
        return (
            True,
            GraphViolation(
                code=GraphViolationCode.ILLEGAL_TRANSITION,
                message=f"Edge ({edge.source} -> {edge.target}): {verdict.reason}",
                edge=(edge.source, edge.target),
            ),
        )
    return True, None


def _build_validated_adjacency(
    edges: Sequence[BehaviorEdge],
    nodes_by_id: Mapping[str, BehaviorNode],
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[GraphViolation]]:
    """Build successor/predecessor maps while collecting local edge violations."""
    successors: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    predecessors: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    violations: list[GraphViolation] = []

    for edge in edges:
        register_edge, violation = _validate_single_edge(edge, nodes_by_id)
        if violation is not None:
            violations.append(violation)
        if register_edge:
            successors[edge.source].append(edge.target)
            predecessors[edge.target].append(edge.source)

    return successors, predecessors, violations


def _check_sinks_and_coreachability(
    reachable: AbstractSet[str],
    nodes: Sequence[BehaviorNode],
    nodes_by_id: Mapping[str, BehaviorNode],
    predecessors: Mapping[str, Sequence[str]],
    successors: Mapping[str, Sequence[str]],
) -> list[GraphViolation]:
    """Verify sink terminal kinds and co-reachability from all reachable vertices."""
    violations: list[GraphViolation] = []
    valid_sinks: list[str] = []

    for nid in sorted(reachable):
        if successors[nid]:
            continue
        n_kind = nodes_by_id[nid].kind
        if n_kind in TERMINAL_KINDS:
            valid_sinks.append(nid)
            continue
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

    co_reachable = _bfs_reachable(valid_sinks, predecessors, allowed=reachable)
    for node in nodes:
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
    return violations


def _check_certify_flow_facts(node: BehaviorNode, facts: NodeFlowFacts) -> list[GraphViolation]:
    """Check dirty-mutation and delegation-reconciliation laws at a ``CERTIFY`` sink."""
    violations: list[GraphViolation] = []
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
    return violations


def _check_node_flow_invariants(node: BehaviorNode, facts: NodeFlowFacts) -> list[GraphViolation]:
    """Evaluate dominance and cleanliness dataflow invariants at ``node`` entry."""
    violations: list[GraphViolation] = []
    if node.kind in _CODES_REQUIRED_KINDS and not facts.codes_bound:
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
    if node.kind in _CHECKPOINT_REQUIRED_KINDS and not facts.checkpoint_active:
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
        violations.extend(_check_certify_flow_facts(node, facts))
    return violations


@icontract.ensure(
    lambda result: result.is_valid == (len(result.violations) == 0),
    "is_valid must hold iff zero violations are reported.",
)
@jezz_callable
def verify_behavior_graph(graph: BehaviorGraph) -> GraphVerificationReport:
    """Statically verify topological, adjacency, and monotone dataflow invariants on ``graph``."""
    nodes_by_id = graph.node_map()
    if graph.entry_id not in nodes_by_id:
        missing = GraphViolation(
            code=GraphViolationCode.MISSING_ENTRY_NODE,
            message=f"entry_id '{graph.entry_id}' does not exist in graph nodes.",
            node_id=graph.entry_id,
        )
        return GraphVerificationReport(
            graph_name=graph.name,
            is_valid=False,
            violations=(missing,),
            entry_facts={},
        )

    violations: list[GraphViolation] = []
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

    successors, predecessors, edge_violations = _build_validated_adjacency(
        graph.edges,
        nodes_by_id,
    )
    violations.extend(edge_violations)

    reachable = _bfs_reachable((graph.entry_id,), successors)
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

    violations.extend(
        _check_sinks_and_coreachability(
            reachable,
            graph.nodes,
            nodes_by_id,
            predecessors,
            successors,
        )
    )

    entry_facts = _run_kildall_dataflow(
        entry_id=graph.entry_id,
        reachable=reachable,
        nodes_by_id=nodes_by_id,
        predecessors=predecessors,
        successors=successors,
    )
    for node in graph.nodes:
        if node.id in reachable:
            violations.extend(_check_node_flow_invariants(node, entry_facts[node.id]))

    return GraphVerificationReport(
        graph_name=graph.name,
        is_valid=len(violations) == 0,
        violations=tuple(violations),
        entry_facts=entry_facts,
    )
