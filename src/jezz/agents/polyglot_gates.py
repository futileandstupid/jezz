"""Polyglot subprocess DynamicGatePort runners across Python, TypeScript, Rust, and Go."""

import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field, TypeAdapter, ValidationError

from jezz.agents.polyglot_prompts import detect_workspace_languages, is_polyglot_test_path
from jezz.agents.workspace import DynamicGatePort, UvSubprocessGateRunner, WorkspaceSandbox
from jezz.codes.ast_utils import SourceUnit
from jezz.codes.permits import GateAttestation, GateKind
from jezz.codes.polyglot_ir import InspectedUnit, PolyglotUnit, SupportedLanguage
from jezz.guards import (
    BoundedText,
    ContainedPath,
    InstanceGuard,
    jezz_callable,
    read_bounded_text,
)

_PKG_JSON_ADAPTER: Final[TypeAdapter[dict[BoundedText, object]]] = TypeAdapter(
    dict[BoundedText, object]
)
_PASSED_COUNT_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\s+passed|pass\s+(\d+)|---\s+PASS:")
_DEFAULT_NPM_PLACEHOLDER: Final[str] = "no test specified"


@dataclass(frozen=True)
class _SubprocessGateSpec:
    """Cohesive invocation specification for a single bounded subprocess gate."""

    argv: tuple[str, ...]
    gate_kind: GateKind
    tool_name: str
    fallback_checks: int
    extra_env: Mapping[str, str] | None = None


def _count_passed_checks(output: str, fallback: int) -> int:
    total = 0
    for match in _PASSED_COUNT_RE.finditer(output):
        g1, g2 = match.group(1), match.group(2)
        if g1 is not None:
            total += int(g1)
        elif g2 is not None:
            total += int(g2)
        else:
            total += 1
    return max(1, total) if total > 0 else max(1, fallback)


def _failed_attestation(
    gate_kind: GateKind,
    tool_name: str,
    exit_code: int,
    duration_ms: float,
    summary: str,
) -> GateAttestation:
    return GateAttestation(
        gate_kind=gate_kind,
        tool_name=tool_name,
        passed=False,
        exit_code=exit_code,
        checks_executed=1,
        duration_ms=max(0.05, duration_ms),
        peak_memory_kb=1024,
        summary=summary[:512],
    )


def _run_subprocess_gate(
    repo_root: Path,
    spec: _SubprocessGateSpec,
    timeout_sec: float,
) -> GateAttestation:
    merged_env = {**os.environ, **spec.extra_env} if spec.extra_env else None
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            spec.argv,
            cwd=repo_root,
            env=merged_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except FileNotFoundError:
        return _failed_attestation(
            spec.gate_kind,
            spec.tool_name,
            127,
            (time.perf_counter() - t0) * 1000.0,
            f"Required binary '{spec.argv[0]}' not found on PATH.",
        )
    except subprocess.TimeoutExpired:
        return _failed_attestation(
            spec.gate_kind,
            spec.tool_name,
            124,
            timeout_sec * 1000.0,
            f"Gate '{spec.tool_name}' exceeded {timeout_sec:.1f}s timeout.",
        )
    elapsed_ms = max(0.05, (time.perf_counter() - t0) * 1000.0)
    combined = f"{proc.stdout}\n{proc.stderr}".strip()
    summary = (proc.stdout.strip() or proc.stderr.strip() or f"{spec.tool_name} completed")[:512]
    checks = (
        _count_passed_checks(combined, spec.fallback_checks)
        if spec.gate_kind == GateKind.DYNAMIC_SUITE
        else max(1, spec.fallback_checks)
    )
    return GateAttestation(
        gate_kind=spec.gate_kind,
        tool_name=spec.tool_name,
        passed=(proc.returncode == 0),
        exit_code=proc.returncode,
        checks_executed=checks,
        duration_ms=elapsed_ms,
        peak_memory_kb=1024,
        summary=summary,
    )


def _has_custom_npm_test_script(repo_root: Path) -> bool:
    pkg_json = ContainedPath.from_parts(repo_root, "package.json")
    if not pkg_json.resolved_path.is_file():
        return False
    try:
        data = _PKG_JSON_ADAPTER.validate_json(read_bounded_text(pkg_json, max_bytes=524_288))
    except ValidationError, OSError:
        return False
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return False
    test_cmd = scripts.get("test")
    return (
        isinstance(test_cmd, str)
        and bool(test_cmd.strip())
        and (_DEFAULT_NPM_PLACEHOLDER not in test_cmd.lower())
    )


def _resolve_local_node_bin(repo_root: Path, binary_name: str) -> str | None:
    candidate = ContainedPath.from_parts(repo_root, f"node_modules/.bin/{binary_name}")
    if candidate.resolved_path.is_file():
        return str(candidate.resolved_path)
    return None


