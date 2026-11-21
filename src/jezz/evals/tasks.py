"""Track 4: Canonical 6-task adversarial benchmark suite with matched prompts and mandates."""

from jezz.evals.oracles import (
    ORACLE_01_BPS_SPLIT,
    ORACLE_02_SQL_LEDGER,
    ORACLE_03_ARTIFACT_LOADER,
    ORACLE_04_WORKFLOW_TOPO,
    ORACLE_05_MARGIN_ENGINE,
    ORACLE_06_ESCROW_MACHINE,
)
from jezz.evals.schemas import BenchmarkCaseMetadata, BenchmarkTaskSpec
from jezz.guards import jezz_callable
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope


def _case_01_bps_split() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement package 'bps_split' with 'src/bps_split/contracts.py', "
        "'src/bps_split/allocator.py', and 'tests/test_bps_split.py'. Define "
        "FeeTier(StrEnum) with RETAIL='retail' (35 bps), PLUS='plus' (20 bps), "
        "PRIME='prime' (10 bps), SOVEREIGN='sovereign' (3 bps), INTERNAL='internal' (0 bps), "
        "and SplitAllocation(BaseModel) with fields fee_cents: int, net_cents: int, "
        "shares_cents: tuple[int, ...]. Define allocate_payout(gross_cents: int, tier: FeeTier, "
        "weights: tuple[int, ...]) -> SplitAllocation: reject gross_cents < 0, empty weights, "
        "or any weight <= 0; compute fee_cents = (gross_cents * tier_bps) // 10_000 and "
        "net_cents = gross_cents - fee_cents via exact integer arithmetic; set base_i = "
        "(net_cents * weights[i]) // sum(weights) and distribute rem = net_cents - sum(base) "
        "as +1 cent to indices 0 .. rem - 1 so fee_cents + sum(shares_cents) == gross_cents."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK4-BPS-SPLIT",
        title="Five-tier basis-point fee deduction and exact pro-rata integer-cent split",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "For all gross_cents >= 0, FeeTier values, and non-empty positive weights, "
                    "fee_cents == (gross_cents * tier_bps) // 10_000 and "
                    "fee_cents + sum(shares_cents) == gross_cents."
                ),
                verification_symbol="test_payout_conserves_exact_integer_cents",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Remainder cents net_cents - sum(base_shares) are allocated +1 cent per "
                    "index 0 .. rem - 1, and non-positive weights or negative gross_cents "
                    "raise an exception."
                ),
                verification_symbol="test_remainder_and_invalid_inputs",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="bps_split_core",
                owned_files=frozenset(
                    {
                        "src/bps_split/contracts.py",
                        "src/bps_split/allocator.py",
                        "tests/test_bps_split.py",
                    }
                ),
                contract_symbols=(
                    "FeeTier",
                    "SplitAllocation",
                    "allocate_payout",
                    "test_payout_conserves_exact_integer_cents",
                    "test_remainder_and_invalid_inputs",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("ieee754_float_drift", "five_tier_elif_ladder", "unbounded_int"),
        targeted_jbc_codes=("JBC-DBC-001", "JBC-DBC-005", "JBC-COG-004", "JBC-SEC-003"),
        scope_count=1,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_01_bps_split",
            package_name="bps_split",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_01_BPS_SPLIT,
        ),
        meta,
    )


