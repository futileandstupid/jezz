"""Interactive CLI callbacks, live PCE progress emitters, and init/hook/charter subcommands."""

import argparse
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field

from jezz.agents.nodes import ElicitationCallback, PceInteractionHooks, ProgressCallback
from jezz.behaviors.taxonomy import BEHAVIOR_BASIS, BehaviorKind
from jezz.codes.complexity import PermitProfile
from jezz.codes.discovery import discover_workspace_units, read_contained_json
from jezz.codes.inspector import BuildingInspector
from jezz.guards import ContainedPath, InstanceGuard, LoopBudget, jezz_callable
from jezz.hooks import StopHookConfig, run_stop_hook_stdio
from jezz.methodology.charter import (
    AUTHORIZED_URI_SCHEMES,
    AxisResolution,
    CharterIntakeSession,
    CharterSealInput,
    ElicitationAxis,
    HazardMutationPolicy,
    MutationPosture,
    ProjectCharter,
    probe_workspace_reality,
    seal_project_charter,
    start_charter_intake,
)
from jezz.methodology.mandate import EngineeringMandate
from jezz.plugin import PluginInstallScope, PluginInstallSpec, initialize_jezz_workspace

_MAX_ANSWER_LINE_CHARS: Final[int] = 4096

type CliExitCode = Annotated[int, Field(ge=0, le=2)]
type GuardedStream = Annotated[io.TextIOBase, InstanceGuard(io.TextIOBase)]
type GuardedArgParser = Annotated[
    argparse.ArgumentParser,
    InstanceGuard(argparse.ArgumentParser),
]


@dataclass(frozen=True)
class CliIoStreams:
    """Standard input, output, and diagnostic text streams threaded into CLI commands."""

    stdin: io.TextIOBase
    stdout: io.TextIOBase
    stderr: io.TextIOBase


def _write_stream_line(stream: io.TextIOBase, text: str) -> None:
    payload = text if text.endswith("\n") else f"{text}\n"
    stream.write(payload)
    stream.flush()


def _stream_is_tty(stream: io.TextIOBase) -> bool:
    try:
        return bool(stream.isatty())
    except OSError, ValueError:
        return False


@jezz_callable
def build_stderr_progress_callback(
    err_stream: GuardedStream,
    *,
    enabled: bool,
) -> ProgressCallback | None:
    """Construct a live stderr step reporter for ``PceWorkflowOrchestrator`` transitions."""
    if not enabled:
        return None

    def _report(kind: BehaviorKind, step_id: str, summary: str) -> None:
        spec = BEHAVIOR_BASIS[kind]
        _write_stream_line(
            err_stream,
            f"[PCE] {spec.code} {kind.upper()} ({step_id}): {summary}",
        )

    return _report


def _prompt_single_ambiguity(
    streams: CliIoStreams,
    index: int,
    total: int,
    question: str,
) -> str | None:
    _write_stream_line(streams.stderr, f"  [{index}/{total}] {question}")
    streams.stderr.write("  > ")
    streams.stderr.flush()
    raw = streams.stdin.readline(_MAX_ANSWER_LINE_CHARS)
    if not raw:
        return None
    cleaned = raw.strip()
    return cleaned if cleaned else None


@jezz_callable
def build_interactive_elicitation_callback(
    streams: Annotated[CliIoStreams, InstanceGuard(CliIoStreams)],
    *,
    enabled: bool,
) -> ElicitationCallback | None:
    """Prompt the terminal user on ``stderr`` and read ``BHV-02 ELICIT`` answers from ``stdin``."""
    if not enabled:
        return None

    def _elicit(mandate: EngineeringMandate) -> Sequence[str] | None:
        total = len(mandate.open_ambiguities)
        if total == 0:
            return None
        _write_stream_line(
            streams.stderr,
            f"[BHV-02 ELICIT] Mandate {mandate.mandate_id} has {total} open ambiguity(ies):",
        )
        answers: list[str] = []
        budget = LoopBudget(max_steps=64)
        for idx, question in enumerate(mandate.open_ambiguities, start=1):
            budget.tick()
            ans = _prompt_single_ambiguity(streams, idx, total, question)
            if ans is None:
                return None
            answers.append(ans)
        return tuple(answers)

    return _elicit


