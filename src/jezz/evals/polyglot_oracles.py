"""Track 17: Held-out offline property/fuzz oracles for TypeScript, Rust, and Go tasks."""

from typing import Final

ORACLE_TS_01_BPS_SPLIT: Final[str] = """\
import assert from "node:assert/strict";
import test from "node:test";
import { allocateBpsSplit, type FeeTier } from "../src/bps_split.ts";

const TIERS: readonly [FeeTier, bigint][] = [
  ["retail", 35n],
  ["plus", 20n],
  ["prime", 10n],
  ["sovereign", 3n],
  ["internal", 0n],
];

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return (): number => {
    state = (state + 0x6d2b79f5) >>> 0;
    let z = Math.imul(state ^ (state >>> 15), 1 | state);
    z = (z + Math.imul(z ^ (z >>> 7), 61 | z)) ^ z;
    return ((z ^ (z >>> 14)) >>> 0) / 4294967296;
  };
}

test("oracle: exact bigint conservation above MAX_SAFE_INTEGER", () => {
  for (const gross of [9_007_199_254_740_993n, 99_999_999_999_999_989n]) {
    for (const [tier, bps] of TIERS) {
      const out = allocateBpsSplit(gross, tier, [3n, 7n, 11n]);
      const expectedFee = (gross * bps) / 10_000n;
      const expectedNet = gross - expectedFee;
      assert.equal(out.feeCents, expectedFee);
      assert.equal(out.netCents, expectedNet);
      const sumShares = out.sharesCents.reduce((acc, v) => acc + v, 0n);
      assert.equal(sumShares, expectedNet);
      assert.equal(out.feeCents + sumShares, gross);
    }
  }
});

test("oracle: mulberry32 200-run property conservation and remainder prefix", () => {
  const rng = mulberry32(0x5eed1701);
  for (let run = 0; run < 200; run += 1) {
    const gross = BigInt(Math.floor(rng() * 1_000_000_000)) * 1_000_003n + BigInt(run);
    const [tier, bps] = TIERS[run % TIERS.length];
    const len = 1 + (run % 6);
    const weights: bigint[] = [];
    let wSum = 0n;
    for (let i = 0; i < len; i += 1) {
      const w = BigInt(1 + Math.floor(rng() * 5000));
      weights.push(w);
      wSum += w;
    }
    const out = allocateBpsSplit(gross, tier, weights);
    const expectedFee = (gross * bps) / 10_000n;
    const expectedNet = gross - expectedFee;
    assert.equal(out.feeCents, expectedFee);
    assert.equal(out.netCents, expectedNet);
    const bases = weights.map((w) => (expectedNet * w) / wSum);
    const rem = Number(expectedNet - bases.reduce((a, b) => a + b, 0n));
    for (let i = 0; i < len; i += 1) {
      const expectedShare = bases[i] + (i < rem ? 1n : 0n);
      assert.equal(out.sharesCents[i], expectedShare);
    }
  }
});

test("oracle: rejects negative gross, empty weights, and non-positive weights", () => {
  assert.throws(() => allocateBpsSplit(-1n, "retail", [1n]));
  assert.throws(() => allocateBpsSplit(100n, "retail", []));
  assert.throws(() => allocateBpsSplit(100n, "plus", [2n, 0n]));
  assert.throws(() => allocateBpsSplit(100n, "prime", [5n, -2n]));
});
"""

