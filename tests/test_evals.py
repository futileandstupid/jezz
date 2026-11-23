"""Deterministic unit, property-based, CLI, and dogfood tests for Track 4 (jezz.evals)."""

import io
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents import (
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    FilePatchArtifact,
    InProcessGateRunner,
    InstrumentationSuiteBundle,
    JezzAgentDeps,
    PceModelSuite,
    WorkspaceSandbox,
    build_readonly_toolset,
)
from jezz.behaviors import build_greenfield_contract_first_graph
from jezz.cli import TlsResilientModel, main
from jezz.codes import (
    BuildingInspector,
    CodePermit,
    GateAttestation,
    GateKind,
    compute_units_digest,
    issue_certificate,
    load_workspace_units,
)
from jezz.evals import (
    AbBenchmarkComparison,
    BenchmarkArm,
    BenchmarkHarnessDeps,
    BenchmarkRunConfig,
    InProcessWorkspaceVerifier,
    SddDesignArtifact,
    SddImplementationBundle,
    SddSpecArtifact,
    SddTasksArtifact,
    build_track4_benchmark_cases,
    build_track4_dataset,
    format_ab_benchmark_markdown,
    run_ab_benchmark,
    run_sdd_control_case,
    validate_sdd_markdown_compliance,
)
from jezz.methodology import (
    DomainInvariant,
    EngineeringDossier,
    EngineeringMandate,
    ModuleWriteScope,
    audit_pce_dossier,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_PCE_BPS_CONTRACTS = textwrap.dedent(
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

_PCE_BPS_ALLOCATOR = textwrap.dedent(
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

_PCE_BPS_TESTS = textwrap.dedent(
    """\
    import icontract
    import pytest
    from bps_split.allocator import allocate_payout
    from bps_split.contracts import FeeTier
    from pydantic import ValidationError

    def test_payout_conserves_exact_integer_cents() -> None:
        res = allocate_payout(10_003, FeeTier.RETAIL, (1, 1, 1))
        assert res.fee_cents == 35
        assert res.fee_cents + sum(res.shares_cents) == 10_003

    def test_remainder_and_invalid_inputs() -> None:
        res = allocate_payout(10, FeeTier.INTERNAL, (1, 1, 1))
        assert res.shares_cents == (4, 3, 3)
        with pytest.raises((ValidationError, icontract.ViolationError)):
            allocate_payout(-5, FeeTier.RETAIL, (1,))
    """
)

_SDD_FLAWED_CONTRACTS = textwrap.dedent(
    """\
    from enum import StrEnum
    from pydantic import BaseModel

    class FeeTier(StrEnum):
        RETAIL = "retail"
        PLUS = "plus"
        PRIME = "prime"
        SOVEREIGN = "sovereign"
        INTERNAL = "internal"

    class SplitAllocation(BaseModel):
        fee_cents: int
        net_cents: int
        shares_cents: tuple[int, ...]
    """
)

_SDD_FLAWED_ALLOCATOR = textwrap.dedent(
    """\
    from bps_split.contracts import FeeTier, SplitAllocation

    def allocate_payout(gross_cents, tier, weights):
        if tier == FeeTier.RETAIL:
            rate = 0.0035
        elif tier == FeeTier.PLUS:
            rate = 0.0020
        elif tier == FeeTier.PRIME:
            rate = 0.0010
        elif tier == FeeTier.SOVEREIGN:
            rate = 0.0003
        else:
            rate = 0.0
        fee = int(gross_cents * rate)
        net = gross_cents - fee
        total = sum(weights)
        shares = tuple(int(net * (w / total)) for w in weights)
        return SplitAllocation(fee_cents=fee, net_cents=net, shares_cents=shares)
    """
)

_SDD_VACUOUS_TESTS = textwrap.dedent(
    """\
    def test_smoke():
        assert True
    """
)


def _emit_structured(info: AgentInfo, payload: BaseModel) -> ModelResponse:
    raw_json = payload.model_dump_json()
    if info.output_tools:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=raw_json)]
        )
    return ModelResponse(parts=[TextPart(content=raw_json)])


def _schema_property_names(info: AgentInfo) -> set[str]:
    if not info.output_tools:
        return set()
    raw_props = info.output_tools[0].parameters_json_schema.get("properties", {})
    return {str(k) for k in raw_props} if isinstance(raw_props, dict) else set()


