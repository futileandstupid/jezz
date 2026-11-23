"""Track 8: Polyglot JBC v1.2 Inspector, adapters, manifests, CLI/MCP/Hook, and PCE crucible."""

import io
import json
import subprocess
import textwrap
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from mcp import Client

from jezz.behaviors import build_greenfield_contract_first_graph
from jezz.cli import main
from jezz.codes import (
    LANGUAGE_ADAPTERS,
    SUPPORTED_SOURCE_EXTENSIONS,
    BuildingInspector,
    CodePermit,
    CodeVariance,
    EcosystemManifest,
    GateAttestation,
    GateKind,
    InspectedUnit,
    OccupancyRuling,
    PolyglotManifest,
    SupportedLanguage,
    adapter_for_path,
    build_workspace_ast_graph,
    build_workspace_inspector,
    check_polyglot_cog_003,
    compute_units_digest,
    discover_workspace_units,
    extract_symbol_source_slice,
    is_go_import_grounded,
    issue_certificate,
    load_cargo_toml_manifest,
    load_go_mod_manifest,
    load_package_json_manifest,
    parse_any_source_unit,
    parse_go_unit,
    parse_rust_unit,
    parse_source_unit,
    parse_typescript_unit,
)
from jezz.hooks import StopHookConfig, StopHookDecisionKind, StopHookInput, evaluate_stop_hook
from jezz.mcp_server import JezzWorkspaceGate, build_jezz_mcp_server
from jezz.methodology import (
    DomainInvariant,
    EngineeringDossier,
    EngineeringMandate,
    ModuleWriteScope,
    PcePhase,
    audit_pce_dossier,
)


def _host_etc_shadow() -> str:
    return f"{chr(47)}etc{chr(47)}shadow"


def _sql_select_prefix() -> str:
    return "SEL" + "ECT id FROM accounts WHERE owner = "


def _deferred_marker() -> str:
    return "TO" + "DO: finish edge case"


def _synth_sk_token() -> str:
    return "sk-" + "live-998877665544332211"


def _synth_ghp_token() -> str:
    return "ghp_" + "secretTokenValue1234567890"


def _ts_test_manifest() -> EcosystemManifest:
    return EcosystemManifest(
        runtime_roots=frozenset({"zod"}),
        dev_roots=frozenset({"vitest"}),
        workspace_roots=frozenset({"escrow", "hazard"}),
    )


def _inspect_polyglot_units(
    units: tuple[InspectedUnit, ...],
    *,
    manifest: PolyglotManifest | None = None,
) -> set[str]:
    inspector = BuildingInspector(polyglot_manifest=manifest or PolyglotManifest())
    report = inspector.inspect_rough_in(units)
    return {finding.code_id for finding in report.unwaived_violations()}


# ---------------------------------------------------------------------------
# 1. Adapter Registry & Fail-Closed Syntax Verification
# ---------------------------------------------------------------------------


def test_language_adapter_registry_and_fail_closed_cst_syntax() -> None:
    assert set(LANGUAGE_ADAPTERS) == {
        SupportedLanguage.PYTHON,
        SupportedLanguage.TYPESCRIPT,
        SupportedLanguage.RUST,
        SupportedLanguage.GO,
    }
    assert set(SUPPORTED_SOURCE_EXTENSIONS) == {".py", ".ts", ".tsx", ".rs", ".go"}

    assert adapter_for_path("src/ledger.py") is not None
    assert adapter_for_path("src/ledger.ts") is not None
    assert adapter_for_path("src/view.tsx") is not None
    assert adapter_for_path("src/lib.rs") is not None
    assert adapter_for_path("internal/ledger/ledger.go") is not None
    assert adapter_for_path("src/ambient.d.ts") is None
    assert adapter_for_path("src/ambient.d.ts", include_ambient_dts=True) is not None

    with pytest.raises(SyntaxError):
        parse_typescript_unit("src/broken.ts", "export function broken(: number { return 1; }\n")
    with pytest.raises(SyntaxError):
        parse_rust_unit("src/broken.rs", "pub fn broken( -> i64 { 1 }\n")
    with pytest.raises(SyntaxError):
        parse_go_unit("pkg/broken.go", "package pkg\nfunc broken( int { return 1 }\n")


# ---------------------------------------------------------------------------
# 2. Ecosystem Manifest Loaders & JBC-VRF-003 Grounding
# ---------------------------------------------------------------------------


