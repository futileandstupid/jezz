"""Track 15: >=92% CI coverage ratchet, subprocess/provider adapter contracts, and invariants."""

import runpy
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr, ValidationError
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.exceptions import ModelHTTPError, UserError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

import jezz.agents.agy_model as agy_mod
import jezz.agents.providers as prov_mod
import jezz.agents.workspace as ws_mod
import jezz.behaviors.transitions as trans_mod
from jezz.agents import (
    AntigravityCliModel,
    InProcessProvisionRunner,
    JezzAgentDeps,
    PolyglotSubprocessGateRunner,
    PolyglotSubprocessProvisionRunner,
    SubprocessAntigravityRunner,
    TlsResilientModel,
    UvSubprocessGateRunner,
    UvSubprocessProvisionRunner,
    WorkspaceSandbox,
    compute_missing_dependencies,
    infer_authenticated_model,
    refresh_inspector_after_provision,
)
from jezz.behaviors import (
    ALLOWED_TRANSITIONS,
    BEHAVIOR_BASIS,
    BehaviorKind,
    BehaviorSpec,
    BehaviorStratum,
    StateDomain,
    WorkspaceEffect,
    explain_transition,
    validate_basis_invariants,
)
from jezz.codes import (
    CODE_BOOK,
    BuildingInspector,
    CodeArticle,
    CodeOrigin,
    CodePermit,
    CodeTrigger,
    EcosystemManifest,
    ManifestDependencies,
    PolyglotManifest,
    SupportedLanguage,
    adapter_for_path,
    detect_unit_triggers,
    get_language_adapter,
    parse_any_source_unit,
    parse_go_unit,
    parse_source_unit,
    parse_typescript_unit,
    validate_codebook_invariants,
)
from jezz.evals.schemas import BenchmarkArm
from jezz.evals.tasks import build_track4_benchmark_cases
from jezz.evals.verifier import (
    ArmExecutionMetrics,
    InProcessWorkspaceVerifier,
    UvWorkspaceVerifier,
    grade_workspace_artifacts,
    provision_scratch_workspace,
)
from jezz.guards import ContainedPath, read_bounded_text
from jezz.methodology import (
    DependencyRequirement,
    DependencyScope,
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write(root: Path, rel: str, content: str) -> Path:
    target = ContainedPath.from_parts(root, rel).resolved_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_coverage_configuration_and_ci_gate_enforced() -> None:
    root = _repo_root()
    pyproject = tomllib.loads(read_bounded_text(ContainedPath.from_parts(root, "pyproject.toml")))
    dev_deps = pyproject["dependency-groups"]["dev"]
    assert any(str(dep).startswith("pytest-cov") for dep in dev_deps)
    cov_cfg = pyproject["tool"]["coverage"]
    assert cov_cfg["run"]["source"] == ["jezz"]
    assert cov_cfg["report"]["show_missing"] is True

    ci_yml = read_bounded_text(ContainedPath.from_parts(root, ".github/workflows/jbc-gate.yml"))
    assert "uv run pytest --cov=jezz --cov-fail-under=92" in ci_yml


def test_python_module_entrypoint_invokes_cli_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["jezz", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("jezz", run_name="__main__")
    assert exc_info.value.code == 0


def _assert_inprocess_polyglot_provision(tmp_path: Path) -> None:
    py_ws = ContainedPath.from_parts(tmp_path, "py_bare").resolved_path
    _write(
        py_ws,
        "pyproject.toml",
        '[project]\nname = "bare"\nversion = "0.1.0"\n\n[dependency-groups]\n',
    )
    py_sb = WorkspaceSandbox(
        py_ws,
        frozenset({"src/bare/mod.py"}),
        authorized_manifests=frozenset({"pyproject.toml", "uv.lock"}),
    )
    reqs = (
        DependencyRequirement(package="httpx", version_constraint=">=0.28.0"),
        DependencyRequirement(
            package="pytest",
            version_constraint=">=9.0.0",
            scope=DependencyScope.DEV,
        ),
    )
    rec_py = InProcessProvisionRunner(touch_lockfile=True).provision_dependencies(py_sb, reqs)
    assert "uv.lock" in rec_py.updated_files

    rs_ws = ContainedPath.from_parts(tmp_path, "rs_prov").resolved_path
    _write(rs_ws, "Cargo.toml", '[package]\nname = "rs_p"\nversion = "0.1.0"\nedition = "2021"\n')
    rs_sb = WorkspaceSandbox(
        rs_ws,
        frozenset({"src/lib.rs"}),
        authorized_manifests=frozenset({"Cargo.toml"}),
    )
    rs_reqs = (
        DependencyRequirement(package="serde", version_constraint="1.0"),
        DependencyRequirement(
            package="proptest",
            version_constraint="1.5",
            scope=DependencyScope.DEV,
        ),
    )
    rec_rs = InProcessProvisionRunner().provision_dependencies(rs_sb, rs_reqs)
    assert rec_rs.updated_files == ("Cargo.toml",)
    assert "proptest" in rs_sb.read_file("Cargo.toml")

    go_ws = ContainedPath.from_parts(tmp_path, "go_prov").resolved_path
    go_sb = WorkspaceSandbox(
        go_ws,
        frozenset({"main.go"}),
        authorized_manifests=frozenset({"go.mod"}),
    )
    go_reqs = (DependencyRequirement(package="golang.org/x/sync", version_constraint="0.12.0"),)
    rec_go = InProcessProvisionRunner().provision_dependencies(go_sb, go_reqs)
    assert rec_go.updated_files == ("go.mod",)
    assert "v0.12.0" in go_sb.read_file("go.mod")


def _assert_subprocess_polyglot_provision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def _fake_subproc(
        argv: tuple[str, ...],
        cwd: Path,
        **_kw: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(argv))
        match argv[0]:
            case "uv":
                _write(cwd, "pyproject.toml", '[project]\nname = "p"\ndependencies = ["httpx"]\n')
            case "npm":
                _write(cwd, "package.json", '{"name": "ts", "dependencies": {"zod": "^3.24.0"}}\n')
            case "cargo":
                _write(cwd, "Cargo.toml", '[package]\nname = "r"\n[dependencies]\nserde = "1"\n')
            case "go":
                _write(cwd, "go.mod", "module ex.com/m\n\nrequire golang.org/x/sync v0.12.0\n")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("jezz.agents.provision.subprocess.run", _fake_subproc)

    py_ws = ContainedPath.from_parts(tmp_path, "sub_py").resolved_path
    _write(py_ws, "pyproject.toml", '[project]\nname = "p"\ndependencies = []\n')
    unauth_sb = WorkspaceSandbox(py_ws, frozenset({"src/p/a.py"}))
    py_reqs = (
        DependencyRequirement(package="httpx", version_constraint=">=0.28"),
        DependencyRequirement(
            package="ruff",
            version_constraint=">=0.9",
            scope=DependencyScope.DEV,
        ),
    )
    with pytest.raises(PermissionError):
        UvSubprocessProvisionRunner().provision_dependencies(unauth_sb, py_reqs)

    py_sb = WorkspaceSandbox(
        py_ws,
        frozenset({"src/p/a.py"}),
        authorized_manifests=frozenset({"pyproject.toml", "uv.lock"}),
    )
    assert PolyglotSubprocessProvisionRunner().provision_dependencies(py_sb, py_reqs).updated_files

    for sub, src_rel, manifest_rel, pkg in (
        ("sub_ts", "src/index.ts", "package.json", "zod"),
        ("sub_rs", "src/lib.rs", "Cargo.toml", "serde"),
        ("sub_go", "main.go", "go.mod", "golang.org/x/sync"),
    ):
        ws = ContainedPath.from_parts(tmp_path, sub).resolved_path
        _write(ws, manifest_rel, "\n")
        with pytest.raises(PermissionError):
            PolyglotSubprocessProvisionRunner().provision_dependencies(
                WorkspaceSandbox(ws, frozenset({src_rel})),
                (DependencyRequirement(package=pkg, version_constraint="1.0.0"),),
            )
        sb = WorkspaceSandbox(
            ws,
            frozenset({src_rel}),
            authorized_manifests=frozenset({manifest_rel}),
        )
        PolyglotSubprocessProvisionRunner().provision_dependencies(
            sb,
            (
                DependencyRequirement(package=pkg, version_constraint="1.0.0"),
                DependencyRequirement(
                    package=f"{pkg}-dev",
                    version_constraint="1.0.0",
                    scope=DependencyScope.DEV,
                ),
            ),
        )
    assert {cmd[0] for cmd in calls} >= {"uv", "npm", "cargo", "go"}


def test_provision_runners_inprocess_and_subprocess_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_inprocess_polyglot_provision(tmp_path)
    _assert_subprocess_polyglot_provision(tmp_path, monkeypatch)

    poly = PolyglotManifest(
        go=EcosystemManifest(
            runtime_roots=frozenset({"golang.org/x/sync"}),
            module_prefix="example.com/app",
        )
    )
    missing_go = compute_missing_dependencies(
        ManifestDependencies(),
        (
            DependencyRequirement(package="golang.org/x/sync", version_constraint="0.12.0"),
            DependencyRequirement(package="github.com/spf13/cobra", version_constraint="1.8.0"),
        ),
        polyglot_manifest=poly,
        language=SupportedLanguage.GO,
    )
    assert [d.package for d in missing_go] == ["github.com/spf13/cobra"]
    refreshed = refresh_inspector_after_provision(
        tmp_path,
        BuildingInspector(),
        (
            DependencyRequirement(
                package="vitest",
                version_constraint="^3.0.0",
                scope=DependencyScope.DEV,
            ),
        ),
        language=SupportedLanguage.TYPESCRIPT,
    )
    assert "vitest" in refreshed.polyglot_manifest.typescript.dev_roots

    monkeypatch.setattr(
        "jezz.agents.provision.subprocess.run",
        lambda argv, **_kw: subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="", stderr="registry error"
        ),
    )
    fail_ws = ContainedPath.from_parts(tmp_path, "fail_py").resolved_path
    _write(fail_ws, "pyproject.toml", '[project]\nname = "f"\ndependencies = []\n')
    fail_sb = WorkspaceSandbox(
        fail_ws,
        frozenset({"src/f/a.py"}),
        authorized_manifests=frozenset({"pyproject.toml"}),
    )
    with pytest.raises(RuntimeError, match="uv add failed"):
        UvSubprocessProvisionRunner().provision_dependencies(
            fail_sb,
            (DependencyRequirement(package="httpx"),),
        )


async def _assert_agy_subprocess_turns(tmp_path: Path) -> SubprocessAntigravityRunner:
    with pytest.raises(ValueError, match="Invalid Antigravity CLI binary"):
        SubprocessAntigravityRunner(binary_name="-bad")

    missing_runner = SubprocessAntigravityRunner(binary_name="nonexistent_agy_bin_xyz")
    with pytest.raises(UserError, match="not found on PATH"):
        await missing_runner.run_turn("claude-sonnet-5-5-medium", "ping", timeout_sec=2.0)

    stub_ok = _write(
        tmp_path,
        "stub_agy_ok.sh",
        "#!/bin/sh\n"
        'printf \'%s\\n\' "noise" "{bad" '
        '\'{"status":"SUCCESS","conversation_id":"c-1","response":"{\\"text\\":\\"hello\\"}",'
        '"usage":{"input_tokens":12,"output_tokens":4,"cache_read_tokens":6}}\'\n',
    )
    stub_ok.chmod(0o755)
    ok_runner = SubprocessAntigravityRunner(binary_name=str(stub_ok))
    turn = await ok_runner.run_turn(
        "claude-sonnet-5-5-medium",
        "hello",
        conversation_id="conv-prev",
        timeout_sec=5.0,
    )
    assert turn.conversation_id == "c-1" and turn.cache_read_tokens == 6

    stub_err = _write(tmp_path, "stub_agy_err.sh", '#!/bin/sh\nprintf "fatal crash" >&2\nexit 2\n')
    stub_err.chmod(0o755)
    with pytest.raises(ModelHTTPError):
        await SubprocessAntigravityRunner(binary_name=str(stub_err)).run_turn(
            "claude-sonnet-5-5-medium", "hi", timeout_sec=5.0
        )

    stub_hang = _write(tmp_path, "stub_agy_hang.sh", "#!/bin/sh\nsleep 5\n")
    stub_hang.chmod(0o755)
    with pytest.raises(TimeoutError):
        await SubprocessAntigravityRunner(binary_name=str(stub_hang)).run_turn(
            "claude-sonnet-5-5-medium", "hi", timeout_sec=0.15
        )
    return ok_runner


async def _assert_agy_response_and_stream_contracts(ok_runner: SubprocessAntigravityRunner) -> None:
    with pytest.raises(ModelHTTPError, match="no JSON result line"):
        agy_mod._decode_agy_stdout("plain text without json", "", "slug")
    with pytest.raises(ModelHTTPError, match="status=ERROR"):
        agy_mod._decode_agy_stdout('{"status":"ERROR","error":"quota"}', "", "slug")

    assert agy_mod._strip_code_fence("```inline```") == "inline"
    params_out = ModelRequestParameters(
        output_tools=[
            ToolDefinition(
                name="final_result",
                description="Final output",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                },
            )
        ],
        allow_text_output=False,
    )
    parts_final = agy_mod._parse_response_parts(
        '{bad} {"kind": "final", "final_output": {"answer": "ok"}}',
        params_out,
    )
    assert isinstance(parts_final[0], ToolCallPart)
    parts_schema = agy_mod._parse_response_parts('{"answer": "direct"}', params_out)
    assert isinstance(parts_schema[0], ToolCallPart)

    params_txt = ModelRequestParameters(allow_text_output=True)
    parts_txt = agy_mod._parse_response_parts(
        '{"kind": "final", "final_output": [1, 2]}', params_txt
    )
    assert isinstance(parts_txt[0], TextPart)
    parts_plain = agy_mod._parse_response_parts('{"text": "clean"}', params_txt)
    assert isinstance(parts_plain[0], TextPart) and parts_plain[0].content == "clean"

    req_msg = ModelRequest(
        parts=[
            UserPromptPart(content=["chunk-a", "chunk-b"]),
            ToolReturnPart(
                tool_name="probe_tool",
                content="fix field",
                tool_call_id="c-1",
                outcome="retried",
            ),
        ]
    )
    rendered_req = agy_mod._render_message(req_msg, include_system=False)
    assert "chunk-achunk-b" in rendered_req and "VALIDATOR_RETRY" in rendered_req
    rendered_resp = agy_mod._render_message(
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={"x": 1}), TextPart(content="hi")]),
        include_system=False,
    )
    assert "ASSISTANT_TOOL_CALL" in rendered_resp and "ASSISTANT_TEXT" in rendered_resp

    model = AntigravityCliModel("antigravity:claude-sonnet-5-5-medium", runner=ok_runner)
    for idx in range(36):
        model._record_conversation(f"k-{idx}", f"c-{idx}")
    assert len(model._conv_by_prefix) == 32
    with pytest.raises(UserError, match="does not support token streaming"):
        async with model.request_stream([req_msg], None, params_txt):
            pass


