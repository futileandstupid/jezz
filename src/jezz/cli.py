"""Zero-variance CLI harness exposing JBC inspection, PCE dossier audit, and workflow execution."""

import argparse
import asyncio
import contextlib
import io
import sys
from collections.abc import Callable, Coroutine, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import Field, ValidationError
from pydantic_ai.exceptions import AgentRunError, ModelAPIError, UserError

from jezz.agents.caching import PromptCacheMode
from jezz.agents.nodes import ElicitationCallback
from jezz.agents.orchestrator import PceWorkflowOrchestrator
from jezz.agents.providers import TlsResilientModel
from jezz.agents.provision import (
    InProcessProvisionRunner,
    ProvisionPort,
    UvSubprocessProvisionRunner,
)
from jezz.agents.routing import (
    CliModelOverrides,
    ResolvedModelRouting,
    discover_models_config,
    resolve_specialist_routing,
)
from jezz.agents.schemas import (
    ElicitationTicket,
    PceRunStatus,
    PceRuntimeBudget,
    PceWorkflowMode,
    PceWorkflowResult,
)
from jezz.agents.specialists import GuardedModel
from jezz.agents.workspace import DynamicGatePort, InProcessGateRunner, UvSubprocessGateRunner
from jezz.cli_interactive import (
    CliIoStreams,
    handle_hook_command,
    handle_init_command,
    persist_mandate_json,
    register_hook_parser,
    register_init_parser,
    register_interactive_workflow_flags,
    resolve_workflow_interaction_hooks,
)
from jezz.codes.discovery import (
    build_workspace_inspector,
    discover_workspace_units,
    parse_optional_iso_date,
    require_workspace_units,
)
from jezz.codes.inspector import BuildingInspector
from jezz.codes.permits import CodePermit
from jezz.codes.polyglot_ir import InspectedUnit, SupportedLanguage
from jezz.codes.ratchet import (
    DiffBaseMode,
    DifferentialScope,
    GitDiffError,
    inspect_differential_rough_in,
    resolve_git_changed_paths,
)
from jezz.evals.cross_provider import (
    ProviderMatrixComparison,
    ProviderSuiteSpec,
    format_provider_matrix_markdown,
    parse_provider_suite_spec,
    run_provider_matrix_benchmark,
)
from jezz.evals.evaluators import GuardedDataset, build_track4_dataset
from jezz.evals.runner import (
    default_harness_deps,
    format_ab_benchmark_markdown,
    run_ab_benchmark,
)
from jezz.evals.schemas import AbBenchmarkComparison, BenchmarkRunConfig
from jezz.evals.tasks import build_track4_benchmark_cases
from jezz.guards import (
    BoundedText,
    BudgetExceededError,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    jezz_callable,
    read_bounded_text,
)
from jezz.mcp_server import run_stdio_mcp_server
from jezz.methodology.dossier import EngineeringDossier, audit_pce_dossier
from jezz.methodology.mandate import EngineeringMandate
from jezz.secrets import (
    GuardedSecretStr,
    SecretResolutionError,
    canonical_provider_family,
    resolve_model_api_key,
)

type CliExitCode = Annotated[int, Field(ge=0, le=2)]
type GuardedTextStream = Annotated[io.TextIOBase, InstanceGuard(io.TextIOBase)]
type _CommandHandler = Callable[[argparse.Namespace, CliIoStreams], CliExitCode]


class OutputFormat(StrEnum):
    """Structured output format supported by ``jezz`` CLI subcommands."""

    MARKDOWN = "markdown"
    JSON = "json"


class GateRunnerChoice(StrEnum):
    """Dynamic verification gate runner selectable during ``jezz run``."""

    SUBPROCESS = "subprocess"
    IN_PROCESS = "in-process"


def _append_plan_approval_lines(lines: list[str], mandate: EngineeringMandate) -> None:
    lines.extend(["", "## Framed Mandate & Write Scopes", f"- **Objective:** {mandate.objective}"])
    for inv in mandate.invariants:
        lines.append(f"- `{inv.invariant_id}` (`{inv.verification_symbol}`): {inv.statement}")
    for scope in mandate.write_scopes:
        owned = ", ".join(f"`{p}`" for p in sorted(scope.owned_files))
        lines.append(f"- **Scope `{scope.scope_id}`:** {owned}")


