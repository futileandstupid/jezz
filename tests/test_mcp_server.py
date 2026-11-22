"""End-to-end MCP client-server protocol, security jail, CLI, and Hypothesis tests for jezz mcp."""

import io
import subprocess
import textwrap
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from mcp import Client
from mcp.types import TextContent

from jezz.behaviors import (
    BehaviorEdge,
    BehaviorGraph,
    BehaviorKind,
    BehaviorNode,
    all_canonical_archetypes,
    build_readonly_audit_graph,
    verify_behavior_graph,
)
from jezz.cli import main
from jezz.codes import (
    CODE_BOOK,
    BuildingCode,
    BuildingInspector,
    CodeArticle,
    CodePermit,
    CodeTrigger,
    CodeVariance,
    DifferentialInspectionReport,
    GateAttestation,
    GateKind,
    InspectionReport,
    OccupancyRuling,
    compute_units_digest,
    discover_workspace_units,
    issue_certificate,
)
from jezz.mcp_server import (
    JbcCatalogPayload,
    JezzWorkspaceGate,
    build_jezz_mcp_server,
    list_jbc_codes,
)
from jezz.methodology.dossier import DossierAuditReport, EngineeringDossier
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope

_PYPROJECT_TOML = textwrap.dedent(
    """\
    [project]
    name = "sample-pkg"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0", "pydantic>=2.14.4"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)

_CLEAN_CALC_PY = textwrap.dedent(
    """\
    import icontract
    from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

    @icontract.ensure(lambda amount, result: result == amount * 2)
    @jezz_callable
    def double_cents(amount: PositiveInt) -> NonNegativeInt:
        return amount * 2
    """
)

_CLEAN_TEST_PY = textwrap.dedent(
    """\
    from sample_pkg.calc import double_cents

    def test_double_cents_doubles_positive_amount() -> None:
        assert double_cents(25) == 50
    """
)


def _seed_clean_workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(_PYPROJECT_TOML, encoding="utf-8")
    pkg_dir = root / "src" / "sample_pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text('"""Sample package."""\n', encoding="utf-8")
    (pkg_dir / "calc.py").write_text(_CLEAN_CALC_PY, encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_calc.py").write_text(_CLEAN_TEST_PY, encoding="utf-8")
    return root


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
        timeout=10.0,
    )


def _init_git_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "inspector@example.com")
    _git(repo, "config", "user.name", "Building Inspector")


def _first_text_block(blocks: list[object]) -> str:
    for block in blocks:
        if isinstance(block, TextContent):
            return block.text
    return ""


def _build_sealed_dossier(repo: Path) -> EngineeringDossier:
    units = discover_workspace_units(repo)
    permit = CodePermit.full_book(permit_id="PERMIT-MCP")
    inspector = BuildingInspector.from_workspace(repo)
    attestations = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy --strict",
            passed=True,
            exit_code=0,
            checks_executed=2,
            summary="Success: no issues found in 2 source files",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=1,
            summary="1 passed in 0.05s",
        ),
    )
    report = inspector.inspect_final(units, attestations=attestations, permit=permit)
    cert = issue_certificate(report, artifact_digest=compute_units_digest(units))
    mandate = EngineeringMandate(
        mandate_id="MND-MCP-01",
        title="Verified cent doubling contract",
        objective="Implement verified double_cents arithmetic contract.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="double_cents returns strictly twice the positive input amount.",
                verification_symbol="test_double_cents_doubles_positive_amount",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="calc_scope",
                owned_files=frozenset({"src/sample_pkg/calc.py", "tests/test_calc.py"}),
                contract_symbols=("double_cents",),
            ),
        ),
    )
    graph = BehaviorGraph(
        name="mcp_greenfield_graph",
        entry_id="f",
        nodes=(
            BehaviorNode(id="f", kind=BehaviorKind.FRAME, label="Frame"),
            BehaviorNode(id="b", kind=BehaviorKind.BIND_CODES, label="Bind"),
            BehaviorNode(id="cp", kind=BehaviorKind.CHECKPOINT, label="Checkpoint"),
            BehaviorNode(id="sp", kind=BehaviorKind.SPECIFY, label="Specify"),
            BehaviorNode(id="ins", kind=BehaviorKind.INSTRUMENT, label="Instrument"),
            BehaviorNode(id="syn", kind=BehaviorKind.SYNTHESIZE, label="Synthesize"),
            BehaviorNode(id="vs", kind=BehaviorKind.VERIFY_STATIC, label="Static"),
            BehaviorNode(id="vd", kind=BehaviorKind.VERIFY_DYNAMIC, label="Dynamic"),
            BehaviorNode(id="cert", kind=BehaviorKind.CERTIFY, label="Certify"),
        ),
        edges=(
            BehaviorEdge(source="f", target="b"),
            BehaviorEdge(source="b", target="cp"),
            BehaviorEdge(source="cp", target="sp"),
            BehaviorEdge(source="sp", target="ins"),
            BehaviorEdge(source="ins", target="syn"),
            BehaviorEdge(source="syn", target="vs"),
            BehaviorEdge(source="vs", target="vd"),
            BehaviorEdge(source="vd", target="cert"),
        ),
    )
    return EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=graph,
        mutated_files=frozenset({"src/sample_pkg/calc.py", "tests/test_calc.py"}),
        inspection_report=report,
        certificate=cert,
    )


@pytest.mark.parametrize("connect_mode", ["auto", "legacy"])
async def test_mcp_server_advertises_seven_readonly_structured_tools(
    tmp_path: Path,
    connect_mode: str,
) -> None:
    repo = _seed_clean_workspace(tmp_path)
    server = build_jezz_mcp_server(workspace_root=repo)
    async with Client(server, mode=connect_mode) as client:
        listed = await client.list_tools()

    names = [tool.name for tool in listed.tools]
    assert names == [
        "jbc_list_codes",
        "jbc_get_code",
        "jbc_verify_behavior_graph",
        "jbc_inspect_workspace",
        "jbc_inspect_differential",
        "jbc_preview_source",
        "jbc_audit_dossier",
    ]
    for tool in listed.tools:
        assert tool.output_schema is not None
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False


async def test_mcp_list_and_get_code_provisions_and_unknown_code_error(tmp_path: Path) -> None:
    server = build_jezz_mcp_server(workspace_root=_seed_clean_workspace(tmp_path))
    async with Client(server, mode="legacy") as client:
        all_res = await client.call_tool("jbc_list_codes", {})
        cog_res = await client.call_tool("jbc_list_codes", {"article": "COG"})
        code_res = await client.call_tool("jbc_get_code", {"code_id": "jbc-cog-003"})
        bad_res = await client.call_tool("jbc_get_code", {"code_id": "JBC-DBC-999"})

    assert all_res.structured_content is not None
    catalog = JbcCatalogPayload.model_validate_json(_first_text_block(list(all_res.content)))
    assert catalog.edition == "1.1"
    assert catalog.total_matching == 30

    cog_catalog = JbcCatalogPayload.model_validate_json(_first_text_block(list(cog_res.content)))
    assert cog_catalog.total_matching == 5
    assert all(item.article == CodeArticle.COG for item in cog_catalog.provisions)

    provision = BuildingCode.model_validate_json(_first_text_block(list(code_res.content)))
    assert provision.code_id == "JBC-COG-003"
    assert len(provision.standard_refs) >= 1

    assert bad_res.is_error is True
    assert "Unknown BuildingCode 'JBC-DBC-999'" in _first_text_block(list(bad_res.content))


async def test_mcp_verify_behavior_graph_accepts_wire_json_lists(tmp_path: Path) -> None:
    server = build_jezz_mcp_server(workspace_root=_seed_clean_workspace(tmp_path))
    valid_wire = build_readonly_audit_graph().model_dump(mode="json")
    invalid_wire = {
        "name": "missing_governance",
        "entry_id": "f",
        "nodes": [
            {"id": "f", "kind": "frame", "label": "Frame"},
            {"id": "s", "kind": "synthesize", "label": "Mutate without permit"},
            {"id": "vs", "kind": "verify_static", "label": "Static"},
            {"id": "vd", "kind": "verify_dynamic", "label": "Dynamic"},
            {"id": "c", "kind": "certify", "label": "Certify"},
        ],
        "edges": [
            {"source": "f", "target": "s"},
            {"source": "s", "target": "vs"},
            {"source": "vs", "target": "vd"},
            {"source": "vd", "target": "c"},
        ],
    }
    async with Client(server, mode="legacy") as client:
        ok_res = await client.call_tool("jbc_verify_behavior_graph", {"graph": valid_wire})
        err_res = await client.call_tool("jbc_verify_behavior_graph", {"graph": invalid_wire})

    assert ok_res.is_error is False
    assert ok_res.structured_content is not None
    assert ok_res.structured_content["is_valid"] is True

    assert err_res.is_error is False
    assert err_res.structured_content is not None
    assert err_res.structured_content["is_valid"] is False
    violation_codes = {v["code"] for v in err_res.structured_content["violations"]}
    assert "unbound_codes" in violation_codes
    assert "missing_checkpoint" in violation_codes


async def test_mcp_inspect_workspace_enforces_permit_and_sunset_date(tmp_path: Path) -> None:
    repo = _seed_clean_workspace(tmp_path)
    noisy_src = _CLEAN_CALC_PY + '\n@jezz_callable\ndef debug_ping() -> None:\n    print("ping")\n'
    (repo / "src" / "sample_pkg" / "calc.py").write_text(noisy_src, encoding="utf-8")
    permit = CodePermit.full_book(
        permit_id="PERMIT-SUNSET",
        variances=(
            CodeVariance(
                variance_id="VAR-OBS-01",
                code_id="JBC-OBS-001",
                file_path="src/sample_pkg/calc.py",
                authorized_by="Principal Engineer",
                justification="Temporary diagnostic print pending telemetry migration.",
                expires_on=date(2026, 6, 1),
            ),
        ),
    )
    (repo / "permit.json").write_text(permit.model_dump_json(indent=2), encoding="utf-8")
    server = build_jezz_mcp_server(workspace_root=repo)

    async with Client(server, mode="legacy") as client:
        active_res = await client.call_tool(
            "jbc_inspect_workspace",
            {"permit_path": "permit.json", "as_of": "2026-06-01"},
        )
        expired_res = await client.call_tool(
            "jbc_inspect_workspace",
            {"permit_path": "permit.json", "as_of": "2026-06-02"},
        )

    active_report = InspectionReport.model_validate_json(
        _first_text_block(list(active_res.content))
    )
    expired_report = InspectionReport.model_validate_json(
        _first_text_block(list(expired_res.content))
    )
    assert active_report.ruling == OccupancyRuling.APPROVED_WITH_VARIANCES
    assert expired_report.ruling == OccupancyRuling.RED_TAG
    assert "Expired variance 'VAR-OBS-01'" in expired_report.findings[0].message


async def test_mcp_inspect_differential_gates_only_touched_files(tmp_path: Path) -> None:
    repo = _seed_clean_workspace(tmp_path)
    legacy_bad = "def legacy_untyped(x):\n    return x + 1\n"
    (repo / "src" / "sample_pkg" / "legacy.py").write_text(legacy_bad, encoding="utf-8")
    _init_git_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Baseline with legacy technical debt")

    (repo / "src" / "sample_pkg" / "fresh.py").write_text(_CLEAN_CALC_PY, encoding="utf-8")
    server = build_jezz_mcp_server(workspace_root=repo)

    async with Client(server, mode="legacy") as client:
        diff_res = await client.call_tool("jbc_inspect_differential", {})
        conflict_res = await client.call_tool(
            "jbc_inspect_differential",
            {"changed_since": "HEAD", "staged": True},
        )

    diff_report = DifferentialInspectionReport.model_validate_json(
        _first_text_block(list(diff_res.content))
    )
    assert diff_report.is_occupiable is True
    assert diff_report.inspected_unit_paths == ("src/sample_pkg/fresh.py",)
    assert diff_report.untouched_unit_count >= 1
    assert conflict_res.is_error is True


async def test_mcp_preview_source_catches_local_and_cross_module_violations_without_disk_write(
    tmp_path: Path,
) -> None:
    repo = _seed_clean_workspace(tmp_path)
    alpha_src = (
        "from sample_pkg import zebra\n"
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        "_INTERNAL_SCALE = 3\n\n"
        "@jezz_callable\n"
        "def call_zebra(v: PositiveInt) -> PositiveInt:\n"
        "    return zebra.scale_val(v)\n"
    )
    (repo / "src" / "sample_pkg" / "alpha.py").write_text(alpha_src, encoding="utf-8")
    candidate_rel = "src/sample_pkg/zebra.py"
    cyclic_and_private_src = (
        "from sample_pkg.alpha import _INTERNAL_SCALE\n"
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\n"
        "def scale_val(v: PositiveInt) -> PositiveInt:\n"
        "    return v * _INTERNAL_SCALE\n"
    )
    server = build_jezz_mcp_server(workspace_root=repo)

    async with Client(server, mode="legacy") as client:
        bad_preview = await client.call_tool(
            "jbc_preview_source",
            {"file_path": candidate_rel, "source_code": cyclic_and_private_src},
        )
        clean_preview = await client.call_tool(
            "jbc_preview_source",
            {"file_path": "src/sample_pkg/standalone.py", "source_code": _CLEAN_CALC_PY},
        )
        syntax_err = await client.call_tool(
            "jbc_preview_source",
            {"file_path": candidate_rel, "source_code": "def broken( -> int:\n    return 1\n"},
        )

    assert not (repo / candidate_rel).exists()
    bad_report = DifferentialInspectionReport.model_validate_json(
        _first_text_block(list(bad_preview.content))
    )
    assert not bad_report.is_occupiable
    cited_codes = {f.code_id for f in bad_report.report.unwaived_violations()}
    assert "JBC-COG-003" in cited_codes

    clean_report = DifferentialInspectionReport.model_validate_json(
        _first_text_block(list(clean_preview.content))
    )
    assert clean_report.is_occupiable is True
    assert syntax_err.is_error is True
    assert "SyntaxError" in _first_text_block(list(syntax_err.content))


async def test_mcp_preview_source_works_on_empty_greenfield_workspace(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT_TOML, encoding="utf-8")
    server = build_jezz_mcp_server(workspace_root=tmp_path)
    async with Client(server, mode="legacy") as client:
        res = await client.call_tool(
            "jbc_preview_source",
            {"file_path": "src/sample_pkg/calc.py", "source_code": _CLEAN_CALC_PY},
        )
        dict_literal_res = await client.call_tool(
            "jbc_preview_source",
            {"file_path": "src/sample_pkg/__init__.py", "source_code": "{}\n"},
        )
    report = DifferentialInspectionReport.model_validate_json(_first_text_block(list(res.content)))
    assert report.is_occupiable is True
    assert report.total_workspace_units == 1
    assert dict_literal_res.is_error is False


async def test_mcp_audit_dossier_supports_file_and_inline_json(tmp_path: Path) -> None:
    repo = _seed_clean_workspace(tmp_path)
    dossier = _build_sealed_dossier(repo)
    raw_json = dossier.model_dump_json(indent=2)
    (repo / "dossier.json").write_text(raw_json, encoding="utf-8")
    server = build_jezz_mcp_server(workspace_root=repo)

    async with Client(server, mode="legacy") as client:
        by_path = await client.call_tool("jbc_audit_dossier", {"dossier_path": "dossier.json"})
        by_json = await client.call_tool("jbc_audit_dossier", {"dossier_json": raw_json})
        neither = await client.call_tool("jbc_audit_dossier", {})

    assert by_path.structured_content is not None
    assert by_json.structured_content is not None
    path_audit = DossierAuditReport.model_validate_json(_first_text_block(list(by_path.content)))
    json_audit = DossierAuditReport.model_validate_json(_first_text_block(list(by_json.content)))
    assert path_audit.is_occupancy_sealed is True
    assert json_audit.is_occupancy_sealed is True
    assert neither.is_error is True


async def test_mcp_blocks_path_traversal_across_all_workspace_tools(tmp_path: Path) -> None:
    jail_root = _seed_clean_workspace(tmp_path / "jailed")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    server = build_jezz_mcp_server(workspace_root=jail_root)

    async with Client(server, mode="legacy") as client:
        esc_ws = await client.call_tool("jbc_inspect_workspace", {"workspace_subpath": ".."})
        esc_permit = await client.call_tool(
            "jbc_inspect_workspace",
            {"permit_path": "../outside.json"},
        )
        esc_preview = await client.call_tool(
            "jbc_preview_source",
            {"file_path": "../escape.py", "source_code": _CLEAN_CALC_PY},
        )
        esc_dossier = await client.call_tool(
            "jbc_audit_dossier",
            {"dossier_path": "../outside.json"},
        )

    for res in (esc_ws, esc_permit, esc_preview, esc_dossier):
        assert res.is_error is True
        assert "Path traversal blocked" in _first_text_block(list(res.content))


def test_cli_mcp_subcommand_invokes_stdio_server_and_validates_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_roots: list[Path | None] = []

    def _fake_stdio_runner(*, workspace_root: Path | None = None) -> None:
        captured_roots.append(workspace_root)

    monkeypatch.setattr("jezz.cli.run_stdio_mcp_server", _fake_stdio_runner)

    help_out = io.StringIO()
    help_err = io.StringIO()
    assert main(["mcp", "--help"], stdout=help_out, stderr=help_err) == 0
    assert "--root" in help_out.getvalue()

    run_out = io.StringIO()
    run_err = io.StringIO()
    assert main(["mcp", "--root", str(tmp_path)], stdout=run_out, stderr=run_err) == 0
    assert captured_roots == [tmp_path]

    gate_missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        JezzWorkspaceGate(workspace_root=gate_missing)


@settings(max_examples=40)
@given(
    article=st.one_of(st.none(), st.sampled_from(list(CodeArticle))),
    trigger=st.one_of(st.none(), st.sampled_from(list(CodeTrigger))),
)
def test_property_list_jbc_codes_matches_codebook_filter(
    article: CodeArticle | None,
    trigger: CodeTrigger | None,
) -> None:
    payload = list_jbc_codes(article=article, trigger=trigger)
    expected_ids = sorted(
        code.code_id
        for code in CODE_BOOK.values()
        if (article is None or code.article == article)
        and (trigger is None or trigger in code.triggers)
    )
    actual_ids = [item.code_id for item in payload.provisions]
    assert actual_ids == expected_ids
    assert payload.total_matching == len(expected_ids)


async def test_all_canonical_archetypes_roundtrip_over_mcp_wire(tmp_path: Path) -> None:
    server = build_jezz_mcp_server(workspace_root=_seed_clean_workspace(tmp_path))
    async with Client(server, mode="legacy") as client:
        for archetype in all_canonical_archetypes():
            direct = verify_behavior_graph(archetype)
            wire_res = await client.call_tool(
                "jbc_verify_behavior_graph",
                {"graph": archetype.model_dump(mode="json")},
            )
            assert wire_res.is_error is False
            assert wire_res.structured_content is not None
            assert wire_res.structured_content["is_valid"] == direct.is_valid
            assert len(wire_res.structured_content["entry_facts"]) == len(direct.entry_facts)