@pytest.mark.asyncio
async def test_antigravity_cli_model_subprocess_streaming_and_parsing_contracts(
    tmp_path: Path,
) -> None:
    ok_runner = await _assert_agy_subprocess_turns(tmp_path)
    await _assert_agy_response_and_stream_contracts(ok_runner)


def _install_stub_provider_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_classes = (
        ("google", "GoogleProvider"),
        ("google_cloud", "GoogleCloudProvider"),
        ("openai", "OpenAIProvider"),
        ("anthropic", "AnthropicProvider"),
        ("openrouter", "OpenRouterProvider"),
        ("groq", "GroqProvider"),
        ("mistral", "MistralProvider"),
        ("cohere", "CohereProvider"),
        ("deepseek", "DeepSeekProvider"),
        ("xai", "XaiProvider"),
        ("cerebras", "CerebrasProvider"),
        ("fireworks", "FireworksProvider"),
        ("together", "TogetherProvider"),
        ("gateway", "gateway_provider"),
    )
    for mod_suffix, attr_name in provider_classes:
        stub_mod = ModuleType(f"pydantic_ai.providers.{mod_suffix}")
        setattr(stub_mod, attr_name, lambda *_a, **_k: "stub-provider")
        monkeypatch.setitem(sys.modules, f"pydantic_ai.providers.{mod_suffix}", stub_mod)


