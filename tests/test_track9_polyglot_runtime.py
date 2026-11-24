"""Track 9: Polyglot autonomous PCE runtime, subprocess compiler gates, and provisioning."""

import json
import os
import textwrap
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents import (
    CargoSubprocessGateRunner,
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    FilePatchArtifact,
    GoSubprocessGateRunner,
    InProcessProvisionRunner,
    InstrumentationSuiteBundle,
    PceRunStatus,
    PceWorkflowOrchestrator,
    PolyglotSubprocessGateRunner,
    ProvisionPlan,
    RefactorPatchBundle,
    TypeScriptSubprocessGateRunner,
    WorkspaceSandbox,
    code_fence_for_path,
    compute_missing_dependencies,
    detect_workspace_languages,
    is_polyglot_test_path,
    refresh_inspector_after_provision,
    select_polyglot_brief_and_exemplar,
    uniform_model_suite,
)
from jezz.behaviors import BehaviorKind
from jezz.codes import (
    BuildingInspector,
    GateAttestation,
    GateKind,
    SupportedLanguage,
    discover_workspace_units,
)
from jezz.codes.rules_vrf import check_vrf_005, check_vrf_006
from jezz.methodology import (
    DependencyRequirement,
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
    PcePhase,
)


def _structured_reply(info: AgentInfo, payload: BaseModel) -> ModelResponse:
    if info.output_tools:
        tool = info.output_tools[0]
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool.name, args=payload.model_dump(mode="json"))]
        )
    return ModelResponse(parts=[TextPart(content=payload.model_dump_json())])


def _write_executable_shim(bin_dir: Path, name: str, body: str) -> Path:
    target = bin_dir / name
    target.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    target.chmod(0o755)
    return target


# ---------------------------------------------------------------------------
# 1. Multi-Language GateAttestation Conjunction Law (Hypothesis)
# ---------------------------------------------------------------------------


@given(
    static_flags=st.lists(st.booleans(), min_size=1, max_size=6),
    dyn_flags=st.lists(st.booleans(), min_size=1, max_size=6),
)
@settings(max_examples=60)
def test_vrf_005_and_006_enforce_conjunction_across_polyglot_attestations(
    static_flags: list[bool],
    dyn_flags: list[bool],
) -> None:
    atts: list[GateAttestation] = []
    for idx, ok in enumerate(static_flags):
        atts.append(
            GateAttestation(
                gate_kind=GateKind.STATIC_TYPES,
                tool_name=f"typecheck_{idx}",
                passed=ok,
                exit_code=0 if ok else 1,
                summary="ok" if ok else f"type error in lang {idx}",
            )
        )
    for idx, ok in enumerate(dyn_flags):
        atts.append(
            GateAttestation(
                gate_kind=GateKind.DYNAMIC_SUITE,
                tool_name=f"suite_{idx}",
                passed=ok,
                exit_code=0 if ok else 1,
                checks_executed=3,
                duration_ms=15.0,
                peak_memory_kb=1024,
                summary="3 passed" if ok else f"suite failed in lang {idx}",
            )
        )
    vrf5 = check_vrf_005(tuple(atts))
    vrf6 = check_vrf_006(tuple(atts))
    assert (len(vrf5) == 0) is all(static_flags)
    assert (len(vrf6) == 0) is all(dyn_flags)


# ---------------------------------------------------------------------------
# 2. Polyglot Path Predicates, Language Detection, & Dependency Provisioning
# ---------------------------------------------------------------------------


