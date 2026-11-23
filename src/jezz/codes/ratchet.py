"""Git-aware Differential Occupancy Ratchet for incremental polyglot JBC v1.2 enforcement."""

import re
import subprocess
from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self

import icontract
from pydantic import BaseModel, Field, model_validator

from jezz.codes.ast_utils import RawDiscrepancy, SourceUnit
from jezz.codes.codebook import CODE_BOOK, codes_for_triggers
from jezz.codes.inspector import (
    BuildingInspector,
    assemble_inspection_report,
    collect_per_unit_discrepancies,
    detect_hazard_triggers,
)
from jezz.codes.permits import CodePermit, InspectionReport
from jezz.codes.polyglot_ir import InspectedUnit, PolyglotUnit, is_supported_source_path
from jezz.codes.polyglot_rules import check_polyglot_cog_003_for_paths
from jezz.codes.rules_cog import check_cog_003_for_paths
from jezz.codes.taxonomy import InspectionMethod
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_FROZENSET_VALIDATOR,
    WIRE_TUPLE_VALIDATOR,
    ContainedPath,
    FiniteFloat,
    IdentifierStr,
    InstanceGuard,
    NonNegativeInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
)

_SAFE_GIT_REF_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^-]{0,254}$")
_FORBIDDEN_REF_TOKENS: Final[tuple[str, ...]] = ("..", "@{", "//")

type GuardedInspector = Annotated[BuildingInspector, InstanceGuard(BuildingInspector)]


class GitDiffError(RuntimeError):
    """Raised when Git diff discovery fails due to missing git, bad ref, or timeout."""

    @jezz_callable
    def __init__(self, message: ShortText) -> None:
        super().__init__(message)


class DiffBaseMode(StrEnum):
    """Comparison baseline used to compute a Differential Occupancy Ratchet scope."""

    GIT_REF = "git_ref"
    STAGED_INDEX = "staged_index"
    EXPLICIT_PATHS = "explicit_paths"


class DifferentialScope(BaseModel):
    """Target changed-file set and statutory sunset date for differential inspection."""

    model_config = STRICT_FROZEN_CONFIG

    changed_paths: Annotated[
        frozenset[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=100_000),
    ] = frozenset()
    diff_mode: DiffBaseMode = DiffBaseMode.EXPLICIT_PATHS
    base_ref: IdentifierStr | None = None
    reference_date: date | None = None

    @model_validator(mode="after")
    def _validate_mode_ref_pairing(self) -> Self:
        if self.diff_mode == DiffBaseMode.GIT_REF and self.base_ref is None:
            msg = "DifferentialScope with diff_mode='git_ref' requires a non-null base_ref."
            raise ValueError(msg)
        if self.diff_mode == DiffBaseMode.STAGED_INDEX and self.base_ref is not None:
            msg = "DifferentialScope with diff_mode='staged_index' must not set base_ref."
            raise ValueError(msg)
        if self.base_ref is not None:
            validate_git_ref(self.base_ref)
        return self


