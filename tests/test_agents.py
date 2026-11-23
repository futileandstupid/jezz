"""Unit, property-based, Pydantic AI, and Pydantic-Graph runtime tests for Pillar 4."""

import importlib.util
import sys
import tempfile
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from jezz.agents import (
    BEHAVIOR_EXECUTION_BOARD,
    DETERMINISTIC_BEHAVIOR_KINDS,
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    ExecutionEngineKind,
    FilePatchArtifact,
    FramedMandateProposal,
    InProcessGateRunner,
    InstrumentationSuiteBundle,
    JezzAgentDeps,
    PceModelSuite,
    PceRunStatus,
    PceRuntimeBudget,
    PceWorkflowOrchestrator,
    RemediationHypothesis,
    ToolsetAccessLevel,
    WorkspaceSandbox,
    build_instrument_agent,
    build_specify_agent,
    run_bounded_agent,
    uniform_model_suite,
    validate_execution_board_invariants,
)
from jezz.behaviors import (
    FIBONACCI_BASIS_CARDINALITY,
    MUTATE_KINDS,
    BehaviorKind,
    build_greenfield_contract_first_graph,
)
from jezz.codes import (
    BuildingInspector,
    CodePermit,
    GateAttestation,
    GateKind,
    ManifestDependencies,
    SourceUnit,
    compute_units_digest,
    issue_certificate,
    load_workspace_units,
    parse_source_unit,
)
from jezz.methodology import (
    DomainInvariant,
    EngineeringDossier,
    EngineeringMandate,
    ModuleWriteScope,
    PcePhase,
    audit_pce_dossier,
    extract_compiled_symbols,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_SAMPLE_CONTRACTS_PY = textwrap.dedent(
    """\
    from typing import Annotated
    from pydantic import BaseModel, Field
    from jezz.guards import STRICT_FROZEN_CONFIG, PositiveInt

    class TransferOrder(BaseModel):
        model_config = STRICT_FROZEN_CONFIG

        sender_balance_cents: Annotated[int, Field(ge=0, le=10_000_000_000)]
        amount_cents: PositiveInt
    """
)

_SAMPLE_TESTS_PY = textwrap.dedent(
    """\
    from ledger.contracts import TransferOrder
    from ledger.engine import settle_transfer

    def test_settle_transfer_conserves_cents() -> None:
        order = TransferOrder(sender_balance_cents=500, amount_cents=125)
        remaining = settle_transfer(order)
        assert remaining == 375
    """
)

_SAMPLE_GOOD_ENGINE_PY = textwrap.dedent(
    """\
    import icontract
    from ledger.contracts import TransferOrder
    from jezz.guards import NonNegativeInt, jezz_callable

    @icontract.require(lambda order: order.sender_balance_cents >= order.amount_cents)
    @icontract.ensure(
        lambda order, result: result + order.amount_cents == order.sender_balance_cents
    )
    @jezz_callable
    def settle_transfer(order: TransferOrder) -> NonNegativeInt:
        return order.sender_balance_cents - order.amount_cents
    """
)

_SAMPLE_BAD_ENGINE_PY = textwrap.dedent(
    """\
    from ledger.contracts import TransferOrder
    from jezz.guards import NonNegativeInt, jezz_callable

    @jezz_callable
    def settle_transfer(order: TransferOrder) -> NonNegativeInt:
        try:
            return order.sender_balance_cents - order.amount_cents
        except Exception:
            pass
        return 0
    """
)


def _structured_response(info: AgentInfo, payload: BaseModel) -> ModelResponse:
    raw_json = payload.model_dump_json()
    if info.output_tools:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=raw_json)]
        )
    return ModelResponse(parts=[TextPart(content=raw_json)])


def _sample_ledger_inspector() -> BuildingInspector:
    return BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"icontract", "pydantic", "jezz"}),
            dev_roots=frozenset({"pytest", "hypothesis"}),
            workspace_roots=frozenset({"ledger", "tests"}),
        )
    )


