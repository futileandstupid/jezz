"""Argument parser construction for the ``jezz`` CLI subcommands."""

import argparse
from pathlib import Path
from typing import Annotated

from jezz.agents.caching import PromptCacheMode
from jezz.cli_interactive import (
    register_hook_parser,
    register_init_parser,
    register_interactive_workflow_flags,
)
from jezz.codes.complexity import PermitProfile
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.guards import InstanceGuard, jezz_callable

type GuardedArgParser = Annotated[
    argparse.ArgumentParser,
    InstanceGuard(argparse.ArgumentParser),
]


def _register_model_routing_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=None,
        help="Default Pydantic AI model ID (overrides [tool.jezz.models] when passed).",
    )
    parser.add_argument(
        "--architect-model",
        dest="architect_model",
        default=None,
        help="Model override for FRAME, DECOMPOSE, SPECIFY, and HYPOTHESIZE specialists.",
    )
    parser.add_argument(
        "--builder-model",
        dest="builder_model",
        default=None,
        help="Model override for PROVISION, INSTRUMENT, SYNTHESIZE, and REFACTOR specialists.",
    )
    parser.add_argument(
        "--inspector-model",
        dest="inspector_model",
        default=None,
        help="Model override for INSPECT_CODE, INSPECT_ENV, CONSULT, and CURATE_MEMORY.",
    )
    parser.add_argument(
        "--prompt-cache",
        dest="prompt_cache",
        choices=tuple(m.value for m in PromptCacheMode),
        default=None,
        help="Prompt-caching policy across specialist turns (default: auto).",
    )


def _register_secret_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--secret-uri",
        "--api-key-uri",
        "--pass-uri",
        dest="secret_uri",
        default=None,
        help=(
            "Secret URI (env://VAR, pass://vault/item/field, op://vault/item/field, "
            "or keychain://service/account) for model API key."
        ),
    )
    parser.add_argument(
        "--agent-reason",
        dest="agent_reason",
        default=None,
        help="Audit reason propagated to secret providers (e.g. PROTON_PASS_AGENT_REASON).",
    )


def _register_workflow_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    name: str,
    help_text: str,
) -> None:
    p_wf = sub.add_parser(name, help=help_text)
    p_wf.add_argument("repo_path", type=Path, help="Target workspace root directory.")
    _register_model_routing_flags(p_wf)
    src_grp = p_wf.add_mutually_exclusive_group(required=True)
    src_grp.add_argument("--prompt", default=None, help="Raw engineering task prompt.")
    src_grp.add_argument("--mandate", type=Path, default=None, help="EngineeringMandate JSON file.")
    src_grp.add_argument(
        "--resume-elicitation",
        type=Path,
        default=None,
        help="Path to halted ElicitationTicket JSON to resume via ELICIT -> FRAME.",
    )
    p_wf.add_argument(
        "--clarification",
        dest="clarifications",
        action="append",
        default=None,
        help="Authoritative answer resolving open ambiguity (repeatable).",
    )
    p_wf.add_argument(
        "--write-elicitation",
        type=Path,
        default=None,
        help="File path to save ElicitationTicket JSON if halted at BHV-02 ELICIT.",
    )
    p_wf.add_argument("--permit", type=Path, default=None, help="Optional CodePermit JSON file.")
    p_wf.add_argument(
        "--profile",
        choices=tuple(p.value for p in PermitProfile),
        default=None,
        help="Optional JBC threshold profile ('standard' or 'strict_internal').",
    )
    p_wf.add_argument(
        "--ratchet",
        action="store_true",
        default=False,
        help="Enforce Differential Occupancy Ratchet scoped to authorized/modified files.",
    )
    p_wf.add_argument("--gate-runner", choices=("subprocess", "in-process"), default="subprocess")
    p_wf.add_argument(
        "--gate-timeout",
        dest="gate_timeout",
        type=float,
        default=None,
        help="Per-subprocess gate timeout in seconds ((0, 600], overrides [tool.jezz.gates]).",
    )
    p_wf.add_argument("--timeout-sec", type=float, default=600.0)
    p_wf.add_argument("--write-dossier", default=None, help="Relative path to save dossier JSON.")
    register_interactive_workflow_flags(p_wf)
    _register_secret_flags(p_wf)
    p_wf.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )


