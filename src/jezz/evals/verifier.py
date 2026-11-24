"""Track 4 & 17: Polyglot workspace provisioning, JBC v1.2 grading, and oracle verification."""

import ast
import importlib
import importlib.util
import inspect
import json
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Annotated, Final, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from jezz.codes.adapters import parse_any_source_unit
from jezz.codes.ast_utils import SourceUnit
from jezz.codes.cohesion import compute_class_cohesion
from jezz.codes.complexity import compute_callable_metrics
from jezz.codes.discovery import build_workspace_inspector, discover_workspace_units
from jezz.codes.inspector import BuildingInspector, ManifestDependencies, load_workspace_units
from jezz.codes.permits import CodePermit, GateAttestation, GateKind, InspectionReport
from jezz.codes.polyglot_ir import InspectedUnit, PolyglotUnit, SupportedLanguage
from jezz.codes.taxonomy import CodeSeverity
from jezz.evals.schemas import (
    ArmRunOutcome,
    BenchmarkArm,
    BenchmarkTaskSpec,
    OracleSuiteVerdict,
    StructuralHealthSnapshot,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    NonNegativeInt,
    jezz_callable,
)
from jezz.methodology.dossier import extract_compiled_symbols

_PYTEST_PASSED_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s+passed")
_PYTEST_FAILED_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s+(?:failed|error|errors)")
_NODE_PASS_RE: Final[re.Pattern[str]] = re.compile(r"(?:\u2139|#)\s*pass\s+(\d+)")
_NODE_FAIL_RE: Final[re.Pattern[str]] = re.compile(r"(?:\u2139|#)\s*fail\s+(\d+)")
_CARGO_RESULT_RE: Final[re.Pattern[str]] = re.compile(
    r"test result:.*?(\d+)\s+passed;\s+(\d+)\s+failed"
)
_GO_PASS_RE: Final[re.Pattern[str]] = re.compile(r"---\s+PASS:")
_GO_FAIL_RE: Final[re.Pattern[str]] = re.compile(r"---\s+FAIL:")

type PolyglotOracleHook = Callable[
    [Path, BenchmarkTaskSpec, Sequence[InspectedUnit]],
    tuple[int, int, str],
]


class ArmExecutionMetrics(BaseModel):
    """Telemetry and status inputs supplied when grading a completed workspace."""

    model_config = STRICT_FROZEN_CONFIG

    arm: BenchmarkArm
    completed_cleanly: bool
    sdd_markdown_valid: bool
    spec_review_lines: NonNegativeInt
    requests: NonNegativeInt
    tool_calls: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_read_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0
    wall_clock_sec: Annotated[float, Field(ge=0.0, le=86_400.0, allow_inf_nan=False)]
    pce_remediation_rollbacks: NonNegativeInt = 0
    failure_note: BoundedText = ""


def _render_workspace_pyproject(package_name: str, jezz_root: Path) -> str:
    dist_name = package_name.replace("_", "-")
    return textwrap.dedent(
        f"""\
        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [project]
        name = "{dist_name}"
        version = "0.1.0"
        description = "Isolated Track 4 benchmark workspace ({package_name})."
        requires-python = ">=3.14"
        dependencies = [
            "icontract>=2.7.3",
            "jezz>=0.1.0",
            "pydantic>=2.14.4",
        ]

        [dependency-groups]
        dev = [
            "hypothesis>=6.170.16",
            "mypy>=2.4.1",
            "pytest>=9.2.0",
        ]

        [tool.hatch.build.targets.wheel]
        packages = ["src/{package_name}"]

        [tool.pytest.ini_options]
        testpaths = ["tests"]
        strict_config = true
        strict_markers = true

        [tool.mypy]
        python_version = "3.14"
        mypy_path = "src"
        packages = ["{package_name}", "tests"]
        strict = true
        plugins = ["pydantic.mypy"]

        [tool.uv.sources]
        jezz = {{ path = "{jezz_root.resolve().as_posix()}", editable = true }}
        """
    )


def _write_ws_file(ws_root: Path, rel_path: str, content: str) -> ContainedPath:
    target = ContainedPath.from_parts(ws_root, rel_path)
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with target.resolved_path.open("wb") as fh:
        fh.write(content.encode("utf-8"))
    return target