def _build_tsc_argv(repo_root: Path, ts_files: Sequence[str]) -> tuple[str, ...]:
    tsc_bin = _resolve_local_node_bin(repo_root, "tsc") or "tsc"
    has_tsconfig = ContainedPath.from_parts(repo_root, "tsconfig.json").resolved_path.is_file()
    if has_tsconfig or not ts_files:
        return tsc_bin, "--noEmit", "--pretty", "false"
    return (
        tsc_bin,
        "--strict",
        "--noEmit",
        "--pretty",
        "false",
        "--target",
        "ES2023",
        "--module",
        "NodeNext",
        "--moduleResolution",
        "NodeNext",
        *ts_files,
    )


def _run_ts_dynamic_suite(
    repo_root: Path,
    test_files: Sequence[str],
    timeout_sec: float,
) -> GateAttestation:
    local_vitest = _resolve_local_node_bin(repo_root, "vitest")
    if local_vitest is not None:
        spec = _SubprocessGateSpec(
            argv=(local_vitest, "run"),
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="vitest",
            fallback_checks=max(1, len(test_files)),
        )
        return _run_subprocess_gate(repo_root, spec, timeout_sec)
    if _has_custom_npm_test_script(repo_root):
        spec = _SubprocessGateSpec(
            argv=("npm", "test", "--silent"),
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="npm-test",
            fallback_checks=max(1, len(test_files)),
        )
        return _run_subprocess_gate(repo_root, spec, timeout_sec)
    if not test_files:
        return _failed_attestation(
            GateKind.DYNAMIC_SUITE,
            "node-test",
            1,
            0.05,
            "Zero TypeScript test files present in workspace.",
        )
    spec = _SubprocessGateSpec(
        argv=("node", "--test", *test_files),
        gate_kind=GateKind.DYNAMIC_SUITE,
        tool_name="node-test",
        fallback_checks=len(test_files),
    )
    return _run_subprocess_gate(repo_root, spec, timeout_sec)


def _polyglot_units_for_lang(
    units: Sequence[InspectedUnit],
    language: SupportedLanguage,
) -> list[PolyglotUnit]:
    matched: list[PolyglotUnit] = []
    for unit in units:
        if isinstance(unit, PolyglotUnit) and unit.language == language:
            matched.append(unit)
    return matched


class TypeScriptSubprocessGateRunner:
    """Subprocess dynamic and static-type gate runner for TypeScript (``.ts`` / ``.tsx``)."""

    @jezz_callable
    def __init__(
        self,
        timeout_sec: Annotated[float, Field(gt=0.0, le=60.0, allow_inf_nan=False)] = 45.0,
    ) -> None:
        self._timeout_sec = timeout_sec

    @jezz_callable
    def run_gates(
        self,
        sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
        units: Sequence[InspectedUnit],
    ) -> tuple[GateAttestation, ...]:
        """Run TypeScript dynamic test suite and ``tsc --noEmit`` static verification."""
        ts_units = _polyglot_units_for_lang(units, SupportedLanguage.TYPESCRIPT)
        ts_files = tuple(u.file_path for u in ts_units)
        test_files = tuple(
            u.file_path for u in ts_units if u.is_test_file or is_polyglot_test_path(u.file_path)
        )
        dyn_att = _run_ts_dynamic_suite(sandbox.repo_root, test_files, self._timeout_sec)
        static_spec = _SubprocessGateSpec(
            argv=_build_tsc_argv(sandbox.repo_root, ts_files),
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="tsc",
            fallback_checks=max(1, len(ts_units)),
        )
        static_att = _run_subprocess_gate(sandbox.repo_root, static_spec, self._timeout_sec)
        return dyn_att, static_att


