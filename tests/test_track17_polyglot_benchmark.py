"""Track 17: Polyglot adversarial SDD vs. PCE benchmark suite across Python, TS, Rust, and Go."""

import io
import os
import shutil
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents import (
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    FilePatchArtifact,
    InProcessGateRunner,
    InstrumentationSuiteBundle,
)
from jezz.cli import main
from jezz.codes import (
    SupportedLanguage,
    parse_go_unit,
    parse_rust_unit,
    parse_source_unit,
    parse_typescript_unit,
)
from jezz.evals import (
    AbBenchmarkComparison,
    BenchmarkHarnessDeps,
    BenchmarkLanguageSelector,
    BenchmarkRunConfig,
    BenchmarkTaskSpec,
    InProcessWorkspaceVerifier,
    PolyglotWorkspaceVerifier,
    SddDesignArtifact,
    SddImplementationBundle,
    SddSpecArtifact,
    SddTasksArtifact,
    build_non_python_benchmark_cases,
    build_polyglot_benchmark_cases,
    build_polyglot_dataset,
    build_sdd_implement_agent,
    build_track4_benchmark_cases,
    compute_structural_health,
    provision_scratch_workspace,
    render_sdd_constitution,
    run_ab_benchmark,
)
from jezz.guards import ContainedPath

REPO_ROOT = Path(__file__).resolve().parents[1]

_TS_PCE_SRC = textwrap.dedent(
    """\
    export type FeeTier = "retail" | "plus" | "prime" | "sovereign" | "internal";

    export interface SplitAllocation {
      readonly feeCents: bigint;
      readonly netCents: bigint;
      readonly sharesCents: readonly bigint[];
    }

    const TIER_BPS: Readonly<Record<FeeTier, bigint>> = {
      retail: 35n,
      plus: 20n,
      prime: 10n,
      sovereign: 3n,
      internal: 0n,
    };

    function sumPositiveWeights(weights: readonly bigint[]): bigint {
      let sum = 0n;
      for (const w of weights) {
        if (w <= 0n) {
          throw new RangeError("each weight must be positive");
        }
        sum += w;
      }
      return sum;
    }

    export function allocateBpsSplit(
      grossCents: bigint,
      tier: FeeTier,
      weights: readonly bigint[],
    ): SplitAllocation {
      if (grossCents < 0n || !(tier in TIER_BPS) || weights.length === 0 || weights.length > 1024) {
        throw new RangeError("invalid grossCents, tier, or weights");
      }
      const totalWeight = sumPositiveWeights(weights);
      const feeCents = (grossCents * TIER_BPS[tier]) / 10_000n;
      const netCents = grossCents - feeCents;
      const bases = weights.map((w) => (netCents * w) / totalWeight);
      const rem = Number(netCents - bases.reduce((a, b) => a + b, 0n));
      const sharesCents = bases.map((b, idx) => b + (idx < rem ? 1n : 0n));
      return { feeCents, netCents, sharesCents };
    }
    """
)

_TS_PCE_TEST = textwrap.dedent(
    """\
    import assert from "node:assert/strict";
    import test from "node:test";
    import { allocateBpsSplit } from "../src/bps_split.ts";

    test("test_bps_split_conserves_cents", () => {
      const out = allocateBpsSplit(10_003n, "retail", [1n, 1n, 1n]);
      assert.equal(out.feeCents, 35n);
      assert.equal(out.feeCents + out.sharesCents.reduce((a, b) => a + b, 0n), 10_003n);
    });

    test("test_bps_split_rejects_invalid_inputs", () => {
      assert.throws(() => allocateBpsSplit(-1n, "retail", [1n]));
      assert.throws(() => allocateBpsSplit(100n, "retail", []));
    });
    """
)

_TS_SDD_FLAWED_SRC = textwrap.dedent(
    """\
    export type FeeTier = any;
    export interface SplitAllocation {
      feeCents: any;
      netCents: any;
      sharesCents: any[];
    }
    export function allocateBpsSplit(grossCents: any, tier: any, weights: any): SplitAllocation {
      let rate = 0;
      if (tier === "retail") {
        rate = 0.0035;
      } else if (tier === "plus") {
        rate = 0.002;
      } else if (tier === "prime") {
        rate = 0.001;
      } else if (tier === "sovereign") {
        rate = 0.0003;
      }
      const feeCents = Math.floor(Number(grossCents) * rate);
      const netCents = Number(grossCents) - feeCents;
      return { feeCents, netCents, sharesCents: weights };
    }
    """
)