def test_polyglot_test_paths_fences_and_prompt_selection(tmp_path: Path) -> None:
    assert is_polyglot_test_path("tests/test_ledger.py")
    assert is_polyglot_test_path("tests/escrow.test.ts")
    assert is_polyglot_test_path("src/escrow.spec.tsx")
    assert is_polyglot_test_path("tests/escrow_test.rs")
    assert is_polyglot_test_path("internal/ledger/account_test.go")
    assert not is_polyglot_test_path("src/escrow.ts")
    assert not is_polyglot_test_path("src/lib.rs")
    assert not is_polyglot_test_path("internal/ledger/account.go")

    assert code_fence_for_path("src/a.py") == "python"
    assert code_fence_for_path("src/a.ts") == "typescript"
    assert code_fence_for_path("src/a.rs") == "rust"
    assert code_fence_for_path("pkg/a.go") == "go"

    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
    assert detect_workspace_languages(tmp_path) == (SupportedLanguage.RUST,)

    rs_brief, rs_ex = select_polyglot_brief_and_exemplar(
        frozenset({SupportedLanguage.RUST}),
        "PY_BRIEF",
        "PY_EX",
    )
    assert "Rust Statutory Requirements" in rs_brief
    assert "pub enum LedgerError" in rs_ex


def test_polyglot_dependency_requirements_and_in_process_provisioning(tmp_path: Path) -> None:
    npm_dep = DependencyRequirement(package="@opentelemetry/api", version_constraint="^1.9.0")
    rs_dep = DependencyRequirement(package="serde", version_constraint="1.0")
    go_dep = DependencyRequirement(
        package="github.com/go-chi/chi/v5",
        version_constraint="v5.1.0",
    )
    assert npm_dep.ecosystem_spec("typescript") == "@opentelemetry/api@^1.9.0"
    assert rs_dep.ecosystem_spec("rust") == "serde@1.0"
    assert go_dep.ecosystem_spec("go") == "github.com/go-chi/chi/v5@v5.1.0"

    for bad_pkg in ("--evil-flag", "../escape", "git+https://bad.example/repo.git", "a;rm -rf"):
        with pytest.raises(ValueError):
            DependencyRequirement(package=bad_pkg)

    ts_dir = tmp_path / "ts_ws"
    ts_dir.mkdir()
    ts_sb = WorkspaceSandbox(
        ts_dir,
        frozenset({"src/app.ts"}),
        authorized_manifests=frozenset({"package.json"}),
    )
    ts_sb.create_checkpoint("ckpt")
    prov = InProcessProvisionRunner()
    receipt = prov.provision_dependencies(ts_sb, (npm_dep,))
    assert receipt.updated_files == ("package.json",)
    insp = refresh_inspector_after_provision(
        ts_dir,
        BuildingInspector.from_workspace(ts_dir),
        (npm_dep,),
        language=SupportedLanguage.TYPESCRIPT,
    )
    assert (
        compute_missing_dependencies(
            insp.manifest,
            (npm_dep,),
            polyglot_manifest=insp.polyglot_manifest,
            language=SupportedLanguage.TYPESCRIPT,
        )
        == ()
    )
    ts_sb.restore_checkpoint("ckpt")
    assert not (ts_dir / "package.json").exists()


# ---------------------------------------------------------------------------
# 3. Real Compiler Subprocess Gates (Rust Cargo, Go, & TypeScript Node)
# ---------------------------------------------------------------------------


_CLEAN_RUST_LIB = textwrap.dedent(
    """\
    #[derive(Debug, Clone, PartialEq, Eq)]
    pub enum FeeError {
        InvalidAmount(i64),
        InvalidRateBps(i64),
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct FeeQuote {
        pub net_cents: i64,
        pub fee_cents: i64,
    }

    pub fn quote_fee(gross_cents: i64, rate_bps: i64) -> Result<FeeQuote, FeeError> {
        if gross_cents <= 0 {
            return Err(FeeError::InvalidAmount(gross_cents));
        }
        if !(0..=10_000).contains(&rate_bps) {
            return Err(FeeError::InvalidRateBps(rate_bps));
        }
        let fee_cents = (gross_cents * rate_bps) / 10_000;
        Ok(FeeQuote {
            net_cents: gross_cents - fee_cents,
            fee_cents,
        })
    }
    """
)

