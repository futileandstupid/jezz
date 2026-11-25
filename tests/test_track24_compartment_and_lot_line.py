"""Track 24: JBC-COG-006 (Edition 1.3) compartment DAG/floorplates & pre-flight lot-line shear."""

import textwrap
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from mcp import Client
from mcp.types import TextContent
from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents import (
    EnvironmentObservation,
    EpistemicObservation,
    FramedMandateProposal,
    InProcessGateRunner,
    PceModelSuite,
    PceRunStatus,
    PceWorkflowOrchestrator,
    ScopeDecompositionPlan,
)
from jezz.behaviors import BehaviorKind
from jezz.codes import (
    CODE_BOOK,
    CODEBOOK_EDITION,
    BuildingInspector,
    CodePermit,
    CodeTrigger,
    CodeVariance,
    DifferentialScope,
    InspectedUnit,
    ManifestDependencies,
    OccupancyRuling,
    PermitProfile,
    analyze_workspace_compartments,
    check_cog_006,
    discover_workspace_units,
    inspect_differential_rough_in,
    parse_go_unit,
    parse_source_unit,
    parse_typescript_unit,
)
from jezz.codes.rules_cog import check_cog_003
from jezz.guards import ContainedPath
from jezz.mcp_server import build_jezz_mcp_server
from jezz.methodology import (
    DependencyRequirement,
    DomainInvariant,
    EngineeringMandate,
    LotLineAssessment,
    LotLineDisposition,
    ModuleWriteScope,
    assess_mandate_lot_line,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _clean_py_mod(fn_name: str, extra_imports: str = "") -> str:
    imp_block = f"{extra_imports}\n" if extra_imports else ""
    return (
        f"{imp_block}"
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\n"
        f"def {fn_name}(x: PositiveInt) -> PositiveInt:\n"
        "    return x\n"
    )


def _write_rel(root: Path, rel_path: str, content: str) -> Path:
    target = ContainedPath.from_parts(root, rel_path).resolved_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _first_text(blocks: list[object]) -> str:
    for blk in blocks:
        if isinstance(blk, TextContent):
            return blk.text
    return ""


def _structured_reply(info: AgentInfo, payload: BaseModel) -> ModelResponse:
    raw = payload.model_dump_json()
    if info.output_tools:
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=raw)])
    return ModelResponse(parts=[TextPart(content=raw)])


def _seed_six_module_core_repo(repo: Path) -> tuple[InspectedUnit, ...]:
    _write_rel(
        repo,
        "pyproject.toml",
        textwrap.dedent(
            """\
            [project]
            name = "core-pkg"
            version = "0.1.0"
            dependencies = ["jezz>=0.1.0", "pydantic>=2.14.4"]

            [dependency-groups]
            dev = ["pytest>=9.2.0"]
            """
        ),
    )
    _write_rel(repo, "src/core_pkg/__init__.py", '"""Core package."""\n')
    _write_rel(repo, "src/core_pkg/core/__init__.py", '"""Core compartment."""\n')
    for idx in range(4):
        _write_rel(repo, f"src/core_pkg/core/m{idx}.py", _clean_py_mod(f"step_{idx}"))
    _write_rel(
        repo,
        "src/core_pkg/api.py",
        _clean_py_mod("run_api", "import core_pkg.core.m0"),
    )
    return discover_workspace_units(repo)


def test_self_hosted_jezz_passes_cog_006_in_standard_and_strict_profiles() -> None:
    """INV-2401 & INV-2402: Self-hosted jezz satisfies Edition 1.3 JBC-COG-006 in standard mode."""
    assert CODEBOOK_EDITION == "1.3"
    assert "JBC-COG-006" in CODE_BOOK
    units = discover_workspace_units(REPO_ROOT)

    assert check_cog_006(units, profile=PermitProfile.STANDARD) == []

    strict_hits = check_cog_006(units, profile=PermitProfile.STRICT_INTERNAL)
    assert not any("Cyclic" in d.message or "portal ratio" in d.message for d in strict_hits)
    assert len(strict_hits) == 2

    summary = analyze_workspace_compartments(units)
    comp_keys = {c.compartment_key for c in summary.compartments}
    assert {
        ".:jezz/agents",
        ".:jezz/behaviors",
        ".:jezz/codes",
        ".:jezz/evals",
        ".:jezz/methodology",
    } <= comp_keys
    assert all(c.direct_module_count <= 35 for c in summary.compartments)
    assert all(p.module_count <= 100 for p in summary.packages)