class DifferentialInspectionReport(BaseModel):
    """Differential Occupancy Ratchet ledger pairing diff scope metadata with JBC verdicts."""

    model_config = STRICT_FROZEN_CONFIG

    diff_mode: DiffBaseMode
    base_ref: IdentifierStr | None = None
    reference_date: date
    total_workspace_units: NonNegativeInt
    untouched_unit_count: NonNegativeInt
    changed_python_paths: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=100_000),
    ] = ()
    inspected_unit_paths: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=100_000),
    ] = ()
    report: InspectionReport

    @model_validator(mode="after")
    def _validate_unit_accounting(self) -> Self:
        expected_total = len(self.inspected_unit_paths) + self.untouched_unit_count
        if self.total_workspace_units != expected_total:
            msg = (
                f"total_workspace_units ({self.total_workspace_units}) must equal "
                f"len(inspected_unit_paths) ({len(self.inspected_unit_paths)}) + "
                f"untouched_unit_count ({self.untouched_unit_count})."
            )
            raise ValueError(msg)
        return self

    @property
    def is_occupiable(self) -> bool:
        """Return True iff the differential ratchet scope achieved zero unwaived violations."""
        return self.report.is_occupiable

    @property
    def changed_source_paths(self) -> tuple[IdentifierStr, ...]:
        """Return all changed source paths (Python, TypeScript, Rust, Go) in scope."""
        return self.changed_python_paths

    @jezz_callable
    def to_markdown(
        self,
        *,
        max_findings: Annotated[int, Field(ge=1, le=5_000)] = 200,
    ) -> SourceCodeText:
        """Render the Differential Occupancy Ratchet summary and underlying JBC report."""
        basis = (
            f"`{self.diff_mode}` (`{self.base_ref}`)"
            if self.base_ref is not None
            else f"`{self.diff_mode}`"
        )
        header = [
            "# Differential Occupancy Ratchet",
            f"- **Diff Basis:** {basis}",
            f"- **Statutory Sunset Date:** `{self.reference_date.isoformat()}`",
            f"- **Workspace Graph Units:** `{self.total_workspace_units}` "
            f"(`{len(self.inspected_unit_paths)}` touched, "
            f"`{self.untouched_unit_count}` untouched)",
        ]
        if self.inspected_unit_paths:
            preview = ", ".join(f"`{p}`" for p in self.inspected_unit_paths[:12])
            extra = len(self.inspected_unit_paths) - 12
            suffix = f", ... (+{extra} more)" if extra > 0 else ""
            header.append(f"- **Gated Units:** {preview}{suffix}")
        header.extend(["", self.report.to_markdown(max_findings=max_findings)])
        return "\n".join(header)


@icontract.ensure(lambda result: not result.startswith("-") and ".." not in result)
@jezz_callable
def validate_git_ref(raw_ref: IdentifierStr) -> IdentifierStr:
    """Validate a Git revision identifier and reject option-injection or range tokens."""
    if raw_ref != raw_ref.strip() or not _SAFE_GIT_REF_RE.match(raw_ref):
        msg = f"Invalid Git revision '{raw_ref}'; expected alphanumeric branch, tag, or SHA."
        raise ValueError(msg)
    if raw_ref.endswith(".lock") or any(tok in raw_ref for tok in _FORBIDDEN_REF_TOKENS):
        msg = f"Forbidden range or lock token in Git revision '{raw_ref}'."
        raise ValueError(msg)
    return raw_ref


def _sanitize_git_stderr(raw_stderr: str) -> str:
    lines = [ln.strip() for ln in raw_stderr.splitlines() if ln.strip()]
    if not lines:
        return "git exited with non-zero status."
    return lines[-1][:240]


def _run_git_command(
    resolved_root: Path,
    git_args: Sequence[str],
    timeout_sec: float,
) -> str:
    argv = ("git", "-C", str(resolved_root), *git_args)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except FileNotFoundError as exc:
        msg = "Git executable 'git' was not found on PATH."
        raise GitDiffError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"Git command timed out after {timeout_sec:.1f}s in '{resolved_root}'."
        raise GitDiffError(msg) from exc
    except OSError as exc:
        msg = f"OS failure invoking git in '{resolved_root}': {type(exc).__name__}."
        raise GitDiffError(msg) from exc

    if proc.returncode != 0:
        detail = _sanitize_git_stderr(proc.stderr)
        msg = f"Git diff command failed (exit {proc.returncode}): {detail}"
        raise GitDiffError(msg)
    return proc.stdout


def _extract_contained_source_paths(resolved_root: Path, nul_output: str) -> set[str]:
    paths: set[str] = set()
    for raw_entry in nul_output.split("\x00"):
        if not raw_entry or not is_supported_source_path(raw_entry):
            continue
        contained = ContainedPath.from_parts(resolved_root, raw_entry)
        if contained.resolved_path.is_file():
            paths.add(contained.relative_posix)
    return paths


