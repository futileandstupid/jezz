"""Unit, async pydantic-graph, contract, and Hypothesis property tests for Pillar 1."""

from dataclasses import dataclass
from typing import ClassVar

import icontract
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from pydantic_graph import End, GraphBuilder, GraphRunContext

from jezz.behaviors import (
    ALLOWED_TRANSITIONS,
    BEHAVIOR_BASIS,
    FIBONACCI_BASIS_CARDINALITY,
    MUTATE_KINDS,
    MUTATION_STRATUM_KINDS,
    AgentExecutionState,
    AtomicBehaviorGraphNode,
    BehaviorEdge,
    BehaviorGraph,
    BehaviorKind,
    BehaviorNode,
    BehaviorSpec,
    BehaviorStratum,
    ExecutionStep,
    GraphViolationCode,
    StateDomain,
    WorkspaceEffect,
    all_canonical_archetypes,
    build_abductive_bugfix_graph,
    build_readonly_audit_graph,
    explain_transition,
    fibonacci_index,
    fibonacci_number,
    get_behavior_spec,
    is_fibonacci,
    is_perfect_square,
    is_transition_allowed,
    validate_basis_invariants,
    verify_behavior_graph,
    verify_execution_trace,
)

# ---------------------------------------------------------------------------
# 1. Fibonacci & icontract Invariants
# ---------------------------------------------------------------------------


def test_canonical_basis_cardinality_is_f8_21() -> None:
    assert FIBONACCI_BASIS_CARDINALITY == 21
    assert is_fibonacci(FIBONACCI_BASIS_CARDINALITY)
    assert fibonacci_number(8) == 21
    assert fibonacci_index(21) == 8
    assert len(BEHAVIOR_BASIS) == 21
    assert len(BehaviorKind) == 21


def test_boolean_inputs_rejected_by_icontract() -> None:
    with pytest.raises(icontract.ViolationError):
        is_fibonacci(True)
    with pytest.raises(icontract.ViolationError):
        is_perfect_square(False)
    with pytest.raises(icontract.ViolationError):
        fibonacci_number(-1)


@given(st.integers(min_value=0, max_value=60))
def test_property_every_generated_fibonacci_satisfies_predicate(idx: int) -> None:
    val = fibonacci_number(idx)
    if idx == 0:
        assert val == 0
        assert not is_fibonacci(val)
    else:
        assert is_fibonacci(val)
        resolved = fibonacci_index(val)
        assert resolved is not None
        assert fibonacci_number(resolved) == val


@given(st.integers(min_value=-500, max_value=50_000))
def test_property_fibonacci_index_roundtrip(candidate: int) -> None:
    if candidate < 1:
        assert not is_fibonacci(candidate)
    else:
        idx = fibonacci_index(candidate)
        assert (idx is not None) == is_fibonacci(candidate)


# ---------------------------------------------------------------------------
# 2. Taxonomy & BehaviorSpec Contract Invariants
# ---------------------------------------------------------------------------


def test_stratum_partition_counts() -> None:
    counts = {stratum: 0 for stratum in BehaviorStratum}
    for spec in BEHAVIOR_BASIS.values():
        counts[spec.stratum] += 1

    assert counts == {
        BehaviorStratum.GOVERNANCE: 3,
        BehaviorStratum.EPISTEMIC: 4,
        BehaviorStratum.TOPOLOGY: 4,
        BehaviorStratum.MUTATION: 6,
        BehaviorStratum.VERIFICATION: 4,
    }
    assert sum(counts.values()) == 21


