"""Track 4 & 17 Control Arm: Steelmanned Prose 'SDD' pipeline across 4 ecosystems."""

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, UsageLimits
from pydantic_ai.messages import InstructionPart

from jezz.agents.caching import resolve_cache_model_settings
from jezz.agents.specialists import GuardedModel
from jezz.codes.polyglot_ir import (
    LANGUAGE_DISPLAY_NAMES,
    SupportedLanguage,
    detect_language_for_path,
)
from jezz.evals.schemas import (
    BenchmarkRunConfig,
    BenchmarkTaskSpec,
    SddDesignArtifact,
    SddImplementationBundle,
    SddSpecArtifact,
    SddTasksArtifact,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    BoundedText,
    ContainedPath,
    InstanceGuard,
    NonNegativeInt,
    jezz_callable,
)

type GuardedSddAgent[StageOutT] = Annotated[Agent[None, StageOutT], InstanceGuard(Agent)]

SDD_CONSTITUTION_MD: Final[str] = """\
# Project Engineering Constitution (SDD Governance)

## Article I — Specification Primacy
Specifications (`spec.md`, `design.md`, `tasks.md`) are the authoritative source of truth.
Preserve all exact package paths, class/enum names, field names, and function signatures.

## Article II — Type Safety & Data Modeling
Target Python 3.14. Provide explicit type annotations on functions and methods, and model
structured records using `pydantic.BaseModel` and `enum.StrEnum` where appropriate.

## Article III — Exact Financial & Integer Arithmetic
Never use floating-point arithmetic (`float`) for monetary cent calculations or pro-rata splits;
use exact integer arithmetic so total cents are strictly conserved.

## Article IV — Security & Input Containment
Always use parameterized SQL placeholders (`?`), confine filesystem paths inside trusted root
directories (blocking `../` and symlink escapes), and reject malformed or unexpected JSON fields.

## Article V — Fault Handling & Bounded Execution
Raise descriptive exceptions (`ValueError`) on invalid inputs without silently swallowing errors
(`except Exception: pass`), and guard graph/loop traversals against cycles and unbounded steps.

## Article VI — Cohesion & Clean Control Flow
Keep classes focused on a single cohesive responsibility without unrelated stateless methods,
and prefer lookup tables over long `if/elif` chains.

## Article VII — Verification & Task Completion
Author `pytest` unit tests covering normal flows and edge cases, and toggle every checklist item
in `tasks.md` from `- [ ]` to `- [x]` once implemented.
"""

_ECOSYSTEM_ARTICLE_II: Final[Mapping[SupportedLanguage, str]] = MappingProxyType(
    {
        SupportedLanguage.TYPESCRIPT: (
            "Target Node-native TypeScript (`erasableSyntaxOnly`). Annotate all parameters, "
            "return types, and `readonly` interfaces explicitly without `any`, and author "
            "`node:test` suites under `tests/`."
        ),
        SupportedLanguage.RUST: (
            "Target Rust 2024 edition. Use explicit `Result<T, E>` error types without "
            "`unwrap()`/`expect()`/`panic!` in library code, and author `#[test]` suites under "
            "`tests/`."
        ),
        SupportedLanguage.GO: (
            "Target Go 1.24+. Return explicit `error` values without discarding errors via `_`, "
            "reject unknown JSON fields via `DisallowUnknownFields()`, and author `*_test.go` "
            "suites."
        ),
    }
)

_MODAL_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:SHALL|MUST)\b")
_SCENARIO_RE: Final[re.Pattern[str]] = re.compile(r"\bScenario\b", re.IGNORECASE)
_UNCHECKED_BOX_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\n)\s*[-*]\s*\[ ]")
_CHECKED_BOX_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\n)\s*[-*]\s*\[[xX]]")