_TS_SDD_FLAWED_TEST = textwrap.dedent(
    """\
    import test from "node:test";
    test("smoke", () => {
      expect(1).toBe(1);
    });
    """
)

_RS_PCE_SRC = textwrap.dedent(
    """\
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum FeeTier {
        Retail,
        Plus,
        Prime,
        Sovereign,
        Internal,
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct SplitAllocation {
        pub fee_cents: i128,
        pub net_cents: i128,
        pub shares_cents: Vec<i128>,
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub enum SplitError {
        NegativeGross,
        InvalidWeights,
    }

    fn tier_bps(tier: FeeTier) -> i128 {
        match tier {
            FeeTier::Retail => 35,
            FeeTier::Plus => 20,
            FeeTier::Prime => 10,
            FeeTier::Sovereign => 3,
            FeeTier::Internal => 0,
        }
    }

    fn sum_positive_weights(weights: &[i128]) -> Result<i128, SplitError> {
        let mut total: i128 = 0;
        for w in weights {
            if *w <= 0 {
                return Err(SplitError::InvalidWeights);
            }
            total = total.checked_add(*w).ok_or(SplitError::InvalidWeights)?;
        }
        Ok(total)
    }

    pub fn allocate_bps_split(
        gross_cents: i128,
        tier: FeeTier,
        weights: &[i128],
    ) -> Result<SplitAllocation, SplitError> {
        if gross_cents < 0 || weights.is_empty() || weights.len() > 1024 {
            return Err(SplitError::InvalidWeights);
        }
        let total_w = sum_positive_weights(weights)?;
        let fee_cents = gross_cents
            .checked_mul(tier_bps(tier))
            .ok_or(SplitError::InvalidWeights)?
            / 10_000;
        let net_cents = gross_cents - fee_cents;
        let mut bases = Vec::with_capacity(weights.len());
        let mut base_sum: i128 = 0;
        for w in weights {
            let share = net_cents
                .checked_mul(*w)
                .ok_or(SplitError::InvalidWeights)?
                / total_w;
            base_sum += share;
            bases.push(share);
        }
        let rem = usize::try_from(net_cents - base_sum).unwrap_or(0);
        let shares_cents = bases
            .into_iter()
            .enumerate()
            .map(|(i, b)| if i < rem { b + 1 } else { b })
            .collect();
        Ok(SplitAllocation {
            fee_cents,
            net_cents,
            shares_cents,
        })
    }
    """
)

_RS_PCE_TEST = textwrap.dedent(
    """\
    use bps_split_rs::{allocate_bps_split, FeeTier};

    #[test]
    fn test_bps_split_conserves_cents() {
        let out = allocate_bps_split(10_003, FeeTier::Retail, &[1, 1, 1]).expect("valid");
        assert_eq!(out.fee_cents, 35);
        assert_eq!(out.fee_cents + out.shares_cents.iter().sum::<i128>(), 10_003);
    }

    #[test]
    fn test_bps_split_rejects_invalid_inputs() {
        assert!(allocate_bps_split(-1, FeeTier::Retail, &[1]).is_err());
        assert!(allocate_bps_split(100, FeeTier::Retail, &[]).is_err());
    }
    """
)

_RS_SDD_FLAWED_SRC = textwrap.dedent(
    """\
    #[derive(Debug, Clone, Copy)]
    pub enum FeeTier { Retail, Plus, Prime, Sovereign, Internal }
    pub struct SplitAllocation {
        pub fee_cents: i128,
        pub net_cents: i128,
        pub shares_cents: Vec<i128>,
    }
    #[derive(Debug)]
    pub enum SplitError { Bad }

    pub fn allocate_bps_split(
        gross_cents: i128,
        _tier: FeeTier,
        weights: &[i128],
    ) -> Result<SplitAllocation, SplitError> {
        let first = *weights.first().unwrap();
        let share = gross_cents / first;
        Ok(SplitAllocation { fee_cents: 0, net_cents: gross_cents, shares_cents: vec![share] })
    }
    """
)

