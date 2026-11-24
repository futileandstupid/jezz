"""Track 11: Differential Occupancy Ratchet & scoped CodePermit brownfield renovation tests."""

import io
import textwrap
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents.orchestrator import PceWorkflowOrchestrator
from jezz.agents.schemas import (
    EnvironmentObservation,
    EpistemicObservation,
    FilePatchArtifact,
    PceRunStatus,
    PceRuntimeBudget,
    RefactorPatchBundle,
    RemediationHypothesis,
)
from jezz.agents.specialists import uniform_model_suite
from jezz.agents.workspace import InProcessGateRunner, find_type_alias_misuse
from jezz.cli import main
from jezz.codes import (
    CodePermit,
    CodeVariance,
    DifferentialScope,
    FindingDisposition,
    GateAttestation,
    GateKind,
    OccupancyRuling,
    build_workspace_inspector,
    discover_workspace_units,
    inspect_differential_final,
    inspect_differential_rough_in,
    parse_source_unit,
)
from jezz.guards import ContainedPath
from jezz.methodology import (
    DomainInvariant,
    EngineeringDossier,
    EngineeringMandate,
    ModuleWriteScope,
    audit_pce_dossier,
)

_GO_MOD = "module example.com/brownfield\n\ngo 1.24\n"

_LEGACY_UNTOUCHED_GO = textwrap.dedent(
    """\
    package brownfield

    import "fmt"

    var GlobalMutableCounter = 0

    func LegacyUntouchedHelper(raw any) any {
        fmt.Println(raw)
        return raw
    }
    """
)

_DEFECT_TARGET_GO = textwrap.dedent(
    """\
    package brownfield

    import "fmt"

    func ComputeQuota(tier string, base int64) (result int64) {
        fmt.Println(tier)
        if tier == "gold" {
            result = base * 3
            return
        } else if tier == "silver" {
            result = base * 2
            return
        } else if tier == "bronze" {
            result = base
            return
        }
        result = 0
        return
    }
    """
)

_CLEAN_TARGET_GO = textwrap.dedent(
    """\
    package brownfield

    import "errors"

    var ErrInvalidQuotaInput = errors.New("invalid quota input")

    func ComputeQuota(tier string, base int64) (int64, error) {
        if len(tier) == 0 || len(tier) > 32 || base < 0 {
            return 0, ErrInvalidQuotaInput
        }
        switch tier {
        case "gold":
            return base * 3, nil
        case "silver":
            return base * 2, nil
        case "bronze":
            return base, nil
        default:
            return 0, ErrInvalidQuotaInput
        }
    }
    """
)

_UPSTREAM_TEST_GO = textwrap.dedent(
    """\
    package brownfield

    import "testing"

    func TestComputeQuotaTiers(t *testing.T) {
        got, err := ComputeQuota("gold", 10)
        if err != nil || got != 30 {
            t.Fatalf("expected 30, got %d (err=%v)", got, err)
        }
    }
    """
)


def _write_file(root: Path, rel: str, content: str) -> None:
    target = ContainedPath.from_parts(root, rel)
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    target.resolved_path.write_text(content, encoding="utf-8")


def _select_renovate_payload(info: AgentInfo) -> BaseModel:
    out_tool = info.output_tools[0]
    props = out_tool.parameters_json_schema.get("properties", {})
    if "python_version" in props:
        return EnvironmentObservation(
            python_version="3.14",
            summary="Detected Go module workspace.",
        )
    if "inspected_files" in props:
        return EpistemicObservation(
            inspected_files=("quota.go",),
            discovered_symbols=("ComputeQuota",),
            summary="Identified naked returns, fmt.Println, and else-if chain in quota.go.",
        )
    if "root_causes" in props:
        return RemediationHypothesis(
            root_causes=("Untouched legacy.go triggers full-repository RED_TAG.",),
            cited_code_ids=("JBC-OBS-001",),
            remediation_plan="Enable ratchet_to_scope=True for brownfield slice isolation.",
        )
    return RefactorPatchBundle(
        refactored_patches=(
            FilePatchArtifact(
                file_path="quota.go",
                source_code=_CLEAN_TARGET_GO,
                rationale="Eliminated naked returns, fmt.Println, and >1 else-if ladder.",
            ),
        ),
        preserved_symbols=("ComputeQuota",),
        summary="Renovated quota.go to zero JBC v1.2 violations.",
    )


def _build_mock_renovate_model() -> FunctionModel:
    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        payload = _select_renovate_payload(info)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args=payload.model_dump_json(),
                )
            ]
        )

    return FunctionModel(_respond)