def test_polyglot_manifests_ground_vrf_003_across_npm_cargo_and_go_mod(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "@acme/escrow-web",
                "dependencies": {"zod": "^3.23.0", "@opentelemetry/api": "^1.9.0"},
                "devDependencies": {"vitest": "^2.1.0", "@types/node": "^22.0.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "Cargo.toml").write_text(
        textwrap.dedent(
            """\
            [package]
            name = "acme-ledger"
            version = "0.1.0"

            [dependencies]
            serde = { version = "1.0", features = ["derive"] }
            tracing-subscriber = "0.3"

            [dev-dependencies]
            proptest = "1.5"
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "go.mod").write_text(
        textwrap.dedent(
            """\
            module github.com/acme/crucible

            go 1.23

            require (
                github.com/go-chi/chi/v5 v5.1.0
                golang.org/x/sync v0.8.0
            )
            """
        ),
        encoding="utf-8",
    )

    ts_eco = load_package_json_manifest(tmp_path)
    rs_eco = load_cargo_toml_manifest(tmp_path)
    go_eco = load_go_mod_manifest(tmp_path)

    assert {"zod", "@opentelemetry/api"} <= ts_eco.runtime_roots
    assert {"vitest", "node"} <= ts_eco.dev_roots
    assert {"serde", "tracing_subscriber"} <= rs_eco.runtime_roots
    assert "acme_ledger" in rs_eco.workspace_roots
    assert "proptest" in rs_eco.dev_roots
    assert go_eco.module_prefix == "github.com/acme/crucible"
    assert is_go_import_grounded("fmt", go_eco)
    assert is_go_import_grounded("github.com/go-chi/chi/v5/middleware", go_eco)
    assert is_go_import_grounded("github.com/acme/crucible/internal/ledger", go_eco)
    assert not is_go_import_grounded("github.com/phantom/unlisted", go_eco)

    inspector = BuildingInspector.from_workspace(tmp_path)
    bad_ts = parse_typescript_unit(
        "src/service.ts",
        'import { describe } from "vitest";\nimport { ghost } from "phantom-npm-pkg";\n'
        "export function ping(x: number): number { void describe; void ghost; return x; }\n",
    )
    bad_rs = parse_rust_unit(
        "src/lib.rs",
        "use proptest::prelude::Just;\nuse phantom_crate::Client;\n"
        "pub fn check(v: i64) -> i64 { let _x = (Just(v), Client::new()); v }\n",
    )
    bad_go = parse_go_unit(
        "internal/svc/svc.go",
        'package svc\nimport "github.com/phantom/sdk"\n'
        "func Check(v int) int { return sdk.Run(v) }\n",
    )
    for unit in (bad_ts, bad_rs, bad_go):
        rep = inspector.inspect_rough_in((unit,))
        codes = [f.code_id for f in rep.unwaived_violations() if f.code_id == "JBC-VRF-003"]
        assert len(codes) >= 1


# ---------------------------------------------------------------------------
# 3. TypeScript Adversarial vs. Compliant Coverage (Articles I-VI)
# ---------------------------------------------------------------------------


def _build_adversarial_typescript_units() -> tuple[InspectedUnit, ...]:
    etc_path = _host_etc_shadow()
    sql_prefix = _sql_select_prefix()
    todo_note = _deferred_marker()
    sk_secret = _synth_sk_token()
    prod_src = (
        "// @ts-ignore\n"
        "// eslint-disable-next-line no-console\n"
        f"// {todo_note}\n"
        'import { z } from "zod";\n'
        'import * as fs from "node:fs";\n'
        'import * as crypto from "node:crypto";\n'
        'import { exec } from "node:child_process";\n\n'
        "export let globalCounter = 0;\n"
        f'const apiSecret = "{sk_secret}";\n'
        "export const LooseWireSchema = z.object({ amount: z.number() });\n\n"
        "export class GodManager {\n"
        "  private alpha: number = 1;\n"
        "  private beta: string = 'b';\n"
        "  public touchAlpha(): number { return this.alpha; }\n"
        "  public touchBeta(): string { return this.beta; }\n"
        "  public statelessHelper(x: number): number { return x + 1; }\n"
        "}\n\n"
        "export function stubLater(id: string): void {}\n\n"
        "export async function syncWorker(u: string): Promise<number> { return u.length; }\n\n"
        "export async function hazardousHandler(\n"
        "  rawPayload,\n"
        "  userInput: any,\n"
        "  a: number,\n"
        "  b: number,\n"
        "  c: number,\n"
        "  d: number,\n"
        "  items: string[] = [],\n"
        ") {\n"
        "  console.assert(true);\n"
        "  if (a < 0) { return; }\n"
        "  void syncWorker(String(a));\n"
        f'  const rawCfg = fs.readFileSync("{etc_path}", "utf-8");\n'
        "  const parsed = JSON.parse(rawPayload as unknown as string);\n"
        f"  const query = `{sql_prefix}${{userInput}}`;\n"
        "  eval(query);\n"
        "  exec(String(parsed));\n"
        '  const digest = crypto.createHash("md5").update(rawCfg).digest("hex");\n'
        "  const nonce = Math.random();\n"
        "  console.log(apiSecret, digest, nonce, items);\n"
        "  await fetch(String(userInput));\n"
        "  let flag = a > 0;\n"
        "  while (flag) { flag = false; }\n"
        "  if (a > 10) { return 1; }\n"
        "  else if (b > 10) { return 2; }\n"
        "  else if (c > 10) { return 3; }\n"
        "  try { globalCounter += d; } catch (err) {}\n"
        "  try { return globalCounter; } catch (err: unknown) {\n"
        '    throw new Error("lost context");\n'
        "  }\n"
        "}\n"
    )
    test_src = (
        'import { it, expect } from "vitest";\n'
        'it("theatre without assertions", () => {\n'
        "  const total = 2 + 2;\n"
        "  void total;\n"
        "});\n"
        'it.skip("skipped obligation", () => {\n'
        "  expect(4).toBe(4);\n"
        "});\n"
        'it("tautology", () => {\n'
        "  expect(true).toBe(true);\n"
        "});\n"
    )
    return (
        parse_typescript_unit("src/hazard.ts", prod_src),
        parse_typescript_unit("tests/hazard.test.ts", test_src),
    )


def test_typescript_adversarial_vs_clean_across_all_six_jbc_articles() -> None:
    ts_manifest = PolyglotManifest(typescript=_ts_test_manifest())
    violated = _inspect_polyglot_units(_build_adversarial_typescript_units(), manifest=ts_manifest)
    expected = {
        "JBC-DBC-001",
        "JBC-DBC-002",
        "JBC-DBC-003",
        "JBC-DBC-005",
        "JBC-FLT-001",
        "JBC-FLT-002",
        "JBC-FLT-004",
        "JBC-FLT-005",
        "JBC-FLT-006",
        "JBC-SEC-001",
        "JBC-SEC-002",
        "JBC-SEC-003",
        "JBC-SEC-004",
        "JBC-SEC-005",
        "JBC-SEC-006",
        "JBC-COG-002",
        "JBC-COG-004",
        "JBC-COG-005",
        "JBC-OBS-001",
        "JBC-OBS-002",
        "JBC-OBS-003",
        "JBC-VRF-001",
        "JBC-VRF-002",
        "JBC-VRF-004",
    }
    assert expected <= violated

    clean_ts = parse_typescript_unit(
        "src/escrow.ts",
        textwrap.dedent(
            """\
            import { z } from "zod";

            export const DepositCommandSchema = z.object({
              accountId: z.string().min(3),
              cents: z.number().int().positive(),
            }).strict();

            export type DepositCommand = z.infer<typeof DepositCommandSchema>;

            export class EscrowLedger {
              private balanceCents: number;

              public constructor(initialCents: number) {
                if (initialCents < 0) {
                  throw new RangeError("initialCents must be non-negative");
                }
                this.balanceCents = initialCents;
              }

              public credit(cmd: DepositCommand): number {
                const verified = DepositCommandSchema.parse(cmd);
                this.balanceCents += verified.cents;
                return this.balanceCents;
              }

              public snapshot(): number {
                return this.balanceCents;
              }
            }
            """
        ),
    )
    clean_test = parse_typescript_unit(
        "tests/escrow.test.ts",
        textwrap.dedent(
            """\
            import { it, expect } from "vitest";
            import { EscrowLedger } from "../src/escrow";

            it("credits positive integer cents onto balance", () => {
              const ledger = new EscrowLedger(100);
              const updated = ledger.credit({ accountId: "acc-01", cents: 250 });
              expect(updated).toBe(350);
            });
            """
        ),
    )
    assert _inspect_polyglot_units((clean_ts, clean_test), manifest=ts_manifest) == set()


# ---------------------------------------------------------------------------
# 4. Rust Adversarial vs. Compliant Coverage (Articles I-VI)
# ---------------------------------------------------------------------------


def _build_adversarial_rust_source() -> str:
    etc_path = _host_etc_shadow()
    sql_prefix = _sql_select_prefix()
    todo_note = _deferred_marker()
    ghp_secret = _synth_ghp_token()
    return (
        "#![allow(dead_code)]\n"
        f"// {todo_note}\n"
        "use std::any::Any;\n"
        "use std::fs;\n"
        "use std::process::Command;\n"
        "use serde::Deserialize;\n\n"
        "static mut GLOBAL_SEQ: i64 = 0;\n\n"
        "#[derive(Deserialize)]\n"
        "pub struct UnsealedPayload {\n"
        "    pub amount: i64,\n"
        "}\n\n"
        "pub struct SplitService {\n"
        "    left: i64,\n"
        "    right: i64,\n"
        "}\n\n"
        "impl SplitService {\n"
        "    pub fn get_left(&self) -> i64 { self.left }\n"
        "    pub fn get_right(&self) -> i64 { self.right }\n"
        "    pub fn stateless(&self, x: i64) -> i64 { x + 1 }\n"
        "}\n\n"
        "pub fn bad_rust_entry(user_id: &str, bag: serde_json::Value) -> i64 {\n"
        "    debug_assert!(true);\n"
        f'    let api_token = "{ghp_secret}";\n'
        f'    let raw = fs::read_to_string("{etc_path}").unwrap();\n'
        "    let _ = fs::remove_file(&raw);\n"
        "    let _ = raw.parse::<i64>().ok();\n"
        '    let _ = raw.parse::<i64>().map_err(|_| "dropped");\n'
        '    let _ = Command::new("sh").arg("-c").arg(user_id).status();\n'
        f'    let sql = format!("{sql_prefix}{{}}", user_id);\n'
        "    let _ = md5::compute(sql.as_bytes());\n"
        "    let _ = reqwest::blocking::get(user_id);\n"
        "    tokio::spawn(async move { let _ = bag; });\n"
        '    tracing::info!(api_token = api_token, "leaked");\n'
        '    println!("{}", raw);\n'
        "    let dyn_box: Box<dyn Any> = Box::new(1_i64);\n"
        "    let _ = dyn_box;\n"
        "    unsafe { GLOBAL_SEQ += 1; }\n"
        "    let mut n = 0;\n"
        "    loop { n += 1; if n > 1 { break; } }\n"
        "    if n == 1 { return 1; } else if n == 2 { return 2; } else if n == 3 { return 3; }\n"
        '    todo!("finish");\n'
        "}\n\n"
        "pub fn unguarded_unbounded(items: Vec<String>) -> usize {\n"
        '    std::env::set_var("MODE", "1");\n'
        "    items.len()\n"
        "}\n\n"
        "pub fn crashing_helper() {\n"
        "    panic!();\n"
        "}\n\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    #[test]\n"
        "    fn empty_test() {\n"
        "        let v = 1 + 1;\n"
        "        let _ = v;\n"
        "    }\n"
        "    #[test]\n"
        "    #[ignore]\n"
        "    fn ignored_test() {\n"
        "        assert_eq!(2, 2);\n"
        "    }\n"
        "}\n"
    )


def test_rust_adversarial_vs_clean_across_all_six_jbc_articles() -> None:
    rs_manifest = PolyglotManifest(
        rust=EcosystemManifest(
            runtime_roots=frozenset({"serde", "serde_json", "md5", "reqwest", "tokio", "tracing"}),
            workspace_roots=frozenset({"ledger"}),
        )
    )
    violated = _inspect_polyglot_units(
        (parse_rust_unit("src/lib.rs", _build_adversarial_rust_source()),),
        manifest=rs_manifest,
    )
    expected = {
        "JBC-DBC-001",
        "JBC-DBC-003",
        "JBC-DBC-005",
        "JBC-FLT-001",
        "JBC-FLT-002",
        "JBC-FLT-004",
        "JBC-FLT-005",
        "JBC-FLT-006",
        "JBC-SEC-001",
        "JBC-SEC-002",
        "JBC-SEC-003",
        "JBC-SEC-004",
        "JBC-SEC-005",
        "JBC-SEC-006",
        "JBC-COG-004",
        "JBC-COG-005",
        "JBC-OBS-001",
        "JBC-OBS-002",
        "JBC-OBS-003",
        "JBC-VRF-001",
        "JBC-VRF-002",
        "JBC-VRF-004",
    }
    assert expected <= violated

    clean_rs = parse_rust_unit(
        "src/ledger.rs",
        textwrap.dedent(
            """\
            use serde::Deserialize;

            #[derive(Debug, Clone, PartialEq, Eq)]
            pub enum LedgerError {
                NonPositiveDeposit(i64),
                InsufficientFunds { requested: i64, available: i64 },
            }

            #[derive(Debug, Deserialize)]
            #[serde(deny_unknown_fields)]
            pub struct TransferWire {
                pub cents: i64,
            }

            #[derive(Debug)]
            pub struct VaultAccount {
                balance_cents: i64,
            }

            impl VaultAccount {
                pub fn new(initial_cents: i64) -> Result<Self, LedgerError> {
                    if initial_cents < 0 {
                        return Err(LedgerError::NonPositiveDeposit(initial_cents));
                    }
                    Ok(Self { balance_cents: initial_cents })
                }

                pub fn deposit(&mut self, wire: &TransferWire) -> Result<i64, LedgerError> {
                    if wire.cents <= 0 {
                        return Err(LedgerError::NonPositiveDeposit(wire.cents));
                    }
                    self.balance_cents += wire.cents;
                    Ok(self.balance_cents)
                }
            }

            #[cfg(test)]
            mod tests {
                use super::{TransferWire, VaultAccount};

                #[test]
                fn deposit_increments_vault_balance() {
                    let mut acct = VaultAccount::new(500).expect("valid seed");
                    let wire = TransferWire { cents: 125 };
                    let updated = acct.deposit(&wire).expect("valid deposit");
                    assert_eq!(updated, 625);
                }
            }
            """
        ),
    )
    assert _inspect_polyglot_units((clean_rs,), manifest=rs_manifest) == set()


# ---------------------------------------------------------------------------
# 5. Go Adversarial vs. Compliant Coverage (Articles I-VI)
# ---------------------------------------------------------------------------


def _build_adversarial_go_units() -> tuple[InspectedUnit, ...]:
    etc_path = _host_etc_shadow()
    sql_prefix = _sql_select_prefix()
    todo_note = _deferred_marker()
    sk_secret = _synth_sk_token()
    go_src = (
        "package ledger\n\n"
        "//nolint:errcheck\n"
        f"// {todo_note}\n"
        "import (\n"
        '\t"crypto/md5"\n'
        '\t"encoding/json"\n'
        '\t"fmt"\n'
        '\t"io"\n'
        '\t"log/slog"\n'
        '\t"math/rand"\n'
        '\t"net/http"\n'
        '\t"os"\n'
        '\t"os/exec"\n'
        '\t"unsafe"\n'
        ")\n\n"
        "var GlobalMutableCounter = 0\n\n"
        "func init() { GlobalMutableCounter = 1 }\n\n"
        "type UnvalidatedDTO struct {\n"
        '\tAmount int `json:"amount"`\n'
        "}\n\n"
        "type DisconnectedSvc struct {\n"
        "\tleft  int\n"
        "\tright int\n"
        "}\n\n"
        "func (s *DisconnectedSvc) Left() int { return s.left }\n"
        "func (s *DisconnectedSvc) Right() int { return s.right }\n"
        "func (s *DisconnectedSvc) Stateless(v int) int { return v + 1 }\n\n"
        "func EmptyStub(x int) {}\n\n"
        "func UnguardedBatch(items []string) int { return len(items) }\n\n"
        "func HazardousGo(raw io.Reader, user string, payload any) (res int, err error) {\n"
        f'\tapiSecret := "{sk_secret}"\n'
        f'\tf, openErr := os.Open("{etc_path}")\n'
        "\tif openErr != nil {\n"
        '\t\treturn 0, fmt.Errorf("open failed: %v", openErr)\n'
        "\t}\n"
        "\t_ = f.Sync()\n"
        "\tbody, _ := io.ReadAll(raw)\n"
        "\tdec := json.NewDecoder(f)\n"
        "\tvar bag map[string]any\n"
        "\t_ = dec.Decode(&bag)\n"
        f'\tq := "{sql_prefix}" + user\n'
        '\t_ = exec.Command("sh", "-c", q)\n'
        "\t_ = md5.Sum(body)\n"
        "\t_ = rand.Int()\n"
        "\t_, _ = http.Get(user)\n"
        "\tgo EmptyStub(1)\n"
        '\tslog.Info("audit", "apiSecret", apiSecret)\n'
        "\tfmt.Println(payload)\n"
        "\tptr := unsafe.Pointer(&res)\n"
        "\t_ = ptr\n"
        "\tfor { break }\n"
        "\tif res == 1 { res = 2 } else if res == 2 { res = 3 } else if res == 3 { res = 4 }\n"
        "\treturn\n"
        "}\n"
    )
    go_test_src = (
        "package ledger\n\n"
        'import "testing"\n\n'
        "func TestSmokeTheatre(t *testing.T) {\n"
        "\tx := 1 + 1\n"
        "\t_ = x\n"
        "}\n\n"
        "func TestSkipped(t *testing.T) {\n"
        '\tt.Skip("deferred")\n'
        "}\n"
    )
    return (
        parse_go_unit("ledger/hazard.go", go_src),
        parse_go_unit("ledger/hazard_test.go", go_test_src),
    )


def test_go_adversarial_vs_clean_across_all_six_jbc_articles() -> None:
    go_manifest = PolyglotManifest(
        go=EcosystemManifest(
            module_prefix="github.com/acme/ledger",
            workspace_roots=frozenset({"ledger"}),
        )
    )
    violated = _inspect_polyglot_units(_build_adversarial_go_units(), manifest=go_manifest)
    expected = {
        "JBC-DBC-002",
        "JBC-DBC-003",
        "JBC-DBC-004",
        "JBC-DBC-005",
        "JBC-FLT-001",
        "JBC-FLT-002",
        "JBC-FLT-003",
        "JBC-FLT-004",
        "JBC-FLT-005",
        "JBC-FLT-006",
        "JBC-SEC-001",
        "JBC-SEC-002",
        "JBC-SEC-003",
        "JBC-SEC-005",
        "JBC-SEC-006",
        "JBC-COG-004",
        "JBC-COG-005",
        "JBC-OBS-001",
        "JBC-OBS-002",
        "JBC-OBS-003",
        "JBC-VRF-001",
        "JBC-VRF-002",
        "JBC-VRF-004",
    }
    assert expected <= violated

    clean_go = parse_go_unit(
        "ledger/account.go",
        textwrap.dedent(
            """\
            package ledger

            import (
            \t"bytes"
            \t"encoding/json"
            \t"errors"
            \t"fmt"
            )

            type CreditRequest struct {
            \tAccountID string `json:"account_id"`
            \tCents     int64  `json:"cents"`
            }

            func (r CreditRequest) Validate() error {
            \tif r.AccountID == "" || r.Cents <= 0 {
            \t\treturn errors.New("invalid credit request")
            \t}
            \treturn nil
            }

            func DecodeStrictCredit(payload []byte) (CreditRequest, error) {
            \tdec := json.NewDecoder(bytes.NewReader(payload))
            \tdec.DisallowUnknownFields()
            \tvar req CreditRequest
            \tif err := dec.Decode(&req); err != nil {
            \t\treturn CreditRequest{}, fmt.Errorf("decode credit: %w", err)
            \t}
            \tif err := req.Validate(); err != nil {
            \t\treturn CreditRequest{}, fmt.Errorf("validate credit: %w", err)
            \t}
            \treturn req, nil
            }
            """
        ),
    )
    clean_go_test = parse_go_unit(
        "ledger/account_test.go",
        textwrap.dedent(
            """\
            package ledger

            import "testing"

            func TestDecodeStrictCreditRejectsUnknownField(t *testing.T) {
            \t_, err := DecodeStrictCredit([]byte(`{"account_id":"a1","cents":50,"rogue":1}`))
            \tif err == nil {
            \t\tt.Fatalf("expected strict decoder to reject unknown field")
            \t}
            }
            """
        ),
    )
    assert _inspect_polyglot_units((clean_go, clean_go_test), manifest=go_manifest) == set()


# ---------------------------------------------------------------------------
# 6. Whole-Graph Polyglot JBC-COG-003 (Tarjan Cycles, Wildcards, Encapsulation)
# ---------------------------------------------------------------------------


def test_polyglot_cog_003_tarjan_cycles_wildcards_and_private_breaches() -> None:
    ts_a = parse_typescript_unit(
        "src/alpha.ts",
        'import { betaFn } from "./beta";\nexport * from "./beta";\n'
        "export function alphaFn(x: number): number { return betaFn(x); }\n",
    )
    ts_b = parse_typescript_unit(
        "src/beta.ts",
        'import { alphaFn } from "./alpha";\nimport { _secret } from "./vault/_internal";\n'
        "export function betaFn(x: number): number { return alphaFn(x) + _secret; }\n",
    )
    ts_priv = parse_typescript_unit("src/vault/_internal.ts", "export const _secret: number = 7;\n")

    rs_a = parse_rust_unit(
        "src/alpha.rs",
        "use crate::beta::*;\npub fn run_a(v: i64) -> i64 { run_b(v) }\n",
    )
    rs_b = parse_rust_unit(
        "src/beta.rs",
        "use crate::alpha::run_a;\npub fn run_b(v: i64) -> i64 { run_a(v) }\n",
    )

    go_a = parse_go_unit(
        "pkg/a/a.go",
        'package a\nimport (\n\t. "fmt"\n\t"github.com/acme/app/pkg/b"\n'
        '\t"github.com/acme/vendor/internal/secret"\n)\n'
        'func A(v int) int { Sprintf("%d", v); return b.B(v) + secret.X }\n',
    )
    go_b = parse_go_unit(
        "pkg/b/b.go",
        'package b\nimport "github.com/acme/app/pkg/a"\nfunc B(v int) int { return a.A(v) }\n',
    )

    findings = check_polyglot_cog_003((ts_a, ts_b, ts_priv, rs_a, rs_b, go_a, go_b))
    messages = "\n".join(item.message for item in findings)
    assert messages.count("Cyclic module/package dependency detected: alpha <-> beta.") == 2
    assert "Cyclic module/package dependency detected: pkg/a <-> pkg/b." in messages
    assert "Wildcard import './beta'" in messages
    assert "Wildcard import 'crate::beta::*'" in messages
    assert "Wildcard import 'fmt'" in messages
    assert "private/internal symbol '_secret'" in messages
    assert "private/internal symbol 'internal/secret'" in messages


# ---------------------------------------------------------------------------
# 7. Four-Language Polyglot PCE Crucible (CLI + MCP + Stop Hook + Sealed Dossier)
# ---------------------------------------------------------------------------


def _git_exec(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=False,
        timeout=15.0,
    )
    assert proc.returncode == 0, proc.stderr


def _seed_crucible_manifests(repo: Path) -> None:
    (repo / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "polyglot-crucible"
            version = "0.1.0"
            dependencies = ["icontract>=2.7", "pydantic>=2.10"]

            [dependency-groups]
            dev = ["pytest>=8.0"]
            """
        ),
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        json.dumps({"name": "crucible-ts", "dependencies": {"zod": "^3.23.0"}}),
        encoding="utf-8",
    )
    (repo / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/rs_core"]\n[workspace.dependencies]\nserde = "1.0"\n',
        encoding="utf-8",
    )
    (repo / "go.mod").write_text("module github.com/jezz/crucible\n\ngo 1.23\n", encoding="utf-8")
    permit = CodePermit.full_book(permit_id="PERMIT-POLYGLOT-CRUCIBLE")
    (repo / ".jezz" / "permit.json").write_text(
        permit.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _seed_four_language_crucible(root: Path) -> Path:
    repo = root / "crucible"
    for sub in (
        "src/py_ledger",
        "src/ts_gateway",
        "crates/rs_core/src",
        "internal/go_settle",
        "tests",
        ".jezz",
    ):
        (repo / sub).mkdir(parents=True)
    _seed_crucible_manifests(repo)

    (repo / "src" / "py_ledger" / "__init__.py").write_text(
        '"""Python ledger."""\n',
        encoding="utf-8",
    )
    (repo / "src" / "py_ledger" / "quota.py").write_text(
        textwrap.dedent(
            """\
            from typing import Annotated
            import icontract
            from pydantic import Field, validate_call

            type Cents = Annotated[int, Field(ge=0, le=10_000_000)]

            @icontract.ensure(lambda amount, bonus, result: result == amount + bonus)
            @validate_call(validate_return=True)
            def apply_bonus_cents(amount: Cents, bonus: Cents) -> Cents:
                \"\"\"Return deterministic sum of validated cent amounts.\"\"\"
                return amount + bonus
            """
        ),
        encoding="utf-8",
    )
    (repo / "tests" / "test_quota.py").write_text(
        textwrap.dedent(
            """\
            from py_ledger.quota import apply_bonus_cents

            def test_apply_bonus_cents_adds_amounts() -> None:
                assert apply_bonus_cents(250, 50) == 300
            """
        ),
        encoding="utf-8",
    )
    (repo / "src" / "ts_gateway" / "wire.ts").write_text(
        textwrap.dedent(
            """\
            import { z } from "zod";

            export const WireTransferSchema = z.object({
              transferId: z.string().min(4),
              cents: z.number().int().positive(),
            }).strict();

            export type WireTransfer = z.infer<typeof WireTransferSchema>;

            export function parseWireTransfer(candidate: WireTransfer): number {
              const verified = WireTransferSchema.parse(candidate);
              return verified.cents;
            }
            """
        ),
        encoding="utf-8",
    )
    (repo / "crates" / "rs_core" / "src" / "escrow.rs").write_text(
        textwrap.dedent(
            """\
            use serde::Deserialize;

            #[derive(Debug, Clone, PartialEq, Eq)]
            pub enum EscrowError {
                ZeroOrNegative(i64),
            }

            #[derive(Debug, Deserialize)]
            #[serde(deny_unknown_fields)]
            pub struct EscrowAllocation {
                pub cents: i64,
            }

            pub fn allocate_escrow(req: &EscrowAllocation) -> Result<i64, EscrowError> {
                if req.cents <= 0 {
                    return Err(EscrowError::ZeroOrNegative(req.cents));
                }
                Ok(req.cents)
            }
            """
        ),
        encoding="utf-8",
    )
    (repo / "internal" / "go_settle" / "receipt.go").write_text(
        textwrap.dedent(
            """\
            package go_settle

            import "errors"

            type SettlementReceipt struct {
            \tReceiptID string `json:"receipt_id"`
            \tNetCents  int64  `json:"net_cents"`
            }

            func (r SettlementReceipt) Validate() error {
            \tif r.ReceiptID == "" || r.NetCents < 0 {
            \t\treturn errors.New("invalid settlement receipt")
            \t}
            \treturn nil
            }

            func BuildReceipt(receiptID string, netCents int64) (SettlementReceipt, error) {
            \trec := SettlementReceipt{ReceiptID: receiptID, NetCents: netCents}
            \tif err := rec.Validate(); err != nil {
            \t\treturn SettlementReceipt{}, err
            \t}
            \treturn rec, nil
            }
            """
        ),
        encoding="utf-8",
    )

    _git_exec(repo, "init")
    _git_exec(repo, "config", "user.email", "inspector@jezz.dev")
    _git_exec(repo, "config", "user.name", "Jezz Inspector")
    _git_exec(repo, "add", ".")
    _git_exec(repo, "commit", "-m", "Seed clean 4-language PCE crucible")
    return repo


def _build_crucible_dossier(repo: Path, units: tuple[InspectedUnit, ...]) -> EngineeringDossier:
    permit = CodePermit.full_book(permit_id="PERMIT-POLYGLOT-CRUCIBLE")
    inspector = build_workspace_inspector(repo, units)
    attestations = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy+tsc+cargo-check+go-vet",
            passed=True,
            exit_code=0,
            summary="All 4 language typecheckers passed.",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest+vitest+cargo-test+go-test",
            passed=True,
            exit_code=0,
            checks_executed=12,
            duration_ms=420.0,
            max_duration_ms=30_000.0,
            peak_memory_kb=16_384,
            max_memory_kb=262_144,
            summary="All 4 language test suites passed.",
        ),
    )
    final_report = inspector.inspect_final(units, permit=permit, attestations=attestations)
    cert = issue_certificate(final_report, artifact_digest=compute_units_digest(units))
    mandate = EngineeringMandate(
        mandate_id="MND-POLYGLOT-CRUCIBLE-01",
        title="Four-Language Settlement Crucible",
        objective="Enforce strict wire schemas and total error contracts across Py, TS, Rust, Go.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Python cent bonuses equal the exact arithmetic sum of inputs.",
                verification_symbol="test_apply_bonus_cents_adds_amounts",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="TypeScript wire transfer parser enforces strict positive integer cents.",
                verification_symbol="ts_gateway.wire.parseWireTransfer",
            ),
            DomainInvariant(
                invariant_id="INV-003",
                statement="Rust escrow allocator rejects zero or negative cent allocations.",
                verification_symbol="escrow::allocate_escrow",
            ),
            DomainInvariant(
                invariant_id="INV-004",
                statement="Go receipt builder validates receipt invariants prior to return.",
                verification_symbol="internal.go_settle.BuildReceipt",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_python",
                owned_files=frozenset(
                    {"src/py_ledger/__init__.py", "src/py_ledger/quota.py", "tests/test_quota.py"}
                ),
                contract_symbols=("apply_bonus_cents", "test_apply_bonus_cents_adds_amounts"),
            ),
            ModuleWriteScope(
                scope_id="scope_typescript",
                owned_files=frozenset({"src/ts_gateway/wire.ts"}),
                contract_symbols=("parseWireTransfer",),
                depends_on=frozenset({"scope_python"}),
            ),
            ModuleWriteScope(
                scope_id="scope_rust",
                owned_files=frozenset({"crates/rs_core/src/escrow.rs"}),
                contract_symbols=("EscrowAllocation", "allocate_escrow"),
                depends_on=frozenset({"scope_python"}),
            ),
            ModuleWriteScope(
                scope_id="scope_go",
                owned_files=frozenset({"internal/go_settle/receipt.go"}),
                contract_symbols=("SettlementReceipt", "BuildReceipt"),
                depends_on=frozenset({"scope_typescript", "scope_rust"}),
            ),
        ),
    )
    return EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        mutated_files=mandate.authorized_files(),
        inspection_report=final_report,
        certificate=cert,
    )


