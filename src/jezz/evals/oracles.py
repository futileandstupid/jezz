"""Track 4 & 17: Held-out adversarial property/fuzz oracle test suites across 4 languages."""

from typing import Final

from jezz.evals.polyglot_oracles import (
    ORACLE_GO_01_BPS_SPLIT,
    ORACLE_GO_02_STRICT_RECEIPT,
    ORACLE_RS_01_BPS_SPLIT,
    ORACLE_RS_02_ESCROW_FSM,
    ORACLE_TS_01_BPS_SPLIT,
    ORACLE_TS_02_ESCROW_VAULT,
)

ORACLE_01_BPS_SPLIT: Final[str] = '''\
"""Held-out adversarial Hypothesis oracle for case_01_bps_split."""

from collections.abc import Callable

import icontract
from hypothesis import given, settings
from hypothesis import strategies as st

from bps_split.allocator import allocate_payout
from bps_split.contracts import FeeTier
from jezz.guards import BudgetExceededError

_REJECTED = (ValueError, TypeError, LookupError, icontract.ViolationError, BudgetExceededError)
_EXPECTED_BPS = {
    FeeTier.RETAIL: 35,
    FeeTier.PLUS: 20,
    FeeTier.PRIME: 10,
    FeeTier.SOVEREIGN: 3,
    FeeTier.INTERNAL: 0,
}


def _expect_rejected(fn: Callable[[], object]) -> None:
    try:
        fn()
    except _REJECTED:
        return
    msg = "Expected invalid input to raise a validation or contract error."
    raise AssertionError(msg)


def test_oracle_ieee754_large_cent_precision() -> None:
    """Catch float mantissa precision loss above 2**53 cents."""
    for gross in (9_007_199_254_740_993, 99_999_999_999_999_989):
        for tier, bps in _EXPECTED_BPS.items():
            res = allocate_payout(gross, tier, (3, 7, 11))
            expected_fee = (gross * bps) // 10_000
            expected_net = gross - expected_fee
            assert res.fee_cents == expected_fee
            assert res.net_cents == expected_net
            shares = tuple(res.shares_cents)
            assert sum(shares) == expected_net
            assert res.fee_cents + sum(shares) == gross


@settings(max_examples=80, deadline=None)
@given(
    gross=st.integers(min_value=0, max_value=10**15),
    tier=st.sampled_from(list(FeeTier)),
    weights=st.lists(st.integers(min_value=1, max_value=10_000), min_size=1, max_size=8).map(tuple),
)
def test_oracle_conservation_and_remainder_hypothesis(
    gross: int,
    tier: FeeTier,
    weights: tuple[int, ...],
) -> None:
    """Verify universal integer cent conservation and exact first-bucket remainder distribution."""
    res = allocate_payout(gross, tier, weights)
    expected_fee = (gross * _EXPECTED_BPS[tier]) // 10_000
    expected_net = gross - expected_fee
    total_w = sum(weights)
    bases = [(expected_net * w) // total_w for w in weights]
    rem = expected_net - sum(bases)
    expected_shares = tuple(b + (1 if idx < rem else 0) for idx, b in enumerate(bases))

    assert res.fee_cents == expected_fee
    assert res.net_cents == expected_net
    assert tuple(res.shares_cents) == expected_shares
    assert res.fee_cents + sum(res.shares_cents) == gross


def test_oracle_rejects_invalid_inputs() -> None:
    """Reject negative gross cents, empty weights, and non-positive weights."""
    _expect_rejected(lambda: allocate_payout(-1, FeeTier.RETAIL, (1, 1)))
    _expect_rejected(lambda: allocate_payout(1_000, FeeTier.RETAIL, ()))
    _expect_rejected(lambda: allocate_payout(1_000, FeeTier.PLUS, (5, 0)))
    _expect_rejected(lambda: allocate_payout(1_000, FeeTier.PRIME, (5, -2)))
'''

