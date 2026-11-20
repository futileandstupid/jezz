"""Canonical coding-agent task-graph archetypes constructively spanning all 21 behaviors."""

from jezz.behaviors.graph import BehaviorEdge, BehaviorGraph, BehaviorNode
from jezz.behaviors.taxonomy import BehaviorKind


def build_readonly_audit_graph() -> BehaviorGraph:
    """Archetype 1: Read-only architectural & security compliance audit (no Stratum IV edits)."""
    return BehaviorGraph(
        name="readonly_security_audit",
        description="Read-only threat modeling and static compliance audit with zero mutations.",
        entry_id="frame_audit",
        nodes=(
            BehaviorNode(
                id="frame_audit",
                kind=BehaviorKind.FRAME,
                label="Bound audit target and deliverables",
            ),
            BehaviorNode(
                id="bind_security_codes",
                kind=BehaviorKind.BIND_CODES,
                label="Attach CWE/static building codes",
            ),
            BehaviorNode(
                id="inspect_target_code",
                kind=BehaviorKind.INSPECT_CODE,
                label="Traverse AST and trust boundaries",
            ),
            BehaviorNode(
                id="consult_standards",
                kind=BehaviorKind.CONSULT,
                label="Cross-check upstream crypto/auth specs",
            ),
            BehaviorNode(
                id="hypothesize_threats",
                kind=BehaviorKind.HYPOTHESIZE,
                label="Model candidate boundary bypass vectors",
            ),
            BehaviorNode(
                id="run_static_gates",
                kind=BehaviorKind.VERIFY_STATIC,
                label="Run static & type compliance checks",
            ),
            BehaviorNode(
                id="certify_audit",
                kind=BehaviorKind.CERTIFY,
                label="Emit code-cited compliance audit report",
            ),
        ),
        edges=(
            BehaviorEdge(source="frame_audit", target="bind_security_codes"),
            BehaviorEdge(source="bind_security_codes", target="inspect_target_code"),
            BehaviorEdge(source="inspect_target_code", target="consult_standards"),
            BehaviorEdge(source="consult_standards", target="hypothesize_threats"),
            BehaviorEdge(source="hypothesize_threats", target="run_static_gates"),
            BehaviorEdge(source="run_static_gates", target="certify_audit"),
        ),
    )


