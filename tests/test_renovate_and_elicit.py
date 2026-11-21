"""Verification suite for Brownfield Renovation (BHV-17 REFACTOR) and ELICIT -> FRAME resume."""

import io
import textwrap
from collections.abc import Sequence
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import jezz.cli as cli_mod
from jezz.agents import (
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    ElicitationTicket,
    EpistemicObservation,
    FilePatchArtifact,
    FramedMandateProposal,
    InProcessGateRunner,
    InstrumentationSuiteBundle,
    PceRunStatus,
    PceWorkflowMode,
    PceWorkflowOrchestrator,
    RefactorPatchBundle,
    RemediationHypothesis,
    uniform_model_suite,
)
from jezz.behaviors import BehaviorKind, ExecutionStep
from jezz.cli import main
from jezz.codes import BuildingInspector, OccupancyRuling, load_workspace_units
from jezz.guards import ContainedPath, read_bounded_text
from jezz.methodology import DomainInvariant, EngineeringMandate, ModuleWriteScope

_PYPROJECT_TOML = textwrap.dedent(
    """\
    [project]
    name = "sample-pkg"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)

_LEGACY_DIRTY_CALC_SRC = textwrap.dedent(
    """\
    def double_cents(amount: int) -> int:
        print("legacy debug log")
        return amount * 2
    """
)

_RENOVATED_CLEAN_CALC_SRC = textwrap.dedent(
    """\
    import icontract
    from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

    @icontract.ensure(lambda amount, result: result == amount * 2)
    @jezz_callable
    def double_cents(amount: PositiveInt) -> NonNegativeInt:
        return amount * 2
    """
)

_RENOVATED_CLEAN_FEE_SRC = textwrap.dedent(
    """\
    import icontract
    from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

    @icontract.ensure(lambda fee, result: result == fee + 10)
    @jezz_callable
    def add_flat_fee(fee: PositiveInt) -> NonNegativeInt:
        return fee + 10
    """
)

_CLEAN_CALC_TEST_SRC = textwrap.dedent(
    """\
    from sample_pkg.calc import double_cents

    def test_double_cents_preserves_behavior() -> None:
        assert double_cents(25) == 50
    """
)

_CLEAN_MULTI_TEST_SRC = textwrap.dedent(
    """\
    from sample_pkg.calc import double_cents
    from sample_pkg.fee import add_flat_fee

    def test_double_cents_preserves_behavior() -> None:
        assert double_cents(25) == 50

    def test_add_flat_fee_preserves_behavior() -> None:
        assert add_flat_fee(40) == 50
    """
)


def _write_utf8(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        fh.write(content)


def _prompt_text(messages: Sequence[ModelMessage]) -> str:
    return "\n".join(str(part) for msg in messages for part in msg.parts)


def _build_mandate(*, open_ambiguities: tuple[str, ...] = ()) -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id="MND-CALC-ELICIT",
        title="Verified double_cents arithmetic",
        objective="Implement and verify integer-cent doubling.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="double_cents(x) == 2 * x for positive cents.",
                verification_symbol="test_double_cents_preserves_behavior",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="calc_scope",
                owned_files=frozenset({"src/sample_pkg/calc.py", "tests/test_calc.py"}),
                contract_symbols=("double_cents", "test_double_cents_preserves_behavior"),
            ),
        ),
        open_ambiguities=open_ambiguities,
    )


def _greenfield_stage_payload(schema_title: str, prompt_blob: str) -> str:
    if schema_title == "FramedMandateProposal":
        resolved = "Authoritative Clarifications Resolving Open Ambiguities:" in prompt_blob
        ambiguities = () if resolved else ("Clarify overflow ceiling for cent amounts.",)
        return FramedMandateProposal(
            mandate=_build_mandate(open_ambiguities=ambiguities),
            framing_notes="Resolved framing." if resolved else "Surfaced open ambiguity.",
        ).model_dump_json()
    if schema_title == "EpistemicObservation":
        return EpistemicObservation(
            inspected_files=(),
            discovered_symbols=(),
            summary="Inspected greenfield workspace.",
        ).model_dump_json()
    if schema_title == "ContractSpecificationBundle":
        return ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/sample_pkg/calc.py",
                    source_code=_RENOVATED_CLEAN_CALC_SRC,
                    rationale="Contracted double_cents.",
                ),
            ),
            declared_symbols=("double_cents",),
            summary="Specified double_cents contract.",
        ).model_dump_json()
    if schema_title == "InstrumentationSuiteBundle":
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_calc.py",
                    source_code=_CLEAN_CALC_TEST_SRC,
                    rationale="Locked test_double_cents_preserves_behavior.",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Instrumented test suite.",
        ).model_dump_json()
    return ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/sample_pkg/calc.py",
                source_code=_RENOVATED_CLEAN_CALC_SRC,
                rationale="Synthesized double_cents.",
            ),
        ),
        summary="Synthesized double_cents.",
    ).model_dump_json()


def _elicit_resume_router(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    tool = info.output_tools[0]
    title = str(tool.parameters_json_schema.get("title", ""))
    payload = _greenfield_stage_payload(title, _prompt_text(messages))
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool.name, args=payload, tool_call_id="call_elicit")]
    )


async def test_in_process_elicit_to_frame_callback_certifies_occupancy(tmp_path: Path) -> None:
    _write_utf8(tmp_path / "pyproject.toml", _PYPROJECT_TOML)
    _write_utf8(tmp_path / "src" / "sample_pkg" / "__init__.py", "")
    seen_ambiguities: list[tuple[str, ...]] = []

    def _authority_callback(draft: EngineeringMandate) -> Sequence[str]:
        seen_ambiguities.append(draft.open_ambiguities)
        return ("Use PositiveInt bounds from jezz.guards for cent amounts.",)

    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(FunctionModel(_elicit_resume_router)),
        gate_runner=InProcessGateRunner(),
        elicitation_callback=_authority_callback,
    )
    result = await orchestrator.execute_prompt(tmp_path, "Build cent doubler")

    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert seen_ambiguities == [("Clarify overflow ceiling for cent amounts.",)]
    assert result.graph_verification.is_valid, result.graph_verification.violations
    edge_pairs = {(e.source, e.target) for e in result.induced_graph.edges}
    assert ("frame", "elicit") in edge_pairs
    assert ("elicit", "reframe_resolved") in edge_pairs
    assert ("reframe_resolved", "bind_codes") in edge_pairs
    assert result.dossier_audit is not None and result.dossier_audit.is_occupancy_sealed


def test_cli_elicit_ticket_persistence_and_resume_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = tmp_path / "ws"
    _write_utf8(repo_dir / "pyproject.toml", _PYPROJECT_TOML)
    _write_utf8(repo_dir / "src" / "sample_pkg" / "__init__.py", "")
    ticket_file = tmp_path / "elicit_ticket.json"
    monkeypatch.setattr(
        cli_mod, "resolve_cli_model", lambda _name: FunctionModel(_elicit_resume_router)
    )

    # Step 1: Halt at BHV-02 ELICIT and write ElicitationTicket without mutating workspace
    out_halt = io.StringIO()
    code_halt = main(
        [
            "run",
            str(repo_dir),
            "--model",
            "test",
            "--prompt",
            "Build cent doubler",
            "--gate-runner",
            "in-process",
            "--write-elicitation",
            str(ticket_file),
        ],
        stdout=out_halt,
    )
    assert code_halt == 1
    assert "HALTED_FOR_ELICITATION" in out_halt.getvalue()
    assert ticket_file.is_file()
    assert not (repo_dir / "src" / "sample_pkg" / "calc.py").exists()

    # Step 2: Missing --clarification on --resume-elicitation fails cleanly with exit code 2
    err_missing = io.StringIO()
    code_missing = main(
        [
            "run",
            str(repo_dir),
            "--model",
            "test",
            "--resume-elicitation",
            str(ticket_file),
            "--gate-runner",
            "in-process",
        ],
        stderr=err_missing,
    )
    assert code_missing == 2
    assert "--clarification" in err_missing.getvalue()

    # Step 3: Resume with authoritative --clarification -> OCCUPANCY_CERTIFIED (exit 0)
    out_resumed = io.StringIO()
    code_resumed = main(
        [
            "run",
            str(repo_dir),
            "--model",
            "test",
            "--resume-elicitation",
            str(ticket_file),
            "--clarification",
            "Use PositiveInt bounds for cent inputs.",
            "--gate-runner",
            "in-process",
            "--write-dossier",
            "artifacts/dossier.json",
        ],
        stdout=out_resumed,
    )
    assert code_resumed == 0
    assert "OCCUPANCY_CERTIFIED" in out_resumed.getvalue()

    out_audit = io.StringIO()
    assert (
        main(
            ["audit", str(repo_dir / "artifacts" / "dossier.json"), "--repo", str(repo_dir)],
            stdout=out_audit,
        )
        == 0
    )


@settings(max_examples=30, deadline=None)
@given(terminal_kind=st.sampled_from(list(BehaviorKind)))
def test_elicitation_ticket_enforces_elicit_frontier_property(
    terminal_kind: BehaviorKind,
) -> None:
    trace = (
        ExecutionStep(step_id="frame", kind=BehaviorKind.FRAME, summary="Initial frame"),
        ExecutionStep(step_id="tail_step", kind=terminal_kind, summary="Frontier step"),
    )
    ambiguous_mandate = _build_mandate(open_ambiguities=("Pending question?",))
    if terminal_kind == BehaviorKind.ELICIT:
        ticket = ElicitationTicket(mandate=ambiguous_mandate, prior_trace=trace)
        assert ticket.prior_trace[-1].kind == BehaviorKind.ELICIT
        return
    with pytest.raises(ValidationError):
        ElicitationTicket(mandate=ambiguous_mandate, prior_trace=trace)


def _renovate_router(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    tool = info.output_tools[0]
    title = str(tool.parameters_json_schema.get("title", ""))
    blob = _prompt_text(messages)
    if title == "EpistemicObservation":
        payload = EpistemicObservation(
            inspected_files=("src/sample_pkg/calc.py",),
            discovered_symbols=("double_cents",),
            summary="Observed legacy calc.py with JBC violations.",
        ).model_dump_json()
    elif title == "InstrumentationSuiteBundle":
        payload = InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_multi.py",
                    source_code=_CLEAN_MULTI_TEST_SRC,
                    rationale="Locked behavioral characterization suite.",
                ),
            ),
            covered_invariant_ids=("INV-001", "INV-002"),
            summary="Instrumented multi-module suite.",
        ).model_dump_json()
    else:
        payload = _build_refactor_or_hyp_payload(title, blob)
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool.name, args=payload, tool_call_id="call_renovate")]
    )


def _build_refactor_or_hyp_payload(title: str, blob: str) -> str:
    if title == "RemediationHypothesis":
        return RemediationHypothesis(
            root_causes=("Dynamic check rejected wrong multiplier.",),
            cited_code_ids=("JBC-VRF-005",),
            remediation_plan="Restore exact amount * 2 postcondition.",
        ).model_dump_json()
    if "Active Slice Scope: fee_scope" in blob:
        return RefactorPatchBundle(
            refactored_patches=(
                FilePatchArtifact(
                    file_path="src/sample_pkg/fee.py",
                    source_code=_RENOVATED_CLEAN_FEE_SRC,
                    rationale="Renovated add_flat_fee to JBC v1.1.",
                ),
            ),
            preserved_symbols=("add_flat_fee",),
            summary="Renovated fee.py.",
        ).model_dump_json()
    return RefactorPatchBundle(
        refactored_patches=(
            FilePatchArtifact(
                file_path="src/sample_pkg/calc.py",
                source_code=_RENOVATED_CLEAN_CALC_SRC,
                rationale="Renovated double_cents to JBC v1.1.",
            ),
        ),
        preserved_symbols=("double_cents",),
        summary="Renovated calc.py.",
    ).model_dump_json()


def test_cli_renovate_remediates_red_tag_brownfield_module_via_bhv17(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_utf8(tmp_path / "pyproject.toml", _PYPROJECT_TOML)
    _write_utf8(tmp_path / "src" / "sample_pkg" / "__init__.py", "")
    _write_utf8(tmp_path / "src" / "sample_pkg" / "calc.py", _LEGACY_DIRTY_CALC_SRC)
    _write_utf8(tmp_path / "tests" / "test_calc.py", _CLEAN_CALC_TEST_SRC)

    # Confirm workspace starts RED_TAG before renovation
    inspector = BuildingInspector.from_workspace(tmp_path)
    pre_report = inspector.inspect_rough_in(load_workspace_units(tmp_path))
    assert pre_report.ruling == OccupancyRuling.RED_TAG

    # Authorize ONLY src/sample_pkg/calc.py (tests/test_calc.py stays locked read-only)
    mandate = EngineeringMandate(
        mandate_id="MND-RENOVATE-CALC",
        title="Renovate legacy calc module",
        objective="Bring calc.py to zero-variance JBC v1.1 occupancy preserving behavior.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="double_cents(amount) == amount * 2 for positive cents.",
                verification_symbol="test_double_cents_preserves_behavior",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="calc_refactor_scope",
                owned_files=frozenset({"src/sample_pkg/calc.py"}),
                contract_symbols=("double_cents",),
            ),
        ),
    )
    mandate_file = tmp_path / "renovate_mandate.json"
    _write_utf8(mandate_file, mandate.model_dump_json(indent=2))
    monkeypatch.setattr(cli_mod, "resolve_cli_model", lambda _name: FunctionModel(_renovate_router))

    out_ren = io.StringIO()
    exit_ren = main(
        [
            "renovate",
            str(tmp_path),
            "--model",
            "test",
            "--mandate",
            str(mandate_file),
            "--gate-runner",
            "in-process",
            "--write-dossier",
            "artifacts/renovate_dossier.json",
        ],
        stdout=out_ren,
    )
    assert exit_ren == 0, out_ren.getvalue()
    assert "`RENOVATE`" in out_ren.getvalue()
    assert "`OCCUPANCY_CERTIFIED`" in out_ren.getvalue()

    post_report = inspector.inspect_rough_in(load_workspace_units(tmp_path))
    assert post_report.ruling == OccupancyRuling.APPROVED
    locked_test = ContainedPath.from_parts(tmp_path, "tests/test_calc.py")
    assert read_bounded_text(locked_test) == _CLEAN_CALC_TEST_SRC


async def test_multi_scope_brownfield_renovation_runs_instrument_delegate_reconcile(
    tmp_path: Path,
) -> None:
    _write_utf8(tmp_path / "pyproject.toml", _PYPROJECT_TOML)
    _write_utf8(tmp_path / "src" / "sample_pkg" / "__init__.py", "")
    _write_utf8(tmp_path / "src" / "sample_pkg" / "calc.py", _LEGACY_DIRTY_CALC_SRC)
    _write_utf8(
        tmp_path / "src" / "sample_pkg" / "fee.py",
        "def add_flat_fee(fee: int) -> int:\n    return fee + 10\n",
    )
    mandate = EngineeringMandate(
        mandate_id="MND-RENOVATE-MULTI",
        title="Multi-scope brownfield renovation",
        objective="Instrument characterization tests and refactor calc.py and fee.py in parallel.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="double_cents(x) == 2 * x",
                verification_symbol="test_double_cents_preserves_behavior",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="add_flat_fee(x) == x + 10",
                verification_symbol="test_add_flat_fee_preserves_behavior",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="calc_scope",
                owned_files=frozenset({"src/sample_pkg/calc.py", "tests/test_multi.py"}),
                contract_symbols=("double_cents",),
            ),
            ModuleWriteScope(
                scope_id="fee_scope",
                owned_files=frozenset({"src/sample_pkg/fee.py"}),
                contract_symbols=("add_flat_fee",),
            ),
        ),
    )
    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(FunctionModel(_renovate_router)),
        gate_runner=InProcessGateRunner(),
    )
    result = await orchestrator.renovate_mandate(tmp_path, mandate)

    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert result.workflow_mode == PceWorkflowMode.RENOVATE
    assert result.graph_verification.is_valid
    executed_kinds = result.induced_graph.distinct_kinds()
    assert BehaviorKind.INSTRUMENT in executed_kinds
    assert BehaviorKind.DELEGATE in executed_kinds
    assert BehaviorKind.RECONCILE in executed_kinds
    assert BehaviorKind.SPECIFY not in executed_kinds
    assert result.dossier_audit is not None and result.dossier_audit.is_occupancy_sealed


def test_mandate_rejects_pytest_nodeid_symbols_and_test_only_frame_scopes() -> None:
    with pytest.raises(ValidationError, match="valid Python identifier"):
        DomainInvariant(
            invariant_id="INV-001",
            statement="Quota balance remains non-negative.",
            verification_symbol="tests/test_limiter.py:test_quota",
        )

    with pytest.raises(ValidationError, match="valid Python identifier"):
        ModuleWriteScope(
            scope_id="bad_scope",
            owned_files=frozenset({"src/quota_pkg/limiter.py"}),
            contract_symbols=("src/quota_pkg/limiter.py:QuotaLimiter",),
        )
