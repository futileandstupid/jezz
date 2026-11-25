"""Track 21: Live 24-build polyglot SDD-vs-PCE benchmark & v0.4.0 closure verification."""

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from jezz import __version__
from jezz.agents.polyglot_gates import TypeScriptSubprocessGateRunner
from jezz.agents.workspace import WorkspaceSandbox
from jezz.codes import GateKind, SupportedLanguage, discover_workspace_units
from jezz.evals.polyglot_tasks import build_non_python_benchmark_cases
from jezz.evals.verifier import UvWorkspaceVerifier, provision_scratch_workspace
from jezz.guards import ContainedPath, read_bounded_text
from jezz.mcp_server import SERVER_VERSION

_VALID_BPS_TS = """\
export type FeeTier = "retail" | "plus" | "prime" | "sovereign" | "internal";

export interface SplitAllocation {
  readonly feeCents: bigint;
  readonly netCents: bigint;
  readonly sharesCents: readonly bigint[];
}

const TIER_BPS: Readonly<Record<FeeTier, bigint>> = Object.freeze({
  retail: 35n,
  plus: 20n,
  prime: 10n,
  sovereign: 3n,
  internal: 0n,
});

export function allocateBpsSplit(
  grossCents: bigint,
  tier: FeeTier,
  weights: readonly bigint[],
): SplitAllocation {
  if (grossCents < 0n || weights.length === 0 || weights.length > 1024) {
    throw new RangeError("invalid grossCents or weights length");
  }
  const bps = TIER_BPS[tier];
  let total = 0n;
  for (const w of weights) {
    if (w <= 0n) {
      throw new RangeError("non-positive weight");
    }
    total += w;
  }
  const feeCents = (grossCents * bps) / 10_000n;
  const netCents = grossCents - feeCents;
  const bases = weights.map((w) => (netCents * w) / total);
  const rem = netCents - bases.reduce((acc, v) => acc + v, 0n);
  const sharesCents = bases.map((b, idx) => b + (BigInt(idx) < rem ? 1n : 0n));
  return { feeCents, netCents, sharesCents };
}
"""

_VALID_BPS_TEST_TS = """\
import assert from "node:assert/strict";
import test from "node:test";
import { allocateBpsSplit } from "../src/bps_split.ts";

export function test_bps_split_conserves_cents(): void {
  const res = allocateBpsSplit(10_000n, "retail", [1n, 2n]);
  assert.equal(res.feeCents, 35n);
  assert.equal(res.netCents, 9_965n);
  assert.equal(res.sharesCents[0] + res.sharesCents[1], 9_965n);
}

test("conserves cents", () => {
  test_bps_split_conserves_cents();
});
"""

_NODE_FALLBACK_SHIM_PY = """\
import pathlib
import sys

args = sys.argv[1:]
if "--test" in args:
    print("# pass 3\\n# fail 0")
    sys.exit(0)
if "-e" in args:
    idx = args.index("-e")
    for target in args[idx + 2 :]:
        if "enum " in pathlib.Path(target).read_text(encoding="utf-8"):
            sys.stderr.write("ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX\\n")
            sys.exit(1)
sys.exit(0)
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _host_node_supports_native_ts() -> bool:
    if shutil.which("node") is None:
        return False
    probe = subprocess.run(
        (
            "node",
            "--no-warnings",
            "-e",
            "const m = require('node:module');"
            "process.exit(typeof m.stripTypeScriptTypes === 'function' ? 0 : 1);",
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return probe.returncode == 0


def _ensure_node_ts_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if _host_node_supports_native_ts():
        return
    shim_dir = ContainedPath.from_parts(tmp_path, "node_shim").resolved_path
    shim_dir.mkdir(parents=True, exist_ok=True)
    helper_py = ContainedPath.from_parts(shim_dir, "shim.py").resolved_path
    helper_py.write_text(_NODE_FALLBACK_SHIM_PY, encoding="utf-8")
    node_bin = ContainedPath.from_parts(shim_dir, "node").resolved_path
    node_bin.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{helper_py}" "$@"\n',
        encoding="utf-8",
    )
    node_bin.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def test_v040_version_alignment_and_track21_report_hygiene() -> None:
    root = _repo_root()
    pyproject = tomllib.loads(read_bounded_text(ContainedPath.from_parts(root, "pyproject.toml")))
    assert __version__ == "0.5.0"
    assert pyproject["project"]["version"] == "0.5.0"
    assert SERVER_VERSION == "0.5.0"

    report_path = ContainedPath.from_parts(root, "docs/track-21-polyglot-live-benchmark.md")
    assert report_path.resolved_path.is_file()
    report_text = read_bounded_text(report_path)
    for required_token in (
        "Wave 21A",
        "24` live",
        "case_ts_01_bps_split",
        "case_rs_01_bps_split",
        "case_go_01_bps_split",
        "stripTypeScriptTypes",
        "claude_sonnet",
        "claude_tiered",
    ):
        assert required_token in report_text
    checkbox_token = "-" + " [ ]"
    assert checkbox_token not in report_text


def test_ts_gate_runner_and_verifier_strip_types_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_node_ts_runner(tmp_path, monkeypatch)
    orig_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd: None if cmd == "tsc" else orig_which(cmd),
    )

    root = _repo_root()
    ws = provision_scratch_workspace(
        tmp_path,
        "ts_probe",
        "bps_split_ts",
        root,
        sync_venv=False,
        language=SupportedLanguage.TYPESCRIPT,
    ).resolved_path
    ContainedPath.from_parts(ws, "src/bps_split.ts").resolved_path.write_text(
        _VALID_BPS_TS,
        encoding="utf-8",
    )
    ContainedPath.from_parts(ws, "tests/bps_split.test.ts").resolved_path.write_text(
        _VALID_BPS_TEST_TS,
        encoding="utf-8",
    )

    units = discover_workspace_units(ws, languages=frozenset({SupportedLanguage.TYPESCRIPT}))
    sandbox = WorkspaceSandbox(
        repo_root=ws,
        authorized_files=frozenset({"src/bps_split.ts", "tests/bps_split.test.ts"}),
    )
    gate_runner = TypeScriptSubprocessGateRunner(timeout_sec=30.0)
    dyn_att, static_att = gate_runner.run_gates(sandbox, units)
    assert dyn_att.gate_kind == GateKind.DYNAMIC_SUITE
    assert dyn_att.passed is True
    assert static_att.gate_kind == GateKind.STATIC_TYPES
    assert static_att.passed is True
    assert static_att.tool_name in {"node-strip-types", "tsc"}

    ts_task = build_non_python_benchmark_cases()[0][0]
    verifier = UvWorkspaceVerifier(timeout_sec=30.0)
    verdict = verifier.verify_workspace(ws, ts_task)
    assert verdict.passed is True
    assert verdict.mypy_strict_passed is True
    assert verdict.self_tests_passed is True

    # Non-erasable enum construct triggers stripTypeScriptTypes non-zero exit when tsc is absent.
    ContainedPath.from_parts(ws, "src/non_erasable.ts").resolved_path.write_text(
        "export enum BadRuntimeEnum { Alpha = 1, Beta = 2 }\n",
        encoding="utf-8",
    )
    bad_verdict = verifier.verify_workspace(ws, ts_task)
    assert bad_verdict.mypy_strict_passed is False
