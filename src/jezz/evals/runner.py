"""Track 4: Controlled A/B benchmark runner executing SDD Control vs. Jezz PCE Treatment."""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import icontract
from pydantic import Field, ValidationError
from pydantic_ai.exceptions import AgentRunError, ModelAPIError, UserError
from pydantic_evals import increment_eval_metric, set_eval_attribute

from jezz.agents.orchestrator import PceWorkflowOrchestrator
from jezz.agents.schemas import PceRunStatus, PceRuntimeBudget
from jezz.agents.specialists import GuardedModel, PceModelSuite, uniform_model_suite
from jezz.agents.workspace import (
    DynamicGatePort,
    InProcessGateRunner,
    UvSubprocessGateRunner,
)
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.inspector import BuildingInspector, ManifestDependencies
from jezz.evals.evaluators import GuardedDataset, build_track4_dataset
from jezz.evals.schemas import (
    AbBenchmarkComparison,
    ArmAggregateSummary,
    ArmRunOutcome,
    BenchmarkArm,
    BenchmarkRunConfig,
    BenchmarkTaskSpec,
)
from jezz.evals.sdd_baseline import execute_sdd_prose_pipeline
from jezz.evals.verifier import (
    ArmExecutionMetrics,
    InProcessWorkspaceVerifier,
    UvWorkspaceVerifier,
    WorkspaceVerifierPort,
    grade_workspace_artifacts,
    provision_scratch_workspace,
)
from jezz.guards import (
    BoundedText,
    BudgetExceededError,
    IdentifierStr,
    InstanceGuard,
    jezz_callable,
)

_RECOVERABLE_ARM_ERRORS = (
    AgentRunError,
    ModelAPIError,
    UserError,
    ValidationError,
    BudgetExceededError,
    TimeoutError,
    ValueError,
    RuntimeError,
    AssertionError,
    ArithmeticError,
    OSError,
)


@dataclass(frozen=True)
class BenchmarkHarnessDeps:
    """Immutable execution dependencies shared across both experimental arms."""

    work_root: Path
    jezz_root: Path
    config: BenchmarkRunConfig
    verifier: WorkspaceVerifierPort
    gate_runner: DynamicGatePort
    model_factory: Callable[[], GuardedModel] | None = None
    suite_factory: Callable[[], PceModelSuite] | None = None


type GuardedHarnessDeps = Annotated[BenchmarkHarnessDeps, InstanceGuard(BenchmarkHarnessDeps)]


@jezz_callable
def default_harness_deps(
    work_root: Path,
    *,
    jezz_root: Path | None = None,
    config: BenchmarkRunConfig | None = None,
    model_factory: Callable[[], GuardedModel] | None = None,
    suite_factory: Callable[[], PceModelSuite] | None = None,
) -> GuardedHarnessDeps:
    """Construct default subprocess or in-process benchmark dependencies."""
    cfg = config or BenchmarkRunConfig()
    resolved_jezz = (
        jezz_root.resolve() if jezz_root is not None else Path(__file__).resolve().parents[3]
    )
    verifier: WorkspaceVerifierPort = (
        UvWorkspaceVerifier(timeout_sec=45.0)
        if cfg.sync_workspace_venv
        else InProcessWorkspaceVerifier()
    )
    gate_runner: DynamicGatePort = (
        UvSubprocessGateRunner(timeout_sec=45.0)
        if cfg.sync_workspace_venv
        else InProcessGateRunner()
    )
    return BenchmarkHarnessDeps(
        work_root=work_root.resolve(),
        jezz_root=resolved_jezz,
        config=cfg,
        verifier=verifier,
        gate_runner=gate_runner,
        model_factory=model_factory,
        suite_factory=suite_factory,
    )


def _record_outcome_telemetry(outcome: ArmRunOutcome) -> None:
    set_eval_attribute("arm", str(outcome.arm))
    set_eval_attribute("violated_codes", list(outcome.violated_code_ids))
    increment_eval_metric("input_tokens", outcome.input_tokens)
    increment_eval_metric("output_tokens", outcome.output_tokens)
    increment_eval_metric("cache_read_tokens", outcome.cache_read_tokens)
    increment_eval_metric("cache_write_tokens", outcome.cache_write_tokens)
    increment_eval_metric("total_tokens", outcome.input_tokens + outcome.output_tokens)
    increment_eval_metric("llm_requests", outcome.requests)


