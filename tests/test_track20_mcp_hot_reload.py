"""Track 20 statutory falsification suite: live MCP daemon hot-reload & syntax-fault survival."""

import shutil
from pathlib import Path

from mcp import Client
from mcp.types import TextContent

import jezz
from jezz.codes import BuildingCode, DifferentialInspectionReport, OccupancyRuling
from jezz.mcp_reload import HotReloadSupervisor, compute_package_fingerprint
from jezz.mcp_server import build_jezz_mcp_server


def _first_text(blocks: list[object]) -> str:
    for block in blocks:
        if isinstance(block, TextContent):
            return block.text
    return ""


def _copy_isolated_jezz_tree(dst_parent: Path) -> Path:
    src_pkg = Path(jezz.__file__).resolve().parent
    dst_pkg = dst_parent / "jezz"
    shutil.copytree(src_pkg, dst_pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dst_pkg


def _seed_target_workspace(ws_root: Path) -> Path:
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / "pyproject.toml").write_text(
        '[project]\nname = "demo-app"\nversion = "0.1.0"\n'
        'dependencies = ["jezz>=0.1.0", "pydantic>=2.14.4"]\n',
        encoding="utf-8",
    )
    app_dir = ws_root / "src" / "demo_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").write_text('"""Demo package."""\n', encoding="utf-8")
    return ws_root


_REAL_COG_003_TITLE = "Acyclic Module Topology, Wildcard Ban & Sealed Private Enclosures"


async def test_live_mcp_hot_reloads_on_package_source_mutation(tmp_path: Path) -> None:
    """INV-020-01: Open MCP session reflects mutated jezz source code without reconnecting."""
    pkg_copy = _copy_isolated_jezz_tree(tmp_path / "isolated_site")
    ws_root = _seed_target_workspace(tmp_path / "target_repo")
    fp_before = compute_package_fingerprint(pkg_copy)

    server = build_jezz_mcp_server(workspace_root=ws_root, watched_package_root=pkg_copy)
    codebook_file = pkg_copy / "codes" / "codebook.py"
    original_codebook = codebook_file.read_text(encoding="utf-8")
    candidate_buf = (
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\n"
        "def echo_val(val: PositiveInt) -> PositiveInt:\n"
        "    return val\n"
    )

    async with Client(server, mode="legacy") as client:
        res_v1 = await client.call_tool("jbc_get_code", {"code_id": "JBC-COG-003"})
        code_v1 = BuildingCode.model_validate_json(_first_text(list(res_v1.content)))
        assert code_v1.title == _REAL_COG_003_TITLE

        mutated_v2 = original_codebook.replace(
            f'title="{_REAL_COG_003_TITLE}"',
            'title="Acyclic Graph [HotReload-V2]"',
            1,
        )
        assert mutated_v2 != original_codebook
        codebook_file.write_text(mutated_v2, encoding="utf-8")
        assert compute_package_fingerprint(pkg_copy) != fp_before

        res_v2 = await client.call_tool("jbc_get_code", {"code_id": "JBC-COG-003"})
        code_v2 = BuildingCode.model_validate_json(_first_text(list(res_v2.content)))
        assert code_v2.title == "Acyclic Graph [HotReload-V2]"

        preview_res = await client.call_tool(
            "jbc_preview_source",
            {"file_path": "src/demo_app/core.py", "source_code": candidate_buf},
        )
        preview_report = DifferentialInspectionReport.model_validate_json(
            _first_text(list(preview_res.content))
        )
        assert preview_report.report.ruling == OccupancyRuling.APPROVED


async def test_live_mcp_survives_transient_syntax_error_and_recovers(tmp_path: Path) -> None:
    """INV-020-02: Mid-edit SyntaxError falls back to last healthy snapshot, then recovers."""
    pkg_copy = _copy_isolated_jezz_tree(tmp_path / "fault_site")
    ws_root = _seed_target_workspace(tmp_path / "fault_repo")
    supervisor = HotReloadSupervisor(workspace_root=ws_root, package_root=pkg_copy)
    server = build_jezz_mcp_server(
        workspace_root=ws_root,
        watched_package_root=pkg_copy,
        supervisor=supervisor,
    )
    codebook_file = pkg_copy / "codes" / "codebook.py"
    original_codebook = codebook_file.read_text(encoding="utf-8")

    async with Client(server, mode="legacy") as client:
        v2_src = original_codebook.replace(
            f'title="{_REAL_COG_003_TITLE}"',
            'title="Healthy Snapshot V2"',
            1,
        )
        assert v2_src != original_codebook
        codebook_file.write_text(v2_src, encoding="utf-8")
        ok_v2 = await client.call_tool("jbc_get_code", {"code_id": "JBC-COG-003"})
        assert (
            BuildingCode.model_validate_json(_first_text(list(ok_v2.content))).title
            == "Healthy Snapshot V2"
        )
        assert supervisor.reload_generation == 1
        assert supervisor.last_reload_error == ""

        codebook_file.write_text("def mid_edit_syntax_error(:\n", encoding="utf-8")
        during_fault = await client.call_tool("jbc_get_code", {"code_id": "JBC-COG-003"})
        assert during_fault.is_error is False
        assert (
            BuildingCode.model_validate_json(_first_text(list(during_fault.content))).title
            == "Healthy Snapshot V2"
        )
        assert "SyntaxError" in supervisor.last_reload_error

        v3_src = original_codebook.replace(
            f'title="{_REAL_COG_003_TITLE}"',
            'title="Recovered Live V3"',
            1,
        )
        codebook_file.write_text(v3_src, encoding="utf-8")
        recovered_v3 = await client.call_tool("jbc_get_code", {"code_id": "JBC-COG-003"})
        assert (
            BuildingCode.model_validate_json(_first_text(list(recovered_v3.content))).title
            == "Recovered Live V3"
        )
        assert supervisor.reload_generation == 2
        assert supervisor.last_reload_error == ""