def test_behavior_spec_rejects_inconsistent_workspace_contracts() -> None:
    with pytest.raises(ValidationError):
        BehaviorSpec(
            code="BHV-99",
            kind=BehaviorKind.INSPECT_CODE,
            stratum=BehaviorStratum.EPISTEMIC,
            summary="Invalid epistemic spec claiming workspace mutation.",
            reads=frozenset({StateDomain.WORKSPACE}),
            writes=frozenset({StateDomain.WORKSPACE}),
            workspace_effect=WorkspaceEffect.NONE,
            postcondition="Should fail model validation before instantiation.",
        )

    with pytest.raises(ValidationError):
        BehaviorSpec(
            code="BHV-98",
            kind=BehaviorKind.REVERT,
            stratum=BehaviorStratum.VERIFICATION,
            summary="Revert spec omitting negative knowledge write to WORLD_MODEL.",
            reads=frozenset({StateDomain.WORKSPACE}),
            writes=frozenset({StateDomain.WORKSPACE}),
            workspace_effect=WorkspaceEffect.RESTORE,
            postcondition="Should fail because REVERT must write negative knowledge.",
        )


def test_validate_basis_rejects_non_fibonacci_subset() -> None:
    subset = {
        kind: get_behavior_spec(kind)
        for kind in list(BehaviorKind)[:20]  # 20 is non-Fibonacci
    }
    with pytest.raises(ValueError, match="Fibonacci"):
        validate_basis_invariants(subset)


# ---------------------------------------------------------------------------
# 3. Local Transition Matrix Laws
# ---------------------------------------------------------------------------


def test_transition_matrix_core_statutory_bans() -> None:
    # CERTIFY is a strict sink
    assert ALLOWED_TRANSITIONS[BehaviorKind.CERTIFY] == frozenset()
    for target in BehaviorKind:
        assert not is_transition_allowed(BehaviorKind.CERTIFY, target)

    # ELICIT resumes strictly via FRAME
    assert ALLOWED_TRANSITIONS[BehaviorKind.ELICIT] == frozenset({BehaviorKind.FRAME})

    # No mutating node can jump directly to CERTIFY, ELICIT, or FRAME
    for mut_kind in MUTATE_KINDS:
        assert not is_transition_allowed(mut_kind, BehaviorKind.CERTIFY)
        assert not is_transition_allowed(mut_kind, BehaviorKind.ELICIT)
        assert not is_transition_allowed(mut_kind, BehaviorKind.FRAME)
        verdict = explain_transition(mut_kind, BehaviorKind.CERTIFY)
        assert not verdict.allowed
        assert "verification" in verdict.reason

    # REVERT cannot jump directly back into mutation (no blind retry)
    for mut_stratum_kind in MUTATION_STRATUM_KINDS:
        assert not is_transition_allowed(BehaviorKind.REVERT, mut_stratum_kind)
        verdict = explain_transition(BehaviorKind.REVERT, mut_stratum_kind)
        assert not verdict.allowed
        assert "blind retry" in verdict.reason


# ---------------------------------------------------------------------------
# 4. Canonical Archetypes & Constructive Span Across All 21 Behaviors
# ---------------------------------------------------------------------------


def test_all_canonical_archetypes_verify_clean_and_span_entire_basis() -> None:
    archetypes = all_canonical_archetypes()
    assert len(archetypes) == 5

    covered_kinds: set[BehaviorKind] = set()
    for graph in archetypes:
        report = verify_behavior_graph(graph)
        assert report.is_valid, f"{graph.name} failed verification: {report.violations}"
        covered_kinds |= set(graph.distinct_kinds())
        mermaid = graph.to_mermaid()
        assert mermaid.startswith("flowchart TD")

    # Every single one of the 21 atomic behaviors is constructively exercised
    assert covered_kinds == set(BehaviorKind)


def test_cyclic_bugfix_graph_dataflow_converges_soundly() -> None:
    graph = build_abductive_bugfix_graph()
    report = verify_behavior_graph(graph)
    assert report.is_valid
    certify_facts = report.entry_facts["certify_bugfix"]
    assert certify_facts.codes_bound is True
    assert certify_facts.checkpoint_active is True
    assert certify_facts.may_be_dirty_static is False
    assert certify_facts.may_be_dirty_dynamic is False
    assert certify_facts.unreconciled_delegation is False