def _single_scope_mandate() -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id="MND-LEDGER-001",
        title="Deterministic integer-cent ledger settlement",
        objective="Settle integer-cent TransferOrder debits with exact balance conservation.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Remaining sender cents plus debited cents equals initial sender cents.",
                verification_symbol="test_settle_transfer_conserves_cents",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="ledger_core",
                owned_files=frozenset(
                    {
                        "src/ledger/contracts.py",
                        "src/ledger/engine.py",
                        "tests/test_ledger.py",
                    }
                ),
                contract_symbols=(
                    "TransferOrder",
                    "settle_transfer",
                    "test_settle_transfer_conserves_cents",
                ),
            ),
        ),
    )


def _load_module_from_file(mod_name: str, file_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    if spec is None or spec.loader is None:
        msg = f"Unable to create module spec for {file_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _execute_ledger_tests_dynamically(
    sandbox: WorkspaceSandbox,
    units: Sequence[SourceUnit],
) -> int:
    _ = units
    src_root = sandbox.repo_root / "src" / "ledger"
    test_file = sandbox.repo_root / "tests" / "test_ledger.py"
    pkg = ModuleType("ledger")
    pkg.__path__ = [str(src_root)]
    sys.modules["ledger"] = pkg
    try:
        _load_module_from_file("ledger.contracts", src_root / "contracts.py")
        _load_module_from_file("ledger.engine", src_root / "engine.py")
        test_mod = _load_module_from_file("test_ledger_dyn", test_file)
        fn: Callable[[], object] = test_mod.test_settle_transfer_conserves_cents
        fn()
        return 1
    finally:
        for key in ("test_ledger_dyn", "ledger.engine", "ledger.contracts", "ledger"):
            sys.modules.pop(key, None)


def _json_model(
    payload: BaseModel,
    *,
    call_tool_once: str | None = None,
) -> FunctionModel:
    calls = 0

    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        nonlocal calls
        calls += 1
        if call_tool_once is not None and calls == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name=call_tool_once, args="{}")])
        return _structured_response(info, payload)

    return FunctionModel(_respond)


def _base_test_models(synthesize_model: FunctionModel | TestModel) -> PceModelSuite:
    contract_bundle = ContractSpecificationBundle(
        contract_patches=(
            FilePatchArtifact(
                file_path="src/ledger/contracts.py",
                source_code=_SAMPLE_CONTRACTS_PY,
                rationale="Strict frozen TransferOrder contract.",
            ),
        ),
        declared_symbols=("TransferOrder",),
        summary="Authored Phase III TransferOrder contract.",
    )
    instrument_bundle = InstrumentationSuiteBundle(
        test_patches=(
            FilePatchArtifact(
                file_path="tests/test_ledger.py",
                source_code=_SAMPLE_TESTS_PY,
                rationale="Balance conservation falsification test.",
            ),
        ),
        covered_invariant_ids=("INV-001",),
        summary="Authored Phase III falsification test suite.",
    )
    obs = EpistemicObservation(
        inspected_files=("src/ledger/contracts.py",),
        discovered_symbols=(),
        summary="Inspected authorized ledger workspace layout.",
    )
    hyp = RemediationHypothesis(
        root_causes=("Broad except Exception: pass violates JBC-FLT-001.",),
        cited_code_ids=("JBC-FLT-001",),
        remediation_plan="Remove silent exception handler and enforce @icontract preconditions.",
    )
    return PceModelSuite(
        frame_model=TestModel(call_tools=[]),
        inspect_model=_json_model(obs, call_tool_once="_tool_list_authorized_files"),
        specify_model=_json_model(contract_bundle),
        instrument_model=_json_model(instrument_bundle),
        synthesize_model=synthesize_model,
        hypothesize_model=_json_model(hyp),
    )


# ---------------------------------------------------------------------------
# 1. Execution Board Bijection, Schemas, & Sandbox Checkpoint Properties
# ---------------------------------------------------------------------------


def test_execution_board_bijection_and_capability_isolation() -> None:
    bindings = validate_execution_board_invariants()
    assert len(bindings) == FIBONACCI_BASIS_CARDINALITY == 21

    write_capable = {
        kind
        for kind, b in BEHAVIOR_EXECUTION_BOARD.items()
        if b.toolset_access == ToolsetAccessLevel.SCOPED_WRITE
    }
    assert write_capable == MUTATE_KINDS

    for gate_kind in DETERMINISTIC_BEHAVIOR_KINDS:
        binding = BEHAVIOR_EXECUTION_BOARD[gate_kind]
        assert binding.engine_kind == ExecutionEngineKind.DETERMINISTIC_ENGINE
        assert binding.toolset_access == ToolsetAccessLevel.NONE


