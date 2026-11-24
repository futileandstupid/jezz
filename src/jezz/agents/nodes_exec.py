"""Parallel TaskGroup execution, workspace refresh, and remediation helpers for PCE nodes."""

import asyncio
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field

from jezz.agents.caching import partition_cached_user_prompt
from jezz.agents.memory import compact_world_model_deterministically
from jezz.agents.nodes_context import (
    GuardedGraphCtx,
    GuardedPceDeps,
    PceGraphDeps,
    active_languages,
    apply_patches_to_state,
    format_decompose_prompt,
    format_deps_prompt,
    has_authorized_tests,
    implementation_scopes,
    make_agent_deps,
    partition_deps_prompt,
    require_mandate,
)
from jezz.agents.polyglot_prompts import is_polyglot_test_path
from jezz.agents.schemas import (
    AgentInvocationTelemetry,
    ConstructionPatchBundle,
    CuratedMemoryDigest,
    FilePatchArtifact,
    InstrumentationSuiteBundle,
    PceWorkflowMode,
)
from jezz.agents.specialists import (
    build_curate_memory_agent,
    build_decompose_agent,
    build_instrument_agent,
    build_refactor_agent,
    build_synthesize_agent,
    run_bounded_agent,
)
from jezz.agents.workspace import WorkspaceSandbox
from jezz.behaviors.runtime import AgentExecutionState
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.ast_graph import build_workspace_ast_graph
from jezz.codes.discovery import build_workspace_inspector
from jezz.codes.inspector import BuildingInspector, ManifestDependencies
from jezz.codes.permits import InspectionReport
from jezz.codes.polyglot_ir import EcosystemManifest, PolyglotManifest
from jezz.codes.ratchet import DifferentialScope
from jezz.guards import BoundedText, InstanceGuard, NonNegativeInt, ShortText, jezz_callable
from jezz.methodology.mandate import (
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
    compute_scope_execution_waves,
)

_MYPY_TEST_ERR_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)tests/[^\s:]+:\d+:\s*error:")
_MYPY_SRC_ERR_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)src/[^\s:]+:\d+:\s*error:")


@jezz_callable
async def maybe_refine_scopes_via_decompose(
    ctx: GuardedGraphCtx,
    mandate: EngineeringMandate,
) -> EngineeringMandate:
    """Optionally refine ``mandate.write_scopes`` via BHV-09 DECOMPOSE specialist."""
    ast_graph = build_workspace_ast_graph(ctx.deps.sandbox.load_units())
    if not ctx.deps.mandate_was_framed or (
        len(ast_graph.symbols) == 0 and len(mandate.write_scopes) <= 1
    ):
        return mandate
    active_model = ctx.deps.models.decompose_model or ctx.deps.models.frame_model
    plan, tel = await run_bounded_agent(
        build_decompose_agent(active_model, languages=active_languages(ctx.deps)),
        behavior_kind=BehaviorKind.DECOMPOSE,
        prompt=format_decompose_prompt(
            mandate,
            ctx.deps.ast_graph_brief or ast_graph.to_compact_brief(),
            ctx.state.world_model.observations,
        ),
        deps=make_agent_deps(ctx.deps),
        budget=ctx.deps.budget,
    )
    ctx.deps.telemetry.append(tel)
    refined = EngineeringMandate.model_validate(
        {**mandate.model_dump(), "write_scopes": plan.write_scopes}
    )
    ctx.deps.mandate = refined
    ctx.deps.sandbox = WorkspaceSandbox(
        ctx.deps.repo_root,
        refined.authorized_files(),
        authorized_manifests=refined.authorized_manifest_files(),
    )
    return refined


def _union_eco_workspace_roots(
    base: EcosystemManifest,
    discovered: EcosystemManifest,
) -> EcosystemManifest:
    return EcosystemManifest(
        runtime_roots=base.runtime_roots,
        dev_roots=base.dev_roots,
        workspace_roots=frozenset(base.workspace_roots | discovered.workspace_roots),
        module_prefix=base.module_prefix or discovered.module_prefix,
    )


