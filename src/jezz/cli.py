"""Zero-variance CLI harness exposing JBC inspection, PCE dossier audit, and workflow execution."""

import argparse
import asyncio
import contextlib
import io
import sys
from collections.abc import Callable, Coroutine, Mapping, Sequence
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import Field, ValidationError
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import infer_model

from jezz.agents.orchestrator import PceWorkflowOrchestrator
from jezz.agents.schemas import PceRunStatus, PceWorkflowResult
from jezz.agents.specialists import GuardedModel, uniform_model_suite
from jezz.agents.workspace import DynamicGatePort, InProcessGateRunner, UvSubprocessGateRunner
from jezz.codes.ast_utils import SourceUnit, parse_source_unit
from jezz.codes.inspector import BuildingInspector, ManifestDependencies
from jezz.codes.permits import CodePermit
from jezz.guards import (
    BoundedText,
    BudgetExceededError,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    jezz_callable,
    read_bounded_text,
)
from jezz.methodology.dossier import EngineeringDossier, audit_pce_dossier
from jezz.methodology.mandate import EngineeringMandate

_PRUNED_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "__pycache__",
        "venv",
        "env",
        "build",
        "dist",
        "node_modules",
        "site-packages",
    }
)

type CliExitCode = Annotated[int, Field(ge=0, le=2)]
type GuardedTextStream = Annotated[io.TextIOBase, InstanceGuard(io.TextIOBase)]
type _CommandHandler = Callable[[argparse.Namespace, io.TextIOBase], CliExitCode]


class OutputFormat(StrEnum):
    """Structured output format supported by ``jezz`` CLI subcommands."""

    MARKDOWN = "markdown"
    JSON = "json"


class GateRunnerChoice(StrEnum):
    """Dynamic verification gate runner selectable during ``jezz run``."""

    SUBPROCESS = "subprocess"
    IN_PROCESS = "in-process"


def _is_pruned_relative_path(rel_parts: Sequence[str]) -> bool:
    for part in rel_parts[:-1]:
        if part == ".":
            continue
        if part.startswith(".") or part.endswith(".egg-info") or part in _PRUNED_DIR_NAMES:
            return True
    return False


def _resolve_scan_dirs(resolved_root: Path, target_dirs: Sequence[str]) -> tuple[str, ...]:
    if target_dirs:
        return tuple(target_dirs)
    src_candidate = ContainedPath.from_parts(resolved_root, "src")
    if src_candidate.resolved_path.is_dir():
        return "src", "tests", "test"
    return (".",)


def _collect_python_files(resolved_root: Path, scan_dirs: Sequence[str]) -> list[Path]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for rel_dir in scan_dirs:
        contained_dir = ContainedPath.from_parts(resolved_root, rel_dir)
        if not contained_dir.resolved_path.exists():
            continue
        candidates = (
            [contained_dir.resolved_path]
            if contained_dir.resolved_path.is_file()
            else sorted(contained_dir.resolved_path.rglob("*.py"))
        )
        for py_path in candidates:
            rel_parts = py_path.relative_to(resolved_root).parts
            if py_path not in seen and not _is_pruned_relative_path(rel_parts):
                seen.add(py_path)
                ordered.append(py_path)
    return ordered


@jezz_callable
def discover_workspace_units(
    repo_root: Path,
    *,
    target_dirs: Annotated[Sequence[IdentifierStr], Field(max_length=64)] = (),
) -> tuple[SourceUnit, ...]:
    """Discover and parse Python ``SourceUnit`` files under ``repo_root`` safely."""
    resolved_root = repo_root.resolve()
    if not resolved_root.is_dir():
        msg = f"Workspace directory does not exist: '{repo_root}'."
        raise FileNotFoundError(msg)
    scan_dirs = _resolve_scan_dirs(resolved_root, target_dirs)
    units: list[SourceUnit] = []
    for py_path in _collect_python_files(resolved_root, scan_dirs):
        rel_posix = py_path.relative_to(resolved_root).as_posix()
        contained = ContainedPath.from_parts(resolved_root, rel_posix)
        source = read_bounded_text(contained)
        units.append(parse_source_unit(contained.relative_posix, source))
    return tuple(units)