ORACLE_02_SQL_LEDGER: Final[str] = '''\
"""Held-out adversarial Hypothesis + SQL-injection oracle for case_02_sql_ledger_store."""

import sqlite3
from collections.abc import Callable

import icontract
from hypothesis import given, settings
from hypothesis import strategies as st

from jezz.guards import BudgetExceededError
from ledger_store.repository import (
    create_account,
    get_account,
    init_ledger_schema,
    transfer_cents,
)

_REJECTED = (ValueError, TypeError, LookupError, icontract.ViolationError, BudgetExceededError)


def _expect_rejected(fn: Callable[[], object]) -> None:
    try:
        fn()
    except _REJECTED:
        return
    msg = "Expected invalid ledger operation to raise an error."
    raise AssertionError(msg)


def test_oracle_sql_injection_immunity() -> None:
    """Adversarial SQL metacharacters in account IDs and tx IDs must never corrupt tables."""
    conn = sqlite3.connect(":memory:")
    init_ledger_schema(conn)
    inj_sender = "alice' OR '1'='1'; DROP TABLE accounts; --"
    inj_recipient = "bob'--; SELECT 1"
    create_account(conn, inj_sender, 5_000)
    create_account(conn, inj_recipient, 1_000)
    create_account(conn, "bystander", 9_999)

    receipt = transfer_cents(conn, "tx' OR '1'='1'--", inj_sender, inj_recipient, 1_500)
    assert receipt.sender_balance_cents == 3_500
    assert receipt.recipient_balance_cents == 2_500
    assert get_account(conn, "bystander").balance_cents == 9_999
    conn.close()


def test_oracle_atomicity_on_failed_and_duplicate_transfers() -> None:
    """Rejected transfers and duplicate tx_ids must leave all balances untouched."""
    conn = sqlite3.connect(":memory:")
    init_ledger_schema(conn)
    create_account(conn, "a1", 1_000)
    create_account(conn, "a2", 500)

    transfer_cents(conn, "tx-001", "a1", "a2", 300)
    assert get_account(conn, "a1").balance_cents == 700
    assert get_account(conn, "a2").balance_cents == 800

    _expect_rejected(lambda: transfer_cents(conn, "tx-001", "a1", "a2", 100))
    _expect_rejected(lambda: transfer_cents(conn, "tx-002", "a1", "ghost_account", 200))
    _expect_rejected(lambda: transfer_cents(conn, "tx-003", "a1", "a2", 701))
    _expect_rejected(lambda: transfer_cents(conn, "tx-004", "a1", "a1", 100))
    _expect_rejected(lambda: transfer_cents(conn, "tx-005", "a1", "a2", 0))
    _expect_rejected(lambda: transfer_cents(conn, "tx-006", "a1", "a2", -50))

    assert get_account(conn, "a1").balance_cents == 700
    assert get_account(conn, "a2").balance_cents == 800
    conn.close()


@settings(max_examples=50, deadline=None)
@given(
    ops=st.lists(
        st.tuples(
            st.sampled_from(["u0", "u1", "u2", "u3"]),
            st.sampled_from(["u0", "u1", "u2", "u3", "missing"]),
            st.integers(min_value=-200, max_value=2_000),
        ),
        min_size=1,
        max_size=20,
    )
)
def test_oracle_hypothesis_global_balance_conservation(
    ops: list[tuple[str, str, int]],
) -> None:
    """Global sum of balances across all accounts must remain invariant across arbitrary ops."""
    conn = sqlite3.connect(":memory:")
    init_ledger_schema(conn)
    ids = ("u0", "u1", "u2", "u3")
    for uid in ids:
        create_account(conn, uid, 1_000)

    for idx, (src, dst, amt) in enumerate(ops):
        try:
            transfer_cents(conn, f"tx_{idx}", src, dst, amt)
        except _REJECTED:
            pass
        total = sum(get_account(conn, uid).balance_cents for uid in ids)
        assert total == 4_000
        assert all(get_account(conn, uid).balance_cents >= 0 for uid in ids)
    conn.close()
'''