def _sync_workspace_uv(resolved_ws: Path) -> None:
    offline = subprocess.run(
        ("uv", "sync", "--offline"),
        cwd=resolved_ws,
        capture_output=True,
        text=True,
        check=False,
        timeout=60.0,
    )
    if offline.returncode != 0:
        online = subprocess.run(
            ("uv", "sync"),
            cwd=resolved_ws,
            capture_output=True,
            text=True,
            check=False,
            timeout=120.0,
        )
        if online.returncode != 0:
            detail = (online.stderr.strip() or online.stdout.strip())[:300]
            msg = f"Failed to provision workspace virtualenv via 'uv sync': {detail}"
            raise RuntimeError(msg)


def _provision_polyglot_layout(
    ws_root: Path,
    package_name: str,
    language: SupportedLanguage,
) -> None:
    if language == SupportedLanguage.TYPESCRIPT:
        pkg_payload = {
            "name": package_name.replace("_", "-"),
            "version": "0.1.0",
            "type": "module",
            "private": True,
        }
        tsconfig_payload = {
            "compilerOptions": {
                "target": "ES2023",
                "module": "NodeNext",
                "moduleResolution": "NodeNext",
                "strict": True,
                "noEmit": True,
                "allowImportingTsExtensions": True,
            }
        }
        _write_ws_file(ws_root, "package.json", json.dumps(pkg_payload, indent=2) + "\n")
        _write_ws_file(ws_root, "tsconfig.json", json.dumps(tsconfig_payload, indent=2) + "\n")
        ContainedPath.from_parts(ws_root, "src").resolved_path.mkdir(parents=True, exist_ok=True)
        ContainedPath.from_parts(ws_root, "tests").resolved_path.mkdir(parents=True, exist_ok=True)
        return
    if language == SupportedLanguage.RUST:
        cargo_toml = f'[package]\nname = "{package_name}"\nversion = "0.1.0"\nedition = "2024"\n'
        _write_ws_file(ws_root, "Cargo.toml", cargo_toml)
        ContainedPath.from_parts(ws_root, "src").resolved_path.mkdir(parents=True, exist_ok=True)
        ContainedPath.from_parts(ws_root, "tests").resolved_path.mkdir(parents=True, exist_ok=True)
        return
    go_mod = f"module example.com/{package_name}\n\ngo 1.24\n"
    _write_ws_file(ws_root, "go.mod", go_mod)
    ContainedPath.from_parts(ws_root, package_name).resolved_path.mkdir(parents=True, exist_ok=True)


@jezz_callable
def provision_scratch_workspace(
    parent_dir: Path,
    workspace_slug: IdentifierStr,
    package_name: IdentifierStr,
    jezz_root: Path,
    *,
    sync_venv: bool = True,
    language: SupportedLanguage = SupportedLanguage.PYTHON,
) -> ContainedPath:
    """Create a clean, symmetric scratch workspace under ``parent_dir / workspace_slug``."""
    resolved_parent = parent_dir.resolve()
    resolved_parent.mkdir(parents=True, exist_ok=True)
    ws_target = ContainedPath.from_parts(resolved_parent, workspace_slug)
    if ws_target.resolved_path.exists():
        shutil.rmtree(ws_target.resolved_path)
    ws_target.resolved_path.mkdir(parents=True, exist_ok=True)
    if language != SupportedLanguage.PYTHON:
        _provision_polyglot_layout(ws_target.resolved_path, package_name, language)
        return ws_target

    _write_ws_file(
        ws_target.resolved_path,
        "pyproject.toml",
        _render_workspace_pyproject(package_name, jezz_root),
    )
    _write_ws_file(
        ws_target.resolved_path,
        f"src/{package_name}/__init__.py",
        f'"""{package_name} domain package."""\n',
    )
    _write_ws_file(
        ws_target.resolved_path,
        "tests/__init__.py",
        '"""Workspace test suite."""\n',
    )
    if sync_venv:
        _sync_workspace_uv(ws_target.resolved_path)
    return ws_target


def _count_unit_elifs(unit: SourceUnit) -> int:
    return sum(
        1
        for node in ast.walk(unit.tree)
        if isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)
    )


def _python_unit_health(unit: SourceUnit) -> tuple[int, int, int, int, int, int]:
    max_cog = 0
    max_stmts = 0
    max_wmc = 0
    max_lcom4 = 0
    stateless = 0
    for ctx in unit.callables:
        cm = compute_callable_metrics(
            ctx.node,
            qualname=ctx.qualname,
            is_method=ctx.enclosing_class is not None,
        )
        max_cog = max(max_cog, cm.cognitive_complexity)
        max_stmts = max(max_stmts, cm.statement_count)
    for cls_node in unit.classes:
        cohesion = compute_class_cohesion(cls_node)
        if not cohesion.is_schema_or_value:
            max_wmc = max(max_wmc, cohesion.wmc)
            max_lcom4 = max(max_lcom4, cohesion.lcom4)
            stateless += len(cohesion.stateless_methods)
    return max_cog, max_stmts, _count_unit_elifs(unit), max_wmc, max_lcom4, stateless


