"""Pillar 4: 21-behavior execution board and bounded Pydantic AI specialist agents."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final, Protocol, runtime_checkable

import icontract
from pydantic import Field
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.models import Model

from jezz.agents.schemas import (
    AgentInvocationTelemetry,
    BehaviorExecutionBinding,
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    ExecutionEngineKind,
    FilePatchArtifact,
    FramedMandateProposal,
    InstrumentationSuiteBundle,
    PceRuntimeBudget,
    RefactorPatchBundle,
    RemediationHypothesis,
    ScopeDecompositionPlan,
    ToolsetAccessLevel,
)
from jezz.agents.workspace import (
    JezzAgentDeps,
    build_readonly_toolset,
    build_scoped_write_toolset,
    find_type_alias_misuse,
    verify_runtime_importable_patches,
)
from jezz.behaviors.fibonacci import FIBONACCI_BASIS_CARDINALITY, is_fibonacci
from jezz.behaviors.taxonomy import BEHAVIOR_BASIS, BehaviorKind, WorkspaceEffect
from jezz.codes.ast_utils import SourceUnit, parse_source_unit
from jezz.guards import InstanceGuard, jezz_callable
from jezz.methodology.dossier import extract_compiled_symbols
from jezz.methodology.mandate import compute_scope_execution_waves

DETERMINISTIC_BEHAVIOR_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.BIND_CODES,
        BehaviorKind.RECONCILE,
        BehaviorKind.CHECKPOINT,
        BehaviorKind.VERIFY_STATIC,
        BehaviorKind.VERIFY_DYNAMIC,
        BehaviorKind.REVERT,
        BehaviorKind.CERTIFY,
    }
)

BEHAVIOR_EXECUTION_BOARD: Final[Mapping[BehaviorKind, BehaviorExecutionBinding]] = MappingProxyType(
    {
        BehaviorKind.FRAME: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.FRAME,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="mandate_framer",
            rationale="Translates user intent into a typed anti-Agile EngineeringMandate.",
        ),
        BehaviorKind.ELICIT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.ELICIT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="ambiguity_elicitor",
            rationale="Halts construction to request human disambiguation on open ambiguities.",
        ),
        BehaviorKind.BIND_CODES: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.BIND_CODES,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="permit_issuer",
            rationale="Zero-entropy JBC v1.1 CodePermit and hazard-trigger binder.",
        ),
        BehaviorKind.INSPECT_CODE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSPECT_CODE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="code_inspector",
            rationale="Grounds WorldModelState via read-only AST and file inspection tools.",
        ),
        BehaviorKind.INSPECT_ENV: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSPECT_ENV,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="env_inspector",
            rationale="Reads runtime toolchain and manifest metadata without mutation capability.",
        ),
        BehaviorKind.CONSULT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CONSULT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="standards_consultant",
            rationale="Queries primary technical references and standards citations.",
        ),
        BehaviorKind.CURATE_MEMORY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CURATE_MEMORY,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="memory_curator",
            rationale="Compacts grounded observations and negative knowledge in WorldModelState.",
        ),
        BehaviorKind.HYPOTHESIZE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.HYPOTHESIZE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="causal_diagnostician",
            rationale="Diagnoses RED_TAG citations after REVERT to form a remediation plan.",
        ),
        BehaviorKind.DECOMPOSE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.DECOMPOSE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="scope_architect",
            rationale="Partitions work across pairwise-disjoint ModuleWriteScopes.",
        ),
        BehaviorKind.DELEGATE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.DELEGATE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="delegation_coordinator",
            rationale="Dispatches parallel TaskGroup subagents over disjoint ModuleWriteScopes.",
        ),
        BehaviorKind.RECONCILE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.RECONCILE,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="scope_reconciler",
            rationale="Verifies pairwise disjointness of delegated write sets before commit.",
        ),
        BehaviorKind.CHECKPOINT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CHECKPOINT,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="snapshot_keeper",
            rationale="Captures deterministic pre-mutation workspace snapshots.",
        ),
        BehaviorKind.PROVISION: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.PROVISION,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="dependency_provisioner",
            rationale="Updates declared manifest dependencies within authorized scope.",
        ),
        BehaviorKind.SPECIFY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.SPECIFY,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="contract_specifier",
            rationale="Authors Phase III strict Pydantic schemas and @icontract obligations.",
        ),
        BehaviorKind.INSTRUMENT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSTRUMENT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="falsification_instrumenter",
            rationale="Authors Phase III pytest and Hypothesis falsification suites.",
        ),
        BehaviorKind.SYNTHESIZE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.SYNTHESIZE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="code_synthesizer",
            rationale="Synthesizes Phase IV implementation satisfying locked contracts.",
        ),
        BehaviorKind.REFACTOR: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.REFACTOR,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="structural_refactorer",
            rationale="Applies behavior-preserving structural transformations within scope.",
        ),
        BehaviorKind.VERIFY_STATIC: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.VERIFY_STATIC,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="rough_in_inspector",
            rationale="Runs deterministic JBC v1.1 AST, complexity, cohesion, and security gates.",
        ),
        BehaviorKind.VERIFY_DYNAMIC: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.VERIFY_DYNAMIC,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="final_gate_inspector",
            rationale="Executes dynamic tests, typecheck, lint, and runtime budget watchdogs.",
        ),
        BehaviorKind.REVERT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.REVERT,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="rollback_enforcer",
            rationale="Restores clean checkpoint on RED_TAG and records falsified paths.",
        ),
        BehaviorKind.CERTIFY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CERTIFY,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="occupancy_certifier",
            rationale="Issues SHA-256 CertificateOfOccupancy and audits PCE Dossier.",
        ),
    }
)


@icontract.ensure(
    lambda result: is_fibonacci(len(result)) and len(result) == FIBONACCI_BASIS_CARDINALITY
)
@jezz_callable
def validate_execution_board_invariants() -> tuple[BehaviorExecutionBinding, ...]:
    """Verify 21-behavior coverage, write-capability isolation, and zero-entropy gate locks."""
    if set(BEHAVIOR_EXECUTION_BOARD) != set(BehaviorKind):
        msg = "BEHAVIOR_EXECUTION_BOARD must map all 21 BehaviorKinds bijectively."
        raise ValueError(msg)

    for kind, binding in BEHAVIOR_EXECUTION_BOARD.items():
        if binding.behavior_kind != kind:
            msg = f"Board key '{kind}' mismatches binding.behavior_kind '{binding.behavior_kind}'."
            raise ValueError(msg)
        is_mutate = BEHAVIOR_BASIS[kind].workspace_effect == WorkspaceEffect.MUTATE
        has_write = binding.toolset_access == ToolsetAccessLevel.SCOPED_WRITE
        if is_mutate != has_write:
            msg = (
                f"Capability violation on '{kind}': SCOPED_WRITE ({has_write}) must match "
                f"WorkspaceEffect.MUTATE ({is_mutate})."
            )
            raise ValueError(msg)
        if kind in DETERMINISTIC_BEHAVIOR_KINDS and (
            binding.engine_kind != ExecutionEngineKind.DETERMINISTIC_ENGINE
            or binding.toolset_access != ToolsetAccessLevel.NONE
        ):
            msg = f"Gate behavior '{kind}' must run as a toolless DETERMINISTIC_ENGINE."
            raise ValueError(msg)

    return tuple(BEHAVIOR_EXECUTION_BOARD.values())


@dataclass(frozen=True)
class PceModelSuite:
    """Pluggable Pydantic AI models assigned to each generative PCE specialist role."""

    frame_model: Model
    inspect_model: Model
    specify_model: Model
    instrument_model: Model
    synthesize_model: Model
    hypothesize_model: Model
    refactor_model: Model | None = None
    decompose_model: Model | None = None


type GuardedModel = Annotated[Model, InstanceGuard(Model)]
type GuardedAgent[OutT] = Annotated[Agent[JezzAgentDeps, OutT], InstanceGuard(Agent)]
type BoundedPrompt = Annotated[str, Field(min_length=1, max_length=65_536)]


@jezz_callable
def uniform_model_suite(
    model: GuardedModel,
) -> Annotated[PceModelSuite, InstanceGuard(PceModelSuite)]:
    """Bind a single Pydantic AI ``Model`` across all generative PCE specialist roles."""
    return PceModelSuite(
        frame_model=model,
        inspect_model=model,
        specify_model=model,
        instrument_model=model,
        synthesize_model=model,
        hypothesize_model=model,
        refactor_model=model,
        decompose_model=model,
    )


def _enforce_patch_scope_and_parse(
    patches: Sequence[FilePatchArtifact],
    allowed_files: frozenset[str],
) -> tuple[SourceUnit, ...]:
    units: list[SourceUnit] = []
    for patch in patches:
        if patch.file_path not in allowed_files:
            msg = (
                f"Patch target '{patch.file_path}' is outside authorized write scope "
                f"{sorted(allowed_files)}."
            )
            raise ModelRetry(msg)
        units.append(parse_source_unit(patch.file_path, patch.source_code))
    return tuple(units)


def _parse_and_rough_in_check(
    deps: JezzAgentDeps,
    patches: Sequence[FilePatchArtifact],
    stage_label: str,
) -> tuple[SourceUnit, ...]:
    units = _enforce_patch_scope_and_parse(patches, deps.sandbox.authorized_files)
    report = deps.inspector.inspect_rough_in(units, permit=deps.permit)
    if not report.is_occupiable:
        cited = " | ".join(
            f"{f.code_id} (L{f.location.line}): {f.message} -> Fix: {f.remediation}"
            for f in report.unwaived_violations()[:6]
        )
        raise ModelRetry(f"{stage_label} failed JBC rough-in: {cited}")
    alias_err = find_type_alias_misuse(units, deps.sandbox.load_units())
    if alias_err is not None:
        raise ModelRetry(f"{stage_label} failed PEP 695 type-alias check: {alias_err}")
    return units


def _validate_specify_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: ContractSpecificationBundle,
) -> ContractSpecificationBundle:
    units = _parse_and_rough_in_check(
        ctx.deps,
        bundle.contract_patches,
        "Phase III contracts",
    )
    compiled = extract_compiled_symbols(units)
    missing_declared = [sym for sym in bundle.declared_symbols if sym not in compiled]
    if missing_declared:
        raise ModelRetry(
            f"Contract patches omit symbols listed in declared_symbols: {missing_declared}."
        )
    runtime_err = verify_runtime_importable_patches(
        ctx.deps.sandbox,
        bundle.contract_patches,
        ignore_missing_local_imports=False,
    )
    if runtime_err is not None:
        raise ModelRetry(f"Phase III contracts failed runtime import gate: {runtime_err}")
    return bundle


def _validate_instrument_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: InstrumentationSuiteBundle,
) -> InstrumentationSuiteBundle:
    for patch in bundle.test_patches:
        if not patch.file_path.startswith("tests/"):
            raise ModelRetry(f"Instrumentation file '{patch.file_path}' must reside under tests/.")
    test_units = _parse_and_rough_in_check(
        ctx.deps,
        bundle.test_patches,
        "Phase III falsification suite",
    )
    if ctx.deps.mandate is not None:
        patched_paths = {u.file_path for u in test_units}
        prior_tests = [
            u
            for u in ctx.deps.sandbox.load_units()
            if u.is_test_file and u.file_path not in patched_paths
        ]
        compiled_tests = extract_compiled_symbols((*prior_tests, *test_units))
        missing_verif = [
            inv.verification_symbol
            for inv in ctx.deps.mandate.invariants
            if inv.verification_symbol not in compiled_tests
        ]
        if missing_verif:
            raise ModelRetry(
                f"Instrumentation test patches omit mandated verification_symbol(s): "
                f"{missing_verif}. Define exact test functions matching each invariant."
            )
    return bundle


def _combined_workspace_symbols(
    deps: JezzAgentDeps,
    patch_units: Sequence[SourceUnit],
) -> frozenset[str]:
    overridden = {u.file_path for u in patch_units}
    existing = [u for u in deps.sandbox.load_units() if u.file_path not in overridden]
    return extract_compiled_symbols((*existing, *patch_units))


def _validate_synthesize_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: ConstructionPatchBundle,
) -> ConstructionPatchBundle:
    raw_allowed = (
        ctx.deps.active_scope.owned_files
        if ctx.deps.active_scope is not None
        else ctx.deps.sandbox.authorized_files
    )
    allowed = frozenset(p for p in raw_allowed if not p.startswith(("tests/", "test/")))
    patch_units = _enforce_patch_scope_and_parse(bundle.implementation_patches, allowed)
    if ctx.deps.mandate is not None:
        compiled = _combined_workspace_symbols(ctx.deps, patch_units)
        expected = (
            list(ctx.deps.active_scope.contract_symbols)
            if ctx.deps.active_scope is not None
            else [
                *[inv.verification_symbol for inv in ctx.deps.mandate.invariants],
                *[sym for s in ctx.deps.mandate.write_scopes for sym in s.contract_symbols],
            ]
        )
        missing = [sym for sym in expected if sym not in compiled]
        if missing:
            raise ModelRetry(f"Implementation omits mandated symbol(s): {missing}.")
    alias_err = find_type_alias_misuse(patch_units, ctx.deps.sandbox.load_units())
    if alias_err is not None:
        raise ModelRetry(f"Implementation failed PEP 695 type-alias check: {alias_err}")
    runtime_err = verify_runtime_importable_patches(
        ctx.deps.sandbox,
        bundle.implementation_patches,
        ignore_missing_local_imports=(ctx.deps.active_scope is not None),
    )
    if runtime_err is not None:
        raise ModelRetry(f"Implementation failed runtime import gate: {runtime_err}")
    return bundle


def _expected_scope_symbols(deps: JezzAgentDeps) -> list[str]:
    if deps.mandate is None:
        return []
    if deps.active_scope is not None:
        return list(deps.active_scope.contract_symbols)
    return [
        *[inv.verification_symbol for inv in deps.mandate.invariants],
        *[sym for s in deps.mandate.write_scopes for sym in s.contract_symbols],
    ]


def _validate_refactor_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: RefactorPatchBundle,
) -> RefactorPatchBundle:
    raw_allowed = (
        ctx.deps.active_scope.owned_files
        if ctx.deps.active_scope is not None
        else ctx.deps.sandbox.authorized_files
    )
    allowed = frozenset(p for p in raw_allowed if not p.startswith(("tests/", "test/")))
    patch_units = _enforce_patch_scope_and_parse(bundle.refactored_patches, allowed)
    _parse_and_rough_in_check(ctx.deps, bundle.refactored_patches, "Phase IV brownfield refactor")
    compiled = _combined_workspace_symbols(ctx.deps, patch_units)
    required = [*bundle.preserved_symbols, *_expected_scope_symbols(ctx.deps)]
    missing = [sym for sym in required if sym not in compiled]
    if missing:
        raise ModelRetry(f"Refactored output omits required/preserved symbol(s): {missing}.")
    runtime_err = verify_runtime_importable_patches(
        ctx.deps.sandbox,
        bundle.refactored_patches,
        ignore_missing_local_imports=(ctx.deps.active_scope is not None),
    )
    if runtime_err is not None:
        raise ModelRetry(f"Refactored output failed runtime import gate: {runtime_err}")
    return bundle


_JBC_CANONICAL_EXEMPLAR: Final[str] = (
    "Canonical JBC v1.1 Idioms (copy exact jezz.guards signatures):\n"
    "```python\n"
    "from types import MappingProxyType\n"
    "from typing import Annotated, Final\n"
    "import icontract\n"
    "from pydantic import BaseModel, Field\n"
    "from jezz.guards import (\n"
    "    STRICT_FROZEN_CONFIG, BudgetExceededError, ContainedPath, IdentifierStr,\n"
    "    LoopBudget, NonNegativeInt, PositiveInt, ShortText, jezz_callable, read_bounded_text,\n"
    ")\n"
    "type RateBps = Annotated[int, Field(ge=0, le=10_000)]  # NEVER subscript BoundedInt[...]\n"
    "_RATES: Final = MappingProxyType({'a': 10, 'b': 20})   # <=1 elif; use MappingProxyType\n"
    "class Order(BaseModel):\n"
    "    model_config = STRICT_FROZEN_CONFIG\n"
    "    item_id: IdentifierStr\n"
    "    rate_bps: RateBps\n"
    "@icontract.require(lambda max_steps: max_steps >= 1)\n"
    "@icontract.ensure(lambda result: result >= 0)\n"
    "@jezz_callable\n"
    "def step_loop(\n"
    "    items: Annotated[tuple[IdentifierStr, ...], Field(min_length=1, max_length=512)],\n"
    "    *, max_steps: PositiveInt = 1000,\n"
    ") -> NonNegativeInt:\n"
    "    budget = LoopBudget(max_steps=max_steps)  # required on every while in src/ and tests/\n"
    "    rem = list(items)\n"
    "    while rem:\n"
    "        budget.tick()  # raises BudgetExceededError(RuntimeError) on overrun\n"
    "        rem.pop()\n"
    "    return len(items)\n"
    "```"
)


_JBC_STATUTORY_BRIEF: Final[str] = (
    "JBC v1.1 Statutory Requirements: "
    "(1) Every BaseModel must set model_config = STRICT_FROZEN_CONFIG (from jezz.guards) or "
    "ConfigDict(frozen=True, extra='forbid', strict=True, allow_inf_nan=False), and bound every "
    "field via jezz.guards (PositiveInt, NonNegativeInt, BoundedInt, FiniteFloat, IdentifierStr "
    "1..256, ShortText 1..1024, BoundedText 0..65_536 — PEP 695 TypeAliasType aliases, NEVER "
    "subscript BoundedInt[...] or call IdentifierStr('x') at runtime; pass plain literals and "
    "use Annotated[int, Field(ge=..., le=...)] for custom ranges), "
    "StrEnum/Literal/BaseModel, or Annotated[..., Field(...)]. "
    "(2) Every callable requires 100% parameter and return type annotations with zero Any and no "
    "untyped dict/Mapping[str, object] parameters or returns; decorate every public non-test "
    "callable with @jezz_callable (from jezz.guards) and bound all public scalar parameters. "
    "(3) Attach falsifiable @icontract.require / @icontract.ensure predicates (@ensure must "
    "reference result; guard divisor parameters); raise specific exceptions with diagnostic "
    "messages ('from err' inside except); never use silent except, while without "
    "LoopBudget.tick(), print(), TODO/FIXME, or # noqa/# type: ignore. "
    "(4) Respect structural limits: <= 35 statements, <= 6 parameters, nesting <= 4, Cognitive "
    "Complexity <= 15, <= 1 elif per if (use MappingProxyType dispatch tables, match, or early "
    "guard returns), and never place else/elif after return/raise/break/continue."
)


def _validate_frame_output(
    ctx: RunContext[JezzAgentDeps],
    proposal: FramedMandateProposal,
) -> FramedMandateProposal:
    _ = ctx
    for scope in proposal.mandate.write_scopes:
        has_src = any(not rel.startswith(("tests/", "test/")) for rel in scope.owned_files)
        if not has_src:
            raise ModelRetry(
                f"ModuleWriteScope '{scope.scope_id}' owns only test files "
                f"{sorted(scope.owned_files)}. Include each tests/... file inside the same "
                "ModuleWriteScope as the non-test src/... module it verifies."
            )
    return proposal


@jezz_callable
def build_frame_agent(model: GuardedModel) -> GuardedAgent[FramedMandateProposal]:
    """Build BHV-01 FRAME specialist producing typed anti-Agile EngineeringMandates."""
    agent = Agent[JezzAgentDeps, FramedMandateProposal](
        model,
        output_type=FramedMandateProposal,
        deps_type=JezzAgentDeps,
        name="jezz_mandate_framer",
        retries=3,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "Convert the engineering request into a concise EngineeringMandate (mandate_id "
            "matching ^MND-[A-Za-z0-9_-]+$, invariant_id matching ^INV-[0-9]{3,6}$). "
            "Use pure Python identifiers for verification_symbol (e.g. 'test_quota_limit') and "
            "contract_symbols (never file paths or 'file.py:sym' pytest nodeids). Every "
            "ModuleWriteScope must own at least one non-test src/.../*.py file (place paired "
            "tests/test_*.py files inside the same ModuleWriteScope rather than a separate "
            "test-only scope). State invariants as strict falsifiable conditions; never emit "
            "Agile user stories ('As a ..., I want ...'), Markdown checkboxes ('- [ ]'), "
            "ceremony jargon, or weasel modals ('should'/'might'/'ideally'/'maybe'/'try to'). "
            "Populate open_ambiguities whenever material domain constraints, bounds, failure "
            "policies, or edge-case semantics are under-determined rather than guessing. When "
            "'Authoritative Clarifications Resolving Open Ambiguities' is provided, integrate "
            "those answers into concrete invariants/write_scopes and set open_ambiguities=() "
            "once resolved."
        ),
    )
    agent.output_validator(_validate_frame_output)
    return agent


def _validate_decompose_output(
    ctx: RunContext[JezzAgentDeps],
    plan: ScopeDecompositionPlan,
) -> ScopeDecompositionPlan:
    try:
        compute_scope_execution_waves(plan.write_scopes)
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    for scope in plan.write_scopes:
        has_src = any(not rel.startswith(("tests/", "test/")) for rel in scope.owned_files)
        if not has_src:
            raise ModelRetry(
                f"ModuleWriteScope '{scope.scope_id}' owns only test files "
                f"{sorted(scope.owned_files)}; include at least one non-test src/.../*.py module."
            )
    if ctx.deps.mandate is not None:
        verif_syms = {inv.verification_symbol for inv in ctx.deps.mandate.invariants}
        orig_syms = {
            sym
            for s in ctx.deps.mandate.write_scopes
            for sym in s.contract_symbols
            if sym not in verif_syms
        }
        planned_syms = {sym for s in plan.write_scopes for sym in s.contract_symbols}
        missing = sorted(orig_syms - planned_syms)
        if missing:
            raise ModelRetry(
                f"Decomposed write_scopes dropped mandated contract_symbols: {missing}."
            )
    return plan


@jezz_callable
def build_decompose_agent(model: GuardedModel) -> GuardedAgent[ScopeDecompositionPlan]:
    """Build BHV-09 DECOMPOSE specialist refining scopes and DAG waves over the AST graph."""
    agent = Agent[JezzAgentDeps, ScopeDecompositionPlan](
        model,
        output_type=ScopeDecompositionPlan,
        deps_type=JezzAgentDeps,
        name="jezz_scope_architect",
        retries=3,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "Refine the EngineeringMandate's ModuleWriteScopes using the grounded Workspace AST "
            "Graph and epistemic observations provided in the prompt. Partition files into "
            "pairwise-disjoint ModuleWriteScopes where each scope owns at least one non-test "
            "src/.../*.py file (pair tests/test_*.py files inside the scope they verify). "
            "Populate `depends_on` with upstream `scope_id`s whenever a scope imports types or "
            "callables synthesized in an upstream scope, forming a strict DAG (zero cycles, "
            "preventing JBC-COG-003 circular imports by design). Preserve all mandated "
            "contract_symbols."
        ),
    )
    agent.output_validator(_validate_decompose_output)
    return agent


@jezz_callable
def build_inspect_agent(model: GuardedModel) -> GuardedAgent[EpistemicObservation]:
    """Build BHV-04 INSPECT_CODE read-only epistemic specialist."""
    return Agent[JezzAgentDeps, EpistemicObservation](
        model,
        output_type=EpistemicObservation,
        deps_type=JezzAgentDeps,
        name="jezz_code_inspector",
        retries=2,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "If 'Existing Authorized Files On Disk' in the prompt is empty ([]), return "
            "EpistemicObservation immediately in a single turn (inspected_files=(), "
            "discovered_symbols=()) without calling any tools. Otherwise summarize the inline "
            "snapshots directly, calling read tools only for existing files omitted inline."
        ),
    )


@jezz_callable
def build_specify_agent(model: GuardedModel) -> GuardedAgent[ContractSpecificationBundle]:
    """Build BHV-14 SPECIFY specialist with @output_validator JBC rough-in breaker."""
    agent = Agent[JezzAgentDeps, ContractSpecificationBundle](
        model,
        output_type=ContractSpecificationBundle,
        deps_type=JezzAgentDeps,
        name="jezz_contract_specifier",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=(
            "Author Phase III data contracts (strict Pydantic BaseModels, StrEnums, and bounded "
            "Annotated type aliases) in authorized non-test src/... contract file(s). Authorized "
            "paths and existing file contents are already provided inline in the prompt—do NOT "
            "call read/list tools on absent or already-inlined files. Never write tests/... files "
            "and never emit unimplemented pass/.../NotImplementedError function stubs (concrete "
            "implementation callables belong in Phase IV SYNTHESIZE). Include only symbols "
            "defined in contract_patches inside declared_symbols. Draft the complete file in one "
            "pass and call _tool_preview_rough_in once on the full candidate file before "
            f"returning. {_JBC_STATUTORY_BRIEF}\n{_JBC_CANONICAL_EXEMPLAR}"
        ),
    )
    agent.output_validator(_validate_specify_output)
    return agent


@jezz_callable
def build_instrument_agent(model: GuardedModel) -> GuardedAgent[InstrumentationSuiteBundle]:
    """Build BHV-15 INSTRUMENT specialist enforcing non-tautological falsification suites."""
    agent = Agent[JezzAgentDeps, InstrumentationSuiteBundle](
        model,
        output_type=InstrumentationSuiteBundle,
        deps_type=JezzAgentDeps,
        name="jezz_falsification_instrumenter",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=(
            "Use the inline 'Existing Authorized File Snapshots' directly (do NOT call read/list "
            "tools on already-inlined or absent files) so imports and field names match Phase III "
            "contracts exactly. Author pytest and Hypothesis (@given) falsification suites under "
            "authorized tests/... file(s) defining every mandated invariant verification_symbol. "
            "Every test and helper callable in tests/ must have 100% parameter and '-> None' "
            "return annotations with zero Any, <= 6 parameters (when @given needs >6 values, use "
            "'@given(st.data())' with 'data: st.DataObject' instead of >6 args or untyped "
            "@st.composite), and <= 35 statements; name any delegated assertion helper "
            "'_assert_*', '_verify_*', '_check_*', or '_expect_*' (JBC-VRF-001). When testing "
            "that invalid inputs or step-budget overruns raise an error, expect "
            "(ValueError, TypeError, icontract.ViolationError, BudgetExceededError); never "
            "combine @given with function-scoped fixtures like tmp_path, and guard any helper "
            "while loop in tests/ with LoopBudget(max_steps=...). Preview each complete test file "
            f"once via _tool_preview_rough_in before returning. {_JBC_STATUTORY_BRIEF}\n"
            f"{_JBC_CANONICAL_EXEMPLAR}"
        ),
    )
    agent.output_validator(_validate_instrument_output)
    return agent


@jezz_callable
def build_synthesize_agent(model: GuardedModel) -> GuardedAgent[ConstructionPatchBundle]:
    """Build BHV-16 SYNTHESIZE specialist constrained to authorized ModuleWriteScopes."""
    agent = Agent[JezzAgentDeps, ConstructionPatchBundle](
        model,
        output_type=ConstructionPatchBundle,
        deps_type=JezzAgentDeps,
        name="jezz_code_synthesizer",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=(
            "Use the inline 'Existing Authorized File Snapshots' directly (do NOT call read/list "
            "tools on already-inlined or absent files) so imports, types, and postconditions "
            "align with locked Phase III contracts and falsification suites. Emit complete "
            "src/... implementation patches strictly within your authorized ModuleWriteScope "
            "without modifying locked tests/... files or removing Phase III contract symbols; "
            "keep helper functions that do not read 'self' at module level rather than as "
            "stateless instance methods on classes (JBC-COG-005). Preview each complete file once "
            f"via _tool_preview_rough_in before returning. {_JBC_STATUTORY_BRIEF}\n"
            f"{_JBC_CANONICAL_EXEMPLAR}"
        ),
    )
    agent.output_validator(_validate_synthesize_output)
    return agent


@jezz_callable
def build_refactor_agent(model: GuardedModel) -> GuardedAgent[RefactorPatchBundle]:
    """Build BHV-17 REFACTOR specialist renovating brownfield modules to JBC v1.1 occupancy."""
    agent = Agent[JezzAgentDeps, RefactorPatchBundle](
        model,
        output_type=RefactorPatchBundle,
        deps_type=JezzAgentDeps,
        name="jezz_structural_refactorer",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=(
            "Renovate the existing non-test src/... file(s) shown in 'Existing Authorized File "
            "Snapshots' so every file achieves zero JBC v1.1 violations while preserving public "
            "symbol names, call signatures compatible with locked tests, and observable domain "
            "semantics. Do NOT modify tests/... files or call read/list tools on already-inlined "
            "files. Remediate every cited baseline RED_TAG finding (attach @jezz_callable, "
            "STRICT_FROZEN_CONFIG, bounded jezz.guards / Annotated[..., Field(...)] types, "
            "falsifiable @icontract.require/@icontract.ensure, LoopBudget on while loops, "
            "MappingProxyType dispatch / <=1 elif, LCOM4 == 1 cohesion, narrow chained exceptions, "
            "and ContainedPath filesystem containment). Preview each complete candidate file once "
            f"via _tool_preview_rough_in before returning. {_JBC_STATUTORY_BRIEF}\n"
            f"{_JBC_CANONICAL_EXEMPLAR}"
        ),
    )
    agent.output_validator(_validate_refactor_output)
    return agent


@jezz_callable
def build_hypothesize_agent(model: GuardedModel) -> GuardedAgent[RemediationHypothesis]:
    """Build BHV-08 HYPOTHESIZE diagnostician analyzing RED_TAG findings post-REVERT."""
    return Agent[JezzAgentDeps, RemediationHypothesis](
        model,
        output_type=RemediationHypothesis,
        deps_type=JezzAgentDeps,
        name="jezz_causal_diagnostician",
        retries=2,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "Diagnose the cited JBC RED_TAG findings and formulate a concrete structural "
            "remediation plan before re-entering construction."
        ),
    )


@runtime_checkable
class ConfiguredModelIdPort(Protocol):
    """Optional wrapper protocol exposing the configured ``provider:model`` identifier."""

    @property
    def configured_model_id(self) -> str:
        """Return the exact configured ``provider:model`` string."""
        ...


def _extract_agent_model_id[OutputT](agent: Agent[JezzAgentDeps, OutputT]) -> str:
    raw_model = agent.model
    if isinstance(raw_model, ConfiguredModelIdPort) and raw_model.configured_model_id:
        return raw_model.configured_model_id[:256]
    if isinstance(raw_model, Model):
        prefix = f"{raw_model.system}:"
        label = (
            raw_model.model_name
            if raw_model.model_name.startswith(prefix)
            else f"{raw_model.system}:{raw_model.model_name}"
        )
        return label[:256]
    if isinstance(raw_model, str) and raw_model:
        return raw_model[:256]
    return "unknown"


@jezz_callable
async def run_bounded_agent[OutputT](
    agent: GuardedAgent[OutputT],
    *,
    behavior_kind: BehaviorKind,
    prompt: BoundedPrompt,
    deps: Annotated[JezzAgentDeps, InstanceGuard(JezzAgentDeps)],
    budget: PceRuntimeBudget,
) -> tuple[Annotated[OutputT, InstanceGuard(object)], AgentInvocationTelemetry]:
    """Execute ``agent`` under wall-clock timeout and UsageLimits token/step breakers."""
    limits = UsageLimits(
        request_limit=budget.request_limit_for(behavior_kind),
        tool_calls_limit=budget.tool_calls_limit_per_step,
        total_tokens_limit=budget.total_tokens_limit_per_step,
    )
    async with asyncio.timeout(budget.agent_timeout_sec):
        result = await agent.run(prompt, deps=deps, usage_limits=limits)

    usage = result.usage
    binding = BEHAVIOR_EXECUTION_BOARD[behavior_kind]
    telemetry = AgentInvocationTelemetry(
        behavior_kind=behavior_kind,
        specialist_role=binding.specialist_role,
        model_id=_extract_agent_model_id(agent),
        requests=max(0, usage.requests),
        tool_calls=max(0, usage.tool_calls),
        input_tokens=max(0, usage.input_tokens),
        output_tokens=max(0, usage.output_tokens),
    )
    return result.output, telemetry