@jezz_callable
def format_workflow_markdown(result: PceWorkflowResult) -> BoundedText:
    """Render a structured Markdown summary of a ``PceWorkflowResult``."""
    lines = [
        f"# PCE Workflow Execution — `{result.mandate.mandate_id}`",
        f"- **Mode:** `{result.workflow_mode.upper()}`",
        f"- **Status:** `{result.status.upper()}`",
        f"- **Behavior Graph Valid:** `{result.graph_verification.is_valid}` "
        f"(`{len(result.induced_graph.nodes)}` nodes)",
        f"- **Agent Invocations:** `{len(result.telemetry)}`",
    ]
    if result.telemetry:
        exercised = list(dict.fromkeys(t.model_id for t in result.telemetry))
        models_str = ", ".join(f"`{m}`" for m in exercised)
        lines.append(f"- **Models Exercised:** {models_str}")
    if result.total_input_tokens > 0:
        lines.append(
            f"- **Tokens (In / Out / Cache Read):** `{result.total_input_tokens:,}` / "
            f"`{result.total_output_tokens:,}` / `{result.total_cache_read_tokens:,}` "
            f"(`{result.cache_hit_ratio:.1%}` hit, "
            f"`{result.effective_billed_input_tokens:,.0f}` billed-eq)"
        )
    if result.certificate is not None:
        lines.append(f"- **Occupancy Seal:** `{result.certificate.inspector_seal}`")
    if result.status == PceRunStatus.HALTED_FOR_PLAN_APPROVAL:
        _append_plan_approval_lines(lines, result.mandate)
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
    raw_languages: Sequence[str] | None = None,
) -> tuple[Path, tuple[InspectedUnit, ...]]:
    lang_set = frozenset(SupportedLanguage(raw) for raw in raw_languages) if raw_languages else None
    return require_workspace_units(repo_path, tuple(raw_dirs or ()), languages=lang_set)


def _handle_differential_inspect(
    ns: argparse.Namespace,
    resolved_repo: Path,
    units: Sequence[InspectedUnit],
    inspector: BuildingInspector,
    permit: CodePermit | None,
    out_stream: io.TextIOBase,
) -> CliExitCode:
    reference_date = parse_optional_iso_date(ns.as_of)
    staged_only = bool(ns.staged)
    base_ref = str(ns.changed_since) if ns.changed_since is not None else None
    changed_paths = resolve_git_changed_paths(
        resolved_repo,
        base_ref=base_ref,
        staged_only=staged_only,
    )
    scope = DifferentialScope(
        changed_paths=changed_paths,
        diff_mode=DiffBaseMode.STAGED_INDEX if staged_only else DiffBaseMode.GIT_REF,
        base_ref=base_ref,
        reference_date=reference_date,
    )
    diff_report = inspect_differential_rough_in(inspector, units, scope, permit=permit)
    rendered = (
        diff_report.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else diff_report.to_markdown()
    )
    _write_line(out_stream, rendered)
    return 0 if diff_report.is_occupiable else 1


def _handle_inspect(ns: argparse.Namespace, streams: CliIoStreams) -> CliExitCode:
    resolved_repo, units = _require_units(ns.repo_path, ns.target_dirs, ns.languages)
    permit = _load_optional_permit(ns.permit)
    inspector = build_workspace_inspector(resolved_repo, units)
    if ns.changed_since is not None or bool(ns.staged):
        return _handle_differential_inspect(
            ns, resolved_repo, units, inspector, permit, streams.stdout
        )
    reference_date = parse_optional_iso_date(ns.as_of)
    report = inspector.inspect_rough_in(units, permit=permit, reference_date=reference_date)
    rendered = (
        report.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else report.to_markdown()
    )
    _write_line(streams.stdout, rendered)
    return 0 if report.is_occupiable else 1


def _handle_audit(ns: argparse.Namespace, streams: CliIoStreams) -> CliExitCode:
    dossier = EngineeringDossier.model_validate_json(_read_json_file(ns.dossier_json))
    repo_dir: Path = ns.repo if ns.repo is not None else ns.dossier_json.resolve().parent
    _, units = _require_units(repo_dir, ns.target_dirs)
    audit_report = audit_pce_dossier(dossier, units)
    rendered = (
        audit_report.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else audit_report.to_markdown()
    )
    _write_line(streams.stdout, rendered)
    return 0 if audit_report.is_occupancy_sealed else 1


