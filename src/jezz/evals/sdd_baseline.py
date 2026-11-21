"""Track 4 Control Arm: Steelmanned Prose 'SDD' pipeline (spec-kit / OpenSpec / Kiro)."""

import asyncio
import re
from pathlib import Path
from typing import Annotated, Final

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, UsageLimits

from jezz.agents.specialists import GuardedModel
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
    NonNegativeInt,
    jezz_callable,
)

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

_MODAL_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:SHALL|MUST)\b")
_SCENARIO_RE: Final[re.Pattern[str]] = re.compile(r"\bScenario\b", re.IGNORECASE)
_UNCHECKED_BOX_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\n)\s*[-*]\s*\[ ]")
_CHECKED_BOX_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\n)\s*[-*]\s*\[[xX]]")


class SddPipelineTelemetry(BaseModel):
    """Token, request, and specification-volume telemetry for a 4-stage SDD Control run."""

    model_config = STRICT_FROZEN_CONFIG

    spec_review_lines: NonNegativeInt
    sdd_markdown_valid: bool
    requests: NonNegativeInt
    tool_calls: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    written_code_files: Annotated[tuple[str, ...], Field(max_length=64)]


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


def _validate_sdd_bundle_paths(bundle: SddImplementationBundle) -> SddImplementationBundle:
    for patch in bundle.patches:
        rel = patch.file_path.strip("/")
        if ".." in rel.split("/") or not (rel.startswith("src/") or rel.startswith("tests/")):
            raise ModelRetry(
                f"Implementation file '{patch.file_path}' must be a relative path under "
                "'src/' or 'tests/'."
            )
    return bundle


def _build_sdd_specify_agent(model: GuardedModel) -> Agent[None, SddSpecArtifact]:
    return Agent[None, SddSpecArtifact](
        model,
        output_type=SddSpecArtifact,
        name="sdd_specify_stage",
        retries=2,
        instructions=(
            f"{SDD_CONSTITUTION_MD}\n"
            "Stage 1 (/speckit.specify + Kiro requirements.md + OpenSpec): Expand the feature "
            "request into a comprehensive `spec.md` containing Persona User Stories ('As a ..., "
            "I want ..., so that ...'), EARS requirements ('WHEN ... THE SYSTEM SHALL ...'), "
            "exact public module/class/function signatures from the request, and `#### Scenario:` "
            "`GIVEN / WHEN / THEN` acceptance blocks."
        ),
    )


def _build_sdd_design_agent(model: GuardedModel) -> Agent[None, SddDesignArtifact]:
    return Agent[None, SddDesignArtifact](
        model,
        output_type=SddDesignArtifact,
        name="sdd_design_stage",
        retries=2,
        instructions=(
            f"{SDD_CONSTITUTION_MD}\n"
            "Stage 2 (/speckit.plan + Kiro design.md): Given `spec.md`, produce `design.md` "
            "documenting module architecture, exact public class/enum/function definitions, "
            "algorithms, and error-handling behavior in Markdown."
        ),
    )


def _build_sdd_tasks_agent(model: GuardedModel) -> Agent[None, SddTasksArtifact]:
    return Agent[None, SddTasksArtifact](
        model,
        output_type=SddTasksArtifact,
        name="sdd_tasks_stage",
        retries=2,
        instructions=(
            f"{SDD_CONSTITUTION_MD}\n"
            "Stage 3 (/speckit.tasks + tasks.md): Given `spec.md` and `design.md`, produce "
            "`tasks.md` with ordered `- [ ] 1.1 ...` implementation and unit-testing checklist "
            "items."
        ),
    )