@jezz_callable
def resolve_workflow_interaction_hooks(
    ns: Annotated[argparse.Namespace, InstanceGuard(argparse.Namespace)],
    streams: Annotated[CliIoStreams, InstanceGuard(CliIoStreams)],
    pre_supplied_callback: ElicitationCallback | None,
) -> Annotated[PceInteractionHooks, InstanceGuard(PceInteractionHooks)]:
    """Assemble elicitation, stderr progress, and ``--plan-only`` hooks for a workflow run."""
    interactive_flag = bool(ns.interactive) and not bool(ns.no_interactive)
    active_elicit = (
        pre_supplied_callback
        if pre_supplied_callback is not None
        else build_interactive_elicitation_callback(
            streams,
            enabled=(interactive_flag and ns.resume_elicitation is None),
        )
    )
    show_progress = bool(ns.progress) or (not bool(ns.quiet) and _stream_is_tty(streams.stderr))
    progress_cb = build_stderr_progress_callback(streams.stderr, enabled=show_progress)
    return PceInteractionHooks(
        on_elicit=active_elicit,
        on_progress=progress_cb,
        plan_only=bool(ns.plan_only),
    )


def _write_contained_json_text(resolved_repo: Path, target_path: Path, json_text: str) -> Path:
    if not target_path.is_absolute():
        contained = ContainedPath.from_parts(resolved_repo, target_path.as_posix())
    else:
        resolved_parent = target_path.resolve().parent
        resolved_parent.mkdir(parents=True, exist_ok=True)
        contained = ContainedPath.from_parts(resolved_parent, target_path.name)
    contained.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with contained.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(json_text if json_text.endswith("\n") else f"{json_text}\n")
    return contained.resolved_path


@jezz_callable
def persist_mandate_json(
    resolved_repo: Path,
    target_path: Path,
    mandate: EngineeringMandate,
) -> Path:
    """Write ``mandate`` JSON inside ``resolved_repo`` (if relative) or to ``target_path``."""
    return _write_contained_json_text(resolved_repo, target_path, mandate.model_dump_json(indent=2))


@jezz_callable
def register_interactive_workflow_flags(
    parser: GuardedArgParser,
) -> None:
    """Register ``--plan-only``, ``--write-mandate``, TTY elicitation, and progress flags."""
    parser.add_argument(
        "--plan-only",
        dest="plan_only",
        action="store_true",
        default=False,
        help="Halt cleanly after FRAME -> BIND_CODES -> INSPECT_CODE -> DECOMPOSE for review.",
    )
    parser.add_argument(
        "--write-mandate",
        dest="write_mandate",
        type=Path,
        default=None,
        help="File path to persist the framed EngineeringMandate JSON.",
    )
    tty_grp = parser.add_mutually_exclusive_group()
    tty_grp.add_argument(
        "--interactive",
        dest="interactive",
        action="store_true",
        default=False,
        help="Prompt interactively on stdin/stderr if BHV-02 ELICIT encounters ambiguities.",
    )
    tty_grp.add_argument(
        "--no-interactive",
        dest="no_interactive",
        action="store_true",
        default=False,
        help="Disable interactive TTY elicitation even when attached to a terminal.",
    )
    prog_grp = parser.add_mutually_exclusive_group()
    prog_grp.add_argument(
        "--progress",
        dest="progress",
        action="store_true",
        default=False,
        help="Emit live BHV step transitions to stderr.",
    )
    prog_grp.add_argument(
        "--quiet",
        dest="quiet",
        action="store_true",
        default=False,
        help="Suppress live stderr progress output.",
    )


@jezz_callable
def register_init_parser(p_init: GuardedArgParser) -> None:
    """Configure CLI flags on the ``jezz init`` subparser."""
    p_init.add_argument(
        "repo_path",
        nargs="?",
        type=Path,
        default=None,
        help="Target workspace root directory (defaults to current directory).",
    )
    p_init.add_argument(
        "--plugin-scope",
        dest="plugin_scope",
        choices=("workspace", "global", "none"),
        default="workspace",
        help="Where to install the Antigravity plugin bundle (default: workspace).",
    )
    p_init.add_argument(
        "--global-config-dir",
        dest="global_config_dir",
        type=Path,
        default=None,
        help="Override ~/.gemini/config root when --plugin-scope=global.",
    )
    p_init.add_argument(
        "--default-model",
        dest="default_model",
        default="google:gemini-3.8-flash",
        help="Default Pydantic AI model written to [tool.jezz.models] if absent.",
    )
    p_init.add_argument(
        "--pre-commit",
        dest="pre_commit",
        action="store_true",
        default=False,
        help="Install .git/hooks/pre-commit running `jezz inspect --staged`.",
    )
    p_init.add_argument(
        "--no-pyproject",
        dest="no_pyproject",
        action="store_true",
        default=False,
        help="Do not append [tool.jezz.models] to pyproject.toml.",
    )
    p_init.add_argument(
        "--overwrite",
        dest="overwrite",
        action="store_true",
        default=False,
        help="Overwrite existing plugin or pre-commit files.",
    )
    p_init.add_argument(
        "--format",
        dest="output_format",
        choices=("markdown", "json"),
        default="markdown",
    )