async def _await_with_deadline[ResultT](
    coro: Coroutine[object, object, ResultT],
    timeout_sec: float,
) -> ResultT:
    async with asyncio.timeout(timeout_sec):
        return await coro


@jezz_callable
def resolve_cli_model(
    model_name: IdentifierStr,
    *,
    api_key: GuardedSecretStr | None = None,
) -> GuardedModel:
    """Resolve a Pydantic AI ``provider:model`` string into a TLS-resilient ``Model``."""
    return TlsResilientModel(model_name, api_key=api_key)


def _instantiate_cli_model(model_name: str, api_key: GuardedSecretStr | None) -> GuardedModel:
    if api_key is None:
        return resolve_cli_model(model_name)
    return resolve_cli_model(model_name, api_key=api_key)


def _one_shot_clarification_callback(
    clarifications: Sequence[str] | None,
) -> ElicitationCallback | None:
    if not clarifications:
        return None
    pending: list[tuple[str, ...]] = [tuple(clarifications)]

    def _supply(_mandate: EngineeringMandate) -> Sequence[str] | None:
        return pending.pop(0) if pending else None

    return _supply


def _dispatch_orchestrator_coro(
    orchestrator: PceWorkflowOrchestrator,
    ns: argparse.Namespace,
    resolved_repo: Path,
    permit: CodePermit | None,
    mode: PceWorkflowMode,
) -> Coroutine[object, object, PceWorkflowResult]:
    if ns.resume_elicitation is not None:
        if not ns.clarifications:
            msg = "--resume-elicitation requires at least one --clarification answer."
            raise ValueError(msg)
        ticket = ElicitationTicket.model_validate_json(_read_json_file(ns.resume_elicitation))
        return orchestrator.resume_elicitation(
            resolved_repo,
            ticket,
            tuple(str(c) for c in ns.clarifications),
            permit=permit,
        )
    if ns.mandate is not None:
        mandate = EngineeringMandate.model_validate_json(_read_json_file(ns.mandate))
        if mode == PceWorkflowMode.RENOVATE:
            return orchestrator.renovate_mandate(resolved_repo, mandate, permit=permit)
        return orchestrator.execute_mandate(resolved_repo, mandate, permit=permit)
    if mode == PceWorkflowMode.RENOVATE:
        return orchestrator.renovate_prompt(resolved_repo, str(ns.prompt), permit=permit)
    return orchestrator.execute_prompt(resolved_repo, str(ns.prompt), permit=permit)


def _resolve_credentials_for_models(
    model_names: Sequence[str],
    ns: argparse.Namespace,
    search_roots: Sequence[Path],
) -> dict[str, GuardedSecretStr | None]:
    families = {fam for m in model_names if (fam := canonical_provider_family(m))}
    if ns.pass_uri is not None and len(families) > 1:
        msg = (
            f"Single --api-key-uri cannot be shared across multiple model providers "
            f"{sorted(families)}; configure per-provider URIs under "
            "[tool.jezz.secrets.providers] in pyproject.toml."
        )
        raise SecretResolutionError(msg)

    family_cache: dict[str, GuardedSecretStr | None] = {}
    by_model: dict[str, GuardedSecretStr | None] = {}
    for model_name in model_names:
        cache_slot = canonical_provider_family(model_name) or f"raw:{model_name}"
        if cache_slot not in family_cache:
            family_cache[cache_slot] = resolve_model_api_key(
                model_name,
                explicit_pass_uri=ns.pass_uri,
                search_roots=tuple(search_roots),
                reason_override=ns.agent_reason,
            )
        by_model[model_name] = family_cache[cache_slot]
    return by_model


def _resolve_cli_routing(
    ns: argparse.Namespace,
    search_roots: Sequence[Path],
) -> tuple[ResolvedModelRouting, dict[str, GuardedSecretStr | None]]:
    cache_override = PromptCacheMode(str(ns.prompt_cache)) if ns.prompt_cache is not None else None
    overrides = CliModelOverrides(
        model=str(ns.model) if ns.model is not None else None,
        architect_model=str(ns.architect_model) if ns.architect_model is not None else None,
        builder_model=str(ns.builder_model) if ns.builder_model is not None else None,
        inspector_model=str(ns.inspector_model) if ns.inspector_model is not None else None,
        prompt_cache=cache_override,
    )
    project_cfg = discover_models_config(tuple(search_roots))
    routing = resolve_specialist_routing(overrides, project_config=project_cfg)
    by_model = _resolve_credentials_for_models(routing.unique_model_names(), ns, search_roots)
    return routing, by_model