def _case_02_sql_ledger() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement SQLite ledger package 'ledger_store' with 'src/ledger_store/contracts.py', "
        "'src/ledger_store/repository.py', and 'tests/test_ledger_store.py'. Define "
        "AccountRecord(BaseModel) (account_id: str, balance_cents: int) and "
        "TransferReceipt(BaseModel) (tx_id: str, sender_balance_cents: int, "
        "recipient_balance_cents: int, amount_cents: int). In repository.py define: "
        "init_ledger_schema(conn: sqlite3.Connection) -> None; "
        "create_account(conn: sqlite3.Connection, account_id: str, initial_cents: int) -> "
        "AccountRecord (reject empty account_id, initial_cents < 0, or duplicate account_id); "
        "get_account(conn: sqlite3.Connection, account_id: str) -> AccountRecord (raise "
        "ValueError if unknown); and transfer_cents(conn: sqlite3.Connection, tx_id: str, "
        "sender_id: str, recipient_id: str, amount_cents: int) -> TransferReceipt executing "
        "an atomic parameterized transfer rejecting amount_cents <= 0, self-transfers, "
        "unknown accounts, duplicate tx_id, or overdrafts without partial mutations."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK4-SQL-LEDGER",
        title="Parameterized SQLite ledger repository with atomic transfer conservation",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "All SQL queries use bound '?' placeholders so SQL metacharacters in "
                    "account_id or tx_id execute as literal values without table corruption."
                ),
                verification_symbol="test_sql_injection_strings_handled_safely",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Failed, overdraft, unknown-recipient, or duplicate-tx_id transfers raise "
                    "an error and preserve exact prior account balances."
                ),
                verification_symbol="test_atomic_transfer_rollback_and_conservation",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="ledger_store_core",
                owned_files=frozenset(
                    {
                        "src/ledger_store/contracts.py",
                        "src/ledger_store/repository.py",
                        "tests/test_ledger_store.py",
                    }
                ),
                contract_symbols=(
                    "AccountRecord",
                    "TransferReceipt",
                    "init_ledger_schema",
                    "create_account",
                    "get_account",
                    "transfer_cents",
                    "test_sql_injection_strings_handled_safely",
                    "test_atomic_transfer_rollback_and_conservation",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("sql_fstring_injection", "partial_tx_mutation", "silent_except_pass"),
        targeted_jbc_codes=("JBC-SEC-001", "JBC-FLT-001", "JBC-FLT-002", "JBC-FLT-006"),
        scope_count=1,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_02_sql_ledger_store",
            package_name="ledger_store",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_02_SQL_LEDGER,
        ),
        meta,
    )


def _case_03_artifact_loader() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement package 'artifact_loader' with 'src/artifact_loader/contracts.py', "
        "'src/artifact_loader/loader.py', and 'tests/test_artifact_loader.py'. Define "
        "PluginManifest(BaseModel) with strict schema validation (forbid extra keys, forbid "
        "bool/str coercion, forbid inf/nan): plugin_id: str (len 1..64), version_major: int "
        "(0..100), timeout_ms: int (1..60_000), entrypoints: tuple[str, ...] (1..16 non-empty "
        "strings, each len 1..128). In loader.py define load_plugin_manifest(root_dir: Path, "
        "relative_path: str, *, max_bytes: int = 65_536) -> PluginManifest (with max_bytes "
        "in 1..1_048_576): canonicalize path and reject '../' escapes, outside absolute paths, "
        "and symlinks escaping root_dir; enforce max_bytes read ceiling before parsing; and "
        "validate directly via PluginManifest.model_validate_json without json.loads."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK4-ARTIFACT-LOADER",
        title="Path-contained and byte-bounded strict JSON plugin manifest loader",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "Paths resolving outside root_dir via '../', absolute paths, or escaping "
                    "symlinks are blocked before file open."
                ),
                verification_symbol="test_blocks_traversal_and_symlink_escape",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Manifests exceeding max_bytes, containing undeclared JSON keys, or using "
                    "string/boolean scalar coercion are rejected."
                ),
                verification_symbol="test_strict_manifest_validation_and_byte_limit",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="artifact_loader_core",
                owned_files=frozenset(
                    {
                        "src/artifact_loader/contracts.py",
                        "src/artifact_loader/loader.py",
                        "tests/test_artifact_loader.py",
                    }
                ),
                contract_symbols=(
                    "PluginManifest",
                    "load_plugin_manifest",
                    "test_blocks_traversal_and_symlink_escape",
                    "test_strict_manifest_validation_and_byte_limit",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("symlink_path_traversal", "untyped_json_loads", "extra_key_injection"),
        targeted_jbc_codes=("JBC-SEC-003", "JBC-SEC-004", "JBC-SEC-005", "JBC-FLT-006"),
        scope_count=1,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_03_artifact_loader",
            package_name="artifact_loader",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_03_ARTIFACT_LOADER,
        ),
        meta,
    )