@jezz_callable
def refresh_local_workspace_roots(deps: GuardedPceDeps) -> None:
    """Refresh local workspace import roots on ``deps.inspector`` after Phase III SPECIFY."""
    discovered = build_workspace_inspector(deps.repo_root, deps.sandbox.load_units())
    cur = deps.inspector
    py_manifest = ManifestDependencies(
        runtime_roots=cur.manifest.runtime_roots,
        dev_roots=cur.manifest.dev_roots,
        workspace_roots=frozenset(
            cur.manifest.workspace_roots | discovered.manifest.workspace_roots
        ),
    )
    poly_cur = cur.polyglot_manifest
    poly_disc = discovered.polyglot_manifest
    poly_manifest = PolyglotManifest(
        python=_union_eco_workspace_roots(poly_cur.python, poly_disc.python),
        typescript=_union_eco_workspace_roots(poly_cur.typescript, poly_disc.typescript),
        rust=_union_eco_workspace_roots(poly_cur.rust, poly_disc.rust),
        go=_union_eco_workspace_roots(poly_cur.go, poly_disc.go),
    )
    deps.inspector = BuildingInspector(manifest=py_manifest, polyglot_manifest=poly_manifest)


def _test_owning_scopes(mandate: EngineeringMandate) -> tuple[ModuleWriteScope, ...]:
    return tuple(
        s for s in mandate.write_scopes if any(is_polyglot_test_path(p) for p in s.owned_files)
    )


def _clean_test_stem(rel_path: str) -> str:
    stem = Path(rel_path).stem.removeprefix("test_")
    for suffix in ("_test", ".test", ".spec"):
        stem = stem.removesuffix(suffix)
    return stem


def _scope_invariant_match_key(
    inv: DomainInvariant,
    scope: ModuleWriteScope,
    wave_idx_by_scope: dict[str, int],
) -> tuple[int, int, int]:
    wave_idx = wave_idx_by_scope.get(scope.scope_id, 0)
    if inv.verification_symbol in scope.contract_symbols:
        return 2, wave_idx, 100
    haystack = f"{inv.verification_symbol} {inv.statement}"
    sym_hits = sum(
        1 for sym in scope.contract_symbols if re.search(rf"\b{re.escape(sym)}\b", haystack)
    )
    if sym_hits > 0:
        return 1, wave_idx, sym_hits
    stems = {_clean_test_stem(p) for p in scope.owned_files}
    stem_hits = sum(1 for st in stems if st and st.lower() in inv.verification_symbol.lower())
    return 0, stem_hits, wave_idx


def _partition_invariants_by_test_scope(
    mandate: EngineeringMandate,
    test_scopes: Sequence[ModuleWriteScope],
) -> tuple[tuple[ModuleWriteScope, tuple[DomainInvariant, ...]], ...]:
    wave_idx_by_scope = {
        s.scope_id: w_idx for w_idx, wave in enumerate(mandate.execution_waves()) for s in wave
    }
    buckets: dict[str, list[DomainInvariant]] = {s.scope_id: [] for s in test_scopes}
    for inv in mandate.invariants:
        winner = max(
            test_scopes,
            key=lambda s: _scope_invariant_match_key(inv, s, wave_idx_by_scope),
        )
        buckets[winner.scope_id].append(inv)
    return tuple((s, tuple(buckets[s.scope_id])) for s in test_scopes if buckets[s.scope_id])


async def _run_scoped_instrument_slice(
    deps: PceGraphDeps,
    base_prompt: str,
    scope: ModuleWriteScope,
    invariants: tuple[DomainInvariant, ...],
) -> tuple[InstrumentationSuiteBundle, AgentInvocationTelemetry]:
    test_files = sorted(p for p in scope.owned_files if is_polyglot_test_path(p))
    inv_list = "; ".join(f"{i.invariant_id} ({i.verification_symbol})" for i in invariants)
    footer = (
        f"\nActive Instrumentation Scope: {scope.scope_id} owning test file(s) {test_files} ONLY.\n"
        f"Mandated Invariants For This Scope ONLY: {inv_list}.\n"
        f"Emit ONLY {test_files} defining those exact verification_symbol functions; other "
        "scopes' invariants are instrumented concurrently by sibling workers."
    )
    scoped_prompt = partition_cached_user_prompt(
        base_prompt[:57_344],
        footer[:8192],
        mode=deps.budget.prompt_cache_mode,
    )
    return await run_bounded_agent(
        build_instrument_agent(
            deps.models.instrument_model,
            languages=active_languages(deps, scope=scope),
        ),
        behavior_kind=BehaviorKind.INSTRUMENT,
        prompt=scoped_prompt,
        deps=make_agent_deps(deps, scope=scope, scoped_invariants=invariants),
        budget=deps.budget,
    )