def build_abductive_bugfix_graph() -> BehaviorGraph:
    """Archetype 2: Cyclic abductive debugging with deterministic repro and rollback loop."""
    return BehaviorGraph(
        name="abductive_bugfix_loop",
        description=(
            "Fault localization with cyclic hypothesis falsification, revert, and memory curation."
        ),
        entry_id="frame_bug",
        nodes=(
            BehaviorNode(
                id="frame_bug",
                kind=BehaviorKind.FRAME,
                label="Parse failure symptom and constraints",
            ),
            BehaviorNode(
                id="inspect_runtime_env",
                kind=BehaviorKind.INSPECT_ENV,
                label="Inspect runtime logs and environment state",
            ),
            BehaviorNode(
                id="bind_bugfix_codes",
                kind=BehaviorKind.BIND_CODES,
                label="Bind regression and postcondition codes",
            ),
            BehaviorNode(
                id="trace_defect_code",
                kind=BehaviorKind.INSPECT_CODE,
                label="Trace call graph around fault site",
            ),
            BehaviorNode(
                id="snapshot_baseline",
                kind=BehaviorKind.CHECKPOINT,
                label="Create clean workspace checkpoint",
            ),
            BehaviorNode(
                id="formulate_hypothesis",
                kind=BehaviorKind.HYPOTHESIZE,
                label="Propose falsifiable root-cause model",
            ),
            BehaviorNode(
                id="write_repro_test",
                kind=BehaviorKind.INSTRUMENT,
                label="Author failing reproduction test",
            ),
            BehaviorNode(
                id="confirm_repro_red",
                kind=BehaviorKind.VERIFY_DYNAMIC,
                label="Execute repro to confirm failure",
            ),
            BehaviorNode(
                id="apply_candidate_fix",
                kind=BehaviorKind.SYNTHESIZE,
                label="Mutate implementation to fix root cause",
            ),
            BehaviorNode(
                id="check_static_types",
                kind=BehaviorKind.VERIFY_STATIC,
                label="Verify types, AST, and lints",
            ),
            BehaviorNode(
                id="check_dynamic_suite",
                kind=BehaviorKind.VERIFY_DYNAMIC,
                label="Run repro and property test suite",
            ),
            BehaviorNode(
                id="rollback_failed_fix",
                kind=BehaviorKind.REVERT,
                label="Restore checkpoint and log falsified hypothesis",
            ),
            BehaviorNode(
                id="compact_debug_context",
                kind=BehaviorKind.CURATE_MEMORY,
                label="Evict failed trace noise and retain negations",
            ),
            BehaviorNode(
                id="certify_bugfix",
                kind=BehaviorKind.CERTIFY,
                label="Seal verified fix with regression evidence",
            ),
        ),
        edges=(
            BehaviorEdge(source="frame_bug", target="inspect_runtime_env"),
            BehaviorEdge(source="inspect_runtime_env", target="bind_bugfix_codes"),
            BehaviorEdge(source="bind_bugfix_codes", target="trace_defect_code"),
            BehaviorEdge(source="trace_defect_code", target="snapshot_baseline"),
            BehaviorEdge(source="snapshot_baseline", target="formulate_hypothesis"),
            BehaviorEdge(source="formulate_hypothesis", target="write_repro_test"),
            BehaviorEdge(source="write_repro_test", target="confirm_repro_red"),
            BehaviorEdge(source="confirm_repro_red", target="apply_candidate_fix"),
            BehaviorEdge(source="apply_candidate_fix", target="check_static_types"),
            BehaviorEdge(source="check_static_types", target="check_dynamic_suite"),
            BehaviorEdge(
                source="check_dynamic_suite",
                target="rollback_failed_fix",
                condition="postcondition_failed",
            ),
            BehaviorEdge(source="rollback_failed_fix", target="compact_debug_context"),
            BehaviorEdge(source="compact_debug_context", target="formulate_hypothesis"),
            BehaviorEdge(
                source="check_dynamic_suite",
                target="certify_bugfix",
                condition="all_gates_green",
            ),
        ),
    )