ORACLE_TS_02_ESCROW_VAULT: Final[str] = """\
import assert from "node:assert/strict";
import test from "node:test";
import {
  applyEscrowEvent,
  openEscrow,
  type ActionKind,
  type EscrowEvent,
} from "../src/escrow.ts";

const ACTIONS: readonly ActionKind[] = ["release", "refund", "freeze", "unfreeze"];

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return (): number => {
    state = (state + 0x6d2b79f5) >>> 0;
    let z = Math.imul(state ^ (state >>> 15), 1 | state);
    z = (z + Math.imul(z ^ (z >>> 7), 61 | z)) ^ z;
    return ((z ^ (z >>> 14)) >>> 0) / 4294967296;
  };
}

test("oracle: rejects invalid principal, frozen payouts, and closed mutations", () => {
  assert.throws(() => openEscrow(0n));
  assert.throws(() => openEscrow(-10n));
  const funded = openEscrow(500n);
  const frozen = applyEscrowEvent(funded, {
    eventId: "ev-freeze",
    action: "freeze",
    amountCents: 0n,
  });
  assert.equal(frozen.state, "frozen");
  assert.throws(() =>
    applyEscrowEvent(frozen, { eventId: "ev-leak", action: "release", amountCents: 100n }),
  );
  const dup = applyEscrowEvent(frozen, {
    eventId: "ev-freeze",
    action: "release",
    amountCents: 100n,
  });
  assert.equal(dup.heldCents, 500n);
  assert.equal(dup.state, "frozen");
});

test("oracle: mulberry32 160-trace state machine conserves principal", () => {
  const rng = mulberry32(0x5eed1702);
  const ids = ["e0", "e1", "e2", "e3", "e4", "e5"];
  for (let trace = 0; trace < 160; trace += 1) {
    const principal = BigInt(10 + Math.floor(rng() * 5000));
    let ledger = openEscrow(principal);
    for (let step = 0; step < 18; step += 1) {
      const action = ACTIONS[Math.floor(rng() * ACTIONS.length)];
      const amt = action === "freeze" || action === "unfreeze"
        ? (rng() < 0.8 ? 0n : 5n)
        : BigInt(Math.floor(rng() * 3000));
      const ev: EscrowEvent = {
        eventId: ids[Math.floor(rng() * ids.length)],
        action,
        amountCents: amt,
      };
      try {
        ledger = applyEscrowEvent(ledger, ev);
      } catch {
        // Rejected invalid transitions must leave prior immutable ledger intact.
      }
      assert.equal(
        ledger.heldCents + ledger.releasedCents + ledger.refundedCents,
        principal,
      );
      assert.equal(ledger.state === "closed", ledger.heldCents === 0n);
    }
  }
});
"""

ORACLE_RS_01_BPS_SPLIT: Final[str] = """\
use bps_split_rs::{allocate_bps_split, FeeTier};

fn next_u64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

#[test]
fn oracle_large_cent_precision_and_invalid_boundaries() {
    let tiers = [
        (FeeTier::Retail, 35_i128),
        (FeeTier::Plus, 20_i128),
        (FeeTier::Prime, 10_i128),
        (FeeTier::Sovereign, 3_i128),
        (FeeTier::Internal, 0_i128),
    ];
    for gross in [9_007_199_254_740_993_i128, 99_999_999_999_999_989_i128] {
        for (tier, bps) in tiers {
            let out = allocate_bps_split(gross, tier, &[3, 7, 11]).expect("valid split");
            let expected_fee = (gross * bps) / 10_000;
            let expected_net = gross - expected_fee;
            assert_eq!(out.fee_cents, expected_fee);
            assert_eq!(out.net_cents, expected_net);
            let sum_shares: i128 = out.shares_cents.iter().copied().sum();
            assert_eq!(sum_shares, expected_net);
            assert_eq!(out.fee_cents + sum_shares, gross);
        }
    }
    assert!(allocate_bps_split(-1, FeeTier::Retail, &[1]).is_err());
    assert!(allocate_bps_split(100, FeeTier::Retail, &[]).is_err());
    assert!(allocate_bps_split(100, FeeTier::Plus, &[1, 0]).is_err());
    assert!(allocate_bps_split(100, FeeTier::Prime, &[4, -2]).is_err());
}

#[test]
fn oracle_splitmix64_property_conservation_250_runs() {
    let tiers = [
        (FeeTier::Retail, 35_i128),
        (FeeTier::Plus, 20_i128),
        (FeeTier::Prime, 10_i128),
        (FeeTier::Sovereign, 3_i128),
        (FeeTier::Internal, 0_i128),
    ];
    let mut seed: u64 = 0x5EED_1703;
    for run in 0..250_usize {
        let gross = i128::from(next_u64(&mut seed) % 1_000_000_000_000_u64);
        let (tier, bps) = tiers[run % tiers.len()];
        let len = 1 + (run % 6);
        let mut weights = Vec::with_capacity(len);
        for _ in 0..len {
            let w = 1 + i128::from(next_u64(&mut seed) % 5_000_u64);
            weights.push(w);
        }
        let w_sum: i128 = weights.iter().copied().sum();
        let out = allocate_bps_split(gross, tier, &weights).expect("valid fuzz case");
        let expected_fee = (gross * bps) / 10_000;
        let expected_net = gross - expected_fee;
        assert_eq!(out.fee_cents, expected_fee);
        assert_eq!(out.net_cents, expected_net);
        let bases: Vec<i128> = weights.iter().map(|w| (expected_net * *w) / w_sum).collect();
        let rem = (expected_net - bases.iter().copied().sum::<i128>()) as usize;
        for (idx, base) in bases.iter().copied().enumerate() {
            let bonus = if idx < rem { 1 } else { 0 };
            assert_eq!(out.shares_cents[idx], base + bonus);
        }
    }
}
"""

