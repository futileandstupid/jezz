"""Track 14: Polyglot --ratchet gate scoping, configurable timeouts, and O(|delta|) staging."""

import io
import subprocess
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

import jezz.agents.workspace as ws_mod
from jezz.agents import (
    CargoSubprocessGateRunner,
    FilePatchArtifact,
    JezzGatesConfig,
    PolyglotSubprocessGateRunner,
    TypeScriptSubprocessGateRunner,
    UvSubprocessGateRunner,
    WorkspaceSandbox,
    discover_gates_config,
    resolve_gate_timeout_sec,
    verify_runtime_importable_patches,
)
from jezz.cli import main
from jezz.codes import (
    BuildingInspector,
    CodePermit,
    ManifestDependencies,
    OccupancyRuling,
    parse_go_unit,
    parse_rust_unit,
    parse_source_unit,
    parse_typescript_unit,
)
from jezz.guards import ContainedPath


def _write(root: Path, rel: str, content: str) -> Path:
    target = ContainedPath.from_parts(root, rel).resolved_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _fake_completed(args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="1 passed\n", stderr="")


def _assert_python_and_ts_ratchet_scoping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    py_ws = ContainedPath.from_parts(tmp_path, "py_ws").resolved_path
    _write(py_ws, "src/pkg/calc.py", "def add(x: int) -> int:\n    return x + 1\n")
    _write(py_ws, "tests/test_calc.py", "def test_add() -> None:\n    assert 1 + 1 == 2\n")
    _write(py_ws, "tests/test_legacy.py", "def test_broken() -> None:\n    assert 1 == 2\n")

    recorded_py: list[tuple[str, ...]] = []

    def _mock_py_run(
        argv: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        recorded_py.append(tuple(argv))
        return _fake_completed(tuple(argv))

    monkeypatch.setattr("jezz.agents.workspace.subprocess.run", _mock_py_run)
    py_sb = WorkspaceSandbox(py_ws, frozenset({"src/pkg/calc.py", "tests/test_calc.py"}))
    py_units = (
        parse_source_unit("src/pkg/calc.py", "def add(x: int) -> int:\n    return x + 1\n"),
        parse_source_unit("tests/test_calc.py", "def test_add() -> None:\n    assert True\n"),
    )
    UvSubprocessGateRunner(timeout_sec=90.0, ratchet_to_scope=True).run_gates(py_sb, py_units)
    assert any("tests/test_calc.py" in cmd and "pytest" in cmd for cmd in recorded_py)
    assert not any("tests/test_legacy.py" in cmd for cmd in recorded_py)

    ts_ws = ContainedPath.from_parts(tmp_path, "ts_ws").resolved_path
    _write(ts_ws, "package.json", '{"name": "ts-demo", "scripts": {"test": "vitest run"}}\n')
    vitest_bin = _write(ts_ws, "node_modules/.bin/vitest", "#!/bin/sh\nexit 0\n")
    tsc_bin = _write(ts_ws, "node_modules/.bin/tsc", "#!/bin/sh\nexit 0\n")
    vitest_bin.chmod(0o755)
    tsc_bin.chmod(0o755)
    _write(ts_ws, "src/calc.ts", "export function inc(x: number): number { return x + 1; }\n")
    _write(ts_ws, "src/calc.test.ts", "export function testInc(): void {}\n")

    recorded_ts: list[tuple[str, ...]] = []

    def _mock_pg_run(
        argv: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        recorded_ts.append(tuple(argv))
        return _fake_completed(tuple(argv))

    monkeypatch.setattr("jezz.agents.polyglot_gates.subprocess.run", _mock_pg_run)
    ts_sb = WorkspaceSandbox(ts_ws, frozenset({"src/calc.ts", "src/calc.test.ts"}))
    ts_units = (
        parse_typescript_unit(
            "src/calc.ts",
            "export function inc(x: number): number { return x + 1; }\n",
        ),
        parse_typescript_unit("src/calc.test.ts", "export function testInc(): void {}\n"),
    )
    TypeScriptSubprocessGateRunner(ratchet_to_scope=True).run_gates(ts_sb, ts_units)
    assert any(cmd[-2:] == ("run", "src/calc.test.ts") for cmd in recorded_ts)


def _assert_rust_and_go_ratchet_scoping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, ...]] = []

    def _mock_pg_run(
        argv: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        recorded.append(tuple(argv))
        return _fake_completed(tuple(argv))

    monkeypatch.setattr("jezz.agents.polyglot_gates.subprocess.run", _mock_pg_run)

    rs_ws = ContainedPath.from_parts(tmp_path, "rs_ws").resolved_path
    _write(rs_ws, "Cargo.toml", '[workspace]\nmembers = ["crates/auth", "crates/legacy"]\n')
    _write(
        rs_ws,
        "crates/auth/Cargo.toml",
        '[package]\nname = "acme_auth"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    rs_src = (
        "pub fn verify_tok(x: u32) -> u32 { x + 1 }\n#[test]\nfn t_tok() { assert_eq!(2, 2); }\n"
    )
    _write(rs_ws, "crates/auth/src/lib.rs", rs_src)
    rs_sb = WorkspaceSandbox(rs_ws, frozenset({"crates/auth/src/lib.rs"}))
    rs_units = (parse_rust_unit("crates/auth/src/lib.rs", rs_src),)
    CargoSubprocessGateRunner(ratchet_to_scope=True).run_gates(rs_sb, rs_units)
    assert ("cargo", "check", "--all-targets", "--quiet", "-p", "acme_auth") in recorded
    assert ("cargo", "test", "--quiet", "-p", "acme_auth") in recorded

    recorded.clear()
    go_ws = ContainedPath.from_parts(tmp_path, "go_ws").resolved_path
    _write(go_ws, "go.mod", "module example.com/svc\n\ngo 1.24\n")
    go_src = "package quota\n\nfunc Cap(x int) int {\n\tif x < 0 { return 0 }\n\treturn x\n}\n"
    go_tst = 'package quota\n\nimport "testing"\n\nfunc TestCap(t *testing.T) {}\n'
    _write(go_ws, "internal/quota/quota.go", go_src)
    _write(go_ws, "internal/quota/quota_test.go", go_tst)
    go_sb = WorkspaceSandbox(
        go_ws,
        frozenset({"internal/quota/quota.go", "internal/quota/quota_test.go"}),
    )
    go_units = (
        parse_go_unit("internal/quota/quota.go", go_src),
        parse_go_unit("internal/quota/quota_test.go", go_tst),
    )
    PolyglotSubprocessGateRunner(timeout_sec=150.0, ratchet_to_scope=True).run_gates(
        go_sb, go_units
    )
    assert ("go", "vet", "./internal/quota") in recorded
    assert ("go", "test", "-v", "./internal/quota") in recorded


def test_polyglot_ratchet_scopes_compiler_and_test_invocations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_python_and_ts_ratchet_scoping(tmp_path, monkeypatch)
    _assert_rust_and_go_ratchet_scoping(tmp_path, monkeypatch)


def test_gate_timeout_configurable_via_cli_and_pyproject_up_to_600s(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.jezz.gates]\ntimeout_sec = 180.0\n',
    )
    cfg = discover_gates_config((tmp_path,))
    assert cfg is not None and cfg.timeout_sec == 180.0
    assert (
        resolve_gate_timeout_sec(cli_timeout_sec=None, project_config=cfg, default_sec=45.0)
        == 180.0
    )
    assert (
        resolve_gate_timeout_sec(cli_timeout_sec=300.0, project_config=cfg, default_sec=45.0)
        == 300.0
    )

    with pytest.raises(ValidationError):
        JezzGatesConfig(timeout_sec=601.0)
    with pytest.raises(ValidationError):
        UvSubprocessGateRunner(timeout_sec=0.0)

    monkeypatch.setattr(
        "jezz.agents.workspace.subprocess.run",
        lambda argv, **_kw: _fake_completed(tuple(argv)),
    )
    sb = WorkspaceSandbox(tmp_path, frozenset({"src/demo/calc.py", "tests/test_calc.py"}))
    u_prod = parse_source_unit(
        "src/demo/calc.py",
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\ndef f(x: PositiveInt) -> PositiveInt:\n    return x\n",
    )
    u_test = parse_source_unit(
        "tests/test_calc.py",
        "from demo.calc import f\n\ndef test_f() -> None:\n    assert f(1) == 1\n",
    )
    atts = UvSubprocessGateRunner(timeout_sec=180.0).run_gates(sb, (u_prod, u_test))
    slow_atts = tuple(att.model_copy(update={"duration_ms": 95_000.0}) for att in atts)
    assert all(att.max_duration_ms == 180_000.0 for att in slow_atts)
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"jezz"}),
            dev_roots=frozenset({"pytest"}),
            workspace_roots=frozenset({"demo", "tests"}),
        )
    )
    final_rep = inspector.inspect_final(
        (u_prod, u_test),
        permit=CodePermit.full_book("PERMIT-SLOW-GATE"),
        attestations=slow_atts,
    )
    assert final_rep.ruling == OccupancyRuling.APPROVED

    err_buf = io.StringIO()
    rc = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "antigravity:claude-sonnet-5-5-medium",
            "--prompt",
            "Add helper",
            "--gate-timeout",
            "650",
        ],
        stderr=err_buf,
    )
    assert rc == 2