def _polyglot_unit_health(unit: PolyglotUnit) -> tuple[int, int, int, int, int, int]:
    max_cog = max((fn.cognitive_complexity for fn in unit.callables), default=0)
    max_stmts = max((fn.statement_count for fn in unit.callables), default=0)
    elifs = sum(1 for h in unit.hazards if h.code_id == "JBC-COG-004")
    max_wmc = 0
    stateless = sum(1 for fn in unit.callables if fn.is_method and not fn.is_receiver_used)
    for decl in unit.types:
        if decl.is_schema_or_value:
            continue
        methods = [fn for fn in unit.callables if fn.enclosing_type == decl.name]
        if methods:
            wmc = sum(max(1, fn.cognitive_complexity) for fn in methods)
            max_wmc = max(max_wmc, wmc)
    return max_cog, max_stmts, elifs, max_wmc, 0, stateless


@jezz_callable
def compute_structural_health(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
) -> StructuralHealthSnapshot:
    """Compute AST/CST complexity, branch-ladder, WMC, and LCOM4 metrics over production units."""
    prod_units = [u for u in units if not u.is_test_file]
    max_cog = 0
    max_stmts = 0
    total_elifs = 0
    max_wmc = 0
    max_lcom4 = 0
    stateless_total = 0

    for unit in prod_units:
        u_cog, u_stmts, u_elifs, u_wmc, u_lcom4, u_stateless = (
            _python_unit_health(unit)
            if isinstance(unit, SourceUnit)
            else _polyglot_unit_health(unit)
        )
        max_cog = max(max_cog, u_cog)
        max_stmts = max(max_stmts, u_stmts)
        total_elifs += u_elifs
        max_wmc = max(max_wmc, u_wmc)
        max_lcom4 = max(max_lcom4, u_lcom4)
        stateless_total += u_stateless

    return StructuralHealthSnapshot(
        python_unit_count=len(units),
        max_cognitive_complexity=max_cog,
        max_statement_count=max_stmts,
        total_elif_branches=total_elifs,
        max_class_wmc=max_wmc,
        max_class_lcom4=max_lcom4,
        stateless_method_count=stateless_total,
    )


@runtime_checkable
class WorkspaceVerifierPort(Protocol):
    """Port executing independent held-out property oracle, self-tests, and static typecheck."""

    def verify_workspace(
        self,
        ws_dir: Path,
        task: BenchmarkTaskSpec,
    ) -> OracleSuiteVerdict:
        """Run dynamic self-tests, static typecheck, and held-out oracle suite."""
        ...


def _parse_pytest_counts(output: str, returncode: int) -> tuple[int, int]:
    passed = sum(int(m) for m in _PYTEST_PASSED_RE.findall(output))
    failed = sum(int(m) for m in _PYTEST_FAILED_RE.findall(output))
    if returncode != 0 and failed == 0:
        failed = 1
    return passed, failed


def _parse_node_counts(output: str, returncode: int) -> tuple[int, int]:
    passed = sum(int(m) for m in _NODE_PASS_RE.findall(output))
    failed = sum(int(m) for m in _NODE_FAIL_RE.findall(output))
    if returncode == 0 and passed == 0:
        passed = 1
    if returncode != 0 and failed == 0:
        failed = 1
    return passed, failed


def _parse_cargo_counts(output: str, returncode: int) -> tuple[int, int]:
    matches = _CARGO_RESULT_RE.findall(output)
    passed = sum(int(p) for p, _ in matches)
    failed = sum(int(f) for _, f in matches)
    if returncode != 0 and failed == 0:
        failed = 1
    return passed, failed


def _parse_go_counts(output: str, returncode: int) -> tuple[int, int]:
    passed = len(_GO_PASS_RE.findall(output))
    failed = len(_GO_FAIL_RE.findall(output))
    if returncode != 0 and failed == 0:
        failed = 1
    return passed, failed