def _resolve_sdd_arm_models(
    fallback_model: GuardedModel,
    deps: BenchmarkHarnessDeps,
) -> tuple[GuardedModel, GuardedModel]:
    if deps.suite_factory is not None:
        suite = deps.suite_factory()
        return suite.specify_model, suite.synthesize_model
    active = deps.model_factory() if deps.model_factory is not None else fallback_model
    return active, active


@jezz_callable
async def run_sdd_control_case(
    task: BenchmarkTaskSpec,
    model: GuardedModel,
    deps: GuardedHarnessDeps,
) -> ArmRunOutcome:
    """Execute one benchmark Case under the Marketed Prose 'SDD' Control Arm."""
    ws = provision_scratch_workspace(
        deps.work_root,
        f"sdd_{task.case_id}",
        task.package_name,
        deps.jezz_root,
        sync_venv=deps.config.sync_workspace_venv,
    )
    arch_model, impl_model = _resolve_sdd_arm_models(model, deps)
    t0 = time.perf_counter()
    try:
        tel = await execute_sdd_prose_pipeline(
            ws.resolved_path,
            task,
            impl_model,
            deps.config,
            architect_model=arch_model,
        )
        elapsed = time.perf_counter() - t0
        metrics = ArmExecutionMetrics(
            arm=BenchmarkArm.SDD_PROSE,
            completed_cleanly=True,
            sdd_markdown_valid=tel.sdd_markdown_valid,
            spec_review_lines=tel.spec_review_lines,
            requests=tel.requests,
            tool_calls=tel.tool_calls,
            input_tokens=tel.input_tokens,
            output_tokens=tel.output_tokens,
            cache_read_tokens=tel.cache_read_tokens,
            cache_write_tokens=tel.cache_write_tokens,
            wall_clock_sec=elapsed,
        )
    except _RECOVERABLE_ARM_ERRORS as exc:
        elapsed = time.perf_counter() - t0
        metrics = ArmExecutionMetrics(
            arm=BenchmarkArm.SDD_PROSE,
            completed_cleanly=False,
            sdd_markdown_valid=False,
            spec_review_lines=0,
            requests=0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            wall_clock_sec=elapsed,
            failure_note=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
    outcome = grade_workspace_artifacts(ws.resolved_path, task, metrics, deps.verifier)
    _record_outcome_telemetry(outcome)
    return outcome


def _build_pce_inspector(ws_path: Path, package_name: str) -> BuildingInspector:
    base = BuildingInspector.from_workspace(ws_path)
    manifest = ManifestDependencies(
        runtime_roots=base.manifest.runtime_roots,
        dev_roots=base.manifest.dev_roots,
        workspace_roots=frozenset(base.manifest.workspace_roots | {package_name, "tests"}),
    )
    return BuildingInspector(manifest=manifest)


def _build_isolated_model_suite(
    fallback_model: GuardedModel,
    deps: BenchmarkHarnessDeps,
) -> PceModelSuite:
    if deps.suite_factory is not None:
        return deps.suite_factory()
    if deps.model_factory is None:
        return uniform_model_suite(fallback_model)
    return PceModelSuite(
        frame_model=deps.model_factory(),
        inspect_model=deps.model_factory(),
        specify_model=deps.model_factory(),
        instrument_model=deps.model_factory(),
        synthesize_model=deps.model_factory(),
        hypothesize_model=deps.model_factory(),
        refactor_model=deps.model_factory(),
        decompose_model=deps.model_factory(),
        inspect_env_model=deps.model_factory(),
        consult_model=deps.model_factory(),
        curate_memory_model=deps.model_factory(),
        provision_model=deps.model_factory(),
    )


@jezz_callable
async def run_pce_treatment_case(
    task: BenchmarkTaskSpec,
    model: GuardedModel,
    deps: GuardedHarnessDeps,
) -> ArmRunOutcome:
    """Execute one benchmark Case under the Jezz PCE + JBC v1.2 Treatment Arm."""
    ws = provision_scratch_workspace(
        deps.work_root,
        f"pce_{task.case_id}",
        task.package_name,
        deps.jezz_root,
        sync_venv=deps.config.sync_workspace_venv,
    )
    mandate_lines = len(task.mandate.model_dump_json(indent=2).splitlines())
    synth_cap = (
        min(64, max(deps.config.request_limit_per_step, 36))
        if deps.config.request_limit_per_step >= 16
        else deps.config.request_limit_per_step
    )
    budget = PceRuntimeBudget(
        prompt_cache_mode=deps.config.prompt_cache_mode,
        agent_timeout_sec=deps.config.agent_timeout_sec,
        request_limit_per_step=deps.config.request_limit_per_step,
        tool_calls_limit_per_step=deps.config.tool_calls_limit_per_step,
        total_tokens_limit_per_step=deps.config.total_tokens_limit_per_step,
        phase_request_overrides={
            BehaviorKind.INSTRUMENT: synth_cap,
            BehaviorKind.SYNTHESIZE: synth_cap,
            BehaviorKind.REFACTOR: synth_cap,
            BehaviorKind.DELEGATE: synth_cap,
        },
    )
    orchestrator = PceWorkflowOrchestrator(
        _build_isolated_model_suite(model, deps),
        inspector=_build_pce_inspector(ws.resolved_path, task.package_name),
        gate_runner=deps.gate_runner,
        budget=budget,
    )
    t0 = time.perf_counter()
    try:
        res = await orchestrator.execute_mandate(ws.resolved_path, task.mandate)
        elapsed = time.perf_counter() - t0
        certified = res.status == PceRunStatus.OCCUPANCY_CERTIFIED
        metrics = ArmExecutionMetrics(
            arm=BenchmarkArm.JEZZ_PCE,
            completed_cleanly=certified,
            sdd_markdown_valid=False,
            spec_review_lines=mandate_lines,
            requests=sum(t.requests for t in res.telemetry),
            tool_calls=sum(t.tool_calls for t in res.telemetry),
            input_tokens=sum(t.input_tokens for t in res.telemetry),
            output_tokens=sum(t.output_tokens for t in res.telemetry),
            cache_read_tokens=sum(t.cache_read_tokens for t in res.telemetry),
            cache_write_tokens=sum(t.cache_write_tokens for t in res.telemetry),
            wall_clock_sec=elapsed,
            pce_remediation_rollbacks=len(res.falsified_paths),
            failure_note="" if certified else f"PCE status={res.status}",
        )
    except _RECOVERABLE_ARM_ERRORS as exc:
        elapsed = time.perf_counter() - t0
        metrics = ArmExecutionMetrics(
            arm=BenchmarkArm.JEZZ_PCE,
            completed_cleanly=False,
            sdd_markdown_valid=False,
            spec_review_lines=mandate_lines,
            requests=0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            wall_clock_sec=elapsed,
            failure_note=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
    outcome = grade_workspace_artifacts(ws.resolved_path, task, metrics, deps.verifier)
    _record_outcome_telemetry(outcome)
    return outcome


@icontract.require(lambda outcomes: len(outcomes) >= 1)
@icontract.ensure(lambda outcomes, result: result.cases_total == len(outcomes))
@jezz_callable
def summarize_arm_outcomes(
    arm: BenchmarkArm,
    outcomes: Annotated[Sequence[ArmRunOutcome], Field(min_length=1, max_length=64)],
) -> ArmAggregateSummary:
    """Aggregate JBC safety, held-out oracle survival, complexity, and cost across cases."""
    n = len(outcomes)
    return ArmAggregateSummary(
        arm=arm,
        cases_total=n,
        cases_jbc_occupiable=sum(1 for o in outcomes if o.jbc_occupiable),
        cases_zero_life_safety=sum(1 for o in outcomes if o.life_safety_violations == 0),
        cases_oracle_passed=sum(1 for o in outcomes if o.oracle_verdict.passed),
        cases_mypy_strict_passed=sum(1 for o in outcomes if o.oracle_verdict.mypy_strict_passed),
        cases_self_tests_passed=sum(1 for o in outcomes if o.oracle_verdict.self_tests_passed),
        cases_sdd_markdown_valid=sum(1 for o in outcomes if o.sdd_markdown_valid),
        total_life_safety_violations=sum(o.life_safety_violations for o in outcomes),
        total_structural_violations=sum(o.structural_violations for o in outcomes),
        total_maintainability_violations=sum(o.maintainability_violations for o in outcomes),
        total_jbc_violations=sum(o.total_jbc_violations for o in outcomes),
        mean_oracle_survival_rate=round(
            sum(o.oracle_verdict.survival_rate for o in outcomes) / n, 4
        ),
        mean_max_cognitive_complexity=round(
            sum(o.structural.max_cognitive_complexity for o in outcomes) / n, 2
        ),
        mean_max_class_lcom4=round(sum(o.structural.max_class_lcom4 for o in outcomes) / n, 2),
        total_elif_branches=sum(o.structural.total_elif_branches for o in outcomes),
        mean_spec_review_lines=round(sum(o.spec_review_lines for o in outcomes) / n, 1),
        total_input_tokens=sum(o.input_tokens for o in outcomes),
        total_output_tokens=sum(o.output_tokens for o in outcomes),
        total_cache_read_tokens=sum(o.cache_read_tokens for o in outcomes),
        total_cache_write_tokens=sum(o.cache_write_tokens for o in outcomes),
        mean_wall_clock_sec=round(sum(o.wall_clock_sec for o in outcomes) / n, 2),
    )


@jezz_callable
async def run_ab_benchmark(
    model: GuardedModel,
    deps: GuardedHarnessDeps,
    *,
    model_label: IdentifierStr = "model",
    dataset: GuardedDataset | None = None,
    control_model: GuardedModel | None = None,
) -> AbBenchmarkComparison:
    """Run controlled pydantic-evals A/B evaluation across Control (SDD) and Treatment (PCE)."""
    active_ds = dataset if dataset is not None else build_track4_dataset()
    active_ctrl_model = control_model if control_model is not None else model

    async def _control_sdd_task(spec: BenchmarkTaskSpec) -> ArmRunOutcome:
        return await run_sdd_control_case(spec, active_ctrl_model, deps)

    async def _treatment_pce_task(spec: BenchmarkTaskSpec) -> ArmRunOutcome:
        return await run_pce_treatment_case(spec, model, deps)

    ctrl_report = await active_ds.evaluate(
        _control_sdd_task,
        name="control_sdd_prose",
        max_concurrency=deps.config.max_concurrency,
        progress=False,
    )
    treat_report = await active_ds.evaluate(
        _treatment_pce_task,
        name="treatment_jezz_pce",
        max_concurrency=deps.config.max_concurrency,
        progress=False,
    )
    ctrl_outcomes = tuple(c.output for c in ctrl_report.cases)
    treat_outcomes = tuple(c.output for c in treat_report.cases)
    diff_rendered = treat_report.render(width=140, baseline=ctrl_report)[:65_000]

    return AbBenchmarkComparison(
        dataset_name=active_ds.name,
        model_label=model_label,
        prompt_cache_mode=deps.config.prompt_cache_mode,
        control_summary=summarize_arm_outcomes(BenchmarkArm.SDD_PROSE, ctrl_outcomes),
        treatment_summary=summarize_arm_outcomes(BenchmarkArm.JEZZ_PCE, treat_outcomes),
        control_outcomes=ctrl_outcomes,
        treatment_outcomes=treat_outcomes,
        pydantic_evals_diff_table=diff_rendered,
    )


def _format_case_row(ctrl: ArmRunOutcome, treat: ArmRunOutcome) -> str:
    c_codes = ",".join(ctrl.violated_code_ids[:4]) or "0"
    t_codes = ",".join(treat.violated_code_ids[:4]) or "0"
    return (
        f"| `{ctrl.case_id}` "
        f"| `{ctrl.life_safety_violations}` / `{ctrl.total_jbc_violations}` ({c_codes}) "
        f"| `{treat.life_safety_violations}` / `{treat.total_jbc_violations}` ({t_codes}) "
        f"| `{ctrl.oracle_verdict.passed}` ({ctrl.oracle_verdict.survival_rate:.0%}) "
        f"| `{treat.oracle_verdict.passed}` ({treat.oracle_verdict.survival_rate:.0%}) "
        f"| `{ctrl.spec_review_lines}` vs `{treat.spec_review_lines}` |"
    )


def _metric_row(label: str, ctrl_val: str, treat_val: str) -> str:
    return f"| {label} | {ctrl_val} | {treat_val} |"


@jezz_callable
def format_ab_benchmark_markdown(comparison: AbBenchmarkComparison) -> BoundedText:
    """Render an executive Markdown report comparing Control (SDD) vs. Treatment (PCE)."""
    c = comparison.control_summary
    t = comparison.treatment_summary
    c_orc = f"`{c.cases_oracle_passed}/{c.cases_total}` (`{c.mean_oracle_survival_rate:.1%}`)"
    t_orc = f"`{t.cases_oracle_passed}/{t.cases_total}` (`{t.mean_oracle_survival_rate:.1%}`)"
    c_tok = f"`{c.total_input_tokens:,}` / `{c.total_output_tokens:,}`"
    t_tok = f"`{t.total_input_tokens:,}` / `{t.total_output_tokens:,}`"
    c_cache = (
        f"`{c.total_cache_read_tokens:,}` (`{c.cache_hit_ratio:.1%}`) "
        f"/ `{c.effective_billed_input_tokens:,.0f}`"
    )
    t_cache = (
        f"`{t.total_cache_read_tokens:,}` (`{t.cache_hit_ratio:.1%}`) "
        f"/ `{t.effective_billed_input_tokens:,.0f}`"
    )
    lines = [
        f"# Track 4 Controlled A/B Benchmark — `{comparison.model_label}`",
        "",
        "## Aggregate Comparison (`Control: Prose SDD` vs. `Treatment: Jezz PCE`)",
        "",
        "| Metric | Control (`SDD` Prose) | Treatment (`Jezz PCE`) |",
        "| :--- | :---: | :---: |",
        _metric_row(
            "JBC v1.2 Occupiable Cases (`0` violations)",
            f"`{c.cases_jbc_occupiable}/{c.cases_total}`",
            f"`{t.cases_jbc_occupiable}/{t.cases_total}`",
        ),
        _metric_row(
            "Cases with Zero `LIFE_SAFETY` Defects",
            f"`{c.cases_zero_life_safety}/{c.cases_total}`",
            f"`{t.cases_zero_life_safety}/{t.cases_total}`",
        ),
        _metric_row(
            "Total `LIFE_SAFETY` / All JBC Violations",
            f"`{c.total_life_safety_violations}` / `{c.total_jbc_violations}`",
            f"`{t.total_life_safety_violations}` / `{t.total_jbc_violations}`",
        ),
        _metric_row("Held-Out `Hypothesis` Oracle Pass Rate", c_orc, t_orc),
        _metric_row(
            "`mypy --strict` Pass Rate",
            f"`{c.cases_mypy_strict_passed}/{c.cases_total}`",
            f"`{t.cases_mypy_strict_passed}/{t.cases_total}`",
        ),
        _metric_row(
            "Arm Self-Tests Pass Rate",
            f"`{c.cases_self_tests_passed}/{c.cases_total}`",
            f"`{t.cases_self_tests_passed}/{t.cases_total}`",
        ),
        _metric_row(
            "Mean Max Cognitive Complexity / `elif`s",
            f"`{c.mean_max_cognitive_complexity}` / `{c.total_elif_branches}`",
            f"`{t.mean_max_cognitive_complexity}` / `{t.total_elif_branches}`",
        ),
        _metric_row(
            "Mean Human-Reviewed Spec Lines",
            f"`{c.mean_spec_review_lines}`",
            f"`{t.mean_spec_review_lines}`",
        ),
        _metric_row("Total Tokens (`In` / `Out`)", c_tok, t_tok),
        _metric_row("Cache Read (`Hit%`) / Effective Billed In", c_cache, t_cache),
        _metric_row(
            "Mean Wall-Clock Latency per Case",
            f"`{c.mean_wall_clock_sec}s`",
            f"`{t.mean_wall_clock_sec}s`",
        ),
        "",
        "## Per-Case Breakdown",
        "",
        (
            "| Case ID | SDD `LIFE_SAFETY`/Total | PCE `LIFE_SAFETY`/Total "
            "| SDD Oracle | PCE Oracle | Spec Lines (SDD vs PCE) |"
        ),
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]
    by_id_treat = {o.case_id: o for o in comparison.treatment_outcomes}
    for ctrl in comparison.control_outcomes:
        treat = by_id_treat.get(ctrl.case_id)
        if treat is not None:
            lines.append(_format_case_row(ctrl, treat))
    lines.extend(
        [
            "",
            "## `pydantic-evals` Baseline Diff Table",
            "",
            "```text",
            comparison.pydantic_evals_diff_table.rstrip(),
            "```",
        ]
    )
    return "\n".join(lines)[:65_536]