_CLEAN_RUST_TEST = textwrap.dedent(
    """\
    use fee_crate::{quote_fee, FeeError};

    #[test]
    fn test_quote_fee_conserves_cents_and_rejects_negative() {
        let quote = quote_fee(10_000, 250).expect("valid quote");
        assert_eq!(quote.fee_cents, 250);
        assert_eq!(quote.net_cents + quote.fee_cents, 10_000);
        assert_eq!(quote_fee(0, 100), Err(FeeError::InvalidAmount(0)));
    }
    """
)

_CLEAN_GO_SRC = textwrap.dedent(
    """\
    package settle

    import "errors"

    var ErrInvalidSettlement = errors.New("settlement inputs violate bounds")

    type Receipt struct {
    \tGrossCents int64
    \tFeeCents   int64
    \tNetCents   int64
    }

    func Settle(grossCents int64, feeCents int64) (Receipt, error) {
    \tif grossCents <= 0 || feeCents < 0 || feeCents > grossCents {
    \t\treturn Receipt{}, ErrInvalidSettlement
    \t}
    \treturn Receipt{
    \t\tGrossCents: grossCents,
    \t\tFeeCents:   feeCents,
    \t\tNetCents:   grossCents - feeCents,
    \t}, nil
    }
    """
)

_CLEAN_GO_TEST = textwrap.dedent(
    """\
    package settle

    import "testing"

    func TestSettleConservesCents(t *testing.T) {
    \trec, err := Settle(5000, 125)
    \tif err != nil {
    \t\tt.Fatalf("unexpected error: %v", err)
    \t}
    \tif rec.NetCents != 4875 {
    \t\tt.Fatalf("expected 4875, got %d", rec.NetCents)
    \t}
    \tif _, badErr := Settle(100, 200); badErr == nil {
    \t\tt.Fatalf("expected error when fee exceeds gross")
    \t}
    }
    """
)

_CLEAN_TS_SRC = textwrap.dedent(
    """\
    export interface Allocation {
      readonly grossCents: number;
      readonly reserveCents: number;
      readonly availableCents: number;
    }

    export function allocateReserve(grossCents: number, reserveBps: number): Allocation {
      if (!Number.isSafeInteger(grossCents) || grossCents <= 0) {
        throw new RangeError("grossCents must be a positive safe integer");
      }
      if (!Number.isSafeInteger(reserveBps) || reserveBps < 0 || reserveBps > 10000) {
        throw new RangeError("reserveBps must be within 0..10000");
      }
      const reserveCents = Math.floor((grossCents * reserveBps) / 10000);
      return {
        grossCents,
        reserveCents,
        availableCents: grossCents - reserveCents,
      };
    }
    """
)

_CLEAN_TS_TEST = textwrap.dedent(
    """\
    import { test } from "node:test";
    import assert from "node:assert/strict";
    import { allocateReserve } from "../src/reserve.ts";

    test("test_allocate_reserve_conserves_cents", () => {
      const out = allocateReserve(10000, 500);
      assert.equal(out.reserveCents, 500);
      assert.equal(out.availableCents + out.reserveCents, 10000);
      assert.throws(() => allocateReserve(0, 500));
    });
    """
)