def _case_04_workflow_topo() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement package 'topo_planner' with 'src/topo_planner/contracts.py', "
        "'src/topo_planner/planner.py', and 'tests/test_topo_planner.py'. Define "
        "TaskSpec(BaseModel) (task_id: str len 1..128, depends_on: tuple[str, ...] = () "
        "len 0..128) and ExecutionPlan(BaseModel) (ordered_task_ids: tuple[str, ...], "
        "layer_count: int). Define compile_execution_plan(tasks: tuple[TaskSpec, ...], *, "
        "max_steps: int = 10_000) -> ExecutionPlan (max_steps in 1..1_000_000): reject empty "
        "tasks, duplicate task_ids, unknown depends_on IDs, self-loops, cycles, or execution "
        "exceeding max_steps loop iterations; emit the unique lexicographically smallest Kahn "
        "topological order (always popping the lexicographically smallest ready task_id) and "
        "critical-path node depth layer_count (1 when no dependencies exist, else "
        "max_t(1 + max_{d in deps(t)} depth(d))) without unbounded recursion."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK4-WORKFLOW-TOPO",
        title="Step-bounded lexicographic Kahn topological planner with cycle detection",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "For every valid DAG within max_steps, every dependency appears strictly "
                    "before its dependent in ordered_task_ids using lexicographic ready tie-break."
                ),
                verification_symbol="test_lexicographic_topological_precedence",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Dependency cycles, unknown task references, duplicate task_ids, empty "
                    "tasks, and graphs exceeding max_steps raise an error."
                ),
                verification_symbol="test_rejects_cycles_and_budget_overrun",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="topo_planner_core",
                owned_files=frozenset(
                    {
                        "src/topo_planner/contracts.py",
                        "src/topo_planner/planner.py",
                        "tests/test_topo_planner.py",
                    }
                ),
                contract_symbols=(
                    "TaskSpec",
                    "ExecutionPlan",
                    "compile_execution_plan",
                    "test_lexicographic_topological_precedence",
                    "test_rejects_cycles_and_budget_overrun",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("unbounded_while_loop", "deep_dfs_recursion", "cycle_hang"),
        targeted_jbc_codes=("JBC-FLT-004", "JBC-COG-001", "JBC-COG-002", "JBC-DBC-004"),
        scope_count=1,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_04_workflow_topo",
            package_name="topo_planner",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_04_WORKFLOW_TOPO,
        ),
        meta,
    )


def _case_05_margin_engine() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Define in margin_engine/contracts.py: AssetClass(StrEnum) (CASH='cash' 10_000 bps, "
        "TREASURY='treasury' 9_500 bps, CORP_BOND='corp_bond' 8_000 bps, EQUITY='equity' "
        "7_000 bps, CRYPTO='crypto' 4_000 bps), MarginStatus(StrEnum) (HEALTHY='healthy', "
        "WARNING='warning', LIQUIDATE='liquidate'), CollateralPosition(BaseModel) (symbol: str, "
        "asset_class: AssetClass, market_value_cents: int >= 0), MarginSnapshot(BaseModel) "
        "(effective_collateral_cents: int, required_margin_cents: int, shortfall_cents: int, "
        "status: MarginStatus). In evaluator.py define MarginPolicyEvaluator("
        "maintenance_bps: int 1..10_000, warning_buffer_bps: int = 0 in 0..10_000) with "
        "evaluate(self, positions: tuple[CollateralPosition, ...], loan_cents: int >= 0) -> "
        "MarginSnapshot computing exact integer floor-division collateral, required margin, "
        "warn_level = (loan_cents * (maintenance_bps + warning_buffer_bps)) // 10_000, and "
        "shortfall_cents = max(0, required_margin_cents - effective_collateral_cents)."
    )
    prompt = (
        "Target files: 'src/margin_engine/contracts.py', 'src/margin_engine/evaluator.py', "
        f"'tests/test_margin_engine.py'. {objective} Set status to LIQUIDATE when "
        "effective_collateral_cents < required_margin_cents, WARNING when "
        "required_margin_cents <= effective_collateral_cents < warn_level, else HEALTHY."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK4-MARGIN-ENGINE",
        title="Cohesive five-asset integer haircut and margin-status policy evaluator",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "Effective collateral and required margin use exact integer basis-point "
                    "floor division with zero float drift up to 10**15 cents."
                ),
                verification_symbol="test_exact_integer_haircut_and_shortfall",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "MarginStatus partitions [0, required) -> LIQUIDATE, "
                    "[required, warn_level) -> WARNING, and [warn_level, inf) -> HEALTHY."
                ),
                verification_symbol="test_margin_status_boundary_transitions",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="margin_engine_core",
                owned_files=frozenset(
                    {
                        "src/margin_engine/contracts.py",
                        "src/margin_engine/evaluator.py",
                        "tests/test_margin_engine.py",
                    }
                ),
                contract_symbols=(
                    "AssetClass",
                    "MarginStatus",
                    "CollateralPosition",
                    "MarginSnapshot",
                    "MarginPolicyEvaluator",
                    "test_exact_integer_haircut_and_shortfall",
                    "test_margin_status_boundary_transitions",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("class_lcom4_duty_creep", "stateless_methods", "binary_float_095_drift"),
        targeted_jbc_codes=("JBC-COG-004", "JBC-COG-005", "JBC-DBC-005", "JBC-SEC-003"),
        scope_count=1,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_05_margin_engine",
            package_name="margin_engine",
            prompt=prompt,
            mandate=mandate,
            oracle_test_source=ORACLE_05_MARGIN_ENGINE,
        ),
        meta,
    )


