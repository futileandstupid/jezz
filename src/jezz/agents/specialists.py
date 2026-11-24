"""Pillar 4: 21-behavior execution board and bounded Pydantic AI specialist agents."""

import asyncio
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Annotated, Protocol, runtime_checkable

from pydantic import Field
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.messages import InstructionPart
from pydantic_ai.models import Model

from jezz.agents.caching import (
    CachedUserPrompt,
    normalize_prompt_for_model,
    resolve_cache_model_settings,
)
from jezz.agents.memory import validate_curated_digest_invariants
from jezz.agents.polyglot_prompts import (
    is_polyglot_test_path,
    polyglot_construction_role_instructions,
    polyglot_decompose_instructions,
    polyglot_frame_instructions,
    select_polyglot_brief_and_exemplar,
)
from jezz.agents.schemas import (
    AgentInvocationTelemetry,
    ConstructionPatchBundle,
    ConsultationBrief,
    ContractSpecificationBundle,
    CuratedMemoryDigest,
    EnvironmentObservation,
    EpistemicObservation,
    FilePatchArtifact,
    FramedMandateProposal,
    InstrumentationSuiteBundle,
    PceRuntimeBudget,
    ProvisionPlan,
    RefactorPatchBundle,
    RemediationHypothesis,
    ScopeDecompositionPlan,
)
from jezz.agents.specialist_board import (
    BEHAVIOR_EXECUTION_BOARD as BEHAVIOR_EXECUTION_BOARD,
)
from jezz.agents.specialist_board import (
    DETERMINISTIC_BEHAVIOR_KINDS as DETERMINISTIC_BEHAVIOR_KINDS,
)
from jezz.agents.specialist_board import (
    JBC_CANONICAL_EXEMPLAR as JBC_CANONICAL_EXEMPLAR,
)
from jezz.agents.specialist_board import (
    JBC_STATUTORY_BRIEF as JBC_STATUTORY_BRIEF,
)
from jezz.agents.specialist_board import (
    PY_DECOMPOSE_INSTRUCTIONS,
    PY_FRAME_INSTRUCTIONS,
    PY_INSTRUMENT_ROLE,
    PY_REFACTOR_ROLE,
    PY_SPECIFY_ROLE,
    PY_SYNTHESIZE_ROLE,
)
from jezz.agents.specialist_board import (
    validate_execution_board_invariants as validate_execution_board_invariants,
)
from jezz.agents.workspace import (
    JezzAgentDeps,
    build_readonly_toolset,
    build_scoped_write_toolset,
    find_type_alias_misuse,
    verify_runtime_importable_patches,
)
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.adapters import parse_any_source_unit
from jezz.codes.polyglot_ir import InspectedUnit, PolyglotUnit, SupportedLanguage
from jezz.codes.ratchet import DifferentialScope, inspect_differential_rough_in
from jezz.guards import WIRE_FROZENSET_VALIDATOR, InstanceGuard, jezz_callable
from jezz.methodology.dossier import extract_compiled_symbols
from jezz.methodology.mandate import DependencyScope, compute_scope_execution_waves


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
    inspect_env_model: Model | None = None
    consult_model: Model | None = None
    curate_memory_model: Model | None = None
    provision_model: Model | None = None


type GuardedModel = Annotated[Model, InstanceGuard(Model)]
type GuardedAgent[OutT] = Annotated[Agent[JezzAgentDeps, OutT], InstanceGuard(Agent)]
type BoundedPrompt = Annotated[str, Field(min_length=1, max_length=65_536)]
type LanguageFilter = Annotated[AbstractSet[SupportedLanguage], WIRE_FROZENSET_VALIDATOR] | None


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
        inspect_env_model=model,
        consult_model=model,
        curate_memory_model=model,
        provision_model=model,
    )


def _enforce_patch_scope_and_parse(
    patches: Sequence[FilePatchArtifact],
    allowed_files: frozenset[str],
) -> tuple[InspectedUnit, ...]:
    units: list[InspectedUnit] = []
    for patch in patches:
        if patch.file_path not in allowed_files:
            msg = (
                f"Patch target '{patch.file_path}' is outside authorized write scope "
                f"{sorted(allowed_files)}."
            )
            raise ModelRetry(msg)
        try:
            units.append(parse_any_source_unit(patch.file_path, patch.source_code))
        except (SyntaxError, ValueError) as exc:
            raise ModelRetry(f"Syntax error in '{patch.file_path}': {exc}") from exc
    return tuple(units)