# ---------------------------------------------------------------------------
# 5. Exhaustive Violation Detection Tests
# ---------------------------------------------------------------------------


def test_detects_invalid_entry_and_terminal_kinds() -> None:
    bad_graph = BehaviorGraph(
        name="bad_endpoints",
        entry_id="n1",
        nodes=(
            BehaviorNode(id="n1", kind=BehaviorKind.INSPECT_CODE, label="Wrong entry"),
            BehaviorNode(id="n2", kind=BehaviorKind.HYPOTHESIZE, label="Wrong terminal"),
        ),
        edges=(BehaviorEdge(source="n1", target="n2"),),
    )
    report = verify_behavior_graph(bad_graph)
    codes = {v.code for v in report.violations}
    assert GraphViolationCode.INVALID_ENTRY_KIND in codes
    assert GraphViolationCode.INVALID_TERMINAL_KIND in codes
    assert GraphViolationCode.NO_TERMINAL_PATH in codes


def test_detects_missing_bind_codes_and_missing_checkpoint() -> None:
    bad_graph = BehaviorGraph(
        name="unbound_and_uncheckpointed",
        entry_id="f",
        nodes=(
            BehaviorNode(id="f", kind=BehaviorKind.FRAME, label="Frame"),
            BehaviorNode(id="i", kind=BehaviorKind.INSPECT_CODE, label="Inspect"),
            BehaviorNode(id="s", kind=BehaviorKind.SYNTHESIZE, label="Edit without safety"),
            BehaviorNode(id="vs", kind=BehaviorKind.VERIFY_STATIC, label="Static"),
            BehaviorNode(id="vd", kind=BehaviorKind.VERIFY_DYNAMIC, label="Dynamic"),
            BehaviorNode(id="c", kind=BehaviorKind.CERTIFY, label="Certify"),
        ),
        edges=(
            BehaviorEdge(source="f", target="i"),
            BehaviorEdge(source="i", target="s"),
            BehaviorEdge(source="s", target="vs"),
            BehaviorEdge(source="vs", target="vd"),
            BehaviorEdge(source="vd", target="c"),
        ),
    )
    report = verify_behavior_graph(bad_graph)
    codes = {v.code for v in report.violations}
    assert GraphViolationCode.UNBOUND_CODES in codes
    assert GraphViolationCode.MISSING_CHECKPOINT in codes


def test_detects_unverified_static_and_dynamic_mutations_and_dangling_delegate() -> None:
    # Synthesize -> VerifyStatic only (skips VerifyDynamic!) + Delegate without Reconcile
    bad_graph = BehaviorGraph(
        name="skipped_dynamic_and_reconcile",
        entry_id="f",
        nodes=(
            BehaviorNode(id="f", kind=BehaviorKind.FRAME, label="Frame"),
            BehaviorNode(id="b", kind=BehaviorKind.BIND_CODES, label="Bind"),
            BehaviorNode(id="cp", kind=BehaviorKind.CHECKPOINT, label="Checkpoint"),
            BehaviorNode(id="syn", kind=BehaviorKind.SYNTHESIZE, label="Synthesize"),
            BehaviorNode(id="vs", kind=BehaviorKind.VERIFY_STATIC, label="Static only"),
            BehaviorNode(id="del", kind=BehaviorKind.DELEGATE, label="Delegate"),
            BehaviorNode(id="mem", kind=BehaviorKind.CURATE_MEMORY, label="Curate"),
            BehaviorNode(id="cert", kind=BehaviorKind.CERTIFY, label="Certify prematurely"),
        ),
        edges=(
            BehaviorEdge(source="f", target="b"),
            BehaviorEdge(source="b", target="cp"),
            BehaviorEdge(source="cp", target="syn"),
            BehaviorEdge(source="syn", target="vs"),
            BehaviorEdge(source="vs", target="del"),
            BehaviorEdge(source="del", target="mem"),
            BehaviorEdge(source="mem", target="cert"),
        ),
    )
    report = verify_behavior_graph(bad_graph)
    codes = {v.code for v in report.violations}
    assert GraphViolationCode.UNVERIFIED_DYNAMIC_MUTATION in codes
    assert GraphViolationCode.UNRECONCILED_DELEGATION in codes