def _execute_pce_run(
    ns: argparse.Namespace,
    resolved_repo: Path,
    permit: CodePermit | None,
    streams: CliIoStreams,
    *,
    mode: PceWorkflowMode = PceWorkflowMode.GREENFIELD,
) -> PceWorkflowResult:
    timeout_sec = float(ns.timeout_sec)
    if timeout_sec <= 0.0 or timeout_sec > 3600.0:
        msg = f"Invalid --timeout-sec={timeout_sec}; expected (0, 3600]."
        raise ValueError(msg)
    routing, creds_by_model = _resolve_cli_routing(ns, (resolved_repo, Path.cwd()))
    suite = routing.build_suite(lambda m: _instantiate_cli_model(m, creds_by_model[m]))
    is_uv = ns.gate_runner == GateRunnerChoice.SUBPROCESS
    runner: DynamicGatePort = UvSubprocessGateRunner() if is_uv else InProcessGateRunner()
    provisioner: ProvisionPort = (
        UvSubprocessProvisionRunner() if is_uv else InProcessProvisionRunner()
    )
    budget = PceRuntimeBudget.production_defaults(
        agent_timeout_sec=min(480.0, timeout_sec),
        prompt_cache_mode=routing.prompt_cache,
    )
    pre_supplied = (
        _one_shot_clarification_callback(ns.clarifications)
        if ns.resume_elicitation is None
        else None
    )
    hooks = resolve_workflow_interaction_hooks(ns, streams, pre_supplied)
    orchestrator = PceWorkflowOrchestrator(
        suite,
        gate_runner=runner,
        provision_runner=provisioner,
        budget=budget,
        elicitation_callback=hooks,
    )
    coro = _dispatch_orchestrator_coro(orchestrator, ns, resolved_repo, permit, mode)
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
    allowed = result.mandate.authorized_files() | result.mandate.authorized_manifest_files()
    present_mutated = frozenset(
        rel_file
        for rel_file in allowed
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


def _persist_elicitation_ticket(target_path: Path, result: PceWorkflowResult) -> None:
    if result.elicitation_ticket is None:
        return
    resolved_parent = target_path.resolve().parent
    resolved_parent.mkdir(parents=True, exist_ok=True)
    contained = ContainedPath.from_parts(resolved_parent, target_path.name)
    with contained.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(result.elicitation_ticket.model_dump_json(indent=2) + "\n")


def _is_successful_workflow_exit(result: PceWorkflowResult, *, plan_only: bool) -> bool:
    if result.status == PceRunStatus.OCCUPANCY_CERTIFIED:
        return True
    return plan_only and result.status == PceRunStatus.HALTED_FOR_PLAN_APPROVAL


def _run_workflow_command(
    ns: argparse.Namespace,
    streams: CliIoStreams,
    mode: PceWorkflowMode,
) -> CliExitCode:
    resolved_repo = ns.repo_path.resolve()
    if not resolved_repo.is_dir():
        msg = f"Workspace directory does not exist: '{ns.repo_path}'."
        raise FileNotFoundError(msg)
    permit = _load_optional_permit(ns.permit)
    result = (
        _execute_pce_run(ns, resolved_repo, permit, streams)
        if mode == PceWorkflowMode.GREENFIELD
        else _execute_pce_run(ns, resolved_repo, permit, streams, mode=mode)
    )
    if ns.write_mandate is not None:
        persist_mandate_json(resolved_repo, ns.write_mandate, result.mandate)
    if ns.write_elicitation is not None:
        _persist_elicitation_ticket(ns.write_elicitation, result)
    if ns.write_dossier is not None and result.inspection_report is not None:
        _persist_run_dossier(resolved_repo, str(ns.write_dossier), result)
    rendered = (
        result.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else format_workflow_markdown(result)
    )
    _write_line(streams.stdout, rendered)
    return 0 if _is_successful_workflow_exit(result, plan_only=bool(ns.plan_only)) else 1


def _handle_run(ns: argparse.Namespace, streams: CliIoStreams) -> CliExitCode:
    return _run_workflow_command(ns, streams, PceWorkflowMode.GREENFIELD)


def _handle_renovate(ns: argparse.Namespace, streams: CliIoStreams) -> CliExitCode:
    return _run_workflow_command(ns, streams, PceWorkflowMode.RENOVATE)


def _select_benchmark_dataset(case_ids: Sequence[str] | None) -> GuardedDataset:
    if not case_ids:
        return build_track4_dataset()
    wanted = set(case_ids)
    filtered = tuple(pair for pair in build_track4_benchmark_cases() if pair[0].case_id in wanted)
    if len(filtered) != len(wanted):
        found = {pair[0].case_id for pair in filtered}
        missing = sorted(wanted - found)
        msg = f"Unknown benchmark --case-id value(s): {missing}."
        raise ValueError(msg)
    return build_track4_dataset(filtered)


def _validate_benchmark_timeout(raw_timeout: float) -> float:
    timeout_sec = float(raw_timeout)
    if timeout_sec <= 0.0 or timeout_sec > 14_400.0:
        msg = f"Invalid --timeout-sec={timeout_sec}; expected (0, 14400]."
        raise ValueError(msg)
    return timeout_sec


def _execute_cli_benchmark(ns: argparse.Namespace, work_dir: Path) -> AbBenchmarkComparison:
    timeout_sec = _validate_benchmark_timeout(ns.timeout_sec)
    routing, creds_by_model = _resolve_cli_routing(ns, (work_dir, Path.cwd()))
    ctrl_name = routing.synthesize
    ctrl_model = _instantiate_cli_model(ctrl_name, creds_by_model[ctrl_name])
    cfg = BenchmarkRunConfig(
        max_concurrency=int(ns.max_concurrency),
        prompt_cache_mode=routing.prompt_cache,
        agent_timeout_sec=min(480.0, timeout_sec),
        sync_workspace_venv=(ns.gate_runner == GateRunnerChoice.SUBPROCESS),
    )
    deps = default_harness_deps(
        work_dir,
        config=cfg,
        model_factory=lambda: _instantiate_cli_model(ctrl_name, creds_by_model[ctrl_name]),
        suite_factory=lambda: routing.build_suite(
            lambda m: _instantiate_cli_model(m, creds_by_model[m])
        ),
    )
    ds = _select_benchmark_dataset(ns.case_ids)
    coro = run_ab_benchmark(ctrl_model, deps, model_label=routing.summary_label(), dataset=ds)
    return asyncio.run(_await_with_deadline(coro, timeout_sec))


def _execute_cli_matrix_benchmark(
    ns: argparse.Namespace,
    work_dir: Path,
    raw_suites: Sequence[str],
) -> ProviderMatrixComparison:
    timeout_sec = _validate_benchmark_timeout(ns.timeout_sec)
    default_cache = (
        PromptCacheMode(str(ns.prompt_cache))
        if ns.prompt_cache is not None
        else PromptCacheMode.AUTO
    )
    suites: tuple[ProviderSuiteSpec, ...] = tuple(
        parse_provider_suite_spec(str(s), default_cache=default_cache) for s in raw_suites
    )
    all_models = list(
        dict.fromkeys(m for suite in suites for m in suite.routing.unique_model_names())
    )
    creds_by_model = _resolve_credentials_for_models(all_models, ns, (work_dir, Path.cwd()))
    cfg = BenchmarkRunConfig(
        max_concurrency=int(ns.max_concurrency),
        prompt_cache_mode=default_cache,
        agent_timeout_sec=min(480.0, timeout_sec),
        sync_workspace_venv=(ns.gate_runner == GateRunnerChoice.SUBPROCESS),
    )
    ds = _select_benchmark_dataset(ns.case_ids)
    coro = run_provider_matrix_benchmark(
        suites,
        work_dir,
        lambda m: _instantiate_cli_model(m, creds_by_model[m]),
        base_config=cfg,
        dataset=ds,
    )
    return asyncio.run(_await_with_deadline(coro, timeout_sec))


def _persist_benchmark_json(work_dir: Path, rel_json: str, payload: str) -> None:
    out_target = ContainedPath.from_parts(work_dir, rel_json)
    out_target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with out_target.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(payload + "\n")


def _comparison_passed(comparison: AbBenchmarkComparison) -> bool:
    t = comparison.treatment_summary
    return t.cases_jbc_occupiable == t.cases_total and t.cases_oracle_passed == t.cases_total


def _handle_benchmark(ns: argparse.Namespace, streams: CliIoStreams) -> CliExitCode:
    resolved_work = ns.work_dir.resolve()
    resolved_work.mkdir(parents=True, exist_ok=True)
    if ns.suites:
        matrix = _execute_cli_matrix_benchmark(ns, resolved_work, tuple(ns.suites))
        if ns.write_json is not None:
            _persist_benchmark_json(
                resolved_work, str(ns.write_json), matrix.model_dump_json(indent=2)
            )
        rendered_mat = (
            matrix.model_dump_json(indent=2)
            if ns.output_format == OutputFormat.JSON
            else format_provider_matrix_markdown(matrix)
        )
        _write_line(streams.stdout, rendered_mat)
        return 0 if all(_comparison_passed(c) for c in matrix.comparisons) else 1

    comparison = _execute_cli_benchmark(ns, resolved_work)
    if ns.write_json is not None:
        _persist_benchmark_json(
            resolved_work, str(ns.write_json), comparison.model_dump_json(indent=2)
        )
    rendered = (
        comparison.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else format_ab_benchmark_markdown(comparison)
    )
    _write_line(streams.stdout, rendered)
    return 0 if _comparison_passed(comparison) else 1


def _handle_mcp(ns: argparse.Namespace, streams: CliIoStreams) -> CliExitCode:
    _ = streams
    workspace_root: Path | None = ns.workspace_root
    run_stdio_mcp_server(workspace_root=workspace_root)
    return 0


_COMMAND_HANDLERS: Final[Mapping[str, _CommandHandler]] = MappingProxyType(
    {
        "inspect": _handle_inspect,
        "audit": _handle_audit,
        "run": _handle_run,
        "renovate": _handle_renovate,
        "benchmark": _handle_benchmark,
        "mcp": _handle_mcp,
        "init": handle_init_command,
        "hook": handle_hook_command,
    }
)


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
        "--api-key-uri",
        dest="pass_uri",
        default=None,
        help="Proton Pass secret URI (pass://vault/item/field) for model API key.",
    )
    parser.add_argument(
        "--agent-reason",
        dest="agent_reason",
        default=None,
        help="Audit reason logged via PROTON_PASS_AGENT_REASON during pass-cli access.",
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
    p_wf.add_argument("--gate-runner", choices=("subprocess", "in-process"), default="subprocess")
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


def _build_cli_parser() -> argparse.ArgumentParser:
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


def _resolve_default_stdin(explicit_stdin: GuardedTextStream | None) -> io.TextIOBase:
    if explicit_stdin is not None:
        return explicit_stdin
    if isinstance(sys.stdin, io.TextIOBase):
        return sys.stdin
    return io.StringIO("")


@jezz_callable
def main(
    argv: Annotated[Sequence[BoundedText], Field(max_length=256)] | None = None,
    *,
    stdout: GuardedTextStream | None = None,
    stderr: GuardedTextStream | None = None,
    stdin: GuardedTextStream | None = None,
) -> CliExitCode:
    """Execute the ``jezz`` CLI and return POSIX exit code ``0``, ``1``, or ``2``."""
    raw_args = tuple(sys.argv[1:]) if argv is None else tuple(argv)
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    in_stream = _resolve_default_stdin(stdin)
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

    streams = CliIoStreams(stdin=in_stream, stdout=out_stream, stderr=err_stream)
    try:
        return handler(namespace, streams)
    except (
        FileNotFoundError,
        SyntaxError,
        UnicodeDecodeError,
        ValidationError,
        ValueError,
        UserError,
        AgentRunError,
        ModelAPIError,
        BudgetExceededError,
        GitDiffError,
        SecretResolutionError,
        TimeoutError,
        OSError,
    ) as exc:
        detail = str(exc) or type(exc).__name__
        _write_line(err_stream, f"jezz error: {detail}")
        return 2


__all__ = [
    "CliExitCode",
    "GateRunnerChoice",
    "GuardedTextStream",
    "OutputFormat",
    "TlsResilientModel",
    "discover_workspace_units",
    "format_workflow_markdown",
    "main",
    "resolve_cli_model",
]