ORACLE_RS_02_ESCROW_FSM: Final[str] = """\
use escrow_rs::{apply_escrow_event, open_escrow, ActionKind, EscrowEvent, EscrowState};

fn next_u64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

#[test]
fn oracle_rejects_frozen_payout_and_preserves_idempotency() {
    assert!(open_escrow(0).is_err());
    assert!(open_escrow(-5).is_err());
    let funded = open_escrow(500).expect("funded");
    let freeze_ev = EscrowEvent {
        event_id: "e-freeze".to_string(),
        action: ActionKind::Freeze,
        amount_cents: 0,
    };
    let frozen = apply_escrow_event(&funded, &freeze_ev).expect("frozen");
    assert_eq!(frozen.state, EscrowState::Frozen);
    let leak_ev = EscrowEvent {
        event_id: "e-leak".to_string(),
        action: ActionKind::Release,
        amount_cents: 50,
    };
    assert!(apply_escrow_event(&frozen, &leak_ev).is_err());
    let dup = apply_escrow_event(
        &frozen,
        &EscrowEvent {
            event_id: "e-freeze".to_string(),
            action: ActionKind::Release,
            amount_cents: 50,
        },
    )
    .expect("duplicate event_id is idempotent");
    assert_eq!(dup.held_cents, 500);
    assert_eq!(dup.state, EscrowState::Frozen);
}

#[test]
fn oracle_splitmix64_trace_conservation_200_runs() {
    let actions = [
        ActionKind::Release,
        ActionKind::Refund,
        ActionKind::Freeze,
        ActionKind::Unfreeze,
    ];
    let ids = ["e0", "e1", "e2", "e3", "e4", "e5"];
    let mut seed: u64 = 0x5EED_1704;
    for _ in 0..200_usize {
        let principal = 10 + i64::try_from(next_u64(&mut seed) % 5_000).unwrap_or(100);
        let mut ledger = open_escrow(principal).expect("open valid principal");
        for _ in 0..18_usize {
            let act = actions[(next_u64(&mut seed) as usize) % actions.len()];
            let amt = i64::try_from(next_u64(&mut seed) % 3_000).unwrap_or(0);
            let ev = EscrowEvent {
                event_id: ids[(next_u64(&mut seed) as usize) % ids.len()].to_string(),
                action: act,
                amount_cents: amt,
            };
            if let Ok(next_ledger) = apply_escrow_event(&ledger, &ev) {
                ledger = next_ledger;
            }
            assert_eq!(
                ledger.held_cents + ledger.released_cents + ledger.refunded_cents,
                principal
            );
            assert_eq!(ledger.state == EscrowState::Closed, ledger.held_cents == 0);
        }
    }
}
"""

ORACLE_GO_01_BPS_SPLIT: Final[str] = """\
package bpssplit

import (
\t"testing"
\t"testing/quick"
)

func TestOracleBpsSplitBoundariesAndLargeInt64(t *testing.T) {
\ttiers := []struct {
\t\ttier FeeTier
\t\tbps  int64
\t}{
\t\t{TierRetail, 35},
\t\t{TierPlus, 20},
\t\t{TierPrime, 10},
\t\t{TierSovereign, 3},
\t\t{TierInternal, 0},
\t}
\tfor _, tc := range tiers {
\t\tout, err := AllocateBpsSplit(900_719_925_474_099, tc.tier, []int64{3, 7, 11})
\t\tif err != nil {
\t\t\tt.Fatalf("unexpected error for %s: %v", tc.tier, err)
\t\t}
\t\texpFee := (int64(900_719_925_474_099) * tc.bps) / 10_000
\t\texpNet := int64(900_719_925_474_099) - expFee
\t\tif out.FeeCents != expFee || out.NetCents != expNet {
\t\t\tt.Fatalf("fee/net mismatch: got (%d, %d)", out.FeeCents, out.NetCents)
\t\t}
\t\tvar sum int64
\t\tfor _, s := range out.SharesCents {
\t\t\tsum += s
\t\t}
\t\tif out.FeeCents+sum != 900_719_925_474_099 {
\t\t\tt.Fatalf("conservation violated: %d + %d", out.FeeCents, sum)
\t\t}
\t}
\tif _, err := AllocateBpsSplit(-1, TierRetail, []int64{1}); err == nil {
\t\tt.Fatal("expected error for negative grossCents")
\t}
\tif _, err := AllocateBpsSplit(100, TierRetail, nil); err == nil {
\t\tt.Fatal("expected error for empty weights")
\t}
\tif _, err := AllocateBpsSplit(100, TierPlus, []int64{2, 0}); err == nil {
\t\tt.Fatal("expected error for zero weight")
\t}
\tif _, err := AllocateBpsSplit(100, FeeTier("bogus"), []int64{1}); err == nil {
\t\tt.Fatal("expected error for unknown tier")
\t}
}

func TestOracleBpsSplitQuickProperty(t *testing.T) {
\ttiers := []FeeTier{TierRetail, TierPlus, TierPrime, TierSovereign, TierInternal}
\tprop := func(rawGross uint32, rawTier uint8, w1 uint16, w2 uint16, w3 uint16) bool {
\t\tgross := int64(rawGross)
\t\ttier := tiers[int(rawTier)%len(tiers)]
\t\tweights := []int64{int64(w1%500) + 1, int64(w2%500) + 1, int64(w3%500) + 1}
\t\tout, err := AllocateBpsSplit(gross, tier, weights)
\t\tif err != nil {
\t\t\treturn false
\t\t}
\t\tvar sum int64
\t\tfor _, v := range out.SharesCents {
\t\t\tsum += v
\t\t}
\t\treturn out.FeeCents+sum == gross && out.NetCents == sum
\t}
\tif err := quick.Check(prop, &quick.Config{MaxCount: 200}); err != nil {
\t\tt.Fatalf("quick property falsified: %v", err)
\t}
}
"""