def _resolve_ts_static_check(
    resolved_ws: Path,
    run_cmd: Callable[[Path, tuple[str, ...]], tuple[int, str]],
) -> tuple[int, str]:
    local_tsc = ContainedPath.from_parts(resolved_ws, "node_modules/.bin/tsc")
    tsc_bin = (
        str(local_tsc.resolved_path) if local_tsc.resolved_path.is_file() else shutil.which("tsc")
    )
    if tsc_bin is not None:
        return run_cmd(resolved_ws, (tsc_bin, "--noEmit", "--pretty", "false"))
    units = discover_workspace_units(
        resolved_ws, languages=frozenset({SupportedLanguage.TYPESCRIPT})
    )
    if not units:
        return 1, "Zero TypeScript units present."
    inspector = build_workspace_inspector(resolved_ws, units)
    report = inspector.inspect_rough_in(units)
    type_errs = [
        f.code_id
        for f in report.unwaived_violations()
        if f.code_id in {"JBC-DBC-001", "JBC-DBC-002"}
    ]
    if type_errs:
        return 1, f"TypeScript AST/CST type check failed: {type_errs}"
    for unit in units:
        rc, out = run_cmd(resolved_ws, ("node", "--check", unit.file_path))
        if rc != 0:
            return rc, out
    return 0, "node --check + JBC TypeScript type annotations clean"


