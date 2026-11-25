"""Track 29: Empirical reproducibility suite and CI-verified in-repo ``examples/``."""

import io
import json
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from jezz.cli import main as run_cli
from jezz.codes import CodePermit, CodeVariance, build_workspace_inspector, discover_workspace_units
from jezz.codes.permits import OccupancyRuling
from jezz.guards import ContainedPath, read_bounded_text

REPO_ROOT = Path(__file__).resolve().parents[1]
_HOST_ROOT_TOKENS = ("/" + "Users" + "/", "/" + "home" + "/", "C:" + "\\\\")


def _run_bench_script(script_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    bench_dir = ContainedPath.from_parts(REPO_ROOT, "benchmarks").resolved_path
    script = ContainedPath.from_parts(bench_dir, script_name).resolved_path
    src_dir = ContainedPath.from_parts(REPO_ROOT, "src").resolved_path
    env = {**os.environ, "PYTHONPATH": str(src_dir)}
    return subprocess.run(
        (sys.executable, str(script), *args),
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_brownfield_lockfile_and_offline_reproducer_cli(tmp_path: Path) -> None:
    """INV-029-01: Pinned brownfield lockfile validates and reproduces offline checkouts."""
    lock_file = ContainedPath.from_parts(REPO_ROOT, "benchmarks/brownfield_corpus.lock.json")
    lock_data = json.loads(read_bounded_text(lock_file))
    repos = lock_data["repositories"]
    assert len(repos) >= 8
    for entry in repos:
        assert str(entry["repo_url"]).startswith("https://github.com/")
        assert len(str(entry["commit_or_tag"])) >= 4

    # Seed a deterministic Go repository exhibiting a LIFE_SAFETY JBC-FLT-001 violation
    fake_chi = ContainedPath.from_parts(tmp_path, "fake_chi").resolved_path
    fake_chi.mkdir(parents=True, exist_ok=True)
    ContainedPath.from_parts(fake_chi, "go.mod").resolved_path.write_text(
        "module github.com/go-chi/chi/v5\n\ngo 1.23\n",
        encoding="utf-8",
    )
    ContainedPath.from_parts(fake_chi, "router.go").resolved_path.write_text(
        'package chi\nimport "strconv"\n'
        "func ParsePort(raw string) int {\n"
        "  if len(raw) == 0 { return 0 }\n"
        "  val, _ := strconv.Atoi(raw)\n"
        "  return val\n"
        "}\n",
        encoding="utf-8",
    )
    proc = _run_bench_script(
        "reproduce_brownfield.py",
        "--repo-id",
        "go_chi",
        "--local-checkout",
        f"go_chi={fake_chi}",
        "--format",
        "json",
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    payload = json.loads(proc.stdout)
    assert len(payload["outcomes"]) == 1
    row = payload["outcomes"][0]
    assert row["observed_ruling"] == "red_tag"
    assert row["ruling_matches"] is True
    assert row["life_safety_violations"] >= 1


def test_published_telemetry_claims_verifier_and_container_hygiene(tmp_path: Path) -> None:
    """INV-029-02: Recomputed telemetry matches published claims and rejects tampering."""
    proc = _run_bench_script("verify_published_claims.py", "--format", "json")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    report = json.loads(proc.stdout)
    assert report["billed_accounting_consistent"] is True
    assert len(report["verdicts"]) >= 14
    assert all(v["passed"] is True for v in report["verdicts"])

    # Tamper with one headline claim and verify fail-closed exit code 1
    orig = json.loads(
        read_bounded_text(ContainedPath.from_parts(REPO_ROOT, "benchmarks/published_metrics.json"))
    )
    orig["headline_claims"]["polyglot_pce_occupancy_pct"] = 91.0
    tampered_file = ContainedPath.from_parts(tmp_path, "tampered_metrics.json").resolved_path
    tampered_file.write_text(json.dumps(orig), encoding="utf-8")

    bad_proc = _run_bench_script("verify_published_claims.py", "--metrics-file", str(tampered_file))
    assert bad_proc.returncode == 1

    for rel in ("benchmarks/Containerfile", "benchmarks/README.md"):
        txt = read_bounded_text(ContainedPath.from_parts(REPO_ROOT, rel))
        assert not any(tok in txt for tok in _HOST_ROOT_TOKENS)
    readme_txt = read_bounded_text(ContainedPath.from_parts(REPO_ROOT, "benchmarks/README.md"))
    for tier_tok in ("smoke", "core", "full"):
        assert tier_tok in readme_txt


def test_examples_01_and_02_red_tag_remediation_and_blueprint_lvs() -> None:
    """INV-029-03 (01 & 02): Gallery RED_TAG vs APPROVED and Blueprint LVS SHORT_CIRCUIT."""
    broken_dir = ContainedPath.from_parts(
        REPO_ROOT, "examples/01-red-tag-gallery/broken"
    ).resolved_path
    out_bad = io.StringIO()
    assert run_cli(("inspect", str(broken_dir)), stdout=out_bad, stderr=io.StringIO()) == 1
    bad_md = out_bad.getvalue()
    assert "JBC-SEC-001" in bad_md
    assert "JBC-FLT-001" in bad_md
    assert "JBC-SEC-003" in bad_md

    rem_dir = ContainedPath.from_parts(
        REPO_ROOT, "examples/01-red-tag-gallery/remediated"
    ).resolved_path
    assert run_cli(("inspect", str(rem_dir)), stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert (
        run_cli(
            ("prove", str(rem_dir), "--require-deductive"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == 0
    )

    rec_dir = ContainedPath.from_parts(
        REPO_ROOT, "examples/02-blueprint-lvs/reconciled"
    ).resolved_path
    target_bp = ContainedPath.from_parts(rec_dir, "target_blueprint.json").resolved_path
    assert (
        run_cli(
            ("blueprint", str(rec_dir), "--verify-target", str(target_bp)),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == 0
    )

    drifted_dir = ContainedPath.from_parts(
        REPO_ROOT, "examples/02-blueprint-lvs/drifted"
    ).resolved_path
    out_drift = io.StringIO()
    assert (
        run_cli(
            ("blueprint", str(drifted_dir), "--verify-target", str(target_bp)),
            stdout=out_drift,
            stderr=io.StringIO(),
        )
        == 1
    )
    assert "SHORT_CIRCUIT" in out_drift.getvalue() or "storage" in out_drift.getvalue()


def test_examples_03_and_04_sunset_variance_and_sealed_dossier(tmp_path: Path) -> None:
    """INV-029-03 (03 & 04): Sunset variance expiration and tamper-evident PCE dossier seal."""
    ex03 = ContainedPath.from_parts(REPO_ROOT, "examples/03-sunset-variance").resolved_path
    permit03 = ContainedPath.from_parts(ex03, "permit.json").resolved_path

    out_pre = io.StringIO()
    assert (
        run_cli(
            ("inspect", str(ex03), "--permit", str(permit03), "--as-of", "2026-06-15"),
            stdout=out_pre,
            stderr=io.StringIO(),
        )
        == 0
    )
    assert "APPROVED_WITH_VARIANCES" in out_pre.getvalue()

    out_post = io.StringIO()
    assert (
        run_cli(
            ("inspect", str(ex03), "--permit", str(permit03), "--as-of", "2026-07-01"),
            stdout=out_post,
            stderr=io.StringIO(),
        )
        == 1
    )
    assert "RED_TAG" in out_post.getvalue()

    ex04 = ContainedPath.from_parts(REPO_ROOT, "examples/04-minimal-pce-dossier").resolved_path
    dossier04 = ContainedPath.from_parts(ex04, "dossier.json").resolved_path
    assert run_cli(("inspect", str(ex04)), stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert (
        run_cli(
            ("prove", str(ex04), "--require-deductive"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == 0
    )
    out_aud = io.StringIO()
    assert (
        run_cli(
            ("audit", str(dossier04), "--repo", str(ex04)),
            stdout=out_aud,
            stderr=io.StringIO(),
        )
        == 0
    )
    assert "OCCUPANCY_SEALED" in out_aud.getvalue()

    # Copy example 04 and mutate src/lib.rs to verify tamper detection
    tampered_repo = ContainedPath.from_parts(tmp_path, "tampered_ex04").resolved_path
    shutil.copytree(ex04, tampered_repo)
    lib_rs = ContainedPath.from_parts(tampered_repo, "src/lib.rs").resolved_path
    lib_rs.write_text(
        lib_rs.read_text(encoding="utf-8") + "\n// post-seal tamper\n",
        encoding="utf-8",
    )
    tampered_dos = ContainedPath.from_parts(tampered_repo, "dossier.json").resolved_path
    out_tamper = io.StringIO()
    assert (
        run_cli(
            ("audit", str(tampered_dos), "--repo", str(tampered_repo)),
            stdout=out_tamper,
            stderr=io.StringIO(),
        )
        == 1
    )
    assert "invalid_occupancy_seal" in out_tamper.getvalue().lower()


@settings(max_examples=25)
@given(delta_days=st.integers(min_value=-90, max_value=90))
def test_property_sunset_variance_ruling_flips_strictly_after_expiry(delta_days: int) -> None:
    """Property: CodeVariance waives JBC-COG-002 iff reference_date <= expires_on."""
    ex03 = ContainedPath.from_parts(REPO_ROOT, "examples/03-sunset-variance").resolved_path
    units = discover_workspace_units(ex03)
    inspector = build_workspace_inspector(ex03, units)
    expiry = date(2026, 6, 30)
    permit = CodePermit.full_book(
        "PERMIT-PROP-03",
        variances=(
            CodeVariance(
                variance_id="VAR-PROP-01",
                code_id="JBC-COG-002",
                file_path="adapter.go",
                symbol="TranslateLegacyOrder",
                authorized_by="Architect",
                justification="Time-bounded C-ABI adapter migration.",
                expires_on=expiry,
            ),
        ),
    )
    probe_date = expiry + timedelta(days=delta_days)
    rep = inspector.inspect_rough_in(units, permit=permit, reference_date=probe_date)
    expected = (
        OccupancyRuling.APPROVED_WITH_VARIANCES if probe_date <= expiry else OccupancyRuling.RED_TAG
    )
    assert rep.ruling == expected