def test_detects_self_loop_dangling_edge_and_unreachable_island() -> None:
    base = build_readonly_audit_graph()
    corrupted = BehaviorGraph(
        name="corrupted_topology",
        entry_id=base.entry_id,
        nodes=(
            *base.nodes,
            BehaviorNode(id="orphan", kind=BehaviorKind.CONSULT, label="Unreachable island"),
        ),
        edges=(
            *base.edges,
            BehaviorEdge(source="inspect_target_code", target="inspect_target_code"),
            BehaviorEdge(source="inspect_target_code", target="ghost_node"),
        ),
    )
    report = verify_behavior_graph(corrupted)
    codes = {v.code for v in report.violations}
    assert GraphViolationCode.UNREACHABLE_NODE in codes
    assert GraphViolationCode.SELF_LOOP in codes
    assert GraphViolationCode.DANGLING_EDGE_ENDPOINT in codes


# ---------------------------------------------------------------------------
# 6. Hypothesis Property-Based Graph Fuzzing
# ---------------------------------------------------------------------------


@settings(max_examples=75)
@given(
    mut_kind=st.sampled_from(sorted(MUTATE_KINDS)),
    skip_static=st.booleans(),
    skip_dynamic=st.booleans(),
)
def test_property_mutation_only_certifies_when_both_static_and_dynamic_run(
    mut_kind: BehaviorKind,
    skip_static: bool,
    skip_dynamic: bool,
) -> None:
    """Property: Any workspace mutation requires downstream static/dynamic gates per dirty rules."""
    steps: list[ExecutionStep] = [
        ExecutionStep(step_id="s_frame", kind=BehaviorKind.FRAME, summary="Frame"),
        ExecutionStep(step_id="s_bind", kind=BehaviorKind.BIND_CODES, summary="Bind"),
        ExecutionStep(step_id="s_cp", kind=BehaviorKind.CHECKPOINT, summary="Checkpoint"),
        ExecutionStep(step_id="s_mut", kind=mut_kind, summary="Mutate"),
    ]
    if not skip_static:
        steps.append(
            ExecutionStep(step_id="s_vs", kind=BehaviorKind.VERIFY_STATIC, summary="Static")
        )
    if not skip_dynamic:
        steps.append(
            ExecutionStep(step_id="s_vd", kind=BehaviorKind.VERIFY_DYNAMIC, summary="Dynamic")
        )
    # Route via epistemic node if both skipped so we isolate dataflow law from local edge ban
    if skip_static and skip_dynamic:
        steps.append(
            ExecutionStep(step_id="s_mem", kind=BehaviorKind.CURATE_MEMORY, summary="Memory")
        )
    steps.append(ExecutionStep(step_id="s_cert", kind=BehaviorKind.CERTIFY, summary="Certify"))

    report = verify_execution_trace(steps)

    needs_static = mut_kind != BehaviorKind.PROVISION
    needs_dynamic = mut_kind != BehaviorKind.SPECIFY

    expected_valid = (not (needs_static and skip_static)) and (not (needs_dynamic and skip_dynamic))
    assert report.is_valid == expected_valid


# ---------------------------------------------------------------------------
# 7. Async End-to-End pydantic-graph Runtime Integration Test
# ---------------------------------------------------------------------------


@dataclass
class _FrameStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.FRAME

    async def run(self, ctx: GraphRunContext[AgentExecutionState, None]) -> _BindStep:
        self.record_into_context(ctx, "frame", "Frame division-by-zero bug")
        ctx.state.problem.objective = "Guard zero divisor"
        return _BindStep()