_RS_SDD_FLAWED_TEST = textwrap.dedent(
    """\
    #[test]
    fn test_smoke() {
        assert_eq!(1, 1);
    }
    """
)

_GO_PCE_SRC = textwrap.dedent(
    """\
    package settle

    import (
    \t"bytes"
    \t"encoding/json"
    \t"errors"
    \t"io"
    )

    type SettlementReceipt struct {
    \tTxID        string `json:"tx_id"`
    \tAccountID   string `json:"account_id"`
    \tAmountCents int64  `json:"amount_cents"`
    \tFeeBps      int64  `json:"fee_bps"`
    }

    func (r SettlementReceipt) Validate() error {
    \tif len(r.TxID) == 0 || len(r.TxID) > 128 {
    \t\treturn errors.New("invalid tx_id")
    \t}
    \tif len(r.AccountID) == 0 || len(r.AccountID) > 128 {
    \t\treturn errors.New("invalid account_id")
    \t}
    \tif r.AmountCents <= 0 || r.FeeBps < 0 || r.FeeBps > 10_000 {
    \t\treturn errors.New("amount or fee_bps out of bounds")
    \t}
    \treturn nil
    }

    func (r SettlementReceipt) NetCents() int64 {
    \treturn r.AmountCents - (r.AmountCents*r.FeeBps)/10_000
    }

    func DecodeSettlement(raw []byte) (SettlementReceipt, error) {
    \tif len(raw) == 0 || len(raw) > 65_536 {
    \t\treturn SettlementReceipt{}, errors.New("raw payload out of bounds")
    \t}
    \tdec := json.NewDecoder(bytes.NewReader(raw))
    \tdec.DisallowUnknownFields()
    \tvar out SettlementReceipt
    \tif err := dec.Decode(&out); err != nil {
    \t\treturn SettlementReceipt{}, err
    \t}
    \tif dec.More() || dec.Decode(&struct{}{}) != io.EOF {
    \t\treturn SettlementReceipt{}, errors.New("trailing JSON data")
    \t}
    \tif err := out.Validate(); err != nil {
    \t\treturn SettlementReceipt{}, err
    \t}
    \treturn out, nil
    }
    """
)

_GO_PCE_TEST = textwrap.dedent(
    """\
    package settle

    import "testing"

    func TestStrictSettlementRejectsUnknownFields(t *testing.T) {
    \traw := []byte(`{"tx_id":"t1","account_id":"a1","amount_cents":100,"fee_bps":10,"extra":1}`)
    \tif _, err := DecodeSettlement(raw); err == nil {
    \t\tt.Fatal("expected unknown field error")
    \t}
    }

    func TestValidSettlementNetCents(t *testing.T) {
    \traw := []byte(`{"tx_id":"t1","account_id":"a1","amount_cents":10000,"fee_bps":25}`)
    \tout, err := DecodeSettlement(raw)
    \tif err != nil || out.NetCents() != 9975 {
    \t\tt.Fatalf("unexpected result: %v, %+v", err, out)
    \t}
    }
    """
)

_GO_SDD_FLAWED_SRC = textwrap.dedent(
    """\
    package settle

    import "encoding/json"

    type SettlementReceipt struct {
    \tTxID        string `json:"tx_id"`
    \tAccountID   string `json:"account_id"`
    \tAmountCents int64  `json:"amount_cents"`
    \tFeeBps      int64  `json:"fee_bps"`
    }

    func (r SettlementReceipt) Validate() error { return nil }
    func (r SettlementReceipt) NetCents() int64 { return r.AmountCents }

    func DecodeSettlement(raw []byte) (SettlementReceipt, error) {
    \tvar out SettlementReceipt
    \t_ = json.Unmarshal(raw, &out)
    \treturn out, nil
    }
    """
)

_GO_SDD_FLAWED_TEST = textwrap.dedent(
    """\
    package settle

    import "testing"

    func TestSmoke(t *testing.T) {}
    """
)


