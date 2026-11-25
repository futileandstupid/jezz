"""Read-only Model Context Protocol (MCP) statutory gate server for polyglot JBC v1.3 and PCE."""

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from jezz import __version__
from jezz.behaviors.graph import BehaviorGraph, GraphVerificationReport, verify_behavior_graph
from jezz.codes.adapters import parse_any_source_unit
from jezz.codes.ast_graph import (
    ModuleGraphEntry,
    SymbolGraphEntry,
    build_workspace_ast_graph,
    extract_symbol_source_slice,
)
from jezz.codes.codebook import CODE_BOOK, CODEBOOK_EDITION
from jezz.codes.complexity import PermitProfile
from jezz.codes.discovery import (
    build_workspace_inspector,
    discover_workspace_units,
    load_contained_permit,
    parse_optional_iso_date,
    read_contained_json,
    require_workspace_units,
)
from jezz.codes.inspector import BuildingInspector
from jezz.codes.permits import InspectionReport
from jezz.codes.polyglot_ir import InspectedUnit, is_supported_source_path
from jezz.codes.ratchet import (
    DiffBaseMode,
    DifferentialInspectionReport,
    DifferentialScope,
    GitDiffError,
    inspect_differential_rough_in,
    resolve_git_changed_paths,
)
from jezz.codes.taxonomy import (
    BuildingCode,
    CodeArticle,
    CodeSeverity,
    CodeTrigger,
    InspectionMethod,
)
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    BudgetExceededError,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    NonNegativeInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
)
from jezz.mcp_reload import HotReloadSupervisor
from jezz.methodology.dossier import DossierAuditReport, EngineeringDossier, audit_pce_dossier
from jezz.methodology.lot_line import LotLineAssessment, assess_mandate_lot_line
from jezz.methodology.mandate import EngineeringMandate

SERVER_NAME: Final[str] = "jezz"
SERVER_VERSION: Final[str] = __version__

_READ_ONLY_ANNOTATIONS: Final[ToolAnnotations] = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

_SERVER_INSTRUCTIONS: Final[str] = (
    "Jezz Software Building Code (JBC Edition 1.3) & Permit-and-Contract Engineering (PCE) "
    "polyglot statutory gate server across Python (.py), TypeScript (.ts, .tsx), Rust (.rs), "
    "and Go (.go). Use jbc_preview_source to validate candidate source buffers in memory "
    "(including whole-workspace JBC-COG-003/006 topology checks) before writing to disk; "
    "use jbc_inspect_differential or jbc_inspect_workspace for Rough-In verification; use "
    "jbc_assess_lot_line for pre-flight package/compartment shear checks; use "
    "jbc_query_ast_graph for workspace AST/CST import/call/symbol slices; use jbc_get_code / "
    "jbc_list_codes for normative JBC provisions; and use jbc_verify_behavior_graph and "
    "jbc_audit_dossier for Pillar 1 and Pillar 3 verification."
)

type GuardedMcpServer = Annotated[MCPServer[object], InstanceGuard(MCPServer)]
type GuardedSupervisor = Annotated[HotReloadSupervisor, InstanceGuard(HotReloadSupervisor)]
type TargetDirsArg = Annotated[
    tuple[IdentifierStr, ...],
    WIRE_TUPLE_VALIDATOR,
    Field(max_length=64),
]


def _restore_pre_parsed_json_text(raw_value: object) -> object:
    if isinstance(raw_value, dict | list):
        return json.dumps(raw_value)
    return raw_value


_WIRE_JSON_TEXT_VALIDATOR: Final[BeforeValidator] = BeforeValidator(_restore_pre_parsed_json_text)
type WireJsonText = Annotated[
    str,
    _WIRE_JSON_TEXT_VALIDATOR,
    Field(min_length=2, max_length=2_000_000),
]