@jezz_callable
def resolve_git_changed_paths(
    repo_root: Path,
    *,
    base_ref: IdentifierStr | None = None,
    staged_only: bool = False,
    include_untracked: bool = True,
    timeout_sec: Annotated[FiniteFloat, Field(gt=0.0, le=120.0)] = 15.0,
) -> frozenset[IdentifierStr]:
    """Return workspace-relative supported source paths modified since ``base_ref`` or staged."""
    if (base_ref is not None) == staged_only:
        msg = "Specify exactly one of base_ref or staged_only=True."
        raise ValueError(msg)
    resolved_root = repo_root.resolve()
    if not resolved_root.is_dir():
        msg = f"Workspace directory does not exist: '{repo_root}'."
        raise FileNotFoundError(msg)

    if staged_only:
        staged_out = _run_git_command(
            resolved_root,
            ("diff", "-z", "--cached", "--relative", "--name-only", "--diff-filter=ACMR", "--"),
            timeout_sec,
        )
        return frozenset(_extract_contained_source_paths(resolved_root, staged_out))

    if base_ref is None:
        msg = "Specify base_ref when staged_only=False."
        raise ValueError(msg)
    clean_ref = validate_git_ref(base_ref)
    merge_base = _run_git_command(
        resolved_root,
        ("merge-base", clean_ref, "HEAD"),
        timeout_sec,
    ).strip()
    clean_base = validate_git_ref(merge_base)
    diff_out = _run_git_command(
        resolved_root,
        ("diff", "-z", "--relative", "--name-only", "--diff-filter=ACMR", clean_base, "--"),
        timeout_sec,
    )
    discovered = _extract_contained_source_paths(resolved_root, diff_out)
    if include_untracked:
        untracked_out = _run_git_command(
            resolved_root,
            ("ls-files", "-z", "--others", "--exclude-standard", "--"),
            timeout_sec,
        )
        discovered |= _extract_contained_source_paths(resolved_root, untracked_out)
    return frozenset(discovered)


def _collect_differential_discrepancies(
    inspector: BuildingInspector,
    all_units: Sequence[InspectedUnit],
    touched_units: Sequence[InspectedUnit],
    touched_paths: frozenset[str],
    static_ids: frozenset[str],
) -> list[RawDiscrepancy]:
    if not touched_units:
        return []
    per_unit = collect_per_unit_discrepancies(
        inspector.manifest,
        touched_units,
        static_ids,
        polyglot_manifest=inspector.polyglot_manifest,
    )
    if "JBC-COG-003" not in static_ids:
        return per_unit
    all_py = [u for u in all_units if isinstance(u, SourceUnit)]
    all_poly = [u for u in all_units if isinstance(u, PolyglotUnit)]
    cog_003 = [
        *(check_cog_003_for_paths(all_py, touched_paths) if all_py else []),
        *(check_polyglot_cog_003_for_paths(all_poly, touched_paths) if all_poly else []),
    ]
    combined = [*per_unit, *cog_003]
    combined.sort(key=lambda item: (item.code_id, item.location.file_path, item.location.line))
    return combined


@jezz_callable
def inspect_differential_rough_in(
    inspector: GuardedInspector,
    all_units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
    scope: DifferentialScope,
    *,
    permit: CodePermit | None = None,
) -> DifferentialInspectionReport:
    """Gate touched units under JBC v1.2 while preserving whole-workspace cycle analysis."""
    active_permit = permit or CodePermit.full_book(permit_id="PERMIT-RATCHET")
    active_date = (
        scope.reference_date if scope.reference_date is not None else datetime.now(UTC).date()
    )
    touched_units = tuple(u for u in all_units if u.file_path in scope.changed_paths)
    touched_paths = frozenset(u.file_path for u in touched_units)

    detected = detect_hazard_triggers(touched_units)
    triggered_ids = {c.code_id for c in codes_for_triggers(detected)}
    candidate_ids = active_permit.bound_code_ids | triggered_ids
    static_ids = frozenset(
        cid
        for cid in candidate_ids
        if CODE_BOOK[cid].inspection_method != InspectionMethod.GATE_ATTESTATION
    )
    raw = _collect_differential_discrepancies(
        inspector, all_units, touched_units, touched_paths, static_ids
    )
    report = assemble_inspection_report(
        active_permit,
        detected,
        static_ids,
        raw,
        len(touched_units),
        active_date,
    )
    return DifferentialInspectionReport(
        diff_mode=scope.diff_mode,
        base_ref=scope.base_ref,
        reference_date=active_date,
        total_workspace_units=len(all_units),
        untouched_unit_count=len(all_units) - len(touched_units),
        changed_python_paths=tuple(sorted(scope.changed_paths)),
        inspected_unit_paths=tuple(sorted(touched_paths)),
        report=report,
    )