@pytest.mark.asyncio
async def test_authenticated_provider_builders_and_tls_wrapper_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stub_provider_modules(monkeypatch)
    invoked_factories: list[str] = []

    def _stub_infer_model(
        model_name: str,
        provider_factory: object = None,
    ) -> TestModel:
        invoked_factories.append(model_name)
        if callable(provider_factory):
            provider_factory("stub")
        return TestModel()

    monkeypatch.setattr(prov_mod, "infer_model", _stub_infer_model)
    key = SecretStr("sk-test-secret-123456")

    for spec in (
        "google:gemini-2.5-flash",
        "google-cloud:gemini-2.5-pro",
        "openai:gpt-5",
        "anthropic:claude-sonnet-4-5",
        "openrouter:qwen/qwen3-coder",
        "groq:llama-3.3-70b",
        "mistral:mistral-large",
        "cohere:command-r-plus",
        "deepseek:deepseek-chat",
        "xai:grok-3",
        "cerebras:llama-3.3-70b",
        "fireworks:accounts/fireworks/models/qwen3",
        "together:Qwen/Qwen3",
        "gateway/openai:gpt-5",
    ):
        built = infer_authenticated_model(spec, api_key=key)
        assert isinstance(built, TestModel)
    assert len(invoked_factories) == 14

    with pytest.raises(UserError, match="Expected 'provider:model'"):
        infer_authenticated_model("unprefixed_model", api_key=key)
    with pytest.raises(UserError, match="does not support direct SecretStr"):
        infer_authenticated_model("bedrock:claude", api_key=key)

    agy_wrapped = TlsResilientModel("antigravity:claude-sonnet-5-5-medium")
    assert agy_wrapped.configured_model_id == "antigravity:claude-sonnet-5-5-medium"

    tls_model = TlsResilientModel("test", max_attempts=2)

    async def _raise_401(*_a: object, **_k: object) -> ModelResponse:
        raise ModelHTTPError(status_code=401, model_name="test", body="unauthorized")

    monkeypatch.setattr(tls_model, "_dispatch_single_attempt", _raise_401)
    with pytest.raises(ModelHTTPError):
        await tls_model.request(
            [ModelRequest(parts=[UserPromptPart(content="hi")])],
            None,
            ModelRequestParameters(),
        )