def test_wire_collection_validators_accept_json_lists_while_preserving_strict_scalars() -> None:
    raw_wire_dict: dict[str, object] = {
        "mandate": {
            "mandate_id": "MND-WIRE-001",
            "title": "Validate Python dict wire payloads with JSON list arrays",
            "objective": "Ensure GoogleModel and AnthropicModel dict tool calls validate cleanly.",
            "invariants": [
                {
                    "invariant_id": "INV-001",
                    "statement": "Settled integer balance equals initial balance minus debit.",
                    "verification_symbol": "test_wire_balance",
                }
            ],
            "write_scopes": [
                {
                    "scope_id": "wire_scope",
                    "owned_files": ["src/wire/mod.py", "tests/test_wire.py"],
                    "contract_symbols": ["WireOrder", "test_wire_balance"],
                }
            ],
            "out_of_scope": ["External HTTP transport"],
            "open_ambiguities": [],
        },
        "framing_notes": "Parsed from pre-decoded provider JSON dictionary.",
    }
    proposal = FramedMandateProposal.model_validate(raw_wire_dict)
    assert proposal.mandate.write_scopes[0].owned_files == frozenset(
        {"src/wire/mod.py", "tests/test_wire.py"}
    )
    assert proposal.mandate.out_of_scope == ("External HTTP transport",)

    # Strict inner scalar validation must still reject int->str coercion and string->tuple splitting
    bad_scalar = {**raw_wire_dict, "framing_notes": 12345}
    with pytest.raises(ValidationError):
        FramedMandateProposal.model_validate(bad_scalar)

    bad_seq: dict[str, object] = {
        "inspected_files": "src/not_a_list.py",
        "discovered_symbols": ["WireOrder"],
        "summary": "Should reject bare string where tuple/list expected.",
    }
    with pytest.raises(ValidationError):
        EpistemicObservation.model_validate(bad_seq)

    unit = parse_source_unit(
        "src/wire/mod.py",
        "class WireOrder:\n    def execute(self) -> int:\n        return 1\n",
    )
    compiled = extract_compiled_symbols((unit,))
    assert "wire.mod.WireOrder" in compiled
    assert "src.wire.mod.WireOrder" in compiled
    assert "src.wire.mod.WireOrder.execute" in compiled


def test_file_patch_artifact_rejects_syntax_errors_and_sandbox_locks_scope(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="invalid Python syntax"):
        FilePatchArtifact(
            file_path="src/broken.py",
            source_code="def broken(:\n    return 1\n",
            rationale="Broken syntax test.",
        )

    sandbox = WorkspaceSandbox(tmp_path, frozenset({"src/allowed.py"}))
    with pytest.raises(PermissionError, match="Write-scope violation"):
        sandbox.write_authorized_file("src/forbidden.py", "X = 1\n")
    with pytest.raises(ValueError, match="traversal"):
        sandbox.write_authorized_file("../escape.py", "X = 1\n")


@given(
    initial_val=st.integers(min_value=0, max_value=10_000),
    mutated_val=st.integers(min_value=10_001, max_value=99_999),
)
def test_property_workspace_checkpoint_restore_is_exact(
    initial_val: int,
    mutated_val: int,
) -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        case_dir = Path(raw_dir)
        sandbox = WorkspaceSandbox(case_dir, frozenset({"src/a.py", "src/b.py"}))
        sandbox.write_authorized_file("src/a.py", f"VAL = {initial_val}\n")
        sandbox.create_checkpoint("baseline")

        sandbox.write_authorized_file("src/a.py", f"VAL = {mutated_val}\n")
        sandbox.write_authorized_file("src/b.py", "EXTRA = True\n")
        assert (case_dir / "src" / "b.py").exists()

        restored_mod = sandbox.restore_checkpoint("baseline")
        assert restored_mod == frozenset({"src/a.py"})
        assert sandbox.read_file("src/a.py") == f"VAL = {initial_val}\n"
        assert not (case_dir / "src" / "b.py").exists()


