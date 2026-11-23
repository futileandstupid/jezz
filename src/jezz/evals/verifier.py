"""Track 4: Isolated workspace provisioning, JBC v1.2 grading, and held-out oracle verification."""

import ast
import importlib
import importlib.util
import inspect
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Annotated, Final, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from jezz.codes.ast_utils import SourceUnit
from jezz.codes.cohesion import compute_class_cohesion
from jezz.codes.complexity import compute_callable_metrics
from jezz.codes.inspector import BuildingInspector, ManifestDependencies, load_workspace_units
from jezz.codes.permits import CodePermit, GateAttestation, GateKind, InspectionReport
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

_PYTEST_PASSED_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s+passed")
_PYTEST_FAILED_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s+(?:failed|error|errors)")


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


@jezz_callable
def provision_scratch_workspace(
    parent_dir: Path,
    workspace_slug: IdentifierStr,
    package_name: IdentifierStr,
    jezz_root: Path,
    *,
    sync_venv: bool = True,
) -> ContainedPath:
    """Create a clean, symmetric scratch workspace under ``parent_dir / workspace_slug``."""
    resolved_parent = parent_dir.resolve()
    resolved_parent.mkdir(parents=True, exist_ok=True)
    ws_target = ContainedPath.from_parts(resolved_parent, workspace_slug)
    if ws_target.resolved_path.exists():
        shutil.rmtree(ws_target.resolved_path)
    ws_target.resolved_path.mkdir(parents=True, exist_ok=True)
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


@jezz_callable
def compute_structural_health(
    units: Annotated[Sequence[SourceUnit], Field(max_length=512)],
) -> StructuralHealthSnapshot:
    """Compute AST complexity, branch-ladder, WMC, and LCOM4 metrics over ``src/`` units."""
    prod_units = [u for u in units if not u.is_test_file]
    max_cog = 0
    max_stmts = 0
    total_elifs = 0
    max_wmc = 0
    max_lcom4 = 0
    stateless_total = 0

    for unit in prod_units:
        total_elifs += _count_unit_elifs(unit)
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
                stateless_total += len(cohesion.stateless_methods)

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
    """Port executing independent held-out Hypothesis oracle, self-tests, and mypy."""

    def verify_workspace(
        self,
        ws_dir: Path,
        task: BenchmarkTaskSpec,
    ) -> OracleSuiteVerdict:
        """Run dynamic self-tests, mypy --strict, and held-out oracle suite."""
        ...


def _parse_pytest_counts(output: str, returncode: int) -> tuple[int, int]:
    passed = sum(int(m) for m in _PYTEST_PASSED_RE.findall(output))
    failed = sum(int(m) for m in _PYTEST_FAILED_RE.findall(output))
    if returncode != 0 and failed == 0:
        failed = 1
    return passed, failed


class UvWorkspaceVerifier:
    """Subprocess workspace verifier running isolated ``uv run mypy`` and ``uv run pytest``."""

    @jezz_callable
    def __init__(
        self,
        timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 45.0,
    ) -> None:
        self._timeout_sec = timeout_sec

    def _run_uv(self, ws_dir: Path, args: tuple[str, ...]) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                ("uv", "run", *args),
                cwd=ws_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_sec,
            )
            combined = f"{proc.stdout}\n{proc.stderr}".strip()
            return proc.returncode, combined
        except subprocess.TimeoutExpired:
            return 124, f"Timed out after {self._timeout_sec}s running {' '.join(args)}."

    @jezz_callable
    def verify_workspace(
        self,
        ws_dir: Path,
        task: BenchmarkTaskSpec,
    ) -> OracleSuiteVerdict:
        """Execute ``mypy .``, workspace ``tests/``, and held-out ``Hypothesis`` oracle."""
        resolved_ws = ws_dir.resolve()
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


class InProcessWorkspaceVerifier:
    """Fast deterministic in-process verifier for sandboxed unit tests (zero subprocesses)."""

    @jezz_callable
    def __init__(self, *, assume_mypy_clean: bool = True) -> None:
        self._assume_mypy_clean = assume_mypy_clean

    @jezz_callable
    def verify_workspace(
        self,
        ws_dir: Path,
        task: BenchmarkTaskSpec,
    ) -> OracleSuiteVerdict:
        """Load ``src/<pkg>`` in-process and execute self-tests plus held-out oracle."""
        resolved_ws = ws_dir.resolve()
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


def _inspect_workspace_full_jbc(
    resolved_ws: Path,
    package_name: str,
    units: Sequence[SourceUnit],
    oracle: OracleSuiteVerdict,
) -> InspectionReport:
    base = BuildingInspector.from_workspace(resolved_ws)
    manifest = ManifestDependencies(
        runtime_roots=base.manifest.runtime_roots,
        dev_roots=base.manifest.dev_roots,
        workspace_roots=frozenset(base.manifest.workspace_roots | {package_name, "tests"}),
    )
    inspector = BuildingInspector(manifest=manifest)
    attestations = (
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=oracle.self_tests_passed,
            exit_code=0 if oracle.self_tests_passed else 1,
            checks_executed=max(1, len(units)),
            duration_ms=10.0,
            peak_memory_kb=2048,
            summary="Workspace pytest execution.",
        ),
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy",
            passed=oracle.mypy_strict_passed,
            exit_code=0 if oracle.mypy_strict_passed else 1,
            checks_executed=max(1, len(units)),
            duration_ms=10.0,
            peak_memory_kb=2048,
            summary="Workspace mypy --strict execution.",
        ),
    )
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
    units = load_workspace_units(resolved_ws, target_dirs=("src", "tests"))
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
            wall_clock_sec=metrics.wall_clock_sec,
            pce_remediation_rollbacks=metrics.pce_remediation_rollbacks,
            failure_note=metrics.failure_note or "Zero Python units emitted.",
        )

    report = _inspect_workspace_full_jbc(resolved_ws, task.package_name, units, oracle)
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
        wall_clock_sec=metrics.wall_clock_sec,
        pce_remediation_rollbacks=metrics.pce_remediation_rollbacks,
        failure_note=metrics.failure_note,
    )
