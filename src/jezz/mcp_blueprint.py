"""Read-only MCP tool handlers for BlueprintGraph extraction and coarse-grained LVS verification."""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Final

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, BeforeValidator, Field, TypeAdapter, ValidationError

from jezz.codes.blueprint import (
    BlueprintGraph,
    InvariantIdStr,
    RedlineDelta,
    extract_blueprint,
)
from jezz.codes.blueprint_lvs import (
    BlueprintLvsReport,
    apply_blueprint_redlines,
    verify_blueprint_lvs,
)
from jezz.codes.complexity import PermitProfile
from jezz.codes.discovery import (
    discover_workspace_units,
    load_contained_permit,
    read_contained_json,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BudgetExceededError,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    ShortText,
    jezz_callable,
)
from jezz.mcp_reload import HotReloadSupervisor

type GuardedSupervisor = Annotated[HotReloadSupervisor, InstanceGuard(HotReloadSupervisor)]
type TargetDirsTuple = Annotated[
    tuple[IdentifierStr, ...],
    WIRE_TUPLE_VALIDATOR,
    Field(max_length=64),
]
type InvariantBindingsTuple = Annotated[
    tuple[tuple[InvariantIdStr, IdentifierStr], ...],
    WIRE_TUPLE_VALIDATOR,
    Field(max_length=512),
]

_REDLINE_LIST_ADAPTER: Final[TypeAdapter[tuple[RedlineDelta, ...]]] = TypeAdapter(
    tuple[RedlineDelta, ...]
)


def _restore_json_str(raw_value: object) -> object:
    if isinstance(raw_value, dict | list):
        return json.dumps(raw_value)
    return raw_value


_BLUEPRINT_WIRE_JSON_VALIDATOR: Final[BeforeValidator] = BeforeValidator(_restore_json_str)
type BlueprintWireJson = Annotated[
    str,
    _BLUEPRINT_WIRE_JSON_VALIDATOR,
    Field(min_length=2, max_length=2_000_000),
]


class BlueprintExtractToolArgs(BaseModel):
    """Typed IPC argument bundle for ``jbc_extract_blueprint``."""

    model_config = STRICT_FROZEN_CONFIG

    workspace_subpath: IdentifierStr = "."
    target_dirs: TargetDirsTuple = ()
    title: ShortText = "Workspace Blueprint"
    permit_path: IdentifierStr | None = None
    redlines_json: BlueprintWireJson | None = None
    invariant_bindings: InvariantBindingsTuple = ()


class BlueprintLvsToolArgs(BaseModel):
    """Typed IPC argument bundle for ``jbc_verify_blueprint_lvs``."""

    model_config = STRICT_FROZEN_CONFIG

    target_blueprint_json: BlueprintWireJson | None = None
    target_blueprint_path: IdentifierStr | None = None
    workspace_subpath: IdentifierStr = "."
    target_dirs: TargetDirsTuple = ()
    permit_path: IdentifierStr | None = None


def _run_blueprint_tool_safely[ResultT](operation: Callable[[], ResultT]) -> ResultT:
    try:
        return operation()
    except ToolError:
        raise
    except (
        FileNotFoundError,
        SyntaxError,
        UnicodeDecodeError,
        ValidationError,
        ValueError,
        BudgetExceededError,
        OSError,
    ) as exc:
        detail = str(exc) or type(exc).__name__
        raise ToolError(detail) from exc


def _resolve_repo_dir(anchor_root: Path, workspace_subpath: str) -> Path:
    contained = ContainedPath.from_parts(anchor_root, workspace_subpath)
    if not contained.resolved_path.is_dir():
        msg = f"Workspace directory does not exist: '{workspace_subpath}'."
        raise FileNotFoundError(msg)
    return contained.resolved_path


def _resolve_profile(anchor_root: Path, permit_path: str | None) -> PermitProfile:
    if permit_path is None:
        return PermitProfile.STANDARD
    permit = load_contained_permit(ContainedPath.from_parts(anchor_root, permit_path))
    return permit.profile if permit is not None else PermitProfile.STANDARD


def _load_target_blueprint(
    anchor_root: Path,
    target_path: IdentifierStr | None,
    target_json: str | None,
) -> BlueprintGraph:
    if (target_path is None) == (target_json is None):
        msg = "Specify exactly one of 'target_blueprint_path' or 'target_blueprint_json'."
        raise ToolError(msg)
    if target_json is not None:
        return BlueprintGraph.model_validate_json(target_json)
    contained = ContainedPath.from_parts(anchor_root, target_path or "")
    return BlueprintGraph.model_validate_json(read_contained_json(contained))