def _register_benchmark_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p_bmk = sub.add_parser(
        "benchmark",
        help="Run Track 4/7 pydantic-evals A/B benchmark (Prose SDD vs. Jezz PCE).",
    )
    p_bmk.add_argument("work_dir", type=Path, help="Scratch directory for isolated workspaces.")
    _register_model_routing_flags(p_bmk)
    p_bmk.add_argument(
        "--suite",
        dest="suites",
        action="append",
        default=None,
        help="Repeatable matrix suite (<label>=<provider:model>[@<cache>] or arch:...,build:...).",
    )
    p_bmk.add_argument("--max-concurrency", type=int, default=2)
    p_bmk.add_argument(
        "--language",
        dest="benchmark_language",
        choices=(*(lang.value for lang in SupportedLanguage), "all"),
        default=None,
        help="Target benchmark language ('python', 'typescript', 'rust', 'go', or 'all').",
    )
    p_bmk.add_argument("--case-id", dest="case_ids", action="append", default=None)
    p_bmk.add_argument("--gate-runner", choices=("subprocess", "in-process"), default="subprocess")
    p_bmk.add_argument("--timeout-sec", type=float, default=3600.0)
    p_bmk.add_argument("--write-json", default=None, help="Relative path inside work_dir for JSON.")
    _register_secret_flags(p_bmk)
    p_bmk.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )


def _register_inspect_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p_ins = sub.add_parser(
        "inspect",
        help="Run polyglot JBC v1.2 Rough-In inspection (.py, .ts, .tsx, .rs, .go).",
    )
    p_ins.add_argument("repo_path", type=Path, help="Target repository root directory.")
    p_ins.add_argument("--target-dir", dest="target_dirs", action="append", default=None)
    p_ins.add_argument(
        "--language",
        dest="languages",
        action="append",
        choices=tuple(lang.value for lang in SupportedLanguage),
        default=None,
        help="Restrict inspection to specific language(s) (repeatable).",
    )
    p_ins.add_argument("--permit", type=Path, default=None, help="Optional CodePermit JSON file.")
    p_ins.add_argument(
        "--profile",
        choices=tuple(p.value for p in PermitProfile),
        default=None,
        help="Optional JBC threshold profile ('standard' or 'strict_internal').",
    )
    diff_grp = p_ins.add_mutually_exclusive_group()
    diff_grp.add_argument(
        "--changed-since",
        dest="changed_since",
        default=None,
        help="Git ref (e.g. origin/main, HEAD~1) for Differential Occupancy Ratchet.",
    )
    diff_grp.add_argument(
        "--staged",
        dest="staged",
        action="store_true",
        default=False,
        help="Ratchet only Git-staged source files in the index.",
    )
    p_ins.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help="ISO-8601 date (YYYY-MM-DD) for evaluating CodeVariance sunset expiration.",
    )
    p_ins.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )


def _register_mcp_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p_mcp = sub.add_parser(
        "mcp",
        help="Run the read-only JBC v1.2 & PCE statutory MCP server over stdio.",
    )
    p_mcp.add_argument(
        "--root",
        dest="workspace_root",
        type=Path,
        default=None,
        help="Workspace root directory confining MCP reads (defaults to current directory).",
    )


@jezz_callable
def build_cli_parser() -> GuardedArgParser:
    """Construct the top-level ``argparse.ArgumentParser`` for all ``jezz`` subcommands."""
    parser = argparse.ArgumentParser(
        prog="jezz",
        description="Jezz Software Building Code (JBC v1.2) & PCE runtime CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _register_inspect_subparser(sub)

    p_aud = sub.add_parser("audit", help="Audit a 5-phase PCE EngineeringDossier JSON file.")
    p_aud.add_argument("dossier_json", type=Path, help="Path to EngineeringDossier JSON.")
    p_aud.add_argument("--repo", type=Path, default=None, help="Workspace root for AST grounding.")
    p_aud.add_argument("--target-dir", dest="target_dirs", action="append", default=None)
    p_aud.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )

    _register_workflow_subparser(
        sub,
        name="run",
        help_text="Execute a greenfield PCE mandate or prompt via PceWorkflowOrchestrator.",
    )
    _register_workflow_subparser(
        sub,
        name="renovate",
        help_text="Renovate a brownfield workspace via BHV-17 REFACTOR to JBC v1.2 occupancy.",
    )
    _register_benchmark_subparser(sub)
    _register_mcp_subparser(sub)
    register_init_parser(
        sub.add_parser(
            "init",
            help="Install the Antigravity jezz plugin (MCP + Stop hook + PCE skill) and config.",
        )
    )
    register_hook_parser(
        sub.add_parser("hook", help="Execute an Antigravity lifecycle hook handler over stdio.")
    )
    return parser