def test_inspect_differential_final_and_scoped_variance_permit(tmp_path: Path) -> None:
    _write_file(tmp_path, "go.mod", _GO_MOD)
    _write_file(tmp_path, "legacy.go", _LEGACY_UNTOUCHED_GO)
    _write_file(tmp_path, "quota.go", _CLEAN_TARGET_GO)
    _write_file(tmp_path, "quota_test.go", _UPSTREAM_TEST_GO)

    units = discover_workspace_units(tmp_path)
    inspector = build_workspace_inspector(tmp_path, units)
    full_report = inspector.inspect_rough_in(units)
    assert not full_report.is_occupiable

    attestations = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="go-vet",
            passed=True,
            exit_code=0,
            checks_executed=3,
            duration_ms=5.0,
            peak_memory_kb=512,
            summary="go vet clean",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="go-test",
            passed=True,
            exit_code=0,
            checks_executed=1,
            duration_ms=8.0,
            peak_memory_kb=512,
            summary="PASS",
        ),
    )
    diff_final = inspect_differential_final(
        inspector,
        units,
        DifferentialScope(changed_paths=frozenset({"quota.go"})),
        attestations=attestations,
    )
    assert diff_final.is_occupiable
    assert diff_final.untouched_unit_count == 2
    assert "JBC-VRF-005" in diff_final.report.effective_code_ids
    assert "JBC-VRF-006" in diff_final.report.effective_code_ids

    permit = CodePermit.full_book(
        permit_id="PERMIT-OSS-VAR-01",
        variances=(
            CodeVariance(
                variance_id="VAR-LEGACY-PRINT-01",
                code_id="JBC-OBS-001",
                file_path="legacy.go",
                authorized_by="Principal Engineer",
                justification="Legacy CLI helper writes to stdout for v1 compatibility.",
                expires_on=date(2027, 6, 1),
            ),
        ),
    )
    legacy_diff = inspect_differential_final(
        inspector,
        units,
        DifferentialScope(
            changed_paths=frozenset({"legacy.go"}),
            reference_date=date(2026, 11, 23),
        ),
        permit=permit,
        attestations=attestations,
    )
    waived_codes = [
        f.code_id
        for f in legacy_diff.report.findings
        if f.disposition == FindingDisposition.VARIANCE_WAIVED
    ]
    assert "JBC-OBS-001" in waived_codes


@pytest.mark.asyncio
async def test_ratchet_scoped_brownfield_renovation_isolates_untouched_legacy_debt(
    tmp_path: Path,
) -> None:
    _write_file(tmp_path, "go.mod", _GO_MOD)
    _write_file(tmp_path, "legacy.go", _LEGACY_UNTOUCHED_GO)
    _write_file(tmp_path, "quota.go", _DEFECT_TARGET_GO)
    _write_file(tmp_path, "quota_test.go", _UPSTREAM_TEST_GO)

    mandate = EngineeringMandate(
        mandate_id="MND-TRACK11-RATCHET-GO",
        title="Surgical renovation of quota.go inside brownfield Go module",
        objective="Eliminate naked returns, stdout prints, and >1 else-if branches in quota.go.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "ComputeQuota maps gold/silver/bronze multipliers and rejects bad inputs."
                ),
                verification_symbol="TestComputeQuotaTiers",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_quota",
                owned_files=frozenset({"quota.go"}),
                contract_symbols=("ComputeQuota",),
            ),
        ),
    )

    orchestrator_strict = PceWorkflowOrchestrator(
        uniform_model_suite(_build_mock_renovate_model()),
        gate_runner=InProcessGateRunner(),
        budget=PceRuntimeBudget(max_remediation_cycles=1, ratchet_to_scope=False),
    )
    strict_res = await orchestrator_strict.renovate_mandate(tmp_path, mandate)
    assert strict_res.status == PceRunStatus.ESCALATED_AFTER_RED_TAG

    orchestrator_ratchet = PceWorkflowOrchestrator(
        uniform_model_suite(_build_mock_renovate_model()),
        gate_runner=InProcessGateRunner(),
        budget=PceRuntimeBudget(ratchet_to_scope=True),
    )
    ratchet_res = await orchestrator_ratchet.renovate_mandate(tmp_path, mandate)
    assert ratchet_res.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert ratchet_res.inspection_report is not None
    assert ratchet_res.inspection_report.ruling == OccupancyRuling.APPROVED
    assert ratchet_res.dossier_audit is not None
    assert ratchet_res.dossier_audit.is_occupancy_sealed
    assert (tmp_path / "legacy.go").read_text(encoding="utf-8") == _LEGACY_UNTOUCHED_GO

    units_after = discover_workspace_units(tmp_path)
    assert ratchet_res.certificate is not None
    persisted = EngineeringDossier(
        mandate=mandate,
        permit=ratchet_res.permit,
        behavior_graph=ratchet_res.induced_graph,
        mutated_files=frozenset({"quota.go"}),
        ratchet_scoped=True,
        inspection_report=ratchet_res.inspection_report,
        certificate=ratchet_res.certificate,
    )
    dossier_path = ContainedPath.from_parts(tmp_path, "dossier.json").resolved_path
    dossier_path.write_text(persisted.model_dump_json(indent=2), encoding="utf-8")
    assert audit_pce_dossier(persisted, units_after).is_occupancy_sealed

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = main(
        ["audit", str(dossier_path), "--repo", str(tmp_path)],
        stdout=out_buf,
        stderr=err_buf,
    )
    assert rc == 0
    assert "OCCUPANCY_SEALED" in out_buf.getvalue()