def _assert_workspace_toolset_branches(tmp_path: Path) -> None:
    ws = ContainedPath.from_parts(tmp_path, "tools_ws").resolved_path
    _write(
        ws,
        "src/pkg/calc.py",
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\ndef double(x: PositiveInt) -> PositiveInt:\n    return x * 2\n",
    )
    _write(
        ws,
        "src/pkg/caller.py",
        "from pkg.calc import double\nfrom jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\ndef quad(x: PositiveInt) -> PositiveInt:\n    return double(double(x))\n",
    )
    sb = WorkspaceSandbox(
        ws,
        frozenset({"src/pkg/calc.py", "src/pkg/caller.py", "tests/test_calc.py"}),
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TOOLS",
        title="Verify workspace tool helpers",
        objective="Verify epistemic and rough-in preview tool branches.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="double(x) == 2 * x",
                verification_symbol="test_expected_symbol",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="s_calc",
                owned_files=frozenset({"src/pkg/calc.py", "tests/test_calc.py"}),
                contract_symbols=("double",),
            ),
        ),
    )
    deps = JezzAgentDeps(
        sandbox=sb,
        inspector=BuildingInspector(
            ManifestDependencies(
                runtime_roots=frozenset({"jezz"}),
                dev_roots=frozenset({"pytest"}),
                workspace_roots=frozenset({"pkg", "tests"}),
            )
        ),
        permit=CodePermit.full_book("PERMIT-TOOLS"),
        mandate=mandate,
    )
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())

    assert "src/pkg/calc.py" in ws_mod._tool_list_authorized_files(ctx)
    assert "ABSENT" in ws_mod._tool_read_workspace_file(ctx, "src/pkg/missing.py")
    assert "DIRECTORY" in ws_mod._tool_read_workspace_file(ctx, "src/pkg")
    with pytest.raises(ModelRetry):
        ws_mod._tool_read_workspace_file(ctx, "../escape.py")

    assert ws_mod._tool_inspect_file_symbols(ctx, "src/pkg/missing.py") == ()
    assert "double" in ws_mod._tool_inspect_file_symbols(ctx, "src/pkg/calc.py")
    _write(ws, "src/pkg/bad_syn.py", "def broken(:\n")
    with pytest.raises(ModelRetry):
        ws_mod._tool_inspect_file_symbols(ctx, "src/pkg/bad_syn.py")
    ContainedPath.from_parts(ws, "src/pkg/bad_syn.py").resolved_path.unlink()

    assert "pkg.calc" in ws_mod._tool_query_ast_graph(ctx, None)
    assert "NOT_FOUND" in ws_mod._tool_query_ast_graph(ctx, "nonexistent.mod")
    assert "public_symbols" in ws_mod._tool_query_ast_graph(ctx, "src/pkg/calc.py")
    assert "quad" in ws_mod._tool_find_symbol_callers(ctx, "double")
    assert "Zero workspace" in ws_mod._tool_find_symbol_callers(ctx, "nobody_calls_me")
    assert "def double" in ws_mod._tool_read_symbol_slice(ctx, "double", "src/pkg/calc.py")
    assert "BaseModel" in ws_mod._tool_inspect_package_api(ctx, "pydantic", "BaseModel")
    assert "NOT_FOUND" in ws_mod._tool_inspect_package_api(ctx, "nonexistent_pkg_xyz_123")
    assert "JBC-SEC-001" in ws_mod._tool_consult_building_code(ctx, "JBC-SEC-001")
    assert "NOT_FOUND" in ws_mod._tool_consult_building_code(ctx, "JBC-NOPE-999")
    assert "SyntaxError" in ws_mod._tool_preview_rough_in(ctx, "src/pkg/calc.py", "def bad(:\n")
    note = ws_mod._tool_preview_rough_in(
        ctx,
        "tests/test_calc.py",
        "from pkg.calc import double\n\ndef test_other() -> None:\n    assert double(2) == 4\n",
    )
    assert "mandated verification_symbol" in note