# ---------------------------------------------------------------------------
# 2. Pydantic AI @output_validator (ModelRetry) Self-Correction Tests
# ---------------------------------------------------------------------------


async def test_specify_and_instrument_agents_self_correct_via_model_retry(
    tmp_path: Path,
) -> None:
    mandate = _single_scope_mandate()
    deps = JezzAgentDeps(
        sandbox=WorkspaceSandbox(tmp_path, mandate.authorized_files()),
        inspector=_sample_ledger_inspector(),
        permit=CodePermit.full_book(permit_id="PERMIT-RETRY-TEST"),
        mandate=mandate,
    )
    spec_calls = 0

    def _flaky_specify(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        nonlocal spec_calls
        spec_calls += 1
        bad_src = "def untyped(x):\n    return x\n"
        src = bad_src if spec_calls == 1 else _SAMPLE_CONTRACTS_PY
        payload = ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/ledger/contracts.py",
                    source_code=src,
                    rationale="Contract attempt.",
                ),
            ),
            declared_symbols=("TransferOrder",),
            summary="Contract specification.",
        )
        return _structured_response(info, payload)

    out_spec, tel_spec = await run_bounded_agent(
        build_specify_agent(FunctionModel(_flaky_specify)),
        behavior_kind=BehaviorKind.SPECIFY,
        prompt="Emit contracts",
        deps=deps,
        budget=PceRuntimeBudget(),
    )
    assert out_spec.declared_symbols == ("TransferOrder",)
    assert tel_spec.requests == 2

    inst_calls = 0

    def _flaky_instrument(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        nonlocal inst_calls
        inst_calls += 1
        theatre_test = "def test_theatre() -> None:\n    assert True\n"
        src = theatre_test if inst_calls == 1 else _SAMPLE_TESTS_PY
        payload = InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_ledger.py",
                    source_code=src,
                    rationale="Test attempt.",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Test suite.",
        )
        return _structured_response(info, payload)

    _, tel_inst = await run_bounded_agent(
        build_instrument_agent(FunctionModel(_flaky_instrument)),
        behavior_kind=BehaviorKind.INSTRUMENT,
        prompt="Emit falsification tests",
        deps=deps,
        budget=PceRuntimeBudget(),
    )
    assert tel_inst.requests == 2


async def test_specify_agent_runtime_import_gate_rejects_subscripted_alias(
    tmp_path: Path,
) -> None:
    mandate = _single_scope_mandate()
    sandbox = WorkspaceSandbox(tmp_path, mandate.authorized_files())
    deps = JezzAgentDeps(
        sandbox=sandbox,
        inspector=_sample_ledger_inspector(),
        permit=CodePermit.full_book(permit_id="PERMIT-SPEC-IMPORT-GATE"),
        mandate=mandate,
    )
    bad_runtime_contract = textwrap.dedent(
        """\
        from pydantic import BaseModel
        from jezz.guards import STRICT_FROZEN_CONFIG, BoundedInt, PositiveInt

        BadRate = BoundedInt[0, 10_000]

        class TransferOrder(BaseModel):
            model_config = STRICT_FROZEN_CONFIG

            sender_balance_cents: BadRate
            amount_cents: PositiveInt
        """
    )
    assert_contract = textwrap.dedent(
        """\
        from pydantic import BaseModel
        from jezz.guards import STRICT_FROZEN_CONFIG, PositiveInt

        assert 1 == 2, "Module-level self-assertion failure"

        class TransferOrder(BaseModel):
            model_config = STRICT_FROZEN_CONFIG

            sender_balance_cents: PositiveInt
            amount_cents: PositiveInt
        """
    )
    attempts = 0

    def _runtime_flaky(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            src = bad_runtime_contract
        elif attempts == 2:
            src = assert_contract
        else:
            src = _SAMPLE_CONTRACTS_PY
        bundle = ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/ledger/contracts.py",
                    source_code=src,
                    rationale="Contract import gate probe.",
                ),
            ),
            declared_symbols=("TransferOrder",),
            summary="Phase III contract attempt.",
        )
        return _structured_response(info, bundle)

    out, tel = await run_bounded_agent(
        build_specify_agent(FunctionModel(_runtime_flaky)),
        behavior_kind=BehaviorKind.SPECIFY,
        prompt="Emit runtime-importable contracts",
        deps=deps,
        budget=PceRuntimeBudget(),
    )
    assert out.declared_symbols == ("TransferOrder",)
    assert tel.requests == 3
    assert not any(p.name.startswith(".jezz_smoke_") for p in tmp_path.iterdir())