def test_cog_006_catches_cross_compartment_quotient_cycle_missed_by_cog_003() -> None:
    """INV-2401: Cross-folder cycle (alpha/x -> beta/y, beta/z -> alpha/w) is caught by G/~."""
    units = (
        parse_source_unit("src/pkg/alpha/x.py", _clean_py_mod("fx", "import pkg.beta.y")),
        parse_source_unit("src/pkg/alpha/w.py", _clean_py_mod("fw")),
        parse_source_unit("src/pkg/beta/y.py", _clean_py_mod("fy")),
        parse_source_unit("src/pkg/beta/z.py", _clean_py_mod("fz", "import pkg.alpha.w")),
    )
    assert check_cog_003(units) == []

    cog_006_hits = check_cog_006(units)
    assert len(cog_006_hits) == 1
    assert "pkg/alpha <-> pkg/beta" in cog_006_hits[0].message

    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"jezz"}),
            workspace_roots=frozenset({"pkg"}),
        )
    )
    rough = inspector.inspect_rough_in(units)
    assert rough.ruling == OccupancyRuling.RED_TAG
    assert [f.code_id for f in rough.unwaived_violations()] == ["JBC-COG-006"]

    diff_rep = inspect_differential_rough_in(
        inspector,
        units,
        DifferentialScope(changed_paths=frozenset({"src/pkg/alpha/x.py"})),
    )
    assert diff_rep.report.ruling == OccupancyRuling.RED_TAG
    assert [f.code_id for f in diff_rep.report.unwaived_violations()] == ["JBC-COG-006"]


@settings(max_examples=30)
@given(
    edges=st.sets(
        st.tuples(
            st.integers(min_value=0, max_value=3),
            st.integers(min_value=0, max_value=1),
            st.integers(min_value=0, max_value=3),
            st.integers(min_value=0, max_value=1),
        ),
        max_size=12,
    )
)
def test_property_monotone_compartment_dags_have_zero_quotient_cycles(
    edges: set[tuple[int, int, int, int]],
) -> None:
    """INV-2401: Strict topological compartment order produces zero JBC-COG-006 cycle findings."""
    imports_map: dict[tuple[int, int], set[str]] = {(c, m): set() for c in range(4) for m in (0, 1)}
    for c_src, m_src, c_dst, m_dst in edges:
        if c_src < c_dst:
            imports_map[(c_src, m_src)].add(f"import pkg.c{c_dst}.m{m_dst}")

    dag_units = tuple(
        parse_source_unit(
            f"src/pkg/c{c}/m{m}.py",
            _clean_py_mod(f"fn_{c}_{m}", "\n".join(sorted(imports_map[(c, m)]))),
        )
        for c in range(4)
        for m in (0, 1)
    )
    assert check_cog_006(dag_units) == []

    cyc_units = (
        *dag_units,
        parse_source_unit("src/pkg/c0/lead.py", _clean_py_mod("lead", "import pkg.c3.m0")),
        parse_source_unit("src/pkg/c3/back.py", _clean_py_mod("back", "import pkg.c0.m1")),
    )
    cyc_msgs = [d.message for d in check_cog_006(cyc_units)]
    assert any("Cyclic subpackage compartment dependency" in msg for msg in cyc_msgs)


