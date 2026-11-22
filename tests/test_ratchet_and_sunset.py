"""Verification suite for sunset CodeVariances and Git-aware Differential Occupancy Ratchet."""

import io
import subprocess
import textwrap
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from jezz.cli import main
from jezz.codes import (
    BuildingInspector,
    CodePermit,
    CodeVariance,
    ContainedPath,
    DiffBaseMode,
    DifferentialInspectionReport,
    DifferentialScope,
    FindingDisposition,
    GitDiffError,
    ManifestDependencies,
    OccupancyRuling,
    RedTagError,
    SourceSpan,
    inspect_differential_rough_in,
    issue_certificate,
    parse_source_unit,
    resolve_git_changed_paths,
    validate_git_ref,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_PYPROJECT_TOML = textwrap.dedent(
    """\
    [project]
    name = "ratchet-demo"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)

_LEGACY_DIRTY_SRC = textwrap.dedent(
    """\
    def unguarded_legacy_calc(x, y):
        print("legacy debug")
        return x + y
    """
)

_CLEAN_MODULE_SRC = textwrap.dedent(
    """\
    import icontract
    from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

    @icontract.ensure(lambda units, result: result == units * 3)
    @jezz_callable
    def triple_units(units: PositiveInt) -> NonNegativeInt:
        return units * 3
    """
)

_NOISY_MODULE_SRC = textwrap.dedent(
    """\
    from jezz.guards import jezz_callable

    @jezz_callable
    def noisy_banner() -> None:
        print("banner")
    """
)


def _write_contained_file(root: Path, rel_path: str, content: str) -> Path:
    resolved_root = root.resolve()
    parent_rel = Path(rel_path).parent.as_posix()
    if parent_rel != ".":
        (resolved_root / parent_rel).mkdir(parents=True, exist_ok=True)
    contained = ContainedPath.from_parts(resolved_root, rel_path)
    with contained.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
    return contained.resolved_path


def _run_git(repo_dir: Path, args: Sequence[str]) -> str:
    proc = subprocess.run(
        ("git", "-C", str(repo_dir), *args),
        capture_output=True,
        text=True,
        check=True,
        timeout=15.0,
    )
    return proc.stdout


def _init_brownfield_git_repo(repo_dir: Path) -> None:
    _run_git(repo_dir, ("init", "-b", "main"))
    _run_git(repo_dir, ("config", "user.email", "inspector@jezz.local"))
    _run_git(repo_dir, ("config", "user.name", "Jezz Inspector"))
    _write_contained_file(repo_dir, "pyproject.toml", _PYPROJECT_TOML)
    _write_contained_file(repo_dir, "README.md", "# Demo\n")
    _write_contained_file(repo_dir, "src/ratchet_demo/__init__.py", "")
    _write_contained_file(repo_dir, "src/ratchet_demo/legacy.py", _LEGACY_DIRTY_SRC)
    _run_git(repo_dir, ("add", "."))
    _run_git(repo_dir, ("commit", "-m", "Seed unrenovated brownfield baseline"))


# ---------------------------------------------------------------------------
# 1. Sunset CodeVariances (expires_on + reference_date)
# ---------------------------------------------------------------------------


@given(
    sunset=st.dates(min_value=date(2020, 1, 1), max_value=date(2040, 12, 31)),
    day_offset=st.integers(min_value=-3650, max_value=3650),
)
def test_property_sunset_variance_expiration_boundary(
    sunset: date,
    day_offset: int,
) -> None:
    variance = CodeVariance(
        variance_id="VAR-OBS-SUNSET",
        code_id="JBC-OBS-001",
        file_path="src/pkg/mod.py",
        authorized_by="Chief Engineer",
        justification="Bounded legacy console output pending structured logger cutover.",
        expires_on=sunset,
    )
    span = SourceSpan(file_path="src/pkg/mod.py", line=4, column=4, symbol="noisy_banner")
    probe_date = sunset + timedelta(days=day_offset)

    expected_expired = day_offset > 0
    assert variance.is_expired(probe_date) is expected_expired
    assert variance.matches("JBC-OBS-001", span, reference_date=probe_date) is (
        not expected_expired
    )
    assert variance.covers_span("JBC-OBS-001", span) is True


def test_sunset_variance_flips_report_to_red_tag_with_expiry_citation() -> None:
    unit = parse_source_unit("src/pkg/mod.py", _NOISY_MODULE_SRC)
    permit = CodePermit.full_book(
        permit_id="PERMIT-SUNSET-01",
        variances=(
            CodeVariance(
                variance_id="VAR-OBS-2026",
                code_id="JBC-OBS-001",
                file_path="src/pkg/mod.py",
                authorized_by="Principal Architect",
                justification="Temporary console banner approved through Q2 2026.",
                expires_on=date(2026, 6, 30),
            ),
        ),
    )
    # Round-trip through JSON to verify strict ISO-8601 date serialization
    reloaded = CodePermit.model_validate_json(permit.model_dump_json())
    inspector = BuildingInspector(
        ManifestDependencies(runtime_roots=frozenset({"jezz"}), workspace_roots=frozenset({"pkg"}))
    )

    on_sunset = inspector.inspect_rough_in(
        (unit,), permit=reloaded, reference_date=date(2026, 6, 30)
    )
    assert on_sunset.ruling == OccupancyRuling.APPROVED_WITH_VARIANCES
    cert = issue_certificate(on_sunset, artifact_digest="deadbeef01234567")
    assert cert.exercised_variance_ids == ("VAR-OBS-2026",)

    past_sunset = inspector.inspect_rough_in(
        (unit,), permit=reloaded, reference_date=date(2026, 7, 1)
    )
    assert past_sunset.ruling == OccupancyRuling.RED_TAG
    violations = past_sunset.unwaived_violations()
    assert len(violations) == 1
    assert violations[0].disposition == FindingDisposition.VIOLATION
    assert "Expired variance 'VAR-OBS-2026' (sunset 2026-06-30)" in violations[0].message
    with pytest.raises(RedTagError):
        issue_certificate(past_sunset, artifact_digest="deadbeef01234567")


def test_renewed_variance_supersedes_expired_variance_on_same_span() -> None:
    unit = parse_source_unit("src/pkg/mod.py", _NOISY_MODULE_SRC)
    permit = CodePermit.full_book(
        permit_id="PERMIT-RENEWAL",
        variances=(
            CodeVariance(
                variance_id="VAR-OLD",
                code_id="JBC-OBS-001",
                file_path="src/pkg/mod.py",
                authorized_by="Principal Architect",
                justification="Initial sunset variance that has already lapsed.",
                expires_on=date(2026, 3, 1),
            ),
            CodeVariance(
                variance_id="VAR-RENEWED",
                code_id="JBC-OBS-001",
                file_path="src/pkg/mod.py",
                authorized_by="Principal Architect",
                justification="Renewed variance extending migration window to year-end.",
                expires_on=date(2026, 12, 31),
            ),
        ),
    )
    inspector = BuildingInspector(
        ManifestDependencies(runtime_roots=frozenset({"jezz"}), workspace_roots=frozenset({"pkg"}))
    )
    report = inspector.inspect_rough_in((unit,), permit=permit, reference_date=date(2026, 9, 15))
    assert report.ruling == OccupancyRuling.APPROVED_WITH_VARIANCES
    assert report.findings[0].variance_id == "VAR-RENEWED"


def test_cli_inspect_as_of_enforces_sunset_variance_and_rejects_bad_date(tmp_path: Path) -> None:
    _write_contained_file(tmp_path, "pyproject.toml", _PYPROJECT_TOML)
    _write_contained_file(tmp_path, "src/ratchet_demo/__init__.py", "")
    _write_contained_file(tmp_path, "src/ratchet_demo/mod.py", _NOISY_MODULE_SRC)
    permit = CodePermit.full_book(
        permit_id="PERMIT-CLI-SUNSET",
        variances=(
            CodeVariance(
                variance_id="VAR-CLI-01",
                code_id="JBC-OBS-001",
                file_path="src/ratchet_demo/mod.py",
                authorized_by="Staff Engineer",
                justification="Temporary startup banner scheduled for removal.",
                expires_on=date(2026, 10, 15),
            ),
        ),
    )
    permit_path = _write_contained_file(tmp_path, "permit.json", permit.model_dump_json(indent=2))

    out_ok = io.StringIO()
    code_ok = main(
        ["inspect", str(tmp_path), "--permit", str(permit_path), "--as-of", "2026-10-15"],
        stdout=out_ok,
    )
    assert code_ok == 0
    assert "APPROVED_WITH_VARIANCES" in out_ok.getvalue()

    out_exp = io.StringIO()
    code_exp = main(
        ["inspect", str(tmp_path), "--permit", str(permit_path), "--as-of", "2026-10-16"],
        stdout=out_exp,
    )
    assert code_exp == 1
    assert "Expired variance 'VAR-CLI-01'" in out_exp.getvalue()

    err_bad = io.StringIO()
    code_bad = main(
        ["inspect", str(tmp_path), "--as-of", "not-a-date"],
        stdout=io.StringIO(),
        stderr=err_bad,
    )
    assert code_bad == 2
    assert "jezz error:" in err_bad.getvalue()


# ---------------------------------------------------------------------------
# 2. Git Ref Validation & Differential Scope Guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valid_ref",
    [
        "main",
        "origin/main",
        "HEAD",
        "HEAD~1",
        "HEAD^2",
        "release/v1.2.0",
        "4e899d7a1b2c3d4e5f60718293a4b5c6d7e8f901",
    ],
)
def test_validate_git_ref_accepts_canonical_revisions(valid_ref: str) -> None:
    assert validate_git_ref(valid_ref) == valid_ref


@pytest.mark.parametrize(
    "hostile_ref",
    [
        "-o",
        "--upload-pack=evil",
        "main..HEAD",
        "main...HEAD",
        "HEAD@{1}",
        "origin//main",
        "branch.lock",
        "main; rm -rf /",
        " main",
    ],
)
def test_validate_git_ref_rejects_flag_injection_and_range_operators(hostile_ref: str) -> None:
    with pytest.raises(ValueError):
        validate_git_ref(hostile_ref)


def test_differential_scope_validates_mode_and_ref_invariants() -> None:
    with pytest.raises(ValidationError, match="requires a non-null base_ref"):
        DifferentialScope(diff_mode=DiffBaseMode.GIT_REF, base_ref=None)

    with pytest.raises(ValidationError, match="must not set base_ref"):
        DifferentialScope(diff_mode=DiffBaseMode.STAGED_INDEX, base_ref="main")


# ---------------------------------------------------------------------------
# 3. Git-Aware Differential Occupancy Ratchet & Cross-Module Cycle Detection
# ---------------------------------------------------------------------------


def test_differential_ratchet_passes_clean_edits_and_docs_on_dirty_brownfield_repo(
    tmp_path: Path,
) -> None:
    _init_brownfield_git_repo(tmp_path)

    # Full-repository inspection fails due to unrenovated legacy.py
    assert main(["inspect", str(tmp_path)], stdout=io.StringIO()) == 1

    # Docs-only edit touches 0 Python units -> Differential Ratchet approves (exit 0)
    _write_contained_file(tmp_path, "README.md", "# Updated Docs Only\n")
    out_docs = io.StringIO()
    assert main(["inspect", str(tmp_path), "--changed-since", "main"], stdout=out_docs) == 0
    assert "`0` touched" in out_docs.getvalue()

    # Add a brand-new JBC-compliant Python module on a feature branch (untracked, then staged)
    _run_git(tmp_path, ("checkout", "-b", "feat/clean-addition"))
    _write_contained_file(tmp_path, "src/ratchet_demo/clean_mod.py", _CLEAN_MODULE_SRC)

    untracked_paths = resolve_git_changed_paths(tmp_path, base_ref="main")
    assert untracked_paths == frozenset({"src/ratchet_demo/clean_mod.py"})

    _run_git(tmp_path, ("add", "src/ratchet_demo/clean_mod.py"))
    out_staged = io.StringIO()
    exit_staged = main(
        ["inspect", str(tmp_path), "--staged", "--format", "json"],
        stdout=out_staged,
    )
    assert exit_staged == 0
    parsed = DifferentialInspectionReport.model_validate_json(out_staged.getvalue())
    assert parsed.diff_mode == DiffBaseMode.STAGED_INDEX
    assert parsed.inspected_unit_paths == ("src/ratchet_demo/clean_mod.py",)
    assert parsed.untouched_unit_count == 2
    assert parsed.report.ruling == OccupancyRuling.APPROVED


def test_differential_ratchet_blocks_violating_touched_module(tmp_path: Path) -> None:
    _init_brownfield_git_repo(tmp_path)
    _run_git(tmp_path, ("checkout", "-b", "feat/bad-addition"))
    _write_contained_file(tmp_path, "src/ratchet_demo/bad_mod.py", _NOISY_MODULE_SRC)
    _run_git(tmp_path, ("add", "src/ratchet_demo/bad_mod.py"))
    _run_git(tmp_path, ("commit", "-m", "Add violating module"))

    out_md = io.StringIO()
    exit_code = main(["inspect", str(tmp_path), "--changed-since", "main"], stdout=out_md)
    rendered = out_md.getvalue()
    assert exit_code == 1
    assert "# Differential Occupancy Ratchet" in rendered
    assert "`src/ratchet_demo/bad_mod.py`" in rendered
    assert "`JBC-OBS-001`" in rendered
    # Untouched legacy.py violations must not leak into differential findings
    assert "src/ratchet_demo/legacy.py" not in rendered


def test_differential_ratchet_catches_cross_module_cycle_when_lexicographic_tail_touched() -> None:
    alpha_clean = textwrap.dedent(
        """\
        from pkg.zebra import BASE_MULTIPLIER
        from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

        @jezz_callable
        def scale_alpha(val: PositiveInt) -> NonNegativeInt:
            return val * BASE_MULTIPLIER
        """
    )
    zebra_cyclic = textwrap.dedent(
        """\
        from typing import Final
        from pkg.alpha import scale_alpha
        from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

        BASE_MULTIPLIER: Final[int] = 2

        @jezz_callable
        def close_cycle(val: PositiveInt) -> NonNegativeInt:
            return scale_alpha(val)
        """
    )
    units = (
        parse_source_unit("src/pkg/__init__.py", ""),
        parse_source_unit("src/pkg/alpha.py", alpha_clean),
        parse_source_unit("src/pkg/zebra.py", zebra_cyclic),
    )
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"jezz"}),
            workspace_roots=frozenset({"pkg"}),
        )
    )
    # Only zebra.py ('pkg.zebra' > 'pkg.alpha') was modified in the diff!
    scope = DifferentialScope(
        changed_paths=frozenset({"src/pkg/zebra.py"}),
        diff_mode=DiffBaseMode.GIT_REF,
        base_ref="main",
        reference_date=date(2026, 11, 21),
    )
    diff_report = inspect_differential_rough_in(inspector, units, scope)
    assert not diff_report.is_occupiable
    assert diff_report.report.ruling == OccupancyRuling.RED_TAG
    violations = diff_report.report.unwaived_violations()
    assert any(
        v.code_id == "JBC-COG-003" and v.location.file_path == "src/pkg/zebra.py"
        for v in violations
    )


def test_cli_differential_inspect_surfaces_git_errors_with_exit_code_two(tmp_path: Path) -> None:
    _write_contained_file(tmp_path, "pyproject.toml", _PYPROJECT_TOML)
    _write_contained_file(tmp_path, "src/ratchet_demo/__init__.py", "")
    _write_contained_file(tmp_path, "src/ratchet_demo/clean.py", _CLEAN_MODULE_SRC)

    # tmp_path is not a git repository -> GitDiffError -> exit code 2
    err_non_git = io.StringIO()
    code_non_git = main(
        ["inspect", str(tmp_path), "--changed-since", "main"],
        stdout=io.StringIO(),
        stderr=err_non_git,
    )
    assert code_non_git == 2
    assert "jezz error:" in err_non_git.getvalue()

    with pytest.raises(GitDiffError):
        resolve_git_changed_paths(tmp_path, staged_only=True)


def test_cli_differential_inspect_dogfoods_live_jezz_repository() -> None:
    out_md = io.StringIO()
    err_md = io.StringIO()
    exit_code = main(
        ["inspect", str(REPO_ROOT), "--changed-since", "HEAD"],
        stdout=out_md,
        stderr=err_md,
    )
    assert exit_code == 0, f"{out_md.getvalue()}\n{err_md.getvalue()}"
    assert "**Ruling:** `APPROVED`" in out_md.getvalue()
