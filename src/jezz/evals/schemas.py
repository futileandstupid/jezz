"""Track 4, 7, & 17: Strict frozen schemas for polyglot SDD vs. PCE evaluation."""

import ast
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, Field, model_validator

from jezz.agents.caching import (
    CoercedPromptCacheMode,
    PromptCacheMode,
    compute_cache_hit_ratio,
    compute_effective_billed_input_tokens,
    compute_input_cost_savings_ratio,
)
from jezz.agents.schemas import FilePatchArtifact
from jezz.codes.adapters import parse_any_source_unit
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    IdentifierStr,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    SourceCodeText,
)
from jezz.methodology.mandate import EngineeringMandate


class BenchmarkArm(StrEnum):
    """Experimental methodology arm under controlled A/B comparison."""

    SDD_PROSE = "sdd_prose"
    JEZZ_PCE = "jezz_pce"


def _oracle_probe_path(
    case_id: str,
    package_name: str,
    language: SupportedLanguage,
) -> str:
    table: dict[SupportedLanguage, str] = {
        SupportedLanguage.PYTHON: f"oracle_{case_id}.py",
        SupportedLanguage.TYPESCRIPT: f"tests/oracle_{case_id}.test.ts",
        SupportedLanguage.RUST: f"tests/oracle_{case_id}.rs",
        SupportedLanguage.GO: f"{package_name}/oracle_{case_id}_test.go",
    }
    return table[language]


class BenchmarkCaseMetadata(BaseModel):
    """Hazard, language, and topology annotations attached to a benchmark Case."""

    model_config = STRICT_FROZEN_CONFIG

    seeded_hazards: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=16),
    ]
    targeted_jbc_codes: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=16),
    ]
    scope_count: Annotated[int, Field(ge=1, le=16)]
    language: SupportedLanguage = SupportedLanguage.PYTHON


class BenchmarkTaskSpec(BaseModel):
    """Matched task specification and held-out adversarial property oracle for one Case."""

    model_config = STRICT_FROZEN_CONFIG

    case_id: IdentifierStr
    package_name: IdentifierStr
    prompt: NonEmptyStr
    mandate: EngineeringMandate
    oracle_test_source: SourceCodeText
    language: SupportedLanguage = SupportedLanguage.PYTHON

    @model_validator(mode="after")
    def _enforce_valid_oracle_syntax(self) -> Self:
        probe_path = _oracle_probe_path(self.case_id, self.package_name, self.language)
        try:
            if self.language == SupportedLanguage.PYTHON:
                ast.parse(self.oracle_test_source, filename=probe_path)
            else:
                parse_any_source_unit(probe_path, self.oracle_test_source)
        except SyntaxError as exc:
            msg = f"Held-out oracle for '{self.case_id}' has invalid syntax: {exc.msg}."
            raise ValueError(msg) from exc
        return self


class SddSpecArtifact(BaseModel):
    """Stage 1 SDD prose output (spec.md / requirements.md with User Stories + EARS + Scenarios)."""

    model_config = STRICT_FROZEN_CONFIG

    spec_markdown: Annotated[str, Field(min_length=1, max_length=500_000)]


class SddDesignArtifact(BaseModel):
    """Stage 2 SDD prose output (design.md / plan.md architectural Markdown)."""

    model_config = STRICT_FROZEN_CONFIG

    design_markdown: Annotated[str, Field(min_length=1, max_length=500_000)]


class SddTasksArtifact(BaseModel):
    """Stage 3 SDD prose output (tasks.md with '- [ ]' checklist items)."""

    model_config = STRICT_FROZEN_CONFIG

    tasks_markdown: Annotated[str, Field(min_length=1, max_length=500_000)]