def build_greenfield_contract_first_graph() -> BehaviorGraph:
    """Archetype 3: Ambiguity elicitation followed by contract-first greenfield construction."""
    return BehaviorGraph(
        name="greenfield_contract_first",
        description=(
            "Resolves under-specification via ELICIT, then enforces SPECIFY before SYNTHESIZE."
        ),
        entry_id="frame_initial",
        nodes=(
            BehaviorNode(
                id="frame_initial",
                kind=BehaviorKind.FRAME,
                label="Detect underspecified domain invariants",
            ),
            BehaviorNode(
                id="elicit_constraints",
                kind=BehaviorKind.ELICIT,
                label="Query user for domain boundary decisions",
            ),
            BehaviorNode(
                id="reframe_resolved",
                kind=BehaviorKind.FRAME,
                label="Integrate resolved constraints into objective",
            ),
            BehaviorNode(
                id="bind_design_codes",
                kind=BehaviorKind.BIND_CODES,
                label="Bind DbC and static typing building codes",
            ),
            BehaviorNode(
                id="decompose_modules",
                kind=BehaviorKind.DECOMPOSE,
                label="Partition module interfaces and write scopes",
            ),
            BehaviorNode(
                id="checkpoint_repo",
                kind=BehaviorKind.CHECKPOINT,
                label="Snapshot baseline repository state",
            ),
            BehaviorNode(
                id="specify_contracts",
                kind=BehaviorKind.SPECIFY,
                label="Author Pydantic schemas and icontract invariants",
            ),
            BehaviorNode(
                id="author_property_tests",
                kind=BehaviorKind.INSTRUMENT,
                label="Author Hypothesis property test suite",
            ),
            BehaviorNode(
                id="synthesize_module",
                kind=BehaviorKind.SYNTHESIZE,
                label="Implement module against formal contracts",
            ),
            BehaviorNode(
                id="verify_static_strict",
                kind=BehaviorKind.VERIFY_STATIC,
                label="Run strict mypy and ruff gates",
            ),
            BehaviorNode(
                id="verify_properties",
                kind=BehaviorKind.VERIFY_DYNAMIC,
                label="Execute property and contract suite",
            ),
            BehaviorNode(
                id="certify_module",
                kind=BehaviorKind.CERTIFY,
                label="Certify compliance and commit module",
            ),
        ),
        edges=(
            BehaviorEdge(source="frame_initial", target="elicit_constraints"),
            BehaviorEdge(source="elicit_constraints", target="reframe_resolved"),
            BehaviorEdge(source="reframe_resolved", target="bind_design_codes"),
            BehaviorEdge(source="bind_design_codes", target="decompose_modules"),
            BehaviorEdge(source="decompose_modules", target="checkpoint_repo"),
            BehaviorEdge(source="checkpoint_repo", target="specify_contracts"),
            BehaviorEdge(source="specify_contracts", target="author_property_tests"),
            BehaviorEdge(source="author_property_tests", target="synthesize_module"),
            BehaviorEdge(source="synthesize_module", target="verify_static_strict"),
            BehaviorEdge(source="verify_static_strict", target="verify_properties"),
            BehaviorEdge(source="verify_properties", target="certify_module"),
        ),
    )


def build_ast_refactor_graph() -> BehaviorGraph:
    """Archetype 4: Behavior-preserving structural refactoring across symbol graph."""
    return BehaviorGraph(
        name="ast_semantic_refactor",
        description=(
            "Semantics-preserving structural transformation verified statically and dynamically."
        ),
        entry_id="frame_refactor",
        nodes=(
            BehaviorNode(
                id="frame_refactor",
                kind=BehaviorKind.FRAME,
                label="Define structural refactoring scope",
            ),
            BehaviorNode(
                id="inspect_usages",
                kind=BehaviorKind.INSPECT_CODE,
                label="Resolve symbol usages across AST",
            ),
            BehaviorNode(
                id="bind_equivalence_codes",
                kind=BehaviorKind.BIND_CODES,
                label="Bind observable-equivalence invariants",
            ),
            BehaviorNode(
                id="checkpoint_before_refactor",
                kind=BehaviorKind.CHECKPOINT,
                label="Snapshot workspace before AST rewrite",
            ),
            BehaviorNode(
                id="execute_ast_refactor",
                kind=BehaviorKind.REFACTOR,
                label="Apply AST-safe symbol/module transformation",
            ),
            BehaviorNode(
                id="verify_refactor_static",
                kind=BehaviorKind.VERIFY_STATIC,
                label="Verify zero broken references or type errors",
            ),
            BehaviorNode(
                id="verify_refactor_dynamic",
                kind=BehaviorKind.VERIFY_DYNAMIC,
                label="Verify identical behavioral test outcomes",
            ),
            BehaviorNode(
                id="certify_refactor",
                kind=BehaviorKind.CERTIFY,
                label="Certify semantics-preserving refactor",
            ),
        ),
        edges=(
            BehaviorEdge(source="frame_refactor", target="inspect_usages"),
            BehaviorEdge(source="inspect_usages", target="bind_equivalence_codes"),
            BehaviorEdge(source="bind_equivalence_codes", target="checkpoint_before_refactor"),
            BehaviorEdge(source="checkpoint_before_refactor", target="execute_ast_refactor"),
            BehaviorEdge(source="execute_ast_refactor", target="verify_refactor_static"),
            BehaviorEdge(source="verify_refactor_static", target="verify_refactor_dynamic"),
            BehaviorEdge(source="verify_refactor_dynamic", target="certify_refactor"),
        ),
    )