@jezz_callable
def register_hook_parser(p_hook: GuardedArgParser) -> None:
    """Configure CLI flags on the ``jezz hook`` subparser."""
    hook_sub = p_hook.add_subparsers(dest="hook_event", required=True)
    p_stop = hook_sub.add_parser(
        "stop",
        help="Run the JBC v1.3 Differential Occupancy Ratchet as an Antigravity Stop gate.",
    )
    p_stop.add_argument(
        "--root",
        dest="workspace_root",
        type=Path,
        default=None,
        help="Explicit workspace root overriding hook stdin workspacePaths.",
    )
    p_stop.add_argument(
        "--max-continuations",
        dest="max_continuations",
        type=int,
        default=3,
        help="Maximum forced Stop continuations per user turn (1..10).",
    )
    p_stop.add_argument(
        "--require-marker",
        dest="require_marker",
        action="store_true",
        default=False,
        help="Only enforce on workspaces with a local jezz plugin or [tool.jezz] config.",
    )
    p_stop.add_argument("--permit", type=Path, default=None, help="Optional CodePermit JSON file.")
    p_stop.add_argument("--as-of", dest="as_of", default=None, help="ISO-8601 date (YYYY-MM-DD).")


def _add_charter_intake_flags(p_chr: argparse.ArgumentParser) -> None:
    p_chr.add_argument(
        "repo_path",
        nargs="?",
        type=Path,
        default=None,
        help="Target workspace root directory (defaults to current directory).",
    )
    p_chr.add_argument("--title", required=True, help="ProjectCharter title.")
    p_chr.add_argument("--vision", required=True, help="Project vision prose (>= 12 chars).")
    p_chr.add_argument("--charter-id", dest="charter_id", default=None, help="Optional CHR-* ID.")
    p_chr.add_argument(
        "--probe-bin",
        dest="probe_bins",
        action="append",
        default=None,
        help="Candidate CLI executable name to probe (repeatable).",
    )
    p_chr.add_argument(
        "--probe-pkg",
        dest="probe_pkgs",
        action="append",
        default=None,
        help="Candidate package name to probe (repeatable).",
    )
    p_chr.add_argument(
        "--intake-only",
        dest="intake_only",
        action="store_true",
        default=False,
        help="Emit the reality-probed 3-axis CharterIntakeSession without sealing.",
    )


def _add_charter_policy_flags(p_chr: argparse.ArgumentParser) -> None:
    p_chr.add_argument("--ontology", default=None, help="Resolution for domain_ontology axis.")
    p_chr.add_argument(
        "--hazard-summary",
        dest="hazard_summary",
        default=None,
        help="Resolution summary for hazard_mutation_policy axis.",
    )
    p_chr.add_argument(
        "--scope-summary",
        dest="scope_summary",
        default=None,
        help="Resolution summary for scope_and_exclusions axis.",
    )
    p_chr.add_argument(
        "--exclusion",
        dest="exclusions",
        action="append",
        default=None,
        help="Explicit out-of-scope exclusion item (repeatable).",
    )
    p_chr.add_argument(
        "--mutation-posture",
        dest="mutation_posture",
        choices=tuple(m.value for m in MutationPosture),
        default=MutationPosture.LOCAL_WORKSPACE_ONLY.value,
    )
    p_chr.add_argument(
        "--allow-scheme",
        dest="allow_schemes",
        choices=tuple(sorted(AUTHORIZED_URI_SCHEMES)),
        action="append",
        default=None,
    )
    p_chr.add_argument(
        "--rollback-journal",
        dest="rollback_journal",
        action="store_true",
        default=False,
    )
    p_chr.add_argument("--timeout-bound", dest="timeout_bound", type=int, default=None)


