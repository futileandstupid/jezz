"""Verification suite for Step 2: AST Graph, Package Introspection, and Topological DAG Waves."""

import asyncio
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents import (
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    FilePatchArtifact,
    FramedMandateProposal,
    InProcessGateRunner,
    InstrumentationSuiteBundle,
    JezzAgentDeps,
    PceRunStatus,
    PceRuntimeBudget,
    PceWorkflowOrchestrator,
    ScopeDecompositionPlan,
    WorkspaceSandbox,
    build_instrument_agent,
    inspect_installed_package_api,
    run_bounded_agent,
    uniform_model_suite,
)
from jezz.behaviors import BehaviorKind
from jezz.codes import (
    BuildingInspector,
    CodePermit,
    GateAttestation,
    GateKind,
    build_workspace_ast_graph,
    extract_symbol_source_slice,
    parse_source_unit,
)
from jezz.methodology import (
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
    compute_scope_execution_waves,
)

_PYPROJECT_SRC = textwrap.dedent(
    """\
    [project]
    name = "layered-pkg"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0", "pydantic>=2.14.4"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)

_DOMAIN_SRC = textwrap.dedent(
    """\
    import icontract
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

    class InvoiceSpec(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        subtotal_cents: PositiveInt

    @icontract.ensure(lambda spec, result: result >= spec.subtotal_cents)
    @jezz_callable
    def compute_gross_cents(spec: InvoiceSpec) -> NonNegativeInt:
        return spec.subtotal_cents + 50
    """
)

_SERVICE_SRC = textwrap.dedent(
    """\
    import icontract
    from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable
    from layered_pkg.domain import InvoiceSpec, compute_gross_cents

    @icontract.ensure(lambda subtotal, result: result > subtotal)
    @jezz_callable
    def quote_invoice_total(subtotal: PositiveInt) -> NonNegativeInt:
        return compute_gross_cents(InvoiceSpec(subtotal_cents=subtotal))
    """
)

_LAYERED_TEST_SRC = textwrap.dedent(
    """\
    from layered_pkg.service import quote_invoice_total

    def test_quote_invoice_total_adds_fee() -> None:
        assert quote_invoice_total(200) == 250
    """
)


def _write_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(text)


@given(layer_sizes=st.lists(st.integers(min_value=1, max_value=4), min_size=1, max_size=5))
def test_property_compute_scope_execution_waves_preserves_dag_order(
    layer_sizes: list[int],
) -> None:
    scopes: list[ModuleWriteScope] = []
    prior_layer_ids: frozenset[str] = frozenset()
    for layer_idx, count in enumerate(layer_sizes):
        current_ids: list[str] = []
        for item_idx in range(count):
            sid = f"scope_L{layer_idx}_{item_idx}"
            scopes.append(
                ModuleWriteScope(
                    scope_id=sid,
                    owned_files=frozenset({f"src/pkg/m_{layer_idx}_{item_idx}.py"}),
                    contract_symbols=(f"fn_{layer_idx}_{item_idx}",),
                    depends_on=prior_layer_ids,
                )
            )
            current_ids.append(sid)
        prior_layer_ids = frozenset(current_ids)

    waves = compute_scope_execution_waves(tuple(scopes))
    assert len(waves) == len(layer_sizes)
    seen_before: set[str] = set()
    for wave in waves:
        wave_ids = {s.scope_id for s in wave}
        for scope in wave:
            assert scope.depends_on <= seen_before
        seen_before |= wave_ids


def test_scope_dag_rejects_self_loop_unknown_dep_and_cycle() -> None:
    with pytest.raises(ValidationError, match="self-dependency"):
        ModuleWriteScope(
            scope_id="alpha",
            owned_files=frozenset({"src/pkg/a.py"}),
            contract_symbols=("fn_a",),
            depends_on=frozenset({"alpha"}),
        )

    s_a = ModuleWriteScope(
        scope_id="a",
        owned_files=frozenset({"src/pkg/a.py"}),
        contract_symbols=("fn_a",),
        depends_on=frozenset({"missing"}),
    )
    with pytest.raises(ValueError, match="unknown scope_id"):
        compute_scope_execution_waves((s_a,))

    cyc_1 = ModuleWriteScope(
        scope_id="c1",
        owned_files=frozenset({"src/pkg/c1.py"}),
        contract_symbols=("f1",),
        depends_on=frozenset({"c2"}),
    )
    cyc_2 = ModuleWriteScope(
        scope_id="c2",
        owned_files=frozenset({"src/pkg/c2.py"}),
        contract_symbols=("f2",),
        depends_on=frozenset({"c1"}),
    )
    with pytest.raises(ValueError, match="Cyclic ModuleWriteScope"):
        compute_scope_execution_waves((cyc_1, cyc_2))


def test_workspace_ast_graph_computes_layers_callers_cycles_and_slices() -> None:
    u_dom = parse_source_unit("src/layered_pkg/domain.py", _DOMAIN_SRC)
    u_svc = parse_source_unit("src/layered_pkg/service.py", _SERVICE_SRC)
    u_tst = parse_source_unit("tests/test_service.py", _LAYERED_TEST_SRC)

    graph = build_workspace_ast_graph((u_dom, u_svc, u_tst))
    assert graph.has_import_cycles is False

    dom_mod = graph.module_neighbors("layered_pkg.domain")
    svc_mod = graph.module_neighbors("src/layered_pkg/service.py")
    tst_mod = graph.module_neighbors("tests/test_service.py")
    assert dom_mod is not None and dom_mod.topological_layer == 0
    assert svc_mod is not None and svc_mod.topological_layer == 1
    assert tst_mod is not None and tst_mod.topological_layer == 2

    callers = graph.find_callers("compute_gross_cents")
    assert [c.qualname for c in callers] == ["quote_invoice_total"]

    slice_text = extract_symbol_source_slice((u_dom, u_svc), "quote_invoice_total")
    assert "@jezz_callable" in slice_text
    assert "def quote_invoice_total(" in slice_text
    assert "class InvoiceSpec" not in slice_text

    # Verify cycle detection
    cyc_a = parse_source_unit("src/pkg/a.py", "import pkg.b\ndef fa() -> int:\n    return 1\n")
    cyc_b = parse_source_unit("src/pkg/b.py", "import pkg.a\ndef fb() -> int:\n    return 2\n")
    assert build_workspace_ast_graph((cyc_a, cyc_b)).has_import_cycles is True


def test_inspect_installed_package_api_extracts_signatures_and_blocks_unsafe_roots() -> None:
    summary = inspect_installed_package_api("pydantic", symbol_filter="BaseModel")
    assert "BaseModel" in summary.exported_classes
    assert "Module `pydantic`" in summary.to_compact_text()

    sym_fallback = inspect_installed_package_api("hypothesis.strategies.DataObject")
    assert sym_fallback.module_path == "hypothesis.strategies"
    assert "DataObject" in sym_fallback.exported_classes

    with pytest.raises(ValueError, match="Refusing side-effectful module"):
        inspect_installed_package_api("antigravity")


def test_phase_scoped_budget_and_instrument_verification_symbol_guard(tmp_path: Path) -> None:
    budget = PceRuntimeBudget.production_defaults()
    assert budget.request_limit_for(BehaviorKind.FRAME) == 18
    assert budget.request_limit_for(BehaviorKind.INSTRUMENT) == 36
    assert budget.request_limit_for(BehaviorKind.SYNTHESIZE) == 36

    _write_utf8(tmp_path / "pyproject.toml", _PYPROJECT_SRC)
    mandate = EngineeringMandate(
        mandate_id="MND-GUARD-01",
        title="Guard missing test symbol",
        objective="Ensure InstrumentNode rejects missing verification_symbol.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="quote_invoice_total(200) == 250",
                verification_symbol="test_quote_invoice_total_adds_fee",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="svc_scope",
                owned_files=frozenset({"src/layered_pkg/service.py", "tests/test_service.py"}),
                contract_symbols=("quote_invoice_total", "test_quote_invoice_total_adds_fee"),
            ),
        ),
    )
    wrong_test = "def test_wrong_function_name() -> None:\n    assert 1 + 1 == 2\n"
    bad_bundle = InstrumentationSuiteBundle(
        test_patches=(
            FilePatchArtifact(
                file_path="tests/test_service.py",
                source_code=wrong_test,
                rationale="Wrong symbol name.",
            ),
        ),
        covered_invariant_ids=("INV-001",),
        summary="Wrong test name.",
    )
    agent = build_instrument_agent(
        FunctionModel(
            lambda _m, info: ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=info.output_tools[0].name,
                        args=bad_bundle.model_dump_json(),
                    )
                ]
            )
        )
    )
    deps = JezzAgentDeps(
        sandbox=WorkspaceSandbox(tmp_path, mandate.authorized_files()),
        inspector=BuildingInspector.from_workspace(tmp_path),
        permit=CodePermit.full_book(permit_id="PERMIT-GUARD"),
        mandate=mandate,
    )
    with pytest.raises(UnexpectedModelBehavior):
        asyncio.run(
            run_bounded_agent(
                agent,
                behavior_kind=BehaviorKind.INSTRUMENT,
                prompt="Instrument suite.",
                deps=deps,
                budget=budget,
            )
        )


async def test_grounded_decompose_and_multi_wave_delegate_stage_upstream_slices(
    tmp_path: Path,
) -> None:
    _write_utf8(tmp_path / "pyproject.toml", _PYPROJECT_SRC)
    _write_utf8(tmp_path / "src" / "layered_pkg" / "__init__.py", "")
    observed_service_prompts: list[str] = []

    draft_mandate = EngineeringMandate(
        mandate_id="MND-WAVES-01",
        title="Two-layer invoice service",
        objective="Synthesize domain.py then service.py in topological waves.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="quote_invoice_total(200) == 250",
                verification_symbol="test_quote_invoice_total_adds_fee",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="domain_scope",
                owned_files=frozenset({"src/layered_pkg/domain.py"}),
                contract_symbols=("InvoiceSpec", "compute_gross_cents"),
            ),
            ModuleWriteScope(
                scope_id="service_scope",
                owned_files=frozenset({"src/layered_pkg/service.py", "tests/test_service.py"}),
                contract_symbols=("quote_invoice_total", "test_quote_invoice_total_adds_fee"),
            ),
        ),
    )

    def _wave_router(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        blob = "\n".join(str(p) for m in messages for p in m.parts)
        tool = info.output_tools[0]
        title = str(tool.parameters_json_schema.get("title", ""))
        raw = _wave_stage_json(title, blob, draft_mandate, observed_service_prompts)
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=raw)])

    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(FunctionModel(_wave_router)),
        gate_runner=InProcessGateRunner(),
    )
    result = await orchestrator.execute_prompt(tmp_path, "Build layered invoice quote service.")

    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert result.graph_verification.is_valid
    roles = [t.specialist_role for t in result.telemetry]
    assert "scope_architect" in roles
    # Wave 1 (service_scope) saw Wave 0's synthesized compute_gross_cents implementation inline!
    assert len(observed_service_prompts) == 1
    assert "def compute_gross_cents(" in observed_service_prompts[0]


def _wave_stage_json(
    title: str,
    blob: str,
    draft_mandate: EngineeringMandate,
    observed_service_prompts: list[str],
) -> str:
    if title == "FramedMandateProposal":
        return FramedMandateProposal(
            mandate=draft_mandate,
            framing_notes="Initial multi-scope draft.",
        ).model_dump_json()
    if title == "EpistemicObservation":
        return EpistemicObservation(
            inspected_files=(),
            discovered_symbols=(),
            summary="Inspected empty package.",
        ).model_dump_json()
    if title == "ScopeDecompositionPlan":
        return ScopeDecompositionPlan(
            write_scopes=(
                ModuleWriteScope(
                    scope_id="domain_scope",
                    owned_files=frozenset({"src/layered_pkg/domain.py"}),
                    contract_symbols=("InvoiceSpec", "compute_gross_cents"),
                ),
                ModuleWriteScope(
                    scope_id="service_scope",
                    owned_files=frozenset({"src/layered_pkg/service.py", "tests/test_service.py"}),
                    contract_symbols=("quote_invoice_total", "test_quote_invoice_total_adds_fee"),
                    depends_on=frozenset({"domain_scope"}),
                ),
            ),
            decomposition_rationale="service_scope imports domain_scope types and callables.",
        ).model_dump_json()
    if title == "ContractSpecificationBundle":
        contract_only_domain = textwrap.dedent(
            """\
            from pydantic import BaseModel
            from jezz.guards import STRICT_FROZEN_CONFIG, PositiveInt

            class InvoiceSpec(BaseModel):
                model_config = STRICT_FROZEN_CONFIG
                subtotal_cents: PositiveInt
            """
        )
        return ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/layered_pkg/domain.py",
                    source_code=contract_only_domain,
                    rationale="Contract schema.",
                ),
            ),
            declared_symbols=("InvoiceSpec",),
            summary="Specified InvoiceSpec.",
        ).model_dump_json()
    if title == "InstrumentationSuiteBundle":
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_service.py",
                    source_code=_LAYERED_TEST_SRC,
                    rationale="Falsification suite.",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Instrumented INV-001.",
        ).model_dump_json()
    if "Active Slice Scope: service_scope" in blob:
        observed_service_prompts.append(blob)
        return ConstructionPatchBundle(
            implementation_patches=(
                FilePatchArtifact(
                    file_path="src/layered_pkg/service.py",
                    source_code=_SERVICE_SRC,
                    rationale="Synthesized service layer.",
                ),
            ),
            summary="Synthesized service_scope.",
        ).model_dump_json()
    return ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/layered_pkg/domain.py",
                source_code=_DOMAIN_SRC,
                rationale="Synthesized domain layer.",
            ),
        ),
        summary="Synthesized domain_scope.",
    ).model_dump_json()


def _aborting_stage_response(info: AgentInfo) -> ModelResponse:
    tool = info.output_tools[0]
    title = str(tool.parameters_json_schema.get("title", ""))
    if title == "EpistemicObservation":
        payload = EpistemicObservation(
            inspected_files=(),
            discovered_symbols=(),
            summary="Clean baseline.",
        ).model_dump_json()
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])
    if title == "ContractSpecificationBundle":
        contract_src = (
            "from pydantic import BaseModel\n"
            "from jezz.guards import STRICT_FROZEN_CONFIG, PositiveInt\n\n"
            "class InvoiceSpec(BaseModel):\n"
            "    model_config = STRICT_FROZEN_CONFIG\n"
            "    subtotal_cents: PositiveInt\n"
        )
        payload = ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/layered_pkg/domain.py",
                    source_code=contract_src,
                    rationale="Phase III contract.",
                ),
            ),
            declared_symbols=("InvoiceSpec",),
            summary="Wrote Phase III contract.",
        ).model_dump_json()
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])
    msg = "Simulated mid-Phase-III InstrumentNode timeout."
    raise TimeoutError(msg)


async def test_orchestrator_rolls_back_workspace_on_unhandled_mid_run_exception(
    tmp_path: Path,
) -> None:
    _write_utf8(tmp_path / "pyproject.toml", _PYPROJECT_SRC)
    _write_utf8(tmp_path / "src" / "layered_pkg" / "__init__.py", "")
    mandate = EngineeringMandate(
        mandate_id="MND-ROLLBACK-01",
        title="Verify mid-run rollback",
        objective="Ensure unhandled InstrumentNode failure leaves zero dirty files on disk.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="InvoiceSpec rejects non-positive subtotal_cents.",
                verification_symbol="test_invoice_spec_bounds",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="domain_scope",
                owned_files=frozenset({"src/layered_pkg/domain.py", "tests/test_domain.py"}),
                contract_symbols=("InvoiceSpec", "test_invoice_spec_bounds"),
            ),
        ),
    )
    base_model = FunctionModel(lambda _m, info: _aborting_stage_response(info))
    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(base_model),
        gate_runner=InProcessGateRunner(),
    )
    with pytest.raises(TimeoutError, match="Simulated mid-Phase-III"):
        await orchestrator.execute_mandate(tmp_path, mandate)
    assert not (tmp_path / "src" / "layered_pkg" / "domain.py").exists()
    assert not (tmp_path / "tests" / "test_domain.py").exists()


def test_inspector_discovers_src_and_hatch_wheel_workspace_roots(tmp_path: Path) -> None:
    pyproject_mismatch = textwrap.dedent(
        """\
        [project]
        name = "company-ledger-suite"
        version = "0.1.0"
        dependencies = ["pydantic>=2.0", "jezz>=0.1.0"]

        [dependency-groups]
        dev = ["pytest>=9.0"]

        [tool.hatch.build.targets.wheel]
        packages = ["src/crucible_ledger"]
        """
    )
    _write_utf8(tmp_path / "pyproject.toml", pyproject_mismatch)
    _write_utf8(tmp_path / "src" / "crucible_ledger" / "__init__.py", "")
    inspector = BuildingInspector.from_workspace(tmp_path)
    assert "crucible_ledger" in inspector.manifest.workspace_roots

    test_unit = parse_source_unit(
        "tests/test_ledger.py",
        (
            "from crucible_ledger.domain import Entry\n\n"
            "def test_ok() -> None:\n"
            "    assert Entry is not None\n"
        ),
    )
    report = inspector.inspect_rough_in((test_unit,))
    assert report.is_occupiable


def test_vrf_005_and_006_surface_gate_summary_and_enriched_ast_signatures() -> None:
    u_dom = parse_source_unit("src/layered_pkg/domain.py", _DOMAIN_SRC)
    graph = build_workspace_ast_graph((u_dom,))
    cls_syms = graph.find_symbols("InvoiceSpec")
    fn_syms = graph.find_symbols("compute_gross_cents")
    assert len(cls_syms) == 1 and "subtotal_cents: PositiveInt" in cls_syms[0].signature_summary
    assert len(fn_syms) == 1 and "spec: InvoiceSpec" in fn_syms[0].signature_summary

    inspector = BuildingInspector()
    failed_mypy = GateAttestation(
        gate_kind=GateKind.STATIC_TYPES,
        tool_name="mypy",
        passed=False,
        exit_code=1,
        checks_executed=1,
        duration_ms=12.0,
        peak_memory_kb=512,
        summary=(
            "tests/test_domain.py:23: error: Non-overlapping equality check [comparison-overlap]"
        ),
    )
    failed_pytest = GateAttestation(
        gate_kind=GateKind.DYNAMIC_SUITE,
        tool_name="pytest",
        passed=False,
        exit_code=1,
        checks_executed=1,
        duration_ms=15.0,
        peak_memory_kb=512,
        summary="FAILED tests/test_domain.py::test_ok - AssertionError: expected 200",
    )
    final_rep = inspector.inspect_final((u_dom,), attestations=(failed_mypy, failed_pytest))
    messages = {f.code_id: f.message for f in final_rep.unwaived_violations()}
    assert "[comparison-overlap]" in messages["JBC-VRF-005"]
    assert "AssertionError: expected 200" in messages["JBC-VRF-006"]


def _parallel_inst_response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    blob = "\n".join(str(p) for m in messages for p in m.parts)
    tool = info.output_tools[0]
    title = str(tool.parameters_json_schema.get("title", ""))
    if title == "EpistemicObservation":
        payload = EpistemicObservation(
            inspected_files=(),
            discovered_symbols=(),
            summary="Clean workspace.",
        ).model_dump_json()
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])
    if title == "ContractSpecificationBundle":
        payload = ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/layered_pkg/domain.py",
                    source_code=_DOMAIN_SRC,
                    rationale="Domain contracts.",
                ),
                FilePatchArtifact(
                    file_path="src/layered_pkg/service.py",
                    source_code=_SERVICE_SRC,
                    rationale="Service contracts.",
                ),
            ),
            declared_symbols=("InvoiceSpec", "compute_gross_cents", "quote_invoice_total"),
            summary="Specified both modules.",
        ).model_dump_json()
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])
    if title == "InstrumentationSuiteBundle":
        is_dom = "Active Instrumentation Scope: domain_scope" in blob
        t_path = "tests/test_domain.py" if is_dom else "tests/test_service.py"
        t_src = (
            "from layered_pkg.domain import InvoiceSpec, compute_gross_cents\n\n"
            "def test_domain_gross() -> None:\n"
            "    assert compute_gross_cents(InvoiceSpec(subtotal_cents=100)) == 100\n"
            if is_dom
            else _LAYERED_TEST_SRC
        )
        inv_id = "INV-001" if is_dom else "INV-002"
        payload = InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(file_path=t_path, source_code=t_src, rationale="Scoped test."),
            ),
            covered_invariant_ids=(inv_id,),
            summary=f"Instrumented {t_path}.",
        ).model_dump_json()
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])
    is_svc = "Active Slice Scope: service_scope" in blob
    s_path = "src/layered_pkg/service.py" if is_svc else "src/layered_pkg/domain.py"
    s_src = _SERVICE_SRC if is_svc else _DOMAIN_SRC
    payload = ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(file_path=s_path, source_code=s_src, rationale="Scoped impl."),
        ),
        summary=f"Synthesized {s_path}.",
    ).model_dump_json()
    return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=payload)])


async def test_parallel_instrument_fan_out_across_multiple_test_scopes(tmp_path: Path) -> None:
    _write_utf8(tmp_path / "pyproject.toml", _PYPROJECT_SRC)
    _write_utf8(tmp_path / "src" / "layered_pkg" / "__init__.py", "")
    mandate = EngineeringMandate(
        mandate_id="MND-PAR-INST-01",
        title="Parallel two-scope instrumentation",
        objective="Verify InstrumentNode fans out concurrently across disjoint test-owning scopes.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="compute_gross_cents(InvoiceSpec(subtotal_cents=100)) == 100",
                verification_symbol="test_domain_gross",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="quote_invoice_total(200) == 250",
                verification_symbol="test_quote_invoice_total_adds_fee",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="domain_scope",
                owned_files=frozenset({"src/layered_pkg/domain.py", "tests/test_domain.py"}),
                contract_symbols=("InvoiceSpec", "compute_gross_cents"),
            ),
            ModuleWriteScope(
                scope_id="service_scope",
                owned_files=frozenset({"src/layered_pkg/service.py", "tests/test_service.py"}),
                contract_symbols=("quote_invoice_total",),
                depends_on=frozenset({"domain_scope"}),
            ),
        ),
    )
    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(FunctionModel(_parallel_inst_response)),
        gate_runner=InProcessGateRunner(),
    )
    result = await orchestrator.execute_mandate(tmp_path, mandate)
    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    inst_calls = [t for t in result.telemetry if t.behavior_kind == BehaviorKind.INSTRUMENT]
    assert len(inst_calls) == 2
