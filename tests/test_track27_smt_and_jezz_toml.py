"""Track 27: Polyglot AST/CST -> Z3 SMT contract prover and jezz.toml configuration tests."""

import io
import json
import textwrap
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from mcp import Client
from mcp.types import TextContent

from jezz.agents.routing import discover_gates_config, discover_models_config
from jezz.behaviors import build_greenfield_contract_first_graph
from jezz.cli import main
from jezz.codes import (
    BuildingInspector,
    CodePermit,
    ContractProofStatus,
    GateAttestation,
    GateKind,
    SmtProofReport,
    compute_units_digest,
    discover_workspace_units,
    extract_unit_obligations,
    issue_certificate,
    parse_go_unit,
    parse_rust_unit,
    parse_source_unit,
    parse_typescript_unit,
    prove_obligations,
    prove_workspace_contracts,
)
from jezz.guards import ContainedPath
from jezz.mcp_server import build_jezz_mcp_server, dispatch_reloaded_mcp_call
from jezz.methodology import (
    DomainInvariant,
    DossierViolationCode,
    EngineeringDossier,
    EngineeringMandate,
    ModuleWriteScope,
    audit_pce_dossier,
)
from jezz.secrets import discover_secrets_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_rel(root: Path, rel_path: str, content: str) -> Path:
    target = ContainedPath.from_parts(root, rel_path).resolved_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _first_text(blocks: list[object]) -> str:
    for blk in blocks:
        if isinstance(blk, TextContent):
            return blk.text
    return ""


def test_polyglot_deductive_proofs_across_python_ts_rust_and_go() -> None:
    """INV-027-01 & INV-027-02: Pure contracts across .py, .ts, .rs, .go prove UNSAT via Z3."""
    py_unit = parse_source_unit(
        "src/ledger/fees.py",
        textwrap.dedent(
            """\
            import icontract
            from jezz.guards import (
                BoundedInt,
                NonNegativeInt,
                PositiveInt,
                ShortText,
                jezz_callable,
            )

            @icontract.require(lambda bps: 0 <= bps <= 10_000)
            @icontract.ensure(lambda amount, result: 0 <= result <= amount)
            @jezz_callable
            def net_after_fee(amount: PositiveInt, bps: BoundedInt) -> NonNegativeInt:
                fee = (amount * bps) // 10_000
                return amount - fee

            @icontract.require(lambda uri: uri.startswith("op://"))
            @icontract.ensure(lambda result: len(result) >= 5)
            @jezz_callable
            def echo_op_uri(uri: ShortText) -> ShortText:
                return uri
            """
        ),
    )
    ts_unit = parse_typescript_unit(
        "src/pricing.ts",
        textwrap.dedent(
            """\
            import { z } from "zod";

            export const FeeQuoteSchema = z.object({
              amount: z.number().nonnegative().max(1000000),
              bps: z.number().int().min(0).max(10000),
            }).strict();

            // @requires: amount >= 0 && discount >= 0
            // @ensures: result >= 0 && result <= amount
            export function applyDiscount(amount: number, discount: number): number {
              if (amount < 0 || discount < 0) {
                throw new RangeError("negative input");
              }
              const candidate = amount - discount;
              return candidate < 0 ? 0 : candidate;
            }
            """
        ),
    )
    rs_unit = parse_rust_unit(
        "src/settlement.rs",
        textwrap.dedent(
            """\
            // @ensures: result <= gross && result >= 0
            pub fn settle_bps(gross: i64, bps: i64) -> Result<i64, &'static str> {
                if gross < 0 || bps < 0 || bps > 10000 {
                    return Err("out of bounds");
                }
                let fee = (gross * bps) / 10000;
                debug_assert!(fee >= 0 && fee <= gross);
                Ok(gross - fee)
            }
            """
        ),
    )
    go_unit = parse_go_unit(
        "internal/quota/quota.go",
        textwrap.dedent(
            """\
            package quota

            import "errors"

            // @ensures: result >= 0 && result <= limit
            func RemainingQuota(limit int64, used int64) (int64, error) {
                if limit < 0 || used < 0 {
                    return 0, errors.New("negative")
                }
                if used >= limit {
                    return 0, nil
                }
                rem := limit - used
                if rem <= 0 {
                    panic("unreachable non-positive remainder")
                }
                return rem, nil
            }
            """
        ),
    )

    report = prove_workspace_contracts((py_unit, ts_unit, rs_unit, go_unit))
    assert not report.has_failures
    by_sym = {r.symbol: r for r in report.results}
    assert by_sym["net_after_fee"].status == ContractProofStatus.PROVED_DEDUCTIVE
    assert by_sym["echo_op_uri"].status == ContractProofStatus.PROVED_DEDUCTIVE
    assert by_sym["FeeQuoteSchema"].status == ContractProofStatus.BOUNDED_BY_PBT
    assert by_sym["applyDiscount"].status == ContractProofStatus.PROVED_DEDUCTIVE
    assert by_sym["settle_bps"].status == ContractProofStatus.PROVED_DEDUCTIVE
    assert by_sym["RemainingQuota"].status == ContractProofStatus.PROVED_DEDUCTIVE