async def _run_parallel_instrument_scopes(
    deps: PceGraphDeps,
    base_prompt: str,
    assignments: Sequence[tuple[ModuleWriteScope, tuple[DomainInvariant, ...]]],
) -> tuple[tuple[FilePatchArtifact, ...], str]:
    tasks: list[asyncio.Task[tuple[InstrumentationSuiteBundle, AgentInvocationTelemetry]]] = []
    async with asyncio.TaskGroup() as tg:
        for scope, invs in assignments:
            tasks.append(
                tg.create_task(_run_scoped_instrument_slice(deps, base_prompt, scope, invs))
            )
    patches: list[FilePatchArtifact] = []
    summaries: list[str] = []
    for finished in tasks:
        bundle, tel = finished.result()
        deps.telemetry.append(tel)
        patches.extend(bundle.test_patches)
        summaries.append(bundle.summary)
    return tuple(patches), " | ".join(summaries)


@jezz_callable
async def execute_instrument_step(
    deps: GuardedPceDeps,
    mandate: EngineeringMandate,
) -> tuple[tuple[FilePatchArtifact, ...], BoundedText]:
    """Execute single-scope or parallel TaskGroup BHV-15 INSTRUMENT suite synthesis."""
    renovate_suffix = (
        "\nBrownfield Renovation Mode: Preserve/lock existing observable "
        "public behavior from src/... snapshots while defining every mandated "
        "verification_symbol under full JBC v1.2 test compliance."
        if deps.workflow_mode == PceWorkflowMode.RENOVATE
        else ""
    )
    test_scopes = _test_owning_scopes(mandate)
    assignments = (
        _partition_invariants_by_test_scope(mandate, test_scopes) if len(test_scopes) >= 2 else ()
    )
    if len(assignments) >= 2:
        raw_base = format_deps_prompt(deps, mandate, include_guidance=True)
        base_prompt = f"{raw_base}{renovate_suffix}"[:57_344]
        patches, summary = await _run_parallel_instrument_scopes(deps, base_prompt, assignments)
        return patches, summary[:65_536]
    bundle, tel = await run_bounded_agent(
        build_instrument_agent(
            deps.models.instrument_model,
            languages=active_languages(deps),
        ),
        behavior_kind=BehaviorKind.INSTRUMENT,
        prompt=partition_deps_prompt(
            deps,
            mandate,
            suffix=renovate_suffix,
            include_guidance=True,
        ),
        deps=make_agent_deps(deps),
        budget=deps.budget,
    )
    deps.telemetry.append(tel)
    return bundle.test_patches, bundle.summary[:65_536]


@jezz_callable
def refactor_baseline_suffix(deps: GuardedPceDeps) -> BoundedText:
    """Format optional baseline JBC RED_TAG summary suffix for BHV-17 REFACTOR prompts."""
    if not deps.baseline_red_tag_summary:
        return ""
    return f"\nBaseline JBC RED_TAG Findings:\n{deps.baseline_red_tag_summary}"[:65_536]