def test_cog_006_enforces_compartment_floorplates_and_variances() -> None:
    """INV-2402: Compartment ceilings enforce <= 35 (standard) and <= 25 (strict_internal)."""
    units_26 = tuple(
        parse_source_unit(f"src/pkg/heavy/m{idx:02d}.py", _clean_py_mod(f"f_{idx}"))
        for idx in range(26)
    )
    assert check_cog_006(units_26, profile=PermitProfile.STANDARD) == []
    strict_hits = check_cog_006(units_26, profile=PermitProfile.STRICT_INTERNAL)
    assert len(strict_hits) == 1
    assert "26 direct production modules (limit 25)" in strict_hits[0].message

    units_36 = tuple(
        parse_source_unit(f"src/pkg/heavy/m{idx:02d}.py", _clean_py_mod(f"f_{idx}"))
        for idx in range(36)
    )
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"jezz"}),
            workspace_roots=frozenset({"pkg"}),
        )
    )
    permit_with_var = CodePermit.full_book(
        "PERMIT-COG006-VAR",
        variances=(
            CodeVariance(
                variance_id="VAR-COG006-01",
                code_id="JBC-COG-006",
                file_path="src/pkg/heavy/m00.py",
                authorized_by="Chief Architect",
                justification="Temporary compartment floorplate waiver pending split.",
            ),
        ),
    )
    waived = inspector.inspect_rough_in(units_36, permit=permit_with_var)
    assert waived.ruling == OccupancyRuling.APPROVED_WITH_VARIANCES


def test_cog_006_enforces_package_floorplate_and_sham_portal_ratio() -> None:
    """INV-2402: Package ceiling (75 strict) and portal ratio (> 0.65) catch unsplit sprawl."""
    pkg_76 = tuple(
        parse_source_unit(
            f"src/pkg/part{idx // 19}/m{idx:02d}.py",
            _clean_py_mod(f"f_{idx}"),
        )
        for idx in range(76)
    )
    assert check_cog_006(pkg_76, profile=PermitProfile.STANDARD) == []
    pkg_strict = check_cog_006(pkg_76, profile=PermitProfile.STRICT_INTERNAL)
    assert any("76 production modules (limit 75)" in d.message for d in pkg_strict)

    sham_units = tuple(
        parse_source_unit(f"src/pkg/sham/s{idx:02d}.py", _clean_py_mod(f"s_{idx}"))
        for idx in range(10)
    )
    porous_caller = parse_source_unit(
        "src/pkg/facade.py",
        _clean_py_mod(
            "call_all",
            "\n".join(f"import pkg.sham.s{idx:02d}" for idx in range(8)),
        ),
    )
    porous_hits = check_cog_006((*sham_units, porous_caller))
    assert len(porous_hits) == 1
    assert "portal ratio 0.80 > 0.65" in porous_hits[0].message

    narrow_caller = parse_source_unit(
        "src/pkg/facade.py",
        _clean_py_mod("call_two", "import pkg.sham.s00\nimport pkg.sham.s01"),
    )
    assert check_cog_006((*sham_units, narrow_caller)) == []


def test_cog_006_enforces_polyglot_workspace_member_firewalls() -> None:
    """INV-2402: Cross-member relative climbs, /src/ penetrations, and Go internal/ are blocked."""
    ts_core = parse_typescript_unit(
        "packages/core/src/secret.ts",
        "export function checkVal(x: bigint): bigint { if (x < 0n) { return 0n; } return x; }\n",
    )
    ts_escape = parse_typescript_unit(
        "packages/web/src/app.ts",
        "import { checkVal } from '../../core/src/secret';\n"
        "import { other } from 'core/src/secret';\n"
        "export function runApp(x: bigint): bigint {\n"
        "  if (x < 0n) { return 0n; }\n"
        "  return checkVal(x) + other(x);\n"
        "}\n",
    )
    ts_hits = check_cog_006((ts_core, ts_escape))
    assert len(ts_hits) == 2

    go_valid = parse_go_unit(
        "services/auth/api/login.go",
        'package api\nimport "github.com/acme/mono/services/auth/internal/keys"\n'
        "func Login(id int64) int64 { if id <= 0 { return 0 }; return keys.Sign(id) }\n",
    )
    go_breach = parse_go_unit(
        "services/billing/charge.go",
        'package billing\nimport "github.com/acme/mono/services/auth/internal/keys"\n'
        "func Charge(id int64) int64 { if id <= 0 { return 0 }; return keys.Sign(id) }\n",
    )
    go_hits = check_cog_006((go_valid, go_breach))
    assert len(go_hits) == 1
    assert go_hits[0].location.file_path == "services/billing/charge.go"

    py_core = parse_source_unit("packages/core/src/core_pkg/_priv.py", _clean_py_mod("p"))
    py_cli = parse_source_unit(
        "packages/cli/src/cli_pkg/run.py",
        "import core_pkg._priv\nfrom ...escape import bad\n" + _clean_py_mod("run_cli"),
    )
    py_hits = check_cog_006((py_core, py_cli))
    assert len(py_hits) == 2