def test_overlay_smoke_import_supports_flat_and_src_layouts_in_o_delta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_ws = ContainedPath.from_parts(tmp_path, "src_ws").resolved_path
    _write(src_ws, "src/big_pkg/__init__.py", '"""Pkg."""\n')
    for idx in range(25):
        _write(src_ws, f"src/big_pkg/sib_{idx}.py", f"VAL_{idx}: int = {idx}\n")

    written_paths: list[str] = []
    orig_write = ws_mod._write_contained_utf8

    def _spy_write(target: ContainedPath, content: str) -> None:
        written_paths.append(target.relative_posix)
        orig_write(target, content)

    monkeypatch.setattr(ws_mod, "_write_contained_utf8", _spy_write)
    src_sb = WorkspaceSandbox(src_ws, frozenset({"src/big_pkg/target.py"}))
    valid_patch = FilePatchArtifact(
        file_path="src/big_pkg/target.py",
        source_code=(
            "from pydantic import BaseModel\n"
            "from big_pkg.sib_0 import VAL_0\n\n"
            "class Item(BaseModel):\n"
            "    count: int = VAL_0\n"
        ),
        rationale="test overlay",
    )
    assert verify_runtime_importable_patches(src_sb, (valid_patch,)) is None
    assert len(written_paths) == 1

    flat_ws = ContainedPath.from_parts(tmp_path, "flat_ws").resolved_path
    _write(flat_ws, "acme_flat/__init__.py", '"""Flat pkg."""\n')
    _write(flat_ws, "acme_flat/helper.py", "BASE_OFFSET: int = 10\n")
    flat_sb = WorkspaceSandbox(flat_ws, frozenset({"acme_flat/service.py"}))
    good_flat = FilePatchArtifact(
        file_path="acme_flat/service.py",
        source_code=(
            "from acme_flat.helper import BASE_OFFSET\n\n"
            "def compute(x: int) -> int:\n"
            "    return x + BASE_OFFSET\n"
        ),
        rationale="valid flat module",
    )
    assert verify_runtime_importable_patches(flat_sb, (good_flat,)) is None

    bad_flat = FilePatchArtifact(
        file_path="acme_flat/service.py",
        source_code="BROKEN: int = 1 // 0\n",
        rationale="import-time crash in flat layout",
    )
    err = verify_runtime_importable_patches(flat_sb, (bad_flat,))
    assert err is not None
    assert "ZeroDivisionError" in err


@given(
    cli_val=st.one_of(
        st.none(),
        st.floats(min_value=0.5, max_value=600.0, allow_nan=False, allow_infinity=False),
    ),
    cfg_val=st.one_of(
        st.none(),
        st.floats(min_value=0.5, max_value=600.0, allow_nan=False, allow_infinity=False),
    ),
    default_val=st.floats(min_value=1.0, max_value=120.0, allow_nan=False, allow_infinity=False),
)
def test_property_gate_timeout_precedence_and_bounds(
    cli_val: float | None,
    cfg_val: float | None,
    default_val: float,
) -> None:
    cfg = JezzGatesConfig(timeout_sec=cfg_val) if cfg_val is not None else None
    resolved = resolve_gate_timeout_sec(
        cli_timeout_sec=cli_val,
        project_config=cfg,
        default_sec=default_val,
    )
    assert 0.0 < resolved <= 600.0
    if cli_val is not None:
        assert resolved == cli_val
    elif cfg_val is not None:
        assert resolved == cfg_val
    else:
        assert resolved == default_val