def _add_charter_output_flags(p_chr: argparse.ArgumentParser) -> None:
    p_chr.add_argument("--package-slug", dest="package_slug", default=None)
    p_chr.add_argument("--mandate", dest="mandate_paths", type=Path, action="append", default=None)
    p_chr.add_argument(
        "--profile",
        choices=tuple(p.value for p in PermitProfile),
        default=PermitProfile.STANDARD.value,
    )
    p_chr.add_argument("--interactive", action="store_true", default=False)
    p_chr.add_argument("--write-intake", dest="write_intake", type=Path, default=None)
    p_chr.add_argument("--write-charter", dest="write_charter", type=Path, default=None)
    p_chr.add_argument("--write-mandates-dir", dest="write_mandates_dir", type=Path, default=None)
    p_chr.add_argument(
        "--format",
        dest="output_format",
        choices=("markdown", "json"),
        default="markdown",
    )


@jezz_callable
def register_charter_parser(p_chr: GuardedArgParser) -> None:
    """Configure CLI flags on the ``jezz charter`` subparser."""
    _add_charter_intake_flags(p_chr)
    _add_charter_policy_flags(p_chr)
    _add_charter_output_flags(p_chr)


@jezz_callable
def handle_init_command(
    ns: Annotated[argparse.Namespace, InstanceGuard(argparse.Namespace)],
    streams: Annotated[CliIoStreams, InstanceGuard(CliIoStreams)],
) -> CliExitCode:
    """Execute ``jezz init`` and render the ``WorkspaceInitReport``."""
    target_repo = ns.repo_path if isinstance(ns.repo_path, Path) else Path.cwd()
    spec = PluginInstallSpec(
        repo_root=target_repo,
        scope=PluginInstallScope(str(ns.plugin_scope)),
        global_config_dir=ns.global_config_dir,
        default_model=str(ns.default_model),
        overwrite=bool(ns.overwrite),
        install_pre_commit=bool(ns.pre_commit),
        bootstrap_pyproject=not bool(ns.no_pyproject),
    )
    report = initialize_jezz_workspace(spec)
    rendered = (
        report.model_dump_json(indent=2)
        if str(ns.output_format) == "json"
        else report.to_markdown()
    )
    _write_stream_line(streams.stdout, rendered)
    return 0


@jezz_callable
def handle_hook_command(
    ns: Annotated[argparse.Namespace, InstanceGuard(argparse.Namespace)],
    streams: Annotated[CliIoStreams, InstanceGuard(CliIoStreams)],
) -> CliExitCode:
    """Execute ``jezz hook stop`` reading stdin JSON and emitting stdout decision JSON."""
    cfg = StopHookConfig(
        workspace_override=ns.workspace_root,
        max_continuations=int(ns.max_continuations),
        require_jezz_marker=bool(ns.require_marker),
        permit_path=ns.permit,
        as_of=str(ns.as_of) if ns.as_of is not None else None,
    )
    run_stop_hook_stdio(streams.stdin, streams.stdout, config=cfg)
    return 0


def _collect_charter_resolutions(
    ns: argparse.Namespace,
    intake: CharterIntakeSession,
    streams: CliIoStreams,
) -> tuple[AxisResolution, ...]:
    raw_by_axis: dict[ElicitationAxis, str | None] = {
        ElicitationAxis.DOMAIN_ONTOLOGY: ns.ontology,
        ElicitationAxis.HAZARD_MUTATION_POLICY: ns.hazard_summary,
        ElicitationAxis.SCOPE_AND_EXCLUSIONS: ns.scope_summary,
    }
    resolutions: list[AxisResolution] = []
    total = len(intake.questions)
    for idx, q in enumerate(intake.questions, start=1):
        answer = raw_by_axis.get(q.axis)
        if answer is None and bool(ns.interactive):
            answer = _prompt_single_ambiguity(
                streams,
                idx,
                total,
                f"[{q.axis}] {q.prompt}",
            )
        if answer is not None:
            resolutions.append(
                AxisResolution(
                    axis=q.axis,
                    summary=answer,
                    decided_constraints=(answer,),
                )
            )
    return tuple(resolutions)


