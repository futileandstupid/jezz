"""Zero-variance CLI harness exposing JBC inspection, PCE dossier audit, and workflow execution."""

import argparse
import asyncio
import contextlib
import io
import sys
from collections.abc import Callable, Coroutine, Mapping, Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import Field, SecretStr, ValidationError
from pydantic_ai.exceptions import AgentRunError, ModelAPIError, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, infer_model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from jezz.agents.nodes import ElicitationCallback
from jezz.agents.orchestrator import PceWorkflowOrchestrator
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
from jezz.codes.ast_utils import SourceUnit, parse_source_unit
from jezz.codes.inspector import BuildingInspector, ManifestDependencies
from jezz.codes.permits import CodePermit
from jezz.codes.ratchet import (
    DiffBaseMode,
    DifferentialScope,
    GitDiffError,
    inspect_differential_rough_in,
    resolve_git_changed_paths,
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
from jezz.methodology.dossier import EngineeringDossier, audit_pce_dossier
from jezz.methodology.mandate import EngineeringMandate
from jezz.secrets import (
    GuardedSecretStr,
    SecretResolutionError,
    canonical_provider_family,
    resolve_model_api_key,
)

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


def _parse_optional_iso_date(raw_date: str | None) -> date | None:
    if raw_date is None:
        return None
    return date.fromisoformat(raw_date)


def _handle_differential_inspect(
    ns: argparse.Namespace,
    resolved_repo: Path,
    units: Sequence[SourceUnit],
    inspector: BuildingInspector,
    permit: CodePermit | None,
    out_stream: io.TextIOBase,
) -> CliExitCode:
    reference_date = _parse_optional_iso_date(ns.as_of)
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


def _handle_inspect(ns: argparse.Namespace, out_stream: io.TextIOBase) -> CliExitCode:
    resolved_repo, units = _require_units(ns.repo_path, ns.target_dirs)
    permit = _load_optional_permit(ns.permit)
    inspector = _build_workspace_inspector(resolved_repo, units)
    if ns.changed_since is not None or bool(ns.staged):
        return _handle_differential_inspect(ns, resolved_repo, units, inspector, permit, out_stream)
    reference_date = _parse_optional_iso_date(ns.as_of)
    report = inspector.inspect_rough_in(units, permit=permit, reference_date=reference_date)
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


async def _await_with_deadline[ResultT](
    coro: Coroutine[object, object, ResultT],
    timeout_sec: float,
) -> ResultT:
    async with asyncio.timeout(timeout_sec):
        return await coro


type _AuthModelBuilder = Callable[[str, SecretStr], Model]


def _infer_google_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.google import GoogleProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: GoogleProvider(api_key=api_key.get_secret_value()),
    )


def _infer_google_cloud_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.google_cloud import GoogleCloudProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: GoogleCloudProvider(api_key=api_key.get_secret_value()),
    )


def _infer_openai_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.openai import OpenAIProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: OpenAIProvider(api_key=api_key.get_secret_value()),
    )


def _infer_anthropic_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.anthropic import AnthropicProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: AnthropicProvider(api_key=api_key.get_secret_value()),
    )


def _infer_openrouter_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: OpenRouterProvider(api_key=api_key.get_secret_value()),
    )


def _infer_groq_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.groq import GroqProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: GroqProvider(api_key=api_key.get_secret_value()),
    )


def _infer_mistral_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.mistral import MistralProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: MistralProvider(api_key=api_key.get_secret_value()),
    )


def _infer_cohere_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.cohere import CohereProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: CohereProvider(api_key=api_key.get_secret_value()),
    )


def _infer_deepseek_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.deepseek import DeepSeekProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: DeepSeekProvider(api_key=api_key.get_secret_value()),
    )


def _infer_xai_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.xai import XaiProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: XaiProvider(api_key=api_key.get_secret_value()),
    )


def _infer_cerebras_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.cerebras import CerebrasProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: CerebrasProvider(api_key=api_key.get_secret_value()),
    )