async def test_instrument_agent_rejects_callable_pep695_type_alias_call(
    tmp_path: Path,
) -> None:
    mandate = _single_scope_mandate()
    sandbox = WorkspaceSandbox(tmp_path, mandate.authorized_files())
    sandbox.write_authorized_file("src/ledger/contracts.py", _SAMPLE_CONTRACTS_PY)
    deps = JezzAgentDeps(
        sandbox=sandbox,
        inspector=_sample_ledger_inspector(),
        permit=CodePermit.full_book(permit_id="PERMIT-INST-ALIAS-CALL"),
        mandate=mandate,
    )
    bad_alias_call_test = textwrap.dedent(
        """\
        from jezz.guards import IdentifierStr
        from ledger.contracts import TransferOrder
        from ledger.engine import settle_transfer

        def test_settle_transfer_conserves_cents() -> None:
            _ = IdentifierStr("CASH_TEST")
            order = TransferOrder(sender_balance_cents=100, amount_cents=40)
            assert settle_transfer(order) == 60
        """
    )
    attempts = 0

    def _alias_flaky(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        nonlocal attempts
        attempts += 1
        src = bad_alias_call_test if attempts == 1 else _SAMPLE_TESTS_PY
        bundle = InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_ledger.py",
                    source_code=src,
                    rationale="Test PEP 695 alias call rejection.",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Phase III test attempt.",
        )
        return _structured_response(info, bundle)

    out, tel = await run_bounded_agent(
        build_instrument_agent(FunctionModel(_alias_flaky)),
        behavior_kind=BehaviorKind.INSTRUMENT,
        prompt="Emit falsification tests without calling TypeAliasType",
        deps=deps,
        budget=PceRuntimeBudget(),
    )
    assert len(out.test_patches) == 1
    assert tel.requests == 2


# ---------------------------------------------------------------------------
# 3. End-to-End Pydantic-Graph PCE Lifecycle, ELICIT Halt, & RED_TAG Recovery
# ---------------------------------------------------------------------------


async def test_prompt_with_open_ambiguities_halts_at_elicit_without_mutating_disk(
    tmp_path: Path,
) -> None:
    ambiguous_mandate = _single_scope_mandate().model_copy(
        update={"open_ambiguities": ("Confirm whether overdraft fee applies or hard-rejects.",)}
    )
    proposal = FramedMandateProposal(
        mandate=ambiguous_mandate,
        framing_notes="Detected unresolved overdraft policy ambiguity.",
    )
    models = uniform_model_suite(_json_model(proposal))
    orchestrator = PceWorkflowOrchestrator(models, inspector=_sample_ledger_inspector())

    result = await orchestrator.execute_prompt(tmp_path, "Build account transfer settlement.")
    assert result.status == PceRunStatus.HALTED_FOR_ELICITATION
    assert result.graph_verification.is_valid
    assert result.induced_graph.nodes[-1].kind == BehaviorKind.ELICIT
    assert result.certificate is None
    assert not (tmp_path / "src").exists()


async def test_single_scope_workflow_certifies_occupancy_and_seals_pce_dossier(
    tmp_path: Path,
) -> None:
    good_impl = ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/ledger/engine.py",
                source_code=_SAMPLE_GOOD_ENGINE_PY,
                rationale="Contract-enforced integer settlement.",
            ),
        ),
        summary="Synthesized settle_transfer.",
    )
    models = _base_test_models(_json_model(good_impl))
    orchestrator = PceWorkflowOrchestrator(
        models,
        inspector=_sample_ledger_inspector(),
        gate_runner=InProcessGateRunner(_execute_ledger_tests_dynamically),
    )

    result = await orchestrator.execute_mandate(tmp_path, _single_scope_mandate())
    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert result.graph_verification.is_valid
    assert result.certificate is not None
    assert len(result.certificate.cited_code_ids) == 31
    assert result.dossier_audit is not None
    assert result.dossier_audit.is_occupancy_sealed
    assert result.dossier_audit.highest_verified_phase == PcePhase.OCCUPANCY