def _load_cli_mandates(
    resolved_repo: Path,
    mandate_paths: Sequence[Path] | None,
) -> tuple[EngineeringMandate, ...]:
    if not mandate_paths:
        return ()
    loaded: list[EngineeringMandate] = []
    for item in mandate_paths:
        target = (
            ContainedPath.from_parts(resolved_repo, item.as_posix())
            if not item.is_absolute()
            else ContainedPath.from_parts(item.resolve().parent, item.name)
        )
        loaded.append(EngineeringMandate.model_validate_json(read_contained_json(target)))
    return tuple(loaded)


def _persist_charter_outputs(
    resolved_repo: Path,
    ns: argparse.Namespace,
    charter: ProjectCharter,
) -> None:
    if isinstance(ns.write_charter, Path):
        _write_contained_json_text(
            resolved_repo,
            ns.write_charter,
            charter.model_dump_json(indent=2),
        )
    if isinstance(ns.write_mandates_dir, Path):
        out_dir = ns.write_mandates_dir
        for idx, mandate in enumerate(charter.mandate_queue, start=1):
            filename = f"{idx:02d}_{mandate.mandate_id}.json"
            rel_or_abs = out_dir / filename
            persist_mandate_json(resolved_repo, rel_or_abs, mandate)


def _seal_cli_charter(
    resolved_repo: Path,
    ns: argparse.Namespace,
    intake: CharterIntakeSession,
    streams: CliIoStreams,
) -> ProjectCharter:
    resolutions = _collect_charter_resolutions(ns, intake, streams)
    policy = HazardMutationPolicy(
        mutation_posture=MutationPosture(str(ns.mutation_posture)),
        allowed_uri_schemes=tuple(str(s) for s in (ns.allow_schemes or ())),
        requires_rollback_journal=bool(ns.rollback_journal),
        rate_or_timeout_bound_sec=ns.timeout_bound,
    )
    exclusions = tuple(str(e) for e in (ns.exclusions or ()))
    if not exclusions:
        msg = "Sealing a ProjectCharter requires at least one --exclusion item."
        raise ValueError(msg)
    seal_req = CharterSealInput(
        intake=intake,
        resolutions=resolutions,
        hazard_policy=policy,
        scope_exclusions=exclusions,
        mandates=_load_cli_mandates(resolved_repo, ns.mandate_paths),
        package_slug=str(ns.package_slug) if ns.package_slug is not None else None,
    )
    units = discover_workspace_units(resolved_repo)
    inspector = BuildingInspector.from_workspace(resolved_repo)
    return seal_project_charter(
        seal_req,
        units,
        manifest=inspector.manifest,
        polyglot_manifest=inspector.polyglot_manifest,
        profile=PermitProfile(str(ns.profile)),
    )


@jezz_callable
def handle_charter_command(
    ns: Annotated[argparse.Namespace, InstanceGuard(argparse.Namespace)],
    streams: Annotated[CliIoStreams, InstanceGuard(CliIoStreams)],
) -> CliExitCode:
    """Execute ``jezz charter`` Phase 0/I reality probe, 3-axis intake, and charter sealing."""
    raw_repo = ns.repo_path if isinstance(ns.repo_path, Path) else Path.cwd()
    resolved_repo = raw_repo.resolve()
    if not resolved_repo.is_dir():
        msg = f"Workspace directory does not exist: '{raw_repo}'."
        raise FileNotFoundError(msg)
    probe = probe_workspace_reality(
        resolved_repo,
        candidate_binaries=tuple(str(b) for b in (ns.probe_bins or ())),
        candidate_packages=tuple(str(p) for p in (ns.probe_pkgs or ())),
    )
    units = discover_workspace_units(resolved_repo)
    intake = start_charter_intake(
        str(ns.title),
        str(ns.vision),
        probe,
        charter_id=str(ns.charter_id) if ns.charter_id is not None else None,
        units=units,
    )
    if isinstance(ns.write_intake, Path):
        _write_contained_json_text(resolved_repo, ns.write_intake, intake.model_dump_json(indent=2))
    if bool(ns.intake_only):
        out_text = (
            intake.model_dump_json(indent=2)
            if str(ns.output_format) == "json"
            else intake.to_markdown()
        )
        _write_stream_line(streams.stdout, out_text)
        return 0
    charter = _seal_cli_charter(resolved_repo, ns, intake, streams)
    _persist_charter_outputs(resolved_repo, ns, charter)
    rendered = (
        charter.model_dump_json(indent=2)
        if str(ns.output_format) == "json"
        else charter.to_markdown()
    )
    _write_stream_line(streams.stdout, rendered)
    return 0
