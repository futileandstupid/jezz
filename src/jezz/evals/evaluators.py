"""Track 4 & 17: Deterministic pydantic-evals Evaluators and Dataset builders for SDD vs. PCE."""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Annotated

from pydantic import Field
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, EvaluatorContext

from jezz.evals.schemas import (
    ArmRunOutcome,
    BenchmarkCaseMetadata,
    BenchmarkTaskSpec,
)
from jezz.evals.tasks import (
    BenchmarkLanguageSelector,
    build_polyglot_benchmark_cases,
    build_track4_benchmark_cases,
)
from jezz.guards import IdentifierStr, InstanceGuard, jezz_callable

type BenchmarkDataset = Dataset[BenchmarkTaskSpec, ArmRunOutcome, BenchmarkCaseMetadata]
type GuardedDataset = Annotated[BenchmarkDataset, InstanceGuard(Dataset)]
type GuardedEvalContext = Annotated[
    EvaluatorContext[BenchmarkTaskSpec, ArmRunOutcome, BenchmarkCaseMetadata],
    InstanceGuard(EvaluatorContext),
]
type GuardedEvalReason = Annotated[EvaluationReason, InstanceGuard(EvaluationReason)]


@jezz_callable
def evaluate_jbc_safety(
    ctx: GuardedEvalContext,
) -> Mapping[IdentifierStr, GuardedEvalReason]:
    """Score JBC Edition 1.2 LIFE_SAFETY, STRUCTURAL, and total violation counts."""
    out = ctx.output
    cited = ", ".join(out.violated_code_ids) if out.violated_code_ids else "0 violations"
    results: dict[str, GuardedEvalReason] = {
        "jbc_zero_violations": EvaluationReason(
            value=out.jbc_occupiable,
            reason=cited,
        ),
        "zero_life_safety_defects": EvaluationReason(
            value=(out.life_safety_violations == 0),
            reason=f"life_safety={out.life_safety_violations} ({cited})",
        ),
        "jbc_life_safety_violations": EvaluationReason(value=out.life_safety_violations),
        "jbc_structural_violations": EvaluationReason(value=out.structural_violations),
        "jbc_total_violations": EvaluationReason(value=out.total_jbc_violations),
    }
    return MappingProxyType(results)


@jezz_callable
def evaluate_oracle_falsification(
    ctx: GuardedEvalContext,
) -> Mapping[IdentifierStr, GuardedEvalReason]:
    """Score survival against the unseen held-out property oracle and static typecheck."""
    verdict = ctx.output.oracle_verdict
    results: dict[str, GuardedEvalReason] = {
        "heldout_oracle_passed": EvaluationReason(
            value=verdict.passed,
            reason=verdict.summary[:240],
        ),
        "mypy_strict_clean": EvaluationReason(
            value=verdict.mypy_strict_passed,
            reason="static_types exit=0" if verdict.mypy_strict_passed else "static_types failed",
        ),
        "oracle_survival_rate": EvaluationReason(
            value=round(verdict.survival_rate, 4),
            reason=f"{verdict.tests_passed} passed / {verdict.tests_failed} failed",
        ),
    }
    return MappingProxyType(results)


@jezz_callable
def evaluate_structural_maintainability(
    ctx: GuardedEvalContext,
) -> Mapping[IdentifierStr, GuardedEvalReason]:
    """Score AST/CST Cognitive Complexity, class LCOM4 cohesion, and elif ladder counts."""
    st = ctx.output.structural
    results: dict[str, GuardedEvalReason] = {
        "max_cognitive_complexity": EvaluationReason(value=st.max_cognitive_complexity),
        "max_class_lcom4": EvaluationReason(value=st.max_class_lcom4),
        "total_elif_branches": EvaluationReason(value=st.total_elif_branches),
    }
    return MappingProxyType(results)


@jezz_callable
def evaluate_spec_and_cost(
    ctx: GuardedEvalContext,
) -> Mapping[IdentifierStr, GuardedEvalReason]:
    """Score human-reviewable specification line volume and emitted source volume."""
    out = ctx.output
    results: dict[str, GuardedEvalReason] = {
        "spec_review_lines": EvaluationReason(value=out.spec_review_lines),
        "emitted_code_lines": EvaluationReason(value=out.emitted_code_lines),
    }
    return MappingProxyType(results)


def _assemble_benchmark_dataset(
    dataset_name: str,
    raw_cases: Sequence[tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]],
) -> GuardedDataset:
    eval_cases = [
        Case[BenchmarkTaskSpec, ArmRunOutcome, BenchmarkCaseMetadata](
            name=spec.case_id,
            inputs=spec,
            metadata=meta,
        )
        for spec, meta in raw_cases
    ]
    return Dataset[BenchmarkTaskSpec, ArmRunOutcome, BenchmarkCaseMetadata](
        name=dataset_name,
        cases=eval_cases,
        evaluators=(
            evaluate_jbc_safety,
            evaluate_oracle_falsification,
            evaluate_structural_maintainability,
            evaluate_spec_and_cost,
        ),
    )


@jezz_callable
def build_track4_dataset(
    cases: Annotated[
        Sequence[tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]],
        Field(min_length=1, max_length=64),
    ]
    | None = None,
) -> GuardedDataset:
    """Build the typed pydantic-evals Dataset with all Track 4 Python cases and evaluators."""
    raw_cases = build_track4_benchmark_cases() if cases is None else tuple(cases)
    return _assemble_benchmark_dataset("jezz_track4_sdd_vs_pce", raw_cases)


@jezz_callable
def build_polyglot_dataset(
    language: BenchmarkLanguageSelector = "all",
    *,
    cases: Annotated[
        Sequence[tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]],
        Field(min_length=1, max_length=64),
    ]
    | None = None,
) -> GuardedDataset:
    """Build a polyglot pydantic-evals Dataset across Python, TypeScript, Rust, Go, or ``all``."""
    raw_cases = build_polyglot_benchmark_cases(language) if cases is None else tuple(cases)
    ds_name = (
        "jezz_track4_sdd_vs_pce"
        if str(language) == "python"
        else f"jezz_polyglot_sdd_vs_pce_{language}"
    )
    return _assemble_benchmark_dataset(ds_name, raw_cases)