ORACLE_03_ARTIFACT_LOADER: Final[str] = '''\
"""Held-out adversarial path-containment and strict-JSON oracle for case_03_artifact_loader."""

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import icontract
from hypothesis import given, settings
from hypothesis import strategies as st

from artifact_loader.loader import load_plugin_manifest
from jezz.guards import BudgetExceededError

_REJECTED = (ValueError, TypeError, LookupError, icontract.ViolationError, BudgetExceededError)


def _expect_rejected(fn: Callable[[], object]) -> None:
    try:
        fn()
    except _REJECTED:
        return
    msg = "Expected untrusted path or malformed manifest to be rejected."
    raise AssertionError(msg)


def test_oracle_blocks_dotdot_absolute_and_symlink_escapes(tmp_path: Path) -> None:
    """Block ../ traversal, absolute outside paths, and symlinks pointing outside root_dir."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside_secret.json"
    outside.write_text(
        json.dumps(
            {
                "plugin_id": "escaped",
                "version_major": 1,
                "timeout_ms": 100,
                "entrypoints": ["run"],
            }
        ),
        encoding="utf-8",
    )
    escape_link = sandbox / "linked_manifest.json"
    if not escape_link.exists():
        escape_link.symlink_to(outside)

    _expect_rejected(lambda: load_plugin_manifest(sandbox, "../outside_secret.json"))
    _expect_rejected(lambda: load_plugin_manifest(sandbox, str(outside)))
    _expect_rejected(lambda: load_plugin_manifest(sandbox, "linked_manifest.json"))


def test_oracle_rejects_extra_keys_coercion_and_oversize(tmp_path: Path) -> None:
    """Reject undeclared fields, bool/str coercion, empty entrypoints, and >max_bytes files."""
    sandbox = tmp_path / "strict_box"
    sandbox.mkdir(parents=True, exist_ok=True)

    bad_payloads = [
        {
            "plugin_id": "p1",
            "version_major": 1,
            "timeout_ms": 100,
            "entrypoints": ["main"],
            "admin_backdoor": True,
        },
        {
            "plugin_id": "p2",
            "version_major": 1,
            "timeout_ms": "100",
            "entrypoints": ["main"],
        },
        {
            "plugin_id": "p3",
            "version_major": True,
            "timeout_ms": 100,
            "entrypoints": ["main"],
        },
        {
            "plugin_id": "p4",
            "version_major": 1,
            "timeout_ms": 100,
            "entrypoints": [],
        },
        {
            "plugin_id": "",
            "version_major": 1,
            "timeout_ms": 100,
            "entrypoints": ["main"],
        },
    ]
    for idx, payload in enumerate(bad_payloads):
        rel = f"bad_{idx}.json"
        (sandbox / rel).write_text(json.dumps(payload), encoding="utf-8")
        _expect_rejected(lambda rel_file=rel: load_plugin_manifest(sandbox, rel_file))

    big_file = sandbox / "huge.json"
    big_file.write_text(
        json.dumps(
            {
                "plugin_id": "big",
                "version_major": 1,
                "timeout_ms": 500,
                "entrypoints": ["a", "b", "c"],
            }
        )
        + (" " * 512),
        encoding="utf-8",
    )
    _expect_rejected(lambda: load_plugin_manifest(sandbox, "huge.json", max_bytes=64))


@settings(max_examples=40, deadline=None)
@given(
    plugin_id=st.from_regex(r"^[a-z][a-z0-9_]{0,15}$", fullmatch=True),
    version_major=st.integers(min_value=0, max_value=100),
    timeout_ms=st.integers(min_value=1, max_value=60_000),
    entrypoints=st.lists(
        st.from_regex(r"^[a-z][a-z0-9_]{0,12}$", fullmatch=True),
        min_size=1,
        max_size=6,
    ).map(tuple),
)
def test_oracle_hypothesis_valid_manifests_roundtrip(
    plugin_id: str,
    version_major: int,
    timeout_ms: int,
    entrypoints: tuple[str, ...],
) -> None:
    """Valid manifests inside root_dir parse with exact field fidelity."""
    with tempfile.TemporaryDirectory() as raw_tmp:
        sandbox = Path(raw_tmp) / "valid_box"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "manifest.json").write_text(
            json.dumps(
                {
                    "plugin_id": plugin_id,
                    "version_major": version_major,
                    "timeout_ms": timeout_ms,
                    "entrypoints": list(entrypoints),
                }
            ),
            encoding="utf-8",
        )
        loaded = load_plugin_manifest(sandbox, "manifest.json")
    assert loaded.plugin_id == plugin_id
    assert loaded.version_major == version_major
    assert loaded.timeout_ms == timeout_ms
    assert tuple(loaded.entrypoints) == entrypoints
'''