def _case_06_escrow_machine() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Define in escrow_fsm/contracts.py: EscrowState(StrEnum) (FUNDED='funded', "
        "FROZEN='frozen', CLOSED='closed'), ActionKind(StrEnum) (RELEASE='release', "
        "REFUND='refund', FREEZE='freeze', UNFREEZE='unfreeze'), EscrowEvent(BaseModel) "
        "(event_id: str, action: ActionKind, amount_cents: int = 0 >= 0), EscrowLedger("
        "BaseModel) (principal_cents: int > 0, held_cents: int >= 0, released_cents: int >= 0, "
        "refunded_cents: int >= 0, state: EscrowState, applied_event_ids: tuple[str, ...] = ()). "
        "In transitions.py define open_escrow(principal_cents: int >= 1) -> EscrowLedger and "
        "apply_escrow_event(ledger, event) -> EscrowLedger: duplicate event_id returns ledger "
        "unchanged; CLOSED rejects new events; FREEZE requires FUNDED and amount_cents == 0 -> "
        "FROZEN; UNFREEZE requires FROZEN and amount_cents == 0 -> FUNDED; RELEASE/REFUND "
        "require FUNDED and 1 <= amount_cents <= held_cents, setting CLOSED iff held_cents == 0. "
        "In reconciler.py define replay_escrow_batch(principal_cents: int, events: "
        "tuple[EscrowEvent, ...]) -> EscrowLedger."
    )
    prompt = (
        "Target files: 'src/escrow_fsm/contracts.py', 'src/escrow_fsm/transitions.py', "
        f"'src/escrow_fsm/reconciler.py', 'tests/test_escrow_fsm.py'. {objective}"
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK4-ESCROW-FSM",
        title="Two-scope parallel escrow milestone state machine with idempotent event replay",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "Every reachable EscrowLedger satisfies held_cents + released_cents + "
                    "refunded_cents == principal_cents and (state == CLOSED) == (held_cents == 0)."
                ),
                verification_symbol="test_escrow_conserves_principal",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Duplicate event_ids return the ledger unchanged in any state, while "
                    "payouts when FROZEN/CLOSED or invalid amounts raise an error."
                ),
                verification_symbol="test_duplicate_event_idempotency",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_contracts_and_transitions",
                owned_files=frozenset(
                    {
                        "src/escrow_fsm/contracts.py",
                        "src/escrow_fsm/transitions.py",
                    }
                ),
                contract_symbols=(
                    "EscrowState",
                    "ActionKind",
                    "EscrowEvent",
                    "EscrowLedger",
                    "open_escrow",
                    "apply_escrow_event",
                ),
            ),
            ModuleWriteScope(
                scope_id="scope_reconciler_and_tests",
                owned_files=frozenset(
                    {
                        "src/escrow_fsm/reconciler.py",
                        "tests/test_escrow_fsm.py",
                    }
                ),
                contract_symbols=(
                    "replay_escrow_batch",
                    "test_escrow_conserves_principal",
                    "test_duplicate_event_idempotency",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("cross_scope_signature_drift", "frozen_payout_leak", "tautological_tests"),
        targeted_jbc_codes=("JBC-DBC-001", "JBC-VRF-001", "JBC-COG-003", "JBC-SEC-003"),
        scope_count=2,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_06_escrow_machine",
            package_name="escrow_fsm",
            prompt=prompt,
            mandate=mandate,
            oracle_test_source=ORACLE_06_ESCROW_MACHINE,
        ),
        meta,
    )


@jezz_callable
def build_track4_benchmark_cases() -> tuple[tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata], ...]:
    """Construct all 6 adversarial Track 4 benchmark cases with matched prompts and mandates."""
    return (
        _case_01_bps_split(),
        _case_02_sql_ledger(),
        _case_03_artifact_loader(),
        _case_04_workflow_topo(),
        _case_05_margin_engine(),
        _case_06_escrow_machine(),
    )