def test_workspace_tools_verifier_and_polyglot_gate_error_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_workspace_toolset_branches(tmp_path)

    def _raise_timeout(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=("uv", "run"), timeout=1.0)

    monkeypatch.setattr("jezz.agents.workspace.subprocess.run", _raise_timeout)
    sb_to = WorkspaceSandbox(tmp_path, frozenset({"a.py"}))
    u_py = parse_source_unit("a.py", "X: int = 1\n")
    atts_to = UvSubprocessGateRunner(timeout_sec=1.0).run_gates(sb_to, (u_py,))
    assert all(a.exit_code == 124 and not a.passed for a in atts_to)

    ts_ws = ContainedPath.from_parts(tmp_path, "ts_fb").resolved_path
    _write(ts_ws, "package.json", '{"name": "ts-fb", "scripts": {"test": "jest"}}\n')
    monkeypatch.setattr("jezz.agents.polyglot_gates.subprocess.run", _raise_timeout)
    ts_u = parse_typescript_unit("src/a.test.ts", "export function testA(): void {}\n")
    pg_atts = PolyglotSubprocessGateRunner(timeout_sec=1.0).run_gates(
        WorkspaceSandbox(ts_ws, frozenset({"src/a.test.ts"})),
        (ts_u,),
    )
    assert any(a.exit_code == 124 for a in pg_atts)

    task = build_track4_benchmark_cases()[0][0]
    empty_ws = ContainedPath.from_parts(tmp_path, "empty_eval").resolved_path
    empty_ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("jezz.evals.verifier.subprocess.run", _raise_timeout)
    uv_verdict = UvWorkspaceVerifier(timeout_sec=1.0).verify_workspace(empty_ws, task)
    assert not uv_verdict.passed and uv_verdict.tests_failed >= 1

    metrics = ArmExecutionMetrics(
        arm=BenchmarkArm.SDD_PROSE,
        completed_cleanly=False,
        sdd_markdown_valid=False,
        spec_review_lines=0,
        requests=1,
        tool_calls=0,
        input_tokens=10,
        output_tokens=10,
        wall_clock_sec=0.1,
    )
    empty_grade = grade_workspace_artifacts(
        empty_ws,
        task,
        metrics,
        InProcessWorkspaceVerifier(),
    )
    assert not empty_grade.jbc_occupiable and empty_grade.emitted_code_lines == 0

    sync_calls: list[tuple[str, ...]] = []

    def _mock_sync(argv: tuple[str, ...], **_kw: object) -> subprocess.CompletedProcess[str]:
        sync_calls.append(tuple(argv))
        rc = 1 if "--offline" in argv else 0
        return subprocess.CompletedProcess(args=argv, returncode=rc, stdout="ok", stderr="")

    monkeypatch.setattr("jezz.evals.verifier.subprocess.run", _mock_sync)
    scratch_parent = ContainedPath.from_parts(tmp_path, "scratch_p").resolved_path
    provision_scratch_workspace(
        scratch_parent, "w1", task.package_name, _repo_root(), sync_venv=True
    )
    provision_scratch_workspace(
        scratch_parent, "w1", task.package_name, _repo_root(), sync_venv=False
    )
    assert any("--offline" in c for c in sync_calls)


