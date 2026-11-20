"""Pillar 1: Complete Fibonacci-cardinal (F_8 = 21) basis of atomic coding-agent behaviors."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self

import icontract
from pydantic import BaseModel, Field, model_validator

from jezz.behaviors.fibonacci import FIBONACCI_BASIS_CARDINALITY, is_fibonacci
from jezz.guards import STRICT_FROZEN_CONFIG, jezz_callable


class StateDomain(StrEnum):
    """Orthogonal components of the agent execution state Sigma = (P, W, Omega, E)."""

    PROBLEM = "problem"
    WORLD_MODEL = "world_model"
    WORKSPACE = "workspace"
    EVIDENCE = "evidence"


class BehaviorStratum(StrEnum):
    """Functional strata partitioning the 21 atomic agent behaviors."""

    GOVERNANCE = "governance"
    EPISTEMIC = "epistemic"
    TOPOLOGY = "topology"
    MUTATION = "mutation"
    VERIFICATION = "verification"


class WorkspaceEffect(StrEnum):
    """Effect of an atomic behavior on the mutable workspace substrate."""

    NONE = "none"
    CHECKPOINT = "checkpoint"
    MUTATE = "mutate"
    RESTORE = "restore"
    SEAL = "seal"


class BehaviorKind(StrEnum):
    """The 21 (F_8) atomic behaviors spanning all coding-agent task graphs."""

    # Stratum I: Framing, Authority & Governance (3)
    FRAME = "frame"
    ELICIT = "elicit"
    BIND_CODES = "bind_codes"

    # Stratum II: Epistemic Sensing & Memory Control (4)
    INSPECT_CODE = "inspect_code"
    INSPECT_ENV = "inspect_env"
    CONSULT = "consult"
    CURATE_MEMORY = "curate_memory"

    # Stratum III: Abduction, Topology & Coordination (4)
    HYPOTHESIZE = "hypothesize"
    DECOMPOSE = "decompose"
    DELEGATE = "delegate"
    RECONCILE = "reconcile"

    # Stratum IV: Controlled State & Artifact Transformation (6)
    CHECKPOINT = "checkpoint"
    PROVISION = "provision"
    SPECIFY = "specify"
    INSTRUMENT = "instrument"
    SYNTHESIZE = "synthesize"
    REFACTOR = "refactor"

    # Stratum V: Verification, Recovery & Certification (4)
    VERIFY_STATIC = "verify_static"
    VERIFY_DYNAMIC = "verify_dynamic"
    REVERT = "revert"
    CERTIFY = "certify"


class BehaviorSpec(BaseModel):
    """Formal specification and state-transition contract of an atomic behavior."""

    model_config = STRICT_FROZEN_CONFIG

    code: str = Field(min_length=6, max_length=6, pattern=r"^BHV-(?:0[1-9]|[1-9][0-9])$")
    kind: BehaviorKind
    stratum: BehaviorStratum
    summary: str = Field(min_length=20, max_length=512)
    reads: frozenset[StateDomain] = Field(max_length=4)
    writes: frozenset[StateDomain] = Field(min_length=1, max_length=4)
    workspace_effect: WorkspaceEffect
    postcondition: str = Field(min_length=20, max_length=512)

    @model_validator(mode="after")
    def _enforce_contract_invariants(self) -> Self:
        if not self.writes:
            msg = f"{self.code} ({self.kind}) must write to at least one StateDomain."
            raise ValueError(msg)

        writes_workspace = StateDomain.WORKSPACE in self.writes
        has_workspace_effect = self.workspace_effect != WorkspaceEffect.NONE
        if writes_workspace != has_workspace_effect:
            msg = (
                f"{self.code} ({self.kind}) mismatch between WORKSPACE write ({writes_workspace}) "
                f"and workspace_effect ({self.workspace_effect})."
            )
            raise ValueError(msg)

        is_mutation_stratum = self.stratum == BehaviorStratum.MUTATION
        is_pre_verify_workspace_op = self.workspace_effect in {
            WorkspaceEffect.CHECKPOINT,
            WorkspaceEffect.MUTATE,
        }
        if is_mutation_stratum != is_pre_verify_workspace_op:
            msg = (
                f"{self.code} ({self.kind}) stratum ({self.stratum}) inconsistent with "
                f"workspace_effect ({self.workspace_effect})."
            )
            raise ValueError(msg)

        if self.workspace_effect == WorkspaceEffect.RESTORE and (
            self.kind != BehaviorKind.REVERT or StateDomain.WORLD_MODEL not in self.writes
        ):
            msg = "RESTORE effect is reserved for REVERT and must record negative knowledge."
            raise ValueError(msg)

        if self.workspace_effect == WorkspaceEffect.SEAL and (
            self.kind != BehaviorKind.CERTIFY
            or StateDomain.EVIDENCE not in self.reads
            or StateDomain.EVIDENCE not in self.writes
        ):
            msg = "SEAL effect is reserved for CERTIFY and must read and seal EVIDENCE."
            raise ValueError(msg)

        return self


_RAW_SPECS: tuple[BehaviorSpec, ...] = (
    # --- Stratum I: Governance (3) ---
    BehaviorSpec(
        code="BHV-01",
        kind=BehaviorKind.FRAME,
        stratum=BehaviorStratum.GOVERNANCE,
        summary="Parse signals into bounded objectives, deliverables, and surface ambiguities.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL}),
        writes=frozenset({StateDomain.PROBLEM}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "Objective, scope boundaries, and open ambiguities are explicitly enumerated."
        ),
    ),
    BehaviorSpec(
        code="BHV-02",
        kind=BehaviorKind.ELICIT,
        stratum=BehaviorStratum.GOVERNANCE,
        summary="Suspend autonomous execution to query external authority for decisions or grants.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL}),
        writes=frozenset({StateDomain.PROBLEM}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "Ambiguity prompt or permission escalation is emitted across authority boundary."
        ),
    ),
    BehaviorSpec(
        code="BHV-03",
        kind=BehaviorKind.BIND_CODES,
        stratum=BehaviorStratum.GOVERNANCE,
        summary="Select and attach mandatory building codes and invariants to active scope.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL}),
        writes=frozenset({StateDomain.PROBLEM}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition="Non-empty set of canonical building codes and invariants is bound to task.",
    ),
    # --- Stratum II: Epistemic Sensing & Memory Control (4) ---
    BehaviorSpec(
        code="BHV-04",
        kind=BehaviorKind.INSPECT_CODE,
        stratum=BehaviorStratum.EPISTEMIC,
        summary="Perform read-only lexical, AST, symbol, and dependency analysis of source code.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.WORLD_MODEL}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "Grounded structural and lexical codebase facts are recorded without mutation."
        ),
    ),
    BehaviorSpec(
        code="BHV-05",
        kind=BehaviorKind.INSPECT_ENV,
        stratum=BehaviorStratum.EPISTEMIC,
        summary="Observe execution substrate: toolchain, packages, VCS state, logs, and schemas.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.WORLD_MODEL}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition="Empirical environment, VCS, or runtime state observations are recorded.",
    ),
    BehaviorSpec(
        code="BHV-06",
        kind=BehaviorKind.CONSULT,
        stratum=BehaviorStratum.EPISTEMIC,
        summary="Retrieve authoritative external documentation, standards, RFCs, and schemas.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL}),
        writes=frozenset({StateDomain.WORLD_MODEL}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition="Normative external references are anchored in world model with citations.",
    ),
    BehaviorSpec(
        code="BHV-07",
        kind=BehaviorKind.CURATE_MEMORY,
        stratum=BehaviorStratum.EPISTEMIC,
        summary="Project raw observations into structured state and evict stale context noise.",
        reads=frozenset({StateDomain.WORLD_MODEL, StateDomain.EVIDENCE}),
        writes=frozenset({StateDomain.WORLD_MODEL}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "Working memory is compacted while preserving active invariants and negations."
        ),
    ),
    # --- Stratum III: Abduction, Topology & Coordination (4) ---
    BehaviorSpec(
        code="BHV-08",
        kind=BehaviorKind.HYPOTHESIZE,
        stratum=BehaviorStratum.TOPOLOGY,
        summary="Formulate falsifiable causal models and discriminating verification experiments.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL, StateDomain.EVIDENCE}),
        writes=frozenset({StateDomain.WORLD_MODEL}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "Candidate causal hypothesis and explicit falsification criterion are recorded."
        ),
    ),
    BehaviorSpec(
        code="BHV-09",
        kind=BehaviorKind.DECOMPOSE,
        stratum=BehaviorStratum.TOPOLOGY,
        summary="Factor objective into a directed sub-goal graph with disjoint write scopes.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL}),
        writes=frozenset({StateDomain.PROBLEM}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "Sub-tasks have explicit ordering edges, contracts, and non-overlapping ownership."
        ),
    ),
    BehaviorSpec(
        code="BHV-10",
        kind=BehaviorKind.DELEGATE,
        stratum=BehaviorStratum.TOPOLOGY,
        summary="Dispatch isolated subgraphs to subagents or workers with strict I/O schemas.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL}),
        writes=frozenset({StateDomain.WORLD_MODEL}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition="Bounded worker assignments are dispatched and tracked for reconciliation.",
    ),
    BehaviorSpec(
        code="BHV-11",
        kind=BehaviorKind.RECONCILE,
        stratum=BehaviorStratum.TOPOLOGY,
        summary="Synchronize delegated outputs and resolve boundary or state conflicts.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL, StateDomain.EVIDENCE}),
        writes=frozenset({StateDomain.WORLD_MODEL, StateDomain.EVIDENCE}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "All active delegated branches are accounted for and boundary conflicts resolved."
        ),
    ),
    # --- Stratum IV: Controlled State & Artifact Transformation (6) ---
    BehaviorSpec(
        code="BHV-12",
        kind=BehaviorKind.CHECKPOINT,
        stratum=BehaviorStratum.MUTATION,
        summary="Capture recoverable workspace snapshot or isolated branch prior to mutation.",
        reads=frozenset({StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.WORKSPACE, StateDomain.EVIDENCE}),
        workspace_effect=WorkspaceEffect.CHECKPOINT,
        postcondition="Deterministic restore handle exists for atomic workspace rollback.",
    ),
    BehaviorSpec(
        code="BHV-13",
        kind=BehaviorKind.PROVISION,
        stratum=BehaviorStratum.MUTATION,
        summary="Mutate execution substrate: dependencies, lockfiles, build graph, or sandbox.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.WORKSPACE}),
        workspace_effect=WorkspaceEffect.MUTATE,
        postcondition="Environment and dependency manifests reflect declared substrate changes.",
    ),
    BehaviorSpec(
        code="BHV-14",
        kind=BehaviorKind.SPECIFY,
        stratum=BehaviorStratum.MUTATION,
        summary="Encode formal types, schemas, interfaces, and pre/postcondition contracts.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.WORKSPACE}),
        workspace_effect=WorkspaceEffect.MUTATE,
        postcondition="Machine-checkable signatures, schemas, and invariants exist in artifacts.",
    ),
    BehaviorSpec(
        code="BHV-15",
        kind=BehaviorKind.INSTRUMENT,
        stratum=BehaviorStratum.MUTATION,
        summary="Author executable tests, property generators, repro harnesses, and probes.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.WORKSPACE}),
        workspace_effect=WorkspaceEffect.MUTATE,
        postcondition="Executable verification or diagnostic harnesses target bound invariants.",
    ),
    BehaviorSpec(
        code="BHV-16",
        kind=BehaviorKind.SYNTHESIZE,
        stratum=BehaviorStratum.MUTATION,
        summary="Construct or mutate implementation artifacts to satisfy formal contracts.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.WORKSPACE}),
        workspace_effect=WorkspaceEffect.MUTATE,
        postcondition="Implementation artifacts are updated to realize target specification.",
    ),
    BehaviorSpec(
        code="BHV-17",
        kind=BehaviorKind.REFACTOR,
        stratum=BehaviorStratum.MUTATION,
        summary="Execute structure-mutating, semantics-preserving AST/symbol transformations.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.WORKSPACE}),
        workspace_effect=WorkspaceEffect.MUTATE,
        postcondition="Internal structure is transformed while preserving observable semantics.",
    ),
    # --- Stratum V: Verification, Recovery & Certification (4) ---
    BehaviorSpec(
        code="BHV-18",
        kind=BehaviorKind.VERIFY_STATIC,
        stratum=BehaviorStratum.VERIFICATION,
        summary="Enforce decidable static gates: syntax, strict types, AST, and static codes.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.EVIDENCE}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "Static verification verdict and diagnostics are appended to evidence ledger."
        ),
    ),
    BehaviorSpec(
        code="BHV-19",
        kind=BehaviorKind.VERIFY_DYNAMIC,
        stratum=BehaviorStratum.VERIFICATION,
        summary="Execute runtime, property-based, and boundary suites against postconditions.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORKSPACE}),
        writes=frozenset({StateDomain.EVIDENCE}),
        workspace_effect=WorkspaceEffect.NONE,
        postcondition=(
            "Empirical execution transcript and pass/fail postcondition verdict recorded."
        ),
    ),
    BehaviorSpec(
        code="BHV-20",
        kind=BehaviorKind.REVERT,
        stratum=BehaviorStratum.VERIFICATION,
        summary="Atomically restore workspace to checkpoint and log falsified path in memory.",
        reads=frozenset({StateDomain.WORKSPACE, StateDomain.EVIDENCE}),
        writes=frozenset({StateDomain.WORKSPACE, StateDomain.WORLD_MODEL, StateDomain.EVIDENCE}),
        workspace_effect=WorkspaceEffect.RESTORE,
        postcondition="Workspace matches prior checkpoint and failed hypothesis is recorded.",
    ),
    BehaviorSpec(
        code="BHV-21",
        kind=BehaviorKind.CERTIFY,
        stratum=BehaviorStratum.VERIFICATION,
        summary="Audit evidence ledger against bound codes, seal state, and emit deliverable.",
        reads=frozenset({StateDomain.PROBLEM, StateDomain.WORLD_MODEL, StateDomain.EVIDENCE}),
        writes=frozenset({StateDomain.WORKSPACE, StateDomain.EVIDENCE}),
        workspace_effect=WorkspaceEffect.SEAL,
        postcondition=(
            "All bound building codes have verified evidence citations; deliverable sealed."
        ),
    ),
)


@icontract.ensure(lambda basis, result: result is None and is_fibonacci(len(basis)))
@jezz_callable
def validate_basis_invariants(
    basis: Mapping[BehaviorKind, BehaviorSpec],
) -> None:
    """Verify Pillar 1 structural invariants on a candidate behavior basis."""
    count = len(basis)
    if not is_fibonacci(count):
        msg = f"Basis cardinality {count} violates the Fibonacci constraint."
        raise ValueError(msg)
    if count != FIBONACCI_BASIS_CARDINALITY:
        msg = f"Expected canonical F_8 = {FIBONACCI_BASIS_CARDINALITY} behaviors, got {count}."
        raise ValueError(msg)

    expected_kinds = set(BehaviorKind)
    if set(basis.keys()) != expected_kinds:
        missing = expected_kinds - set(basis.keys())
        msg = f"Behavior basis is missing BehaviorKind entries: {sorted(missing)}"
        raise ValueError(msg)

    codes = [spec.code for spec in basis.values()]
    expected_codes = [f"BHV-{i:02d}" for i in range(1, count + 1)]
    if sorted(codes) != expected_codes:
        msg = f"Behavior codes must form contiguous sequence BHV-01..BHV-{count:02d}, got {codes}."
        raise ValueError(msg)

    for kind, spec in basis.items():
        if spec.kind != kind:
            msg = f"Key {kind} mapped to mismatched BehaviorSpec kind {spec.kind}."
            raise ValueError(msg)

    strata_present = {spec.stratum for spec in basis.values()}
    if strata_present != set(BehaviorStratum):
        missing_strata = set(BehaviorStratum) - strata_present
        msg = f"BehaviorStratum values have zero behaviors assigned: {sorted(missing_strata)}"
        raise ValueError(msg)


def _build_canonical_basis() -> Mapping[BehaviorKind, BehaviorSpec]:
    mapping: dict[BehaviorKind, BehaviorSpec] = {}
    for spec in _RAW_SPECS:
        if spec.kind in mapping:
            msg = f"Duplicate BehaviorSpec for {spec.kind}"
            raise ValueError(msg)
        mapping[spec.kind] = spec
    validate_basis_invariants(mapping)
    return MappingProxyType(mapping)


BEHAVIOR_BASIS: Final[Mapping[BehaviorKind, BehaviorSpec]] = _build_canonical_basis()


@icontract.ensure(lambda kind, result: result.kind == kind)
@jezz_callable
def get_behavior_spec(kind: BehaviorKind) -> BehaviorSpec:
    """Return the canonical ``BehaviorSpec`` for ``kind``."""
    return BEHAVIOR_BASIS[kind]
