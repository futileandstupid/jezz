"""Track 17: Matched TypeScript, Rust, and Go benchmark tasks with held-out property oracles."""

from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.evals.polyglot_oracles import (
    ORACLE_GO_01_BPS_SPLIT,
    ORACLE_GO_02_STRICT_RECEIPT,
    ORACLE_RS_01_BPS_SPLIT,
    ORACLE_RS_02_ESCROW_FSM,
    ORACLE_TS_01_BPS_SPLIT,
    ORACLE_TS_02_ESCROW_VAULT,
)
from jezz.evals.schemas import BenchmarkCaseMetadata, BenchmarkTaskSpec
from jezz.guards import jezz_callable
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope


def _case_ts_01_bps_split() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement TypeScript module 'src/bps_split.ts' and 'tests/bps_split.test.ts' using "
        "erasable Node-native TypeScript types and 'node:test'. Export type FeeTier = 'retail' "
        "| 'plus' | 'prime' | 'sovereign' | 'internal' (35n, 20n, 10n, 3n, 0n bps), interface "
        "SplitAllocation { readonly feeCents: bigint; readonly netCents: bigint; readonly "
        "sharesCents: readonly bigint[] }, and function allocateBpsSplit(grossCents: bigint, "
        "tier: FeeTier, weights: readonly bigint[]): SplitAllocation. Reject grossCents < 0n, "
        "empty weights, or any weight <= 0n; compute exact bigint feeCents = (grossCents * bps) "
        "/ 10_000n, netCents = grossCents - feeCents, base shares (netCents * w) / sum(weights), "
        "and +1n remainder prefix so feeCents + sum(sharesCents) === grossCents."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK17-TS-BPS-SPLIT",
        title="TypeScript exact bigint five-tier basis-point fee split",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "For all grossCents >= 0n, FeeTier values, and positive weights, "
                    "feeCents + sum(sharesCents) === grossCents with zero IEEE-754 float loss."
                ),
                verification_symbol="test_bps_split_conserves_cents",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Negative grossCents, empty weights, or non-positive weights throw a "
                    "RangeError before division."
                ),
                verification_symbol="test_bps_split_rejects_invalid_inputs",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="ts_bps_split_core",
                owned_files=frozenset({"src/bps_split.ts", "tests/bps_split.test.ts"}),
                contract_symbols=(
                    "FeeTier",
                    "SplitAllocation",
                    "allocateBpsSplit",
                    "test_bps_split_conserves_cents",
                    "test_bps_split_rejects_invalid_inputs",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("ts_number_precision_loss", "ts_any_erasure", "elif_tier_ladder"),
        targeted_jbc_codes=("JBC-DBC-001", "JBC-DBC-002", "JBC-DBC-004", "JBC-COG-004"),
        scope_count=1,
        language=SupportedLanguage.TYPESCRIPT,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_ts_01_bps_split",
            package_name="bps_split_ts",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_TS_01_BPS_SPLIT,
            language=SupportedLanguage.TYPESCRIPT,
        ),
        meta,
    )


def _case_ts_02_escrow_vault() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement TypeScript module 'src/escrow.ts' and 'tests/escrow.test.ts'. Export "
        "type EscrowState = 'funded' | 'frozen' | 'closed', type ActionKind = 'release' | "
        "'refund' | 'freeze' | 'unfreeze', interface EscrowEvent { readonly eventId: string; "
        "readonly action: ActionKind; readonly amountCents: bigint }, interface EscrowLedger "
        "{ readonly principalCents: bigint; readonly heldCents: bigint; readonly releasedCents: "
        "bigint; readonly refundedCents: bigint; readonly state: EscrowState; readonly "
        "appliedEventIds: readonly string[] }, openEscrow(principalCents: bigint): EscrowLedger, "
        "and applyEscrowEvent(ledger: EscrowLedger, event: EscrowEvent): EscrowLedger enforcing "
        "duplicate eventId idempotency, principal conservation, and frozen/closed payout guards."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK17-TS-ESCROW",
        title="TypeScript immutable escrow ledger state machine with idempotent replay",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "Every reachable EscrowLedger satisfies heldCents + releasedCents + "
                    "refundedCents === principalCents and (state === 'closed') === "
                    "(heldCents === 0n)."
                ),
                verification_symbol="test_escrow_conserves_principal",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Duplicate eventIds return ledger unchanged, while payouts in 'frozen' or "
                    "'closed' state throw an Error."
                ),
                verification_symbol="test_escrow_idempotency_and_frozen_guard",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="ts_escrow_core",
                owned_files=frozenset({"src/escrow.ts", "tests/escrow.test.ts"}),
                contract_symbols=(
                    "EscrowState",
                    "ActionKind",
                    "EscrowEvent",
                    "EscrowLedger",
                    "openEscrow",
                    "applyEscrowEvent",
                    "test_escrow_conserves_principal",
                    "test_escrow_idempotency_and_frozen_guard",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("frozen_payout_leak", "unbounded_event_id", "tautological_expect"),
        targeted_jbc_codes=("JBC-DBC-001", "JBC-DBC-004", "JBC-VRF-001", "JBC-FLT-001"),
        scope_count=1,
        language=SupportedLanguage.TYPESCRIPT,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_ts_02_escrow_vault",
            package_name="escrow_ts",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_TS_02_ESCROW_VAULT,
            language=SupportedLanguage.TYPESCRIPT,
        ),
        meta,
    )


