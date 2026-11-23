"""Track 6 verification suite: 21/21 runtime behavior coverage, provisioning, and memory."""

import sys
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents import (
    PCE_BEHAVIOR_NODE_MAP,
    ROLE_TIER_MAP,
    WORKSPACE_MEMORY_REL_PATH,
    ConstructionPatchBundle,
    ConsultationBrief,
    ContractSpecificationBundle,
    CuratedMemoryDigest,
    EnvironmentObservation,
    EpistemicObservation,
    FilePatchArtifact,
    InProcessGateRunner,
    InProcessProvisionRunner,
    InstrumentationSuiteBundle,
    JezzAgentDeps,
    PceRunStatus,
    PceRuntimeBudget,
    PceWorkflowOrchestrator,
    ProvisionPlan,
    RemediationHypothesis,
    SpecialistRoleSlot,
    SpecialistTier,
    WorkspaceSandbox,
    build_curate_memory_agent,
    build_pce_workflow_graph,
    build_provision_agent,
    load_workspace_memory,
    run_bounded_agent,
    uniform_model_suite,
    validate_curated_digest_invariants,
    validate_runtime_node_coverage,
    verify_runtime_importable_patches,
)
from jezz.behaviors import (
    FIBONACCI_BASIS_CARDINALITY,
    BehaviorKind,
    ExecutionStep,
    trace_to_behavior_graph,
    verify_behavior_graph,
)
from jezz.codes import BuildingInspector, CodePermit
from jezz.guards import ContainedPath, read_bounded_text
from jezz.methodology import (
    DependencyRequirement,
    DependencyScope,
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
)