async def _run_scoped_slice(
    deps: PceGraphDeps,
    mandate: EngineeringMandate,
    scope: ModuleWriteScope,
) -> tuple[ModuleWriteScope, ConstructionPatchBundle, AgentInvocationTelemetry]:
    slice_footer = (
        f"\nActive Slice Scope: {scope.scope_id} owning {sorted(scope.owned_files)} "
        f"for symbols {list(scope.contract_symbols)}."
    )
    scoped_langs = active_languages(deps, scope=scope)
    if deps.workflow_mode == PceWorkflowMode.RENOVATE:
        ref_suffix = f"{refactor_baseline_suffix(deps)}{slice_footer}"
        ref_model = deps.models.refactor_model or deps.models.synthesize_model
        ref_bundle, ref_tel = await run_bounded_agent(
            build_refactor_agent(ref_model, languages=scoped_langs),
            behavior_kind=BehaviorKind.DELEGATE,
            prompt=partition_deps_prompt(
                deps,
                mandate,
                suffix=ref_suffix,
                include_guidance=True,
            ),
            deps=make_agent_deps(deps, scope=scope),
            budget=deps.budget,
        )
        adapted = ConstructionPatchBundle(
            implementation_patches=ref_bundle.refactored_patches,
            summary=ref_bundle.summary,
        )
        return scope, adapted, ref_tel
    bundle, tel = await run_bounded_agent(
        build_synthesize_agent(deps.models.synthesize_model, languages=scoped_langs),
        behavior_kind=BehaviorKind.DELEGATE,
        prompt=partition_deps_prompt(
            deps,
            mandate,
            suffix=slice_footer,
            include_guidance=True,
        ),
        deps=make_agent_deps(deps, scope=scope),
        budget=deps.budget,
    )
    return scope, bundle, tel


def _implementation_waves(
    mandate: EngineeringMandate,
) -> tuple[tuple[ModuleWriteScope, ...], ...]:
    impl_scopes = implementation_scopes(mandate)
    impl_ids = {s.scope_id for s in impl_scopes}
    projected = tuple(
        ModuleWriteScope(
            scope_id=s.scope_id,
            owned_files=s.owned_files,
            contract_symbols=s.contract_symbols,
            depends_on=frozenset(s.depends_on & impl_ids),
        )
        for s in impl_scopes
    )
    return compute_scope_execution_waves(projected)


async def _run_single_delegation_wave(
    deps: PceGraphDeps,
    state: AgentExecutionState,
    mandate: EngineeringMandate,
    wave_scopes: Sequence[ModuleWriteScope],
) -> list[tuple[ModuleWriteScope, ConstructionPatchBundle]]:
    tasks: list[
        asyncio.Task[tuple[ModuleWriteScope, ConstructionPatchBundle, AgentInvocationTelemetry]]
    ] = []
    async with asyncio.TaskGroup() as tg:
        for scope in wave_scopes:
            state.world_model.active_delegations.add(scope.scope_id)
            tasks.append(tg.create_task(_run_scoped_slice(deps, mandate, scope)))
    wave_bundles: list[tuple[ModuleWriteScope, ConstructionPatchBundle]] = []
    for finished in tasks:
        scope, bundle, tel = finished.result()
        deps.telemetry.append(tel)
        wave_bundles.append((scope, bundle))
    return wave_bundles


@jezz_callable
def verify_and_flatten_delegated_patches(
    delegated: Annotated[
        Sequence[tuple[ModuleWriteScope, ConstructionPatchBundle]],
        Field(max_length=128),
    ],
) -> list[FilePatchArtifact]:
    """Verify write-scope confinement and pairwise disjointness across ``delegated`` patches."""
    seen_files: set[str] = set()
    merged: list[FilePatchArtifact] = []
    for scope, bundle in delegated:
        for patch in bundle.implementation_patches:
            if patch.file_path not in scope.owned_files:
                msg = (
                    f"Delegated worker '{scope.scope_id}' attempted unauthorized edit "
                    f"to '{patch.file_path}'."
                )
                raise PermissionError(msg)
            if patch.file_path in seen_files:
                msg = f"Write-set collision during RECONCILE on '{patch.file_path}'."
                raise ValueError(msg)
            seen_files.add(patch.file_path)
            merged.append(patch)
    return merged


@jezz_callable
async def execute_delegation_waves(
    deps: GuardedPceDeps,
    state: AgentExecutionState,
    mandate: EngineeringMandate,
) -> tuple[list[tuple[ModuleWriteScope, ConstructionPatchBundle]], NonNegativeInt]:
    """Run topological wave-ordered TaskGroup subagents for BHV-10 DELEGATE."""
    waves = _implementation_waves(mandate)
    all_delegated: list[tuple[ModuleWriteScope, ConstructionPatchBundle]] = []
    for idx, wave_scopes in enumerate(waves):
        wave_bundles = await _run_single_delegation_wave(deps, state, mandate, wave_scopes)
        all_delegated.extend(wave_bundles)
        if idx + 1 < len(waves):
            verify_and_flatten_delegated_patches(all_delegated)
            wave_patches = verify_and_flatten_delegated_patches(wave_bundles)
            apply_patches_to_state(state, deps.sandbox, wave_patches)
    return all_delegated, len(waves)