class BuildingCodeSummary(BaseModel):
    """Compact statutory summary of a single JBC Edition 1.3 BuildingCode provision."""

    model_config = STRICT_FROZEN_CONFIG

    code_id: IdentifierStr
    article: CodeArticle
    title: ShortText
    severity: CodeSeverity
    variance_permitted: bool
    inspection_method: InspectionMethod
    triggers: Annotated[
        tuple[CodeTrigger, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=5),
    ]
    pass_criteria: ShortText

    @classmethod
    @jezz_callable
    def from_code(cls, code: BuildingCode) -> BuildingCodeSummary:
        """Project a full ``BuildingCode`` into a compact ``BuildingCodeSummary``."""
        ordered_triggers = tuple(sorted(code.triggers, key=str))
        return BuildingCodeSummary(
            code_id=code.code_id,
            article=code.article,
            title=code.title,
            severity=code.severity,
            variance_permitted=code.variance_permitted,
            inspection_method=code.inspection_method,
            triggers=ordered_triggers,
            pass_criteria=code.pass_criteria,
        )


class JbcCatalogPayload(BaseModel):
    """Filtered JBC Edition 1.3 catalog response returned by ``jbc_list_codes``."""

    model_config = STRICT_FROZEN_CONFIG

    edition: IdentifierStr
    total_matching: NonNegativeInt
    provisions: Annotated[
        tuple[BuildingCodeSummary, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ]


class AstGraphQueryResult(BaseModel):
    """Structured AST/CST import, call, and symbol-slice response for ``jbc_query_ast_graph``."""

    model_config = STRICT_FROZEN_CONFIG

    has_import_cycles: bool
    compact_brief: BoundedText
    modules: Annotated[
        tuple[ModuleGraphEntry, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=512),
    ] = ()
    callers: Annotated[
        tuple[SymbolGraphEntry, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ] = ()
    symbol_slice: SourceCodeText = ""


class _ReloadToolArgs(BaseModel):
    """Closed typed IPC argument envelope validated inside fresh reload workers."""

    model_config = STRICT_FROZEN_CONFIG

    article: CodeArticle | None = None
    trigger: CodeTrigger | None = None
    code_id: IdentifierStr = "JBC-DBC-001"
    graph: BehaviorGraph | None = None
    workspace_subpath: IdentifierStr = "."
    target_dirs: TargetDirsArg = ()
    permit_path: IdentifierStr | None = None
    as_of: IdentifierStr | None = None
    changed_since: IdentifierStr | None = None
    staged: bool = False
    file_path: IdentifierStr = "preview.py"
    source_code: SourceCodeText = ""
    dossier_path: IdentifierStr | None = None
    dossier_json: WireJsonText | None = None
    mandate_path: IdentifierStr | None = None
    mandate_json: WireJsonText | None = None
    symbol_query: IdentifierStr | None = None
    caller_of: IdentifierStr | None = None


@jezz_callable
def list_jbc_codes(
    article: CodeArticle | None = None,
    trigger: CodeTrigger | None = None,
) -> JbcCatalogPayload:
    """List JBC Edition 1.3 provisions filtered optionally by ``article`` and ``trigger``."""
    matched: list[BuildingCodeSummary] = []
    for code_id in sorted(CODE_BOOK):
        code = CODE_BOOK[code_id]
        if article is not None and code.article != article:
            continue
        if trigger is not None and trigger not in code.triggers:
            continue
        matched.append(BuildingCodeSummary.from_code(code))
    summaries = tuple(matched)
    return JbcCatalogPayload(
        edition=CODEBOOK_EDITION,
        total_matching=len(summaries),
        provisions=summaries,
    )


@jezz_callable
def get_jbc_code(code_id: IdentifierStr) -> BuildingCode:
    """Return the complete statutory definition and standards lineage for ``code_id``."""
    normalized = code_id.strip().upper()
    code = CODE_BOOK.get(normalized)
    if code is None:
        msg = (
            f"Unknown BuildingCode '{code_id}'. Expected an Edition {CODEBOOK_EDITION} "
            "identifier such as 'JBC-DBC-001' or 'JBC-COG-006' (call jbc_list_codes)."
        )
        raise ToolError(msg)
    return code


@jezz_callable
def verify_jbc_behavior_graph(graph: BehaviorGraph) -> GraphVerificationReport:
    """Verify Pillar 1 transitions and Kildall monotone dataflow laws on ``graph``."""
    return verify_behavior_graph(graph)


def _format_syntax_failure(exc: SyntaxError) -> str:
    target = exc.filename or "<preview>"
    line_part = f" at line {exc.lineno}" if exc.lineno is not None else ""
    detail = exc.msg or "invalid syntax"
    return f"Source SyntaxError in '{target}'{line_part}: {detail}"


def _run_tool_safely[ResultT](operation: Callable[[], ResultT]) -> ResultT:
    try:
        return operation()
    except ToolError:
        raise
    except SyntaxError as exc:
        raise ToolError(_format_syntax_failure(exc)) from exc
    except (
        FileNotFoundError,
        UnicodeDecodeError,
        ValidationError,
        ValueError,
        BudgetExceededError,
        GitDiffError,
        OSError,
    ) as exc:
        detail = str(exc) or type(exc).__name__
        raise ToolError(detail) from exc


def _resolve_contained_dir(anchor_root: Path, workspace_subpath: str) -> Path:
    contained = ContainedPath.from_parts(anchor_root, workspace_subpath)
    if not contained.resolved_path.is_dir():
        msg = f"Workspace directory does not exist: '{workspace_subpath}'."
        raise FileNotFoundError(msg)
    return contained.resolved_path


def _resolve_optional_file(anchor_root: Path, raw_path: str | None) -> ContainedPath | None:
    if raw_path is None:
        return None
    return ContainedPath.from_parts(anchor_root, raw_path)


def _overlay_candidate_unit(
    existing_units: Sequence[InspectedUnit],
    candidate_unit: InspectedUnit,
) -> tuple[InspectedUnit, ...]:
    retained = [u for u in existing_units if u.file_path != candidate_unit.file_path]
    retained.append(candidate_unit)
    retained.sort(key=lambda u: u.file_path)
    return tuple(retained)


def _run_workspace_inspection(
    anchor_root: Path,
    workspace_subpath: IdentifierStr,
    target_dirs: Sequence[IdentifierStr],
    permit_path: IdentifierStr | None,
    as_of: IdentifierStr | None,
) -> InspectionReport:
    target_repo = _resolve_contained_dir(anchor_root, workspace_subpath)
    permit = load_contained_permit(_resolve_optional_file(anchor_root, permit_path))
    reference_date = parse_optional_iso_date(as_of)
    resolved_repo, units = require_workspace_units(target_repo, target_dirs=target_dirs)
    inspector = build_workspace_inspector(resolved_repo, units)
    return inspector.inspect_rough_in(units, permit=permit, reference_date=reference_date)


def _run_differential_inspection(
    anchor_root: Path,
    changed_since: IdentifierStr | None,
    staged: bool,
    workspace_subpath: IdentifierStr,
    permit_path: IdentifierStr | None,
    as_of: IdentifierStr | None,
) -> DifferentialInspectionReport:
    if changed_since is not None and staged:
        msg = "Specify at most one of 'changed_since' or 'staged=True', not both."
        raise ToolError(msg)
    target_repo = _resolve_contained_dir(anchor_root, workspace_subpath)
    permit = load_contained_permit(_resolve_optional_file(anchor_root, permit_path))
    reference_date = parse_optional_iso_date(as_of)
    resolved_repo, units = require_workspace_units(target_repo)
    inspector = build_workspace_inspector(resolved_repo, units)
    effective_ref = None if staged else (changed_since if changed_since is not None else "HEAD")
    changed_paths = resolve_git_changed_paths(
        resolved_repo,
        base_ref=effective_ref,
        staged_only=staged,
    )
    scope = DifferentialScope(
        changed_paths=changed_paths,
        diff_mode=DiffBaseMode.STAGED_INDEX if staged else DiffBaseMode.GIT_REF,
        base_ref=effective_ref,
        reference_date=reference_date,
    )
    return inspect_differential_rough_in(inspector, units, scope, permit=permit)


def _run_preview_inspection(
    anchor_root: Path,
    file_path: IdentifierStr,
    source_code: SourceCodeText,
    workspace_subpath: IdentifierStr,
    permit_path: IdentifierStr | None,
    as_of: IdentifierStr | None,
) -> DifferentialInspectionReport:
    target_repo = _resolve_contained_dir(anchor_root, workspace_subpath)
    candidate_target = ContainedPath.from_parts(target_repo, file_path)
    rel_posix = candidate_target.relative_posix
    if rel_posix == "." or not is_supported_source_path(rel_posix, include_ambient_dts=True):
        msg = (
            f"Preview file_path '{file_path}' must target a supported source path "
            "('.py', '.ts', '.tsx', '.rs', '.go') inside the workspace."
        )
        raise ToolError(msg)
    permit = load_contained_permit(_resolve_optional_file(anchor_root, permit_path))
    reference_date = parse_optional_iso_date(as_of)
    candidate_unit = parse_any_source_unit(rel_posix, source_code)
    existing_units = discover_workspace_units(target_repo)
    merged_units = _overlay_candidate_unit(existing_units, candidate_unit)
    inspector = build_workspace_inspector(target_repo, merged_units)
    scope = DifferentialScope(
        changed_paths=frozenset({rel_posix}),
        diff_mode=DiffBaseMode.EXPLICIT_PATHS,
        reference_date=reference_date,
    )
    return inspect_differential_rough_in(inspector, merged_units, scope, permit=permit)


def _load_dossier_payload(
    anchor_root: Path,
    dossier_path: IdentifierStr | None,
    dossier_json: SourceCodeText | None,
) -> EngineeringDossier:
    if (dossier_path is None) == (dossier_json is None):
        msg = "Specify exactly one of 'dossier_path' or 'dossier_json'."
        raise ToolError(msg)
    if dossier_json is not None:
        return EngineeringDossier.model_validate_json(dossier_json)
    target = ContainedPath.from_parts(anchor_root, dossier_path or "")
    return EngineeringDossier.model_validate_json(read_contained_json(target))


def _load_mandate_payload(
    anchor_root: Path,
    mandate_path: IdentifierStr | None,
    mandate_json: SourceCodeText | None,
) -> EngineeringMandate:
    if (mandate_path is None) == (mandate_json is None):
        msg = "Specify exactly one of 'mandate_path' or 'mandate_json'."
        raise ToolError(msg)
    if mandate_json is not None:
        return EngineeringMandate.model_validate_json(mandate_json)
    target = ContainedPath.from_parts(anchor_root, mandate_path or "")
    return EngineeringMandate.model_validate_json(read_contained_json(target))


def _run_dossier_audit(
    anchor_root: Path,
    dossier_path: IdentifierStr | None,
    dossier_json: SourceCodeText | None,
    workspace_subpath: IdentifierStr,
    target_dirs: Sequence[IdentifierStr],
) -> DossierAuditReport:
    target_repo = _resolve_contained_dir(anchor_root, workspace_subpath)
    dossier = _load_dossier_payload(anchor_root, dossier_path, dossier_json)
    _, units = require_workspace_units(target_repo, target_dirs=target_dirs)
    return audit_pce_dossier(dossier, units)


def _run_lot_line_assessment(
    anchor_root: Path,
    mandate_path: IdentifierStr | None,
    mandate_json: SourceCodeText | None,
    workspace_subpath: IdentifierStr,
    target_dirs: Sequence[IdentifierStr],
    permit_path: IdentifierStr | None,
) -> LotLineAssessment:
    target_repo = _resolve_contained_dir(anchor_root, workspace_subpath)
    mandate = _load_mandate_payload(anchor_root, mandate_path, mandate_json)
    permit = load_contained_permit(_resolve_optional_file(anchor_root, permit_path))
    units = discover_workspace_units(target_repo, target_dirs=target_dirs)
    inspector = BuildingInspector.from_workspace(target_repo)
    profile = permit.profile if permit is not None else PermitProfile.STANDARD
    return assess_mandate_lot_line(
        mandate,
        units,
        manifest=inspector.manifest,
        polyglot_manifest=inspector.polyglot_manifest,
        profile=profile,
    )


def _run_ast_graph_query(
    anchor_root: Path,
    workspace_subpath: IdentifierStr,
    target_dirs: Sequence[IdentifierStr],
    symbol_query: IdentifierStr | None,
    caller_of: IdentifierStr | None,
) -> AstGraphQueryResult:
    target_repo = _resolve_contained_dir(anchor_root, workspace_subpath)
    units = discover_workspace_units(target_repo, target_dirs=target_dirs)
    graph = build_workspace_ast_graph(units)
    callers = graph.find_callers(caller_of)[:256] if caller_of is not None else ()
    snippet = extract_symbol_source_slice(units, symbol_query) if symbol_query is not None else ""
    return AstGraphQueryResult(
        has_import_cycles=graph.has_import_cycles,
        compact_brief=graph.to_compact_brief(),
        modules=graph.modules[:512],
        callers=callers,
        symbol_slice=snippet,
    )


class JezzWorkspaceGate:
    """Workspace-jailed polyglot JBC v1.3 and PCE verification gate bound to ``anchor_root``."""

    @jezz_callable
    def __init__(self, workspace_root: Path | None = None) -> None:
        raw_root = workspace_root if workspace_root is not None else Path.cwd()
        resolved = raw_root.resolve()
        if not resolved.is_dir():
            msg = f"MCP workspace root directory does not exist: '{raw_root}'."
            raise FileNotFoundError(msg)
        self._anchor_root: Path = resolved

    @property
    def anchor_root(self) -> Path:
        """Return canonical workspace root directory confining all filesystem reads."""
        return self._anchor_root

    @jezz_callable
    def inspect_workspace(
        self,
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsArg = (),
        permit_path: IdentifierStr | None = None,
        as_of: IdentifierStr | None = None,
    ) -> InspectionReport:
        """Run JBC Edition 1.3 Rough-In static inspection across workspace source files."""
        return _run_tool_safely(
            lambda: _run_workspace_inspection(
                self._anchor_root,
                workspace_subpath,
                target_dirs,
                permit_path,
                as_of,
            )
        )

    @jezz_callable
    def inspect_differential(
        self,
        changed_since: IdentifierStr | None = None,
        staged: bool = False,
        workspace_subpath: IdentifierStr = ".",
        permit_path: IdentifierStr | None = None,
        as_of: IdentifierStr | None = None,
    ) -> DifferentialInspectionReport:
        """Run Git-aware Differential Occupancy Ratchet on touched polyglot source files."""
        return _run_tool_safely(
            lambda: _run_differential_inspection(
                self._anchor_root,
                changed_since,
                staged,
                workspace_subpath,
                permit_path,
                as_of,
            )
        )

    @jezz_callable
    def preview_source(
        self,
        file_path: IdentifierStr,
        source_code: SourceCodeText,
        workspace_subpath: IdentifierStr = ".",
        permit_path: IdentifierStr | None = None,
        as_of: IdentifierStr | None = None,
    ) -> DifferentialInspectionReport:
        """Validate an unsaved source buffer (.py/.ts/.tsx/.rs/.go) overlaid onto the workspace."""
        return _run_tool_safely(
            lambda: _run_preview_inspection(
                self._anchor_root,
                file_path,
                source_code,
                workspace_subpath,
                permit_path,
                as_of,
            )
        )

    @jezz_callable
    def audit_dossier(
        self,
        dossier_path: IdentifierStr | None = None,
        dossier_json: WireJsonText | None = None,
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsArg = (),
    ) -> DossierAuditReport:
        """Audit a 5-phase PCE EngineeringDossier against compiled polyglot workspace symbols."""
        return _run_tool_safely(
            lambda: _run_dossier_audit(
                self._anchor_root,
                dossier_path,
                dossier_json,
                workspace_subpath,
                target_dirs,
            )
        )

    @jezz_callable
    def query_ast_graph(
        self,
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsArg = (),
        symbol_query: IdentifierStr | None = None,
        caller_of: IdentifierStr | None = None,
    ) -> AstGraphQueryResult:
        """Query the polyglot workspace AST/CST import/layer graph, callers, and symbol slices."""
        return _run_tool_safely(
            lambda: _run_ast_graph_query(
                self._anchor_root,
                workspace_subpath,
                target_dirs,
                symbol_query,
                caller_of,
            )
        )

    @jezz_callable
    def assess_lot_line(
        self,
        mandate_path: IdentifierStr | None = None,
        mandate_json: WireJsonText | None = None,
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsArg = (),
        permit_path: IdentifierStr | None = None,
    ) -> LotLineAssessment:
        """Assess pre-flight JBC-COG-006 floorplate capacity and CRP lot-line shear."""
        return _run_tool_safely(
            lambda: _run_lot_line_assessment(
                self._anchor_root,
                mandate_path,
                mandate_json,
                workspace_subpath,
                target_dirs,
                permit_path,
            )
        )


def _dispatch_verify_graph(_gate: JezzWorkspaceGate, args: _ReloadToolArgs) -> BaseModel:
    if args.graph is None:
        raise ToolError("Missing required 'graph' payload for jbc_verify_behavior_graph.")
    return verify_jbc_behavior_graph(args.graph)


type _WorkerDispatchFn = Callable[[JezzWorkspaceGate, _ReloadToolArgs], BaseModel]

_WORKER_TOOL_DISPATCH: Final[Mapping[str, _WorkerDispatchFn]] = MappingProxyType(
    {
        "jbc_list_codes": lambda _g, a: list_jbc_codes(article=a.article, trigger=a.trigger),
        "jbc_get_code": lambda _g, a: get_jbc_code(a.code_id),
        "jbc_verify_behavior_graph": _dispatch_verify_graph,
        "jbc_inspect_workspace": lambda g, a: g.inspect_workspace(
            a.workspace_subpath, a.target_dirs, a.permit_path, a.as_of
        ),
        "jbc_inspect_differential": lambda g, a: g.inspect_differential(
            a.changed_since, a.staged, a.workspace_subpath, a.permit_path, a.as_of
        ),
        "jbc_preview_source": lambda g, a: g.preview_source(
            a.file_path, a.source_code, a.workspace_subpath, a.permit_path, a.as_of
        ),
        "jbc_audit_dossier": lambda g, a: g.audit_dossier(
            a.dossier_path, a.dossier_json, a.workspace_subpath, a.target_dirs
        ),
        "jbc_query_ast_graph": lambda g, a: g.query_ast_graph(
            a.workspace_subpath, a.target_dirs, a.symbol_query, a.caller_of
        ),
        "jbc_assess_lot_line": lambda g, a: g.assess_lot_line(
            a.mandate_path, a.mandate_json, a.workspace_subpath, a.target_dirs, a.permit_path
        ),
    }
)


@jezz_callable
def dispatch_reloaded_mcp_call(
    workspace_root: Path,
    tool_name: IdentifierStr,
    arguments_json: SourceCodeText,
) -> SourceCodeText:
    """Execute ``tool_name`` inside a fresh reload worker process and return serialized JSON."""
    handler = _WORKER_TOOL_DISPATCH.get(tool_name)
    if handler is None:
        raise ToolError(f"Unknown MCP statutory tool '{tool_name}'.")
    parsed_args = _run_tool_safely(lambda: _ReloadToolArgs.model_validate_json(arguments_json))
    gate = JezzWorkspaceGate(workspace_root=workspace_root)
    return handler(gate, parsed_args).model_dump_json()


def _build_catalog_handlers(
    sup: HotReloadSupervisor,
) -> tuple[
    Callable[..., JbcCatalogPayload],
    Callable[..., BuildingCode],
    Callable[..., GraphVerificationReport],
]:
    def _list_tool(
        article: CodeArticle | None = None,
        trigger: CodeTrigger | None = None,
    ) -> JbcCatalogPayload:
        raw = json.dumps(
            {
                "article": str(article) if article is not None else None,
                "trigger": str(trigger) if trigger is not None else None,
            }
        )
        return sup.invoke_or_local(
            "jbc_list_codes",
            raw,
            JbcCatalogPayload,
            lambda: list_jbc_codes(article=article, trigger=trigger),
        )

    def _get_tool(code_id: IdentifierStr) -> BuildingCode:
        raw = json.dumps({"code_id": code_id})
        return sup.invoke_or_local("jbc_get_code", raw, BuildingCode, lambda: get_jbc_code(code_id))

    def _verify_tool(graph: BehaviorGraph) -> GraphVerificationReport:
        raw = json.dumps({"graph": graph.model_dump(mode="json")})
        return sup.invoke_or_local(
            "jbc_verify_behavior_graph",
            raw,
            GraphVerificationReport,
            lambda: verify_jbc_behavior_graph(graph),
        )

    return _list_tool, _get_tool, _verify_tool


def _build_inspection_handlers(
    gate: JezzWorkspaceGate,
    sup: HotReloadSupervisor,
) -> tuple[
    Callable[..., InspectionReport],
    Callable[..., DifferentialInspectionReport],
    Callable[..., DifferentialInspectionReport],
]:
    def _ws_tool(
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsArg = (),
        permit_path: IdentifierStr | None = None,
        as_of: IdentifierStr | None = None,
    ) -> InspectionReport:
        raw = json.dumps(
            {
                "workspace_subpath": workspace_subpath,
                "target_dirs": list(target_dirs),
                "permit_path": permit_path,
                "as_of": as_of,
            }
        )
        return sup.invoke_or_local(
            "jbc_inspect_workspace",
            raw,
            InspectionReport,
            lambda: gate.inspect_workspace(workspace_subpath, target_dirs, permit_path, as_of),
        )

    def _diff_tool(
        changed_since: IdentifierStr | None = None,
        staged: bool = False,
        workspace_subpath: IdentifierStr = ".",
        permit_path: IdentifierStr | None = None,
        as_of: IdentifierStr | None = None,
    ) -> DifferentialInspectionReport:
        raw = json.dumps(
            {
                "changed_since": changed_since,
                "staged": staged,
                "workspace_subpath": workspace_subpath,
                "permit_path": permit_path,
                "as_of": as_of,
            }
        )
        return sup.invoke_or_local(
            "jbc_inspect_differential",
            raw,
            DifferentialInspectionReport,
            lambda: gate.inspect_differential(
                changed_since, staged, workspace_subpath, permit_path, as_of
            ),
        )

    def _preview_tool(
        file_path: IdentifierStr,
        source_code: str,
        workspace_subpath: IdentifierStr = ".",
        permit_path: IdentifierStr | None = None,
        as_of: IdentifierStr | None = None,
    ) -> DifferentialInspectionReport:
        raw = json.dumps(
            {
                "file_path": file_path,
                "source_code": source_code,
                "workspace_subpath": workspace_subpath,
                "permit_path": permit_path,
                "as_of": as_of,
            }
        )
        return sup.invoke_or_local(
            "jbc_preview_source",
            raw,
            DifferentialInspectionReport,
            lambda: gate.preview_source(
                file_path=file_path,
                source_code=source_code,
                workspace_subpath=workspace_subpath,
                permit_path=permit_path,
                as_of=as_of,
            ),
        )

    return _ws_tool, _diff_tool, _preview_tool


def _build_dossier_and_graph_handlers(
    gate: JezzWorkspaceGate,
    sup: HotReloadSupervisor,
) -> tuple[
    Callable[..., DossierAuditReport],
    Callable[..., AstGraphQueryResult],
    Callable[..., LotLineAssessment],
]:
    def _dossier_tool(
        dossier_path: IdentifierStr | None = None,
        dossier_json: WireJsonText | None = None,
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsArg = (),
    ) -> DossierAuditReport:
        raw = json.dumps(
            {
                "dossier_path": dossier_path,
                "dossier_json": dossier_json,
                "workspace_subpath": workspace_subpath,
                "target_dirs": list(target_dirs),
            }
        )
        return sup.invoke_or_local(
            "jbc_audit_dossier",
            raw,
            DossierAuditReport,
            lambda: gate.audit_dossier(dossier_path, dossier_json, workspace_subpath, target_dirs),
        )

    def _ast_tool(
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsArg = (),
        symbol_query: IdentifierStr | None = None,
        caller_of: IdentifierStr | None = None,
    ) -> AstGraphQueryResult:
        raw = json.dumps(
            {
                "workspace_subpath": workspace_subpath,
                "target_dirs": list(target_dirs),
                "symbol_query": symbol_query,
                "caller_of": caller_of,
            }
        )
        return sup.invoke_or_local(
            "jbc_query_ast_graph",
            raw,
            AstGraphQueryResult,
            lambda: gate.query_ast_graph(workspace_subpath, target_dirs, symbol_query, caller_of),
        )

    def _lot_line_tool(
        mandate_path: IdentifierStr | None = None,
        mandate_json: WireJsonText | None = None,
        workspace_subpath: IdentifierStr = ".",
        target_dirs: TargetDirsArg = (),
        permit_path: IdentifierStr | None = None,
    ) -> LotLineAssessment:
        raw = json.dumps(
            {
                "mandate_path": mandate_path,
                "mandate_json": mandate_json,
                "workspace_subpath": workspace_subpath,
                "target_dirs": list(target_dirs),
                "permit_path": permit_path,
            }
        )
        return sup.invoke_or_local(
            "jbc_assess_lot_line",
            raw,
            LotLineAssessment,
            lambda: gate.assess_lot_line(
                mandate_path, mandate_json, workspace_subpath, target_dirs, permit_path
            ),
        )

    return _dossier_tool, _ast_tool, _lot_line_tool


def _register_statutory_tools(
    server: MCPServer[object],
    gate: JezzWorkspaceGate,
    sup: HotReloadSupervisor,
) -> None:
    list_fn, get_fn, verify_fn = _build_catalog_handlers(sup)
    ws_fn, diff_fn, preview_fn = _build_inspection_handlers(gate, sup)
    dossier_fn, ast_fn, lot_line_fn = _build_dossier_and_graph_handlers(gate, sup)
    specs: tuple[tuple[str, str, str, Callable[..., object]], ...] = (
        (
            "jbc_list_codes",
            "List JBC Edition 1.3 Building Codes",
            "List JBC v1.3 provisions filtered optionally by CodeArticle or CodeTrigger.",
            list_fn,
        ),
        (
            "jbc_get_code",
            "Get JBC Building Code Provision",
            "Fetch normative rule, hazard, pass criteria, and standards citations for a JBC code.",
            get_fn,
        ),
        (
            "jbc_verify_behavior_graph",
            "Verify Pillar 1 BehaviorGraph",
            "Verify state-machine transitions and Kildall dataflow laws on a BehaviorGraph.",
            verify_fn,
        ),
        (
            "jbc_inspect_workspace",
            "Inspect Workspace (JBC Rough-In)",
            "Run JBC v1.3 Rough-In static inspection across workspace .py/.ts/.tsx/.rs/.go files.",
            ws_fn,
        ),
        (
            "jbc_inspect_differential",
            "Inspect Differential Scope (Git Ratchet)",
            "Gate Git-modified/untracked (--changed-since, default HEAD) or staged source files.",
            diff_fn,
        ),
        (
            "jbc_preview_source",
            "Preview Unsaved Source Buffer",
            "Overlay an in-memory .py/.ts/.tsx/.rs/.go buffer onto the workspace for JBC check.",
            preview_fn,
        ),
        (
            "jbc_audit_dossier",
            "Audit PCE Engineering Dossier",
            "Verify 5-phase Permit-and-Contract Engineering dossier grounding and occupancy seal.",
            dossier_fn,
        ),
        (
            "jbc_query_ast_graph",
            "Query Workspace AST Graph",
            "Inspect deterministic module import layers, symbol call sites, and AST/CST slices.",
            ast_fn,
        ),
        (
            "jbc_assess_lot_line",
            "Assess Mandate Lot-Line Shear",
            "Evaluate JBC-COG-006 floorplates, API sufficiency, and CRP lot-line disposition.",
            lot_line_fn,
        ),
    )
    for name, title, description, handler in specs:
        server.add_tool(
            handler,
            name=name,
            title=title,
            description=description,
            annotations=_READ_ONLY_ANNOTATIONS,
            structured_output=True,
        )


@jezz_callable
def build_jezz_mcp_server(
    workspace_root: Path | None = None,
    *,
    watched_package_root: Path | None = None,
    supervisor: GuardedSupervisor | None = None,
) -> GuardedMcpServer:
    """Construct a configured, hot-reloading ``MCPServer`` jailed to ``workspace_root``."""
    gate = JezzWorkspaceGate(workspace_root=workspace_root)
    active_sup = supervisor or HotReloadSupervisor(
        workspace_root=gate.anchor_root,
        package_root=watched_package_root,
    )
    server: MCPServer[object] = MCPServer(
        name=SERVER_NAME,
        title="Jezz Software Building Code & PCE Statutory Gate",
        instructions=_SERVER_INSTRUCTIONS,
        version=SERVER_VERSION,
        log_level="WARNING",
    )
    _register_statutory_tools(server, gate, active_sup)
    return server


@jezz_callable
def run_stdio_mcp_server(workspace_root: Path | None = None) -> None:
    """Run the Jezz statutory MCP server over standard input/output (stdio JSON-RPC)."""
    server = build_jezz_mcp_server(workspace_root=workspace_root)
    asyncio.run(server.run_stdio_async())