_BASE_PYPROJECT = textwrap.dedent(
    """\
    [project]
    name = "track6-demo"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0", "pydantic>=2.14.4"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)

_CONTRACT_SRC = textwrap.dedent(
    """\
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, PositiveInt

    class QuotaSpec(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        limit_units: PositiveInt
    """
)

_TEST_SRC = textwrap.dedent(
    """\
    from track6_demo.quota import QuotaSpec, remaining_units

    def test_remaining_units_exact() -> None:
        assert remaining_units(QuotaSpec(limit_units=10), 4) == 6
    """
)

_CLEAN_MCP_IMPL_SRC = textwrap.dedent(
    """\
    import icontract
    import mcp
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

    class QuotaSpec(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        limit_units: PositiveInt

    @icontract.require(lambda spec, used: used <= spec.limit_units)
    @icontract.ensure(lambda spec, used, result: result + used == spec.limit_units)
    @jezz_callable
    def remaining_units(spec: QuotaSpec, used: NonNegativeInt) -> NonNegativeInt:
        _ = mcp.__name__
        return spec.limit_units - used
    """
)

_CLEAN_PLAIN_IMPL_SRC = textwrap.dedent(
    """\
    import icontract
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

    class QuotaSpec(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        limit_units: PositiveInt

    @icontract.require(lambda spec, used: used <= spec.limit_units)
    @icontract.ensure(lambda spec, used, result: result + used == spec.limit_units)
    @jezz_callable
    def remaining_units(spec: QuotaSpec, used: NonNegativeInt) -> NonNegativeInt:
        return spec.limit_units - used
    """
)

_RED_TAG_IMPL_SRC = textwrap.dedent(
    """\
    from pydantic import BaseModel
    from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

    class QuotaSpec(BaseModel):
        model_config = STRICT_FROZEN_CONFIG
        limit_units: PositiveInt

    @jezz_callable
    def remaining_units(spec: QuotaSpec, used: NonNegativeInt) -> NonNegativeInt:
        try:
            return spec.limit_units - used
        except Exception:
            pass
        return 0
    """
)


def _write_contained_file(root: Path, rel_path: str, content: str) -> ContainedPath:
    contained = ContainedPath.from_parts(root.resolve(), rel_path)
    contained.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with contained.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
    return contained


def _structured_tool_response(info: AgentInfo, payload: BaseModel) -> ModelResponse:
    raw_json = payload.model_dump_json()
    if info.output_tools:
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=raw_json)]
        )
    return ModelResponse(parts=[TextPart(content=raw_json)])


def _quota_mandate(
    *,
    mandate_id: str = "MND-TRACK6-001",
    deps: tuple[DependencyRequirement, ...] = (),
    consult_targets: tuple[str, ...] = (),
) -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id=mandate_id,
        title="Deterministic integer quota subtracter",
        objective="Compute exact remaining integer quota units under JBC v1.2 contracts.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Remaining quota units plus used units equals limit_units.",
                verification_symbol="test_remaining_units_exact",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="quota_scope",
                owned_files=frozenset({"src/track6_demo/quota.py", "tests/test_quota.py"}),
                contract_symbols=("QuotaSpec", "remaining_units", "test_remaining_units_exact"),
            ),
        ),
        required_dependencies=deps,
        consultation_targets=consult_targets,
    )


# ---------------------------------------------------------------------------
# 1. Exact 21/21 BehaviorKind Runtime Node Bijection & Reachability
# ---------------------------------------------------------------------------


def test_full_21_behavior_runtime_node_bijection_and_graph_reachability() -> None:
    node_map = validate_runtime_node_coverage()
    assert len(node_map) == FIBONACCI_BASIS_CARDINALITY == 21
    assert set(node_map) == set(BehaviorKind)

    compiled_graph = build_pce_workflow_graph()
    expected_classes = {cls for group in PCE_BEHAVIOR_NODE_MAP.values() for cls in group}
    assert len(expected_classes) == 22
    assert {cls.get_node_id() for cls in expected_classes} <= set(compiled_graph.nodes)

    tier_counts = {
        tier: sum(1 for mapped in ROLE_TIER_MAP.values() if mapped == tier)
        for tier in SpecialistTier
    }
    assert len(SpecialistRoleSlot) == 12
    assert tier_counts == {
        SpecialistTier.ARCHITECT: 4,
        SpecialistTier.BUILDER: 4,
        SpecialistTier.INSPECTOR: 4,
    }


def test_composite_trace_spanning_all_21_behaviors_passes_kildall_verifier() -> None:
    steps = (
        ExecutionStep(step_id="frame", kind=BehaviorKind.FRAME, summary="Frame initial draft"),
        ExecutionStep(step_id="elicit", kind=BehaviorKind.ELICIT, summary="Request clarification"),
        ExecutionStep(
            step_id="reframe_resolved", kind=BehaviorKind.FRAME, summary="Resolve mandate"
        ),
        ExecutionStep(step_id="bind_codes", kind=BehaviorKind.BIND_CODES, summary="Bind JBC v1.2"),
        ExecutionStep(
            step_id="inspect_env", kind=BehaviorKind.INSPECT_ENV, summary="Sense substrate"
        ),
        ExecutionStep(
            step_id="inspect_code", kind=BehaviorKind.INSPECT_CODE, summary="Inspect AST"
        ),
        ExecutionStep(step_id="consult", kind=BehaviorKind.CONSULT, summary="Cite JBC clauses"),
        ExecutionStep(step_id="decompose", kind=BehaviorKind.DECOMPOSE, summary="Partition DAG"),
        ExecutionStep(
            step_id="checkpoint_contract", kind=BehaviorKind.CHECKPOINT, summary="Snap baseline"
        ),
        ExecutionStep(
            step_id="provision", kind=BehaviorKind.PROVISION, summary="Provision manifest"
        ),
        ExecutionStep(step_id="specify", kind=BehaviorKind.SPECIFY, summary="Specify schemas"),
        ExecutionStep(
            step_id="instrument", kind=BehaviorKind.INSTRUMENT, summary="Author Hypothesis suite"
        ),
        ExecutionStep(
            step_id="checkpoint_impl", kind=BehaviorKind.CHECKPOINT, summary="Snap contracts"
        ),
        ExecutionStep(step_id="delegate", kind=BehaviorKind.DELEGATE, summary="Dispatch scopes"),
        ExecutionStep(step_id="reconcile", kind=BehaviorKind.RECONCILE, summary="Merge disjoint"),
        ExecutionStep(
            step_id="verify_static", kind=BehaviorKind.VERIFY_STATIC, summary="Rough-in check"
        ),
        ExecutionStep(step_id="revert", kind=BehaviorKind.REVERT, summary="Rollback RED_TAG"),
        ExecutionStep(
            step_id="hypothesize", kind=BehaviorKind.HYPOTHESIZE, summary="Diagnose root cause"
        ),
        ExecutionStep(
            step_id="curate_memory", kind=BehaviorKind.CURATE_MEMORY, summary="Compact memory"
        ),
        ExecutionStep(
            step_id="checkpoint_impl", kind=BehaviorKind.CHECKPOINT, summary="Re-snap impl"
        ),
        ExecutionStep(
            step_id="synthesize", kind=BehaviorKind.SYNTHESIZE, summary="Synthesize module"
        ),
        ExecutionStep(step_id="refactor", kind=BehaviorKind.REFACTOR, summary="Refine structure"),
        ExecutionStep(
            step_id="verify_static", kind=BehaviorKind.VERIFY_STATIC, summary="Rough-in pass"
        ),
        ExecutionStep(
            step_id="verify_dynamic", kind=BehaviorKind.VERIFY_DYNAMIC, summary="Dynamic pass"
        ),
        ExecutionStep(step_id="certify", kind=BehaviorKind.CERTIFY, summary="Seal occupancy"),
    )
    graph = trace_to_behavior_graph(steps, name="full_21_basis_trace")
    verdict = verify_behavior_graph(graph)
    assert len(graph.distinct_kinds()) == FIBONACCI_BASIS_CARDINALITY == 21
    assert verdict.is_valid, verdict.violations


# ---------------------------------------------------------------------------
# 2. DependencyRequirement Validation, BHV-13 PROVISION, & Manifest Rollback
# ---------------------------------------------------------------------------


@given(
    pkg=st.from_regex(r"[a-z][a-z0-9_-]{1,16}[a-z0-9]", fullmatch=True),
    major=st.integers(min_value=0, max_value=20),
)
def test_property_dependency_requirement_normalizes_pep508_specs(pkg: str, major: int) -> None:
    req = DependencyRequirement(package=pkg, version_constraint=f">= {major}.0")
    assert req.pep508_spec() == f"{pkg}>={major}.0"
    assert req.effective_import_root().isidentifier()


def test_dependency_requirement_rejects_flags_urls_traversal_and_duplicates() -> None:
    bad_packages = (
        "--index-url=https://evil.example",
        "-e",
        "../local_pkg",
        "pkg;rm -rf /",
        "pkg @ https://evil.example/pkg.whl",
    )
    for bad_pkg in bad_packages:
        with pytest.raises(ValidationError):
            DependencyRequirement(package=bad_pkg)

    with pytest.raises(ValidationError):
        DependencyRequirement(package="pyyaml", version_constraint="; sys_platform == 'darwin'")

    with pytest.raises(ValidationError):
        DependencyRequirement(package="pyyaml", import_root="not-an-identifier")

    dup = DependencyRequirement(package="PyYAML", scope=DependencyScope.RUNTIME)
    with pytest.raises(ValidationError, match="Duplicate DependencyRequirement"):
        _quota_mandate(
            deps=(
                DependencyRequirement(package="pyyaml", scope=DependencyScope.RUNTIME),
                dup,
            )
        )


def _dispatch_full_track6_payload(
    props: dict[str, object],
    impl_src: str,
    seen_prompts: list[str],
    prompt_blob: str,
) -> BaseModel:
    seen_prompts.append(prompt_blob)
    if "python_version" in props:
        return EnvironmentObservation(
            python_version="3.14.0",
            declared_runtime_roots=("icontract", "jezz", "pydantic"),
            missing_dependencies=("mcp>=2.3.0",),
            summary="Detected missing runtime package mcp>=2.3.0.",
        )
    if "citations" in props:
        return ConsultationBrief(
            citations=(
                "JBC-SEC-005 (Filesystem/Network Sinks)",
                "jezz.guards.STRICT_FROZEN_CONFIG",
            ),
            normative_constraints=("Enforce STRICT_FROZEN_CONFIG and @jezz_callable.",),
            summary="Anchored JBC-SEC-005 and jezz.guards API constraints.",
        )
    if "approved_specs" in props:
        return ProvisionPlan(
            approved_specs=("mcp>=2.3.0",),
            dev_specs=(),
            rationale="Provision declared mcp>=2.3.0 runtime dependency.",
        )
    if "inspected_files" in props:
        return EpistemicObservation(
            inspected_files=(),
            discovered_symbols=(),
            summary="Inspected greenfield track6_demo workspace.",
        )
    if "contract_patches" in props:
        return ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/track6_demo/quota.py",
                    source_code=_CONTRACT_SRC,
                    rationale="QuotaSpec frozen contract.",
                ),
            ),
            declared_symbols=("QuotaSpec",),
            summary="Authored QuotaSpec contract.",
        )
    if "test_patches" in props:
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_quota.py",
                    source_code=_TEST_SRC,
                    rationale="Falsification test.",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Authored quota test suite.",
        )
    return ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/track6_demo/quota.py",
                source_code=impl_src,
                rationale="Synthesized remaining_units importing provisioned mcp.",
            ),
        ),
        summary="Synthesized quota implementation.",
    )


async def test_provision_consult_and_inspector_refresh_certifies_vrf_003(
    tmp_path: Path,
) -> None:
    _write_contained_file(tmp_path, "pyproject.toml", _BASE_PYPROJECT)
    _write_contained_file(tmp_path, "src/track6_demo/__init__.py", "")
    mandate = _quota_mandate(
        deps=(
            DependencyRequirement(
                package="mcp",
                version_constraint=">=2.3.0",
                import_root="mcp",
            ),
        ),
        consult_targets=("JBC-SEC-005", "jezz.guards"),
    )
    seen_prompts: list[str] = []

    def _router(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        raw_props = out_tool.parameters_json_schema.get("properties", {})
        props = raw_props if isinstance(raw_props, dict) else {}
        payload = _dispatch_full_track6_payload(
            props, _CLEAN_MCP_IMPL_SRC, seen_prompts, str(messages)
        )
        return _structured_tool_response(info, payload)

    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(FunctionModel(_router)),
        gate_runner=InProcessGateRunner(),
        provision_runner=InProcessProvisionRunner(),
    )
    result = await orchestrator.execute_mandate(tmp_path, mandate)

    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert {
        BehaviorKind.INSPECT_ENV,
        BehaviorKind.CONSULT,
        BehaviorKind.PROVISION,
    } <= result.induced_graph.distinct_kinds()
    assert result.graph_verification.is_valid
    assert result.dossier_audit is not None and result.dossier_audit.is_occupancy_sealed
    updated_toml = read_bounded_text(ContainedPath.from_parts(tmp_path, "pyproject.toml"))
    assert '"mcp>=2.3.0"' in updated_toml
    assert any("Normative Consultation Constraints" in p for p in seen_prompts)


async def test_provision_agent_blocks_unpermitted_packages_and_revert_restores_manifests(
    tmp_path: Path,
) -> None:
    _write_contained_file(tmp_path, "pyproject.toml", _BASE_PYPROJECT)
    _write_contained_file(tmp_path, "src/track6_demo/__init__.py", "")
    mandate = _quota_mandate(
        deps=(
            DependencyRequirement(
                package="mcp",
                version_constraint=">=2.3.0",
                import_root="mcp",
            ),
        )
    )
    prov_deps = JezzAgentDeps(
        sandbox=WorkspaceSandbox(
            tmp_path,
            mandate.authorized_files(),
            authorized_manifests=mandate.authorized_manifest_files(),
        ),
        inspector=BuildingInspector.from_workspace(tmp_path),
        permit=CodePermit.full_book(permit_id="PERMIT-PROV-GUARD"),
        mandate=mandate,
    )
    prov_calls = 0

    def _rogue_then_clean_prov(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        nonlocal prov_calls
        prov_calls += 1
        specs = ("unpermitted-pkg>=9.9",) if prov_calls == 1 else ("mcp>=2.3.0",)
        return _structured_tool_response(
            info,
            ProvisionPlan(approved_specs=specs, rationale="Provision attempt."),
        )

    plan, tel = await run_bounded_agent(
        build_provision_agent(FunctionModel(_rogue_then_clean_prov)),
        behavior_kind=BehaviorKind.PROVISION,
        prompt="Validate declared dependencies",
        deps=prov_deps,
        budget=PceRuntimeBudget(),
    )
    assert plan.approved_specs == ("mcp>=2.3.0",)
    assert tel.requests == 2

    def _failing_router(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        raw_props = out_tool.parameters_json_schema.get("properties", {})
        props = raw_props if isinstance(raw_props, dict) else {}
        if "root_causes" in props:
            return _structured_tool_response(
                info,
                RemediationHypothesis(
                    root_causes=("Silent except Exception: pass.",),
                    cited_code_ids=("JBC-FLT-001",),
                    remediation_plan="Remove silent exception handler.",
                ),
            )
        payload = _dispatch_full_track6_payload(props, _RED_TAG_IMPL_SRC, [], str(messages))
        return _structured_tool_response(info, payload)

    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(FunctionModel(_failing_router)),
        gate_runner=InProcessGateRunner(),
        provision_runner=InProcessProvisionRunner(touch_lockfile=True),
        budget=PceRuntimeBudget(max_remediation_cycles=1),
    )
    halted = await orchestrator.execute_mandate(tmp_path, mandate)
    assert halted.status == PceRunStatus.ESCALATED_AFTER_RED_TAG
    restored_toml = read_bounded_text(ContainedPath.from_parts(tmp_path, "pyproject.toml"))
    assert restored_toml == _BASE_PYPROJECT
    assert not ContainedPath.from_parts(tmp_path, "uv.lock").resolved_path.exists()


# ---------------------------------------------------------------------------
# 3. BHV-07 CURATE_MEMORY Negation Preservation & Longitudinal .jezz/memory.json
# ---------------------------------------------------------------------------


@given(
    negations=st.lists(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_:- "
            ),
            min_size=4,
            max_size=40,
        )
        .map(str.strip)
        .filter(bool),
        min_size=1,
        max_size=6,
        unique=True,
    )
)
def test_property_curate_memory_rejects_dropped_negations(negations: list[str]) -> None:
    mandate = _quota_mandate()
    dropped = CuratedMemoryDigest(
        compacted_observations=("Quota module observation",),
        retained_invariants=("INV-001",),
        retained_falsified_paths=tuple(negations[:-1]),
        summary="Dropped one negation.",
    )
    err = validate_curated_digest_invariants(mandate, tuple(negations), dropped)
    assert err is not None and "falsified_paths" in err

    valid = CuratedMemoryDigest(
        compacted_observations=("Quota module observation",),
        retained_invariants=("INV-001",),
        retained_falsified_paths=tuple(negations),
        summary="Preserved all negations.",
    )
    assert validate_curated_digest_invariants(mandate, tuple(negations), valid) is None


async def test_curate_memory_agent_retries_on_evicted_negation(tmp_path: Path) -> None:
    mandate = _quota_mandate()
    prior_neg = ("rough_in_red_tag: JBC-FLT-001@src/track6_demo/quota.py:9",)
    deps = JezzAgentDeps(
        sandbox=WorkspaceSandbox(tmp_path, mandate.authorized_files()),
        inspector=BuildingInspector.from_workspace(tmp_path),
        permit=CodePermit.full_book(permit_id="PERMIT-CURATE-RETRY"),
        mandate=mandate,
        prior_falsified_paths=prior_neg,
    )
    calls = 0

    def _lossy_then_exact(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        nonlocal calls
        calls += 1
        retained = () if calls == 1 else prior_neg
        return _structured_tool_response(
            info,
            CuratedMemoryDigest(
                compacted_observations=("Grounded quota contract.",),
                retained_invariants=("INV-001",),
                retained_falsified_paths=retained,
                architectural_notes=("Use @icontract.require(used <= spec.limit_units).",),
                summary="Compacted observations.",
            ),
        )

    digest, tel = await run_bounded_agent(
        build_curate_memory_agent(FunctionModel(_lossy_then_exact)),
        behavior_kind=BehaviorKind.CURATE_MEMORY,
        prompt="Compact observations while keeping negations",
        deps=deps,
        budget=PceRuntimeBudget(),
    )
    assert digest.retained_falsified_paths == prior_neg
    assert tel.requests == 2


def _extract_negation_from_blob(blob: str) -> str:
    for fragment in blob.split("'"):
        if fragment.startswith("rough_in_red_tag:"):
            return fragment
    return "rough_in_red_tag: JBC-FLT-001"


def _select_longitudinal_payload(
    props: dict[str, object],
    blob: str,
    synth_attempts: list[int],
) -> BaseModel:
    if "root_causes" in props:
        return RemediationHypothesis(
            root_causes=("Silent except block violated JBC-FLT-001.",),
            cited_code_ids=("JBC-FLT-001",),
            remediation_plan="Replace try/except with @icontract.require.",
        )
    if "retained_invariants" in props:
        return CuratedMemoryDigest(
            compacted_observations=("QuotaSpec contract verified.",),
            retained_invariants=("INV-001",),
            retained_falsified_paths=(_extract_negation_from_blob(blob),),
            architectural_notes=("Keep integer quota arithmetic exception-free.",),
            summary="Compacted remediation context via memory_curator.",
        )
    if "implementation_patches" in props:
        synth_attempts.append(1)
        src = _RED_TAG_IMPL_SRC if len(synth_attempts) == 1 else _CLEAN_PLAIN_IMPL_SRC
        return ConstructionPatchBundle(
            implementation_patches=(
                FilePatchArtifact(
                    file_path="src/track6_demo/quota.py",
                    source_code=src,
                    rationale="Quota synthesis.",
                ),
            ),
            summary="Synthesized quota.",
        )
    return _dispatch_full_track6_payload(props, _CLEAN_PLAIN_IMPL_SRC, [], blob)


def _make_longitudinal_router(
    synth_attempts: list[int],
    captured_prompts: list[str],
) -> FunctionModel:
    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        blob = str(messages)
        captured_prompts.append(blob)
        out_tool = info.output_tools[0]
        raw_props = out_tool.parameters_json_schema.get("properties", {})
        props = raw_props if isinstance(raw_props, dict) else {}
        return _structured_tool_response(
            info,
            _select_longitudinal_payload(props, blob, synth_attempts),
        )

    return FunctionModel(_respond)


async def test_longitudinal_workspace_memory_persists_across_sequential_mandates(
    tmp_path: Path,
) -> None:
    _write_contained_file(tmp_path, "pyproject.toml", _BASE_PYPROJECT)
    _write_contained_file(tmp_path, "src/track6_demo/__init__.py", "")

    attempts_m1: list[int] = []
    prompts_m1: list[str] = []
    orch_m1 = PceWorkflowOrchestrator(
        uniform_model_suite(_make_longitudinal_router(attempts_m1, prompts_m1)),
        gate_runner=InProcessGateRunner(),
        budget=PceRuntimeBudget(memory_compaction_threshold=1),
    )
    res_m1 = await orch_m1.execute_mandate(tmp_path, _quota_mandate(mandate_id="MND-SEQ-001"))

    assert res_m1.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert BehaviorKind.CURATE_MEMORY in res_m1.induced_graph.distinct_kinds()
    assert any(t.specialist_role == "memory_curator" for t in res_m1.telemetry)
    assert ContainedPath.from_parts(tmp_path, WORKSPACE_MEMORY_REL_PATH).resolved_path.is_file()

    ledger_after_m1 = load_workspace_memory(tmp_path)
    assert [m.mandate_id for m in ledger_after_m1.sealed_mandates] == ["MND-SEQ-001"]
    assert "Keep integer quota arithmetic exception-free." in ledger_after_m1.architectural_notes
    assert len(ledger_after_m1.falsified_paths) == 1

    attempts_m2: list[int] = [1]
    prompts_m2: list[str] = []
    orch_m2 = PceWorkflowOrchestrator(
        uniform_model_suite(_make_longitudinal_router(attempts_m2, prompts_m2)),
        gate_runner=InProcessGateRunner(),
    )
    res_m2 = await orch_m2.execute_mandate(tmp_path, _quota_mandate(mandate_id="MND-SEQ-002"))

    assert res_m2.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert any("Sealed MND-SEQ-001" in p for p in prompts_m2)
    assert any("Keep integer quota arithmetic exception-free." in p for p in prompts_m2)
    ledger_after_m2 = load_workspace_memory(tmp_path)
    assert [m.mandate_id for m in ledger_after_m2.sealed_mandates] == ["MND-SEQ-001", "MND-SEQ-002"]


def test_workspace_venv_site_packages_discovery_and_smoke_import(tmp_path: Path) -> None:
    _write_contained_file(
        tmp_path,
        ".venv/lib/python3.14/site-packages/ext_provisioned_pkg.py",
        '"""Target-venv package."""\n\nVERSION = "1.2.3"\n',
    )
    sandbox = WorkspaceSandbox(tmp_path, frozenset({"src/track6_demo/ext_user.py"}))
    patch = FilePatchArtifact(
        file_path="src/track6_demo/ext_user.py",
        source_code=(
            "import ext_provisioned_pkg\n\n"
            "def read_ext_version() -> str:\n"
            "    return str(ext_provisioned_pkg.VERSION)\n"
        ),
        rationale="Import package installed strictly inside target workspace .venv.",
    )
    prior_path = list(sys.path)
    err = verify_runtime_importable_patches(sandbox, (patch,))
    assert err is None
    assert list(sys.path) == prior_path
    sys.modules.pop("ext_provisioned_pkg", None)