def _parse_and_rough_in_check(
    deps: JezzAgentDeps,
    patches: Sequence[FilePatchArtifact],
    stage_label: str,
) -> tuple[InspectedUnit, ...]:
    units = _enforce_patch_scope_and_parse(patches, deps.sandbox.authorized_files)
    patched_paths = frozenset(u.file_path for u in units)
    ws_units = deps.sandbox.load_units()
    others = [u for u in ws_units if u.file_path not in patched_paths]
    diff_report = inspect_differential_rough_in(
        deps.inspector,
        (*others, *units),
        DifferentialScope(changed_paths=patched_paths),
        permit=deps.permit,
    )
    cited = [
        f"{f.code_id} ({f.location.file_path}:L{f.location.line}): "
        f"{f.message} -> Fix: {f.remediation}"
        for f in diff_report.report.unwaived_violations()
    ]
    if cited:
        joined = " | ".join(cited[:6])
        raise ModelRetry(f"{stage_label} failed JBC rough-in: {joined}")
    alias_err = find_type_alias_misuse(units, ws_units)
    if alias_err is not None:
        raise ModelRetry(f"{stage_label} failed PEP 695 type-alias check: {alias_err}")
    return units


def _validate_specify_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: ContractSpecificationBundle,
) -> ContractSpecificationBundle:
    for patch in bundle.contract_patches:
        if is_polyglot_test_path(patch.file_path):
            raise ModelRetry(
                f"Contract patch '{patch.file_path}' targets a test file; Phase III SPECIFY "
                "must target non-test implementation modules only."
            )
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


def _is_test_unit(unit: InspectedUnit) -> bool:
    if unit.is_test_file or is_polyglot_test_path(unit.file_path):
        return True
    return isinstance(unit, PolyglotUnit) and any(fn.is_test_callable for fn in unit.callables)