def _case_rs_01_bps_split() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement Rust crate 'bps_split_rs' in 'src/lib.rs' and integration tests in "
        "'tests/bps_split_test.rs'. Define pub enum FeeTier { Retail, Plus, Prime, Sovereign, "
        "Internal } (35, 20, 10, 3, 0 bps), pub struct SplitAllocation { pub fee_cents: i128, "
        "pub net_cents: i128, pub shares_cents: Vec<i128> }, pub enum SplitError, and pub fn "
        "allocate_bps_split(gross_cents: i128, tier: FeeTier, weights: &[i128]) -> "
        "Result<SplitAllocation, SplitError> without unwrap()/expect()/panic! in library code."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK17-RS-BPS-SPLIT",
        title="Rust panic-free i128 basis-point fee deduction and pro-rata split",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "For all non-negative gross_cents and positive weights, allocate_bps_split "
                    "returns Ok where fee_cents + shares_cents.iter().sum() == gross_cents."
                ),
                verification_symbol="test_bps_split_conserves_cents",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Negative gross_cents, empty weights, or non-positive weights return Err "
                    "without panicking."
                ),
                verification_symbol="test_bps_split_rejects_invalid_inputs",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="rs_bps_split_core",
                owned_files=frozenset({"src/lib.rs", "tests/bps_split_test.rs"}),
                contract_symbols=(
                    "FeeTier",
                    "SplitAllocation",
                    "SplitError",
                    "allocate_bps_split",
                    "test_bps_split_conserves_cents",
                    "test_bps_split_rejects_invalid_inputs",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("rust_unwrap_panic", "i64_overflow", "unguarded_slice_div"),
        targeted_jbc_codes=("JBC-FLT-006", "JBC-DBC-004", "JBC-DBC-005", "JBC-COG-004"),
        scope_count=1,
        language=SupportedLanguage.RUST,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_rs_01_bps_split",
            package_name="bps_split_rs",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_RS_01_BPS_SPLIT,
            language=SupportedLanguage.RUST,
        ),
        meta,
    )


def _case_rs_02_escrow_fsm() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement Rust crate 'escrow_rs' in 'src/lib.rs' and 'tests/escrow_test.rs'. Define "
        "pub enum EscrowState { Funded, Frozen, Closed }, pub enum ActionKind { Release, Refund, "
        "Freeze, Unfreeze }, pub struct EscrowEvent { pub event_id: String, pub action: "
        "ActionKind, pub amount_cents: i64 }, pub struct EscrowLedger { pub principal_cents: i64, "
        "pub held_cents: i64, pub released_cents: i64, pub refunded_cents: i64, pub state: "
        "EscrowState, pub applied_event_ids: Vec<String> }, pub enum EscrowError, pub fn "
        "open_escrow(principal_cents: i64) -> Result<EscrowLedger, EscrowError>, and pub fn "
        "apply_escrow_event(ledger: &EscrowLedger, event: &EscrowEvent) -> "
        "Result<EscrowLedger, EscrowError>."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK17-RS-ESCROW",
        title="Rust Result-typed escrow finite state machine with idempotent event replay",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "Every reachable EscrowLedger conserves held_cents + released_cents + "
                    "refunded_cents == principal_cents and (state == Closed) == (held_cents == 0)."
                ),
                verification_symbol="test_escrow_conserves_principal",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Duplicate event_id replays return Ok(ledger.clone()) unchanged while "
                    "Frozen/Closed payouts return Err."
                ),
                verification_symbol="test_escrow_idempotency_and_frozen_guard",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="rs_escrow_core",
                owned_files=frozenset({"src/lib.rs", "tests/escrow_test.rs"}),
                contract_symbols=(
                    "EscrowState",
                    "ActionKind",
                    "EscrowEvent",
                    "EscrowLedger",
                    "EscrowError",
                    "open_escrow",
                    "apply_escrow_event",
                    "test_escrow_conserves_principal",
                    "test_escrow_idempotency_and_frozen_guard",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("rust_expect_panic", "frozen_payout_leak", "unguarded_i64_param"),
        targeted_jbc_codes=("JBC-FLT-006", "JBC-DBC-004", "JBC-VRF-001", "JBC-COG-001"),
        scope_count=1,
        language=SupportedLanguage.RUST,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_rs_02_escrow_fsm",
            package_name="escrow_rs",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_RS_02_ESCROW_FSM,
            language=SupportedLanguage.RUST,
        ),
        meta,
    )