async def test_crucible_four_language_cli_ast_graph_and_mcp_preview(tmp_path: Path) -> None:
    repo = _seed_four_language_crucible(tmp_path)
    units = discover_workspace_units(repo)
    assert len(units) == 6

    out_all, err_all = io.StringIO(), io.StringIO()
    assert main(["inspect", str(repo), "--format", "json"], stdout=out_all, stderr=err_all) == 0
    assert json.loads(out_all.getvalue())["ruling"] == str(OccupancyRuling.APPROVED)

    out_rs, err_rs = io.StringIO(), io.StringIO()
    assert (
        main(
            ["inspect", str(repo), "--language", "rust", "--format", "json"],
            stdout=out_rs,
            stderr=err_rs,
        )
        == 0
    )
    assert "Inspected 1 unit(s)" in out_rs.getvalue()

    graph = build_workspace_ast_graph(units)
    assert len(graph.modules) == 6
    assert graph.has_import_cycles is False
    assert "pub fn allocate_escrow" in extract_symbol_source_slice(units, "allocate_escrow")
    assert "func BuildReceipt" in extract_symbol_source_slice(units, "BuildReceipt")
    assert "export function parseWireTransfer" in extract_symbol_source_slice(
        units, "parseWireTransfer"
    )

    server = build_jezz_mcp_server(workspace_root=repo)
    async with Client(server, mode="legacy") as client:
        bad_ts = await client.call_tool(
            "jbc_preview_source",
            {
                "file_path": "src/ts_gateway/rogue.ts",
                "source_code": (
                    'import { parseWireTransfer } from "./wire";\n'
                    "export function leak(raw: string): any {\n"
                    "  console.log(parseWireTransfer);\n"
                    "  return JSON.parse(raw);\n"
                    "}\n"
                ),
            },
        )
        clean_rs = await client.call_tool(
            "jbc_preview_source",
            {
                "file_path": "crates/rs_core/src/fee.rs",
                "source_code": "pub fn fee_bps(cents: i64) -> i64 { cents / 100 }\n",
            },
        )
    assert bad_ts.is_error is False and '"ruling": "red_tag"' in str(bad_ts.content)
    assert clean_rs.is_error is False and '"ruling": "approved"' in str(clean_rs.content)
    assert not (repo / "src" / "ts_gateway" / "rogue.ts").exists()