def test_real_cargo_and_go_subprocess_gate_runners_enforce_tests_and_types(
    tmp_path: Path,
) -> None:
    rs_ws = tmp_path / "rs_ws"
    (rs_ws / "src").mkdir(parents=True)
    (rs_ws / "tests").mkdir(parents=True)
    (rs_ws / "Cargo.toml").write_text(
        '[package]\nname = "fee_crate"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (rs_ws / "src" / "lib.rs").write_text(_CLEAN_RUST_LIB, encoding="utf-8")
    rs_sb = WorkspaceSandbox(rs_ws, frozenset({"src/lib.rs", "tests/fee_test.rs"}))

    no_test_atts = CargoSubprocessGateRunner().run_gates(rs_sb, discover_workspace_units(rs_ws))
    assert not no_test_atts[0].passed
    assert no_test_atts[1].passed

    (rs_ws / "tests" / "fee_test.rs").write_text(_CLEAN_RUST_TEST, encoding="utf-8")
    pass_atts = CargoSubprocessGateRunner().run_gates(rs_sb, discover_workspace_units(rs_ws))
    assert all(a.passed for a in pass_atts)

    go_ws = tmp_path / "go_ws"
    (go_ws / "settle").mkdir(parents=True)
    (go_ws / "go.mod").write_text("module example.com/settlews\n\ngo 1.23\n", encoding="utf-8")
    (go_ws / "settle" / "settle.go").write_text(_CLEAN_GO_SRC, encoding="utf-8")
    go_sb = WorkspaceSandbox(go_ws, frozenset({"settle/settle.go", "settle/settle_test.go"}))

    go_no_test = GoSubprocessGateRunner().run_gates(go_sb, discover_workspace_units(go_ws))
    assert not go_no_test[0].passed
    assert go_no_test[1].passed

    (go_ws / "settle" / "settle_test.go").write_text(_CLEAN_GO_TEST, encoding="utf-8")
    go_pass = GoSubprocessGateRunner().run_gates(go_sb, discover_workspace_units(go_ws))
    assert all(a.passed for a in go_pass)


def test_typescript_node_test_runner_and_local_tsc_shim(tmp_path: Path) -> None:
    ts_ws = tmp_path / "ts_ws"
    (ts_ws / "src").mkdir(parents=True)
    (ts_ws / "tests").mkdir(parents=True)
    (ts_ws / "node_modules" / ".bin").mkdir(parents=True)
    (ts_ws / "package.json").write_text(
        json.dumps({"name": "reserve-ts", "version": "0.1.0", "type": "module"}),
        encoding="utf-8",
    )
    _write_executable_shim(ts_ws / "node_modules" / ".bin", "tsc", "exit 0")
    (ts_ws / "src" / "reserve.ts").write_text(_CLEAN_TS_SRC, encoding="utf-8")
    (ts_ws / "tests" / "reserve.test.ts").write_text(_CLEAN_TS_TEST, encoding="utf-8")

    ts_sb = WorkspaceSandbox(ts_ws, frozenset({"src/reserve.ts", "tests/reserve.test.ts"}))
    atts = TypeScriptSubprocessGateRunner().run_gates(ts_sb, discover_workspace_units(ts_ws))
    assert all(a.passed for a in atts)
    assert atts[0].tool_name == "node-test"
    assert atts[1].tool_name == "tsc"


# ---------------------------------------------------------------------------
# 4. End-to-End Autonomous PCE Orchestrator Across Rust, Go, & TypeScript
# ---------------------------------------------------------------------------


def _output_schema_title(info: AgentInfo) -> str:
    if not info.output_tools:
        return ""
    raw_title = info.output_tools[0].parameters_json_schema.get("title", "")
    return str(raw_title)


def _build_rust_scripted_model() -> FunctionModel:
    contract_src = _CLEAN_RUST_LIB.split("pub fn quote_fee")[0].rstrip() + "\n"

    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        title = _output_schema_title(info)
        if title == "ContractSpecificationBundle":
            return _structured_reply(
                info,
                ContractSpecificationBundle(
                    contract_patches=(
                        FilePatchArtifact(
                            file_path="src/lib.rs",
                            source_code=contract_src,
                            rationale="Phase III Rust error and quote structs.",
                        ),
                    ),
                    declared_symbols=("FeeError", "FeeQuote"),
                    summary="Locked Rust contracts.",
                ),
            )
        if title == "InstrumentationSuiteBundle":
            return _structured_reply(
                info,
                InstrumentationSuiteBundle(
                    test_patches=(
                        FilePatchArtifact(
                            file_path="tests/fee_test.rs",
                            source_code=_CLEAN_RUST_TEST,
                            rationale="Phase III Rust integration test.",
                        ),
                    ),
                    covered_invariant_ids=("INV-001",),
                    summary="Instrumented Rust integration tests.",
                ),
            )
        if title == "ConstructionPatchBundle":
            return _structured_reply(
                info,
                ConstructionPatchBundle(
                    implementation_patches=(
                        FilePatchArtifact(
                            file_path="src/lib.rs",
                            source_code=_CLEAN_RUST_LIB,
                            rationale="Phase IV Rust implementation.",
                        ),
                    ),
                    summary="Synthesized quote_fee.",
                ),
            )
        return _structured_reply(
            info,
            EpistemicObservation(
                inspected_files=(),
                discovered_symbols=(),
                summary="Inspected Rust crate.",
            ),
        )

    return FunctionModel(_respond)


async def test_orchestrator_certifies_rust_mandate_with_real_cargo_gates(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "fee_crate"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    mandate = EngineeringMandate(
        mandate_id="MND-RUST-FEE-01",
        title="Rust Fee Quote Engine",
        objective="Compute integer basis-point fee quotes with strict bounds in Rust.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Net cents plus fee cents equals gross cents for positive amounts.",
                verification_symbol="test_quote_fee_conserves_cents_and_rejects_negative",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_rust_fee",
                owned_files=frozenset({"src/lib.rs", "tests/fee_test.rs"}),
                contract_symbols=(
                    "FeeError",
                    "FeeQuote",
                    "quote_fee",
                    "test_quote_fee_conserves_cents_and_rejects_negative",
                ),
            ),
        ),
    )
    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(_build_rust_scripted_model()),
        gate_runner=PolyglotSubprocessGateRunner(),
    )
    result = await orchestrator.execute_mandate(tmp_path, mandate)
    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert result.certificate is not None
    assert result.dossier_audit is not None
    assert result.dossier_audit.is_occupancy_sealed
    assert result.dossier_audit.highest_verified_phase == PcePhase.OCCUPANCY