def test_counterexample_extraction_and_vacuous_contract_detection() -> None:
    """INV-027-02: Z3 isolates concrete off-by-one/div-zero witnesses and vacuous contracts."""
    buggy_py = parse_source_unit(
        "src/pkg/buggy.py",
        textwrap.dedent(
            """\
            import icontract
            from pydantic import BaseModel, Field
            from typing import Annotated
            from jezz.guards import STRICT_FROZEN_CONFIG, NonNegativeInt, PositiveInt, jezz_callable

            class ImpossibleConfig(BaseModel):
                model_config = STRICT_FROZEN_CONFIG
                min_items: Annotated[int, Field(ge=50, le=10)]

            @icontract.require(lambda x: x > 100 and x < 20)
            @jezz_callable
            def vacuous_fn(x: PositiveInt) -> PositiveInt:
                return x

            @icontract.ensure(lambda amount, fee, result: result >= 0)
            @jezz_callable
            def buggy_subtract(amount: PositiveInt, fee: PositiveInt) -> NonNegativeInt:
                return amount - fee

            @icontract.ensure(lambda num, den, result: result >= 0)
            @jezz_callable
            def unguarded_div(num: NonNegativeInt, den: NonNegativeInt) -> NonNegativeInt:
                return num // den
            """
        ),
    )
    bad_zod_ts = parse_typescript_unit(
        "src/bad_schema.ts",
        textwrap.dedent(
            """\
            import { z } from "zod";
            export const BrokenRange = z.object({
              score: z.number().min(100).max(5),
            }).strict();
            """
        ),
    )

    rep = prove_workspace_contracts((buggy_py, bad_zod_ts))
    assert rep.has_failures
    by_sym = {r.symbol: r for r in rep.results}
    assert by_sym["ImpossibleConfig"].status == ContractProofStatus.VACUOUS_CONTRACT
    assert by_sym["vacuous_fn"].status == ContractProofStatus.VACUOUS_CONTRACT
    assert by_sym["BrokenRange"].status == ContractProofStatus.VACUOUS_CONTRACT

    sub_cex = by_sym["buggy_subtract"]
    assert sub_cex.status == ContractProofStatus.COUNTEREXAMPLE_FOUND
    assert int(sub_cex.counterexample["amount"]) - int(sub_cex.counterexample["fee"]) < 0

    div_cex = by_sym["unguarded_div"]
    assert div_cex.status == ContractProofStatus.COUNTEREXAMPLE_FOUND
    assert int(div_cex.counterexample["den"]) == 0