def test_monorepo_unit_ceiling_and_cli_validation_error_formatting(tmp_path: Path) -> None:
    candidate = parse_source_unit(
        "src/pkg/mod_target.py",
        "def compute_val(x: int) -> int:\n    return x + 1\n",
    )
    monorepo_units = tuple(
        parse_source_unit(f"src/pkg/m_{idx}.py", f"CONST_{idx}: int = {idx}\n")
        for idx in range(600)
    )
    assert find_type_alias_misuse((candidate,), monorepo_units) is None

    bad_mandate = ContainedPath.from_parts(tmp_path, "bad_mandate.json").resolved_path
    bad_mandate.write_text('{"mandate_id": ""}', encoding="utf-8")
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = main(
        [
            "renovate",
            str(tmp_path),
            "--model",
            "antigravity:claude-sonnet-5-5-medium",
            "--mandate",
            str(bad_mandate),
            "--gate-runner",
            "in-process",
        ],
        stdout=out_buf,
        stderr=err_buf,
    )
    assert rc == 2
    assert "validation error(s)" in err_buf.getvalue()


def test_rust_inline_mod_super_import_avoids_false_module_cycle(tmp_path: Path) -> None:
    _write_file(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "lexprobe"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write_file(
        tmp_path,
        "src/lexical/mod.rs",
        "pub(crate) mod bignum;\npub(crate) mod math;\npub use self::bignum::Bigint;\n",
    )
    _write_file(
        tmp_path,
        "src/lexical/bignum.rs",
        "use super::math::Limb;\npub struct Bigint {\n    pub limb: Limb,\n}\n",
    )
    _write_file(
        tmp_path,
        "src/lexical/math.rs",
        textwrap.dedent(
            """\
            pub type Limb = u64;

            mod small {
                use super::*;

                pub fn step(x: Limb) -> Limb {
                    x.saturating_add(1)
                }
            }

            pub fn next_limb(x: Limb) -> Limb {
                small::step(x)
            }
            """
        ),
    )

    units = discover_workspace_units(tmp_path)
    inspector = build_workspace_inspector(tmp_path, units)
    diff = inspect_differential_rough_in(
        inspector,
        units,
        DifferentialScope(changed_paths=frozenset({"src/lexical/bignum.rs"})),
    )
    assert diff.is_occupiable
    assert diff.report.unwaived_violations() == ()


@given(
    alias_name=st.sampled_from(
        ("IdentifierStr", "PositiveInt", "NonNegativeInt", "BoundedText", "FiniteFloat")
    ),
)
def test_property_find_type_alias_misuse_catches_calls_and_subscripts(
    alias_name: str,
) -> None:
    clean_unit = parse_source_unit(
        "src/pkg/ok.py",
        f"from jezz.guards import {alias_name}\nVAL: {alias_name}\n",
    )
    assert find_type_alias_misuse((clean_unit,)) is None

    call_unit = parse_source_unit(
        "src/pkg/bad_call.py",
        f"from jezz.guards import {alias_name}\nX = {alias_name}(1)\n",
    )
    assert find_type_alias_misuse((call_unit,)) is not None

    sub_unit = parse_source_unit(
        "src/pkg/bad_sub.py",
        f"from jezz.guards import {alias_name}\ntype T = {alias_name}[0, 10]\n",
    )
    assert find_type_alias_misuse((sub_unit,)) is not None
