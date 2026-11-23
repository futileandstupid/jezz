"""Interactive CLI callbacks, live PCE progress emitters, and ``init``/``hook`` subcommands."""

import argparse
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field

from jezz.agents.nodes import ElicitationCallback, PceInteractionHooks, ProgressCallback
from jezz.behaviors.taxonomy import BEHAVIOR_BASIS, BehaviorKind
from jezz.guards import ContainedPath, InstanceGuard, LoopBudget, jezz_callable
from jezz.hooks import StopHookConfig, run_stop_hook_stdio
from jezz.methodology.mandate import EngineeringMandate
from jezz.plugin import PluginInstallScope, PluginInstallSpec, initialize_jezz_workspace

_MAX_ANSWER_LINE_CHARS: Final[int] = 4096

type CliExitCode = Annotated[int, Field(ge=0, le=2)]
type GuardedStream = Annotated[io.TextIOBase, InstanceGuard(io.TextIOBase)]


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


@jezz_callable
def persist_mandate_json(
    resolved_repo: Path,
    target_path: Path,
    mandate: EngineeringMandate,
) -> Path:
    """Write ``mandate`` JSON inside ``resolved_repo`` (if relative) or to ``target_path``."""
    if not target_path.is_absolute():
        contained = ContainedPath.from_parts(resolved_repo, target_path.as_posix())
    else:
        resolved_parent = target_path.resolve().parent
        resolved_parent.mkdir(parents=True, exist_ok=True)
        contained = ContainedPath.from_parts(resolved_parent, target_path.name)
    contained.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with contained.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(mandate.model_dump_json(indent=2) + "\n")
    return contained.resolved_path


@jezz_callable
def register_interactive_workflow_flags(
    parser: Annotated[argparse.ArgumentParser, InstanceGuard(argparse.ArgumentParser)],
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


type GuardedArgParser = Annotated[
    argparse.ArgumentParser,
    InstanceGuard(argparse.ArgumentParser),
]


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
        help="Run the JBC v1.1 Differential Occupancy Ratchet as an Antigravity Stop gate.",
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