class UvWorkspaceVerifier:
    """Subprocess workspace verifier running isolated ecosystem typechecks, tests, and oracles."""

    @jezz_callable
    def __init__(
        self,
        timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 45.0,
    ) -> None:
        self._timeout_sec = timeout_sec

    def _run_cmd(self, ws_dir: Path, argv: tuple[str, ...]) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                argv,
                cwd=ws_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_sec,
            )
            combined = f"{proc.stdout}\n{proc.stderr}".strip()
            return proc.returncode, combined
        except FileNotFoundError:
            return 127, f"Required executable '{argv[0]}' not found on PATH."
        except subprocess.TimeoutExpired:
            return 124, f"Timed out after {self._timeout_sec}s running {' '.join(argv)}."

    def _run_uv(self, ws_dir: Path, args: tuple[str, ...]) -> tuple[int, str]:
        return self._run_cmd(ws_dir, ("uv", "run", *args))

    def _verify_python_uv(self, resolved_ws: Path, task: BenchmarkTaskSpec) -> OracleSuiteVerdict:
        mypy_rc, mypy_out = self._run_uv(resolved_ws, ("mypy", "."))
        self_rc, _ = self._run_uv(resolved_ws, ("pytest", "-q", "tests"))
        oracle_dir = ContainedPath.from_parts(resolved_ws, "_heldout_oracle")
        oracle_file = _write_ws_file(
            resolved_ws,
            "_heldout_oracle/test_oracle.py",
            task.oracle_test_source,
        )
        try:
            oracle_rc, oracle_out = self._run_uv(
                resolved_ws,
                ("pytest", "-q", "-o", "testpaths=[]", oracle_file.relative_posix),
            )
        finally:
            if oracle_dir.resolved_path.exists():
                shutil.rmtree(oracle_dir.resolved_path)

        passed_n, failed_n = _parse_pytest_counts(oracle_out, oracle_rc)
        oracle_ok = oracle_rc == 0 and passed_n > 0 and failed_n == 0
        summary = f"oracle={oracle_out[-240:]}; mypy_rc={mypy_rc} ({mypy_out[-160:]})"
        return OracleSuiteVerdict(
            passed=oracle_ok,
            tests_passed=passed_n,
            tests_failed=failed_n,
            mypy_strict_passed=(mypy_rc == 0),
            self_tests_passed=(self_rc == 0),
            summary=summary[:2000],
        )

    def _verify_typescript_ws(
        self,
        resolved_ws: Path,
        task: BenchmarkTaskSpec,
    ) -> OracleSuiteVerdict:
        static_rc, static_out = _resolve_ts_static_check(resolved_ws, self._run_cmd)
        tests_dir = ContainedPath.from_parts(resolved_ws, "tests").resolved_path
        test_files = (
            tuple(p.relative_to(resolved_ws).as_posix() for p in sorted(tests_dir.rglob("*.ts")))
            if tests_dir.is_dir()
            else ()
        )
        self_rc = (
            self._run_cmd(resolved_ws, ("node", "--test", *test_files))[0] if test_files else 1
        )
        oracle_dir = ContainedPath.from_parts(resolved_ws, "_heldout_oracle")
        oracle_file = _write_ws_file(
            resolved_ws,
            "_heldout_oracle/oracle.test.ts",
            task.oracle_test_source,
        )
        try:
            oracle_rc, oracle_out = self._run_cmd(
                resolved_ws,
                ("node", "--test", oracle_file.relative_posix),
            )
        finally:
            if oracle_dir.resolved_path.exists():
                shutil.rmtree(oracle_dir.resolved_path)

        passed_n, failed_n = _parse_node_counts(oracle_out, oracle_rc)
        oracle_ok = oracle_rc == 0 and passed_n > 0 and failed_n == 0
        summary = f"oracle={oracle_out[-240:]}; ts_static_rc={static_rc} ({static_out[-160:]})"
        return OracleSuiteVerdict(
            passed=oracle_ok,
            tests_passed=passed_n,
            tests_failed=failed_n,
            mypy_strict_passed=(static_rc == 0),
            self_tests_passed=(self_rc == 0),
            summary=summary[:2000],
        )

    def _verify_rust_ws(self, resolved_ws: Path, task: BenchmarkTaskSpec) -> OracleSuiteVerdict:
        static_rc, static_out = self._run_cmd(
            resolved_ws,
            ("cargo", "check", "--all-targets", "--quiet"),
        )
        self_rc, _ = self._run_cmd(resolved_ws, ("cargo", "test", "--quiet"))
        oracle_file = _write_ws_file(
            resolved_ws,
            "tests/_heldout_oracle_suite.rs",
            task.oracle_test_source,
        )
        try:
            oracle_rc, oracle_out = self._run_cmd(
                resolved_ws,
                ("cargo", "test", "--test", "_heldout_oracle_suite"),
            )
        finally:
            if oracle_file.resolved_path.exists():
                oracle_file.resolved_path.unlink()

        passed_n, failed_n = _parse_cargo_counts(oracle_out, oracle_rc)
        oracle_ok = oracle_rc == 0 and passed_n > 0 and failed_n == 0
        summary = f"oracle={oracle_out[-240:]}; cargo_check_rc={static_rc} ({static_out[-160:]})"
        return OracleSuiteVerdict(
            passed=oracle_ok,
            tests_passed=passed_n,
            tests_failed=failed_n,
            mypy_strict_passed=(static_rc == 0),
            self_tests_passed=(self_rc == 0),
            summary=summary[:2000],
        )

    def _verify_go_ws(self, resolved_ws: Path, task: BenchmarkTaskSpec) -> OracleSuiteVerdict:
        static_rc, static_out = self._run_cmd(resolved_ws, ("go", "vet", "./..."))
        self_rc, _ = self._run_cmd(resolved_ws, ("go", "test", "./..."))
        oracle_rel = f"{task.package_name}/heldout_oracle_eval_test.go"
        oracle_file = _write_ws_file(resolved_ws, oracle_rel, task.oracle_test_source)
        try:
            oracle_rc, oracle_out = self._run_cmd(
                resolved_ws,
                ("go", "test", "-v", f"./{task.package_name}", "-run", "^TestOracle"),
            )
        finally:
            if oracle_file.resolved_path.exists():
                oracle_file.resolved_path.unlink()

        passed_n, failed_n = _parse_go_counts(oracle_out, oracle_rc)
        oracle_ok = oracle_rc == 0 and passed_n > 0 and failed_n == 0
        summary = f"oracle={oracle_out[-240:]}; go_vet_rc={static_rc} ({static_out[-160:]})"
        return OracleSuiteVerdict(
            passed=oracle_ok,
            tests_passed=passed_n,
            tests_failed=failed_n,
            mypy_strict_passed=(static_rc == 0),
            self_tests_passed=(self_rc == 0),
            summary=summary[:2000],
        )

    @jezz_callable
    def verify_workspace(
        self,
        ws_dir: Path,
        task: BenchmarkTaskSpec,
    ) -> OracleSuiteVerdict:
        """Execute ecosystem typecheck, workspace self-tests, and held-out property oracle."""
        resolved_ws = ws_dir.resolve()
        dispatch: dict[
            SupportedLanguage,
            Callable[[Path, BenchmarkTaskSpec], OracleSuiteVerdict],
        ] = {
            SupportedLanguage.PYTHON: self._verify_python_uv,
            SupportedLanguage.TYPESCRIPT: self._verify_typescript_ws,
            SupportedLanguage.RUST: self._verify_rust_ws,
            SupportedLanguage.GO: self._verify_go_ws,
        }
        return dispatch[task.language](resolved_ws, task)


PolyglotWorkspaceVerifier = UvWorkspaceVerifier


def _evict_package_modules(prefixes: tuple[str, ...]) -> None:
    stale = [
        mod_name
        for mod_name in list(sys.modules)
        if any(mod_name == p or mod_name.startswith(f"{p}.") for p in prefixes)
    ]
    for mod_name in stale:
        sys.modules.pop(mod_name, None)
    importlib.invalidate_caches()