class SddImplementationBundle(BaseModel):
    """Stage 4 SDD implementation patches plus checked-off '- [x]' tasks.md."""

    model_config = STRICT_FROZEN_CONFIG

    patches: Annotated[
        tuple[FilePatchArtifact, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]
    completed_tasks_markdown: Annotated[str, Field(min_length=1, max_length=500_000)]


class StructuralHealthSnapshot(BaseModel):
    """Deterministic AST/CST complexity, branch-ladder, and class-cohesion telemetry."""

    model_config = STRICT_FROZEN_CONFIG

    python_unit_count: NonNegativeInt
    max_cognitive_complexity: NonNegativeInt
    max_statement_count: NonNegativeInt
    total_elif_branches: NonNegativeInt
    max_class_wmc: NonNegativeInt
    max_class_lcom4: NonNegativeInt
    stateless_method_count: NonNegativeInt

    @property
    def unit_count(self) -> int:
        """Return total inspected source unit count across the workspace."""
        return self.python_unit_count


class OracleSuiteVerdict(BaseModel):
    """Outcome of independent held-out property oracle, self-tests, and static typecheck."""

    model_config = STRICT_FROZEN_CONFIG

    passed: bool
    tests_passed: NonNegativeInt
    tests_failed: NonNegativeInt
    mypy_strict_passed: bool
    self_tests_passed: bool
    summary: BoundedText

    @property
    def survival_rate(self) -> float:
        """Return fraction of held-out oracle tests passing in [0.0, 1.0]."""
        total = self.tests_passed + self.tests_failed
        if total == 0:
            return 1.0 if self.passed else 0.0
        return self.tests_passed / total


class ArmRunOutcome(BaseModel):
    """Complete deterministic grading record for one (Case, BenchmarkArm) execution."""

    model_config = STRICT_FROZEN_CONFIG

    case_id: IdentifierStr
    arm: BenchmarkArm
    completed_cleanly: bool
    jbc_occupiable: bool
    life_safety_violations: NonNegativeInt
    structural_violations: NonNegativeInt
    maintainability_violations: NonNegativeInt
    total_jbc_violations: NonNegativeInt
    violated_code_ids: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    oracle_verdict: OracleSuiteVerdict
    structural: StructuralHealthSnapshot
    sdd_markdown_valid: bool
    spec_review_lines: NonNegativeInt
    emitted_code_lines: NonNegativeInt
    requests: NonNegativeInt
    tool_calls: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_read_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0
    wall_clock_sec: Annotated[float, Field(ge=0.0, le=86_400.0, allow_inf_nan=False)]
    pce_remediation_rollbacks: NonNegativeInt = 0
    failure_note: BoundedText = ""

    @property
    def cache_hit_ratio(self) -> float:
        """Return normalized fraction of ``input_tokens`` served from prompt cache."""
        return compute_cache_hit_ratio(self.input_tokens, self.cache_read_tokens)

    @property
    def effective_billed_input_tokens(self) -> float:
        """Return uncached-equivalent billed input tokens accounting for read/write weights."""
        return compute_effective_billed_input_tokens(
            self.input_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )

    @property
    def input_cost_savings_ratio(self) -> float:
        """Return fractional billed-input cost reduction relative to uncached input tokens."""
        return compute_input_cost_savings_ratio(
            self.input_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )


class BenchmarkRunConfig(BaseModel):
    """Runtime concurrency, cache policy, step budgets, and workspace-sync configuration."""

    model_config = STRICT_FROZEN_CONFIG

    max_concurrency: Annotated[int, Field(ge=1, le=16)] = 2
    prompt_cache_mode: CoercedPromptCacheMode = PromptCacheMode.AUTO
    agent_timeout_sec: Annotated[float, Field(gt=0.0, le=600.0, allow_inf_nan=False)] = 480.0
    request_limit_per_step: Annotated[int, Field(ge=1, le=32)] = 28
    tool_calls_limit_per_step: Annotated[int, Field(ge=0, le=64)] = 56
    total_tokens_limit_per_step: Annotated[int, Field(ge=128, le=1_000_000)] = 786_432
    sync_workspace_venv: bool = True


class ArmAggregateSummary(BaseModel):
    """Experiment-wide quantitative aggregation across all benchmark cases for one arm."""

    model_config = STRICT_FROZEN_CONFIG

    arm: BenchmarkArm
    cases_total: PositiveInt
    cases_jbc_occupiable: NonNegativeInt
    cases_zero_life_safety: NonNegativeInt
    cases_oracle_passed: NonNegativeInt
    cases_mypy_strict_passed: NonNegativeInt
    cases_self_tests_passed: NonNegativeInt
    cases_sdd_markdown_valid: NonNegativeInt
    total_life_safety_violations: NonNegativeInt
    total_structural_violations: NonNegativeInt
    total_maintainability_violations: NonNegativeInt
    total_jbc_violations: NonNegativeInt
    mean_oracle_survival_rate: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    mean_max_cognitive_complexity: Annotated[float, Field(ge=0.0, le=10_000.0, allow_inf_nan=False)]
    mean_max_class_lcom4: Annotated[float, Field(ge=0.0, le=10_000.0, allow_inf_nan=False)]
    total_elif_branches: NonNegativeInt
    mean_spec_review_lines: Annotated[float, Field(ge=0.0, le=1_000_000.0, allow_inf_nan=False)]
    total_input_tokens: NonNegativeInt
    total_output_tokens: NonNegativeInt
    total_cache_read_tokens: NonNegativeInt = 0
    total_cache_write_tokens: NonNegativeInt = 0
    mean_wall_clock_sec: Annotated[float, Field(ge=0.0, le=86_400.0, allow_inf_nan=False)]

    @property
    def cache_hit_ratio(self) -> float:
        """Return aggregate ``total_cache_read_tokens / total_input_tokens``."""
        return compute_cache_hit_ratio(self.total_input_tokens, self.total_cache_read_tokens)

    @property
    def effective_billed_input_tokens(self) -> float:
        """Return aggregate uncached-equivalent billed input tokens across all cases."""
        return compute_effective_billed_input_tokens(
            self.total_input_tokens,
            self.total_cache_read_tokens,
            self.total_cache_write_tokens,
        )

    @property
    def input_cost_savings_ratio(self) -> float:
        """Return aggregate fractional billed-input reduction across all cases."""
        return compute_input_cost_savings_ratio(
            self.total_input_tokens,
            self.total_cache_read_tokens,
            self.total_cache_write_tokens,
        )


class AbBenchmarkComparison(BaseModel):
    """Side-by-side Control (Prose SDD) vs. Treatment (Jezz PCE) benchmark report."""

    model_config = STRICT_FROZEN_CONFIG

    dataset_name: IdentifierStr
    model_label: IdentifierStr
    prompt_cache_mode: CoercedPromptCacheMode = PromptCacheMode.AUTO
    control_summary: ArmAggregateSummary
    treatment_summary: ArmAggregateSummary
    control_outcomes: Annotated[
        tuple[ArmRunOutcome, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    treatment_outcomes: Annotated[
        tuple[ArmRunOutcome, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    pydantic_evals_diff_table: BoundedText