def _emit(info: AgentInfo, payload: BaseModel) -> ModelResponse:
    raw = payload.model_dump_json()
    if info.output_tools:
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=raw)])
    return ModelResponse(parts=[TextPart(content=raw)])


def _props(info: AgentInfo) -> set[str]:
    if not info.output_tools:
        return set()
    raw = info.output_tools[0].parameters_json_schema.get("properties", {})
    return {str(k) for k in raw} if isinstance(raw, dict) else set()


def _prompt_blob(messages: list[ModelMessage]) -> str:
    return "\n".join(str(m) for m in messages)


def _sdd_impl_for_prompt(blob: str) -> SddImplementationBundle:
    if "bps_split_rs" in blob:
        patches = (
            FilePatchArtifact(
                file_path="src/lib.rs", source_code=_RS_SDD_FLAWED_SRC, rationale="rs"
            ),
            FilePatchArtifact(
                file_path="tests/bps_split_test.rs",
                source_code=_RS_SDD_FLAWED_TEST,
                rationale="rs test",
            ),
        )
    elif "DecodeSettlement" in blob:
        patches = (
            FilePatchArtifact(
                file_path="settle/receipt.go",
                source_code=_GO_SDD_FLAWED_SRC,
                rationale="go",
            ),
            FilePatchArtifact(
                file_path="settle/receipt_test.go",
                source_code=_GO_SDD_FLAWED_TEST,
                rationale="go test",
            ),
        )
    else:
        patches = (
            FilePatchArtifact(
                file_path="src/bps_split.ts",
                source_code=_TS_SDD_FLAWED_SRC,
                rationale="ts",
            ),
            FilePatchArtifact(
                file_path="tests/bps_split.test.ts",
                source_code=_TS_SDD_FLAWED_TEST,
                rationale="ts test",
            ),
        )
    return SddImplementationBundle(
        patches=patches,
        completed_tasks_markdown="- [x] 1.1 Implement\n- [x] 1.2 Test\n",
    )


def _pce_bundles_for_prompt(
    blob: str,
) -> tuple[ContractSpecificationBundle, InstrumentationSuiteBundle, ConstructionPatchBundle]:
    if "MND-TRACK17-RS-BPS-SPLIT" in blob or "bps_split_rs" in blob:
        return (
            ContractSpecificationBundle(
                contract_patches=(
                    FilePatchArtifact(
                        file_path="src/lib.rs", source_code=_RS_PCE_SRC, rationale="c"
                    ),
                ),
                declared_symbols=("FeeTier", "SplitAllocation", "SplitError", "allocate_bps_split"),
                summary="Rust contracts.",
            ),
            InstrumentationSuiteBundle(
                test_patches=(
                    FilePatchArtifact(
                        file_path="tests/bps_split_test.rs",
                        source_code=_RS_PCE_TEST,
                        rationale="t",
                    ),
                ),
                covered_invariant_ids=("INV-001", "INV-002"),
                summary="Rust tests.",
            ),
            ConstructionPatchBundle(
                implementation_patches=(
                    FilePatchArtifact(
                        file_path="src/lib.rs", source_code=_RS_PCE_SRC, rationale="i"
                    ),
                ),
                summary="Rust implementation.",
            ),
        )
    if "MND-TRACK17-GO-SETTLE" in blob or "DecodeSettlement" in blob:
        return (
            ContractSpecificationBundle(
                contract_patches=(
                    FilePatchArtifact(
                        file_path="settle/receipt.go",
                        source_code=_GO_PCE_SRC,
                        rationale="c",
                    ),
                ),
                declared_symbols=("SettlementReceipt", "DecodeSettlement"),
                summary="Go contracts.",
            ),
            InstrumentationSuiteBundle(
                test_patches=(
                    FilePatchArtifact(
                        file_path="settle/receipt_test.go",
                        source_code=_GO_PCE_TEST,
                        rationale="t",
                    ),
                ),
                covered_invariant_ids=("INV-001", "INV-002"),
                summary="Go tests.",
            ),
            ConstructionPatchBundle(
                implementation_patches=(
                    FilePatchArtifact(
                        file_path="settle/receipt.go",
                        source_code=_GO_PCE_SRC,
                        rationale="i",
                    ),
                ),
                summary="Go implementation.",
            ),
        )
    return (
        ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/bps_split.ts", source_code=_TS_PCE_SRC, rationale="c"
                ),
            ),
            declared_symbols=("FeeTier", "SplitAllocation", "allocateBpsSplit"),
            summary="TS contracts.",
        ),
        InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/bps_split.test.ts",
                    source_code=_TS_PCE_TEST,
                    rationale="t",
                ),
            ),
            covered_invariant_ids=("INV-001", "INV-002"),
            summary="TS tests.",
        ),
        ConstructionPatchBundle(
            implementation_patches=(
                FilePatchArtifact(
                    file_path="src/bps_split.ts", source_code=_TS_PCE_SRC, rationale="i"
                ),
            ),
            summary="TS implementation.",
        ),
    )