def _assert_codebook_invariant_guards() -> None:
    mismatched_book = dict(CODE_BOOK)
    mismatched_book["JBC-SEC-999"] = CODE_BOOK["JBC-SEC-001"]
    with pytest.raises(ValueError, match="mismatches"):
        validate_codebook_invariants(mismatched_book)

    missing_article_book = {k: v for k, v in CODE_BOOK.items() if v.article != CodeArticle.VRF}
    with pytest.raises(ValueError, match="zero BuildingCodes"):
        validate_codebook_invariants(missing_article_book)

    gap_book = {k: v for k, v in CODE_BOOK.items() if k != "JBC-DBC-001"}
    with pytest.raises(ValueError, match="contiguous"):
        validate_codebook_invariants(gap_book)

    est_refs = CODE_BOOK["JBC-DBC-001"].standard_refs
    one_origin_book = {
        k: v.model_copy(
            update={"origin": CodeOrigin.ESTABLISHED_STANDARD, "standard_refs": est_refs}
        )
        for k, v in CODE_BOOK.items()
    }
    with pytest.raises(ValueError, match="ESTABLISHED_STANDARD"):
        validate_codebook_invariants(one_origin_book)

    no_univ_book = {
        k: v.model_copy(update={"triggers": frozenset({CodeTrigger.EXTERNAL_BOUNDARY})})
        for k, v in CODE_BOOK.items()
    }
    with pytest.raises(ValueError, match="UNIVERSAL"):
        validate_codebook_invariants(no_univ_book)