ORACLE_GO_02_STRICT_RECEIPT: Final[str] = """\
package settle

import (
\t"fmt"
\t"testing"
\t"testing/quick"
)

func TestOracleRejectsUnknownFieldsAndOutOfRangeValues(t *testing.T) {
\tbadPayloads := []string{
\t\t`{"tx_id":"tx-1","account_id":"acc-1","amount_cents":100,"fee_bps":25,"extra":true}`,
\t\t`{"tx_id":"","account_id":"acc-1","amount_cents":100,"fee_bps":25}`,
\t\t`{"tx_id":"tx-1","account_id":"","amount_cents":100,"fee_bps":25}`,
\t\t`{"tx_id":"tx-1","account_id":"acc-1","amount_cents":0,"fee_bps":25}`,
\t\t`{"tx_id":"tx-1","account_id":"acc-1","amount_cents":-50,"fee_bps":25}`,
\t\t`{"tx_id":"tx-1","account_id":"acc-1","amount_cents":100,"fee_bps":-1}`,
\t\t`{"tx_id":"tx-1","account_id":"acc-1","amount_cents":100,"fee_bps":10001}`,
\t\t`{"tx_id":"tx-1","account_id":"acc-1","amount_cents":100,"fee_bps":25}{"trailing":1}`,
\t}
\tfor idx, raw := range badPayloads {
\t\tif _, err := DecodeSettlement([]byte(raw)); err == nil {
\t\t\tt.Fatalf("payload %d should have been rejected: %s", idx, raw)
\t\t}
\t}
}

func TestOracleValidSettlementQuickRoundtrip(t *testing.T) {
\tprop := func(rawAmt uint32, rawBps uint16) bool {
\t\tamt := int64(rawAmt%100_000_000) + 1
\t\tbps := int64(rawBps % 10_001)
\t\tpayload := fmt.Sprintf(
\t\t\t`{"tx_id":"tx-%d","account_id":"acct-%d","amount_cents":%d,"fee_bps":%d}`,
\t\t\tamt,
\t\t\tbps,
\t\t\tamt,
\t\t\tbps,
\t\t)
\t\treceipt, err := DecodeSettlement([]byte(payload))
\t\tif err != nil {
\t\t\treturn false
\t\t}
\t\texpectedNet := amt - (amt*bps)/10_000
\t\treturn receipt.NetCents() == expectedNet && receipt.Validate() == nil
\t}
\tif err := quick.Check(prop, &quick.Config{MaxCount: 200}); err != nil {
\t\tt.Fatalf("quick roundtrip falsified: %v", err)
\t}
}
"""

__all__ = (
    "ORACLE_GO_01_BPS_SPLIT",
    "ORACLE_GO_02_STRICT_RECEIPT",
    "ORACLE_RS_01_BPS_SPLIT",
    "ORACLE_RS_02_ESCROW_FSM",
    "ORACLE_TS_01_BPS_SPLIT",
    "ORACLE_TS_02_ESCROW_VAULT",
)