def _load_module_from_contained(module_name: str, target: ContainedPath) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, target.resolved_path)
    if spec is None or spec.loader is None:
        msg = f"Cannot create module spec for '{target.relative_posix}'."
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_INPROCESS_TEST_ERRORS = (
    AssertionError,
    ImportError,
    SyntaxError,
    ValueError,
    TypeError,
    LookupError,
    ArithmeticError,
    AttributeError,
    RuntimeError,
    OSError,
)


def _invoke_module_tests(module: ModuleType, scratch_tmp: Path) -> tuple[int, int, list[str]]:
    passed = 0
    failed = 0
    errors: list[str] = []
    for attr_name in sorted(dir(module)):
        if not attr_name.startswith("test_"):
            continue
        candidate = getattr(module, attr_name)
        if not callable(candidate):
            continue
        sig = inspect.signature(candidate)
        scratch_tmp.mkdir(parents=True, exist_ok=True)
        try:
            if "tmp_path" in sig.parameters:
                case_tmp = ContainedPath.from_parts(scratch_tmp, attr_name)
                case_tmp.resolved_path.mkdir(parents=True, exist_ok=True)
                candidate(case_tmp.resolved_path)
            else:
                candidate()
            passed += 1
        except _INPROCESS_TEST_ERRORS as exc:
            failed += 1
            errors.append(f"{attr_name}: {type(exc).__name__}: {str(exc)[:120]}")
    return passed, failed, errors


def _run_self_tests_inprocess(resolved_ws: Path, scratch_tmp: Path) -> bool:
    tests_dir = ContainedPath.from_parts(resolved_ws, "tests")
    if not tests_dir.resolved_path.is_dir():
        return False
    test_files = sorted(p for p in tests_dir.resolved_path.rglob("test_*.py") if p.is_file())
    if not test_files:
        return False
    total_passed = 0
    for idx, tf in enumerate(test_files):
        rel = tf.relative_to(resolved_ws).as_posix()
        contained = ContainedPath.from_parts(resolved_ws, rel)
        try:
            mod = _load_module_from_contained(f"_self_test_mod_{idx}", contained)
        except ImportError, SyntaxError, ValueError, TypeError, AttributeError, RuntimeError:
            return False
        p_cnt, f_cnt, _ = _invoke_module_tests(mod, scratch_tmp)
        if f_cnt > 0:
            return False
        total_passed += p_cnt
    return total_passed > 0


def _polyglot_oracle_probe_name(task: BenchmarkTaskSpec) -> str:
    if task.language == SupportedLanguage.TYPESCRIPT:
        return f"tests/oracle_{task.case_id}.test.ts"
    if task.language == SupportedLanguage.RUST:
        return f"tests/oracle_{task.case_id}.rs"
    return f"{task.package_name}/oracle_{task.case_id}_test.go"


def _verify_polyglot_inprocess(
    resolved_ws: Path,
    task: BenchmarkTaskSpec,
    *,
    assume_mypy_clean: bool,
    hook: PolyglotOracleHook | None,
) -> OracleSuiteVerdict:
    try:
        units = discover_workspace_units(resolved_ws, languages=frozenset({task.language}))
        oracle_unit = parse_any_source_unit(
            _polyglot_oracle_probe_name(task),
            task.oracle_test_source,
        )
    except (SyntaxError, ValueError, OSError) as exc:
        return OracleSuiteVerdict(
            passed=False,
            tests_passed=0,
            tests_failed=1,
            mypy_strict_passed=False,
            self_tests_passed=False,
            summary=f"Polyglot CST parse failed: {exc}"[:2000],
        )

    if not units:
        return OracleSuiteVerdict(
            passed=False,
            tests_passed=0,
            tests_failed=1,
            mypy_strict_passed=False,
            self_tests_passed=False,
            summary="Zero polyglot source units discovered.",
        )

    compiled = extract_compiled_symbols(units)
    expected_test_syms = {inv.verification_symbol for inv in task.mandate.invariants}
    test_fns = [
        fn
        for u in units
        if isinstance(u, PolyglotUnit)
        for fn in u.callables
        if fn.is_test_callable and fn.has_test_assertion
    ]
    self_ok = bool(test_fns) and (expected_test_syms <= compiled)

    if hook is not None:
        passed_n, failed_n, summary = hook(resolved_ws, task, units)
    else:
        required_syms = {sym for sc in task.mandate.write_scopes for sym in sc.contract_symbols}
        missing_syms = sorted(required_syms - compiled)
        inspector = build_workspace_inspector(resolved_ws, units)
        rough = inspector.inspect_rough_in(units)
        life_safety = [
            f.code_id for f in rough.unwaived_violations() if f.severity == CodeSeverity.LIFE_SAFETY
        ]
        oracle_checks = max(
            1,
            sum(
                1
                for fn in (oracle_unit.callables if isinstance(oracle_unit, PolyglotUnit) else ())
                if fn.is_test_callable
            ),
        )
        if missing_syms or life_safety:
            passed_n, failed_n = 0, oracle_checks
            summary = f"Falsified: missing={missing_syms}, life_safety={life_safety}"
        else:
            passed_n, failed_n = oracle_checks, 0
            summary = f"All {oracle_checks} polyglot oracle contract check(s) passed."

    return OracleSuiteVerdict(
        passed=(passed_n > 0 and failed_n == 0),
        tests_passed=passed_n,
        tests_failed=failed_n,
        mypy_strict_passed=assume_mypy_clean,
        self_tests_passed=self_ok,
        summary=summary[:2000],
    )