@jezz_callable
def format_workflow_markdown(result: PceWorkflowResult) -> BoundedText:
    """Render a structured Markdown summary of a ``PceWorkflowResult``."""
    lines = [
        f"# PCE Workflow Execution — `{result.mandate.mandate_id}`",
        f"- **Status:** `{result.status.upper()}`",
        f"- **Behavior Graph Valid:** `{result.graph_verification.is_valid}` "
        f"(`{len(result.induced_graph.nodes)}` nodes)",
        f"- **Agent Invocations:** `{len(result.telemetry)}`",
    ]
    if result.certificate is not None:
        lines.append(f"- **Occupancy Seal:** `{result.certificate.inspector_seal}`")
    if result.mandate.open_ambiguities:
        lines.extend(["", "## Open Ambiguities (Elicitation Required)"])
        for item in result.mandate.open_ambiguities:
            lines.append(f"- {item}")
    if result.falsified_paths:
        lines.extend(["", "## Falsified Paths Recorded"])
        for entry in result.falsified_paths:
            lines.append(f"- {entry}")
    return "\n".join(lines)


def _read_json_file(file_path: Path) -> str:
    resolved = file_path.resolve()
    if not resolved.is_file():
        msg = f"File does not exist: '{file_path}'."
        raise FileNotFoundError(msg)
    contained = ContainedPath.from_parts(resolved.parent, resolved.name)
    return read_bounded_text(contained)


def _write_line(stream: io.TextIOBase, text: str) -> None:
    payload = text if text.endswith("\n") else f"{text}\n"
    stream.write(payload)
    stream.flush()


def _load_optional_permit(permit_path: Path | None) -> CodePermit | None:
    if permit_path is None:
        return None
    return CodePermit.model_validate_json(_read_json_file(permit_path))


def _require_units(
    repo_path: Path,
    raw_dirs: Sequence[str] | None,
) -> tuple[Path, tuple[SourceUnit, ...]]:
    resolved_repo = repo_path.resolve()
    units = discover_workspace_units(resolved_repo, target_dirs=tuple(raw_dirs or ()))
    if not units:
        msg = f"Zero Python source files discovered under '{repo_path}'."
        raise ValueError(msg)
    return resolved_repo, units


def _extract_local_unit_roots(units: Sequence[SourceUnit]) -> set[str]:
    roots: set[str] = set()
    for unit in units:
        if not unit.module_name or unit.is_test_file:
            continue
        top = unit.module_name.split(".", maxsplit=1)[0]
        if top.isidentifier():
            roots.add(top)
            continue
        stem = PurePosixPath(unit.file_path).stem
        if stem.isidentifier():
            roots.add(stem)
    return roots


def _build_workspace_inspector(
    resolved_repo: Path,
    units: Sequence[SourceUnit],
) -> BuildingInspector:
    base = BuildingInspector.from_workspace(resolved_repo)
    local_roots = _extract_local_unit_roots(units)
    manifest = ManifestDependencies(
        runtime_roots=base.manifest.runtime_roots,
        dev_roots=base.manifest.dev_roots,
        workspace_roots=frozenset(base.manifest.workspace_roots | local_roots),
    )
    return BuildingInspector(manifest=manifest)


def _handle_inspect(ns: argparse.Namespace, out_stream: io.TextIOBase) -> CliExitCode:
    resolved_repo, units = _require_units(ns.repo_path, ns.target_dirs)
    permit = _load_optional_permit(ns.permit)
    inspector = _build_workspace_inspector(resolved_repo, units)
    report = inspector.inspect_rough_in(units, permit=permit)
    rendered = (
        report.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else report.to_markdown()
    )
    _write_line(out_stream, rendered)
    return 0 if report.is_occupiable else 1