def _build_mock_payload_table() -> tuple[tuple[str, BaseModel], ...]:
    return (
        (
            "spec_markdown",
            SddSpecArtifact(
                spec_markdown=(
                    "# Spec\nAs a user, I want fee splits.\n"
                    "WHEN requested THE SYSTEM SHALL split.\n"
                    "#### Scenario: Basic\n- GIVEN 100\n- WHEN split\n- THEN ok\n"
                )
            ),
        ),
        (
            "design_markdown",
            SddDesignArtifact(design_markdown="# Design\nModule architecture.\n" * 8),
        ),
        (
            "tasks_markdown",
            SddTasksArtifact(tasks_markdown="- [ ] 1.1 Implement allocator\n- [ ] 1.2 Test\n"),
        ),
        (
            "completed_tasks_markdown",
            SddImplementationBundle(
                patches=(
                    FilePatchArtifact(
                        file_path="src/bps_split/contracts.py",
                        source_code=_SDD_FLAWED_CONTRACTS,
                        rationale="SDD contracts.",
                    ),
                    FilePatchArtifact(
                        file_path="src/bps_split/allocator.py",
                        source_code=_SDD_FLAWED_ALLOCATOR,
                        rationale="SDD float allocator.",
                    ),
                    FilePatchArtifact(
                        file_path="tests/test_bps_split.py",
                        source_code=_SDD_VACUOUS_TESTS,
                        rationale="SDD smoke test.",
                    ),
                ),
                completed_tasks_markdown="- [x] 1.1 Implement allocator\n- [x] 1.2 Test\n",
            ),
        ),
        (
            "contract_patches",
            ContractSpecificationBundle(
                contract_patches=(
                    FilePatchArtifact(
                        file_path="src/bps_split/contracts.py",
                        source_code=_PCE_BPS_CONTRACTS,
                        rationale="Strict FeeTier and SplitAllocation contracts.",
                    ),
                ),
                declared_symbols=("FeeTier", "SplitAllocation"),
                summary="Locked Phase III bps_split contracts.",
            ),
        ),
        (
            "test_patches",
            InstrumentationSuiteBundle(
                test_patches=(
                    FilePatchArtifact(
                        file_path="tests/test_bps_split.py",
                        source_code=_PCE_BPS_TESTS,
                        rationale="Falsification tests for INV-001 and INV-002.",
                    ),
                ),
                covered_invariant_ids=("INV-001", "INV-002"),
                summary="Locked Phase III bps_split falsification suite.",
            ),
        ),
    )