def test_crucible_four_language_stop_hook_ratchet_and_sealed_dossier(tmp_path: Path) -> None:
    repo = _seed_four_language_crucible(tmp_path)
    units = discover_workspace_units(repo)

    rogue_rs = repo / "crates" / "rs_core" / "src" / "untracked_bad.rs"
    rogue_rs.write_text(
        "pub fn bad_parse(raw: &str) -> i64 {\n"
        "    let _ = raw.parse::<i64>();\n"
        "    raw.parse::<i64>().unwrap()\n"
        "}\n",
        encoding="utf-8",
    )
    hook_res = evaluate_stop_hook(
        StopHookInput(workspace_paths=(str(repo),)),
        config=StopHookConfig(require_jezz_marker=True),
    )
    assert hook_res.decision == StopHookDecisionKind.CONTINUE
    assert hook_res.reason is not None
    assert "JBC-DBC-003" in hook_res.reason and "JBC-FLT-001" in hook_res.reason

    diff_out, diff_err = io.StringIO(), io.StringIO()
    assert (
        main(["inspect", str(repo), "--changed-since", "HEAD"], stdout=diff_out, stderr=diff_err)
        == 1
    )
    rogue_rs.unlink()

    clean_hook = evaluate_stop_hook(
        StopHookInput(workspace_paths=(str(repo),)),
        config=StopHookConfig(require_jezz_marker=True),
    )
    assert clean_hook.decision == StopHookDecisionKind.ALLOW

    dossier = _build_crucible_dossier(repo, units)
    audit = audit_pce_dossier(dossier, units)
    assert audit.is_occupancy_sealed is True
    assert audit.highest_verified_phase == PcePhase.OCCUPANCY
    assert len(audit.verified_symbols) == 10

    dossier_path = repo / ".jezz" / "dossier.json"
    dossier_path.write_text(dossier.model_dump_json(indent=2) + "\n", encoding="utf-8")
    aud_out, aud_err = io.StringIO(), io.StringIO()
    assert (
        main(["audit", str(dossier_path), "--repo", str(repo)], stdout=aud_out, stderr=aud_err) == 0
    )
    assert "OCCUPANCY_SEALED" in aud_out.getvalue()

    gate = JezzWorkspaceGate(workspace_root=repo)
    assert gate.audit_dossier(dossier_path=".jezz/dossier.json").is_occupancy_sealed is True