def _build_go_renovate_model() -> FunctionModel:
    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        title = _output_schema_title(info)
        if title == "RefactorPatchBundle":
            return _structured_reply(
                info,
                RefactorPatchBundle(
                    refactored_patches=(
                        FilePatchArtifact(
                            file_path="settle/settle.go",
                            source_code=_CLEAN_GO_SRC,
                            rationale="Eliminated panic and mutable package var.",
                        ),
                    ),
                    preserved_symbols=("Receipt", "Settle"),
                    summary="Renovated Go settlement package to JBC v1.2.",
                ),
            )
        if title == "ProvisionPlan":
            return _structured_reply(info, ProvisionPlan(rationale="No deps."))
        return _structured_reply(
            info,
            EpistemicObservation(
                inspected_files=("settle/settle.go",),
                discovered_symbols=("Receipt", "Settle"),
                summary="Inspected Go baseline.",
            ),
        )

    return FunctionModel(_respond)


async def test_orchestrator_renovates_brownfield_go_module_with_real_go_gates(
    tmp_path: Path,
) -> None:
    (tmp_path / "settle").mkdir(parents=True)
    (tmp_path / "go.mod").write_text("module example.com/settlews\n\ngo 1.23\n", encoding="utf-8")
    dirty_go = textwrap.dedent(
        """\
        package settle

        var GlobalCount = 0

        type Receipt struct {
        \tGrossCents int64
        \tFeeCents   int64
        \tNetCents   int64
        }

        func Settle(grossCents int64, feeCents int64) (Receipt, error) {
        \tif grossCents <= 0 {
        \t\tpanic("bad gross")
        \t}
        \tnet := grossCents - feeCents
        \treturn Receipt{GrossCents: grossCents, FeeCents: feeCents, NetCents: net}, nil
        }
        """
    )
    (tmp_path / "settle" / "settle.go").write_text(dirty_go, encoding="utf-8")
    (tmp_path / "settle" / "settle_test.go").write_text(_CLEAN_GO_TEST, encoding="utf-8")

    mandate = EngineeringMandate(
        mandate_id="MND-GO-RENOVATE-01",
        title="Renovate Go Settle Package",
        objective="Eliminate package-level mutable state and panic calls in settle.go.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Settle returns an explicit error on invalid gross or fee bounds.",
                verification_symbol="TestSettleConservesCents",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_go_settle",
                owned_files=frozenset({"settle/settle.go"}),
                contract_symbols=("Receipt", "Settle"),
            ),
        ),
    )
    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(_build_go_renovate_model()),
        gate_runner=PolyglotSubprocessGateRunner(),
    )
    result = await orchestrator.renovate_mandate(tmp_path, mandate)
    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert BehaviorKind.REFACTOR in result.induced_graph.distinct_kinds()
    assert result.dossier_audit is not None and result.dossier_audit.is_occupancy_sealed
    assert os.path.isfile(tmp_path / "settle" / "settle.go")


