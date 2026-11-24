"""Track 12: Release v0.2.0 metadata parity and reusable composite GitHub Action verification."""

import os
import re
import subprocess
import textwrap
import tomllib
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from jezz import __version__
from jezz.behaviors import BehaviorGraph, build_greenfield_contract_first_graph
from jezz.codes import (
    CodePermit,
    GateAttestation,
    GateKind,
    build_workspace_inspector,
    compute_units_digest,
    discover_workspace_units,
    issue_certificate,
)
from jezz.guards import ContainedPath, read_bounded_text
from jezz.mcp_server import SERVER_VERSION
from jezz.methodology import (
    DomainInvariant,
    EngineeringDossier,
    EngineeringMandate,
    ModuleWriteScope,
    audit_pce_dossier,
)

_HOST_ROOT_TOKENS = ("/" + "Users" + "/", "/" + "home" + "/", "C:" + "\\\\")
_RAW_INPUT_INTERP_RE = re.compile(r"\$\{\{\s*inputs\.")

_SAMPLE_CALC_GO = textwrap.dedent(
    """\
    package cigate

    import "errors"

    var ErrNegativeAmount = errors.New("negative amount")

    func DoubleCents(amount int64) (int64, error) {
        if amount < 0 || amount > 1_000_000_000 {
            return 0, ErrNegativeAmount
        }
        return amount * 2, nil
    }
    """
)

_SAMPLE_CALC_TEST_GO = textwrap.dedent(
    """\
    package cigate

    import "testing"

    func TestDoubleCents(t *testing.T) {
        got, err := DoubleCents(25)
        if err != nil || got != 50 {
            t.Fatalf("expected 50, got %d (%v)", got, err)
        }
    }
    """
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _extract_composite_gate_script(action_text: str) -> str:
    marker = "      run: |\n"
    idx = action_text.find(marker)
    assert idx != -1, "Missing composite gate run block in action.yml"
    raw_block = action_text[idx + len(marker) :]
    lines = [line[8:] if line.startswith("        ") else line for line in raw_block.splitlines()]
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _seed_go_git_repo_and_dossier(tmp_path: Path) -> tuple[Path, Path]:
    ws = ContainedPath.from_parts(tmp_path, "target_repo").resolved_path
    ws.mkdir(parents=True, exist_ok=True)
    ContainedPath.from_parts(ws, "go.mod").resolved_path.write_text(
        "module example.com/cigate\n\ngo 1.24\n",
        encoding="utf-8",
    )
    ContainedPath.from_parts(ws, "calc.go").resolved_path.write_text(
        _SAMPLE_CALC_GO,
        encoding="utf-8",
    )
    ContainedPath.from_parts(ws, "calc_test.go").resolved_path.write_text(
        _SAMPLE_CALC_TEST_GO,
        encoding="utf-8",
    )
    _git(ws, "init", "-b", "main")
    _git(ws, "config", "user.email", "ci@jezz.local")
    _git(ws, "config", "user.name", "Jezz CI")
    _git(ws, "add", "go.mod")
    _git(ws, "commit", "-m", "chore: init go.mod")
    _git(ws, "add", "calc.go", "calc_test.go")
    _git(ws, "commit", "-m", "feat: add JBC-clean calc package")

    units = discover_workspace_units(ws)
    inspector = build_workspace_inspector(ws, units)
    permit = CodePermit.full_book(permit_id="PERMIT-CI-GATE-12")
    atts = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="go-vet",
            passed=True,
            exit_code=0,
            checks_executed=2,
            duration_ms=4.0,
            peak_memory_kb=512,
            summary="go vet clean",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="go-test",
            passed=True,
            exit_code=0,
            checks_executed=1,
            duration_ms=6.0,
            peak_memory_kb=512,
            summary="PASS",
        ),
    )
    final_rep = inspector.inspect_final(units, permit=permit, attestations=atts)
    cert = issue_certificate(
        final_rep,
        artifact_digest=compute_units_digest(units),
    )
    graph: BehaviorGraph = build_greenfield_contract_first_graph()
    mandate = EngineeringMandate(
        mandate_id="MND-CI-GATE-12",
        title="Verify composite GitHub Action gate and dossier audit",
        objective="Ensure DoubleCents rejects out-of-bounds inputs and doubles valid cents.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="DoubleCents doubles valid non-negative integer cents.",
                verification_symbol="TestDoubleCents",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_calc",
                owned_files=frozenset({"calc.go", "calc_test.go"}),
                contract_symbols=("DoubleCents",),
            ),
        ),
    )
    dossier = EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=graph,
        mutated_files=frozenset({"calc.go", "calc_test.go"}),
        inspection_report=final_rep,
        certificate=cert,
    )
    assert audit_pce_dossier(dossier, units).is_occupancy_sealed
    dossier_file = ContainedPath.from_parts(tmp_path, "dossier.json").resolved_path
    dossier_file.write_text(dossier.model_dump_json(indent=2), encoding="utf-8")
    return ws, dossier_file