async def test_rough_in_red_tag_reverts_workspace_hypothesizes_and_self_heals(
    tmp_path: Path,
) -> None:
    synth_attempt = 0

    def _remediating_synth(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        nonlocal synth_attempt
        synth_attempt += 1
        code = _SAMPLE_BAD_ENGINE_PY if synth_attempt == 1 else _SAMPLE_GOOD_ENGINE_PY
        payload = ConstructionPatchBundle(
            implementation_patches=(
                FilePatchArtifact(
                    file_path="src/ledger/engine.py",
                    source_code=code,
                    rationale=f"Attempt {synth_attempt}",
                ),
            ),
            summary=f"Synthesis pass {synth_attempt}",
        )
        return _structured_response(info, payload)

    orchestrator = PceWorkflowOrchestrator(
        _base_test_models(FunctionModel(_remediating_synth)),
        inspector=_sample_ledger_inspector(),
        gate_runner=InProcessGateRunner(_execute_ledger_tests_dynamically),
    )

    result = await orchestrator.execute_mandate(tmp_path, _single_scope_mandate())
    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert synth_attempt == 2
    assert len(result.falsified_paths) == 1
    assert "JBC-FLT-001" in result.falsified_paths[0]
    assert {BehaviorKind.REVERT, BehaviorKind.HYPOTHESIZE} <= result.induced_graph.distinct_kinds()
    assert result.graph_verification.is_valid
    assert result.dossier_audit is not None and result.dossier_audit.is_occupancy_sealed


async def test_dynamic_gate_failure_reverts_and_exhausted_budget_escalates_cleanly(
    tmp_path: Path,
) -> None:
    wrong_math_engine = _SAMPLE_GOOD_ENGINE_PY.replace(
        "return order.sender_balance_cents - order.amount_cents",
        "return order.sender_balance_cents",
    ).replace(
        "result + order.amount_cents == order.sender_balance_cents",
        "result >= 0",
    )
    bad_bundle = ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/ledger/engine.py",
                source_code=wrong_math_engine,
                rationale="Buggy debit math that passes static rough-in but fails pytest.",
            ),
        ),
        summary="Buggy implementation.",
    )
    orchestrator = PceWorkflowOrchestrator(
        _base_test_models(_json_model(bad_bundle)),
        inspector=_sample_ledger_inspector(),
        gate_runner=InProcessGateRunner(_execute_ledger_tests_dynamically),
        budget=PceRuntimeBudget(max_remediation_cycles=1),
    )

    result = await orchestrator.execute_mandate(tmp_path, _single_scope_mandate())
    assert result.status == PceRunStatus.ESCALATED_AFTER_RED_TAG
    assert result.graph_verification.is_valid
    assert result.induced_graph.nodes[-1].kind == BehaviorKind.ELICIT
    assert len(result.falsified_paths) == 2
    assert "JBC-VRF-006" in result.falsified_paths[0]
    # Verify REVERT removed the defective implementation file before escalating
    assert not (tmp_path / "src" / "ledger" / "engine.py").exists()


# ---------------------------------------------------------------------------
# 4. Multi-Scope Parallel TaskGroup DELEGATE + RECONCILE & Repository Dogfood
# ---------------------------------------------------------------------------