def _handle_audit(ns: argparse.Namespace, out_stream: io.TextIOBase) -> CliExitCode:
    dossier = EngineeringDossier.model_validate_json(_read_json_file(ns.dossier_json))
    repo_dir: Path = ns.repo if ns.repo is not None else ns.dossier_json.resolve().parent
    _, units = _require_units(repo_dir, ns.target_dirs)
    audit_report = audit_pce_dossier(dossier, units)
    rendered = (
        audit_report.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else audit_report.to_markdown()
    )
    _write_line(out_stream, rendered)
    return 0 if audit_report.is_occupancy_sealed else 1


async def _await_with_deadline(
    coro: Coroutine[object, object, PceWorkflowResult],
    timeout_sec: float,
) -> PceWorkflowResult:
    async with asyncio.timeout(timeout_sec):
        return await coro


@jezz_callable
def resolve_cli_model(model_name: IdentifierStr) -> GuardedModel:
    """Resolve a Pydantic AI ``provider:model`` string into a validated ``Model``."""
    return infer_model(model_name)


def _execute_pce_run(
    ns: argparse.Namespace,
    resolved_repo: Path,
    permit: CodePermit | None,
) -> PceWorkflowResult:
    timeout_sec = float(ns.timeout_sec)
    if timeout_sec <= 0.0 or timeout_sec > 3600.0:
        msg = f"Invalid --timeout-sec={timeout_sec}; expected (0, 3600]."
        raise ValueError(msg)
    model = resolve_cli_model(str(ns.model))
    runner: DynamicGatePort = (
        UvSubprocessGateRunner()
        if ns.gate_runner == GateRunnerChoice.SUBPROCESS
        else InProcessGateRunner()
    )
    orchestrator = PceWorkflowOrchestrator(uniform_model_suite(model), gate_runner=runner)
    if ns.mandate is not None:
        mandate = EngineeringMandate.model_validate_json(_read_json_file(ns.mandate))
        coro = orchestrator.execute_mandate(resolved_repo, mandate, permit=permit)
        return asyncio.run(_await_with_deadline(coro, timeout_sec))
    coro = orchestrator.execute_prompt(resolved_repo, str(ns.prompt), permit=permit)
    return asyncio.run(_await_with_deadline(coro, timeout_sec))


def _persist_run_dossier(
    resolved_repo: Path,
    rel_target: str,
    result: PceWorkflowResult,
) -> None:
    if result.inspection_report is None:
        msg = "Cannot persist EngineeringDossier: workflow halted prior to inspection."
        raise ValueError(msg)
    target = ContainedPath.from_parts(resolved_repo, rel_target)
    present_mutated = frozenset(
        rel_file
        for rel_file in result.mandate.authorized_files()
        if ContainedPath.from_parts(resolved_repo, rel_file).resolved_path.is_file()
    )
    dossier = EngineeringDossier(
        mandate=result.mandate,
        permit=result.permit,
        behavior_graph=result.induced_graph,
        mutated_files=present_mutated,
        inspection_report=result.inspection_report,
        certificate=result.certificate,
    )
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with target.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(dossier.model_dump_json(indent=2) + "\n")


def _handle_run(ns: argparse.Namespace, out_stream: io.TextIOBase) -> CliExitCode:
    resolved_repo = ns.repo_path.resolve()
    if not resolved_repo.is_dir():
        msg = f"Workspace directory does not exist: '{ns.repo_path}'."
        raise FileNotFoundError(msg)
    permit = _load_optional_permit(ns.permit)
    result = _execute_pce_run(ns, resolved_repo, permit)
    if ns.write_dossier is not None:
        _persist_run_dossier(resolved_repo, str(ns.write_dossier), result)
    rendered = (
        result.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else format_workflow_markdown(result)
    )
    _write_line(out_stream, rendered)
    return 0 if result.status == PceRunStatus.OCCUPANCY_CERTIFIED else 1