# ---------------------------------------------------------------------------
# 8. Hypothesis Property Tests: Permutation Invariance Across 4 Languages
# ---------------------------------------------------------------------------


@settings(max_examples=25)
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_property_polyglot_digest_and_ast_graph_are_permutation_invariant(seed: int) -> None:
    base_units: list[InspectedUnit] = [
        parse_source_unit("src/mod_py.py", "def add_one(x: int) -> int:\n    return x + 1\n"),
        parse_typescript_unit(
            "src/mod_ts.ts",
            "export function addTwo(x: number): number { return x + 2; }\n",
        ),
        parse_rust_unit("src/mod_rs.rs", "pub fn add_three(x: i64) -> i64 { x + 3 }\n"),
        parse_go_unit("pkg/mod_go.go", "package pkg\nfunc AddFour(x int) int { return x + 4 }\n"),
        parse_any_source_unit(
            "src/view.tsx",
            "export function Badge(p: { readonly label: string }): string { return p.label; }\n",
        ),
    ]
    n = len(base_units)
    perm = [(i * 3 + seed) % n for i in range(n)]
    seen: list[InspectedUnit] = []
    for idx in perm:
        cand = base_units[idx]
        if cand not in seen:
            seen.append(cand)
    for item in base_units:
        if item not in seen:
            seen.append(item)

    shuffled = tuple(seen)
    canonical = tuple(base_units)
    assert compute_units_digest(shuffled) == compute_units_digest(canonical)
    g1 = build_workspace_ast_graph(canonical)
    g2 = build_workspace_ast_graph(shuffled)
    assert [m.file_path for m in g1.modules] == [m.file_path for m in g2.modules]
    assert [s.qualname for s in g1.symbols] == [s.qualname for s in g2.symbols]