@jezz_callable
def run_mcp_extract_blueprint(
    anchor_root: Path,
    args: BlueprintExtractToolArgs,
) -> BlueprintGraph:
    """Extract canonical BlueprintGraph (and apply optional redlines) under ``anchor_root``."""

    def _op() -> BlueprintGraph:
        repo = _resolve_repo_dir(anchor_root, args.workspace_subpath)
        profile = _resolve_profile(anchor_root, args.permit_path)
        units = discover_workspace_units(repo, target_dirs=args.target_dirs)
        baseline = extract_blueprint(
            units,
            title=args.title,
            profile=profile,
            invariant_bindings=args.invariant_bindings,
        )
        if args.redlines_json is None:
            return baseline
        deltas = _REDLINE_LIST_ADAPTER.validate_json(args.redlines_json)
        return apply_blueprint_redlines(baseline, deltas, title=args.title)

    return _run_blueprint_tool_safely(_op)


@jezz_callable
def run_mcp_verify_blueprint_lvs(
    anchor_root: Path,
    args: BlueprintLvsToolArgs,
) -> BlueprintLvsReport:
    """Verify physical workspace layout against a target BlueprintGraph schematic via LVS."""

    def _op() -> BlueprintLvsReport:
        repo = _resolve_repo_dir(anchor_root, args.workspace_subpath)
        target_bp = _load_target_blueprint(
            anchor_root, args.target_blueprint_path, args.target_blueprint_json
        )
        profile = (
            _resolve_profile(anchor_root, args.permit_path)
            if args.permit_path is not None
            else target_bp.profile
        )
        bindings = tuple(
            (ip.invariant_id, ip.verification_symbol) for ip in target_bp.invariant_pins
        )
        units = discover_workspace_units(repo, target_dirs=args.target_dirs)
        actual_bp = extract_blueprint(
            units,
            title=target_bp.title,
            profile=profile,
            invariant_bindings=bindings,
        )
        return verify_blueprint_lvs(actual_bp, target_bp, units=units)

    return _run_blueprint_tool_safely(_op)


def _make_extract_handler(
    anchor_root: Path,
    sup: HotReloadSupervisor,
) -> Callable[..., BlueprintGraph]:
    def _extract_tool(
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsTuple = (),
        title: ShortText = "Workspace Blueprint",
        permit_path: IdentifierStr | None = None,
        redlines_json: BlueprintWireJson | None = None,
        invariant_bindings: InvariantBindingsTuple = (),
    ) -> BlueprintGraph:
        req = BlueprintExtractToolArgs(
            workspace_subpath=workspace_subpath,
            target_dirs=target_dirs,
            title=title,
            permit_path=permit_path,
            redlines_json=redlines_json,
            invariant_bindings=invariant_bindings,
        )
        raw = json.dumps({"blueprint_extract_args": req.model_dump(mode="json")})
        return sup.invoke_or_local(
            "jbc_extract_blueprint",
            raw,
            BlueprintGraph,
            lambda: run_mcp_extract_blueprint(anchor_root, req),
        )

    return _extract_tool


def _make_lvs_handler(
    anchor_root: Path,
    sup: HotReloadSupervisor,
) -> Callable[..., BlueprintLvsReport]:
    def _lvs_tool(
        target_blueprint_json: BlueprintWireJson | None = None,
        target_blueprint_path: IdentifierStr | None = None,
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsTuple = (),
        permit_path: IdentifierStr | None = None,
    ) -> BlueprintLvsReport:
        req = BlueprintLvsToolArgs(
            target_blueprint_json=target_blueprint_json,
            target_blueprint_path=target_blueprint_path,
            workspace_subpath=workspace_subpath,
            target_dirs=target_dirs,
            permit_path=permit_path,
        )
        raw = json.dumps({"blueprint_lvs_args": req.model_dump(mode="json")})
        return sup.invoke_or_local(
            "jbc_verify_blueprint_lvs",
            raw,
            BlueprintLvsReport,
            lambda: run_mcp_verify_blueprint_lvs(anchor_root, req),
        )

    return _lvs_tool


@jezz_callable
def build_blueprint_mcp_tool_specs(
    anchor_root: Path,
    sup: GuardedSupervisor,
) -> Sequence[tuple[IdentifierStr, ShortText, ShortText, Callable[..., object]]]:
    """Build ``(name, title, description, handler)`` tuples for blueprint & LVS MCP tools."""
    return (
        (
            "jbc_extract_blueprint",
            "Extract Workspace BlueprintGraph",
            "Extract deterministic BlueprintGraph netlist IR (and optional redlines) from AST/CST.",
            _make_extract_handler(anchor_root, sup),
        ),
        (
            "jbc_verify_blueprint_lvs",
            "Verify Blueprint Layout-Versus-Schematic (LVS)",
            "Verify coarse-grained room walls, port pins, buses, risers, and invariants via LVS.",
            _make_lvs_handler(anchor_root, sup),
        ),
    )


__all__ = [
    "BlueprintExtractToolArgs",
    "BlueprintLvsToolArgs",
    "BlueprintWireJson",
    "build_blueprint_mcp_tool_specs",
    "run_mcp_extract_blueprint",
    "run_mcp_verify_blueprint_lvs",
]
