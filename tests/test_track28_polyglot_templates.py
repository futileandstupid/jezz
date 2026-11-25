"""Track 28: Polyglot starter templates, ``jezz init --scaffold``, and Action LVS/Z3 gates."""

import io
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jezz.cli import main as run_cli
from jezz.codes import parse_go_unit
from jezz.codes.blueprint import BlueprintGraph, WallFireRating
from jezz.codes.compartment import check_cog_006
from jezz.codes.smt import SmtProofReport
from jezz.guards import ContainedPath, read_bounded_text
from jezz.plugin import (
    ScaffoldTemplate,
    WorkspaceInitReport,
    resolve_scaffold_template_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_HOST_ROOT_TOKENS = ("/" + "Users" + "/", "/" + "home" + "/", "C:" + "\\\\")


def _extract_composite_gate_script(action_text: str) -> str:
    marker = "      run: |\n"
    idx = action_text.find(marker)
    assert idx != -1, "Missing composite gate run block in action.yml"
    raw_block = action_text[idx + len(marker) :]
    lines = [line[8:] if line.startswith("        ") else line for line in raw_block.splitlines()]
    return "\n".join(lines) + "\n"


def test_all_four_starter_templates_pass_jbc_smt_and_blueprint_lvs() -> None:
    """INV-028-01: Every canonical template passes inspect, Z3 prove, and Blueprint LVS."""
    for template in ScaffoldTemplate:
        tpl_dir = resolve_scaffold_template_dir(template)
        assert tpl_dir.is_dir()

        out_ins, err_ins = io.StringIO(), io.StringIO()
        assert run_cli(("inspect", str(tpl_dir)), stdout=out_ins, stderr=err_ins) == 0, (
            f"{template} failed inspect:\n{out_ins.getvalue()}\n{err_ins.getvalue()}"
        )

        out_prv, err_prv = io.StringIO(), io.StringIO()
        assert (
            run_cli(
                ("prove", str(tpl_dir), "--require-deductive", "--format", "json"),
                stdout=out_prv,
                stderr=err_prv,
            )
            == 0
        ), f"{template} failed prove:\n{out_prv.getvalue()}\n{err_prv.getvalue()}"
        proof_rep = SmtProofReport.model_validate_json(out_prv.getvalue())
        assert proof_rep.proved_deductive_count >= 1
        assert proof_rep.counterexample_count == 0
        assert proof_rep.vacuous_count == 0

        bp_file = ContainedPath.from_parts(tpl_dir, "blueprint.json")
        out_bp, err_bp = io.StringIO(), io.StringIO()
        assert (
            run_cli(
                ("blueprint", str(tpl_dir), "--verify-target", str(bp_file.resolved_path)),
                stdout=out_bp,
                stderr=err_bp,
            )
            == 0
        ), f"{template} failed LVS:\n{out_bp.getvalue()}\n{err_bp.getvalue()}"

        bp_graph = BlueprintGraph.model_validate_json(read_bounded_text(bp_file))
        comp_rooms = [r for r in bp_graph.rooms if r.wall_rating == WallFireRating.COMPARTMENT_WALL]
        assert len(comp_rooms) >= 2
        assert any("domain" in r.room_id for r in comp_rooms)
        assert any("gateway" in r.room_id for r in comp_rooms)
        assert any(
            "gateway" in b.source_room and "domain" in b.target_room and b.has_breaker_guard
            for b in bp_graph.buses
        )

        skip_dirs = {".ruff_cache", ".pytest_cache", ".mypy_cache", "__pycache__", "node_modules"}
        for file_path in sorted(tpl_dir.rglob("*")):
            if file_path.is_file():
                rel_parts = file_path.relative_to(tpl_dir).parts
                if any(part in skip_dirs for part in rel_parts):
                    continue
                rel_posix = "/".join(rel_parts)
                txt = read_bounded_text(ContainedPath.from_parts(tpl_dir, rel_posix))
                assert not any(tok in txt for tok in _HOST_ROOT_TOKENS), (
                    f"Hardcoded host path token leaked in {template}:{rel_posix}"
                )


def test_cli_init_scaffold_materializes_all_four_languages(tmp_path: Path) -> None:
    """INV-028-02: ``jezz init <dir> --scaffold <lang>`` bootstraps clean turnkey workspaces."""
    for template in ScaffoldTemplate:
        dest = ContainedPath.from_parts(tmp_path, f"scaffold_{template.value}").resolved_path
        out_buf, err_buf = io.StringIO(), io.StringIO()
        rc = run_cli(
            ("init", str(dest), "--scaffold", template.value, "--format", "json"),
            stdout=out_buf,
            stderr=err_buf,
        )
        assert rc == 0, err_buf.getvalue()
        report = WorkspaceInitReport.model_validate_json(out_buf.getvalue())
        assert report.scaffold_applied == template
        assert len(report.written_files) >= 8
        assert ContainedPath.from_parts(
            dest, ".agents/plugins/jezz/skills/pce/SKILL.md"
        ).resolved_path.is_file()
        assert ContainedPath.from_parts(
            dest, ".github/workflows/jbc-gate.yml"
        ).resolved_path.is_file()

        # Re-running without --overwrite preserves existing files
        out_second = io.StringIO()
        assert (
            run_cli(
                ("init", str(dest), "--scaffold", template.value, "--format", "json"),
                stdout=out_second,
                stderr=io.StringIO(),
            )
            == 0
        )
        second_rep = WorkspaceInitReport.model_validate_json(out_second.getvalue())
        assert len(second_rep.skipped_files) >= 8

        # Materialized workspace passes JBC inspection immediately
        assert run_cli(("inspect", str(dest)), stdout=io.StringIO(), stderr=io.StringIO()) == 0

    # Execute native Python Hypothesis suite on materialized python scaffold
    py_dest = ContainedPath.from_parts(tmp_path, "scaffold_python").resolved_path
    py_src = ContainedPath.from_parts(py_dest, "src").resolved_path
    py_env = {**os.environ, "PYTHONPATH": str(py_src)}
    py_proc = subprocess.run(
        (sys.executable, "-m", "pytest", "-q"),
        cwd=py_dest,
        env=py_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert py_proc.returncode == 0, f"{py_proc.stdout}\n{py_proc.stderr}"

    # Execute native Go quick.Check suite on materialized go scaffold when Go is installed
    go_bin = shutil.which("go")
    if go_bin is not None:
        go_dest = ContainedPath.from_parts(tmp_path, "scaffold_go").resolved_path
        go_proc = subprocess.run(
            (go_bin, "test", "./..."),
            cwd=go_dest,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert go_proc.returncode == 0, f"{go_proc.stdout}\n{go_proc.stderr}"


def test_composite_action_supports_root_commit_blueprint_lvs_and_z3_prove(
    tmp_path: Path,
) -> None:
    """Verify action.yml handles 1-commit template repos, blueprint-target, and prove gates."""
    ws = ContainedPath.from_parts(tmp_path, "go_one_commit").resolved_path
    assert (
        run_cli(
            ("init", str(ws), "--scaffold", "go", "--plugin-scope", "none"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == 0
    )
    for git_args in (
        ("init", "-b", "main"),
        ("config", "user.email", "ci@jezz.local"),
        ("config", "user.name", "Jezz CI"),
        ("add", "."),
        ("commit", "-m", "feat: initial template commit"),
    ):
        subprocess.run(
            ("git", "-C", str(ws), *git_args),
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )

    action_txt = read_bounded_text(ContainedPath.from_parts(REPO_ROOT, "action.yml"))
    script_file = ContainedPath.from_parts(tmp_path, "run_gate.sh").resolved_path
    script_file.write_text(_extract_composite_gate_script(action_txt), encoding="utf-8")

    gh_out = ContainedPath.from_parts(tmp_path, "gh_output.txt").resolved_path
    gh_sum = ContainedPath.from_parts(tmp_path, "gh_summary.md").resolved_path
    gh_out.write_text("", encoding="utf-8")
    gh_sum.write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "JEZZ_ACTION_PATH": str(REPO_ROOT),
        "JEZZ_PYTHON_VERSION": "3.14",
        "JEZZ_WORKDIR": str(ws),
        "JEZZ_MODE": "differential",
        "JEZZ_BASE_REF": "",
        "JEZZ_EVENT_NAME": "push",
        "JEZZ_PR_BASE_REF": "",
        "JEZZ_LANGUAGES": "go",
        "JEZZ_PERMIT": "",
        "JEZZ_AS_OF": "",
        "JEZZ_DOSSIER": "",
        "JEZZ_BLUEPRINT_TARGET": "blueprint.json",
        "JEZZ_PROVE": "require-deductive",
        "GITHUB_OUTPUT": str(gh_out),
        "GITHUB_STEP_SUMMARY": str(gh_sum),
    }
    proc = subprocess.run(
        ("bash", str(script_file)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    kv_text = gh_out.read_text(encoding="utf-8")
    assert "inspect_rc=0" in kv_text
    assert "blueprint_rc=0" in kv_text
    assert "prove_rc=0" in kv_text


def test_wheel_force_include_and_bundled_template_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify hatchling wheel force-include config and bundled ``_templates`` lookup."""
    pyproject = tomllib.loads(
        read_bounded_text(ContainedPath.from_parts(REPO_ROOT, "pyproject.toml"))
    )
    force_inc = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_inc["templates"] == "jezz/_templates"

    fake_pkg = ContainedPath.from_parts(tmp_path, "fake_jezz_pkg").resolved_path
    fake_rs = ContainedPath.from_parts(fake_pkg, "_templates/rust").resolved_path
    fake_rs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("importlib.resources.files", lambda _pkg: fake_pkg)
    assert resolve_scaffold_template_dir(ScaffoldTemplate.RUST) == fake_rs


@settings(max_examples=25)
@given(pkg_slug=st.from_regex(r"[a-z][a-z0-9]{2,10}", fullmatch=True))
def test_property_go_root_internal_visibility_vs_cross_service_breach(pkg_slug: str) -> None:
    """Root Go ``internal/<pkg>`` is visible in root while cross-service breaches fail."""
    root_dom = parse_go_unit(
        f"internal/{pkg_slug}/core.go",
        f"package {pkg_slug}\nfunc Check(x int64) int64 {{ if x < 0 {{ return 0 }}; return x }}\n",
    )
    root_gw = parse_go_unit(
        "internal/gateway/wire.go",
        f'package gateway\nimport "example.com/mod/internal/{pkg_slug}"\n'
        f"func Run(x int64) int64 {{ if x < 0 {{ return 0 }}; return {pkg_slug}.Check(x) }}\n",
    )
    assert check_cog_006((root_dom, root_gw)) == []

    svc_breach = parse_go_unit(
        "services/billing/run.go",
        f'package billing\nimport "example.com/mono/services/auth/internal/{pkg_slug}"\n'
        f"func Bill(x int64) int64 {{ if x < 0 {{ return 0 }}; return {pkg_slug}.Check(x) }}\n",
    )
    hits = check_cog_006((svc_breach,))
    assert len(hits) == 1
    assert hits[0].code_id == "JBC-COG-006"