_COMMAND_HANDLERS: Final[Mapping[str, _CommandHandler]] = MappingProxyType(
    {
        "inspect": _handle_inspect,
        "audit": _handle_audit,
        "run": _handle_run,
    }
)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jezz",
        description="Jezz Software Building Code (JBC v1.1) & PCE runtime CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ins = sub.add_parser("inspect", help="Run JBC v1.1 Rough-In inspection on a repository.")
    p_ins.add_argument("repo_path", type=Path, help="Target repository root directory.")
    p_ins.add_argument("--target-dir", dest="target_dirs", action="append", default=None)
    p_ins.add_argument("--permit", type=Path, default=None, help="Optional CodePermit JSON file.")
    p_ins.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )

    p_aud = sub.add_parser("audit", help="Audit a 5-phase PCE EngineeringDossier JSON file.")
    p_aud.add_argument("dossier_json", type=Path, help="Path to EngineeringDossier JSON.")
    p_aud.add_argument("--repo", type=Path, default=None, help="Workspace root for AST grounding.")
    p_aud.add_argument("--target-dir", dest="target_dirs", action="append", default=None)
    p_aud.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )

    p_run = sub.add_parser(
        "run", help="Execute a PCE mandate or prompt via PceWorkflowOrchestrator."
    )
    p_run.add_argument("repo_path", type=Path, help="Target workspace root directory.")
    p_run.add_argument("--model", required=True, help="Pydantic AI model ID (e.g. provider:model).")
    src_grp = p_run.add_mutually_exclusive_group(required=True)
    src_grp.add_argument("--prompt", default=None, help="Raw engineering task prompt.")
    src_grp.add_argument("--mandate", type=Path, default=None, help="EngineeringMandate JSON file.")
    p_run.add_argument("--permit", type=Path, default=None, help="Optional CodePermit JSON file.")
    p_run.add_argument("--gate-runner", choices=("subprocess", "in-process"), default="subprocess")
    p_run.add_argument("--timeout-sec", type=float, default=180.0)
    p_run.add_argument("--write-dossier", default=None, help="Relative path to save dossier JSON.")
    p_run.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )
    return parser


def _parse_cli_args(
    raw_args: Sequence[str],
    out_stream: io.TextIOBase,
    err_stream: io.TextIOBase,
) -> tuple[argparse.Namespace | None, CliExitCode]:
    parser = _build_cli_parser()
    with contextlib.redirect_stdout(out_stream), contextlib.redirect_stderr(err_stream):
        try:
            return parser.parse_args(list(raw_args)), 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) and 0 <= exc.code <= 2 else 2
            return None, code


@jezz_callable
def main(
    argv: Annotated[Sequence[BoundedText], Field(max_length=256)] | None = None,
    *,
    stdout: GuardedTextStream | None = None,
    stderr: GuardedTextStream | None = None,
) -> CliExitCode:
    """Execute the ``jezz`` CLI and return POSIX exit code ``0``, ``1``, or ``2``."""
    raw_args = tuple(sys.argv[1:]) if argv is None else tuple(argv)
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    if not isinstance(out_stream, io.TextIOBase) or not isinstance(err_stream, io.TextIOBase):
        msg = "Active standard streams must inherit from io.TextIOBase."
        raise TypeError(msg)

    namespace, parse_exit = _parse_cli_args(raw_args, out_stream, err_stream)
    if namespace is None:
        return parse_exit

    handler = _COMMAND_HANDLERS.get(str(namespace.command))
    if handler is None:
        _write_line(err_stream, f"jezz error: unknown subcommand '{namespace.command}'.")
        return 2

    try:
        return handler(namespace, out_stream)
    except (
        FileNotFoundError,
        SyntaxError,
        UnicodeDecodeError,
        ValidationError,
        ValueError,
        UserError,
        BudgetExceededError,
        TimeoutError,
        OSError,
    ) as exc:
        _write_line(err_stream, f"jezz error: {exc}")
        return 2
