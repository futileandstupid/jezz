"""Read-only Model Context Protocol (MCP) statutory gate server for JBC v1.2 and PCE."""

import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Final

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from jezz.behaviors.graph import BehaviorGraph, GraphVerificationReport, verify_behavior_graph
from jezz.codes.ast_graph import (
    ModuleGraphEntry,
    SymbolGraphEntry,
    build_workspace_ast_graph,
    extract_symbol_source_slice,
)
from jezz.codes.ast_utils import SourceUnit, parse_source_unit
from jezz.codes.codebook import CODE_BOOK, CODEBOOK_EDITION
from jezz.codes.discovery import (
    build_workspace_inspector,
    discover_workspace_units,
    load_contained_permit,
    parse_optional_iso_date,
    read_contained_json,
    require_workspace_units,
)
from jezz.codes.permits import InspectionReport
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
from jezz.methodology.dossier import DossierAuditReport, EngineeringDossier, audit_pce_dossier

SERVER_NAME: Final[str] = "jezz"
SERVER_VERSION: Final[str] = "0.1.0"

_READ_ONLY_ANNOTATIONS: Final[ToolAnnotations] = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

_SERVER_INSTRUCTIONS: Final[str] = (
    "Jezz Software Building Code (JBC Edition 1.2) & Permit-and-Contract Engineering (PCE) "
    "statutory gate server. Use jbc_preview_source to validate candidate Python buffers in "
    "memory (including whole-workspace JBC-COG-003 cycle/encapsulation checks) before writing "
    "to disk; use jbc_inspect_differential or jbc_inspect_workspace for Rough-In verification; "
    "use jbc_query_ast_graph for workspace AST import/call/symbol slices; use jbc_get_code / "
    "jbc_list_codes for normative JBC provisions; and use jbc_verify_behavior_graph and "
    "jbc_audit_dossier for Pillar 1 and Pillar 3 verification."
)

type GuardedMcpServer = Annotated[MCPServer[object], InstanceGuard(MCPServer)]


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
    """Compact statutory summary of a single JBC Edition 1.2 BuildingCode provision."""

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
    """Filtered JBC Edition 1.2 catalog response returned by ``jbc_list_codes``."""

    model_config = STRICT_FROZEN_CONFIG

    edition: IdentifierStr
    total_matching: NonNegativeInt
    provisions: Annotated[
        tuple[BuildingCodeSummary, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=256),
    ]


class AstGraphQueryResult(BaseModel):
    """Structured AST import, call, and symbol-slice query response for ``jbc_query_ast_graph``."""

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


@jezz_callable
def list_jbc_codes(
    article: CodeArticle | None = None,
    trigger: CodeTrigger | None = None,
) -> JbcCatalogPayload:
    """List JBC Edition 1.2 provisions filtered optionally by ``article`` and ``trigger``."""
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
            "identifier such as 'JBC-DBC-001' or 'JBC-COG-003' (call jbc_list_codes)."
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
    return f"Python SyntaxError in '{target}'{line_part}: {detail}"


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
    existing_units: Sequence[SourceUnit],
    candidate_unit: SourceUnit,
) -> tuple[SourceUnit, ...]:
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
    if rel_posix == "." or not rel_posix.endswith(".py"):
        msg = f"Preview file_path '{file_path}' must target a '.py' path inside the workspace."
        raise ToolError(msg)
    permit = load_contained_permit(_resolve_optional_file(anchor_root, permit_path))
    reference_date = parse_optional_iso_date(as_of)
    candidate_unit = parse_source_unit(rel_posix, source_code)
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
    """Workspace-jailed JBC v1.2 and PCE verification gate bound to ``anchor_root``."""

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
        target_dirs: Annotated[
            tuple[IdentifierStr, ...],
            WIRE_TUPLE_VALIDATOR,
            Field(max_length=64),
        ] = (),
        permit_path: IdentifierStr | None = None,
        as_of: IdentifierStr | None = None,
    ) -> InspectionReport:
        """Run JBC Edition 1.2 Rough-In static inspection across the workspace."""
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
        """Run Git-aware Differential Occupancy Ratchet on touched Python files."""
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
        """Validate an unsaved Python buffer in memory overlaid onto the workspace graph."""
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
        target_dirs: Annotated[
            tuple[IdentifierStr, ...],
            WIRE_TUPLE_VALIDATOR,
            Field(max_length=64),
        ] = (),
    ) -> DossierAuditReport:
        """Audit a 5-phase PCE EngineeringDossier against compiled workspace AST symbols."""
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
        target_dirs: Annotated[
            tuple[IdentifierStr, ...],
            WIRE_TUPLE_VALIDATOR,
            Field(max_length=64),
        ] = (),
        symbol_query: IdentifierStr | None = None,
        caller_of: IdentifierStr | None = None,
    ) -> AstGraphQueryResult:
        """Query the workspace AST import/topological-layer graph, callers, and symbol slices."""
        return _run_tool_safely(
            lambda: _run_ast_graph_query(
                self._anchor_root,
                workspace_subpath,
                target_dirs,
                symbol_query,
                caller_of,
            )
        )