# ---------------------------------------------------------------------------
# 9. Modern TS 4.7-5.0 Grammar Bridge, TSX Components, & Sunset Variances
# ---------------------------------------------------------------------------


def test_typescript_modern_47_to_50_grammar_bridge_and_negative_fixture_pruning(
    tmp_path: Path,
) -> None:
    modern_ts = textwrap.dedent(
        """\
        export type * from "./wire";

        export interface Codec<
          /** Variance comment */
          out Output = unknown,
          in Input = never,
        > {
          <K extends string>(key: K): Output
          <K extends number>(idx: K): Output
        }

        export function identityBox(v: Codec<string, string>): Codec<string, string> {
          return v;
        }
        """
    )
    unit = parse_typescript_unit("src/codec.ts", modern_ts)
    assert any(t.name == "Codec" for t in unit.types)
    assert any(fn.name == "identityBox" and fn.start_line == 12 for fn in unit.callables)
    assert check_polyglot_cog_003((unit,)) == []

    # Compiler-diagnostic negative fixture dirs (tests/ui, testdata) are pruned on discovery
    (tmp_path / "src").mkdir()
    (tmp_path / "tests" / "ui").mkdir(parents=True)
    (tmp_path / "internal" / "pkg" / "testdata").mkdir(parents=True)
    (tmp_path / "src" / "lib.rs").write_text("pub fn valid_fn() -> i64 { 42 }\n", encoding="utf-8")
    (tmp_path / "tests" / "ui" / "compile_err.rs").write_text(
        "fn broken_macro() { bad!({ ~ }); }\n",
        encoding="utf-8",
    )
    (tmp_path / "internal" / "pkg" / "testdata" / "bad.go").write_text(
        "package bad\nfunc ( {\n",
        encoding="utf-8",
    )
    discovered = discover_workspace_units(tmp_path)
    assert [u.file_path for u in discovered] == ["src/lib.rs"]