def _build_polyglot_ab_model() -> FunctionModel:
    sdd_spec = SddSpecArtifact(
        spec_markdown=(
            "# Spec\nAs a developer, I want domain logic.\n"
            "WHEN called THE SYSTEM SHALL compute.\n#### Scenario: Main\n- GIVEN x\n"
        )
    )
    sdd_design = SddDesignArtifact(design_markdown="# Design\nArchitecture notes.\n" * 4)
    sdd_tasks = SddTasksArtifact(tasks_markdown="- [ ] 1.1 Implement\n- [ ] 1.2 Test\n")
    obs = EpistemicObservation(inspected_files=(), discovered_symbols=(), summary="Inspected.")

    def _dispatch(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        p = _props(info)
        blob = _prompt_blob(messages)
        if "inspected_files" in p:
            return _emit(info, obs)
        if "spec_markdown" in p:
            return _emit(info, sdd_spec)
        if "design_markdown" in p:
            return _emit(info, sdd_design)
        if "tasks_markdown" in p:
            return _emit(info, sdd_tasks)
        if "completed_tasks_markdown" in p:
            return _emit(info, _sdd_impl_for_prompt(blob))
        spec_b, inst_b, synth_b = _pce_bundles_for_prompt(blob)
        if "contract_patches" in p:
            return _emit(info, spec_b)
        if "test_patches" in p:
            return _emit(info, inst_b)
        return _emit(info, synth_b)

    return FunctionModel(_dispatch)


def _assert_invalid_polyglot_oracle_rejected() -> None:
    sample_ts = build_polyglot_benchmark_cases(SupportedLanguage.TYPESCRIPT)[0][0]
    for bad_lang, bad_src in (
        (SupportedLanguage.TYPESCRIPT, "export function broken(: void {"),
        (SupportedLanguage.RUST, "fn broken( -> i32 {"),
        (SupportedLanguage.GO, "package p\nfunc broken( {"),
    ):
        with pytest.raises(ValidationError, match="invalid syntax"):
            BenchmarkTaskSpec(
                case_id=f"bad_{bad_lang}",
                package_name="pkg",
                prompt=sample_ts.prompt,
                mandate=sample_ts.mandate,
                oracle_test_source=bad_src,
                language=bad_lang,
            )


async def test_polyglot_ab_benchmark_dataset_and_oracles_across_four_languages(
    tmp_path: Path,
) -> None:
    assert len(build_track4_benchmark_cases()) == 6
    assert len(build_non_python_benchmark_cases()) == 6
    all_cases = build_polyglot_benchmark_cases("all")
    assert len(all_cases) == 12
    _assert_invalid_polyglot_oracle_rejected()

    selected = (
        build_polyglot_benchmark_cases("typescript")[0],
        build_polyglot_benchmark_cases("rust")[0],
        build_polyglot_benchmark_cases("go")[1],
    )
    ds = build_polyglot_dataset("all", cases=selected)
    deps = BenchmarkHarnessDeps(
        work_root=tmp_path,
        jezz_root=REPO_ROOT,
        config=BenchmarkRunConfig(max_concurrency=1, sync_workspace_venv=False),
        verifier=InProcessWorkspaceVerifier(),
        gate_runner=InProcessGateRunner(),
    )
    comparison = await run_ab_benchmark(
        _build_polyglot_ab_model(),
        deps,
        model_label="mock:polyglot-ab",
        dataset=ds,
    )
    assert comparison.control_summary.cases_total == 3
    assert comparison.control_summary.cases_jbc_occupiable == 0
    assert comparison.control_summary.cases_oracle_passed == 0
    diag = [(o.case_id, o.violated_code_ids, o.failure_note) for o in comparison.treatment_outcomes]
    assert comparison.treatment_summary.cases_jbc_occupiable == 3, diag
    assert comparison.treatment_summary.cases_oracle_passed == 3, diag


def _write_shim(bin_dir: Path, name: str, script_body: str) -> None:
    shim = ContainedPath.from_parts(bin_dir, name).resolved_path
    shim.write_text(f"#!/bin/sh\n{script_body}\n", encoding="utf-8")
    shim.chmod(0o755)


def _install_polyglot_shims(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_shim(bin_dir, "node", 'echo "# pass 3"\necho "# fail 0"\nexit 0')
    _write_shim(bin_dir, "tsc", "exit 0")
    _write_shim(
        bin_dir,
        "cargo",
        'echo "test result: ok. 2 passed; 0 failed; 0 ignored"\nexit 0',
    )
    _write_shim(
        bin_dir,
        "go",
        'echo "--- PASS: TestOracleOne (0.00s)"\necho "--- PASS: TestOracleTwo (0.00s)"\nexit 0',
    )


def _populate_polyglot_workspaces(root: Path) -> tuple[Path, Path, Path]:
    ts_ws = provision_scratch_workspace(
        root,
        "ws_ts",
        "bps_split_ts",
        REPO_ROOT,
        sync_venv=False,
        language=SupportedLanguage.TYPESCRIPT,
    ).resolved_path
    ContainedPath.from_parts(ts_ws, "src/bps_split.ts").resolved_path.write_text(
        _TS_PCE_SRC, encoding="utf-8"
    )
    ContainedPath.from_parts(ts_ws, "tests/bps_split.test.ts").resolved_path.write_text(
        _TS_PCE_TEST, encoding="utf-8"
    )

    rs_ws = provision_scratch_workspace(
        root, "ws_rs", "bps_split_rs", REPO_ROOT, sync_venv=False, language=SupportedLanguage.RUST
    ).resolved_path
    ContainedPath.from_parts(rs_ws, "src/lib.rs").resolved_path.write_text(
        _RS_PCE_SRC, encoding="utf-8"
    )
    ContainedPath.from_parts(rs_ws, "tests/bps_split_test.rs").resolved_path.write_text(
        _RS_PCE_TEST, encoding="utf-8"
    )

    go_ws = provision_scratch_workspace(
        root, "ws_go", "settle", REPO_ROOT, sync_venv=False, language=SupportedLanguage.GO
    ).resolved_path
    ContainedPath.from_parts(go_ws, "settle/receipt.go").resolved_path.write_text(
        _GO_PCE_SRC, encoding="utf-8"
    )
    ContainedPath.from_parts(go_ws, "settle/receipt_test.go").resolved_path.write_text(
        _GO_PCE_TEST, encoding="utf-8"
    )
    return ts_ws, rs_ws, go_ws


def test_polyglot_subprocess_verifier_shims_and_live_go_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ts_task = build_polyglot_benchmark_cases("typescript")[0][0]
    rs_task = build_polyglot_benchmark_cases("rust")[0][0]
    go_task = build_polyglot_benchmark_cases("go")[1][0]
    ts_ws, rs_ws, go_ws = _populate_polyglot_workspaces(tmp_path)

    if shutil.which("go") is not None:
        live_go = PolyglotWorkspaceVerifier(timeout_sec=30.0).verify_workspace(go_ws, go_task)
        assert live_go.passed and live_go.tests_passed == 2 and live_go.mypy_strict_passed

    bin_dir = ContainedPath.from_parts(tmp_path, "shims").resolved_path
    _install_polyglot_shims(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    verifier = PolyglotWorkspaceVerifier(timeout_sec=15.0)
    for ws, task in ((ts_ws, ts_task), (rs_ws, rs_task), (go_ws, go_task)):
        verdict = verifier.verify_workspace(ws, task)
        assert verdict.passed and verdict.mypy_strict_passed and verdict.self_tests_passed

    ContainedPath.from_parts(bin_dir, "tsc").resolved_path.unlink()
    monkeypatch.setenv("PATH", str(bin_dir))
    ts_fallback = verifier.verify_workspace(ts_ws, ts_task)
    assert ts_fallback.passed and ts_fallback.mypy_strict_passed
    missing_uv = verifier.verify_workspace(
        rs_ws,
        build_track4_benchmark_cases()[0][0],
    )
    assert not missing_uv.passed


def test_cli_benchmark_language_flags_hooks_and_sdd_validators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jezz.cli.resolve_cli_model", lambda _m: _build_polyglot_ab_model())
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc_ok = main(
        (
            "benchmark",
            str(tmp_path),
            "--model",
            "mock:poly",
            "--language",
            "rust",
            "--case-id",
            "case_rs_01_bps_split",
            "--gate-runner",
            "in-process",
            "--max-concurrency",
            "1",
            "--write-json",
            "out/rust_ab.json",
        ),
        stdout=out_buf,
        stderr=err_buf,
    )
    assert rc_ok == 0, err_buf.getvalue()
    saved = AbBenchmarkComparison.model_validate_json(
        ContainedPath.from_parts(tmp_path, "out/rust_ab.json").resolved_path.read_text("utf-8")
    )
    assert saved.dataset_name == "jezz_polyglot_sdd_vs_pce_rust"
    assert saved.treatment_summary.cases_oracle_passed == 1

    rc_mismatch = main(
        (
            "benchmark",
            str(tmp_path),
            "--model",
            "mock:poly",
            "--language",
            "typescript",
            "--case-id",
            "case_rs_01_bps_split",
            "--gate-runner",
            "in-process",
        ),
        stdout=io.StringIO(),
        stderr=err_buf,
    )
    assert rc_mismatch == 2

    hook_verdict = InProcessWorkspaceVerifier(
        polyglot_oracle_hook=lambda _w, _t, _u: (4, 0, "custom hook green"),
    ).verify_workspace(
        ContainedPath.from_parts(tmp_path, "pce_case_rs_01_bps_split").resolved_path,
        build_polyglot_benchmark_cases("rust")[0][0],
    )
    assert hook_verdict.passed and hook_verdict.tests_passed == 4


async def test_sdd_polyglot_constitution_and_bundle_path_guards() -> None:
    for lang in SupportedLanguage:
        text = render_sdd_constitution(lang)
        assert "Article I" in text

    bad_bundle = SddImplementationBundle(
        patches=(
            FilePatchArtifact(file_path="../escape.go", source_code="package p\n", rationale="x"),
        ),
        completed_tasks_markdown="- [x] done\n",
    )
    agent = build_sdd_implement_agent(
        FunctionModel(lambda _m, info: _emit(info, bad_bundle)),
        language=SupportedLanguage.GO,
    )
    with pytest.raises(UnexpectedModelBehavior):
        await agent.run("implement")


@given(
    selector=st.sampled_from(("python", "typescript", "rust", "go", "all")),
)
def test_property_polyglot_benchmark_language_partition_and_structural_health(
    selector: BenchmarkLanguageSelector,
) -> None:
    cases = build_polyglot_benchmark_cases(selector)
    expected_len = 12 if selector == "all" else (6 if selector == "python" else 2)
    assert len(cases) == expected_len
    assert len({spec.case_id for spec, _ in cases}) == expected_len
    if selector != "all":
        assert all(spec.language == SupportedLanguage(selector) for spec, _ in cases)

    units = (
        parse_source_unit("src/a.py", "def f(x: int) -> int:\n    return x + 1\n"),
        parse_typescript_unit("src/b.ts", "export function g(x: number): number { return x; }\n"),
        parse_rust_unit("src/lib.rs", "pub fn h(x: i32) -> i32 { if x > 0 { x } else { 0 } }\n"),
        parse_go_unit("p/a.go", "package p\nfunc K(x int) int { return x }\n"),
    )
    snap = compute_structural_health(units)
    assert snap.unit_count == 4
    assert snap.max_cognitive_complexity >= 1