def _validate_instrument_output(
    ctx: RunContext[JezzAgentDeps],
    bundle: InstrumentationSuiteBundle,
) -> InstrumentationSuiteBundle:
    for patch in bundle.test_patches:
        if not is_polyglot_test_path(patch.file_path):
            raise ModelRetry(
                f"Instrumentation file '{patch.file_path}' must be an authorized test file "
                "(tests/*, *.test.ts, *.spec.ts, *_test.rs, or *_test.go)."
            )
    test_units = _parse_and_rough_in_check(
        ctx.deps,
        bundle.test_patches,
        "Phase III falsification suite",
    )
    expected = (
        ctx.deps.scoped_invariants
        if ctx.deps.scoped_invariants is not None
        else (ctx.deps.mandate.invariants if ctx.deps.mandate is not None else ())
    )
    if expected:
        patched_paths = {u.file_path for u in test_units}
        prior_tests = [
            u
            for u in ctx.deps.sandbox.load_units()
            if _is_test_unit(u) and u.file_path not in patched_paths
        ]
        compiled_tests = extract_compiled_symbols((*prior_tests, *test_units))
        missing_verif = [
            inv.verification_symbol
            for inv in expected
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
    patch_units: Sequence[InspectedUnit],
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
    allowed = frozenset(p for p in raw_allowed if not is_polyglot_test_path(p))
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
    allowed = frozenset(p for p in raw_allowed if not is_polyglot_test_path(p))
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


def _validate_curate_memory_output(
    ctx: RunContext[JezzAgentDeps],
    digest: CuratedMemoryDigest,
) -> CuratedMemoryDigest:
    if ctx.deps.mandate is None:
        return digest
    err = validate_curated_digest_invariants(
        ctx.deps.mandate,
        ctx.deps.prior_falsified_paths,
        digest,
    )
    if err is not None:
        raise ModelRetry(err)
    return digest


def _validate_provision_output(
    ctx: RunContext[JezzAgentDeps],
    plan: ProvisionPlan,
) -> ProvisionPlan:
    if ctx.deps.mandate is None:
        return plan
    expected_run = {
        r.pep508_spec()
        for r in ctx.deps.mandate.required_dependencies
        if r.scope == DependencyScope.RUNTIME
    }
    expected_dev = {
        r.pep508_spec()
        for r in ctx.deps.mandate.required_dependencies
        if r.scope == DependencyScope.DEV
    }
    if set(plan.approved_specs) != expected_run or set(plan.dev_specs) != expected_dev:
        raise ModelRetry(
            f"ProvisionPlan must match declared required_dependencies exactly: "
            f"approved_specs={sorted(expected_run)}, dev_specs={sorted(expected_dev)}."
        )
    return plan


def _validate_frame_output(
    ctx: RunContext[JezzAgentDeps],
    proposal: FramedMandateProposal,
) -> FramedMandateProposal:
    _ = ctx
    for scope in proposal.mandate.write_scopes:
        has_src = any(not is_polyglot_test_path(rel) for rel in scope.owned_files)
        if not has_src:
            raise ModelRetry(
                f"ModuleWriteScope '{scope.scope_id}' owns only test files "
                f"{sorted(scope.owned_files)}. Include each test file inside the same "
                "ModuleWriteScope as the non-test implementation module it verifies."
            )
    return proposal


@jezz_callable
def build_frame_agent(
    model: GuardedModel,
    *,
    languages: LanguageFilter = None,
) -> GuardedAgent[FramedMandateProposal]:
    """Build BHV-01 FRAME specialist producing typed anti-Agile EngineeringMandates."""
    agent = Agent[JezzAgentDeps, FramedMandateProposal](
        model,
        output_type=FramedMandateProposal,
        deps_type=JezzAgentDeps,
        name="jezz_mandate_framer",
        retries=3,
        toolsets=(build_readonly_toolset(),),
        instructions=polyglot_frame_instructions(languages, PY_FRAME_INSTRUCTIONS),
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
        has_src = any(not is_polyglot_test_path(rel) for rel in scope.owned_files)
        if not has_src:
            raise ModelRetry(
                f"ModuleWriteScope '{scope.scope_id}' owns only test files "
                f"{sorted(scope.owned_files)}; include at least one non-test module."
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
def build_decompose_agent(
    model: GuardedModel,
    *,
    languages: LanguageFilter = None,
) -> GuardedAgent[ScopeDecompositionPlan]:
    """Build BHV-09 DECOMPOSE specialist refining scopes and DAG waves over the AST/CST graph."""
    agent = Agent[JezzAgentDeps, ScopeDecompositionPlan](
        model,
        output_type=ScopeDecompositionPlan,
        deps_type=JezzAgentDeps,
        name="jezz_scope_architect",
        retries=3,
        toolsets=(build_readonly_toolset(),),
        instructions=polyglot_decompose_instructions(languages, PY_DECOMPOSE_INSTRUCTIONS),
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
def build_inspect_env_agent(model: GuardedModel) -> GuardedAgent[EnvironmentObservation]:
    """Build BHV-05 INSPECT_ENV specialist sensing runtime toolchain and manifest state."""
    return Agent[JezzAgentDeps, EnvironmentObservation](
        model,
        output_type=EnvironmentObservation,
        deps_type=JezzAgentDeps,
        name="jezz_env_inspector",
        retries=2,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "Summarize the workspace toolchain, declared runtime/dev dependency roots, "
            "unprovisioned required_dependencies, and prior sealed mandates provided in the "
            "substrate snapshot. Call _tool_inspect_package_api only if verifying an installed "
            "Python package signature."
        ),
    )


@jezz_callable
def build_consult_agent(model: GuardedModel) -> GuardedAgent[ConsultationBrief]:
    """Build BHV-06 CONSULT specialist anchoring normative standards and API citations."""
    return Agent[JezzAgentDeps, ConsultationBrief](
        model,
        output_type=ConsultationBrief,
        deps_type=JezzAgentDeps,
        name="jezz_standards_consultant",
        retries=2,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "Synthesize normative citations and concrete implementation constraints from the "
            "resolved JBC provisions, installed package APIs, and workspace reference documents "
            "in the prompt. Use _tool_consult_building_code or _tool_inspect_package_api only "
            "for unresolved targets."
        ),
    )


@jezz_callable
def build_curate_memory_agent(model: GuardedModel) -> GuardedAgent[CuratedMemoryDigest]:
    """Build BHV-07 CURATE_MEMORY specialist compacting observations without losing negations."""
    agent = Agent[JezzAgentDeps, CuratedMemoryDigest](
        model,
        output_type=CuratedMemoryDigest,
        deps_type=JezzAgentDeps,
        name="jezz_memory_curator",
        retries=3,
        toolsets=(build_readonly_toolset(),),
        instructions=(
            "Compact working memory observations into concise architectural facts while "
            "preserving 100% of active mandate invariant_ids in retained_invariants and 100% of "
            "prior falsified_paths in retained_falsified_paths verbatim."
        ),
    )
    agent.output_validator(_validate_curate_memory_output)
    return agent


@jezz_callable
def build_provision_agent(model: GuardedModel) -> GuardedAgent[ProvisionPlan]:
    """Build BHV-13 PROVISION specialist validating declared manifest dependency specs."""
    agent = Agent[JezzAgentDeps, ProvisionPlan](
        model,
        output_type=ProvisionPlan,
        deps_type=JezzAgentDeps,
        name="jezz_dependency_provisioner",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=(
            "Validate and emit the exact dependency specifiers declared in the mandate's "
            "required_dependencies (partitioned into approved_specs for runtime and dev_specs for "
            "dev). Never add undeclared packages or omit declared requirements."
        ),
    )
    agent.output_validator(_validate_provision_output)
    return agent


def _build_construction_instructions(
    role_key: str,
    python_role: str,
    languages: LanguageFilter,
) -> tuple[InstructionPart, InstructionPart, InstructionPart]:
    brief_txt, exemplar_txt = select_polyglot_brief_and_exemplar(
        languages,
        JBC_STATUTORY_BRIEF,
        JBC_CANONICAL_EXEMPLAR,
    )
    role_txt = polyglot_construction_role_instructions(role_key, languages, python_role)
    return (
        InstructionPart(content=brief_txt, name="jbc_statutory_brief"),
        InstructionPart(content=exemplar_txt, name="jbc_canonical_exemplar"),
        InstructionPart(content=role_txt, name="specialist_role"),
    )


@jezz_callable
def build_specify_agent(
    model: GuardedModel,
    *,
    languages: LanguageFilter = None,
) -> GuardedAgent[ContractSpecificationBundle]:
    """Build BHV-14 SPECIFY specialist with @output_validator JBC rough-in breaker."""
    agent = Agent[JezzAgentDeps, ContractSpecificationBundle](
        model,
        output_type=ContractSpecificationBundle,
        deps_type=JezzAgentDeps,
        name="jezz_contract_specifier",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=_build_construction_instructions("specify", PY_SPECIFY_ROLE, languages),
    )
    agent.output_validator(_validate_specify_output)
    return agent


@jezz_callable
def build_instrument_agent(
    model: GuardedModel,
    *,
    languages: LanguageFilter = None,
) -> GuardedAgent[InstrumentationSuiteBundle]:
    """Build BHV-15 INSTRUMENT specialist enforcing non-tautological falsification suites."""
    agent = Agent[JezzAgentDeps, InstrumentationSuiteBundle](
        model,
        output_type=InstrumentationSuiteBundle,
        deps_type=JezzAgentDeps,
        name="jezz_falsification_instrumenter",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=_build_construction_instructions("instrument", PY_INSTRUMENT_ROLE, languages),
    )
    agent.output_validator(_validate_instrument_output)
    return agent


@jezz_callable
def build_synthesize_agent(
    model: GuardedModel,
    *,
    languages: LanguageFilter = None,
) -> GuardedAgent[ConstructionPatchBundle]:
    """Build BHV-16 SYNTHESIZE specialist constrained to authorized ModuleWriteScopes."""
    agent = Agent[JezzAgentDeps, ConstructionPatchBundle](
        model,
        output_type=ConstructionPatchBundle,
        deps_type=JezzAgentDeps,
        name="jezz_code_synthesizer",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=_build_construction_instructions("synthesize", PY_SYNTHESIZE_ROLE, languages),
    )
    agent.output_validator(_validate_synthesize_output)
    return agent


@jezz_callable
def build_refactor_agent(
    model: GuardedModel,
    *,
    languages: LanguageFilter = None,
) -> GuardedAgent[RefactorPatchBundle]:
    """Build BHV-17 REFACTOR specialist renovating brownfield modules to JBC v1.2 occupancy."""
    agent = Agent[JezzAgentDeps, RefactorPatchBundle](
        model,
        output_type=RefactorPatchBundle,
        deps_type=JezzAgentDeps,
        name="jezz_structural_refactorer",
        retries=3,
        toolsets=(build_scoped_write_toolset(),),
        instructions=_build_construction_instructions("refactor", PY_REFACTOR_ROLE, languages),
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
    prompt: CachedUserPrompt,
    deps: Annotated[JezzAgentDeps, InstanceGuard(JezzAgentDeps)],
    budget: PceRuntimeBudget,
) -> tuple[Annotated[OutputT, InstanceGuard(object)], AgentInvocationTelemetry]:
    """Execute ``agent`` under wall-clock timeout, prompt caching, and UsageLimits breakers."""
    limits = UsageLimits(
        request_limit=budget.request_limit_for(behavior_kind),
        tool_calls_limit=budget.tool_calls_limit_per_step,
        total_tokens_limit=budget.total_tokens_limit_per_step,
    )
    settings = resolve_cache_model_settings(budget.prompt_cache_mode)
    raw_model = agent.model
    bound_model = raw_model if isinstance(raw_model, Model) else None
    wire_prompt = normalize_prompt_for_model(prompt, bound_model)
    async with asyncio.timeout(budget.agent_timeout_sec):
        result = await agent.run(
            wire_prompt,
            deps=deps,
            usage_limits=limits,
            model_settings=settings,
        )

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
        cache_read_tokens=max(0, usage.cache_read_tokens),
        cache_write_tokens=max(0, usage.cache_write_tokens),
    )
    return result.output, telemetry