class SddPipelineTelemetry(BaseModel):
    """Token, cache, request, and specification-volume telemetry for a 4-stage SDD Control run."""

    model_config = STRICT_FROZEN_CONFIG

    spec_review_lines: NonNegativeInt
    sdd_markdown_valid: bool
    requests: NonNegativeInt
    tool_calls: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_read_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0
    written_code_files: Annotated[tuple[str, ...], Field(max_length=64)]


class _SddStageUsage(BaseModel):
    """Internal per-stage token and cache counters."""

    model_config = STRICT_FROZEN_CONFIG

    requests: NonNegativeInt
    tool_calls: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_read_tokens: NonNegativeInt
    cache_write_tokens: NonNegativeInt


@jezz_callable
def render_sdd_constitution(
    language: SupportedLanguage = SupportedLanguage.PYTHON,
) -> BoundedText:
    """Render the steelmanned SDD Markdown constitution for ``language``."""
    if language == SupportedLanguage.PYTHON:
        return SDD_CONSTITUTION_MD
    ecosystem_guidance = _ECOSYSTEM_ARTICLE_II[language]
    py_article = (
        "Target Python 3.14. Provide explicit type annotations on functions and methods, and "
        "model\nstructured records using `pydantic.BaseModel` and `enum.StrEnum` where "
        "appropriate."
    )
    return SDD_CONSTITUTION_MD.replace(py_article, ecosystem_guidance)


def _write_contained_file(root_dir: Path, rel_path: str, content: str) -> ContainedPath:
    contained = ContainedPath.from_parts(root_dir, rel_path)
    contained.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with contained.resolved_path.open("wb") as fh:
        fh.write(content.encode("utf-8"))
    return contained


@jezz_callable
def validate_sdd_markdown_compliance(
    spec_markdown: BoundedText,
    completed_tasks_markdown: BoundedText,
) -> bool:
    """Check OpenSpec/Kiro Markdown validity (SHALL/MUST + Scenario + all tasks '[x]')."""
    has_modal = _MODAL_RE.search(spec_markdown) is not None
    has_scenario = _SCENARIO_RE.search(spec_markdown) is not None
    has_checked = _CHECKED_BOX_RE.search(completed_tasks_markdown) is not None
    has_unchecked = _UNCHECKED_BOX_RE.search(completed_tasks_markdown) is not None
    return has_modal and has_scenario and has_checked and not has_unchecked


def _is_valid_sdd_patch_path(raw_path: str, language: SupportedLanguage) -> bool:
    if not raw_path or raw_path.startswith(("/", "~")) or "\\" in raw_path:
        return False
    parts = raw_path.split("/")
    if any(p in {"", ".", ".."} for p in parts) or parts[0] == "specs":
        return False
    if detect_language_for_path(raw_path) != language:
        return False
    if language == SupportedLanguage.GO:
        return len(parts) >= 2
    return parts[0] in {"src", "tests"}


def _make_sdd_bundle_validator(
    language: SupportedLanguage,
) -> object:
    lang_label = LANGUAGE_DISPLAY_NAMES[language]

    def _validate(bundle: SddImplementationBundle) -> SddImplementationBundle:
        for patch in bundle.patches:
            if not _is_valid_sdd_patch_path(patch.file_path, language):
                raise ModelRetry(
                    f"Implementation file '{patch.file_path}' is not a valid relative "
                    f"{lang_label} source/test path."
                )
        return bundle

    return _validate