ORACLE_04_WORKFLOW_TOPO: Final[str] = '''\
"""Held-out adversarial cycle, step-budget, and DAG-order oracle for case_04_workflow_topo."""

import heapq
from collections.abc import Callable

import icontract
from hypothesis import given, settings
from hypothesis import strategies as st

from jezz.guards import BudgetExceededError
from topo_planner.contracts import TaskSpec
from topo_planner.planner import compile_execution_plan

_REJECTED = (ValueError, TypeError, LookupError, icontract.ViolationError, BudgetExceededError)


def _expect_rejected(fn: Callable[[], object]) -> None:
    try:
        fn()
    except _REJECTED:
        return
    msg = "Expected invalid or cyclic task graph to raise an error."
    raise AssertionError(msg)


def test_oracle_rejects_cycles_missing_deps_empty_and_duplicates() -> None:
    """Cycles, self-loops, unknown dependencies, duplicates, and empty inputs must fail."""
    _expect_rejected(lambda: compile_execution_plan(()))
    _expect_rejected(lambda: compile_execution_plan((TaskSpec(task_id="a", depends_on=("a",)),)))
    _expect_rejected(
        lambda: compile_execution_plan(
            (
                TaskSpec(task_id="a", depends_on=("c",)),
                TaskSpec(task_id="b", depends_on=("a",)),
                TaskSpec(task_id="c", depends_on=("b",)),
            )
        )
    )
    _expect_rejected(
        lambda: compile_execution_plan((TaskSpec(task_id="a", depends_on=("nonexistent",)),))
    )
    _expect_rejected(
        lambda: compile_execution_plan(
            (
                TaskSpec(task_id="dup", depends_on=()),
                TaskSpec(task_id="dup", depends_on=()),
            )
        )
    )


def test_oracle_enforces_step_budget_and_handles_deep_chain() -> None:
    """Enforce max_steps ceiling on tight budgets and avoid RecursionError on deep chains."""
    chain_300 = tuple(
        TaskSpec(
            task_id=f"n_{idx:04d}",
            depends_on=() if idx == 0 else (f"n_{idx - 1:04d}",),
        )
        for idx in range(300)
    )
    _expect_rejected(lambda: compile_execution_plan(chain_300, max_steps=25))

    plan = compile_execution_plan(chain_300, max_steps=20_000)
    assert tuple(plan.ordered_task_ids) == tuple(f"n_{idx:04d}" for idx in range(300))
    assert plan.layer_count == 300


def _reference_lex_kahn(specs: tuple[TaskSpec, ...]) -> tuple[tuple[str, ...], int]:
    deps_map = {s.task_id: set(s.depends_on) for s in specs}
    rev_map: dict[str, list[str]] = {s.task_id: [] for s in specs}
    indegree = {s.task_id: len(deps_map[s.task_id]) for s in specs}
    for s in specs:
        for d in deps_map[s.task_id]:
            rev_map[d].append(s.task_id)
    heap = [tid for tid, deg in indegree.items() if deg == 0]
    heapq.heapify(heap)
    order: list[str] = []
    depth: dict[str, int] = {}
    while heap:
        curr = heapq.heappop(heap)
        order.append(curr)
        depth[curr] = 1 + max((depth[d] for d in deps_map[curr]), default=0)
        for child in rev_map[curr]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(heap, child)
    return tuple(order), max(depth.values())


@settings(max_examples=60, deadline=None)
@given(
    node_count=st.integers(min_value=1, max_value=18),
    data=st.data(),
)
def test_oracle_hypothesis_dag_precedence_and_lex_tiebreak(
    node_count: int,
    data: st.DataObject,
) -> None:
    """Verify exact lexicographic Kahn ordering and critical-path layer_count over random DAGs."""
    perm = data.draw(st.permutations([f"task_{i:02d}" for i in range(node_count)]))
    specs_list: list[TaskSpec] = []
    for idx, tid in enumerate(perm):
        prior = perm[:idx]
        chosen_deps = (
            tuple(
                data.draw(
                    st.lists(st.sampled_from(prior), max_size=min(4, len(prior)), unique=True)
                )
            )
            if prior
            else ()
        )
        specs_list.append(TaskSpec(task_id=tid, depends_on=chosen_deps))
    shuffled = tuple(data.draw(st.permutations(specs_list)))
    expected_order, expected_layers = _reference_lex_kahn(shuffled)

    plan = compile_execution_plan(shuffled, max_steps=10_000)
    assert tuple(plan.ordered_task_ids) == expected_order
    assert plan.layer_count == expected_layers
'''