def test_release_version_and_pyproject_metadata_parity() -> None:
    root = _repo_root()
    pyproject = tomllib.loads(read_bounded_text(ContainedPath.from_parts(root, "pyproject.toml")))
    project = pyproject["project"]
    assert project["name"] == "jezz"
    assert project["version"] == __version__
    assert __version__ in {"0.2.1", "0.3.0", "0.4.0"}
    assert __version__ == SERVER_VERSION
    assert project["requires-python"] == ">=3.14"
    assert project["scripts"]["jezz"] == "jezz.cli:main"
    urls = project["urls"]
    assert "https://github.com/futileandstupid/jezz" in urls["Repository"]


def test_composite_github_action_and_workflow_dogfood_hygiene() -> None:
    root = _repo_root()
    action_txt = read_bounded_text(ContainedPath.from_parts(root, "action.yml"))
    workflow_txt = read_bounded_text(
        ContainedPath.from_parts(root, ".github/workflows/jbc-gate.yml")
    )

    assert 'using: "composite"' in action_txt
    for required_input in (
        "working-directory:",
        "mode:",
        "base-ref:",
        "language:",
        "permit:",
        "as-of:",
        "dossier:",
        "python-version:",
    ):
        assert required_input in action_txt
    assert "inspect-exit-code:" in action_txt
    assert "audit-exit-code:" in action_txt
    assert not any(tok in action_txt for tok in _HOST_ROOT_TOKENS)
    assert not any(tok in workflow_txt for tok in _HOST_ROOT_TOKENS)

    script_body = _extract_composite_gate_script(action_txt)
    assert _RAW_INPUT_INTERP_RE.search(script_body) is None
    assert "uses: ./" in workflow_txt


def test_composite_action_gate_script_end_to_end_differential_and_dossier(
    tmp_path: Path,
) -> None:
    root = _repo_root()
    action_txt = read_bounded_text(ContainedPath.from_parts(root, "action.yml"))
    script_file = ContainedPath.from_parts(tmp_path, "run_gate.sh").resolved_path
    script_file.write_text(_extract_composite_gate_script(action_txt), encoding="utf-8")
    ws, dossier_file = _seed_go_git_repo_and_dossier(tmp_path)

    gh_out = ContainedPath.from_parts(tmp_path, "gh_output.txt").resolved_path
    gh_sum = ContainedPath.from_parts(tmp_path, "gh_summary.md").resolved_path
    gh_out.write_text("", encoding="utf-8")
    gh_sum.write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "JEZZ_ACTION_PATH": str(root),
        "JEZZ_PYTHON_VERSION": "3.14",
        "JEZZ_WORKDIR": str(ws),
        "JEZZ_MODE": "both",
        "JEZZ_BASE_REF": "HEAD~1",
        "JEZZ_EVENT_NAME": "push",
        "JEZZ_PR_BASE_REF": "",
        "JEZZ_LANGUAGES": "go",
        "JEZZ_PERMIT": "",
        "JEZZ_AS_OF": "",
        "JEZZ_DOSSIER": str(dossier_file),
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
    assert proc.returncode == 0, f"Action failed: {proc.stderr}\n{proc.stdout}"
    out_kv = gh_out.read_text(encoding="utf-8")
    summary_md = gh_sum.read_text(encoding="utf-8")
    assert "inspect_rc=0" in out_kv
    assert "audit_rc=0" in out_kv
    assert "OCCUPANCY_SEALED" in summary_md


@given(
    salt=st.from_regex(r"[A-Za-z0-9_]{1,16}", fullmatch=True),
)
def test_property_units_digest_changes_under_any_source_mutation(salt: str) -> None:
    from jezz.codes import parse_go_unit

    baseline = parse_go_unit("calc.go", _SAMPLE_CALC_GO)
    mutated = parse_go_unit("calc.go", f"{_SAMPLE_CALC_GO}\n// {salt}\n")
    assert compute_units_digest((baseline,)) != compute_units_digest((mutated,))