@jezz_callable
def active_ratchet_scope(deps: GuardedPceDeps) -> DifferentialScope:
    """Compute ``DifferentialScope`` spanning authorized and sandbox-modified files."""
    mandate = require_mandate(deps)
    scoped_paths = frozenset(set(mandate.authorized_files()) | set(deps.sandbox.modified_files))
    return DifferentialScope(changed_paths=scoped_paths)


@jezz_callable
def summarize_red_tag_findings(
    report: InspectionReport | None,
    stage: ShortText,
) -> BoundedText:
    """Format compact ``RED_TAG`` citation string for ``falsified_paths`` and diagnosis."""
    if report is None:
        return f"{stage}:unknown_red_tag"
    items = [
        f"{f.code_id}@{f.location.file_path}:{f.location.line} ({f.message})"
        for f in report.unwaived_violations()[:6]
    ]
    return (f"{stage}_red_tag: " + "; ".join(items))[:65_536]


@jezz_callable
def capture_failed_src_snapshot(
    sandbox: Annotated[WorkspaceSandbox, InstanceGuard(WorkspaceSandbox)],
) -> BoundedText:
    """Capture failed candidate source excerpts prior to BHV-20 REVERT rollback."""
    blocks: list[str] = []
    for rel in sorted(sandbox.modified_files):
        if is_polyglot_test_path(rel) or rel in sandbox.authorized_manifests:
            continue
        try:
            content = sandbox.read_file(rel)
        except FileNotFoundError, IsADirectoryError:
            continue
        blocks.append(f"--- FAILED CANDIDATE {rel} ---\n{content[:8000]}")
    return "\n".join(blocks)[:20_000]


def _finding_targets_tests_only(code_id: str, file_path: str, message: str) -> bool:
    if is_polyglot_test_path(file_path):
        return True
    if code_id == "JBC-VRF-005":
        return (
            _MYPY_TEST_ERR_RE.search(message) is not None
            and _MYPY_SRC_ERR_RE.search(message) is None
        )
    return False


@jezz_callable
def is_test_only_static_red_tag(
    report: InspectionReport | None,
    mandate: EngineeringMandate,
) -> bool:
    """Return True iff every unwaived violation in ``report`` is a static test-only fault."""
    if report is None or not has_authorized_tests(mandate):
        return False
    violations = report.unwaived_violations()
    if not violations:
        return False
    return all(
        _finding_targets_tests_only(f.code_id, f.location.file_path, f.message) for f in violations
    )


@jezz_callable
async def run_memory_compaction(
    ctx: GuardedGraphCtx,
    mandate: EngineeringMandate,
) -> CuratedMemoryDigest:
    """Compact working memory deterministically or via BHV-07 CURATE_MEMORY specialist."""
    if len(ctx.state.world_model.observations) < ctx.deps.budget.memory_compaction_threshold:
        return compact_world_model_deterministically(ctx.state, mandate)
    active_model = ctx.deps.models.curate_memory_model or ctx.deps.models.inspect_model
    inv_ids = [inv.invariant_id for inv in mandate.invariants]
    prompt = (
        f"Mandate {mandate.mandate_id} Active Invariant IDs (MUST retain all): {inv_ids}\n"
        f"Prior Falsified Paths (MUST retain verbatim): {ctx.state.world_model.falsified_paths}\n"
        f"Active Hypotheses: {ctx.state.world_model.active_hypotheses}\n"
        f"Raw Observations ({len(ctx.state.world_model.observations)}): "
        f"{ctx.state.world_model.observations}"
    )[:60_000]
    digest, tel = await run_bounded_agent(
        build_curate_memory_agent(active_model),
        behavior_kind=BehaviorKind.CURATE_MEMORY,
        prompt=prompt,
        deps=make_agent_deps(
            ctx.deps,
            prior_falsified_paths=tuple(ctx.state.world_model.falsified_paths),
        ),
        budget=ctx.deps.budget,
    )
    ctx.deps.telemetry.append(tel)
    return digest
