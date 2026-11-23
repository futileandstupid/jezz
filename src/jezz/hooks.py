"""Antigravity lifecycle hook engine enforcing JBC Edition 1.2 statutory Stop gates."""

import io
import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from jezz.codes.ast_utils import SourceUnit
from jezz.codes.discovery import (
    build_workspace_inspector,
    discover_workspace_units,
    load_contained_permit,
    parse_optional_iso_date,
)
from jezz.codes.inspector import BuildingInspector
from jezz.codes.permits import CodePermit, InspectionReport
from jezz.codes.ratchet import (
    DiffBaseMode,
    DifferentialScope,
    GitDiffError,
    inspect_differential_rough_in,
    resolve_git_changed_paths,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    NonNegativeInt,
    jezz_callable,
    read_bounded_text,
)

_MAX_HOOK_STDIN_CHARS: Final[int] = 262_144
_MAX_HOOK_FINDINGS: Final[int] = 10
_DEFAULT_PERMIT_RELPATH: Final[str] = ".jezz/permit.json"
_PLUGIN_MANIFEST_RELPATH: Final[str] = ".agents/plugins/jezz/plugin.json"

type GuardedHookStream = Annotated[io.TextIOBase, InstanceGuard(io.TextIOBase)]


class StopHookDecisionKind(StrEnum):
    """Statutory decision emitted by an Antigravity ``Stop`` lifecycle hook."""

    ALLOW = "allow"
    CONTINUE = "continue"