def _build_sdd_implement_agent(model: GuardedModel) -> Agent[None, SddImplementationBundle]:
    agent = Agent[None, SddImplementationBundle](
        model,
        output_type=SddImplementationBundle,
        name="sdd_implement_stage",
        retries=3,
        instructions=(
            f"{SDD_CONSTITUTION_MD}\n"
            "Stage 4 (/speckit.implement): Implement all Python source files (`src/...`) and "
            "`pytest` unit tests (`tests/...`) strictly from `spec.md`, `design.md`, and "
            "`tasks.md`, and return `completed_tasks_markdown` with every task marked `- [x]`."
        ),
    )
    agent.output_validator(_validate_sdd_bundle_paths)
    return agent


async def _run_sdd_stage[StageOutT](
    agent: Agent[None, StageOutT],
    prompt: str,
    config: BenchmarkRunConfig,
) -> tuple[StageOutT, int, int, int, int]:
    limits = UsageLimits(
        request_limit=config.request_limit_per_step,
        tool_calls_limit=config.tool_calls_limit_per_step,
        total_tokens_limit=config.total_tokens_limit_per_step,
    )
    async with asyncio.timeout(config.agent_timeout_sec):
        res = await agent.run(prompt, usage_limits=limits)
    u = res.usage
    return (
        res.output,
        max(0, u.requests),
        max(0, u.tool_calls),
        max(0, u.input_tokens),
        max(0, u.output_tokens),
    )


@jezz_callable
async def execute_sdd_prose_pipeline(
    ws_dir: Path,
    task: BenchmarkTaskSpec,
    model: GuardedModel,
    config: BenchmarkRunConfig,
) -> SddPipelineTelemetry:
    """Execute the 4-stage marketed SDD workflow (`spec.md -> design.md -> tasks.md -> code`)."""
    resolved_ws = ws_dir.resolve()
    _write_contained_file(resolved_ws, "specs/constitution.md", SDD_CONSTITUTION_MD)

    s1, r1, tc1, in1, out1 = await _run_sdd_stage(
        _build_sdd_specify_agent(model),
        f"Feature Request:\n{task.prompt}",
        config,
    )
    _write_contained_file(resolved_ws, "specs/spec.md", s1.spec_markdown)

    s2, r2, tc2, in2, out2 = await _run_sdd_stage(
        _build_sdd_design_agent(model),
        f"# spec.md\n{s1.spec_markdown}",
        config,
    )
    _write_contained_file(resolved_ws, "specs/design.md", s2.design_markdown)

    s3, r3, tc3, in3, out3 = await _run_sdd_stage(
        _build_sdd_tasks_agent(model),
        f"# spec.md\n{s1.spec_markdown}\n\n# design.md\n{s2.design_markdown}",
        config,
    )
    _write_contained_file(resolved_ws, "specs/tasks.md", s3.tasks_markdown)

    impl_prompt = (
        f"# spec.md\n{s1.spec_markdown}\n\n"
        f"# design.md\n{s2.design_markdown}\n\n"
        f"# tasks.md\n{s3.tasks_markdown}"
    )
    s4, r4, tc4, in4, out4 = await _run_sdd_stage(
        _build_sdd_implement_agent(model),
        impl_prompt,
        config,
    )
    _write_contained_file(resolved_ws, "specs/tasks.md", s4.completed_tasks_markdown)

    written: list[str] = []
    for patch in s4.patches:
        saved = _write_contained_file(resolved_ws, patch.file_path, patch.source_code)
        written.append(saved.relative_posix)

    spec_lines = (
        len(s1.spec_markdown.splitlines())
        + len(s2.design_markdown.splitlines())
        + len(s3.tasks_markdown.splitlines())
    )
    md_valid = validate_sdd_markdown_compliance(
        s1.spec_markdown[:65_536],
        s4.completed_tasks_markdown[:65_536],
    )
    return SddPipelineTelemetry(
        spec_review_lines=spec_lines,
        sdd_markdown_valid=md_valid,
        requests=r1 + r2 + r3 + r4,
        tool_calls=tc1 + tc2 + tc3 + tc4,
        input_tokens=in1 + in2 + in3 + in4,
        output_tokens=out1 + out2 + out3 + out4,
        written_code_files=tuple(written),
    )