ORACLE_05_MARGIN_ENGINE: Final[str] = '''\
"""Held-out adversarial precision & boundary oracle for case_05_margin_engine."""

from collections.abc import Callable

import icontract
from hypothesis import given, settings
from hypothesis import strategies as st

from jezz.guards import BudgetExceededError
from margin_engine.contracts import AssetClass, CollateralPosition, MarginStatus
from margin_engine.evaluator import MarginPolicyEvaluator

_REJECTED = (ValueError, TypeError, LookupError, icontract.ViolationError, BudgetExceededError)
_WEIGHT_BPS = {
    AssetClass.CASH: 10_000,
    AssetClass.TREASURY: 9_500,
    AssetClass.CORP_BOND: 8_000,
    AssetClass.EQUITY: 7_000,
    AssetClass.CRYPTO: 4_000,
}


def _expect_rejected(fn: Callable[[], object]) -> None:
    try:
        fn()
    except _REJECTED:
        return
    msg = "Expected invalid margin parameter to raise an error."
    raise AssertionError(msg)


def test_oracle_large_treasury_and_crypto_exact_integer_math() -> None:
    """Catch binary float representation drift on 0.95 and 0.40 above 2**53 cents."""
    ev = MarginPolicyEvaluator(maintenance_bps=2_500, warning_buffer_bps=500)
    val = 9_007_199_254_740_997
    positions = (
        CollateralPosition(symbol="UST", asset_class=AssetClass.TREASURY, market_value_cents=val),
        CollateralPosition(symbol="BTC", asset_class=AssetClass.CRYPTO, market_value_cents=val),
    )
    snap = ev.evaluate(positions, loan_cents=val)
    expected_eff = ((val * 9_500) // 10_000) + ((val * 4_000) // 10_000)
    expected_req = (val * 2_500) // 10_000
    assert snap.effective_collateral_cents == expected_eff
    assert snap.required_margin_cents == expected_req
    assert snap.shortfall_cents == 0
    assert snap.status == MarginStatus.HEALTHY


def test_oracle_exact_threshold_boundaries_and_invalid_inputs() -> None:
    """Verify exact 1-cent status transitions across LIQUIDATE, WARNING, and HEALTHY."""
    ev = MarginPolicyEvaluator(maintenance_bps=5_000, warning_buffer_bps=1_000)
    p_liq = (
        CollateralPosition(symbol="USD", asset_class=AssetClass.CASH, market_value_cents=4_999),
    )
    s_liq = ev.evaluate(p_liq, loan_cents=10_000)
    assert s_liq.status == MarginStatus.LIQUIDATE
    assert s_liq.shortfall_cents == 1

    p_warn_lo = (
        CollateralPosition(symbol="USD", asset_class=AssetClass.CASH, market_value_cents=5_000),
    )
    s_warn_lo = ev.evaluate(p_warn_lo, loan_cents=10_000)
    assert s_warn_lo.status == MarginStatus.WARNING
    assert s_warn_lo.shortfall_cents == 0

    p_warn_hi = (
        CollateralPosition(symbol="USD", asset_class=AssetClass.CASH, market_value_cents=5_999),
    )
    assert ev.evaluate(p_warn_hi, loan_cents=10_000).status == MarginStatus.WARNING

    p_ok = (
        CollateralPosition(symbol="USD", asset_class=AssetClass.CASH, market_value_cents=6_000),
    )
    assert ev.evaluate(p_ok, loan_cents=10_000).status == MarginStatus.HEALTHY

    _expect_rejected(lambda: MarginPolicyEvaluator(maintenance_bps=0, warning_buffer_bps=100))
    _expect_rejected(lambda: MarginPolicyEvaluator(maintenance_bps=5_000, warning_buffer_bps=-1))
    _expect_rejected(lambda: ev.evaluate(p_ok, loan_cents=-10))


@settings(max_examples=70, deadline=None)
@given(
    maint_bps=st.integers(min_value=1, max_value=10_000),
    warn_bps=st.integers(min_value=0, max_value=10_000),
    loan=st.integers(min_value=0, max_value=10**14),
    items=st.lists(
        st.tuples(
            st.sampled_from(list(AssetClass)),
            st.integers(min_value=0, max_value=10**13),
        ),
        max_size=8,
    ),
)
def test_oracle_hypothesis_margin_invariants(
    maint_bps: int,
    warn_bps: int,
    loan: int,
    items: list[tuple[AssetClass, int]],
) -> None:
    """Verify exact haircut summation, shortfall formula, and status partition."""
    ev = MarginPolicyEvaluator(maintenance_bps=maint_bps, warning_buffer_bps=warn_bps)
    positions = tuple(
        CollateralPosition(symbol=f"P{i}", asset_class=ac, market_value_cents=mv)
        for i, (ac, mv) in enumerate(items)
    )
    snap = ev.evaluate(positions, loan_cents=loan)
    exp_eff = sum((mv * _WEIGHT_BPS[ac]) // 10_000 for ac, mv in items)
    exp_req = (loan * maint_bps) // 10_000
    exp_warn = (loan * (maint_bps + warn_bps)) // 10_000
    exp_shortfall = max(0, exp_req - exp_eff)

    assert snap.effective_collateral_cents == exp_eff
    assert snap.required_margin_cents == exp_req
    assert snap.shortfall_cents == exp_shortfall
    if exp_eff < exp_req:
        assert snap.status == MarginStatus.LIQUIDATE
    elif exp_eff < exp_warn:
        assert snap.status == MarginStatus.WARNING
    else:
        assert snap.status == MarginStatus.HEALTHY
'''

