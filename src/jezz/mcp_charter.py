"""Read-only MCP tool handlers for PCE Phase 0/I intake and ProjectCharter sealing."""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Final

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from jezz.codes.complexity import PermitProfile
from jezz.codes.discovery import (
    discover_workspace_units,
    load_contained_permit,
    read_contained_json,
)
from jezz.codes.inspector import BuildingInspector
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
from jezz.methodology.charter import (
    CharterIntakeSession,
    CharterSealInput,
    ProjectCharter,
    probe_workspace_reality,
    seal_project_charter,
    start_charter_intake,
)

type GuardedSupervisor = Annotated[HotReloadSupervisor, InstanceGuard(HotReloadSupervisor)]
type TargetDirsTuple = Annotated[
    tuple[IdentifierStr, ...],
    WIRE_TUPLE_VALIDATOR,
    Field(max_length=64),
]
type CandidateNamesTuple = Annotated[
    tuple[IdentifierStr, ...],
    WIRE_TUPLE_VALIDATOR,
    Field(max_length=64),
]


def _restore_json_text(raw_value: object) -> object:
    if isinstance(raw_value, dict | list):
        return json.dumps(raw_value)
    return raw_value


_JSON_TEXT_VALIDATOR: Final[BeforeValidator] = BeforeValidator(_restore_json_text)
type CharterWireJson = Annotated[
    str,
    _JSON_TEXT_VALIDATOR,
    Field(min_length=2, max_length=2_000_000),
]


class CharterIntakeToolArgs(BaseModel):
    """Typed IPC argument bundle for ``pce_start_intake``."""

    model_config = STRICT_FROZEN_CONFIG

    title: ShortText
    vision: Annotated[str, Field(min_length=12, max_length=65_536)]
    charter_id: IdentifierStr | None = None
    workspace_subpath: IdentifierStr = "."
    candidate_binaries: CandidateNamesTuple = ()
    candidate_packages: CandidateNamesTuple = ()


class CharterSealToolArgs(BaseModel):
    """Typed IPC argument bundle for ``pce_seal_charter``."""

    model_config = STRICT_FROZEN_CONFIG

    seal_input_json: CharterWireJson | None = None
    seal_input_path: IdentifierStr | None = None
    workspace_subpath: IdentifierStr = "."
    target_dirs: TargetDirsTuple = ()
    permit_path: IdentifierStr | None = None


def _run_charter_tool_safely[ResultT](operation: Callable[[], ResultT]) -> ResultT:
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


def _resolve_target_repo(anchor_root: Path, workspace_subpath: str) -> Path:
    contained = ContainedPath.from_parts(anchor_root, workspace_subpath)
    if not contained.resolved_path.is_dir():
        msg = f"Workspace directory does not exist: '{workspace_subpath}'."
        raise FileNotFoundError(msg)
    return contained.resolved_path


def _load_seal_input(
    anchor_root: Path,
    seal_input_path: IdentifierStr | None,
    seal_input_json: str | None,
) -> CharterSealInput:
    if (seal_input_path is None) == (seal_input_json is None):
        msg = "Specify exactly one of 'seal_input_path' or 'seal_input_json'."
        raise ToolError(msg)
    if seal_input_json is not None:
        return CharterSealInput.model_validate_json(seal_input_json)
    target = ContainedPath.from_parts(anchor_root, seal_input_path or "")
    return CharterSealInput.model_validate_json(read_contained_json(target))


@jezz_callable
def run_mcp_start_intake(anchor_root: Path, args: CharterIntakeToolArgs) -> CharterIntakeSession:
    """Execute reality-probed Phase 0/I charter intake jailed under ``anchor_root``."""

    def _op() -> CharterIntakeSession:
        target_repo = _resolve_target_repo(anchor_root, args.workspace_subpath)
        probe = probe_workspace_reality(
            target_repo,
            candidate_binaries=args.candidate_binaries,
            candidate_packages=args.candidate_packages,
        )
        units = discover_workspace_units(target_repo)
        return start_charter_intake(
            args.title,
            args.vision,
            probe,
            charter_id=args.charter_id,
            units=units,
        )

    return _run_charter_tool_safely(_op)