def test_taxonomy_transition_and_codebook_invariant_guards() -> None:
    assert explain_transition(BehaviorKind.SYNTHESIZE, BehaviorKind.ELICIT).allowed is False
    assert explain_transition(BehaviorKind.REVERT, BehaviorKind.SYNTHESIZE).allowed is False
    assert explain_transition(BehaviorKind.CHECKPOINT, BehaviorKind.CERTIFY).allowed is False

    bad_cert = dict(ALLOWED_TRANSITIONS)
    bad_cert[BehaviorKind.CERTIFY] = frozenset({BehaviorKind.FRAME})
    with pytest.raises(ValueError, match="terminal sink"):
        trans_mod._validate_transition_matrix(bad_cert)

    bad_elicit = dict(ALLOWED_TRANSITIONS)
    bad_elicit[BehaviorKind.ELICIT] = frozenset({BehaviorKind.SYNTHESIZE})
    with pytest.raises(ValueError, match="exclusively through FRAME"):
        trans_mod._validate_transition_matrix(bad_elicit)

    with pytest.raises(ValidationError):
        BehaviorSpec(
            code="BHV-99",
            kind=BehaviorKind.FRAME,
            stratum=BehaviorStratum.GOVERNANCE,
            summary="Mismatched workspace write vs workspace_effect invariant check.",
            reads=frozenset({StateDomain.PROBLEM}),
            writes=frozenset({StateDomain.WORKSPACE}),
            workspace_effect=WorkspaceEffect.NONE,
            postcondition="Should fail strict model validator check immediately.",
        )
    with pytest.raises(ValidationError):
        BehaviorSpec(
            code="BHV-98",
            kind=BehaviorKind.FRAME,
            stratum=BehaviorStratum.GOVERNANCE,
            summary="Mismatched governance stratum vs mutate workspace_effect check.",
            reads=frozenset({StateDomain.PROBLEM}),
            writes=frozenset({StateDomain.WORKSPACE}),
            workspace_effect=WorkspaceEffect.MUTATE,
            postcondition="Should fail stratum consistency validator immediately.",
        )

    short_basis = dict(list(BEHAVIOR_BASIS.items())[:13])
    with pytest.raises(ValueError, match="Expected canonical"):
        validate_basis_invariants(short_basis)
    swapped_basis = dict(BEHAVIOR_BASIS)
    swapped_basis[BehaviorKind.FRAME] = BEHAVIOR_BASIS[BehaviorKind.ELICIT]
    with pytest.raises(ValueError):
        validate_basis_invariants(swapped_basis)
    _assert_codebook_invariant_guards()