def _tri_lang_instrument_bundle(prompt_text: str) -> InstrumentationSuiteBundle:
    if "Active Instrumentation Scope: scope_ts" in prompt_text:
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/reserve.test.ts",
                    source_code=_CLEAN_TS_TEST,
                    rationale="TS test",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Instrumented TS.",
        )
    if "Active Instrumentation Scope: scope_rs" in prompt_text:
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="crates/fee_crate/tests/fee_test.rs",
                    source_code=_CLEAN_RUST_TEST,
                    rationale="Rust test",
                ),
            ),
            covered_invariant_ids=("INV-002",),
            summary="Instrumented Rust.",
        )
    return InstrumentationSuiteBundle(
        test_patches=(
            FilePatchArtifact(
                file_path="internal/settle/settle_test.go",
                source_code=_CLEAN_GO_TEST,
                rationale="Go test",
            ),
        ),
        covered_invariant_ids=("INV-003",),
        summary="Instrumented Go.",
    )


def _tri_lang_synth_bundle(prompt_text: str) -> ConstructionPatchBundle:
    if "Active Slice Scope: scope_ts" in prompt_text:
        patch = FilePatchArtifact(
            file_path="src/reserve.ts",
            source_code=_CLEAN_TS_SRC,
            rationale="TS impl",
        )
    elif "Active Slice Scope: scope_rs" in prompt_text:
        patch = FilePatchArtifact(
            file_path="crates/fee_crate/src/lib.rs",
            source_code=_CLEAN_RUST_LIB,
            rationale="Rust impl",
        )
    else:
        patch = FilePatchArtifact(
            file_path="internal/settle/settle.go",
            source_code=_CLEAN_GO_SRC,
            rationale="Go impl",
        )
    return ConstructionPatchBundle(implementation_patches=(patch,), summary="Slice built.")