@jezz_callable
def run_mcp_seal_charter(anchor_root: Path, args: CharterSealToolArgs) -> ProjectCharter:
    """Validate 3-axis resolutions, lot lines, and permit topology, then seal ProjectCharter."""

    def _op() -> ProjectCharter:
        target_repo = _resolve_target_repo(anchor_root, args.workspace_subpath)
        seal_req = _load_seal_input(anchor_root, args.seal_input_path, args.seal_input_json)
        permit_target = (
            ContainedPath.from_parts(anchor_root, args.permit_path)
            if args.permit_path is not None
            else None
        )
        permit = load_contained_permit(permit_target)
        profile = permit.profile if permit is not None else PermitProfile.STANDARD
        units = discover_workspace_units(target_repo, target_dirs=args.target_dirs)
        inspector = BuildingInspector.from_workspace(target_repo)
        return seal_project_charter(
            seal_req,
            units,
            manifest=inspector.manifest,
            polyglot_manifest=inspector.polyglot_manifest,
            profile=profile,
        )

    return _run_charter_tool_safely(_op)


def _make_intake_handler(
    anchor_root: Path,
    sup: HotReloadSupervisor,
) -> Callable[..., CharterIntakeSession]:
    def _start_intake_tool(
        title: ShortText,
        vision: Annotated[str, Field(min_length=12, max_length=65_536)],
        charter_id: IdentifierStr | None = None,
        workspace_subpath: IdentifierStr = ".",
        candidate_binaries: CandidateNamesTuple = (),
        candidate_packages: CandidateNamesTuple = (),
    ) -> CharterIntakeSession:
        req = CharterIntakeToolArgs(
            title=title,
            vision=vision,
            charter_id=charter_id,
            workspace_subpath=workspace_subpath,
            candidate_binaries=candidate_binaries,
            candidate_packages=candidate_packages,
        )
        raw = json.dumps({"intake_args": req.model_dump(mode="json")})
        return sup.invoke_or_local(
            "pce_start_intake",
            raw,
            CharterIntakeSession,
            lambda: run_mcp_start_intake(anchor_root, req),
        )

    return _start_intake_tool


def _make_seal_handler(
    anchor_root: Path,
    sup: HotReloadSupervisor,
) -> Callable[..., ProjectCharter]:
    def _seal_charter_tool(
        seal_input_json: CharterWireJson | None = None,
        seal_input_path: IdentifierStr | None = None,
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsTuple = (),
        permit_path: IdentifierStr | None = None,
    ) -> ProjectCharter:
        req = CharterSealToolArgs(
            seal_input_json=seal_input_json,
            seal_input_path=seal_input_path,
            workspace_subpath=workspace_subpath,
            target_dirs=target_dirs,
            permit_path=permit_path,
        )
        raw = json.dumps({"seal_charter_args": req.model_dump(mode="json")})
        return sup.invoke_or_local(
            "pce_seal_charter",
            raw,
            ProjectCharter,
            lambda: run_mcp_seal_charter(anchor_root, req),
        )

    return _seal_charter_tool


@jezz_callable
def build_charter_mcp_tool_specs(
    anchor_root: Path,
    sup: GuardedSupervisor,
) -> Sequence[tuple[IdentifierStr, ShortText, ShortText, Callable[..., object]]]:
    """Build ``(name, title, description, handler)`` tuples for PCE charter MCP tools."""
    return (
        (
            "pce_start_intake",
            "Start PCE Project Charter Intake",
            "Probe workspace/CLI/package reality and generate 3-axis Phase 0/I intake questions.",
            _make_intake_handler(anchor_root, sup),
        ),
        (
            "pce_seal_charter",
            "Seal PCE Project Charter",
            "Verify 3-axis completeness, hazard posture, and phased permit DAG to seal a charter.",
            _make_seal_handler(anchor_root, sup),
        ),
    )


__all__ = [
    "CharterIntakeToolArgs",
    "CharterSealToolArgs",
    "CharterWireJson",
    "build_charter_mcp_tool_specs",
    "run_mcp_seal_charter",
    "run_mcp_start_intake",
]