def test_tsx_component_inspection_catches_hazards_and_approves_typed_props() -> None:
    bad_tsx = parse_any_source_unit(
        "src/AmountBadge.tsx",
        textwrap.dedent(
            """\
            export function AmountBadge(props: any) {
              console.log(props);
              return <span>{JSON.parse(props.raw)}</span>;
            }
            """
        ),
    )
    assert {"JBC-DBC-002", "JBC-OBS-001", "JBC-SEC-004"} <= _inspect_polyglot_units((bad_tsx,))

    clean_tsx = parse_any_source_unit(
        "src/AmountBadge.tsx",
        textwrap.dedent(
            """\
            export interface AmountBadgeProps {
              readonly label: string;
              readonly cents: number;
            }

            export function AmountBadge(props: AmountBadgeProps): JSX.Element {
              return <span data-cents={props.cents}>{props.label}</span>;
            }
            """
        ),
    )
    assert _inspect_polyglot_units((clean_tsx,)) == set()


def test_polyglot_code_variance_waivers_and_sunset_expiration() -> None:
    with pytest.raises(ValueError, match="non-waivable"):
        CodeVariance(
            variance_id="VAR-LIFE-SAFETY",
            code_id="JBC-SEC-001",
            file_path="src/ffi_bridge.rs",
            authorized_by="principal.engineer@jezz.dev",
            justification="Attempt to waive non-waivable life-safety injection code.",
        )

    suppressed_rs = parse_rust_unit(
        "src/ffi_bridge.rs",
        "#[allow(dead_code)]\npub fn bridge_version() -> i64 { 1 }\n",
    )
    variance = CodeVariance(
        variance_id="VAR-RUST-ALLOW-01",
        code_id="JBC-VRF-004",
        file_path="src/ffi_bridge.rs",
        authorized_by="principal.engineer@jezz.dev",
        justification="Temporary dead_code allowance while FFI consumer crate lands.",
        expires_on=date(2026, 12, 31),
    )
    permit = CodePermit.full_book(permit_id="PERMIT-VAR-POLY", variances=(variance,))
    inspector = BuildingInspector()

    active_rep = inspector.inspect_rough_in(
        (suppressed_rs,),
        permit=permit,
        reference_date=date(2026, 11, 23),
    )
    assert active_rep.ruling == OccupancyRuling.APPROVED_WITH_VARIANCES
    assert active_rep.unwaived_violations() == ()

    expired_rep = inspector.inspect_rough_in(
        (suppressed_rs,),
        permit=permit,
        reference_date=date(2027, 1, 2),
    )
    assert expired_rep.ruling == OccupancyRuling.RED_TAG
    assert len(expired_rep.unwaived_violations()) == 1
    assert "Expired variance 'VAR-RUST-ALLOW-01'" in expired_rep.unwaived_violations()[0].message