def _build_mock_ab_model() -> FunctionModel:
    inspect_calls: list[int] = []
    table = _build_mock_payload_table()
    synth_fallback = ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/bps_split/allocator.py",
                source_code=_PCE_BPS_ALLOCATOR,
                rationale="Exact integer bps allocator.",
            ),
        ),
        summary="Synthesized Phase IV bps_split allocator.",
    )
    obs = EpistemicObservation(
        inspected_files=(),
        discovered_symbols=(),
        summary="Inspected clean bps_split workspace.",
    )

    def _dispatch(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        props = _schema_property_names(info)
        if "inspected_files" in props:
            inspect_calls.append(1)
            if len(inspect_calls) == 1:
                return ModelResponse(
                    parts=[ToolCallPart(tool_name="_tool_list_authorized_files", args="{}")]
                )
            return _emit_structured(info, obs)
        for key, payload in table:
            if key in props:
                return _emit_structured(info, payload)
        return _emit_structured(info, synth_fallback)

    return FunctionModel(_dispatch)


def test_all_six_track4_benchmark_cases_have_valid_mandates_and_oracles() -> None:
    cases = build_track4_benchmark_cases()
    assert len(cases) == 6
    seen_ids = {spec.case_id for spec, _ in cases}
    assert len(seen_ids) == 6
    assert any(meta.scope_count == 2 for _, meta in cases)
    ds = build_track4_dataset(cases)
    assert len(ds.cases) == 6
    assert len(ds.evaluators) == 4


@given(
    checked=st.booleans(),
    has_modal=st.booleans(),
    has_scenario=st.booleans(),
)
def test_sdd_markdown_compliance_property(
    checked: bool,
    has_modal: bool,
    has_scenario: bool,
) -> None:
    modal_word = "SHALL" if has_modal else "will"
    scen_block = "#### Scenario: Demo" if has_scenario else "#### Example"
    spec_md = f"# Spec\nSystem {modal_word} act.\n{scen_block}\n"
    tasks_md = "- [x] 1.1 Done\n" if checked else "- [ ] 1.1 Pending\n"
    assert validate_sdd_markdown_compliance(spec_md, tasks_md) == (
        checked and has_modal and has_scenario
    )


async def test_ab_benchmark_catches_sdd_float_and_theatre_while_certifying_pce(
    tmp_path: Path,
) -> None:
    case_pair = build_track4_benchmark_cases()[0]
    ds = build_track4_dataset((case_pair,))
    cfg = BenchmarkRunConfig(max_concurrency=1, sync_workspace_venv=False)
    deps = BenchmarkHarnessDeps(
        work_root=tmp_path,
        jezz_root=REPO_ROOT,
        config=cfg,
        verifier=InProcessWorkspaceVerifier(),
        gate_runner=InProcessGateRunner(),
    )
    comparison = await run_ab_benchmark(
        _build_mock_ab_model(),
        deps,
        model_label="mock:function-ab",
        dataset=ds,
    )

    ctrl = comparison.control_outcomes[0]
    treat = comparison.treatment_outcomes[0]
    assert ctrl.arm == BenchmarkArm.SDD_PROSE
    assert ctrl.sdd_markdown_valid is True
    assert ctrl.jbc_occupiable is False
    assert ctrl.life_safety_violations >= 1
    assert "JBC-VRF-001" in ctrl.violated_code_ids
    assert "JBC-COG-004" in ctrl.violated_code_ids
    assert ctrl.oracle_verdict.passed is False

    assert treat.arm == BenchmarkArm.JEZZ_PCE
    assert treat.jbc_occupiable is True
    assert treat.total_jbc_violations == 0
    assert treat.oracle_verdict.passed is True
    assert treat.oracle_verdict.survival_rate == 1.0

    md = format_ab_benchmark_markdown(comparison)
    assert "case_01_bps_split" in md
    assert "control_sdd_prose" in comparison.pydantic_evals_diff_table


def test_cli_benchmark_subcommand_writes_json_and_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jezz.cli.resolve_cli_model", lambda _m: _build_mock_ab_model())
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    exit_code = main(
        (
            "benchmark",
            str(tmp_path),
            "--model",
            "mock:ab",
            "--case-id",
            "case_01_bps_split",
            "--gate-runner",
            "in-process",
            "--max-concurrency",
            "1",
            "--write-json",
            "results/ab_summary.json",
        ),
        stdout=out_buf,
        stderr=err_buf,
    )
    assert exit_code == 0, err_buf.getvalue()
    saved_json = (tmp_path / "results" / "ab_summary.json").read_text(encoding="utf-8")
    parsed = AbBenchmarkComparison.model_validate_json(saved_json)
    assert parsed.treatment_summary.cases_oracle_passed == 1
    assert parsed.control_summary.cases_oracle_passed == 0


async def test_readonly_toolset_handles_absent_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    sandbox = WorkspaceSandbox(tmp_path, frozenset({"src/missing.py"}))
    deps = JezzAgentDeps(
        sandbox=sandbox,
        inspector=BuildingInspector.from_workspace(REPO_ROOT),
        permit=CodePermit.full_book(permit_id="PERMIT-TEST-TOOLS"),
    )
    step = 0

    def _probe_tools(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = info
        nonlocal step
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="_tool_inspect_file_symbols",
                        args='{"rel_path": "src/missing.py"}',
                    ),
                    ToolCallPart(
                        tool_name="_tool_read_workspace_file",
                        args='{"rel_path": "subdir"}',
                    ),
                ]
            )
        return ModelResponse(parts=[TextPart(content=str(messages[-1]))])

    agent = Agent[JezzAgentDeps, str](
        FunctionModel(_probe_tools),
        deps_type=JezzAgentDeps,
        toolsets=(build_readonly_toolset(),),
    )
    res = await agent.run("Probe workspace tools", deps=deps)
    assert "# DIRECTORY:" in res.output