@settings(max_examples=25)
@given(
    lo=st.integers(min_value=-500, max_value=500),
    hi=st.integers(min_value=-500, max_value=500),
)
def test_property_clamp_contract_proves_iff_bounds_ordered(lo: int, hi: int) -> None:
    """Property: clamp(x, lo, hi) is PROVED_DEDUCTIVE when lo <= hi and VACUOUS when lo > hi."""
    unit = parse_source_unit(
        "src/pkg/clamp.py",
        textwrap.dedent(
            f"""\
            import icontract
            from jezz.guards import BoundedInt, jezz_callable

            @icontract.ensure(lambda result: {lo} <= result <= {hi})
            @jezz_callable
            def clamp_val(x: BoundedInt) -> BoundedInt:
                low = max(x, {lo})
                return min(low, {hi})
            """
        ),
    )
    rep = prove_obligations(extract_unit_obligations(unit))
    assert len(rep.results) == 1
    status = rep.results[0].status
    if lo <= hi:
        assert status == ContractProofStatus.PROVED_DEDUCTIVE
    else:
        assert status == ContractProofStatus.VACUOUS_CONTRACT


async def test_cli_mcp_and_dossier_smt_seal_integration(tmp_path: Path) -> None:
    """INV-027-02: CLI 'jezz prove', MCP 'jbc_prove_contracts', and Dossier SMT seal audit."""
    _write_rel(
        tmp_path,
        "pyproject.toml",
        textwrap.dedent(
            """\
            [project]
            name = "smt-demo"
            version = "0.1.0"
            dependencies = ["icontract>=2.7.3", "jezz>=0.5.0", "pydantic>=2.14.4"]

            [dependency-groups]
            dev = ["pytest>=9.2.0"]
            """
        ),
    )
    _write_rel(tmp_path, "src/smt_demo/__init__.py", '"""SMT demo."""\n')
    _write_rel(
        tmp_path,
        "src/smt_demo/math_ops.py",
        textwrap.dedent(
            """\
            import icontract
            from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

            @icontract.ensure(lambda x, result: result == x * 3 and result >= 3)
            @jezz_callable
            def triple_pos(x: PositiveInt) -> NonNegativeInt:
                return x * 3
            """
        ),
    )
    _write_rel(
        tmp_path,
        "tests/test_math_ops.py",
        textwrap.dedent(
            """\
            from smt_demo.math_ops import triple_pos

            def test_triple() -> None:
                assert triple_pos(4) == 12
            """
        ),
    )

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = main(
        [
            "prove",
            str(tmp_path),
            "--require-deductive",
            "--write-json",
            "smt_report.json",
            "--format",
            "markdown",
        ],
        stdout=out_buf,
        stderr=err_buf,
    )
    assert rc == 0
    assert "PROVED_DEDUCTIVE" in out_buf.getvalue()
    saved_rep = SmtProofReport.model_validate_json(
        (tmp_path / "smt_report.json").read_text(encoding="utf-8")
    )
    assert saved_rep.proved_deductive_count == 1

    server = build_jezz_mcp_server(workspace_root=tmp_path)
    async with Client(server, mode="legacy") as client:
        ok_res = await client.call_tool("jbc_prove_contracts", {})
        bad_preview = await client.call_tool(
            "jbc_prove_contracts",
            {
                "file_path": "src/smt_demo/math_ops.py",
                "source_code": (
                    "import icontract\n"
                    "from jezz.guards import PositiveInt, jezz_callable\n"
                    "@icontract.ensure(lambda x, result: result > x * 3)\n"
                    "@jezz_callable\n"
                    "def triple_pos(x: PositiveInt) -> PositiveInt:\n"
                    "    return x * 3\n"
                ),
            },
        )
    mcp_ok = SmtProofReport.model_validate_json(_first_text(list(ok_res.content)))
    assert mcp_ok.proved_deductive_count == 1
    mcp_bad = SmtProofReport.model_validate_json(_first_text(list(bad_preview.content)))
    assert mcp_bad.counterexample_count == 1

    worker_raw = dispatch_reloaded_mcp_call(
        tmp_path,
        "jbc_prove_contracts",
        json.dumps({"smt_prove_args": {"workspace_subpath": "."}}),
    )
    assert SmtProofReport.model_validate_json(worker_raw).proved_deductive_count == 1

    units = discover_workspace_units(tmp_path)
    inspector = BuildingInspector.from_workspace(tmp_path)
    permit = CodePermit.full_book(permit_id="PERMIT-SMT")
    insp = inspector.inspect_final(
        units,
        attestations=(
            GateAttestation(
                gate_kind=GateKind.STATIC_TYPES,
                tool_name="mypy",
                passed=True,
                exit_code=0,
                checks_executed=2,
                summary="ok",
            ),
            GateAttestation(
                gate_kind=GateKind.DYNAMIC_SUITE,
                tool_name="pytest",
                passed=True,
                exit_code=0,
                checks_executed=1,
                summary="ok",
            ),
        ),
        permit=permit,
    )
    cert = issue_certificate(
        insp,
        artifact_digest=compute_units_digest(units),
        smt_proof_digest=saved_rep.proof_digest,
        proved_deductive_count=saved_rep.proved_deductive_count,
    )
    assert cert.verify_seal()
    dossier = EngineeringDossier(
        mandate=EngineeringMandate(
            mandate_id="MND-SMT-01",
            title="Verified triple contract",
            objective="Prove triple_pos deductively via Z3.",
            invariants=(
                DomainInvariant(
                    invariant_id="INV-2701",
                    statement="triple_pos triples positive inputs.",
                    verification_symbol="test_triple",
                ),
            ),
            write_scopes=(
                ModuleWriteScope(
                    scope_id="math_scope",
                    owned_files=frozenset({"src/smt_demo/math_ops.py", "tests/test_math_ops.py"}),
                    contract_symbols=("triple_pos",),
                ),
            ),
        ),
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        mutated_files=frozenset({"src/smt_demo/math_ops.py", "tests/test_math_ops.py"}),
        require_smt_verification=True,
        inspection_report=insp,
        certificate=cert,
    )
    audit = audit_pce_dossier(dossier, units)
    assert audit.is_occupancy_sealed

    tampered_cert = issue_certificate(
        insp,
        artifact_digest=compute_units_digest(units),
        smt_proof_digest="sha256:" + "0" * 64,
        proved_deductive_count=99,
    )
    bad_audit = audit_pce_dossier(dossier.model_copy(update={"certificate": tampered_cert}), units)
    assert DossierViolationCode.INVALID_OCCUPANCY_SEAL in {v.code for v in bad_audit.violations}