class CargoSubprocessGateRunner:
    """Subprocess dynamic and static-type gate runner for Rust Cargo workspaces."""

    @jezz_callable
    def __init__(
        self,
        timeout_sec: Annotated[float, Field(gt=0.0, le=60.0, allow_inf_nan=False)] = 60.0,
    ) -> None:
        self._timeout_sec = timeout_sec

    @jezz_callable
    def run_gates(
        self,
        sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
        units: Sequence[InspectedUnit],
    ) -> tuple[GateAttestation, ...]:
        """Run ``cargo test`` and ``cargo check --all-targets`` within timeout watchdogs."""
        rs_units = _polyglot_units_for_lang(units, SupportedLanguage.RUST)
        has_tests = any(
            u.is_test_file
            or is_polyglot_test_path(u.file_path)
            or any(fn.is_test_callable for fn in u.callables)
            for u in rs_units
        )
        env = {"CARGO_TERM_COLOR": "never"}
        dyn_spec = _SubprocessGateSpec(
            argv=("cargo", "test", "--quiet"),
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="cargo-test",
            fallback_checks=max(1, len(rs_units)),
            extra_env=env,
        )
        dyn_att = (
            _run_subprocess_gate(sandbox.repo_root, dyn_spec, self._timeout_sec)
            if has_tests
            else _failed_attestation(
                GateKind.DYNAMIC_SUITE,
                "cargo-test",
                1,
                0.05,
                "Zero Rust #[test] functions or integration test files present in workspace.",
            )
        )
        static_spec = _SubprocessGateSpec(
            argv=("cargo", "check", "--all-targets", "--quiet"),
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="cargo-check",
            fallback_checks=max(1, len(rs_units)),
            extra_env=env,
        )
        static_att = _run_subprocess_gate(sandbox.repo_root, static_spec, self._timeout_sec)
        return dyn_att, static_att


class GoSubprocessGateRunner:
    """Subprocess dynamic and static-type gate runner for Go module workspaces."""

    @jezz_callable
    def __init__(
        self,
        timeout_sec: Annotated[float, Field(gt=0.0, le=60.0, allow_inf_nan=False)] = 45.0,
    ) -> None:
        self._timeout_sec = timeout_sec

    @jezz_callable
    def run_gates(
        self,
        sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
        units: Sequence[InspectedUnit],
    ) -> tuple[GateAttestation, ...]:
        """Run ``go test -v ./...`` and ``go vet ./...`` within timeout watchdogs."""
        go_units = _polyglot_units_for_lang(units, SupportedLanguage.GO)
        go_tests = [u for u in go_units if u.is_test_file or is_polyglot_test_path(u.file_path)]
        dyn_spec = _SubprocessGateSpec(
            argv=("go", "test", "-v", "./..."),
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="go-test",
            fallback_checks=max(1, len(go_tests)),
        )
        dyn_att = (
            _run_subprocess_gate(sandbox.repo_root, dyn_spec, self._timeout_sec)
            if go_tests
            else _failed_attestation(
                GateKind.DYNAMIC_SUITE,
                "go-test",
                1,
                0.05,
                "Zero Go *_test.go files present in workspace.",
            )
        )
        static_spec = _SubprocessGateSpec(
            argv=("go", "vet", "./..."),
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="go-vet",
            fallback_checks=max(1, len(go_units)),
        )
        static_att = _run_subprocess_gate(sandbox.repo_root, static_spec, self._timeout_sec)
        return dyn_att, static_att


def _units_for_language(
    units: Sequence[InspectedUnit],
    language: SupportedLanguage,
) -> tuple[InspectedUnit, ...]:
    if language == SupportedLanguage.PYTHON:
        return tuple(u for u in units if isinstance(u, SourceUnit))
    return tuple(u for u in units if isinstance(u, PolyglotUnit) and u.language == language)


class PolyglotSubprocessGateRunner:
    """Composite subprocess ``DynamicGatePort`` dispatching Python, TS, Rust, and Go gates."""

    @jezz_callable
    def __init__(
        self,
        timeout_sec: Annotated[float, Field(gt=0.0, le=60.0, allow_inf_nan=False)] = 45.0,
        *,
        ratchet_to_scope: bool = False,
    ) -> None:
        self._timeout_sec = timeout_sec
        self._ratchet_to_scope = ratchet_to_scope

    @jezz_callable
    def run_gates(
        self,
        sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
        units: Sequence[InspectedUnit],
    ) -> tuple[GateAttestation, ...]:
        """Execute static-type and dynamic test gates for every active workspace language."""
        languages = detect_workspace_languages(
            sandbox.repo_root,
            units=units,
            authorized_files=sandbox.authorized_files,
        )
        runners: dict[SupportedLanguage, DynamicGatePort] = {
            SupportedLanguage.PYTHON: UvSubprocessGateRunner(
                timeout_sec=self._timeout_sec,
                ratchet_to_scope=self._ratchet_to_scope,
            ),
            SupportedLanguage.TYPESCRIPT: TypeScriptSubprocessGateRunner(
                timeout_sec=self._timeout_sec
            ),
            SupportedLanguage.RUST: CargoSubprocessGateRunner(timeout_sec=self._timeout_sec),
            SupportedLanguage.GO: GoSubprocessGateRunner(timeout_sec=self._timeout_sec),
        }
        attestations: list[GateAttestation] = []
        for lang in languages:
            scoped_units = _units_for_language(units, lang)
            attestations.extend(runners[lang].run_gates(sandbox, scoped_units))
        return tuple(attestations)