def _build_tri_lang_delegate_model() -> FunctionModel:
    ts_contract = _CLEAN_TS_SRC.split("export function allocateReserve")[0].rstrip() + "\n"
    rs_contract = _CLEAN_RUST_LIB.split("pub fn quote_fee")[0].rstrip() + "\n"
    go_contract = _CLEAN_GO_SRC.split("func Settle")[0].rstrip() + "\n"

    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt_text = str(messages)
        title = _output_schema_title(info)
        if title == "ContractSpecificationBundle":
            return _structured_reply(
                info,
                ContractSpecificationBundle(
                    contract_patches=(
                        FilePatchArtifact(
                            file_path="src/reserve.ts",
                            source_code=ts_contract,
                            rationale="TS contract",
                        ),
                        FilePatchArtifact(
                            file_path="crates/fee_crate/src/lib.rs",
                            source_code=rs_contract,
                            rationale="Rust contract",
                        ),
                        FilePatchArtifact(
                            file_path="internal/settle/settle.go",
                            source_code=go_contract,
                            rationale="Go contract",
                        ),
                    ),
                    declared_symbols=("Allocation", "FeeError", "FeeQuote", "Receipt"),
                    summary="Locked TS, Rust, and Go contracts.",
                ),
            )
        if title == "InstrumentationSuiteBundle":
            return _structured_reply(info, _tri_lang_instrument_bundle(prompt_text))
        if title == "ConstructionPatchBundle":
            return _structured_reply(info, _tri_lang_synth_bundle(prompt_text))
        return _structured_reply(
            info,
            EpistemicObservation(
                inspected_files=(),
                discovered_symbols=(),
                summary="Inspected polyglot monorepo.",
            ),
        )

    return FunctionModel(_respond)


async def test_orchestrator_parallel_delegate_reconciles_ts_rust_and_go_monorepo(
    tmp_path: Path,
) -> None:
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    _write_executable_shim(tmp_path / "node_modules" / ".bin", "tsc", "exit 0")
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "poly-monorepo", "version": "0.1.0", "type": "module"}),
        encoding="utf-8",
    )
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/fee_crate"]\nresolver = "2"\n',
        encoding="utf-8",
    )
    (tmp_path / "crates" / "fee_crate").mkdir(parents=True)
    (tmp_path / "crates" / "fee_crate" / "Cargo.toml").write_text(
        '[package]\nname = "fee_crate"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "go.mod").write_text("module example.com/polymono\n\ngo 1.23\n", encoding="utf-8")

    mandate = EngineeringMandate(
        mandate_id="MND-POLY-MONO-01",
        title="Three-Language Parallel Settlement Monorepo",
        objective="Synthesize TS reserve, Rust fee quote, and Go settlement slices in parallel.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="TypeScript reserve allocation conserves gross integer cents.",
                verification_symbol="test_allocate_reserve_conserves_cents",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="Rust basis-point fee quote conserves gross integer cents.",
                verification_symbol="test_quote_fee_conserves_cents_and_rejects_negative",
            ),
            DomainInvariant(
                invariant_id="INV-003",
                statement="Go settlement receipt conserves gross integer cents.",
                verification_symbol="TestSettleConservesCents",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_ts",
                owned_files=frozenset({"src/reserve.ts", "tests/reserve.test.ts"}),
                contract_symbols=(
                    "Allocation",
                    "allocateReserve",
                    "test_allocate_reserve_conserves_cents",
                ),
            ),
            ModuleWriteScope(
                scope_id="scope_rs",
                owned_files=frozenset(
                    {"crates/fee_crate/src/lib.rs", "crates/fee_crate/tests/fee_test.rs"}
                ),
                contract_symbols=(
                    "FeeError",
                    "FeeQuote",
                    "quote_fee",
                    "test_quote_fee_conserves_cents_and_rejects_negative",
                ),
            ),
            ModuleWriteScope(
                scope_id="scope_go",
                owned_files=frozenset(
                    {"internal/settle/settle.go", "internal/settle/settle_test.go"}
                ),
                contract_symbols=("Receipt", "Settle", "TestSettleConservesCents"),
                depends_on=frozenset({"scope_ts", "scope_rs"}),
            ),
        ),
    )
    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(_build_tri_lang_delegate_model()),
        gate_runner=PolyglotSubprocessGateRunner(),
    )
    result = await orchestrator.execute_mandate(tmp_path, mandate)
    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert {BehaviorKind.DELEGATE, BehaviorKind.RECONCILE} <= result.induced_graph.distinct_kinds()
    assert result.dossier_audit is not None and result.dossier_audit.is_occupancy_sealed
    assert result.inspection_report is not None
    assert len(result.inspection_report.unwaived_violations()) == 0