def test_jezz_toml_precedence_and_self_hosted_smt_report(tmp_path: Path) -> None:
    """INV-027-03: jezz.toml works without pyproject.toml, overrides it, and rejects ambiguity."""
    rust_ws = tmp_path / "rust_svc"
    _write_rel(
        rust_ws,
        "jezz.toml",
        textwrap.dedent(
            """\
            [models]
            default = "antigravity:claude-sonnet-5-5-medium"
            architect = "antigravity:claude-opus-5-5-medium"

            [gates]
            timeout_sec = 90.0

            [secrets.providers]
            google = "env://GEMINI_API_KEY"
            """
        ),
    )
    models_cfg = discover_models_config((rust_ws,))
    gates_cfg = discover_gates_config((rust_ws,))
    secrets_cfg = discover_secrets_config((rust_ws,))
    assert models_cfg is not None and models_cfg.architect == "antigravity:claude-opus-5-5-medium"
    assert gates_cfg is not None and gates_cfg.timeout_sec == 90.0
    assert secrets_cfg is not None and secrets_cfg.providers["google"] == "env://GEMINI_API_KEY"

    _write_rel(
        rust_ws,
        "pyproject.toml",
        '[tool.jezz.models]\ndefault = "google:gemini-3.8-flash"\n',
    )
    override_cfg = discover_models_config((rust_ws,))
    assert (
        override_cfg is not None and override_cfg.default == "antigravity:claude-sonnet-5-5-medium"
    )

    ambig_ws = tmp_path / "ambig"
    _write_rel(
        ambig_ws,
        "jezz.toml",
        '[models]\ndefault = "test"\n[tool.jezz.models]\ndefault = "test"\n',
    )
    with pytest.raises(ValueError, match="Ambiguous"):
        discover_models_config((ambig_ws,))

    self_units = discover_workspace_units(REPO_ROOT)
    self_rep = prove_workspace_contracts(self_units)
    assert not self_rep.has_failures
    assert self_rep.proved_deductive_count >= 1