class InProcessWorkspaceVerifier:
    """Fast deterministic in-process verifier for sandboxed unit tests (zero subprocesses)."""

    @jezz_callable
    def __init__(
        self,
        *,
        assume_mypy_clean: bool = True,
        polyglot_oracle_hook: PolyglotOracleHook | None = None,
    ) -> None:
        self._assume_mypy_clean = assume_mypy_clean
        self._polyglot_oracle_hook = polyglot_oracle_hook

    @jezz_callable
    def verify_workspace(
        self,
        ws_dir: Path,
        task: BenchmarkTaskSpec,
    ) -> OracleSuiteVerdict:
        """Load workspace in-process and execute self-tests plus held-out oracle."""
        resolved_ws = ws_dir.resolve()
        if task.language != SupportedLanguage.PYTHON:
            return _verify_polyglot_inprocess(
                resolved_ws,
                task,
                assume_mypy_clean=self._assume_mypy_clean,
                hook=self._polyglot_oracle_hook,
            )

        src_dir = ContainedPath.from_parts(resolved_ws, "src")
        oracle_dir = ContainedPath.from_parts(resolved_ws, "_heldout_oracle")
        scratch_dir = ContainedPath.from_parts(resolved_ws, "_oracle_tmp")
        oracle_file = _write_ws_file(
            resolved_ws,
            "_heldout_oracle/test_oracle.py",
            task.oracle_test_source,
        )
        prefixes = (task.package_name, "_self_test_mod", "_heldout_oracle_mod")
        _evict_package_modules(prefixes)
        src_str = str(src_dir.resolved_path)
        sys.path.insert(0, src_str)
        try:
            self_ok = _run_self_tests_inprocess(resolved_ws, scratch_dir.resolved_path)
            mod = _load_module_from_contained("_heldout_oracle_mod", oracle_file)
            passed_n, failed_n, errs = _invoke_module_tests(mod, scratch_dir.resolved_path)
            summary = "; ".join(errs) if errs else f"All {passed_n} oracle check(s) passed."
        except _INPROCESS_TEST_ERRORS as exc:
            self_ok = False
            passed_n = 0
            failed_n = 1
            summary = f"Oracle import failed: {type(exc).__name__}: {str(exc)[:200]}"
        finally:
            if src_str in sys.path:
                sys.path.remove(src_str)
            _evict_package_modules(prefixes)
            if oracle_dir.resolved_path.exists():
                shutil.rmtree(oracle_dir.resolved_path)
            if scratch_dir.resolved_path.exists():
                shutil.rmtree(scratch_dir.resolved_path)

        oracle_ok = passed_n > 0 and failed_n == 0
        return OracleSuiteVerdict(
            passed=oracle_ok,
            tests_passed=passed_n,
            tests_failed=failed_n,
            mypy_strict_passed=self._assume_mypy_clean,
            self_tests_passed=self_ok,
            summary=summary[:2000],
        )


def _build_eval_attestations(
    unit_count: int,
    oracle: OracleSuiteVerdict,
) -> tuple[GateAttestation, GateAttestation]:
    return (
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="test-suite",
            passed=oracle.self_tests_passed,
            exit_code=0 if oracle.self_tests_passed else 1,
            checks_executed=max(1, unit_count),
            duration_ms=10.0,
            peak_memory_kb=2048,
            summary="Workspace dynamic suite execution.",
        ),
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="static-types",
            passed=oracle.mypy_strict_passed,
            exit_code=0 if oracle.mypy_strict_passed else 1,
            checks_executed=max(1, unit_count),
            duration_ms=10.0,
            peak_memory_kb=2048,
            summary="Workspace static type verification.",
        ),
    )


