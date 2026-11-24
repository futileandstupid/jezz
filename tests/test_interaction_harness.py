"""Verification suite for Step 1: Antigravity plugin, Stop hook, and interactive CLI UX."""

import io
import json
import subprocess
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents.schemas import (
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    FilePatchArtifact,
    FramedMandateProposal,
    InstrumentationSuiteBundle,
    PceRunStatus,
    PceWorkflowResult,
)
from jezz.cli import main
from jezz.hooks import (
    StopHookConfig,
    StopHookDecisionKind,
    StopHookInput,
    StopHookOutput,
    evaluate_stop_hook,
)
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope
from jezz.plugin import WorkspaceInitReport

_JSON_DICT_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])

_CLEAN_CALC_SRC = textwrap.dedent(
    """\
    import icontract
    from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

    @icontract.ensure(lambda x, result: result == x * 3)
    @jezz_callable
    def triple_units(x: PositiveInt) -> NonNegativeInt:
        return x * 3
    """
)

_CLEAN_TEST_SRC = textwrap.dedent(
    """\
    from pkg_one.calc import triple_units

    def test_triple_units() -> None:
        assert triple_units(4) == 12
    """
)

_BASE_PYPROJECT = textwrap.dedent(
    """\
    [project]
    name = "pkg-one"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)


def _write_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(text)


def _read_utf8(path: Path) -> str:
    with path.open("r", encoding="utf-8") as fh:
        return fh.read(131_072)


def _init_git_repo_with_clean_commit(repo: Path) -> None:
    _write_utf8(repo / "pyproject.toml", _BASE_PYPROJECT)
    _write_utf8(repo / "src" / "pkg_one" / "__init__.py", "")
    _write_utf8(repo / "src" / "pkg_one" / "calc.py", _CLEAN_CALC_SRC)
    _write_utf8(repo / "tests" / "test_calc.py", _CLEAN_TEST_SRC)
    subprocess.run(("git", "init"), cwd=repo, check=True, capture_output=True, timeout=10.0)
    subprocess.run(
        ("git", "config", "user.email", "test@example.com"),
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=10.0,
    )
    subprocess.run(
        ("git", "config", "user.name", "Jezz Test"),
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=10.0,
    )
    subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True, timeout=10.0)
    subprocess.run(
        ("git", "commit", "-m", "chore: baseline"),
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=10.0,
    )


def test_jezz_init_installs_workspace_plugin_pyproject_and_pre_commit_idempotently(
    tmp_path: Path,
) -> None:
    _init_git_repo_with_clean_commit(tmp_path)

    out_json = io.StringIO()
    code = main(
        ["init", str(tmp_path), "--pre-commit", "--format", "json"],
        stdout=out_json,
    )
    assert code == 0
    report = WorkspaceInitReport.model_validate_json(out_json.getvalue())
    assert report.pyproject_updated is True
    assert report.pre_commit_installed is True
    assert len(report.written_files) == 7

    plugin_root = tmp_path / ".agents" / "plugins" / "jezz"
    assert (plugin_root / "plugin.json").is_file()
    mcp_raw = _read_utf8(plugin_root / "mcp_config.json")
    hooks_raw = _read_utf8(plugin_root / "hooks.json")
    pre_commit_raw = _read_utf8(tmp_path / ".git" / "hooks" / "pre-commit")
    assert str(tmp_path) not in mcp_raw
    assert str(tmp_path) not in hooks_raw
    assert str(tmp_path) not in pre_commit_raw
    hooks_doc = _JSON_DICT_ADAPTER.validate_json(hooks_raw)
    assert "jezz-jbc-stop-gate" in hooks_doc
    skill_text = _read_utf8(plugin_root / "skills" / "pce" / "SKILL.md")
    assert skill_text.startswith("---\nname: pce\n")
    assert "[tool.jezz.models]" in _read_utf8(tmp_path / "pyproject.toml")
    assert (tmp_path / ".git" / "hooks" / "pre-commit").is_file()

    # Second run without --overwrite preserves all existing plugin + hook files
    out_second = io.StringIO()
    assert main(["init", str(tmp_path), "--pre-commit", "--format", "json"], stdout=out_second) == 0
    second_report = WorkspaceInitReport.model_validate_json(out_second.getvalue())
    assert second_report.pyproject_updated is False
    assert second_report.pre_commit_installed is False
    assert len(second_report.skipped_files) == 6


def test_jezz_init_global_scope_writes_require_marker_hook(tmp_path: Path) -> None:
    repo_dir = tmp_path / "workspace"
    global_cfg = tmp_path / "global_cfg"
    repo_dir.mkdir()

    out_md = io.StringIO()
    code = main(
        [
            "init",
            str(repo_dir),
            "--plugin-scope",
            "global",
            "--global-config-dir",
            str(global_cfg),
            "--no-pyproject",
        ],
        stdout=out_md,
    )
    assert code == 0
    assert "Jezz Workspace Initialization" in out_md.getvalue()
    hooks_text = _read_utf8(global_cfg / "plugins" / "jezz" / "hooks.json")
    assert "--require-marker" in hooks_text
    mcp_text = _read_utf8(global_cfg / "plugins" / "jezz" / "mcp_config.json")
    assert "--root" not in mcp_text


def test_stop_hook_allows_clean_diff_and_blocks_red_tag_or_syntax_error(
    tmp_path: Path,
) -> None:
    _init_git_repo_with_clean_commit(tmp_path)
    hook_stdin_payload = json.dumps(
        {
            "conversationId": "conv-01",
            "workspacePaths": [str(tmp_path)],
            "terminationReason": "NO_TOOL_CALL",
            "executionNum": 0,
            "continuationActive": False,
        }
    )

    # 1. Zero modified files since HEAD -> allow
    out_clean = io.StringIO()
    assert main(["hook", "stop"], stdin=io.StringIO(hook_stdin_payload), stdout=out_clean) == 0
    parsed_clean = StopHookOutput.model_validate_json(out_clean.getvalue())
    assert parsed_clean.decision == StopHookDecisionKind.ALLOW

    # 2. Introduce JBC violation (print + missing @jezz_callable) -> continue with citation
    bad_src = _CLEAN_CALC_SRC + '\ndef debug_dump() -> None:\n    print("leak")\n'
    _write_utf8(tmp_path / "src" / "pkg_one" / "calc.py", bad_src)
    out_block = io.StringIO()
    assert main(["hook", "stop"], stdin=io.StringIO(hook_stdin_payload), stdout=out_block) == 0
    parsed_block = StopHookOutput.model_validate_json(out_block.getvalue())
    assert parsed_block.decision == StopHookDecisionKind.CONTINUE
    assert parsed_block.reason is not None
    assert "RED_TAG" in parsed_block.reason
    assert "src/pkg_one/calc.py" in parsed_block.reason

    # 3. Exceeding max_continuations fails open to prevent infinite Stop loops
    capped = evaluate_stop_hook(
        StopHookInput(
            termination_reason="NO_TOOL_CALL",
            execution_num=3,
            continuation_active=True,
            workspace_paths=(str(tmp_path),),
        ),
        config=StopHookConfig(max_continuations=3),
    )
    assert capped.decision == StopHookDecisionKind.ALLOW

    # 4. Syntax error in touched file -> continue with SYNTAX_ERROR notice
    _write_utf8(tmp_path / "src" / "pkg_one" / "calc.py", "def broken_syntax(:\n")
    out_syn = io.StringIO()
    assert main(["hook", "stop"], stdin=io.StringIO(hook_stdin_payload), stdout=out_syn) == 0
    parsed_syn = StopHookOutput.model_validate_json(out_syn.getvalue())
    assert parsed_syn.decision == StopHookDecisionKind.CONTINUE
    assert parsed_syn.reason is not None
    assert "SYNTAX_ERROR" in parsed_syn.reason


def test_stop_hook_respects_require_marker_and_malformed_stdin(tmp_path: Path) -> None:
    _init_git_repo_with_clean_commit(tmp_path)
    _write_utf8(
        tmp_path / "src" / "pkg_one" / "calc.py",
        _CLEAN_CALC_SRC + '\ndef bad() -> None:\n    print("x")\n',
    )
    payload = json.dumps({"workspacePaths": [str(tmp_path)], "terminationReason": "NO_TOOL_CALL"})

    # Unmarked workspace is skipped when --require-marker is set
    out_skip = io.StringIO()
    assert (
        main(
            ["hook", "stop", "--require-marker"],
            stdin=io.StringIO(payload),
            stdout=out_skip,
        )
        == 0
    )
    assert StopHookOutput.model_validate_json(out_skip.getvalue()).decision == (
        StopHookDecisionKind.ALLOW
    )

    # Malformed stdin never crashes the host agent loop
    out_malformed = io.StringIO()
    assert main(["hook", "stop"], stdin=io.StringIO("{not-json"), stdout=out_malformed) == 0
    assert StopHookOutput.model_validate_json(out_malformed.getvalue()).decision == (
        StopHookDecisionKind.ALLOW
    )


def _interactive_mock_router(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    prompt_blob = " ".join(str(part) for msg in messages for part in msg.parts)
    tool = info.output_tools[0]
    title = str(tool.parameters_json_schema.get("title", ""))
    if title == "FramedMandateProposal":
        resolved = "Authoritative Clarifications Resolving Open Ambiguities" in prompt_blob
        ambiguities = () if resolved else ("Confirm multiplier factor for triple_units.",)
        proposal = FramedMandateProposal(
            mandate=EngineeringMandate(
                mandate_id="MND-TTY-01",
                title="Interactive triple units",
                objective="Implement triple_units(x) returning 3 * x.",
                invariants=(
                    DomainInvariant(
                        invariant_id="INV-001",
                        statement="triple_units(x) == 3 * x",
                        verification_symbol="test_triple_units",
                    ),
                ),
                write_scopes=(
                    ModuleWriteScope(
                        scope_id="calc_scope",
                        owned_files=frozenset({"src/pkg_one/calc.py", "tests/test_calc.py"}),
                        contract_symbols=("triple_units", "test_triple_units"),
                    ),
                ),
                open_ambiguities=ambiguities,
            ),
            framing_notes="Framed interactive mandate.",
        )
        raw = proposal.model_dump_json()
    else:
        raw = _stage_mock_payload(title)
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool.name, args=raw, tool_call_id="call_tty")]
    )


def _stage_mock_payload(title: str) -> str:
    if title == "EpistemicObservation":
        return EpistemicObservation(
            inspected_files=("src/pkg_one/__init__.py",),
            discovered_symbols=(),
            summary="Inspected pkg_one.",
        ).model_dump_json()
    if title == "ContractSpecificationBundle":
        return ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/pkg_one/calc.py",
                    source_code=_CLEAN_CALC_SRC,
                    rationale="Contracted triple_units.",
                ),
            ),
            declared_symbols=("triple_units",),
            summary="Specified triple_units.",
        ).model_dump_json()
    if title == "InstrumentationSuiteBundle":
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_calc.py",
                    source_code=_CLEAN_TEST_SRC,
                    rationale="Instrumented test_triple_units.",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Instrumented INV-001.",
        ).model_dump_json()
    return ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/pkg_one/calc.py",
                source_code=_CLEAN_CALC_SRC,
                rationale="Implemented triple_units.",
            ),
        ),
        summary="Synthesized triple_units.",
    ).model_dump_json()


def test_cli_interactive_elicitation_progress_and_plan_only_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_utf8(tmp_path / "pyproject.toml", _BASE_PYPROJECT)
    _write_utf8(tmp_path / "src" / "pkg_one" / "__init__.py", "")
    monkeypatch.setattr(
        "jezz.cli.resolve_cli_model",
        lambda _name: FunctionModel(_interactive_mock_router),
    )

    # 1. --plan-only + --interactive resolves ELICIT on stdin, halts at plan sign-off, writes JSON
    out_plan = io.StringIO()
    err_plan = io.StringIO()
    code_plan = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "test",
            "--prompt",
            "Build triple_units",
            "--interactive",
            "--progress",
            "--plan-only",
            "--write-mandate",
            "mandate.json",
            "--format",
            "json",
        ],
        stdin=io.StringIO("Multiply input by exact integer 3.\n"),
        stdout=out_plan,
        stderr=err_plan,
    )
    assert code_plan == 0
    plan_result = PceWorkflowResult.model_validate_json(out_plan.getvalue())
    assert plan_result.status == PceRunStatus.HALTED_FOR_PLAN_APPROVAL
    assert plan_result.graph_verification.is_valid is True
    assert not (tmp_path / "src" / "pkg_one" / "calc.py").exists()
    assert "[BHV-02 ELICIT]" in err_plan.getvalue()
    assert "[PCE] BHV-09 DECOMPOSE" in err_plan.getvalue()
    saved_mandate = EngineeringMandate.model_validate_json(_read_utf8(tmp_path / "mandate.json"))
    assert saved_mandate.mandate_id == "MND-TTY-01"
    assert saved_mandate.open_ambiguities == ()

    # 2. Execute saved mandate to full OCCUPANCY_CERTIFIED completion
    out_exec = io.StringIO()
    code_exec = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "test",
            "--mandate",
            str(tmp_path / "mandate.json"),
            "--gate-runner",
            "in-process",
        ],
        stdout=out_exec,
    )
    assert code_exec == 0
    assert "OCCUPANCY_CERTIFIED" in out_exec.getvalue()
    assert (tmp_path / "src" / "pkg_one" / "calc.py").is_file()


@given(
    max_cont=st.integers(min_value=1, max_value=10),
    extra_steps=st.integers(min_value=0, max_value=10),
)
def test_property_stop_hook_continuation_cap_always_fails_open(
    max_cont: int,
    extra_steps: int,
) -> None:
    out = evaluate_stop_hook(
        StopHookInput(
            termination_reason="NO_TOOL_CALL",
            execution_num=max_cont + extra_steps,
            continuation_active=True,
            workspace_paths=(),
        ),
        config=StopHookConfig(max_continuations=max_cont),
    )
    assert out.decision == StopHookDecisionKind.ALLOW