def test_language_adapter_registry_and_trigger_contracts() -> None:
    py_ad = get_language_adapter(SupportedLanguage.PYTHON)
    assert py_ad.language == SupportedLanguage.PYTHON and ".py" in py_ad.extensions
    go_ad = get_language_adapter(SupportedLanguage.GO)
    assert go_ad.language == SupportedLanguage.GO and ".go" in go_ad.extensions

    go_unit = parse_go_unit(
        "main.go",
        'package main\nimport "os/exec"\nfunc f() { _ = exec.Command }\n',
    )
    with pytest.raises(TypeError):
        py_ad.detect_triggers(go_unit)
    with pytest.raises(TypeError):
        go_ad.detect_triggers(parse_source_unit("a.py", "X: int = 1\n"))
    assert CodeTrigger.EXTERNAL_BOUNDARY in detect_unit_triggers(go_unit)
    assert adapter_for_path("README.md") is None
    with pytest.raises(ValueError, match="Unsupported source extension"):
        parse_any_source_unit("notes.txt", "hello")


@given(
    src=st.sampled_from(tuple(BehaviorKind)),
    dst=st.sampled_from(tuple(BehaviorKind)),
)
def test_property_explain_transition_bijection_with_adjacency_matrix(
    src: BehaviorKind,
    dst: BehaviorKind,
) -> None:
    verdict = explain_transition(src, dst)
    assert verdict.allowed == (dst in ALLOWED_TRANSITIONS[src])
    assert len(verdict.reason) >= 10