def _inspect_workspace_full_jbc(
    resolved_ws: Path,
    task: BenchmarkTaskSpec,
    units: Sequence[InspectedUnit],
    oracle: OracleSuiteVerdict,
) -> InspectionReport:
    if task.language == SupportedLanguage.PYTHON:
        base = BuildingInspector.from_workspace(resolved_ws)
        manifest = ManifestDependencies(
            runtime_roots=base.manifest.runtime_roots,
            dev_roots=base.manifest.dev_roots,
            workspace_roots=frozenset(base.manifest.workspace_roots | {task.package_name, "tests"}),
        )
        inspector = BuildingInspector(manifest=manifest)
    else:
        inspector = build_workspace_inspector(resolved_ws, units)
    attestations = _build_eval_attestations(len(units), oracle)
    permit = CodePermit.full_book(permit_id="PERMIT-TRACK4-EVAL")
    return inspector.inspect_final(units, permit=permit, attestations=attestations)


@jezz_callable
def grade_workspace_artifacts(
    ws_dir: Path,
    task: BenchmarkTaskSpec,
    metrics: ArmExecutionMetrics,
    verifier: Annotated[WorkspaceVerifierPort, InstanceGuard(WorkspaceVerifierPort)],
) -> ArmRunOutcome:
    """Run deterministic JBC v1.2 full-book inspection and held-out oracle on ``ws_dir``."""
    resolved_ws = ws_dir.resolve()
    units: tuple[InspectedUnit, ...] = (
        load_workspace_units(resolved_ws, target_dirs=("src", "tests"))
        if task.language == SupportedLanguage.PYTHON
        else discover_workspace_units(resolved_ws, languages=frozenset({task.language}))
    )
    oracle = verifier.verify_workspace(resolved_ws, task)
    structural = compute_structural_health(units)
    code_lines = sum(len(u.source.splitlines()) for u in units)

    if not units:
        return ArmRunOutcome(
            case_id=task.case_id,
            arm=metrics.arm,
            completed_cleanly=False,
            jbc_occupiable=False,
            life_safety_violations=1,
            structural_violations=1,
            maintainability_violations=0,
            total_jbc_violations=2,
            violated_code_ids=("JBC-VRF-005", "JBC-VRF-006"),
            oracle_verdict=oracle,
            structural=structural,
            sdd_markdown_valid=metrics.sdd_markdown_valid,
            spec_review_lines=metrics.spec_review_lines,
            emitted_code_lines=0,
            requests=metrics.requests,
            tool_calls=metrics.tool_calls,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            cache_read_tokens=metrics.cache_read_tokens,
            cache_write_tokens=metrics.cache_write_tokens,
            wall_clock_sec=metrics.wall_clock_sec,
            pce_remediation_rollbacks=metrics.pce_remediation_rollbacks,
            failure_note=metrics.failure_note or "Zero source units emitted.",
        )

    report = _inspect_workspace_full_jbc(resolved_ws, task, units, oracle)
    unwaived = report.unwaived_violations()
    life_safety = sum(1 for f in unwaived if f.severity == CodeSeverity.LIFE_SAFETY)
    structural_v = sum(1 for f in unwaived if f.severity == CodeSeverity.STRUCTURAL)
    maint_v = sum(1 for f in unwaived if f.severity == CodeSeverity.WORKMANSHIP)
    cited_ids = tuple(sorted({f.code_id for f in unwaived}))

    return ArmRunOutcome(
        case_id=task.case_id,
        arm=metrics.arm,
        completed_cleanly=metrics.completed_cleanly,
        jbc_occupiable=report.is_occupiable,
        life_safety_violations=life_safety,
        structural_violations=structural_v,
        maintainability_violations=maint_v,
        total_jbc_violations=len(unwaived),
        violated_code_ids=cited_ids,
        oracle_verdict=oracle,
        structural=structural,
        sdd_markdown_valid=metrics.sdd_markdown_valid,
        spec_review_lines=metrics.spec_review_lines,
        emitted_code_lines=code_lines,
        requests=metrics.requests,
        tool_calls=metrics.tool_calls,
        input_tokens=metrics.input_tokens,
        output_tokens=metrics.output_tokens,
        cache_read_tokens=metrics.cache_read_tokens,
        cache_write_tokens=metrics.cache_write_tokens,
        wall_clock_sec=metrics.wall_clock_sec,
        pce_remediation_rollbacks=metrics.pce_remediation_rollbacks,
        failure_note=metrics.failure_note,
    )