def _infer_fireworks_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.fireworks import FireworksProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: FireworksProvider(api_key=api_key.get_secret_value()),
    )


def _infer_together_model(full_name: str, api_key: SecretStr) -> Model:
    from pydantic_ai.providers.together import TogetherProvider

    return infer_model(
        full_name,
        provider_factory=lambda _: TogetherProvider(api_key=api_key.get_secret_value()),
    )


_AUTH_MODEL_BUILDERS: Final[Mapping[str, _AuthModelBuilder]] = MappingProxyType(
    {
        "google": _infer_google_model,
        "google-decisions": _infer_google_model,
        "google-cloud": _infer_google_cloud_model,
        "openai": _infer_openai_model,
        "openai-chat": _infer_openai_model,
        "openai-responses": _infer_openai_model,
        "openai-decisions": _infer_openai_model,
        "anthropic": _infer_anthropic_model,
        "openrouter": _infer_openrouter_model,
        "groq": _infer_groq_model,
        "mistral": _infer_mistral_model,
        "cohere": _infer_cohere_model,
        "deepseek": _infer_deepseek_model,
        "xai": _infer_xai_model,
        "cerebras": _infer_cerebras_model,
        "fireworks": _infer_fireworks_model,
        "together": _infer_together_model,
    }
)


def _infer_model_with_secret(model_name: str, api_key: SecretStr | None) -> Model:
    if api_key is None or model_name == "test":
        return infer_model(model_name)
    active_key: SecretStr = api_key
    if ":" not in model_name:
        msg = (
            f"Expected 'provider:model' identifier when injecting a vault API key: '{model_name}'."
        )
        raise UserError(msg)
    provider_prefix = model_name.split(":", maxsplit=1)[0]
    if provider_prefix.startswith("gateway/"):
        from pydantic_ai.providers.gateway import gateway_provider

        upstream = provider_prefix.removeprefix("gateway/")
        return infer_model(
            model_name,
            provider_factory=lambda _: gateway_provider(
                upstream, api_key=active_key.get_secret_value()
            ),
        )
    builder = _AUTH_MODEL_BUILDERS.get(provider_prefix)
    if builder is None:
        msg = (
            f"Provider '{provider_prefix}' does not support direct SecretStr API-key "
            "injection without environment mutation."
        )
        raise UserError(msg)
    return builder(model_name, active_key)