class StopHookInput(BaseModel):
    """Canonical JBC-verified representation of an Antigravity ``Stop`` hook payload."""

    model_config = STRICT_FROZEN_CONFIG

    termination_reason: Annotated[str, Field(max_length=128)] = "NO_TOOL_CALL"
    execution_num: NonNegativeInt = 0
    continuation_active: bool = False
    workspace_paths: Annotated[
        tuple[BoundedText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    cwd: BoundedText | None = None


_RAW_HOOK_MAP_ADAPTER: Final[TypeAdapter[dict[BoundedText, object]]] = TypeAdapter(
    dict[BoundedText, object]
)


@jezz_callable
def parse_stop_hook_json(raw_json: BoundedText) -> StopHookInput:
    """Project an Antigravity camelCase ``Stop`` JSON payload into a strict ``StopHookInput``."""
    decoded = _RAW_HOOK_MAP_ADAPTER.validate_json(raw_json)
    projected: dict[str, object] = {}
    key_map = (
        ("terminationReason", "termination_reason"),
        ("termination_reason", "termination_reason"),
        ("executionNum", "execution_num"),
        ("execution_num", "execution_num"),
        ("continuationActive", "continuation_active"),
        ("continuation_active", "continuation_active"),
        ("workspacePaths", "workspace_paths"),
        ("workspace_paths", "workspace_paths"),
        ("cwd", "cwd"),
    )
    for wire_key, field_name in key_map:
        if wire_key in decoded and field_name not in projected:
            projected[field_name] = decoded[wire_key]
    return StopHookInput.model_validate(projected)


class StopHookConfig(BaseModel):
    """Configuration governing ``jezz hook stop`` statutory enforcement."""

    model_config = STRICT_FROZEN_CONFIG

    workspace_override: Path | None = None
    max_continuations: Annotated[int, Field(ge=1, le=10)] = 3
    require_jezz_marker: bool = False
    permit_path: Path | None = None
    as_of: IdentifierStr | None = None


class StopHookOutput(BaseModel):
    """Antigravity ``Stop`` hook stdout payload."""

    model_config = STRICT_FROZEN_CONFIG

    decision: StopHookDecisionKind
    reason: BoundedText | None = None

    @model_validator(mode="after")
    def _enforce_continue_reason(self) -> Self:
        if self.decision == StopHookDecisionKind.CONTINUE and not (
            self.reason and self.reason.strip()
        ):
            msg = "StopHookOutput with decision='continue' requires a non-empty reason."
            raise ValueError(msg)
        return self

    @jezz_callable
    def to_wire_json(self) -> BoundedText:
        """Serialize the hook decision to Antigravity's expected stdout JSON contract."""
        if self.reason is None:
            return json.dumps({"decision": str(self.decision)})
        return json.dumps({"decision": str(self.decision), "reason": self.reason})


def _allow_stop() -> StopHookOutput:
    return StopHookOutput(decision=StopHookDecisionKind.ALLOW)


def _block_stop(reason: str) -> StopHookOutput:
    return StopHookOutput(
        decision=StopHookDecisionKind.CONTINUE,
        reason=reason[:16_000],
    )


@jezz_callable
def has_jezz_workspace_marker(repo_root: Path) -> bool:
    """Return True iff ``repo_root`` has a local ``jezz`` plugin or ``[tool.jezz]`` config."""
    resolved = repo_root.resolve()
    if not resolved.is_dir():
        return False
    plugin_manifest = ContainedPath.from_parts(resolved, _PLUGIN_MANIFEST_RELPATH)
    if plugin_manifest.resolved_path.is_file():
        return True
    pyproject = ContainedPath.from_parts(resolved, "pyproject.toml")
    if not pyproject.resolved_path.is_file():
        return False
    content = read_bounded_text(pyproject)
    return "[tool.jezz" in content


def _resolve_hook_workspace(payload: StopHookInput, cfg: StopHookConfig) -> Path | None:
    if cfg.workspace_override is not None:
        resolved_override = cfg.workspace_override.resolve()
        return resolved_override if resolved_override.is_dir() else None
    for raw_path in payload.workspace_paths:
        stripped = raw_path.strip()
        if stripped:
            candidate = Path(stripped).resolve()
            if candidate.is_dir():
                return candidate
    if payload.cwd is not None and payload.cwd.strip():
        cwd_candidate = Path(payload.cwd.strip()).resolve()
        return cwd_candidate if cwd_candidate.is_dir() else None
    return None


def _load_hook_permit(repo_root: Path, explicit_permit: Path | None) -> CodePermit | None:
    if explicit_permit is not None:
        resolved = explicit_permit.resolve()
        contained = ContainedPath.from_parts(resolved.parent, resolved.name)
        return load_contained_permit(contained)
    default_permit = ContainedPath.from_parts(repo_root, _DEFAULT_PERMIT_RELPATH)
    if default_permit.resolved_path.is_file():
        return load_contained_permit(default_permit)
    return None


def _inspect_workspace_changes(
    repo_root: Path,
    units: tuple[SourceUnit, ...],
    inspector: BuildingInspector,
    permit: CodePermit | None,
    reference_date: date | None,
) -> InspectionReport | None:
    try:
        changed = resolve_git_changed_paths(repo_root, base_ref="HEAD", include_untracked=True)
    except GitDiffError:
        return inspector.inspect_rough_in(units, permit=permit, reference_date=reference_date)
    if not changed:
        return None
    scope = DifferentialScope(
        changed_paths=changed,
        diff_mode=DiffBaseMode.GIT_REF,
        base_ref="HEAD",
        reference_date=reference_date,
    )
    return inspect_differential_rough_in(inspector, units, scope, permit=permit).report


def _format_stop_remediation(report: InspectionReport) -> str:
    violations = report.unwaived_violations()
    shown = violations[:_MAX_HOOK_FINDINGS]
    lines = [
        f"JBC v1.2 Statutory Stop Gate (RED_TAG — {len(violations)} unwaived violation(s) "
        "in modified workspace files):",
    ]
    for finding in shown:
        loc = f"{finding.location.file_path}:L{finding.location.line}"
        lines.append(
            f"- {finding.code_id} ({loc}): {finding.message} -> Fix: {finding.remediation}"
        )
    extra = len(violations) - len(shown)
    if extra > 0:
        lines.append(f"- ... (+{extra} additional violation(s))")
    lines.append(
        "Remediate all cited JBC provisions and verify clean occupancy via "
        "`jbc_inspect_differential` or `jezz inspect` before completing your turn."
    )
    return "\n".join(lines)


def _discover_or_block_syntax(repo_root: Path) -> tuple[SourceUnit, ...] | StopHookOutput:
    try:
        return discover_workspace_units(repo_root)
    except SyntaxError as exc:
        loc = f"{exc.filename or '<unknown>'}:L{exc.lineno or 1}"
        return _block_stop(
            f"JBC v1.2 Statutory Stop Gate (SYNTAX_ERROR at {loc}): {exc.msg}. "
            "Repair invalid Python syntax before completing your turn."
        )


@jezz_callable
def evaluate_stop_hook(
    payload: StopHookInput,
    config: StopHookConfig | None = None,
) -> StopHookOutput:
    """Evaluate Antigravity ``Stop`` hook against the Differential Occupancy Ratchet."""
    cfg = config or StopHookConfig()
    if (
        payload.termination_reason != "NO_TOOL_CALL"
        or payload.execution_num >= cfg.max_continuations
    ):
        return _allow_stop()
    repo_root = _resolve_hook_workspace(payload, cfg)
    if repo_root is None:
        return _allow_stop()
    if cfg.require_jezz_marker and not has_jezz_workspace_marker(repo_root):
        return _allow_stop()

    discovered = _discover_or_block_syntax(repo_root)
    if isinstance(discovered, StopHookOutput):
        return discovered
    if not discovered:
        return _allow_stop()

    permit = _load_hook_permit(repo_root, cfg.permit_path)
    ref_date = parse_optional_iso_date(cfg.as_of)
    inspector = build_workspace_inspector(repo_root, discovered)
    report = _inspect_workspace_changes(repo_root, discovered, inspector, permit, ref_date)
    if report is None or report.is_occupiable:
        return _allow_stop()
    return _block_stop(_format_stop_remediation(report))


@jezz_callable
def run_stop_hook_stdio(
    stdin: GuardedHookStream,
    stdout: GuardedHookStream,
    config: StopHookConfig | None = None,
) -> StopHookOutput:
    """Read Antigravity ``Stop`` JSON from ``stdin`` and emit the statutory decision."""
    raw = stdin.read(_MAX_HOOK_STDIN_CHARS).strip()
    if not raw:
        decision = _allow_stop()
    else:
        try:
            parsed = parse_stop_hook_json(raw)
        except ValidationError, ValueError:
            decision = _allow_stop()
        else:
            decision = evaluate_stop_hook(parsed, config=config)
    stdout.write(f"{decision.to_wire_json()}\n")
    stdout.flush()
    return decision