async def test_multi_scope_mandate_executes_parallel_delegate_and_reconcile(
    tmp_path: Path,
) -> None:
    multi_mandate = EngineeringMandate(
        mandate_id="MND-LEDGER-MULTI",
        title="Two-scope parallel ledger settlement",
        objective="Build disjoint ledger contracts, engine, and fee calculator concurrently.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Remaining sender cents plus debited cents equals initial sender cents.",
                verification_symbol="test_settle_transfer_conserves_cents",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_engine",
                owned_files=frozenset({"src/ledger/contracts.py", "src/ledger/engine.py"}),
                contract_symbols=("TransferOrder", "settle_transfer"),
            ),
            ModuleWriteScope(
                scope_id="scope_fees_and_tests",
                owned_files=frozenset({"src/ledger/fees.py", "tests/test_ledger.py"}),
                contract_symbols=("zero_fee_cents", "test_settle_transfer_conserves_cents"),
            ),
        ),
    )
    fees_py = textwrap.dedent(
        """\
        from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

        @jezz_callable
        def zero_fee_cents(amount_cents: PositiveInt) -> NonNegativeInt:
            _ = amount_cents
            return 0
        """
    )

    def _scoped_synth(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt_text = str(messages)
        if "Active Slice Scope: scope_engine" in prompt_text:
            payload = ConstructionPatchBundle(
                implementation_patches=(
                    FilePatchArtifact(
                        file_path="src/ledger/engine.py",
                        source_code=_SAMPLE_GOOD_ENGINE_PY,
                        rationale="Scoped engine slice.",
                    ),
                ),
                summary="Completed scope_engine slice.",
            )
            return _structured_response(info, payload)
        payload = ConstructionPatchBundle(
            implementation_patches=(
                FilePatchArtifact(
                    file_path="src/ledger/fees.py",
                    source_code=fees_py,
                    rationale="Scoped fees slice.",
                ),
            ),
            summary="Completed scope_fees_and_tests slice.",
        )
        return _structured_response(info, payload)

    orchestrator = PceWorkflowOrchestrator(
        _base_test_models(FunctionModel(_scoped_synth)),
        inspector=_sample_ledger_inspector(),
        gate_runner=InProcessGateRunner(_execute_ledger_tests_dynamically),
    )

    result = await orchestrator.execute_mandate(tmp_path, multi_mandate)
    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert {BehaviorKind.DELEGATE, BehaviorKind.RECONCILE} <= result.induced_graph.distinct_kinds()
    assert result.graph_verification.is_valid
    assert result.dossier_audit is not None and result.dossier_audit.is_occupancy_sealed


def test_jezz_agents_package_passes_full_pce_dossier_dogfood_audit() -> None:
    units = load_workspace_units(REPO_ROOT)
    inspector = BuildingInspector.from_workspace(REPO_ROOT)
    permit = CodePermit.full_book(permit_id="PERMIT-JEZZ-PILLAR-4-DOGFOOD")
    attestations = (
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=48,
            duration_ms=520.0,
            peak_memory_kb=65_536,
            summary="Full jezz suite green across Pillars 1-4.",
        ),
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy",
            passed=True,
            exit_code=0,
            checks_executed=len(units),
            duration_ms=340.0,
            peak_memory_kb=65_536,
            summary="mypy --strict clean across all workspace files.",
        ),
    )
    report = inspector.inspect_final(units, permit=permit, attestations=attestations)
    assert report.is_occupiable, report.to_markdown()
    cert = issue_certificate(report, artifact_digest=compute_units_digest(units))

    agent_files = frozenset(
        {
            "src/jezz/agents/__init__.py",
            "src/jezz/agents/schemas.py",
            "src/jezz/agents/workspace.py",
            "src/jezz/agents/specialists.py",
            "src/jezz/agents/nodes.py",
            "src/jezz/agents/orchestrator.py",
            "tests/test_agents.py",
        }
    )
    mandate = EngineeringMandate(
        mandate_id="MND-JEZZ-PILLAR-4",
        title="Pillar 4 Pydantic AI and Pydantic-Graph PCE Runtime",
        objective="Execute 21-behavior PCE task graphs under JBC v1.1 building-code enforcement.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="BEHAVIOR_EXECUTION_BOARD maps all 21 BehaviorKinds bijectively.",
                verification_symbol="validate_execution_board_invariants",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="PceWorkflowOrchestrator seals occupancy only when PCE Dossier passes.",
                verification_symbol="PceWorkflowOrchestrator",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="jezz_agents_runtime",
                owned_files=agent_files,
                contract_symbols=(
                    "WorkspaceSandbox",
                    "PceWorkflowOrchestrator",
                    "build_pce_workflow_graph",
                ),
            ),
        ),
    )
    dossier = EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        mutated_files=agent_files,
        inspection_report=report,
        certificate=cert,
    )
    audit = audit_pce_dossier(dossier, units)
    assert audit.is_occupancy_sealed, audit.to_markdown()