def test_lot_line_assessment_spares_hexagonal_adapter_and_flags_satellites(
    tmp_path: Path,
) -> None:
    """INV-2403: Inward Hexagonal adapters stay IN_PACKAGE while zero-fan-in satellites split."""
    units = _seed_six_module_core_repo(tmp_path)
    manifest = BuildingInspector.from_workspace(tmp_path).manifest

    hex_mandate = EngineeringMandate(
        mandate_id="MND-HEX-24",
        title="Encapsulated CST parser port inside core",
        objective="Add deterministic syntax parser adapter inside core compartment.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-2401",
                statement="parse_port rejects malformed CST inputs deterministically.",
                verification_symbol="test_parse_port",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="core_port",
                owned_files=frozenset({"src/core_pkg/core/cst_port.py", "tests/test_port.py"}),
                contract_symbols=("parse_port",),
            ),
        ),
        required_dependencies=(DependencyRequirement(package="tree-sitter"),),
    )
    hex_res = assess_mandate_lot_line(hex_mandate, units, manifest=manifest)
    assert hex_res.inbound_core_importers >= 1
    assert hex_res.is_zero_fan_in_satellite is False
    assert hex_res.disposition == LotLineDisposition.IN_PACKAGE

    sat_comp = EngineeringMandate(
        mandate_id="MND-SAT-COMP-24",
        title="Offline benchmark harness consumer",
        objective="Add benchmark harness consuming core_pkg public contracts.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-2402",
                statement="run_bench produces deterministic score summaries.",
                verification_symbol="test_run_bench",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="bench_scope",
                owned_files=frozenset({"src/core_pkg/bench/runner.py", "tests/test_bench.py"}),
                contract_symbols=("run_bench",),
            ),
        ),
        required_dependencies=(DependencyRequirement(package="pydantic-evals"),),
    )
    comp_res = assess_mandate_lot_line(sat_comp, units, manifest=manifest)
    assert comp_res.is_zero_fan_in_satellite is True
    assert comp_res.public_api_sufficient is True
    assert comp_res.disposition == LotLineDisposition.WORKSPACE_COMPARTMENT

    sat_split = EngineeringMandate(
        mandate_id="MND-SAT-SPLIT-24",
        title="External HTTPS vault synchronization daemon",
        objective="Synchronize OAuth bearer tokens over HTTPS REST API endpoints.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-2403",
                statement="sync_vault redacts credentials across all HTTPS retries.",
                verification_symbol="test_sync_vault",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="cloud_sync",
                owned_files=frozenset({"src/core_pkg/cloud_sync/client.py", "tests/test_sync.py"}),
                contract_symbols=("sync_vault",),
            ),
        ),
        required_dependencies=(DependencyRequirement(package="httpx"),),
    )
    split_res = assess_mandate_lot_line(sat_split, units, manifest=manifest)
    assert split_res.disposition == LotLineDisposition.LOT_LINE_SPLIT
    assert CodeTrigger.EXTERNAL_BOUNDARY in split_res.escalated_triggers
    assert CodeTrigger.SENSITIVE_CONTEXT in split_res.escalated_triggers


