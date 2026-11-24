"""Track 18: Statutory <= 1,000 LOC module ceiling and acyclic decomposition invariants."""

from pathlib import Path

from jezz.agents.nodes import (
    FrameNode,
    PceGraphDeps,
    PceInteractionHooks,
    validate_runtime_node_coverage,
)
from jezz.agents.orchestrator import build_pce_workflow_graph
from jezz.agents.specialists import (
    JBC_CANONICAL_EXEMPLAR,
    JBC_STATUTORY_BRIEF,
    validate_execution_board_invariants,
)
from jezz.agents.workspace import (
    WorkspaceSandbox,
    find_type_alias_misuse,
    verify_runtime_importable_patches,
)
from jezz.cli import main
from jezz.codes.codebook import CODE_BOOK, validate_codebook_invariants
from jezz.codes.discovery import build_workspace_inspector, discover_workspace_units
from jezz.codes.polyglot_go import parse_go_unit
from jezz.codes.polyglot_rust import parse_rust_unit
from jezz.codes.polyglot_ts import parse_typescript_unit
from jezz.guards import ContainedPath, LoopBudget, read_bounded_text

_MAX_PRODUCTION_MODULE_LINES = 1_000


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_every_production_module_is_at_most_1000_lines() -> None:
    """INV-018-01: Every production Python module in src/jezz/ contains <= 1,000 lines."""
    root = _repo_root()
    src_jezz = ContainedPath.from_parts(root, "src/jezz").resolved_path
    py_files = sorted(src_jezz.rglob("*.py"))
    assert len(py_files) >= 70

    budget = LoopBudget(max_steps=512)
    oversized: list[str] = []
    for path in py_files:
        budget.tick()
        rel = path.relative_to(root).as_posix()
        contained = ContainedPath.from_parts(root, rel)
        line_count = len(read_bounded_text(contained, max_bytes=1_048_576).splitlines())
        if line_count > _MAX_PRODUCTION_MODULE_LINES:
            oversized.append(f"{rel} ({line_count} lines > {_MAX_PRODUCTION_MODULE_LINES})")

    assert oversized == [], f"Production modules exceed 1,000 LOC ceiling: {oversized}"

    units = discover_workspace_units(root)
    inspector = build_workspace_inspector(root, units)
    report = inspector.inspect_rough_in(units)
    assert report.is_occupiable, [f.message for f in report.unwaived_violations()]


def test_decomposed_pce_graph_resolves_all_node_transitions() -> None:
    """INV-018-02: Decomposed modules preserve graph assembly, invariants, and re-exports."""
    node_map = validate_runtime_node_coverage()
    assert len(node_map) == 21
    board = validate_execution_board_invariants()
    assert len(board) == 21
    validate_codebook_invariants(CODE_BOOK)
    assert len(CODE_BOOK) == 31

    graph = build_pce_workflow_graph()
    assert graph is not None
    assert FrameNode is not None
    assert PceGraphDeps is not None
    assert PceInteractionHooks is not None
    assert len(JBC_STATUTORY_BRIEF) > 100
    assert len(JBC_CANONICAL_EXEMPLAR) > 100
    assert WorkspaceSandbox is not None
    assert callable(verify_runtime_importable_patches)
    assert callable(find_type_alias_misuse)
    assert callable(main)

    ts_unit = parse_typescript_unit(
        "src/check.ts",
        "export function add(a: bigint, b: bigint): bigint {\n"
        "  if (a < 0n || b < 0n) { throw new RangeError('neg'); }\n"
        "  return a + b;\n"
        "}\n",
    )
    rs_unit = parse_rust_unit(
        "src/lib.rs",
        "pub fn add(a: u64, b: u64) -> Option<u64> { a.checked_add(b) }\n",
    )
    go_unit = parse_go_unit(
        "pkg/add.go",
        'package pkg\n\nimport "errors"\n\n'
        "func Add(a int64, b int64) (int64, error) {\n"
        '    if a < 0 || b < 0 { return 0, errors.New("neg") }\n'
        "    return a + b, nil\n"
        "}\n",
    )
    assert len(ts_unit.callables) == 1
    assert len(rs_unit.callables) == 1
    assert len(go_unit.callables) == 1