def test_polyglot_branching_expressions_and_dossier_smt_failure(tmp_path: Path) -> None:
    """INV-027-01 & INV-027-02: Rust if-expr/else-if, string len, and Dossier SMT failure gate."""
    rs_branch = parse_rust_unit(
        "src/tier.rs",
        textwrap.dedent(
            """\
            // @ensures: result >= 0 && result <= 200
            pub fn tier_bonus(score: i32, tag: &str) -> u32 {
                let bonus = if score > 100 { 200 } else { 50 };
                if tag.len() == 0 {
                    return 0;
                } else if score < 0 {
                    return 10;
                }
                bonus
            }
            """
        ),
    )
    rep = prove_workspace_contracts((rs_branch,), symbol_filter="tier_bonus")
    assert len(rep.results) == 1
    assert rep.results[0].status == ContractProofStatus.PROVED_DEDUCTIVE

    _write_rel(
        tmp_path,
        "pyproject.toml",
        textwrap.dedent(
            """\
            [project]
            name = "bad-smt"
            version = "0.1.0"
            dependencies = ["icontract>=2.7.3", "jezz>=0.5.0", "pydantic>=2.14.4"]

            [dependency-groups]
            dev = ["pytest>=9.2.0"]
            """
        ),
    )
    _write_rel(tmp_path, "src/bad_smt/__init__.py", '"""Pkg."""\n')
    _write_rel(
        tmp_path,
        "src/bad_smt/ops.py",
        textwrap.dedent(
            """\
            import icontract
            from jezz.guards import BoundedInt, PositiveInt, jezz_callable

            @icontract.ensure(lambda x, result: result > x)
            @jezz_callable
            def broken_inc(x: PositiveInt) -> BoundedInt:
                if x > 5:
                    y = abs(-x) - 1
                else:
                    y = x + 1
                return y
            """
        ),
    )
    _write_rel(
        tmp_path,
        "tests/test_ops.py",
        textwrap.dedent(
            """\
            from bad_smt.ops import broken_inc

            def test_inc() -> None:
                assert broken_inc(1) == 2
            """
        ),
    )

    out_s = io.StringIO()
    err_s = io.StringIO()
    fail_rc = main(["prove", str(tmp_path)], stdout=out_s, stderr=err_s)
    assert fail_rc == 1
    assert "COUNTEREXAMPLE_FOUND" in out_s.getvalue()

    units = discover_workspace_units(tmp_path)
    inspector = BuildingInspector.from_workspace(tmp_path)
    permit = CodePermit.full_book(permit_id="PERMIT-BAD-SMT")
    insp = inspector.inspect_final(
        units,
        attestations=(
            GateAttestation(
                gate_kind=GateKind.STATIC_TYPES,
                tool_name="mypy",
                passed=True,
                exit_code=0,
                checks_executed=2,
                summary="ok",
            ),
            GateAttestation(
                gate_kind=GateKind.DYNAMIC_SUITE,
                tool_name="pytest",
                passed=True,
                exit_code=0,
                checks_executed=1,
                summary="ok",
            ),
        ),
        permit=permit,
    )
    cert = issue_certificate(insp, artifact_digest=compute_units_digest(units))
    dossier = EngineeringDossier(
        mandate=EngineeringMandate(
            mandate_id="MND-BAD-SMT",
            title="Broken increment check",
            objective="Verify audit_pce_dossier catches Z3 counterexample.",
            invariants=(
                DomainInvariant(
                    invariant_id="INV-2702",
                    statement="broken_inc strictly exceeds x.",
                    verification_symbol="test_inc",
                ),
            ),
            write_scopes=(
                ModuleWriteScope(
                    scope_id="ops_scope",
                    owned_files=frozenset({"src/bad_smt/ops.py", "tests/test_ops.py"}),
                    contract_symbols=("broken_inc",),
                ),
            ),
        ),
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        mutated_files=frozenset({"src/bad_smt/ops.py", "tests/test_ops.py"}),
        require_smt_verification=True,
        inspection_report=insp,
        certificate=cert,
    )
    audit = audit_pce_dossier(dossier, units)
    assert not audit.is_occupancy_sealed
    assert DossierViolationCode.SMT_CONTRACT_FAILURE in {v.code for v in audit.violations}