def test_monorepo_package_json_and_delegated_test_assertions(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "acme-root", "private": True, "workspaces": ["packages/*"]}),
        encoding="utf-8",
    )
    sub_pkg = tmp_path / "packages" / "subpkg"
    sub_pkg.mkdir(parents=True)
    (sub_pkg / "package.json").write_text(
        json.dumps(
            {
                "name": "@acme/subpkg",
                "dependencies": {"valibot": "^1.0.0"},
                "devDependencies": {"vitest": "^4.0.0"},
            }
        ),
        encoding="utf-8",
    )
    eco = load_package_json_manifest(tmp_path)
    assert "valibot" in eco.runtime_roots
    assert "vitest" in eco.dev_roots
    assert "@acme/subpkg" in eco.workspace_roots

    ts_type_test = parse_typescript_unit(
        "tests/types.test.ts",
        'import { test, expectTypeOf } from "vitest";\n'
        'test("infers str", () => { expectTypeOf<string>().toEqualTypeOf<string>(); });\n',
    )
    rs_helper_test = parse_rust_unit(
        "src/codec.rs",
        "#[cfg(test)]\nmod tests {\n"
        "    fn test_roundtrip_ok() { assert_eq!(2 + 2, 4); }\n"
        "    #[test]\n    fn delegates_to_helper() { test_roundtrip_ok(); }\n"
        '    #[test]\n    #[should_panic]\n    fn panics_on_bounds() { panic!("boom"); }\n'
        "}\n",
    )
    go_helper_test = parse_go_unit(
        "pkg/codec_test.go",
        'package pkg\nimport "testing"\n'
        'func assertPositive(t *testing.T, v int) { if v <= 0 { t.Fatalf("bad %d", v) } }\n'
        "func TestDelegatedAssertion(t *testing.T) { assertPositive(t, 5) }\n",
    )
    manifest = PolyglotManifest(typescript=eco)
    assert (
        _inspect_polyglot_units(
            (ts_type_test, rs_helper_test, go_helper_test),
            manifest=manifest,
        )
        == set()
    )