ORACLE_06_ESCROW_MACHINE: Final[str] = '''\
"""Held-out adversarial state-machine & conservation oracle for case_06_escrow_machine."""

from collections.abc import Callable

import icontract
from hypothesis import given, settings
from hypothesis import strategies as st

from escrow_fsm.contracts import ActionKind, EscrowEvent, EscrowState
from escrow_fsm.reconciler import replay_escrow_batch
from escrow_fsm.transitions import apply_escrow_event, open_escrow
from jezz.guards import BudgetExceededError

_REJECTED = (ValueError, TypeError, LookupError, icontract.ViolationError, BudgetExceededError)


def _expect_rejected(fn: Callable[[], object]) -> None:
    try:
        fn()
    except _REJECTED:
        return
    msg = "Expected illegal escrow transition or invalid amount to raise an error."
    raise AssertionError(msg)


def test_oracle_idempotency_across_funded_frozen_and_closed() -> None:
    """Duplicate event_id must return unchanged ledger even in FROZEN or CLOSED states."""
    e_rel_1 = EscrowEvent(event_id="ev-1", action=ActionKind.RELEASE, amount_cents=600)
    e_frz = EscrowEvent(event_id="ev-2", action=ActionKind.FREEZE, amount_cents=0)
    e_unfrz = EscrowEvent(event_id="ev-3", action=ActionKind.UNFREEZE, amount_cents=0)
    e_rel_2 = EscrowEvent(event_id="ev-4", action=ActionKind.REFUND, amount_cents=400)

    led = replay_escrow_batch(1_000, (e_rel_1, e_rel_1, e_frz, e_rel_1, e_unfrz, e_rel_2, e_rel_2))
    assert led.state == EscrowState.CLOSED
    assert led.held_cents == 0
    assert led.released_cents == 600
    assert led.refunded_cents == 400
    assert led.held_cents + led.released_cents + led.refunded_cents == led.principal_cents

    _expect_rejected(
        lambda: apply_escrow_event(
            led,
            EscrowEvent(event_id="ev-new", action=ActionKind.FREEZE, amount_cents=0),
        )
    )


def test_oracle_rejects_illegal_transitions_and_bad_amounts() -> None:
    """Reject payouts while FROZEN, nonzero FREEZE/UNFREEZE amounts, and zero/overdraw payouts."""
    led0 = open_escrow(500)
    _expect_rejected(lambda: open_escrow(0))
    _expect_rejected(
        lambda: apply_escrow_event(
            led0,
            EscrowEvent(event_id="bad-1", action=ActionKind.UNFREEZE, amount_cents=0),
        )
    )
    _expect_rejected(
        lambda: apply_escrow_event(
            led0,
            EscrowEvent(event_id="bad-2", action=ActionKind.FREEZE, amount_cents=10),
        )
    )
    _expect_rejected(
        lambda: apply_escrow_event(
            led0,
            EscrowEvent(event_id="bad-3", action=ActionKind.RELEASE, amount_cents=0),
        )
    )
    _expect_rejected(
        lambda: apply_escrow_event(
            led0,
            EscrowEvent(event_id="bad-4", action=ActionKind.REFUND, amount_cents=501),
        )
    )

    frozen = apply_escrow_event(
        led0,
        EscrowEvent(event_id="frz-1", action=ActionKind.FREEZE, amount_cents=0),
    )
    assert frozen.state == EscrowState.FROZEN
    _expect_rejected(
        lambda: apply_escrow_event(
            frozen,
            EscrowEvent(event_id="bad-5", action=ActionKind.RELEASE, amount_cents=100),
        )
    )
    _expect_rejected(
        lambda: apply_escrow_event(
            frozen,
            EscrowEvent(event_id="bad-6", action=ActionKind.FREEZE, amount_cents=0),
        )
    )


@settings(max_examples=60, deadline=None)
@given(
    principal=st.integers(min_value=1, max_value=10_000),
    raw_events=st.lists(
        st.tuples(
            st.sampled_from(["e0", "e1", "e2", "e3", "e4", "e5", "e6"]),
            st.sampled_from(list(ActionKind)),
            st.integers(min_value=0, max_value=6_000),
        ),
        max_size=25,
    ),
)
def test_oracle_hypothesis_state_machine_conservation(
    principal: int,
    raw_events: list[tuple[str, ActionKind, int]],
) -> None:
    """Principal conservation and CLOSED iff held_cents == 0 hold across all event traces."""
    led = open_escrow(principal)
    for eid, act, amt in raw_events:
        ev = EscrowEvent(event_id=eid, action=act, amount_cents=amt)
        try:
            led = apply_escrow_event(led, ev)
        except _REJECTED:
            pass
        assert led.held_cents >= 0
        assert led.released_cents >= 0
        assert led.refunded_cents >= 0
        assert led.held_cents + led.released_cents + led.refunded_cents == principal
        assert (led.state == EscrowState.CLOSED) == (led.held_cents == 0)
'''

__all__ = (
    "ORACLE_01_BPS_SPLIT",
    "ORACLE_02_SQL_LEDGER",
    "ORACLE_03_ARTIFACT_LOADER",
    "ORACLE_04_WORKFLOW_TOPO",
    "ORACLE_05_MARGIN_ENGINE",
    "ORACLE_06_ESCROW_MACHINE",
    "ORACLE_GO_01_BPS_SPLIT",
    "ORACLE_GO_02_STRICT_RECEIPT",
    "ORACLE_RS_01_BPS_SPLIT",
    "ORACLE_RS_02_ESCROW_FSM",
    "ORACLE_TS_01_BPS_SPLIT",
    "ORACLE_TS_02_ESCROW_VAULT",
)