async def test_tls_resilient_model_retries_transient_os_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def _flaky_infer(_name: str) -> FunctionModel:
        def _handler(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = (messages, info)
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                msg = "Simulated SSLV3_ALERT_BAD_RECORD_MAC"
                raise OSError(msg)
            return ModelResponse(parts=[TextPart(content="recovered")])

        return FunctionModel(_handler)

    monkeypatch.setattr("jezz.agents.providers.infer_model", _flaky_infer)
    monkeypatch.setattr("jezz.agents.providers.asyncio.sleep", lambda _s: _noop_sleep())
    resilient = TlsResilientModel("test", max_attempts=3)
    agent = Agent(resilient)
    res = await agent.run("ping")
    assert res.output == "recovered"
    assert attempts == 2


async def _noop_sleep() -> None:
    return None


def test_jezz_evals_package_passes_full_jbc_and_dossier_audit() -> None:
    units = load_workspace_units(REPO_ROOT)
    inspector = BuildingInspector.from_workspace(REPO_ROOT)
    permit = CodePermit.full_book(permit_id="PERMIT-JEZZ-TRACK-4-DOGFOOD")
    attestations = (
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=62,
            duration_ms=480.0,
            peak_memory_kb=65_536,
            summary="Full jezz suite green including Track 4 evals.",
        ),
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy",
            passed=True,
            exit_code=0,
            checks_executed=len(units),
            duration_ms=320.0,
            peak_memory_kb=65_536,
            summary="mypy --strict clean across all workspace files.",
        ),
    )
    report = inspector.inspect_final(units, permit=permit, attestations=attestations)
    assert report.is_occupiable, report.to_markdown()
    cert = issue_certificate(report, artifact_digest=compute_units_digest(units))

    eval_files = frozenset(
        {
            "src/jezz/evals/__init__.py",
            "src/jezz/evals/schemas.py",
            "src/jezz/evals/sdd_baseline.py",
            "src/jezz/evals/verifier.py",
            "src/jezz/evals/oracles.py",
            "src/jezz/evals/tasks.py",
            "src/jezz/evals/evaluators.py",
            "src/jezz/evals/runner.py",
            "tests/test_evals.py",
        }
    )
    mandate = EngineeringMandate(
        mandate_id="MND-JEZZ-TRACK-4",
        title="Track 4 Controlled Pydantic-Evals SDD vs PCE Benchmark Suite",
        objective="Evaluate marketed prose SDD against Jezz PCE across adversarial hazards.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Dataset binds all 6 adversarial cases and 4 deterministic evaluators.",
                verification_symbol="build_track4_dataset",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="run_ab_benchmark executes symmetric Control and Treatment evaluations.",
                verification_symbol="run_ab_benchmark",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="jezz_evals_suite",
                owned_files=eval_files,
                contract_symbols=(
                    "build_track4_benchmark_cases",
                    "build_track4_dataset",
                    "run_ab_benchmark",
                ),
            ),
        ),
    )
    dossier = EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        mutated_files=eval_files,
        inspection_report=report,
        certificate=cert,
    )
    audit = audit_pce_dossier(dossier, units)
    assert audit.is_occupancy_sealed, audit.to_markdown()


async def test_sdd_control_case_routes_stages_via_suite_factory(tmp_path: Path) -> None:
    arch_calls: list[int] = []
    build_calls: list[int] = []
    table = _build_mock_payload_table()

    def _make_recorder(bucket: list[int]) -> FunctionModel:
        def _handler(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            _ = messages
            bucket.append(1)
            props = _schema_property_names(info)
            for key, payload in table:
                if key in props:
                    return _emit_structured(info, payload)
            return ModelResponse(parts=[TextPart(content="unreachable")])

        return FunctionModel(_handler)

    arch_model = _make_recorder(arch_calls)
    build_model = _make_recorder(build_calls)
    suite = PceModelSuite(
        frame_model=arch_model,
        inspect_model=build_model,
        specify_model=arch_model,
        instrument_model=build_model,
        synthesize_model=build_model,
        hypothesize_model=arch_model,
        refactor_model=build_model,
    )
    deps = BenchmarkHarnessDeps(
        work_root=tmp_path,
        jezz_root=REPO_ROOT,
        config=BenchmarkRunConfig(max_concurrency=1, sync_workspace_venv=False),
        verifier=InProcessWorkspaceVerifier(),
        gate_runner=InProcessGateRunner(),
        suite_factory=lambda: suite,
    )
    task_spec, _ = build_track4_benchmark_cases()[0]
    outcome = await run_sdd_control_case(task_spec, build_model, deps)
    assert outcome.completed_cleanly is True
    assert len(arch_calls) == 3
    assert len(build_calls) == 1