class TlsResilientModel(WrapperModel):
    """Per-request fresh HTTP client wrapper retrying transient SSL/socket drops."""

    @jezz_callable
    def __init__(
        self,
        model_name: IdentifierStr,
        *,
        api_key: GuardedSecretStr | None = None,
        max_attempts: Annotated[int, Field(ge=1, le=8)] = 4,
    ) -> None:
        super().__init__(_infer_model_with_secret(model_name, api_key))
        self._model_name = model_name
        self._api_key = api_key
        self._max_attempts = max_attempts

    @property
    def configured_model_id(self) -> str:
        """Return the exact ``provider:model`` string supplied at instantiation."""
        return self._model_name

    @jezz_callable
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Execute one request inside a fresh client context, retrying transient OSErrors."""
        last_err: OSError | None = None
        for attempt in range(self._max_attempts):
            try:
                async with _infer_model_with_secret(self._model_name, self._api_key) as fresh_model:
                    return await fresh_model.request(
                        messages,
                        model_settings,
                        model_request_parameters,
                    )
            except OSError as exc:
                last_err = exc
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(float(attempt + 1))
        if last_err is not None:
            raise last_err
        msg = "Unreachable retry exhaustion without captured OSError."
        raise RuntimeError(msg)


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


def _resolve_cli_routing(
    ns: argparse.Namespace,
    search_roots: Sequence[Path],
) -> tuple[ResolvedModelRouting, dict[str, GuardedSecretStr | None]]:
    overrides = CliModelOverrides(
        model=str(ns.model) if ns.model is not None else None,
        architect_model=str(ns.architect_model) if ns.architect_model is not None else None,
        builder_model=str(ns.builder_model) if ns.builder_model is not None else None,
        inspector_model=str(ns.inspector_model) if ns.inspector_model is not None else None,
    )
    project_cfg = discover_models_config(tuple(search_roots))
    routing = resolve_specialist_routing(overrides, project_config=project_cfg)

    families = routing.unique_provider_families()
    if ns.pass_uri is not None and len(families) > 1:
        msg = (
            f"Single --api-key-uri cannot be shared across multiple model providers "
            f"{sorted(families)}; configure per-provider URIs under "
            "[tool.jezz.secrets.providers] in pyproject.toml."
        )
        raise SecretResolutionError(msg)

    family_cache: dict[str, GuardedSecretStr | None] = {}
    by_model: dict[str, GuardedSecretStr | None] = {}
    for model_name in routing.unique_model_names():
        cache_slot = canonical_provider_family(model_name) or f"raw:{model_name}"
        if cache_slot not in family_cache:
            family_cache[cache_slot] = resolve_model_api_key(
                model_name,
                explicit_pass_uri=ns.pass_uri,
                search_roots=tuple(search_roots),
                reason_override=ns.agent_reason,
            )
        by_model[model_name] = family_cache[cache_slot]
    return routing, by_model


def _execute_pce_run(
    ns: argparse.Namespace,
    resolved_repo: Path,
    permit: CodePermit | None,
    *,
    mode: PceWorkflowMode = PceWorkflowMode.GREENFIELD,
) -> PceWorkflowResult:
    timeout_sec = float(ns.timeout_sec)
    if timeout_sec <= 0.0 or timeout_sec > 3600.0:
        msg = f"Invalid --timeout-sec={timeout_sec}; expected (0, 3600]."
        raise ValueError(msg)
    routing, creds_by_model = _resolve_cli_routing(ns, (resolved_repo, Path.cwd()))
    suite = routing.build_suite(lambda m: _instantiate_cli_model(m, creds_by_model[m]))
    runner: DynamicGatePort = (
        UvSubprocessGateRunner()
        if ns.gate_runner == GateRunnerChoice.SUBPROCESS
        else InProcessGateRunner()
    )
    budget = PceRuntimeBudget(
        agent_timeout_sec=min(240.0, timeout_sec),
        request_limit_per_step=24,
        tool_calls_limit_per_step=48,
        total_tokens_limit_per_step=524_288,
    )
    callback = (
        _one_shot_clarification_callback(ns.clarifications)
        if ns.resume_elicitation is None
        else None
    )
    orchestrator = PceWorkflowOrchestrator(
        suite,
        gate_runner=runner,
        budget=budget,
        elicitation_callback=callback,
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


def _persist_elicitation_ticket(target_path: Path, result: PceWorkflowResult) -> None:
    if result.elicitation_ticket is None:
        return
    resolved_parent = target_path.resolve().parent
    resolved_parent.mkdir(parents=True, exist_ok=True)
    contained = ContainedPath.from_parts(resolved_parent, target_path.name)
    with contained.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(result.elicitation_ticket.model_dump_json(indent=2) + "\n")


def _run_workflow_command(
    ns: argparse.Namespace,
    out_stream: io.TextIOBase,
    mode: PceWorkflowMode,
) -> CliExitCode:
    resolved_repo = ns.repo_path.resolve()
    if not resolved_repo.is_dir():
        msg = f"Workspace directory does not exist: '{ns.repo_path}'."
        raise FileNotFoundError(msg)
    permit = _load_optional_permit(ns.permit)
    result = (
        _execute_pce_run(ns, resolved_repo, permit)
        if mode == PceWorkflowMode.GREENFIELD
        else _execute_pce_run(ns, resolved_repo, permit, mode=mode)
    )
    if ns.write_elicitation is not None:
        _persist_elicitation_ticket(ns.write_elicitation, result)
    if ns.write_dossier is not None and result.inspection_report is not None:
        _persist_run_dossier(resolved_repo, str(ns.write_dossier), result)
    rendered = (
        result.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else format_workflow_markdown(result)
    )
    _write_line(out_stream, rendered)
    return 0 if result.status == PceRunStatus.OCCUPANCY_CERTIFIED else 1


def _handle_run(ns: argparse.Namespace, out_stream: io.TextIOBase) -> CliExitCode:
    return _run_workflow_command(ns, out_stream, PceWorkflowMode.GREENFIELD)


def _handle_renovate(ns: argparse.Namespace, out_stream: io.TextIOBase) -> CliExitCode:
    return _run_workflow_command(ns, out_stream, PceWorkflowMode.RENOVATE)


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


def _execute_cli_benchmark(ns: argparse.Namespace, work_dir: Path) -> AbBenchmarkComparison:
    timeout_sec = float(ns.timeout_sec)
    if timeout_sec <= 0.0 or timeout_sec > 14_400.0:
        msg = f"Invalid --timeout-sec={timeout_sec}; expected (0, 14400]."
        raise ValueError(msg)
    routing, creds_by_model = _resolve_cli_routing(ns, (work_dir, Path.cwd()))
    ctrl_name = routing.synthesize
    ctrl_model = _instantiate_cli_model(ctrl_name, creds_by_model[ctrl_name])
    cfg = BenchmarkRunConfig(
        max_concurrency=int(ns.max_concurrency),
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


def _handle_benchmark(ns: argparse.Namespace, out_stream: io.TextIOBase) -> CliExitCode:
    resolved_work = ns.work_dir.resolve()
    resolved_work.mkdir(parents=True, exist_ok=True)
    comparison = _execute_cli_benchmark(ns, resolved_work)
    if ns.write_json is not None:
        out_target = ContainedPath.from_parts(resolved_work, str(ns.write_json))
        out_target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
        with out_target.resolved_path.open("w", encoding="utf-8") as fh:
            fh.write(comparison.model_dump_json(indent=2) + "\n")
    rendered = (
        comparison.model_dump_json(indent=2)
        if ns.output_format == OutputFormat.JSON
        else format_ab_benchmark_markdown(comparison)
    )
    _write_line(out_stream, rendered)
    t = comparison.treatment_summary
    passed = t.cases_jbc_occupiable == t.cases_total and t.cases_oracle_passed == t.cases_total
    return 0 if passed else 1


_COMMAND_HANDLERS: Final[Mapping[str, _CommandHandler]] = MappingProxyType(
    {
        "inspect": _handle_inspect,
        "audit": _handle_audit,
        "run": _handle_run,
        "renovate": _handle_renovate,
        "benchmark": _handle_benchmark,
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
        help="Model override for FRAME, SPECIFY, and HYPOTHESIZE specialists.",
    )
    parser.add_argument(
        "--builder-model",
        dest="builder_model",
        default=None,
        help="Model override for INSTRUMENT, SYNTHESIZE, and REFACTOR specialists.",
    )
    parser.add_argument(
        "--inspector-model",
        dest="inspector_model",
        default=None,
        help="Model override for read-only INSPECT_CODE specialist.",
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
    _register_secret_flags(p_wf)
    p_wf.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )


def _register_benchmark_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p_bmk = sub.add_parser(
        "benchmark",
        help="Run Track 4 pydantic-evals A/B benchmark (Prose SDD vs. Jezz PCE).",
    )
    p_bmk.add_argument("work_dir", type=Path, help="Scratch directory for isolated workspaces.")
    _register_model_routing_flags(p_bmk)
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
    p_ins = sub.add_parser("inspect", help="Run JBC v1.1 Rough-In inspection on a repository.")
    p_ins.add_argument("repo_path", type=Path, help="Target repository root directory.")
    p_ins.add_argument("--target-dir", dest="target_dirs", action="append", default=None)
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
        help="Ratchet only Git-staged Python files in the index.",
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


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jezz",
        description="Jezz Software Building Code (JBC v1.1) & PCE runtime CLI.",
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
        help_text="Renovate a brownfield workspace via BHV-17 REFACTOR to JBC v1.1 occupancy.",
    )
    _register_benchmark_subparser(sub)
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