def _case_go_01_bps_split() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement Go package 'bpssplit' in 'bpssplit/allocator.go' and "
        "'bpssplit/allocator_test.go'. Define type FeeTier string with constants TierRetail="
        "'retail' (35 bps), TierPlus='plus' (20 bps), TierPrime='prime' (10 bps), TierSovereign="
        "'sovereign' (3 bps), TierInternal='internal' (0 bps); type SplitAllocation struct { "
        "FeeCents int64; NetCents int64; SharesCents []int64 }; and func AllocateBpsSplit("
        "grossCents int64, tier FeeTier, weights []int64) (SplitAllocation, error) returning an "
        "error on negative grossCents, unknown tier, empty weights, or weight <= 0."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK17-GO-BPS-SPLIT",
        title="Go exact int64 five-tier basis-point fee split with explicit error returns",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "For all valid grossCents, FeeTier constants, and positive weights, "
                    "FeeCents + sum(SharesCents) == grossCents."
                ),
                verification_symbol="TestBpsSplitConservesCents",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Negative grossCents, unknown FeeTier, empty weights, or non-positive "
                    "weights return a non-nil error."
                ),
                verification_symbol="TestBpsSplitRejectsInvalidInputs",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="go_bpssplit_core",
                owned_files=frozenset({"bpssplit/allocator.go", "bpssplit/allocator_test.go"}),
                contract_symbols=(
                    "FeeTier",
                    "SplitAllocation",
                    "AllocateBpsSplit",
                    "TestBpsSplitConservesCents",
                    "TestBpsSplitRejectsInvalidInputs",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("go_float64_cent_drift", "discarded_error_blank", "unguarded_int64"),
        targeted_jbc_codes=("JBC-DBC-004", "JBC-DBC-005", "JBC-FLT-001", "JBC-COG-004"),
        scope_count=1,
        language=SupportedLanguage.GO,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_go_01_bps_split",
            package_name="bpssplit",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_GO_01_BPS_SPLIT,
            language=SupportedLanguage.GO,
        ),
        meta,
    )


def _case_go_02_strict_receipt() -> tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata]:
    objective = (
        "Implement Go package 'settle' in 'settle/receipt.go' and 'settle/receipt_test.go'. "
        'Define type SettlementReceipt struct { TxID string `json:"tx_id"`; AccountID string '
        '`json:"account_id"`; AmountCents int64 `json:"amount_cents"`; FeeBps int64 '
        '`json:"fee_bps"` } with Validate() error (requiring non-empty TxID/AccountID len <= '
        "128, AmountCents > 0, 0 <= FeeBps <= 10_000), NetCents() int64 returning AmountCents - "
        "(AmountCents * FeeBps) / 10_000, and DecodeSettlement(raw []byte) (SettlementReceipt, "
        "error) using json.NewDecoder with DisallowUnknownFields(), rejecting trailing tokens."
    )
    mandate = EngineeringMandate(
        mandate_id="MND-TRACK17-GO-SETTLE",
        title="Go strict JSON settlement receipt decoder with DisallowUnknownFields",
        objective=objective,
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement=(
                    "DecodeSettlement rejects unknown JSON keys, trailing JSON values, empty "
                    "IDs, non-positive AmountCents, and FeeBps outside [0, 10_000]."
                ),
                verification_symbol="TestStrictSettlementRejectsUnknownFields",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement=(
                    "Valid settlement payloads round-trip cleanly with NetCents() == "
                    "AmountCents - (AmountCents * FeeBps) / 10_000."
                ),
                verification_symbol="TestValidSettlementNetCents",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="go_settle_core",
                owned_files=frozenset({"settle/receipt.go", "settle/receipt_test.go"}),
                contract_symbols=(
                    "SettlementReceipt",
                    "DecodeSettlement",
                    "TestStrictSettlementRejectsUnknownFields",
                    "TestValidSettlementNetCents",
                ),
            ),
        ),
    )
    meta = BenchmarkCaseMetadata(
        seeded_hazards=("go_json_unmarshal_unknown_keys", "missing_validate_method", "blank_err"),
        targeted_jbc_codes=("JBC-SEC-003", "JBC-DBC-004", "JBC-FLT-001", "JBC-FLT-002"),
        scope_count=1,
        language=SupportedLanguage.GO,
    )
    return (
        BenchmarkTaskSpec(
            case_id="case_go_02_strict_receipt",
            package_name="settle",
            prompt=objective,
            mandate=mandate,
            oracle_test_source=ORACLE_GO_02_STRICT_RECEIPT,
            language=SupportedLanguage.GO,
        ),
        meta,
    )


@jezz_callable
def build_non_python_benchmark_cases() -> tuple[
    tuple[BenchmarkTaskSpec, BenchmarkCaseMetadata],
    ...,
]:
    """Construct the 6 TypeScript, Rust, and Go Track 17 benchmark cases."""
    return (
        _case_ts_01_bps_split(),
        _case_ts_02_escrow_vault(),
        _case_rs_01_bps_split(),
        _case_rs_02_escrow_fsm(),
        _case_go_01_bps_split(),
        _case_go_02_strict_receipt(),
    )