def build_delegated_migration_graph() -> BehaviorGraph:
    """Archetype 5: Dependency provisioning + parallel subagent delegation & reconciliation."""
    return BehaviorGraph(
        name="delegated_dependency_migration",
        description=(
            "Upgrades substrate dependencies, fans out disjoint module migrations, and reconciles."
        ),
        entry_id="frame_migration",
        nodes=(
            BehaviorNode(
                id="frame_migration",
                kind=BehaviorKind.FRAME,
                label="Bound major dependency upgrade objective",
            ),
            BehaviorNode(
                id="consult_migration_docs",
                kind=BehaviorKind.CONSULT,
                label="Fetch upstream breaking-change specification",
            ),
            BehaviorNode(
                id="inspect_lockfile",
                kind=BehaviorKind.INSPECT_ENV,
                label="Inspect current lockfile and Python runtime",
            ),
            BehaviorNode(
                id="bind_migration_codes",
                kind=BehaviorKind.BIND_CODES,
                label="Bind compatibility and lockfile integrity codes",
            ),
            BehaviorNode(
                id="checkpoint_pre_upgrade",
                kind=BehaviorKind.CHECKPOINT,
                label="Snapshot workspace prior to dependency upgrade",
            ),
            BehaviorNode(
                id="provision_new_deps",
                kind=BehaviorKind.PROVISION,
                label="Update pyproject.toml and uv.lock",
            ),
            BehaviorNode(
                id="partition_callsites",
                kind=BehaviorKind.DECOMPOSE,
                label="Partition broken modules into disjoint worker scopes",
            ),
            BehaviorNode(
                id="delegate_workers",
                kind=BehaviorKind.DELEGATE,
                label="Dispatch isolated module migration workers",
            ),
            BehaviorNode(
                id="reconcile_branches",
                kind=BehaviorKind.RECONCILE,
                label="Merge worker diffs and verify interface alignment",
            ),
            BehaviorNode(
                id="verify_integrated_static",
                kind=BehaviorKind.VERIFY_STATIC,
                label="Run full-repo static type and lint gates",
            ),
            BehaviorNode(
                id="verify_integrated_dynamic",
                kind=BehaviorKind.VERIFY_DYNAMIC,
                label="Run full-repo integration and property suite",
            ),
            BehaviorNode(
                id="certify_migration",
                kind=BehaviorKind.CERTIFY,
                label="Certify completed migration",
            ),
        ),
        edges=(
            BehaviorEdge(source="frame_migration", target="consult_migration_docs"),
            BehaviorEdge(source="consult_migration_docs", target="inspect_lockfile"),
            BehaviorEdge(source="inspect_lockfile", target="bind_migration_codes"),
            BehaviorEdge(source="bind_migration_codes", target="checkpoint_pre_upgrade"),
            BehaviorEdge(source="checkpoint_pre_upgrade", target="provision_new_deps"),
            BehaviorEdge(source="provision_new_deps", target="partition_callsites"),
            BehaviorEdge(source="partition_callsites", target="delegate_workers"),
            BehaviorEdge(source="delegate_workers", target="reconcile_branches"),
            BehaviorEdge(source="reconcile_branches", target="verify_integrated_static"),
            BehaviorEdge(source="verify_integrated_static", target="verify_integrated_dynamic"),
            BehaviorEdge(source="verify_integrated_dynamic", target="certify_migration"),
        ),
    )


def all_canonical_archetypes() -> tuple[BehaviorGraph, ...]:
    """Return all five canonical reference BehaviorGraphs."""
    return (
        build_readonly_audit_graph(),
        build_abductive_bugfix_graph(),
        build_greenfield_contract_first_graph(),
        build_ast_refactor_graph(),
        build_delegated_migration_graph(),
    )
