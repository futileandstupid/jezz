"""Track 26: BlueprintGraph IR, Switchable A-1/E-1 CAD projections, and coarse-grained LVS."""

import io
import json
import textwrap
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from mcp import Client
from mcp.types import TextContent
from pydantic import ValidationError

from jezz.behaviors import build_greenfield_contract_first_graph
from jezz.cli import main
from jezz.codes import (
    BLUEPRINT_SCHEMA_VERSION,
    BlueprintGraph,
    BlueprintLvsReport,
    BuildingInspector,
    CodePermit,
    GateAttestation,
    GateKind,
    LvsBoundaryLayer,
    LvsDefectKind,
    ManifestDependencies,
    PermitProfile,
    RedlineDelta,
    RedlineOp,
    RiserKind,
    SupportedLanguage,
    WallFireRating,
    apply_blueprint_redlines,
    compute_units_digest,
    discover_workspace_units,
    extract_blueprint,
    issue_certificate,
    parse_go_unit,
    parse_rust_unit,
    parse_source_unit,
    verify_blueprint_lvs,
)
from jezz.guards import ContainedPath, read_bounded_text
from jezz.mcp_server import build_jezz_mcp_server, dispatch_reloaded_mcp_call
from jezz.methodology import (
    DomainInvariant,
    DossierViolationCode,
    EngineeringDossier,
    EngineeringMandate,
    ModuleWriteScope,
    audit_pce_dossier,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def _seed_two_room_workspace(repo: Path) -> None:
    _write_rel(
        repo,
        "pyproject.toml",
        textwrap.dedent(
            """\
            [project]
            name = "grid-app"
            version = "0.1.0"
            dependencies = ["jezz>=0.1.0", "pydantic>=2.14.4"]
            """
        ),
    )
    _write_rel(repo, "src/grid_app/__init__.py", '"""Grid root."""\n')
    _write_rel(
        repo,
        "src/grid_app/core/__init__.py",
        'from grid_app.core.engine import compute_load\n\n__all__ = ["compute_load"]\n',
    )
    _write_rel(
        repo,
        "src/grid_app/core/engine.py",
        textwrap.dedent(
            """\
            from jezz.guards import PositiveInt, jezz_callable


            @jezz_callable
            def compute_load(units: PositiveInt) -> PositiveInt:
                return units * 2
            """
        ),
    )
    _write_rel(
        repo,
        "src/grid_app/api/__init__.py",
        'from grid_app.api.routes import serve_load\n\n__all__ = ["serve_load"]\n',
    )
    _write_rel(
        repo,
        "src/grid_app/api/routes.py",
        textwrap.dedent(
            """\
            from grid_app.core import compute_load
            from jezz.guards import PositiveInt, jezz_callable


            @jezz_callable
            def serve_load(units: PositiveInt) -> PositiveInt:
                return compute_load(units)
            """
        ),
    )


def test_self_hosted_polyglot_blueprint_extraction_and_tamper_seal() -> None:
    """INV-026-01: extract_blueprint is deterministic across Python + TS and seals its netlist."""
    units = discover_workspace_units(REPO_ROOT)
    bp1 = extract_blueprint(
        units,
        title="Jezz v0.5.0 Self-Hosted",
        invariant_bindings=(("INV-026-01", "extract_blueprint"),),
    )
    bp2 = extract_blueprint(
        units,
        title="Jezz v0.5.0 Self-Hosted",
        invariant_bindings=(("INV-026-01", "extract_blueprint"),),
    )
    assert bp1.schema_version == BLUEPRINT_SCHEMA_VERSION
    assert bp1 == bp2
    assert bp1.netlist_digest == bp2.netlist_digest

    by_room = {r.room_id: r for r in bp1.rooms}
    assert {
        "jezz/agents",
        "jezz/behaviors",
        "jezz/codes",
        "jezz/evals",
        "jezz/methodology",
        "packages/jezz-blueprint",
    } <= set(by_room)
    ts_room = by_room["packages/jezz-blueprint"]
    assert ts_room.language == SupportedLanguage.TYPESCRIPT
    assert ts_room.wall_rating == WallFireRating.WORKSPACE_FIREWALL

    ts_risers = {
        (er.kind, er.target_name)
        for er in bp1.external_risers
        if er.room_id == "packages/jezz-blueprint"
    }
    assert (RiserKind.EXTERNAL_PACKAGE, "elkjs") in ts_risers
    assert (RiserKind.EXTERNAL_PACKAGE, "zod") in ts_risers
    assert any(ip.invariant_id == "INV-026-01" for ip in bp1.invariant_pins)

    md = bp1.to_markdown()
    assert "Sheet A-1" in md
    assert "Sheet E-1" in md

    raw_obj = json.loads(bp1.model_dump_json())
    raw_obj["netlist_digest"] = "sha256:" + ("0" * 64)
    with pytest.raises(ValidationError, match="netlist_digest"):
        BlueprintGraph.model_validate_json(json.dumps(raw_obj))


def test_polyglot_rust_and_go_blueprint_risers_and_barrels() -> None:
    """INV-026-01: Rust crate barrels and Go package rooms classify external I/O risers cleanly."""
    rs_lib = parse_rust_unit(
        "crates/vault/src/lib.rs",
        "use std::process::Command;\nuse reqwest::Client;\npub use crate::seal::{seal_blob};\n",
    )
    rs_mod = parse_rust_unit(
        "crates/vault/src/seal.rs",
        "use sqlx::PgPool;\npub fn seal_blob(n: u32) -> Option<u32> { n.checked_add(1) }\n",
    )
    go_pkg = parse_go_unit(
        "pkg/gateway/handler.go",
        'package gateway\nimport (\n  "net/http"\n  "database/sql"\n  "github.com/acme/otel"\n)\n'
        "func HandlePing(code int64) int64 { if code < 0 { return 0 }; return code }\n",
    )
    bp = extract_blueprint((rs_lib, rs_mod, go_pkg), title="Polyglot Risers")
    rooms = {r.room_id: r for r in bp.rooms}
    assert "crates/vault" in rooms
    assert "pkg/gateway" in rooms

    riser_kinds = {(er.room_id, er.kind, er.target_name) for er in bp.external_risers}
    assert ("crates/vault", RiserKind.SUBPROCESS_IO, "std::process::Command") in riser_kinds or any(
        er.room_id == "crates/vault" and er.kind == RiserKind.SUBPROCESS_IO
        for er in bp.external_risers
    )
    assert ("crates/vault", RiserKind.NETWORK_IO, "reqwest") in riser_kinds
    assert ("crates/vault", RiserKind.DATABASE_IO, "sqlx") in riser_kinds
    assert ("pkg/gateway", RiserKind.NETWORK_IO, "net/http") in riser_kinds
    assert ("pkg/gateway", RiserKind.DATABASE_IO, "database/sql") in riser_kinds
    assert ("pkg/gateway", RiserKind.EXTERNAL_PACKAGE, "github.com/acme/otel") in riser_kinds


def test_redline_algebra_and_lvs_open_and_short_circuit_surfaces(tmp_path: Path) -> None:
    """INV-026-03: apply_blueprint_redlines and verify_blueprint_lvs catch all boundary defects."""
    _seed_two_room_workspace(tmp_path)
    base_units = discover_workspace_units(tmp_path)
    baseline = extract_blueprint(base_units, title="Grid Baseline")

    deltas = (
        RedlineDelta(op=RedlineOp.ADD_ROOM, room_id="grid_app/audit"),
        RedlineDelta(
            op=RedlineOp.ADD_PORT_PIN,
            room_id="grid_app/audit",
            symbol_name="record_event",
        ),
        RedlineDelta(
            op=RedlineOp.ADD_BUS,
            room_id="grid_app/api",
            target_room="grid_app/audit",
            symbol_name="record_event",
        ),
        RedlineDelta(
            op=RedlineOp.ADD_EXTERNAL_RISER,
            room_id="grid_app/audit",
            riser_kind=RiserKind.DATABASE_IO,
            symbol_name="sqlite3",
        ),
        RedlineDelta(
            op=RedlineOp.BIND_INVARIANT_PIN,
            room_id="grid_app/core",
            invariant_id="INV-026-03",
            symbol_name="compute_load",
        ),
    )
    target = apply_blueprint_redlines(baseline, deltas, title="Grid Target v2")
    open_rep = verify_blueprint_lvs(baseline, target)
    assert open_rep.is_lvs_clean is False
    assert open_rep.open_circuit_count >= 5
    layers = {d.layer for d in open_rep.defects if d.kind == LvsDefectKind.OPEN_CIRCUIT}
    assert layers == {
        LvsBoundaryLayer.ROOM_WALL,
        LvsBoundaryLayer.PERIMETER_PORT,
        LvsBoundaryLayer.CROSS_ROOM_BUS,
        LvsBoundaryLayer.EXTERNAL_RISER,
        LvsBoundaryLayer.INVARIANT_PIN,
    }
    assert "LVS_FAILED" in open_rep.to_markdown()

    demolished_target = apply_blueprint_redlines(
        baseline,
        (
            RedlineDelta(
                op=RedlineOp.REMOVE_BUS,
                room_id="grid_app/api",
                target_room="grid_app/core",
            ),
        ),
    )
    rogue_units = (
        *base_units,
        parse_source_unit(
            "src/grid_app/rogue/leak.py",
            "import subprocess\nfrom grid_app.api import serve_load\n"
            "def unguarded(x: str) -> str:\n    return x\n",
        ),
        parse_source_unit(
            "src/grid_app/core/backedge.py",
            "from grid_app.api import serve_load\n"
            "from jezz.guards import PositiveInt, jezz_callable\n"
            "@jezz_callable\n"
            "def loop_back(x: PositiveInt) -> PositiveInt:\n"
            "    return serve_load(x)\n",
        ),
    )
    rogue_actual = extract_blueprint(rogue_units)
    short_rep = verify_blueprint_lvs(rogue_actual, demolished_target, units=rogue_units)
    assert short_rep.is_lvs_clean is False
    assert short_rep.short_circuit_count >= 4
    short_msgs = " | ".join(d.message for d in short_rep.defects)
    assert "Demolished bus" in short_msgs
    assert "Undeclared compartment room" in short_msgs
    assert "Undeclared external riser/sink 'subprocess'" in short_msgs
    assert "Cyclic cross-room" in short_msgs

    with pytest.raises(ValueError, match="unknown room"):
        apply_blueprint_redlines(
            baseline,
            (RedlineDelta(op=RedlineOp.ADD_PORT_PIN, room_id="missing/room", symbol_name="fn"),),
        )


@settings(max_examples=30)
@given(
    private_helpers=st.lists(
        st.integers(min_value=1, max_value=20),
        min_size=1,
        max_size=5,
        unique=True,
    ),
    internal_modules=st.lists(
        st.integers(min_value=1, max_value=12),
        min_size=0,
        max_size=4,
        unique=True,
    ),
)
def test_property_interior_refactoring_leaves_netlist_and_lvs_invariant(
    private_helpers: list[int],
    internal_modules: list[int],
) -> None:
    """INV-026-03: Arbitrary interior helper functions/modules preserve netlist digest & LVS."""
    core_init = parse_source_unit(
        "src/app/core/__init__.py",
        'from app.core.public_api import eval_spec\n\n__all__ = ["eval_spec"]\n',
    )
    helper_defs = "\n\n".join(
        f"def _interior_step_{idx}(v: int) -> int:\n    return v + {idx}" for idx in private_helpers
    )
    helper_sum = " + ".join(f"_interior_step_{idx}(x)" for idx in private_helpers)
    core_pub = parse_source_unit(
        "src/app/core/public_api.py",
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        f"{helper_defs}\n\n"
        "@jezz_callable\n"
        f"def eval_spec(x: PositiveInt) -> int:\n    return {helper_sum}\n",
    )
    extra_internal = tuple(
        parse_source_unit(
            f"src/app/core/algo_{m_idx}.py",
            "from jezz.guards import PositiveInt, jezz_callable\n\n"
            "@jezz_callable\n"
            f"def internal_algo_{m_idx}(n: PositiveInt) -> PositiveInt:\n    return n\n",
        )
        for m_idx in internal_modules
    )
    api_init = parse_source_unit(
        "src/app/api/__init__.py",
        'from app.api.entry import handle_req\n\n__all__ = ["handle_req"]\n',
    )
    api_entry = parse_source_unit(
        "src/app/api/entry.py",
        "from app.core import eval_spec\nfrom jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\n"
        "def handle_req(x: PositiveInt) -> int:\n    return eval_spec(x)\n",
    )

    baseline_units = (
        core_init,
        parse_source_unit(
            "src/app/core/public_api.py",
            "from jezz.guards import PositiveInt, jezz_callable\n\n"
            "@jezz_callable\ndef eval_spec(x: PositiveInt) -> int:\n    return x\n",
        ),
        api_init,
        api_entry,
    )
    bindings = (("INV-026-03", "eval_spec"),)
    target_bp = extract_blueprint(baseline_units, invariant_bindings=bindings)
    mutated_units = (core_init, core_pub, *extra_internal, api_init, api_entry)
    actual_bp = extract_blueprint(mutated_units, invariant_bindings=bindings)

    assert actual_bp.netlist_digest == target_bp.netlist_digest
    lvs = verify_blueprint_lvs(actual_bp, target_bp, units=mutated_units)
    assert lvs.is_lvs_clean is True
    assert lvs.digest_exact_match is True
    assert lvs.defects == ()


def test_cli_jezz_blueprint_projections_and_lvs_exit_codes(tmp_path: Path) -> None:
    """INV-026-02 & INV-026-03: jezz blueprint renders A-1/E-1/HTML CAD views and gates LVS."""
    _seed_two_room_workspace(tmp_path)
    redlines_path = _write_rel(
        tmp_path,
        "redlines.json",
        json.dumps(
            [
                {
                    "op": "add_room",
                    "room_id": "grid_app/telemetry",
                }
            ]
        ),
    )

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc_render = main(
        [
            "blueprint",
            str(tmp_path),
            "--title",
            "Grid CAD Sheet",
            "--redlines",
            str(redlines_path),
            "--write-json",
            "out/target_bp.json",
            "--write-html",
            "out/viewer.html",
            "--write-svg-a1",
            "out/a1.svg",
            "--write-svg-e1",
            "out/e1.svg",
            "--format",
            "markdown",
        ],
        stdout=out_buf,
        stderr=err_buf,
        stdin=io.StringIO(""),
    )
    assert rc_render == 0
    assert "Grid CAD Sheet" in out_buf.getvalue()

    svg_a1 = read_bounded_text(ContainedPath.from_parts(tmp_path, "out/a1.svg"))
    svg_e1 = read_bounded_text(ContainedPath.from_parts(tmp_path, "out/e1.svg"))
    html_doc = read_bounded_text(ContainedPath.from_parts(tmp_path, "out/viewer.html"))
    assert 'data-cad-sheet="A-1"' in svg_a1
    assert "OCCUPANCY" in svg_a1
    assert 'data-cad-role="title-block"' in svg_a1
    assert 'data-cad-sheet="E-1"' in svg_e1
    assert 'data-cad-layer="breakers"' in svg_e1
    assert "Sheet A-1 (Floor Plan)" in html_doc
    assert "Sheet E-1 (Wiring Schematic)" in html_doc
    assert "Export .svg" in html_doc

    fail_lvs_rc = main(
        [
            "blueprint",
            str(tmp_path),
            "--verify-target",
            str(ContainedPath.from_parts(tmp_path, "out/target_bp.json").resolved_path),
            "--format",
            "json",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        stdin=io.StringIO(""),
    )
    assert fail_lvs_rc == 1

    base_out = io.StringIO()
    assert (
        main(
            ["blueprint", str(tmp_path), "--write-json", "out/clean_bp.json", "--format", "svg-e1"],
            stdout=base_out,
            stderr=io.StringIO(),
            stdin=io.StringIO(""),
        )
        == 0
    )
    assert 'data-cad-sheet="E-1"' in base_out.getvalue()

    pass_lvs_rc = main(
        [
            "blueprint",
            str(tmp_path),
            "--verify-target",
            "out/clean_bp.json",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        stdin=io.StringIO(""),
    )
    assert pass_lvs_rc == 0


async def test_mcp_blueprint_tools_and_hot_reload_dispatch(tmp_path: Path) -> None:
    """INV-026-01 & INV-026-03: MCP jbc_extract_blueprint & jbc_verify_blueprint_lvs parity."""
    _seed_two_room_workspace(tmp_path)
    server = build_jezz_mcp_server(workspace_root=tmp_path)

    async with Client(server, mode="legacy") as client:
        extracted = await client.call_tool(
            "jbc_extract_blueprint",
            {
                "title": "MCP Grid Blueprint",
                "invariant_bindings": [["INV-026-01", "compute_load"]],
            },
        )
        bp = BlueprintGraph.model_validate_json(_first_text(list(extracted.content)))
        _write_rel(tmp_path, "blueprint.json", bp.model_dump_json(indent=2))

        redlined = await client.call_tool(
            "jbc_extract_blueprint",
            {
                "redlines_json": [{"op": "add_room", "room_id": "grid_app/worker"}],
            },
        )
        bp_red = BlueprintGraph.model_validate_json(_first_text(list(redlined.content)))

        lvs_ok = await client.call_tool(
            "jbc_verify_blueprint_lvs",
            {"target_blueprint_path": "blueprint.json"},
        )
        lvs_fail = await client.call_tool(
            "jbc_verify_blueprint_lvs",
            {"target_blueprint_json": bp_red.model_dump(mode="json")},
        )
        bad_args = await client.call_tool("jbc_verify_blueprint_lvs", {})

    assert bad_args.is_error is True
    assert BlueprintLvsReport.model_validate_json(_first_text(list(lvs_ok.content))).is_lvs_clean
    fail_rep = BlueprintLvsReport.model_validate_json(_first_text(list(lvs_fail.content)))
    assert fail_rep.is_lvs_clean is False
    assert fail_rep.open_circuit_count == 1

    reloaded_bp_json = dispatch_reloaded_mcp_call(
        tmp_path,
        "jbc_extract_blueprint",
        json.dumps({"blueprint_extract_args": {"title": "Reloaded BP"}}),
    )
    assert BlueprintGraph.model_validate_json(reloaded_bp_json).title == "Reloaded BP"

    reloaded_lvs_json = dispatch_reloaded_mcp_call(
        tmp_path,
        "jbc_verify_blueprint_lvs",
        json.dumps({"blueprint_lvs_args": {"target_blueprint_path": "blueprint.json"}}),
    )
    assert BlueprintLvsReport.model_validate_json(reloaded_lvs_json).is_lvs_clean is True


def test_certificate_dual_seal_and_dossier_blueprint_lvs_gate(tmp_path: Path) -> None:
    """INV-026-03: CertificateOfOccupancy dual seal & audit_pce_dossier enforce Blueprint LVS."""
    _seed_two_room_workspace(tmp_path)
    _write_rel(
        tmp_path,
        "tests/test_grid.py",
        "from grid_app.api import serve_load\n\n"
        "def test_serve_load() -> None:\n    assert serve_load(3) == 6\n",
    )
    units = discover_workspace_units(tmp_path)
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK26-DOSSIER",
        title="Grid load boundary contract",
        objective="Expose guarded load calculation across core and api compartments.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-2603",
                statement="serve_load delegates strictly through guarded compute_load port.",
                verification_symbol="test_serve_load",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="grid_scope",
                owned_files=frozenset(
                    {
                        "src/grid_app/__init__.py",
                        "src/grid_app/core/__init__.py",
                        "src/grid_app/core/engine.py",
                        "src/grid_app/api/__init__.py",
                        "src/grid_app/api/routes.py",
                        "tests/test_grid.py",
                    }
                ),
                contract_symbols=("compute_load", "serve_load"),
            ),
        ),
    )
    permit = CodePermit.full_book("PERMIT-TRACK26", profile=PermitProfile.STANDARD)
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"jezz", "pydantic"}),
            dev_roots=frozenset({"pytest"}),
            workspace_roots=frozenset({"grid_app"}),
        )
    )
    atts = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy",
            passed=True,
            exit_code=0,
            checks_executed=6,
            summary="Success: no issues found",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=1,
            summary="1 passed",
        ),
    )
    report = inspector.inspect_final(
        units,
        permit=permit,
        attestations=atts,
        reference_date=date(2026, 11, 24),
    )
    assert report.is_occupiable

    prod_units = tuple(u for u in units if not u.is_test_file)
    target_bp = extract_blueprint(prod_units, profile=permit.profile)
    art_digest = compute_units_digest(units)
    cert = issue_certificate(
        report,
        artifact_digest=art_digest,
        blueprint_netlist_digest=target_bp.netlist_digest,
    )
    assert cert.verify_seal() is True

    tampered_cert_obj = json.loads(cert.model_dump_json())
    tampered_cert_obj["blueprint_netlist_digest"] = "sha256:" + ("f" * 64)
    tampered_cert = cert.model_validate_json(json.dumps(tampered_cert_obj))
    assert tampered_cert.verify_seal() is False

    dossier = EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        inspection_report=report,
        certificate=cert,
        target_blueprint=target_bp,
        mutated_files=frozenset({"src/grid_app/api/routes.py", "tests/test_grid.py"}),
    )
    clean_audit = audit_pce_dossier(dossier, units)
    assert clean_audit.is_occupancy_sealed is True

    stale_seal_cert = issue_certificate(
        report,
        artifact_digest=art_digest,
        blueprint_netlist_digest="sha256:" + ("a" * 64),
    )
    stale_audit = audit_pce_dossier(
        dossier.model_copy(update={"certificate": stale_seal_cert}), units
    )
    assert stale_audit.is_occupancy_sealed is False
    assert any(
        v.code == DossierViolationCode.INVALID_OCCUPANCY_SEAL for v in stale_audit.violations
    )

    unfulfilled_target = apply_blueprint_redlines(
        target_bp,
        (RedlineDelta(op=RedlineOp.ADD_ROOM, room_id="grid_app/missing"),),
    )
    bad_lvs_dossier = dossier.model_copy(update={"target_blueprint": unfulfilled_target})
    bad_audit = audit_pce_dossier(bad_lvs_dossier, units)
    assert bad_audit.is_occupancy_sealed is False
    assert any(v.code == DossierViolationCode.BLUEPRINT_LVS_MISMATCH for v in bad_audit.violations)
