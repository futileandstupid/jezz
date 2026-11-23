"""Track 7 verification suite: prompt caching, billed-token laws, and cross-provider matrix."""

import io
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, SecretStr
from pydantic_ai.messages import (
    CachePoint,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

import jezz.cli as cli_mod
from jezz.agents import (
    JBC_CANONICAL_EXEMPLAR,
    JBC_STATUTORY_BRIEF,
    AgentInvocationTelemetry,
    CliModelOverrides,
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    FilePatchArtifact,
    FramedMandateProposal,
    InProcessGateRunner,
    InstrumentationSuiteBundle,
    JezzModelsConfig,
    PceRunStatus,
    PceRuntimeBudget,
    PceWorkflowOrchestrator,
    PromptCacheMode,
    ScopeDecompositionPlan,
    SpecialistRoleSlot,
    TlsResilientModel,
    build_instrument_agent,
    build_refactor_agent,
    build_specify_agent,
    build_synthesize_agent,
    compute_cache_hit_ratio,
    compute_effective_billed_input_tokens,
    compute_input_cost_savings_ratio,
    infer_authenticated_model,
    load_pyproject_models_config,
    model_supports_inline_cache_points,
    normalize_prompt_for_model,
    partition_cached_user_prompt,
    resolve_cache_model_settings,
    resolve_specialist_routing,
    uniform_model_suite,
)
from jezz.behaviors import BehaviorKind
from jezz.cli import format_workflow_markdown, main
from jezz.codes import BuildingInspector, ManifestDependencies
from jezz.evals import (
    DEFAULT_WIRE_PROBE_MODELS,
    ProviderMatrixComparison,
    SddDesignArtifact,
    SddImplementationBundle,
    SddSpecArtifact,
    SddTasksArtifact,
    format_provider_matrix_markdown,
    parse_provider_suite_spec,
    verify_cross_provider_agent_compatibility,
)
from jezz.guards import ContainedPath
from jezz.methodology import DomainInvariant, EngineeringMandate, ModuleWriteScope

_BASE_PYPROJECT = textwrap.dedent(
    """\
    [project]
    name = "cache-probe"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0", "pydantic>=2.14.4"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)

_PAR_ALPHA_SRC = textwrap.dedent(
    """\
    import icontract
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

    class AlphaItem(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        units: PositiveInt

    @icontract.ensure(lambda item, result: result == item.units * 2)
    @jezz_callable
    def scale_alpha(item: AlphaItem) -> NonNegativeInt:
        return item.units * 2
    """
)

_PAR_BETA_SRC = textwrap.dedent(
    """\
    import icontract
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

    class BetaItem(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        units: PositiveInt

    @icontract.ensure(lambda item, result: result == item.units * 3)
    @jezz_callable
    def scale_beta(item: BetaItem) -> NonNegativeInt:
        return item.units * 3
    """
)

_PAR_ALPHA_TEST = textwrap.dedent(
    """\
    from cache_probe.alpha import AlphaItem, scale_alpha

    def test_alpha_doubles_units() -> None:
        assert scale_alpha(AlphaItem(units=4)) == 8
    """
)

_PAR_BETA_TEST = textwrap.dedent(
    """\
    from cache_probe.beta import BetaItem, scale_beta

    def test_beta_triples_units() -> None:
        assert scale_beta(BetaItem(units=4)) == 12
    """
)

_BPS_CONTRACTS = textwrap.dedent(
    """\
    from enum import StrEnum
    from typing import Annotated
    from pydantic import BaseModel, Field
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt

    class FeeTier(StrEnum):
        RETAIL = "retail"
        PLUS = "plus"
        PRIME = "prime"
        SOVEREIGN = "sovereign"
        INTERNAL = "internal"

    class SplitAllocation(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        fee_cents: NonNegativeInt
        net_cents: NonNegativeInt
        shares_cents: Annotated[tuple[NonNegativeInt, ...], Field(min_length=1, max_length=64)]
    """
)

_BPS_ALLOCATOR = textwrap.dedent(
    """\
    from types import MappingProxyType
    from typing import Annotated, Final
    import icontract
    from pydantic import Field
    from bps_split.contracts import FeeTier, SplitAllocation
    from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

    _TIER_BPS: Final = MappingProxyType(
        {
            FeeTier.RETAIL: 35,
            FeeTier.PLUS: 20,
            FeeTier.PRIME: 10,
            FeeTier.SOVEREIGN: 3,
            FeeTier.INTERNAL: 0,
        }
    )

    @icontract.require(lambda weights: len(weights) >= 1)
    @icontract.ensure(
        lambda gross_cents, result: result.fee_cents + sum(result.shares_cents) == gross_cents
    )
    @jezz_callable
    def allocate_payout(
        gross_cents: NonNegativeInt,
        tier: FeeTier,
        weights: Annotated[tuple[PositiveInt, ...], Field(min_length=1, max_length=64)],
    ) -> SplitAllocation:
        fee = (gross_cents * _TIER_BPS[tier]) // 10_000
        net = gross_cents - fee
        total_w = sum(weights)
        bases = [(net * w) // total_w for w in weights]
        rem = net - sum(bases)
        shares = tuple(b + (1 if i < rem else 0) for i, b in enumerate(bases))
        return SplitAllocation(fee_cents=fee, net_cents=net, shares_cents=shares)
    """
)

_BPS_TESTS = textwrap.dedent(
    """\
    from bps_split.allocator import allocate_payout
    from bps_split.contracts import FeeTier

    def test_payout_conserves_exact_integer_cents() -> None:
        res = allocate_payout(10_003, FeeTier.RETAIL, (1, 1, 1))
        assert res.fee_cents + sum(res.shares_cents) == 10_003

    def test_remainder_and_invalid_inputs() -> None:
        res = allocate_payout(10, FeeTier.INTERNAL, (1, 1, 1))
        assert res.shares_cents == (4, 3, 3)
    """
)


def _write_contained(root: Path, rel_path: str, content: str) -> ContainedPath:
    target = ContainedPath.from_parts(root.resolve(), rel_path)
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with target.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
    return target


def _emit_with_usage(
    info: AgentInfo,
    payload: BaseModel,
    usage: RequestUsage,
) -> ModelResponse:
    raw_json = payload.model_dump_json()
    if info.output_tools:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=raw_json)],
            usage=usage,
        )
    return ModelResponse(parts=[TextPart(content=raw_json)], usage=usage)


def _schema_props(info: AgentInfo) -> set[str]:
    if not info.output_tools:
        return set()
    props = info.output_tools[0].parameters_json_schema.get("properties", {})
    return {str(k) for k in props} if isinstance(props, dict) else set()


# ---------------------------------------------------------------------------
# INV-001: PromptCacheMode Resolution & Precedence Law
# ---------------------------------------------------------------------------


@given(
    cli_cache=st.none() | st.sampled_from(list(PromptCacheMode)),
    cfg_cache=st.none() | st.sampled_from(list(PromptCacheMode)),
)
def test_prompt_cache_mode_resolution_and_model_settings(
    cli_cache: PromptCacheMode | None,
    cfg_cache: PromptCacheMode | None,
) -> None:
    expected = (
        cli_cache
        if cli_cache is not None
        else (cfg_cache if cfg_cache is not None else PromptCacheMode.AUTO)
    )
    cfg = JezzModelsConfig(default="google:gemini-3.8-flash", prompt_cache=cfg_cache)
    cli = CliModelOverrides(prompt_cache=cli_cache)
    routing = resolve_specialist_routing(cli, project_config=cfg)
    assert routing.prompt_cache == expected

    settings_a = resolve_cache_model_settings(expected)
    settings_b = resolve_cache_model_settings(expected)
    assert settings_a == settings_b
    assert settings_a is not settings_b
    assert "cache" in settings_a


def test_pyproject_prompt_cache_parsing(tmp_path: Path) -> None:
    toml_doc = (
        _BASE_PYPROJECT
        + '\n[tool.jezz.models]\ndefault = "anthropic:claude-sonnet-4-6"\nprompt_cache = "1h"\n'
    )
    pyproject = _write_contained(tmp_path, "pyproject.toml", toml_doc)
    loaded = load_pyproject_models_config(pyproject)
    assert loaded is not None
    assert loaded.prompt_cache == PromptCacheMode.TTL_1H

    resolved = resolve_specialist_routing(CliModelOverrides(), project_config=loaded)
    assert resolved.prompt_cache == PromptCacheMode.TTL_1H
    assert resolve_cache_model_settings(resolved.prompt_cache) == {"cache": "1h"}
    assert resolve_cache_model_settings(PromptCacheMode.OFF) == {"cache": False}
    assert resolve_cache_model_settings(PromptCacheMode.PREFIX_ONLY) == {
        "cache": {"messages": False}
    }


# ---------------------------------------------------------------------------
# INV-002: Statutory Instruction Prefix Canonicalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statutory_instruction_prefix_canonicalization() -> None:
    dummy = FunctionModel(
        lambda _m, i: _emit_with_usage(
            i,
            EpistemicObservation(inspected_files=(), discovered_symbols=(), summary="ok"),
            RequestUsage(),
        )
    )
    builders = (
        build_specify_agent,
        build_instrument_agent,
        build_synthesize_agent,
        build_refactor_agent,
    )
    assert len(builders) == 4
    assert "JBC v1.2 Statutory Requirements" in JBC_STATUTORY_BRIEF
    assert "Canonical JBC v1.2 Idioms" in JBC_CANONICAL_EXEMPLAR

    report = await verify_cross_provider_agent_compatibility(
        model_ids=("anthropic:claude-sonnet-4-6",),
        cache_modes=(PromptCacheMode.AUTO,),
    )
    assert report.all_compatible
    statutory = [
        c
        for c in report.checks
        if c.agent_name
        in {
            "jezz_contract_specifier",
            "jezz_falsification_instrumenter",
            "jezz_code_synthesizer",
            "jezz_structural_refactorer",
        }
    ]
    assert len(statutory) == 4
    assert all(c.static_instruction_count == 3 and c.cache_translated for c in statutory)
    _ = dummy


# ---------------------------------------------------------------------------
# INV-003: User-Prompt CachePoint Partitioning Across Parallel Fan-Out
# ---------------------------------------------------------------------------


def _select_parallel_payload(messages: list[ModelMessage], info: AgentInfo) -> BaseModel:
    props = _schema_props(info)
    blob = str(messages)
    alpha_mod = FilePatchArtifact(
        file_path="src/cache_probe/alpha.py",
        source_code=_PAR_ALPHA_SRC,
        rationale="Alpha module.",
    )
    beta_mod = FilePatchArtifact(
        file_path="src/cache_probe/beta.py",
        source_code=_PAR_BETA_SRC,
        rationale="Beta module.",
    )
    if "inspected_files" in props:
        return EpistemicObservation(inspected_files=(), discovered_symbols=(), summary="Clean.")
    if "contract_patches" in props:
        return ContractSpecificationBundle(
            contract_patches=(alpha_mod, beta_mod),
            declared_symbols=("AlphaItem", "scale_alpha", "BetaItem", "scale_beta"),
            summary="Contracts.",
        )
    if "test_patches" in props:
        is_alpha = "Active Instrumentation Scope: alpha_scope" in blob
        t_path = "tests/test_alpha.py" if is_alpha else "tests/test_beta.py"
        t_src = _PAR_ALPHA_TEST if is_alpha else _PAR_BETA_TEST
        inv_id = "INV-001" if is_alpha else "INV-002"
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(file_path=t_path, source_code=t_src, rationale="Scope test."),
            ),
            covered_invariant_ids=(inv_id,),
            summary=f"Instrumented {inv_id}.",
        )
    is_alpha_slice = "Active Slice Scope: alpha_scope" in blob
    return ConstructionPatchBundle(
        implementation_patches=(alpha_mod if is_alpha_slice else beta_mod,),
        summary="Synthesized slice.",
    )


def _build_two_scope_mandate() -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id="MND-CACHE-PAR-01",
        title="Parallel Two-Scope Cache Partition Probe",
        objective="Verify inline CachePoint placement across parallel INSTRUMENT and DELEGATE.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Alpha doubles positive units.",
                verification_symbol="test_alpha_doubles_units",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="Beta triples positive units.",
                verification_symbol="test_beta_triples_units",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="alpha_scope",
                owned_files=frozenset({"src/cache_probe/alpha.py", "tests/test_alpha.py"}),
                contract_symbols=("AlphaItem", "scale_alpha", "test_alpha_doubles_units"),
            ),
            ModuleWriteScope(
                scope_id="beta_scope",
                owned_files=frozenset({"src/cache_probe/beta.py", "tests/test_beta.py"}),
                contract_symbols=("BetaItem", "scale_beta", "test_beta_triples_units"),
            ),
        ),
    )


class _CacheProbeWrapper(WrapperModel):
    """Test wrapper capturing unified ``ModelSettings['cache']`` on outgoing requests."""

    def __init__(self, inner: Model) -> None:
        super().__init__(inner)
        self.requested_cache_values: list[object] = []
        self.last_cache_on: bool = False

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        if model_settings is not None and "cache" in model_settings:
            self.requested_cache_values.append(model_settings["cache"])
            self.last_cache_on = bool(model_settings["cache"])
        return await super().request(messages, model_settings, model_request_parameters)


def _extract_sequence_prompts(messages: list[ModelMessage]) -> list[list[object]]:
    if not messages or not isinstance(messages[-1], ModelRequest):
        return []
    collected: list[list[object]] = []
    for part in messages[-1].parts:
        if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
            collected.append(list(part.content))
    return collected


async def _verify_two_scope_cache_run(
    tmp_path: Path,
    sub: str,
    mode: PromptCacheMode,
    mandate: EngineeringMandate,
) -> None:
    ws = ContainedPath.from_parts(tmp_path, sub).resolved_path
    _write_contained(ws, "pyproject.toml", _BASE_PYPROJECT)
    _write_contained(ws, "src/cache_probe/__init__.py", '"""Pkg."""\n')
    _write_contained(ws, "tests/__init__.py", '"""Tests."""\n')
    tuple_prompts: list[list[object]] = []

    def _router(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tuple_prompts.extend(_extract_sequence_prompts(messages))
        usage = RequestUsage(
            input_tokens=2000,
            output_tokens=150,
            cache_read_tokens=1600 if mode == PromptCacheMode.AUTO else 0,
        )
        return _emit_with_usage(info, _select_parallel_payload(messages, info), usage)

    wrapped_model = _CacheProbeWrapper(FunctionModel(_router))
    base = BuildingInspector.from_workspace(ws)
    inspector = BuildingInspector(
        manifest=ManifestDependencies(
            runtime_roots=base.manifest.runtime_roots,
            dev_roots=base.manifest.dev_roots,
            workspace_roots=frozenset({"cache_probe", "tests"}),
        )
    )
    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(wrapped_model),
        inspector=inspector,
        gate_runner=InProcessGateRunner(),
        budget=PceRuntimeBudget(prompt_cache_mode=mode),
    )
    res = await orchestrator.execute_mandate(ws, mandate)
    assert res.status == PceRunStatus.OCCUPANCY_CERTIFIED
    if mode == PromptCacheMode.AUTO:
        assert len(tuple_prompts) == 4
        assert all(isinstance(p[1], CachePoint) and isinstance(p[0], str) for p in tuple_prompts)
        assert tuple_prompts[0][0] == tuple_prompts[1][0]
        assert all(flag is True for flag in wrapped_model.requested_cache_values)
        assert res.cache_hit_ratio == 0.8
        assert "80.0%` hit" in format_workflow_markdown(res)
        return
    assert tuple_prompts == []
    assert all(flag is False for flag in wrapped_model.requested_cache_values)
    assert res.total_cache_read_tokens == 0


@pytest.mark.asyncio
async def test_user_prompt_cache_point_injection_on_shared_prefix(tmp_path: Path) -> None:
    assert partition_cached_user_prompt("Prefix only", "") == "Prefix only"
    assert partition_cached_user_prompt("Pfx", "Sfx", mode=PromptCacheMode.OFF) == "PfxSfx"
    part_1h = partition_cached_user_prompt("Pfx", "Sfx", mode=PromptCacheMode.TTL_1H)
    assert isinstance(part_1h, tuple) and isinstance(part_1h[1], CachePoint)
    assert part_1h[1].ttl == "1h"

    mandate = _build_two_scope_mandate()
    await _verify_two_scope_cache_run(tmp_path, "ws_auto", PromptCacheMode.AUTO, mandate)
    await _verify_two_scope_cache_run(tmp_path, "ws_off", PromptCacheMode.OFF, mandate)


# ---------------------------------------------------------------------------
# INV-004: Cache Telemetry & Effective Billed Input Compression Laws
# ---------------------------------------------------------------------------


@given(
    uncached=st.integers(min_value=0, max_value=500_000),
    cache_read=st.integers(min_value=0, max_value=500_000),
    cache_write=st.integers(min_value=0, max_value=500_000),
)
def test_cache_telemetry_and_effective_cost_compression_laws(
    uncached: int,
    cache_read: int,
    cache_write: int,
) -> None:
    total_in = uncached + cache_read + cache_write
    hit = compute_cache_hit_ratio(total_in, cache_read)
    eff = compute_effective_billed_input_tokens(total_in, cache_read, cache_write)
    sav = compute_input_cost_savings_ratio(total_in, cache_read, cache_write)

    assert 0.0 <= hit <= 1.0
    assert eff >= 0.0
    assert -2.0 <= sav <= 1.0
    if total_in == 0:
        assert hit == 0.0 and eff == 0.0 and sav == 0.0
    if cache_read == 0 and cache_write == 0:
        assert eff == float(total_in)
        assert sav == 0.0
    if cache_read > 0 and cache_write == 0:
        assert eff < total_in
        assert sav >= 0.0

    tel = AgentInvocationTelemetry(
        behavior_kind=BehaviorKind.SYNTHESIZE,
        specialist_role="code_synthesizer",
        model_id="anthropic:claude-sonnet-4-6",
        requests=1,
        tool_calls=0,
        input_tokens=total_in,
        output_tokens=100,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )
    assert tel.cache_hit_ratio == hit
    assert tel.effective_billed_input_tokens == eff
    assert tel.input_cost_savings_ratio == sav


# ---------------------------------------------------------------------------
# INV-005: Cross-Provider Wire, Schema, & Cache Translation Compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_provider_wire_schema_and_cache_compatibility() -> None:
    report = await verify_cross_provider_agent_compatibility()
    assert report.failures == ()
    assert report.all_compatible is True
    assert report.models_checked == DEFAULT_WIRE_PROBE_MODELS
    assert report.agents_per_model == 16
    assert report.total_wire_probes == len(DEFAULT_WIRE_PROBE_MODELS) * 4 * 16
    assert all(c.compatible and c.cache_translated for c in report.checks)


# ---------------------------------------------------------------------------
# INV-006: Multi-Provider Matrix Benchmark & CLI Suite Parsing
# ---------------------------------------------------------------------------


def _select_bps_matrix_payload(info: AgentInfo) -> BaseModel:
    props = _schema_props(info)
    c_patch = FilePatchArtifact(
        file_path="src/bps_split/contracts.py",
        source_code=_BPS_CONTRACTS,
        rationale="Contracts.",
    )
    a_patch = FilePatchArtifact(
        file_path="src/bps_split/allocator.py",
        source_code=_BPS_ALLOCATOR,
        rationale="Allocator.",
    )
    t_patch = FilePatchArtifact(
        file_path="tests/test_bps_split.py",
        source_code=_BPS_TESTS,
        rationale="Tests.",
    )
    table: tuple[tuple[str, BaseModel], ...] = (
        (
            "spec_markdown",
            SddSpecArtifact(
                spec_markdown=(
                    "# Spec\nWHEN called THE SYSTEM SHALL split.\n"
                    "#### Scenario: Basic\n- GIVEN 100\n- WHEN split\n- THEN 100\n"
                )
            ),
        ),
        ("design_markdown", SddDesignArtifact(design_markdown="# Design\nModule.\n")),
        ("tasks_markdown", SddTasksArtifact(tasks_markdown="- [ ] 1.1 Implement\n")),
        (
            "completed_tasks_markdown",
            SddImplementationBundle(
                patches=(c_patch, a_patch, t_patch),
                completed_tasks_markdown="- [x] 1.1 Implement\n",
            ),
        ),
        (
            "inspected_files",
            EpistemicObservation(inspected_files=(), discovered_symbols=(), summary="Clean."),
        ),
        (
            "contract_patches",
            ContractSpecificationBundle(
                contract_patches=(c_patch,),
                declared_symbols=("FeeTier", "SplitAllocation"),
                summary="Specified.",
            ),
        ),
        (
            "test_patches",
            InstrumentationSuiteBundle(
                test_patches=(t_patch,),
                covered_invariant_ids=("INV-001", "INV-002"),
                summary="Instrumented.",
            ),
        ),
        (
            "implementation_patches",
            ConstructionPatchBundle(implementation_patches=(a_patch,), summary="Synthesized."),
        ),
    )
    for key, model_obj in table:
        if key in props:
            return model_obj
    raise AssertionError(f"Unexpected benchmark schema keys: {props}")


def test_parse_provider_suite_spec_variants_and_rejections() -> None:
    uniform = parse_provider_suite_spec("claude_1h=anthropic:claude-sonnet-4-6@1h")
    assert uniform.suite_label == "claude_1h"
    assert uniform.routing.is_uniform
    assert uniform.routing.synthesize == "anthropic:claude-sonnet-4-6"
    assert uniform.routing.prompt_cache == PromptCacheMode.TTL_1H

    tiered = parse_provider_suite_spec(
        "hybrid=arch:anthropic:claude-opus-4-8,build:openai:gpt-5.6-sol,inspect:google:gemini-3.8-flash@prefix-only"
    )
    assert tiered.routing.specify == "anthropic:claude-opus-4-8"
    assert tiered.routing.synthesize == "openai:gpt-5.6-sol"
    assert tiered.routing.slot_mapping()[SpecialistRoleSlot.INSPECT] == "google:gemini-3.8-flash"
    assert tiered.routing.prompt_cache == PromptCacheMode.PREFIX_ONLY

    for invalid in ("missing_equals", "../escape=test", "bad=arch:test,unknown_key:test"):
        with pytest.raises(ValueError):
            parse_provider_suite_spec(invalid)


def test_multi_provider_matrix_benchmark_and_cli_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _mock_instantiate(model_name: str, _key: object) -> _CacheProbeWrapper:
        holder: list[_CacheProbeWrapper] = []

        def _respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            is_cached = bool(holder and holder[0].last_cache_on)
            usage = RequestUsage(
                input_tokens=1000,
                output_tokens=100,
                cache_read_tokens=800 if is_cached else 0,
            )
            return _emit_with_usage(info, _select_bps_matrix_payload(info), usage)

        _ = model_name
        probe = _CacheProbeWrapper(FunctionModel(_respond))
        holder.append(probe)
        return probe

    monkeypatch.setattr(cli_mod, "_instantiate_cli_model", _mock_instantiate)
    work_dir = ContainedPath.from_parts(tmp_path, "matrix_work").resolved_path
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = main(
        [
            "benchmark",
            str(work_dir),
            "--suite",
            "cached=test@auto",
            "--suite",
            "uncached=test@off",
            "--case-id",
            "case_01_bps_split",
            "--gate-runner",
            "in-process",
            "--write-json",
            "matrix.json",
        ],
        stdout=out_buf,
        stderr=err_buf,
    )
    assert rc == 0, err_buf.getvalue()
    rendered = out_buf.getvalue()
    assert "Track 7 Cross-Provider & Prompt-Cache Matrix" in rendered
    assert "`cached`" in rendered and "`uncached`" in rendered

    json_file = ContainedPath.from_parts(work_dir, "matrix.json")
    matrix = ProviderMatrixComparison.model_validate_json(
        json_file.resolved_path.read_text(encoding="utf-8")
    )
    assert matrix.suite_labels == ("cached", "uncached")
    cached_pce = matrix.comparisons[0].treatment_summary
    uncached_pce = matrix.comparisons[1].treatment_summary
    assert cached_pce.cache_hit_ratio == 0.8
    assert uncached_pce.cache_hit_ratio == 0.0
    assert cached_pce.effective_billed_input_tokens < uncached_pce.effective_billed_input_tokens
    assert "Cross-Suite Summary" in format_provider_matrix_markdown(matrix)

    plan_repo = ContainedPath.from_parts(tmp_path, "plan_repo").resolved_path
    _write_contained(plan_repo, "pyproject.toml", _BASE_PYPROJECT)
    _write_contained(plan_repo, "src/cache_probe/__init__.py", '"""Pkg."""\n')

    def _mock_plan_model(model_name: str, _key: object) -> FunctionModel:
        _ = model_name
        two_scope = _build_two_scope_mandate()

        def _plan_router(_m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            props = _schema_props(info)
            if "mandate" in props:
                out: BaseModel = FramedMandateProposal(mandate=two_scope, framing_notes="Plan.")
            elif "inspected_files" in props:
                out = EpistemicObservation(
                    inspected_files=(), discovered_symbols=(), summary="Inspected."
                )
            else:
                out = ScopeDecompositionPlan(
                    write_scopes=two_scope.write_scopes,
                    decomposition_rationale="Two disjoint scopes.",
                )
            return _emit_with_usage(info, out, RequestUsage())

        return FunctionModel(_plan_router)

    monkeypatch.setattr(cli_mod, "_instantiate_cli_model", _mock_plan_model)
    plan_out = io.StringIO()
    plan_rc = main(
        [
            "run",
            str(plan_repo),
            "--model",
            "test",
            "--prompt-cache",
            "off",
            "--prompt",
            "Plan two-scope module.",
            "--plan-only",
            "--gate-runner",
            "in-process",
        ],
        stdout=plan_out,
        stderr=err_buf,
    )
    assert plan_rc == 0
    assert "HALTED_FOR_PLAN_APPROVAL" in plan_out.getvalue()


def test_inline_cache_point_coalescing_on_implicit_prefix_models() -> None:
    dummy = SecretStr("offline-test-token")
    partitioned = partition_cached_user_prompt(
        "SharedPrefix:",
        "ScopeSuffix",
        mode=PromptCacheMode.AUTO,
    )
    assert isinstance(partitioned, tuple)

    claude = infer_authenticated_model("anthropic:claude-sonnet-4-6", api_key=dummy)
    gpt56 = infer_authenticated_model("openai-responses:gpt-5.6", api_key=dummy)
    openrouter = infer_authenticated_model(
        "openrouter:anthropic/claude-sonnet-4-6",
        api_key=dummy,
    )
    gemini = infer_authenticated_model("google:gemini-3.8-flash", api_key=dummy)
    wrapped_gemini = TlsResilientModel("google:gemini-3.8-flash", api_key=dummy)

    assert model_supports_inline_cache_points(claude) is True
    assert model_supports_inline_cache_points(gpt56) is True
    assert model_supports_inline_cache_points(openrouter) is True
    assert model_supports_inline_cache_points(gemini) is False
    assert model_supports_inline_cache_points(wrapped_gemini) is False

    assert normalize_prompt_for_model(partitioned, claude) == partitioned
    assert normalize_prompt_for_model(partitioned, gpt56) == partitioned
    assert normalize_prompt_for_model(partitioned, gemini) == "SharedPrefix:ScopeSuffix"
    assert normalize_prompt_for_model(partitioned, wrapped_gemini) == "SharedPrefix:ScopeSuffix"
