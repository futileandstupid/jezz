"""Track 7: Cross-provider wire/schema compatibility & multi-provider matrix benchmark."""

import tempfile
import textwrap
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

from pydantic import BaseModel, Field, SecretStr
from pydantic_ai.exceptions import AgentRunError, ModelAPIError, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from jezz.agents.caching import (
    PromptCacheMode,
    partition_cached_user_prompt,
    resolve_cache_model_settings,
)
from jezz.agents.providers import infer_authenticated_model
from jezz.agents.routing import (
    CliModelOverrides,
    JezzModelsConfig,
    ModelInstanceFactory,
    ResolvedModelRouting,
    resolve_specialist_routing,
)
from jezz.agents.schemas import (
    ConstructionPatchBundle,
    ConsultationBrief,
    ContractSpecificationBundle,
    CuratedMemoryDigest,
    EnvironmentObservation,
    EpistemicObservation,
    FilePatchArtifact,
    FramedMandateProposal,
    InstrumentationSuiteBundle,
    ProvisionPlan,
    RefactorPatchBundle,
    RemediationHypothesis,
    ScopeDecompositionPlan,
)
from jezz.agents.specialists import (
    JBC_CANONICAL_EXEMPLAR,
    JBC_STATUTORY_BRIEF,
    GuardedAgent,
    GuardedModel,
    build_consult_agent,
    build_curate_memory_agent,
    build_decompose_agent,
    build_frame_agent,
    build_hypothesize_agent,
    build_inspect_agent,
    build_inspect_env_agent,
    build_instrument_agent,
    build_provision_agent,
    build_refactor_agent,
    build_specify_agent,
    build_synthesize_agent,
)
from jezz.agents.workspace import JezzAgentDeps, WorkspaceSandbox
from jezz.codes.inspector import BuildingInspector, ManifestDependencies
from jezz.codes.permits import CodePermit
from jezz.evals.evaluators import GuardedDataset
from jezz.evals.runner import (
    default_harness_deps,
    format_ab_benchmark_markdown,
    run_ab_benchmark,
)
from jezz.evals.schemas import (
    AbBenchmarkComparison,
    BenchmarkRunConfig,
    SddDesignArtifact,
    SddImplementationBundle,
    SddSpecArtifact,
    SddTasksArtifact,
)
from jezz.evals.sdd_baseline import (
    SDD_CONSTITUTION_MD,
    GuardedSddAgent,
    build_sdd_design_agent,
    build_sdd_implement_agent,
    build_sdd_specify_agent,
    build_sdd_tasks_agent,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    NonNegativeInt,
    PositiveInt,
    ShortText,
    jezz_callable,
)
from jezz.methodology.mandate import (
    DependencyRequirement,
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
)

DEFAULT_WIRE_PROBE_MODELS: Final[tuple[str, ...]] = (
    "google:gemini-3.8-flash",
    "google:gemini-4-hydrogen",
    "anthropic:claude-sonnet-4-6",
    "anthropic:claude-opus-4-8",
    "openai-chat:gpt-5.6-sol",
    "openai-responses:gpt-6-luna",
    "openrouter:anthropic/claude-sonnet-4.6",
)

_STATUTORY_AGENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "jezz_contract_specifier",
        "jezz_falsification_instrumenter",
        "jezz_code_synthesizer",
        "jezz_structural_refactorer",
    }
)