def _make_preview_handler(gate: JezzWorkspaceGate) -> Callable[..., DifferentialInspectionReport]:
    def _preview_tool(
        file_path: IdentifierStr,
        source_code: str,
        workspace_subpath: IdentifierStr = ".",
        permit_path: IdentifierStr | None = None,
        as_of: IdentifierStr | None = None,
    ) -> DifferentialInspectionReport:
        return gate.preview_source(
            file_path=file_path,
            source_code=source_code,
            workspace_subpath=workspace_subpath,
            permit_path=permit_path,
            as_of=as_of,
        )

    return _preview_tool


def _register_statutory_tools(server: MCPServer[object], gate: JezzWorkspaceGate) -> None:
    specs: tuple[tuple[str, str, str, Callable[..., object]], ...] = (
        (
            "jbc_list_codes",
            "List JBC Edition 1.2 Building Codes",
            "List JBC v1.2 provisions filtered optionally by CodeArticle or CodeTrigger.",
            list_jbc_codes,
        ),
        (
            "jbc_get_code",
            "Get JBC Building Code Provision",
            "Fetch normative rule, hazard, pass criteria, and standards citations for a JBC code.",
            get_jbc_code,
        ),
        (
            "jbc_verify_behavior_graph",
            "Verify Pillar 1 BehaviorGraph",
            "Verify state-machine transitions and Kildall dataflow laws on a BehaviorGraph.",
            verify_jbc_behavior_graph,
        ),
        (
            "jbc_inspect_workspace",
            "Inspect Workspace (JBC Rough-In)",
            "Run full JBC v1.2 Rough-In static inspection across workspace Python files.",
            gate.inspect_workspace,
        ),
        (
            "jbc_inspect_differential",
            "Inspect Differential Scope (Git Ratchet)",
            "Gate Git-modified/untracked (--changed-since, default HEAD) or staged Python files.",
            gate.inspect_differential,
        ),
        (
            "jbc_preview_source",
            "Preview Unsaved Python Buffer",
            "Overlay an in-memory Python buffer onto the workspace graph for pre-write JBC check.",
            _make_preview_handler(gate),
        ),
        (
            "jbc_audit_dossier",
            "Audit PCE Engineering Dossier",
            "Verify 5-phase Permit-and-Contract Engineering dossier grounding and occupancy seal.",
            gate.audit_dossier,
        ),
        (
            "jbc_query_ast_graph",
            "Query Workspace AST Graph",
            "Inspect deterministic module import layers, symbol call sites, and AST source slices.",
            gate.query_ast_graph,
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
def build_jezz_mcp_server(workspace_root: Path | None = None) -> GuardedMcpServer:
    """Construct a configured, read-only ``MCPServer`` jailed to ``workspace_root``."""
    gate = JezzWorkspaceGate(workspace_root=workspace_root)
    server: MCPServer[object] = MCPServer(
        name=SERVER_NAME,
        title="Jezz Software Building Code & PCE Statutory Gate",
        instructions=_SERVER_INSTRUCTIONS,
        version=SERVER_VERSION,
        log_level="WARNING",
    )
    _register_statutory_tools(server, gate)
    return server


@jezz_callable
def run_stdio_mcp_server(workspace_root: Path | None = None) -> None:
    """Run the Jezz statutory MCP server over standard input/output (stdio JSON-RPC)."""
    server = build_jezz_mcp_server(workspace_root=workspace_root)
    asyncio.run(server.run_stdio_async())