@dataclass
class _BindStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.BIND_CODES

    async def run(self, ctx: GraphRunContext[AgentExecutionState, None]) -> _CheckpointStep:
        self.record_into_context(ctx, "bind", "Bind DbC precondition code")
        ctx.state.problem.bound_code_ids.add("JBC-DBC-001")
        return _CheckpointStep()


@dataclass
class _CheckpointStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CHECKPOINT

    async def run(self, ctx: GraphRunContext[AgentExecutionState, None]) -> _HypothesizeStep:
        self.record_into_context(ctx, "checkpoint", "Snapshot baseline")
        ctx.state.workspace.checkpoints.append("cp-0")
        return _HypothesizeStep(attempt=1)


@dataclass
class _HypothesizeStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.HYPOTHESIZE
    attempt: int

    async def run(self, ctx: GraphRunContext[AgentExecutionState, None]) -> _SynthesizeStep:
        self.record_into_context(ctx, "hypothesize", f"Hypothesis attempt {self.attempt}")
        ctx.state.world_model.active_hypotheses.append(f"hypothesis-{self.attempt}")
        return _SynthesizeStep(attempt=self.attempt)


@dataclass
class _SynthesizeStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.SYNTHESIZE
    attempt: int

    async def run(self, ctx: GraphRunContext[AgentExecutionState, None]) -> _VerifyStaticStep:
        self.record_into_context(ctx, "synthesize", "Apply candidate patch")
        ctx.state.workspace.modified_artifacts.add("calc.py")
        return _VerifyStaticStep(attempt=self.attempt)


@dataclass
class _VerifyStaticStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.VERIFY_STATIC
    attempt: int

    async def run(self, ctx: GraphRunContext[AgentExecutionState, None]) -> _VerifyDynamicStep:
        self.record_into_context(ctx, "verify_static", "Typecheck candidate patch")
        ctx.state.evidence.static_checks_passed = True
        return _VerifyDynamicStep(attempt=self.attempt)


@dataclass
class _VerifyDynamicStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.VERIFY_DYNAMIC
    attempt: int

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, None],
    ) -> _RevertStep | _CertifyStep:
        self.record_into_context(ctx, "verify_dynamic", "Run property suite")
        if self.attempt < 2:
            return _RevertStep(next_attempt=self.attempt + 1)
        ctx.state.evidence.dynamic_checks_passed = True
        return _CertifyStep()


@dataclass
class _RevertStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.REVERT
    next_attempt: int

    async def run(self, ctx: GraphRunContext[AgentExecutionState, None]) -> _HypothesizeStep:
        self.record_into_context(ctx, "revert", "Rollback failed attempt 1")
        ctx.state.workspace.modified_artifacts.clear()
        ctx.state.world_model.falsified_paths.append("hypothesis-1")
        return _HypothesizeStep(attempt=self.next_attempt)


@dataclass
class _CertifyStep(AtomicBehaviorGraphNode[None, str]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CERTIFY

    async def run(self, ctx: GraphRunContext[AgentExecutionState, None]) -> End[str]:
        self.record_into_context(ctx, "certify", "Seal verified patch")
        ctx.state.evidence.certified = True
        return End("certified:JBC-DBC-001")


async def test_pydantic_graph_cyclic_execution_produces_valid_induced_behavior_graph() -> None:
    builder = GraphBuilder(
        state_type=AgentExecutionState,
        input_type=_FrameStep,
        output_type=str,
    )
    builder.add(builder.edge_from(builder.start_node).to(_FrameStep))
    builder.add_reachable(_FrameStep)
    runtime_graph = builder.build()

    state = AgentExecutionState()
    output = await runtime_graph.run(state=state, inputs=_FrameStep())

    assert output == "certified:JBC-DBC-001"
    assert state.evidence.certified is True
    assert state.world_model.falsified_paths == ["hypothesis-1"]

    report = verify_execution_trace(state.trace, name="pydantic_graph_runtime_cycle")
    assert report.is_valid, f"Runtime trace failed verification: {report.violations}"