_PROBE_PYPROJECT_TOML: Final[str] = textwrap.dedent(
    """\
    [project]
    name = "wire-probe"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0", "pydantic>=2.14.4"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)

_PROBE_MOD_SOURCE: Final[str] = textwrap.dedent(
    """\
    import icontract
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

    class ProbeSpec(BaseModel):
        model_config = STRICT_FROZEN_CONFIG

        val: PositiveInt

    @icontract.ensure(lambda spec, result: result == spec.val)
    @jezz_callable
    def run_probe(spec: ProbeSpec) -> NonNegativeInt:
        return spec.val
    """
)

_PROBE_TEST_SOURCE: Final[str] = textwrap.dedent(
    """\
    from wire_probe.mod import ProbeSpec, run_probe

    def test_probe_positive() -> None:
        assert run_probe(ProbeSpec(val=3)) == 3
    """
)

type _ProbeRunner = Callable[
    [GuardedModel, JezzAgentDeps, PromptCacheMode],
    Coroutine[object, object, None],
]


class ProviderWireCheck(BaseModel):
    """Single offline ``(model_id, agent_name, cache_mode)`` wire-compatibility verdict."""

    model_config = STRICT_FROZEN_CONFIG

    model_id: IdentifierStr
    agent_name: IdentifierStr
    cache_mode: PromptCacheMode
    output_mode: IdentifierStr
    static_instruction_count: NonNegativeInt
    function_tool_count: NonNegativeInt
    cache_translated: bool
    compatible: bool
    failure_detail: BoundedText = ""


class CrossProviderCompatibilityReport(BaseModel):
    """Deterministic compatibility matrix across providers, agents, and cache modes."""

    model_config = STRICT_FROZEN_CONFIG

    models_checked: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    agents_per_model: PositiveInt
    total_wire_probes: PositiveInt
    all_compatible: bool
    checks: Annotated[
        tuple[ProviderWireCheck, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=2048),
    ]
    failures: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()


class ProviderSuiteSpec(BaseModel):
    """Named provider/tier routing and prompt-cache configuration for matrix benchmarks."""

    model_config = STRICT_FROZEN_CONFIG

    suite_label: IdentifierStr
    routing: ResolvedModelRouting


class ProviderMatrixComparison(BaseModel):
    """Multi-provider or cache-ablation A/B comparison across ``ProviderSuiteSpec`` entries."""

    model_config = STRICT_FROZEN_CONFIG

    dataset_name: IdentifierStr
    suite_labels: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    comparisons: Annotated[
        tuple[AbBenchmarkComparison, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]


@dataclass(frozen=True)
class _AgentProbeCase:
    """Internal descriptor pairing a typed probe runner with its valid canned output."""

    agent_name: str
    is_pce_agent: bool
    runner: _ProbeRunner
    canned_output: BaseModel


class _OfflineWireCaptureModel(WrapperModel):
    """Offline wrapper executing real ``Model.prepare_request`` without network I/O."""

    def __init__(self, target_model: Model, canned_output: BaseModel) -> None:
        super().__init__(target_model)
        self._canned_output = canned_output
        self.captured: list[tuple[ModelSettings | None, ModelRequestParameters]] = []

    @jezz_callable
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        _ = messages
        prepared_settings, prepared_params = self.prepare_request(
            model_settings,
            model_request_parameters,
        )
        self.captured.append((prepared_settings, prepared_params))
        raw_json = self._canned_output.model_dump_json()
        usage = RequestUsage(
            input_tokens=2048,
            output_tokens=160,
            cache_read_tokens=1536,
            cache_write_tokens=0,
        )
        if prepared_params.output_tools:
            tool_name = prepared_params.output_tools[0].name
            return ModelResponse(
                parts=[ToolCallPart(tool_name=tool_name, args=raw_json)],
                usage=usage,
            )
        return ModelResponse(parts=[TextPart(content=raw_json)], usage=usage)


def _make_pce_probe[OutT: BaseModel](
    agent_name: str,
    builder: Callable[[GuardedModel], GuardedAgent[OutT]],
    canned_output: OutT,
    *,
    partitioned: bool = False,
) -> _AgentProbeCase:
    async def _exec(
        model: GuardedModel,
        deps: JezzAgentDeps,
        cache_mode: PromptCacheMode,
    ) -> None:
        settings = resolve_cache_model_settings(cache_mode)
        prompt_payload = (
            partition_cached_user_prompt(
                "Shared Mandate Prefix",
                "\nActive Scope: probe",
                mode=cache_mode,
            )
            if partitioned
            else "Shared Mandate Prefix"
        )
        agent = builder(model)
        await agent.run(prompt_payload, deps=deps, model_settings=settings)

    return _AgentProbeCase(
        agent_name=agent_name,
        is_pce_agent=True,
        runner=_exec,
        canned_output=canned_output,
    )


def _make_sdd_probe[OutT: BaseModel](
    agent_name: str,
    builder: Callable[[GuardedModel], GuardedSddAgent[OutT]],
    canned_output: OutT,
) -> _AgentProbeCase:
    async def _exec(
        model: GuardedModel,
        deps: JezzAgentDeps,
        cache_mode: PromptCacheMode,
    ) -> None:
        _ = deps
        settings = resolve_cache_model_settings(cache_mode)
        agent = builder(model)
        await agent.run("Feature Request: probe", model_settings=settings)

    return _AgentProbeCase(
        agent_name=agent_name,
        is_pce_agent=False,
        runner=_exec,
        canned_output=canned_output,
    )


def _write_probe_file(root: Path, rel_path: str, content: str) -> None:
    target = ContainedPath.from_parts(root, rel_path)
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with target.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(content)


def _build_probe_mandate() -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id="MND-WIRE-001",
        title="Cross-Provider Wire Compatibility Probe",
        objective="Verify tool schemas, output schemas, and prompt-cache translation.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Preserves positive probe values identically.",
                verification_symbol="test_probe_positive",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="probe_scope",
                owned_files=frozenset({"src/wire_probe/mod.py", "tests/test_mod.py"}),
                contract_symbols=("ProbeSpec", "run_probe", "test_probe_positive"),
            ),
        ),
        required_dependencies=(
            DependencyRequirement(package="pydantic", version_constraint=">=2.14.4"),
        ),
    )


def _setup_probe_deps(root: Path, mandate: EngineeringMandate) -> JezzAgentDeps:
    _write_probe_file(root, "pyproject.toml", _PROBE_PYPROJECT_TOML)
    _write_probe_file(root, "src/wire_probe/__init__.py", '"""Wire probe package."""\n')
    _write_probe_file(root, "src/wire_probe/mod.py", _PROBE_MOD_SOURCE)
    _write_probe_file(root, "tests/__init__.py", '"""Wire probe tests."""\n')
    _write_probe_file(root, "tests/test_mod.py", _PROBE_TEST_SOURCE)

    base = BuildingInspector.from_workspace(root)
    manifest = ManifestDependencies(
        runtime_roots=base.manifest.runtime_roots,
        dev_roots=base.manifest.dev_roots,
        workspace_roots=frozenset({"wire_probe", "tests"}),
    )
    sandbox = WorkspaceSandbox(
        root,
        mandate.authorized_files(),
        authorized_manifests=mandate.authorized_manifest_files(),
    )
    return JezzAgentDeps(
        sandbox=sandbox,
        inspector=BuildingInspector(manifest=manifest),
        permit=CodePermit.full_book(permit_id="PERMIT-WIRE-PROBE"),
        mandate=mandate,
        prior_falsified_paths=("prior_negation",),
    )


def _build_pce_probe_cases(mandate: EngineeringMandate) -> tuple[_AgentProbeCase, ...]:
    mod_patch = FilePatchArtifact(
        file_path="src/wire_probe/mod.py",
        source_code=_PROBE_MOD_SOURCE,
        rationale="Wire probe module.",
    )
    test_patch = FilePatchArtifact(
        file_path="tests/test_mod.py",
        source_code=_PROBE_TEST_SOURCE,
        rationale="Wire probe test.",
    )
    return (
        _make_pce_probe(
            "jezz_mandate_framer",
            build_frame_agent,
            FramedMandateProposal(mandate=mandate, framing_notes="Framed."),
        ),
        _make_pce_probe(
            "jezz_scope_architect",
            build_decompose_agent,
            ScopeDecompositionPlan(
                write_scopes=mandate.write_scopes,
                decomposition_rationale="Cohesive single scope.",
            ),
        ),
        _make_pce_probe(
            "jezz_code_inspector",
            build_inspect_agent,
            EpistemicObservation(
                inspected_files=("src/wire_probe/mod.py",),
                discovered_symbols=("ProbeSpec", "run_probe"),
                summary="Inspected.",
            ),
        ),
        _make_pce_probe(
            "jezz_env_inspector",
            build_inspect_env_agent,
            EnvironmentObservation(
                python_version="3.14",
                declared_runtime_roots=("icontract", "jezz", "pydantic"),
                summary="Env sensed.",
            ),
        ),
        _make_pce_probe(
            "jezz_standards_consultant",
            build_consult_agent,
            ConsultationBrief(
                citations=("JBC-DBC-004: @jezz_callable",),
                normative_constraints=("Decorate public callables.",),
                summary="Consulted.",
            ),
        ),
        _make_pce_probe(
            "jezz_memory_curator",
            build_curate_memory_agent,
            CuratedMemoryDigest(
                compacted_observations=("Inspected mod.py.",),
                retained_invariants=("INV-001",),
                retained_falsified_paths=("prior_negation",),
                summary="Compacted.",
            ),
        ),
        _make_pce_probe(
            "jezz_dependency_provisioner",
            build_provision_agent,
            ProvisionPlan(approved_specs=("pydantic>=2.14.4",), rationale="Declared."),
        ),
        _make_pce_probe(
            "jezz_contract_specifier",
            build_specify_agent,
            ContractSpecificationBundle(
                contract_patches=(mod_patch,),
                declared_symbols=("ProbeSpec", "run_probe"),
                summary="Specified.",
            ),
            partitioned=True,
        ),
        _make_pce_probe(
            "jezz_falsification_instrumenter",
            build_instrument_agent,
            InstrumentationSuiteBundle(
                test_patches=(test_patch,),
                covered_invariant_ids=("INV-001",),
                summary="Instrumented.",
            ),
            partitioned=True,
        ),
        _make_pce_probe(
            "jezz_code_synthesizer",
            build_synthesize_agent,
            ConstructionPatchBundle(
                implementation_patches=(mod_patch,),
                summary="Synthesized.",
            ),
            partitioned=True,
        ),
        _make_pce_probe(
            "jezz_structural_refactorer",
            build_refactor_agent,
            RefactorPatchBundle(
                refactored_patches=(mod_patch,),
                preserved_symbols=("ProbeSpec", "run_probe"),
                summary="Refactored.",
            ),
            partitioned=True,
        ),
        _make_pce_probe(
            "jezz_causal_diagnostician",
            build_hypothesize_agent,
            RemediationHypothesis(
                root_causes=("Missing breaker.",),
                cited_code_ids=("JBC-DBC-004",),
                remediation_plan="Attach @jezz_callable.",
            ),
        ),
    )


def _build_sdd_probe_cases() -> tuple[_AgentProbeCase, ...]:
    mod_patch = FilePatchArtifact(
        file_path="src/wire_probe/mod.py",
        source_code=_PROBE_MOD_SOURCE,
        rationale="SDD impl.",
    )
    return (
        _make_sdd_probe(
            "sdd_specify_stage",
            build_sdd_specify_agent,
            SddSpecArtifact(
                spec_markdown=(
                    "# Spec\nWHEN called THE SYSTEM SHALL return val.\n"
                    "#### Scenario: Positive\n- GIVEN 3\n- WHEN run\n- THEN 3\n"
                )
            ),
        ),
        _make_sdd_probe(
            "sdd_design_stage",
            build_sdd_design_agent,
            SddDesignArtifact(design_markdown="# Design\nModule mod.py.\n"),
        ),
        _make_sdd_probe(
            "sdd_tasks_stage",
            build_sdd_tasks_agent,
            SddTasksArtifact(tasks_markdown="- [ ] 1.1 Implement mod.py\n"),
        ),
        _make_sdd_probe(
            "sdd_implement_stage",
            build_sdd_implement_agent,
            SddImplementationBundle(
                patches=(mod_patch,),
                completed_tasks_markdown="- [x] 1.1 Implement mod.py\n",
            ),
        ),
    )


def _check_instruction_invariants(
    agent_name: str,
    is_pce_agent: bool,
    params: ModelRequestParameters,
) -> str | None:
    parts = params.instruction_parts or []
    if not parts:
        return f"Agent '{agent_name}' emitted zero instruction_parts."
    if any(p.dynamic for p in parts):
        return f"Agent '{agent_name}' contains dynamic prefix-busting instruction_parts."
    if agent_name in _STATUTORY_AGENT_NAMES:
        if len(parts) < 3 or parts[0].content != JBC_STATUTORY_BRIEF.strip():
            return f"Statutory agent '{agent_name}' lacks leading JBC_STATUTORY_BRIEF."
        if parts[1].content != JBC_CANONICAL_EXEMPLAR.strip():
            return f"Statutory agent '{agent_name}' lacks second JBC_CANONICAL_EXEMPLAR."
    if not is_pce_agent and parts[0].content != SDD_CONSTITUTION_MD.strip():
        return f"SDD baseline agent '{agent_name}' lacks leading SDD_CONSTITUTION_MD."
    return None


def _was_cache_translated(
    model_id: str,
    cache_mode: PromptCacheMode,
    settings: ModelSettings | None,
    params: ModelRequestParameters,
) -> bool:
    active_settings = settings or {}
    if cache_mode == PromptCacheMode.OFF:
        return (
            params.cache in (False, None)
            and "anthropic_cache_instructions" not in active_settings
            and "openai_cache_instructions" not in active_settings
        )
    if model_id.startswith("anthropic:"):
        return (
            "anthropic_cache_instructions" in active_settings
            and "anthropic_cache_tool_definitions" in active_settings
        )
    if model_id.startswith(("openai:", "openai-chat:", "openai-responses:")):
        return (
            "openai_cache_instructions" in active_settings
            and "openai_prompt_cache_options" in active_settings
        )
    if model_id.startswith("openrouter:"):
        return params.cache is not None
    return True


async def _execute_single_wire_probe(
    model_id: str,
    base_model: Model,
    case: _AgentProbeCase,
    deps: JezzAgentDeps,
    cache_mode: PromptCacheMode,
) -> ProviderWireCheck:
    capture = _OfflineWireCaptureModel(base_model, case.canned_output)
    try:
        await case.runner(capture, deps, cache_mode)
    except (AgentRunError, ModelAPIError, UserError, ValueError, TypeError, RuntimeError) as exc:
        return ProviderWireCheck(
            model_id=model_id,
            agent_name=case.agent_name,
            cache_mode=cache_mode,
            output_mode="error",
            static_instruction_count=0,
            function_tool_count=0,
            cache_translated=False,
            compatible=False,
            failure_detail=f"{type(exc).__name__}: {str(exc)[:300]}",
        )

    prep_settings, prep_params = capture.captured[-1]
    instr_err = _check_instruction_invariants(case.agent_name, case.is_pce_agent, prep_params)
    translated = _was_cache_translated(model_id, cache_mode, prep_settings, prep_params)
    err_detail = instr_err or ("" if translated else "Cache settings failed provider translation.")
    return ProviderWireCheck(
        model_id=model_id,
        agent_name=case.agent_name,
        cache_mode=cache_mode,
        output_mode=str(prep_params.output_mode),
        static_instruction_count=len(prep_params.instruction_parts or ()),
        function_tool_count=len(prep_params.function_tools),
        cache_translated=translated,
        compatible=(err_detail == ""),
        failure_detail=err_detail,
    )


@jezz_callable
async def verify_cross_provider_agent_compatibility(
    model_ids: Annotated[Sequence[IdentifierStr], Field(min_length=1, max_length=32)] = (
        DEFAULT_WIRE_PROBE_MODELS
    ),
    *,
    cache_modes: Annotated[Sequence[PromptCacheMode], Field(min_length=1, max_length=8)] = (
        PromptCacheMode.AUTO,
        PromptCacheMode.PREFIX_ONLY,
        PromptCacheMode.TTL_1H,
        PromptCacheMode.OFF,
    ),
) -> CrossProviderCompatibilityReport:
    """Verify all 12 PCE specialists and 4 SDD stages across provider adapters and cache modes."""
    mandate = _build_probe_mandate()
    cases = (*_build_pce_probe_cases(mandate), *_build_sdd_probe_cases())
    checks: list[ProviderWireCheck] = []
    failures: list[str] = []
    offline_token = SecretStr("offline-wire-probe")

    with tempfile.TemporaryDirectory() as raw_tmp:
        deps = _setup_probe_deps(Path(raw_tmp), mandate)
        for model_id in model_ids:
            base_model = infer_authenticated_model(model_id, api_key=offline_token)
            for cache_mode in cache_modes:
                for case in cases:
                    verdict = await _execute_single_wire_probe(
                        model_id, base_model, case, deps, cache_mode
                    )
                    checks.append(verdict)
                    if not verdict.compatible:
                        failures.append(
                            f"{model_id}/{case.agent_name}@{cache_mode}: {verdict.failure_detail}"[
                                :1024
                            ]
                        )

    return CrossProviderCompatibilityReport(
        models_checked=tuple(model_ids),
        agents_per_model=len(cases),
        total_wire_probes=len(checks),
        all_compatible=(len(failures) == 0),
        checks=tuple(checks),
        failures=tuple(failures[:256]),
    )


def _split_cache_suffix(
    rhs: str,
    default_cache: PromptCacheMode,
) -> tuple[str, PromptCacheMode]:
    if "@" not in rhs:
        return rhs, default_cache
    model_part, raw_cache = rhs.rsplit("@", maxsplit=1)
    return model_part.strip(), PromptCacheMode(raw_cache.strip())


def _parse_tiered_assignments(model_part: str) -> JezzModelsConfig:
    kv: dict[str, str] = {}
    for entry in model_part.split(","):
        piece = entry.strip()
        if ":" not in piece:
            msg = f"Invalid tiered suite segment '{piece}'; expected 'arch:<model>,build:<model>'."
            raise ValueError(msg)
        role_key, model_spec = piece.split(":", maxsplit=1)
        kv[role_key.strip()] = model_spec.strip()
    allowed = {"default", "arch", "architect", "build", "builder", "inspect", "inspector"}
    unknown = sorted(set(kv) - allowed)
    if unknown:
        msg = f"Unknown tier key(s) {unknown} in --suite; allowed: arch, build, inspect, default."
        raise ValueError(msg)
    arch = kv.get("arch") or kv.get("architect")
    build = kv.get("build") or kv.get("builder")
    default = kv.get("default") or build or arch
    inspect = kv.get("inspect") or kv.get("inspector") or build or default
    return JezzModelsConfig(
        default=default,
        architect=arch or default,
        builder=build or default,
        inspector=inspect,
    )


@jezz_callable
def parse_provider_suite_spec(
    raw_spec: ShortText,
    *,
    default_cache: PromptCacheMode = PromptCacheMode.AUTO,
) -> ProviderSuiteSpec:
    """Parse ``<label>=<provider:model>[@<cache>]`` or ``<label>=arch:<m>,build:<m>[@<cache>]``."""
    if "=" not in raw_spec:
        msg = (
            f"Invalid --suite specification '{raw_spec}'; expected "
            "'<label>=<provider:model>[@<cache>]' or '<label>=arch:<m>,build:<m>[@<cache>]'."
        )
        raise ValueError(msg)
    raw_label, raw_rhs = raw_spec.split("=", maxsplit=1)
    label = raw_label.strip()
    if not label or "/" in label or ".." in label:
        msg = f"Invalid suite label '{label}'; must be a safe workspace directory slug."
        raise ValueError(msg)
    model_part, cache_mode = _split_cache_suffix(raw_rhs.strip(), default_cache)
    tiered_prefixes = (
        "arch:",
        "architect:",
        "build:",
        "builder:",
        "inspect:",
        "inspector:",
        "default:",
    )
    if "," in model_part or model_part.startswith(tiered_prefixes):
        cfg = _parse_tiered_assignments(model_part)
        routing = resolve_specialist_routing(
            CliModelOverrides(prompt_cache=cache_mode),
            project_config=cfg,
        )
    else:
        routing = resolve_specialist_routing(
            CliModelOverrides(model=model_part, prompt_cache=cache_mode)
        )
    return ProviderSuiteSpec(suite_label=label, routing=routing)


async def _execute_single_matrix_suite(
    suite: ProviderSuiteSpec,
    resolved_root: Path,
    cfg_template: BenchmarkRunConfig,
    model_resolver: ModelInstanceFactory,
    dataset: GuardedDataset | None,
    jezz_root: Path | None,
) -> AbBenchmarkComparison:
    suite_dir = ContainedPath.from_parts(resolved_root, suite.suite_label)
    suite_dir.resolved_path.mkdir(parents=True, exist_ok=True)
    suite_cfg = cfg_template.model_copy(update={"prompt_cache_mode": suite.routing.prompt_cache})
    ctrl_name = suite.routing.synthesize
    deps = default_harness_deps(
        suite_dir.resolved_path,
        jezz_root=jezz_root,
        config=suite_cfg,
        model_factory=lambda: model_resolver(ctrl_name),
        suite_factory=lambda: suite.routing.build_suite(model_resolver),
    )
    summary = suite.routing.summary_label()
    label_str = f"{suite.suite_label} ({summary}@{suite.routing.prompt_cache})"
    return await run_ab_benchmark(
        model_resolver(ctrl_name),
        deps,
        model_label=label_str[:256],
        dataset=dataset,
    )


@jezz_callable
async def run_provider_matrix_benchmark(
    suites: Annotated[Sequence[ProviderSuiteSpec], Field(min_length=1, max_length=32)],
    work_root: Path,
    model_resolver: ModelInstanceFactory,
    *,
    base_config: BenchmarkRunConfig | None = None,
    dataset: GuardedDataset | None = None,
    jezz_root: Path | None = None,
) -> ProviderMatrixComparison:
    """Execute matched SDD-vs-PCE A/B benchmarks across multiple providers or cache modes."""
    cfg_template = base_config or BenchmarkRunConfig()
    resolved_root = work_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    comparisons: list[AbBenchmarkComparison] = []
    seen_labels: set[str] = set()

    for suite in suites:
        if suite.suite_label in seen_labels:
            msg = f"Duplicate ProviderSuiteSpec label '{suite.suite_label}'."
            raise ValueError(msg)
        seen_labels.add(suite.suite_label)
        comp = await _execute_single_matrix_suite(
            suite,
            resolved_root,
            cfg_template,
            model_resolver,
            dataset,
            jezz_root,
        )
        comparisons.append(comp)

    return ProviderMatrixComparison(
        dataset_name=comparisons[0].dataset_name,
        suite_labels=tuple(s.suite_label for s in suites),
        comparisons=tuple(comparisons),
    )


def _format_matrix_summary_row(label: str, comp: AbBenchmarkComparison) -> str:
    c = comp.control_summary
    t = comp.treatment_summary
    occ_cell = (
        f"`{c.cases_jbc_occupiable}/{c.cases_total}` vs `{t.cases_jbc_occupiable}/{t.cases_total}`"
    )
    orc_cell = (
        f"`{c.cases_oracle_passed}/{c.cases_total}` vs `{t.cases_oracle_passed}/{t.cases_total}`"
    )
    return (
        f"| `{label}` | `{comp.prompt_cache_mode}` | {occ_cell} "
        f"| `{c.total_life_safety_violations}` vs `{t.total_life_safety_violations}` "
        f"| {orc_cell} "
        f"| `{t.total_cache_read_tokens:,}` (`{t.cache_hit_ratio:.1%}`) "
        f"| `{t.total_input_tokens:,}` -> `{t.effective_billed_input_tokens:,.0f}` "
        f"| `{t.mean_wall_clock_sec}s` |"
    )


@jezz_callable
def format_provider_matrix_markdown(matrix: ProviderMatrixComparison) -> BoundedText:
    """Render an executive cross-provider & prompt-cache A/B benchmark matrix report."""
    lines = [
        f"# Track 7 Cross-Provider & Prompt-Cache Matrix — `{matrix.dataset_name}`",
        "",
        "## Cross-Suite Summary (`SDD Control` vs. `Jezz PCE Treatment`)",
        "",
        (
            "| Suite | Cache | JBC Occupiable (`SDD` vs `PCE`) | `LIFE_SAFETY` (`SDD` vs `PCE`) "
            "| Oracle (`SDD` vs `PCE`) | `PCE` Cache Read (`Hit%`) "
            "| `PCE` Raw -> Effective In | `PCE` Mean Latency |"
        ),
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for label, comp in zip(matrix.suite_labels, matrix.comparisons, strict=True):
        lines.append(_format_matrix_summary_row(label, comp))
    for comp in matrix.comparisons:
        lines.extend(["", "---", "", format_ab_benchmark_markdown(comp)])
    return "\n".join(lines)[:65_536]