def _build_preflight_suite(split_mandate: EngineeringMandate) -> PceModelSuite:
    frame_fn = FunctionModel(
        lambda _m, i: _structured_reply(
            i,
            FramedMandateProposal(
                mandate=split_mandate,
                framing_notes="Framed HTTPS webhook publisher.",
            ),
        )
    )
    env_fn = FunctionModel(
        lambda _m, i: _structured_reply(
            i,
            EnvironmentObservation(
                python_version="3.14.0",
                summary="Checked httpx missing from core-pkg runtime roots.",
            ),
        )
    )
    inspect_fn = FunctionModel(
        lambda _m, i: _structured_reply(
            i,
            EpistemicObservation(
                inspected_files=("src/core_pkg/api.py",),
                discovered_symbols=("run_api",),
                summary="Inspected 6 core modules with zero inbound satellite edges.",
            ),
        )
    )
    decomp_fn = FunctionModel(
        lambda _m, i: _structured_reply(
            i,
            ScopeDecompositionPlan(
                write_scopes=split_mandate.write_scopes,
                decomposition_rationale="Isolated webhook scope.",
            ),
        )
    )
    return PceModelSuite(
        frame_model=frame_fn,
        inspect_model=inspect_fn,
        inspect_env_model=env_fn,
        hypothesize_model=inspect_fn,
        curate_memory_model=inspect_fn,
        decompose_model=decomp_fn,
        specify_model=inspect_fn,
        instrument_model=inspect_fn,
        synthesize_model=inspect_fn,
    )


async def test_mcp_jbc_assess_lot_line_and_preflight_decompose_elicitation(
    tmp_path: Path,
) -> None:
    """INV-2403: MCP jbc_assess_lot_line and DecomposeNode route unapproved splits to ELICIT."""
    _seed_six_module_core_repo(tmp_path)
    split_mandate = EngineeringMandate(
        mandate_id="MND-MCP-LOT-24",
        title="Remote HTTPS webhook publisher",
        objective="Publish domain events to external HTTPS webhook endpoints.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-2403",
                statement="publish_webhook enforces bounded retry timeouts.",
                verification_symbol="test_publish_webhook",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="webhook_scope",
                owned_files=frozenset(
                    {"src/core_pkg/webhooks/publisher.py", "tests/test_webhooks.py"}
                ),
                contract_symbols=("publish_webhook",),
            ),
        ),
        required_dependencies=(DependencyRequirement(package="httpx"),),
    )
    _write_rel(tmp_path, "mandate.json", split_mandate.model_dump_json(indent=2))

    server = build_jezz_mcp_server(workspace_root=tmp_path)
    async with Client(server, mode="legacy") as client:
        by_path = await client.call_tool("jbc_assess_lot_line", {"mandate_path": "mandate.json"})
        by_json = await client.call_tool(
            "jbc_assess_lot_line",
            {"mandate_json": split_mandate.model_dump(mode="json")},
        )
        bad_call = await client.call_tool("jbc_assess_lot_line", {})

    assert bad_call.is_error is True
    parsed = LotLineAssessment.model_validate_json(_first_text(list(by_path.content)))
    parsed_json = LotLineAssessment.model_validate_json(_first_text(list(by_json.content)))
    assert parsed.disposition == LotLineDisposition.LOT_LINE_SPLIT
    assert parsed_json == parsed

    orchestrator = PceWorkflowOrchestrator(
        _build_preflight_suite(split_mandate),
        inspector=BuildingInspector.from_workspace(tmp_path),
        gate_runner=InProcessGateRunner(lambda _sb, _u: 1),
    )
    result = await orchestrator.execute_prompt(
        tmp_path,
        "Add external HTTPS webhook publisher module.",
    )
    assert result.status == PceRunStatus.HALTED_FOR_ELICITATION
    assert result.graph_verification.is_valid is True
    assert result.elicitation_ticket is not None
    assert any("Lot-line shear" in amb for amb in result.mandate.open_ambiguities)
    kinds = [n.kind for n in result.induced_graph.nodes]
    assert BehaviorKind.DECOMPOSE in kinds
    assert kinds[-1] == BehaviorKind.ELICIT
    assert not ContainedPath.from_parts(
        tmp_path, "src/core_pkg/webhooks/publisher.py"
    ).resolved_path.exists()