@jezz_callable
def build_sdd_specify_agent(
    model: GuardedModel,
    *,
    language: SupportedLanguage = SupportedLanguage.PYTHON,
) -> GuardedSddAgent[SddSpecArtifact]:
    """Build Stage 1 SDD prose specification agent with leading static constitution."""
    lang_label = LANGUAGE_DISPLAY_NAMES[language]
    return Agent[None, SddSpecArtifact](
        model,
        output_type=SddSpecArtifact,
        name="sdd_specify_stage",
        retries=2,
        instructions=(
            InstructionPart(content=render_sdd_constitution(language), name="sdd_constitution"),
            InstructionPart(
                content=(
                    f"Stage 1 (/speckit.specify + Kiro requirements.md + OpenSpec, {lang_label}): "
                    "Expand the feature request into `spec.md` containing Persona User Stories "
                    "('As a ..., I want ..., so that ...'), EARS requirements ('WHEN ... THE "
                    "SYSTEM SHALL ...'), exact public module/type/function signatures from the "
                    "request, and `#### Scenario:` `GIVEN / WHEN / THEN` acceptance blocks."
                ),
                name="sdd_stage_role",
            ),
        ),
    )


@jezz_callable
def build_sdd_design_agent(
    model: GuardedModel,
    *,
    language: SupportedLanguage = SupportedLanguage.PYTHON,
) -> GuardedSddAgent[SddDesignArtifact]:
    """Build Stage 2 SDD architectural design agent with leading static constitution."""
    lang_label = LANGUAGE_DISPLAY_NAMES[language]
    return Agent[None, SddDesignArtifact](
        model,
        output_type=SddDesignArtifact,
        name="sdd_design_stage",
        retries=2,
        instructions=(
            InstructionPart(content=render_sdd_constitution(language), name="sdd_constitution"),
            InstructionPart(
                content=(
                    f"Stage 2 (/speckit.plan + Kiro design.md, {lang_label}): Given `spec.md`, "
                    "produce `design.md` documenting module architecture, exact public "
                    "type/enum/function definitions, algorithms, and error-handling behavior in "
                    "Markdown."
                ),
                name="sdd_stage_role",
            ),
        ),
    )


@jezz_callable
def build_sdd_tasks_agent(
    model: GuardedModel,
    *,
    language: SupportedLanguage = SupportedLanguage.PYTHON,
) -> GuardedSddAgent[SddTasksArtifact]:
    """Build Stage 3 SDD checklist agent with leading static constitution."""
    return Agent[None, SddTasksArtifact](
        model,
        output_type=SddTasksArtifact,
        name="sdd_tasks_stage",
        retries=2,
        instructions=(
            InstructionPart(content=render_sdd_constitution(language), name="sdd_constitution"),
            InstructionPart(
                content=(
                    "Stage 3 (/speckit.tasks + tasks.md): Given `spec.md` and `design.md`, "
                    "produce `tasks.md` with ordered `- [ ] 1.1 ...` implementation and "
                    "unit-testing checklist items."
                ),
                name="sdd_stage_role",
            ),
        ),
    )


@jezz_callable
def build_sdd_implement_agent(
    model: GuardedModel,
    *,
    language: SupportedLanguage = SupportedLanguage.PYTHON,
) -> GuardedSddAgent[SddImplementationBundle]:
    """Build Stage 4 SDD implementation agent with leading static constitution."""
    lang_label = LANGUAGE_DISPLAY_NAMES[language]
    agent = Agent[None, SddImplementationBundle](
        model,
        output_type=SddImplementationBundle,
        name="sdd_implement_stage",
        retries=3,
        instructions=(
            InstructionPart(content=render_sdd_constitution(language), name="sdd_constitution"),
            InstructionPart(
                content=(
                    f"Stage 4 (/speckit.implement, {lang_label}): Implement all {lang_label} "
                    "source files and unit tests strictly from `spec.md`, `design.md`, and "
                    "`tasks.md`, and return `completed_tasks_markdown` with every task marked "
                    "`- [x]`."
                ),
                name="sdd_stage_role",
            ),
        ),
    )
    validator_fn = _make_sdd_bundle_validator(language)
    if callable(validator_fn):
        agent.output_validator(validator_fn)
    return agent


