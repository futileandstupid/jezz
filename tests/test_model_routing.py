"""Verification suite for multi-model specialist routing, telemetry, and vault deduplication."""

import io
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr, ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel

from jezz.agents import (
    ROLE_TIER_MAP,
    CliModelOverrides,
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    FilePatchArtifact,
    FramedMandateProposal,
    InProcessGateRunner,
    InstrumentationSuiteBundle,
    JezzModelsConfig,
    PceModelSuite,
    PceRunStatus,
    PceWorkflowOrchestrator,
    SpecialistRoleSlot,
    SpecialistTier,
    discover_models_config,
    load_pyproject_models_config,
    resolve_specialist_routing,
)
from jezz.cli import format_workflow_markdown, main
from jezz.guards import ContainedPath
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope
from jezz.secrets import SecretResolutionError, canonical_provider_family

_SAMPLE_MODELS = (
    "google:gemini-3.8-flash",
    "google:gemini-3.8-pro",
    "anthropic:claude-sonnet-4-6",
    "openai:gpt-5.4",
    "test",
)

_PYPROJECT_BASE = textwrap.dedent(
    """\
    [project]
    name = "tiered-demo"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0", "pydantic>=2.14.4"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)

_CONTRACT_CODE = textwrap.dedent(
    """\
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, PositiveInt

    class UnitSpec(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        count: PositiveInt
    """
)

_TEST_CODE = textwrap.dedent(
    """\
    from tiered_demo.calc import UnitSpec, double_units

    def test_double_units_exact() -> None:
        assert double_units(UnitSpec(count=7)) == 14
    """
)

_IMPL_CODE = textwrap.dedent(
    """\
    import icontract
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

    class UnitSpec(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        count: PositiveInt

    @icontract.ensure(lambda spec, result: result == spec.count * 2)
    @jezz_callable
    def double_units(spec: UnitSpec) -> NonNegativeInt:
        return spec.count * 2
    """
)


class _TaggedFunctionModel(WrapperModel):
    """Test double exposing ``configured_model_id`` for multi-tier telemetry assertions."""

    def __init__(self, label: str, inner: FunctionModel) -> None:
        super().__init__(inner)
        self._label = label

    @property
    def configured_model_id(self) -> str:
        return self._label


def _write_contained(root: Path, rel_path: str, content: str) -> ContainedPath:
    contained = ContainedPath.from_parts(root.resolve(), rel_path)
    contained.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with contained.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
    return contained


def _build_demo_mandate() -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id="MND-TIERED-01",
        title="Tiered Unit Doubler",
        objective="Implement bounded integer unit doubler under tiered specialist routing.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Doubles positive units exactly.",
                verification_symbol="test_double_units_exact",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="calc_scope",
                owned_files=frozenset({"src/tiered_demo/calc.py", "tests/test_calc.py"}),
                contract_symbols=("UnitSpec", "double_units", "test_double_units_exact"),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# 1. Hypothesis Precedence & Config Validation Properties
# ---------------------------------------------------------------------------


def _expected_slot_resolution(
    slot: SpecialistRoleSlot,
    cli: CliModelOverrides,
    cfg: JezzModelsConfig,
) -> str:
    tier = ROLE_TIER_MAP[slot]
    cli_tier_map = {
        SpecialistTier.ARCHITECT: cli.architect_model,
        SpecialistTier.BUILDER: cli.builder_model,
        SpecialistTier.INSPECTOR: cli.inspector_model,
    }
    if cli_tier_map[tier] is not None:
        return str(cli_tier_map[tier])
    if cli.model is not None:
        return cli.model
    if slot in cfg.roles:
        return cfg.roles[slot]
    cfg_tier_map = {
        SpecialistTier.ARCHITECT: cfg.architect,
        SpecialistTier.BUILDER: cfg.builder,
        SpecialistTier.INSPECTOR: cfg.inspector,
    }
    if cfg_tier_map[tier] is not None:
        return str(cfg_tier_map[tier])
    return str(cfg.default)


@given(data=st.data())
def test_property_specialist_routing_precedence_law(data: st.DataObject) -> None:
    opt_model = st.none() | st.sampled_from(_SAMPLE_MODELS)
    cli = CliModelOverrides(
        model=data.draw(opt_model),
        architect_model=data.draw(opt_model),
        builder_model=data.draw(opt_model),
        inspector_model=data.draw(opt_model),
    )
    roles_dict = data.draw(
        st.dictionaries(
            st.sampled_from(list(SpecialistRoleSlot)),
            st.sampled_from(_SAMPLE_MODELS),
            max_size=4,
        )
    )
    cfg = JezzModelsConfig(
        default=data.draw(st.sampled_from(_SAMPLE_MODELS)),
        architect=data.draw(opt_model),
        builder=data.draw(opt_model),
        inspector=data.draw(opt_model),
        roles=roles_dict,
    )
    resolved = resolve_specialist_routing(cli, project_config=cfg)
    mapping = resolved.slot_mapping()
    for slot in SpecialistRoleSlot:
        assert mapping[slot] == _expected_slot_resolution(slot, cli, cfg)


def test_models_config_parses_pyproject_and_rejects_credentials_or_unknown_roles(
    tmp_path: Path,
) -> None:
    toml_text = (
        _PYPROJECT_BASE
        + "\n[tool.jezz.models]\n"
        + 'default = "google:gemini-3.8-flash"\n'
        + 'architect = "google:gemini-3.8-pro"\n\n'
        + "[tool.jezz.models.roles]\n"
        + 'hypothesize = "anthropic:claude-opus-4-6"\n'
    )
    pyproject = _write_contained(tmp_path, "pyproject.toml", toml_text)
    cfg = load_pyproject_models_config(pyproject)
    assert cfg is not None
    assert discover_models_config((tmp_path,)) == cfg

    resolved = resolve_specialist_routing(CliModelOverrides(), project_config=cfg)
    assert resolved.frame == "google:gemini-3.8-pro"
    assert resolved.specify == "google:gemini-3.8-pro"
    assert resolved.hypothesize == "anthropic:claude-opus-4-6"
    assert resolved.synthesize == "google:gemini-3.8-flash"
    assert resolved.unique_provider_families() == frozenset({"google", "anthropic"})

    # Reject accidental pass:// URI or API key literal in model config
    with pytest.raises(ValidationError, match="pass://"):
        CliModelOverrides(model="pass://AI-Keys/Gemini/API Key")

    synthetic_cred = "sk-" + ("x" * 24)
    with pytest.raises(ValidationError, match="Refusing credential"):
        JezzModelsConfig(default=synthetic_cred)

    bad_role_toml = _PYPROJECT_BASE + '\n[tool.jezz.models.roles]\nunknown_slot = "test"\n'
    bad_target = _write_contained(tmp_path, "bad_pyproject.toml", bad_role_toml)
    with pytest.raises(ValueError, match="Unknown specialist role"):
        load_pyproject_models_config(bad_target)


def test_build_suite_deduplicates_model_instantiation_and_formats_labels() -> None:
    cfg = JezzModelsConfig(
        default="google:gemini-3.8-flash",
        architect="google:gemini-3.8-pro",
        inspector="google-decisions:gemini-3.8-flash-lite",
    )
    routing = resolve_specialist_routing(CliModelOverrides(), project_config=cfg)
    assert not routing.is_uniform
    assert "tiered(" in routing.summary_label()
    assert canonical_provider_family("google-decisions:gemini-3.8-flash-lite") == "google"
    assert routing.unique_provider_families() == frozenset({"google"})

    instantiated: list[str] = []

    def _factory(model_name: str) -> _TaggedFunctionModel:
        instantiated.append(model_name)
        return _TaggedFunctionModel(
            model_name,
            FunctionModel(lambda _m, _i: ModelResponse(parts=[])),
        )

    suite = routing.build_suite(_factory)
    # 7 slots mapped from only 3 unique model strings -> factory invoked exactly 3 times
    assert instantiated == [
        "google:gemini-3.8-pro",
        "google-decisions:gemini-3.8-flash-lite",
        "google:gemini-3.8-flash",
    ]
    assert suite.frame_model is suite.specify_model
    assert suite.frame_model is suite.hypothesize_model
    assert suite.instrument_model is suite.synthesize_model
    assert suite.instrument_model is suite.refactor_model


# ---------------------------------------------------------------------------
# 2. End-to-End Multi-Model Orchestration & Per-Step Model Telemetry
# ---------------------------------------------------------------------------


def _scripted_payload_for_props(
    props: dict[str, object],
    mandate: EngineeringMandate,
) -> dict[str, object]:
    if "mandate" in props:
        return FramedMandateProposal(
            mandate=mandate,
            framing_notes="Framed cleanly by architect model.",
        ).model_dump(mode="json")
    if "inspected_files" in props:
        return EpistemicObservation(
            inspected_files=(),
            discovered_symbols=(),
            summary="Workspace grounded by inspector model.",
        ).model_dump(mode="json")
    if "contract_patches" in props:
        return ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/tiered_demo/calc.py",
                    source_code=_CONTRACT_CODE,
                    rationale="Contract alias.",
                ),
            ),
            declared_symbols=("UnitSpec",),
            summary="Specified contract.",
        ).model_dump(mode="json")
    if "test_patches" in props:
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_calc.py",
                    source_code=_TEST_CODE,
                    rationale="Falsification suite.",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Instrumented tests.",
        ).model_dump(mode="json")
    return ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/tiered_demo/calc.py",
                source_code=_IMPL_CODE,
                rationale="Concrete implementation.",
            ),
        ),
        summary="Synthesized implementation.",
    ).model_dump(mode="json")


def _make_scripted_handler(mandate: EngineeringMandate) -> FunctionModel:
    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        if not info.output_tools:
            return ModelResponse(parts=[])
        out_tool = info.output_tools[0]
        raw_props = out_tool.parameters_json_schema.get("properties", {})
        props = raw_props if isinstance(raw_props, dict) else {}
        payload = _scripted_payload_for_props(props, mandate)
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args=payload)])

    return FunctionModel(_respond)


async def test_orchestrator_records_distinct_specialist_models_in_telemetry(
    tmp_path: Path,
) -> None:
    _write_contained(tmp_path, "pyproject.toml", _PYPROJECT_BASE)
    _write_contained(tmp_path, "src/tiered_demo/__init__.py", "")
    mandate = _build_demo_mandate()
    inner = _make_scripted_handler(mandate)

    suite = PceModelSuite(
        frame_model=_TaggedFunctionModel("google:gemini-3.8-pro", inner),
        inspect_model=_TaggedFunctionModel("google:gemini-3.8-flash-lite", inner),
        specify_model=_TaggedFunctionModel("google:gemini-3.8-pro", inner),
        instrument_model=_TaggedFunctionModel("google:gemini-3.8-flash", inner),
        synthesize_model=_TaggedFunctionModel("google:gemini-3.8-flash", inner),
        hypothesize_model=_TaggedFunctionModel("anthropic:claude-opus-4-6", inner),
        refactor_model=_TaggedFunctionModel("google:gemini-3.8-flash", inner),
    )
    orchestrator = PceWorkflowOrchestrator(suite, gate_runner=InProcessGateRunner())
    result = await orchestrator.execute_prompt(tmp_path, "Build unit doubler.")

    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    by_role = {entry.specialist_role: entry.model_id for entry in result.telemetry}
    assert by_role == {
        "mandate_framer": "google:gemini-3.8-pro",
        "code_inspector": "google:gemini-3.8-flash-lite",
        "contract_specifier": "google:gemini-3.8-pro",
        "falsification_instrumenter": "google:gemini-3.8-flash",
        "code_synthesizer": "google:gemini-3.8-flash",
    }
    md = format_workflow_markdown(result)
    assert "**Models Exercised:** `google:gemini-3.8-pro`, `google:gemini-3.8-flash-lite`" in md
    assert "`google:gemini-3.8-flash`" in md


# ---------------------------------------------------------------------------
# 3. CLI Vault Deduplication & Cross-Provider --api-key-uri Protection
# ---------------------------------------------------------------------------


def test_cli_deduplicates_same_provider_vault_reads_and_blocks_cross_provider_uri(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_contained(tmp_path, "pyproject.toml", _PYPROJECT_BASE)
    _write_contained(tmp_path, "src/tiered_demo/__init__.py", "")

    # 1. Missing both --model and [tool.jezz.models] -> exit code 2
    err_missing = io.StringIO()
    code_missing = main(["run", str(tmp_path), "--prompt", "Build demo"], stderr=err_missing)
    assert code_missing == 2
    assert "No model configured" in err_missing.getvalue()

    # 2. Cross-provider routing + single --api-key-uri -> blocked by SecretResolutionError (exit 2)
    err_cross = io.StringIO()
    code_cross = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "google:gemini-3.8-flash",
            "--architect-model",
            "anthropic:claude-sonnet-4-6",
            "--api-key-uri",
            "pass://AI-Keys/Gemini Developer API/API Key",
            "--prompt",
            "Build demo",
        ],
        stderr=err_cross,
    )
    assert code_cross == 2
    assert "cannot be shared across multiple model providers" in err_cross.getvalue()

    # 3. Same-provider multi-model routing resolves Proton Pass credential only ONCE
    vault_calls: list[str] = []

    def _fake_resolve(model_name: str, **_kwargs: object) -> SecretStr | None:
        vault_calls.append(model_name)
        return SecretStr("vault-bound-placeholder")

    monkeypatch.setattr("jezz.cli.resolve_model_api_key", _fake_resolve)
    monkeypatch.setattr(
        "jezz.cli._instantiate_cli_model",
        lambda name, _k: _TaggedFunctionModel(
            name,
            _make_scripted_handler(_build_demo_mandate()),
        ),
    )

    out_ok = io.StringIO()
    code_ok = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "google:gemini-3.8-flash",
            "--architect-model",
            "google:gemini-3.8-pro",
            "--inspector-model",
            "google-decisions:gemini-3.8-flash-lite",
            "--gate-runner",
            "in-process",
            "--prompt",
            "Build demo",
        ],
        stdout=out_ok,
    )
    assert code_ok == 0, out_ok.getvalue()
    # All 3 models belong to canonical 'google' provider family -> 1 vault lookup!
    assert len(vault_calls) == 1
    assert SecretResolutionError is not None