async def _run_sdd_stage[StageOutT](
    agent: Agent[None, StageOutT],
    prompt: str,
    config: BenchmarkRunConfig,
) -> tuple[StageOutT, _SddStageUsage]:
    limits = UsageLimits(
        request_limit=config.request_limit_per_step,
        tool_calls_limit=config.tool_calls_limit_per_step,
        total_tokens_limit=config.total_tokens_limit_per_step,
    )
    settings = resolve_cache_model_settings(config.prompt_cache_mode)
    async with asyncio.timeout(config.agent_timeout_sec):
        res = await agent.run(prompt, usage_limits=limits, model_settings=settings)
    u = res.usage
    stage_usage = _SddStageUsage(
        requests=max(0, u.requests),
        tool_calls=max(0, u.tool_calls),
        input_tokens=max(0, u.input_tokens),
        output_tokens=max(0, u.output_tokens),
        cache_read_tokens=max(0, u.cache_read_tokens),
        cache_write_tokens=max(0, u.cache_write_tokens),
    )
    return res.output, stage_usage


@jezz_callable
async def execute_sdd_prose_pipeline(
    ws_dir: Path,
    task: BenchmarkTaskSpec,
    model: GuardedModel,
    config: BenchmarkRunConfig,
    *,
    architect_model: GuardedModel | None = None,
) -> SddPipelineTelemetry:
    """Execute the 4-stage marketed SDD workflow (`spec.md -> design.md -> tasks.md -> code`)."""
    resolved_ws = ws_dir.resolve()
    lang = task.language
    spec_model = architect_model if architect_model is not None else model
    _write_contained_file(resolved_ws, "specs/constitution.md", render_sdd_constitution(lang))

    s1, u1 = await _run_sdd_stage(
        build_sdd_specify_agent(spec_model, language=lang),
        f"Feature Request:\n{task.prompt}",
        config,
    )
    _write_contained_file(resolved_ws, "specs/spec.md", s1.spec_markdown)

    s2, u2 = await _run_sdd_stage(
        build_sdd_design_agent(spec_model, language=lang),
        f"# spec.md\n{s1.spec_markdown}",
        config,
    )
    _write_contained_file(resolved_ws, "specs/design.md", s2.design_markdown)

    s3, u3 = await _run_sdd_stage(
        build_sdd_tasks_agent(spec_model, language=lang),
        f"# spec.md\n{s1.spec_markdown}\n\n# design.md\n{s2.design_markdown}",
        config,
    )
    _write_contained_file(resolved_ws, "specs/tasks.md", s3.tasks_markdown)

    impl_prompt = (
        f"# spec.md\n{s1.spec_markdown}\n\n"
        f"# design.md\n{s2.design_markdown}\n\n"
        f"# tasks.md\n{s3.tasks_markdown}"
    )
    s4, u4 = await _run_sdd_stage(
        build_sdd_implement_agent(model, language=lang),
        impl_prompt,
        config,
    )
    _write_contained_file(resolved_ws, "specs/tasks.md", s4.completed_tasks_markdown)

    written = tuple(
        _write_contained_file(resolved_ws, patch.file_path, patch.source_code).relative_posix
        for patch in s4.patches
    )
    spec_lines = (
        len(s1.spec_markdown.splitlines())
        + len(s2.design_markdown.splitlines())
        + len(s3.tasks_markdown.splitlines())
    )
    md_valid = validate_sdd_markdown_compliance(
        s1.spec_markdown[:65_536],
        s4.completed_tasks_markdown[:65_536],
    )
    stages = (u1, u2, u3, u4)
    return SddPipelineTelemetry(
        spec_review_lines=spec_lines,
        sdd_markdown_valid=md_valid,
        requests=sum(s.requests for s in stages),
        tool_calls=sum(s.tool_calls for s in stages),
        input_tokens=sum(s.input_tokens for s in stages),
        output_tokens=sum(s.output_tokens for s in stages),
        cache_read_tokens=sum(s.cache_read_tokens for s in stages),
        cache_write_tokens=sum(s.cache_write_tokens for s in stages),
        written_code_files=written,
    )
