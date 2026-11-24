"""Track 19 statutory falsification suite: polyglot inter-procedural call-flow analysis."""

from collections.abc import Sequence

from jezz.codes.adapters import parse_any_source_unit
from jezz.codes.call_flow import build_workspace_call_flow
from jezz.codes.inspector import BuildingInspector
from jezz.codes.polyglot_ir import InspectedUnit
from jezz.codes.ratchet import DifferentialScope, inspect_differential_rough_in


def _inspect_codes(units: Sequence[InspectedUnit]) -> set[str]:
    report = BuildingInspector().inspect_rough_in(units)
    return {f.code_id for f in report.unwaived_violations()}


def _build_python_sql_units(*, dynamic: bool) -> tuple[InspectedUnit, ...]:
    helper_src = (
        "import sqlite3\n\n"
        "def _leaf_exec(conn: sqlite3.Connection, stmt: str) -> None:\n"
        "    conn.execute(stmt)\n\n"
        "def run_sql_step(conn: sqlite3.Connection, stmt: str) -> None:\n"
        "    _leaf_exec(conn, stmt)\n\n"
        "def build_user_sql(table_name: str) -> str:\n"
        + ('    return f"SELECT id FROM {table_name}"\n' if dynamic else '    return "SELECT 1"\n')
    )
    caller_expr = (
        "f\"SELECT * FROM accounts WHERE owner = '{owner}'\""
        if dynamic
        else '"SELECT * FROM accounts WHERE id = 1"'
    )
    caller_src = (
        "import sqlite3\n"
        "from app.db_helper import build_user_sql, run_sql_step\n\n"
        "def _query_via_wrapper(conn: sqlite3.Connection, owner: str) -> None:\n"
        f"    query = {caller_expr}\n"
        "    run_sql_step(conn, query)\n\n"
        "def _query_via_builder(conn: sqlite3.Connection, tbl: str) -> None:\n"
        "    stmt = build_user_sql(tbl)\n"
        "    conn.execute(stmt)\n"
    )
    return (
        parse_any_source_unit("src/app/db_helper.py", helper_src),
        parse_any_source_unit("src/app/service.py", caller_src),
    )


def _build_ts_sql_units(*, dynamic: bool) -> tuple[InspectedUnit, ...]:
    builder_ret = "`SELECT * FROM ${tbl}`" if dynamic else '"SELECT 1"'
    wrapper_arg = "`SELECT * FROM t WHERE id = ${uid}`" if dynamic else '"SELECT 1"'
    helper_src = (
        "export interface SqlClient { query(sql: string): string; }\n"
        "function leafQuery(db: SqlClient, sql: string): string { return db.query(sql); }\n"
        "export function runSql(db: SqlClient, sql: string): string {\n"
        "  if (sql.length === 0 || sql.length > 512) { throw new RangeError('len'); }\n"
        "  return leafQuery(db, sql);\n"
        "}\n"
        "export function buildQuery(tbl: string): string {\n"
        "  if (tbl.length === 0 || tbl.length > 64) { throw new RangeError('tbl'); }\n"
        f"  return {builder_ret};\n"
        "}\n"
    )
    caller_src = (
        'import { buildQuery, runSql, type SqlClient } from "./db_helper";\n'
        "function callWrapper(db: SqlClient, uid: string): string {\n"
        f"  const q = {wrapper_arg};\n"
        "  return runSql(db, q);\n"
        "}\n"
        "function callBuilder(db: SqlClient, tbl: string): string {\n"
        "  const q = buildQuery(tbl);\n"
        "  return db.query(q);\n"
        "}\n"
        "export function runBoth(db: SqlClient, uid: string): string {\n"
        "  if (uid.length === 0 || uid.length > 64) { throw new RangeError('uid'); }\n"
        "  return callWrapper(db, uid) + callBuilder(db, uid);\n"
        "}\n"
    )
    return (
        parse_any_source_unit("src/db_helper.ts", helper_src),
        parse_any_source_unit("src/service.ts", caller_src),
    )


def _build_rust_sql_units(*, dynamic: bool) -> tuple[InspectedUnit, ...]:
    builder_ret = 'format!("SELECT * FROM {tbl}")' if dynamic else 'String::from("SELECT 1")'
    wrapper_arg = (
        'format!("SELECT * FROM t WHERE k = {uid}")' if dynamic else 'String::from("SELECT 1")'
    )
    helper_src = (
        "pub struct DbConn { pub tag: u32 }\n"
        "impl DbConn {\n"
        "    pub fn execute(&self, sql: &str) -> usize {\n"
        "        if sql.is_empty() || sql.len() > 512 { return 0; }\n"
        "        sql.len()\n"
        "    }\n"
        "}\n"
        "fn leaf_exec(db: &DbConn, sql: &str) -> usize { db.execute(sql) }\n"
        "pub fn run_sql(db: &DbConn, sql: &str) -> usize {\n"
        "    if sql.is_empty() || sql.len() > 512 { return 0; }\n"
        "    leaf_exec(db, sql)\n"
        "}\n"
        "pub fn build_sql(tbl: &str) -> String {\n"
        "    if tbl.is_empty() || tbl.len() > 64 { return String::new(); }\n"
        f"    {builder_ret}\n"
        "}\n"
    )
    caller_src = (
        "use crate::db_helper::{DbConn, build_sql, run_sql};\n"
        "fn via_wrapper(db: &DbConn, uid: &str) -> usize {\n"
        f"    let q = {wrapper_arg};\n"
        "    run_sql(db, &q)\n"
        "}\n"
        "fn via_builder(db: &DbConn, tbl: &str) -> usize {\n"
        "    let q = build_sql(tbl);\n"
        "    db.execute(&q)\n"
        "}\n"
        "pub fn run_all(db: &DbConn, uid: &str) -> usize {\n"
        "    if uid.is_empty() || uid.len() > 64 { return 0; }\n"
        "    via_wrapper(db, uid) + via_builder(db, uid)\n"
        "}\n"
    )
    return (
        parse_any_source_unit("src/db_helper.rs", helper_src),
        parse_any_source_unit("src/service.rs", caller_src),
    )


def _build_go_sql_units(*, dynamic: bool) -> tuple[InspectedUnit, ...]:
    builder_ret = 'fmt.Sprintf("SELECT * FROM %s", tbl)' if dynamic else '"SELECT 1"'
    wrapper_arg = 'fmt.Sprintf("SELECT * FROM t WHERE k = %s", uid)' if dynamic else '"SELECT 1"'
    helper_src = (
        "package app\n\n"
        'import "fmt"\n\n'
        "type SqlRunner struct{ Tag int }\n\n"
        "func (r SqlRunner) Query(sql string) int {\n"
        "    if len(sql) == 0 || len(sql) > 512 { return r.Tag }\n"
        "    return len(sql) + r.Tag\n"
        "}\n\n"
        "func leafQuery(db SqlRunner, sql string) int { return db.Query(sql) }\n\n"
        "func RunSQL(db SqlRunner, sql string) int {\n"
        "    if len(sql) == 0 || len(sql) > 512 { return 0 }\n"
        "    return leafQuery(db, sql)\n"
        "}\n\n"
        "func BuildSQL(tbl string) string {\n"
        '    if len(tbl) == 0 || len(tbl) > 64 { return fmt.Sprint("SELECT 0") }\n'
        f"    return {builder_ret}\n"
        "}\n"
    )
    caller_src = (
        "package app\n\n"
        'import "fmt"\n\n'
        "func viaWrapper(db SqlRunner, uid string) int {\n"
        f"    q := {wrapper_arg}\n"
        "    return RunSQL(db, q)\n"
        "}\n\n"
        "func viaBuilder(db SqlRunner, uid string) int {\n"
        '    if uid == "" { _ = fmt.Sprint(uid) }\n'
        "    q := BuildSQL(uid)\n"
        "    return db.Query(q)\n"
        "}\n"
    )
    return (
        parse_any_source_unit("app/db_helper.go", helper_src),
        parse_any_source_unit("app/service.go", caller_src),
    )


def test_interprocedural_sql_sink_and_source_chains_across_four_languages() -> None:
    """INV-019-01: Dynamic SQL taint propagates across call chains in Python, TS, Rust, and Go."""
    for builder in (
        _build_python_sql_units,
        _build_ts_sql_units,
        _build_rust_sql_units,
        _build_go_sql_units,
    ):
        tainted_codes = _inspect_codes(builder(dynamic=True))
        assert "JBC-SEC-001" in tainted_codes
        static_codes = _inspect_codes(builder(dynamic=False))
        assert "JBC-SEC-001" not in static_codes

    cycle_py = parse_any_source_unit(
        "src/app/cycle.py",
        "import sqlite3\n"
        "def _ping(conn: sqlite3.Connection, q: str, fuel: int) -> None:\n"
        "    if fuel <= 0:\n"
        "        conn.execute(q)\n"
        "        return\n"
        "    _pong(conn, q, fuel - 1)\n"
        "def _pong(conn: sqlite3.Connection, q: str, fuel: int) -> None:\n"
        "    if fuel <= 0:\n"
        "        return\n"
        "    _ping(conn, q, fuel - 1)\n"
        "def _entry(conn: sqlite3.Connection, user: str) -> None:\n"
        '    _pong(conn, f"SELECT * FROM u WHERE name = {user}", 2)\n',
    )
    flow = build_workspace_call_flow((cycle_py,))
    assert len(flow.summaries) == 3
    assert "JBC-SEC-001" in _inspect_codes((cycle_py,))


def test_interprocedural_path_taint_and_containment_across_four_languages() -> None:
    """INV-019-01: Uncontained path builders/wrappers trigger JBC-SEC-005 while anchors pass."""
    py_bad = parse_any_source_unit(
        "src/app/fs_bad.py",
        "from pathlib import Path\n"
        "def _unlink_target(target: Path) -> None:\n"
        "    target.unlink()\n"
        "def _purge_uncontained(rel_name: str) -> None:\n"
        "    _unlink_target(Path(rel_name))\n",
    )
    py_good = parse_any_source_unit(
        "src/app/fs_good.py",
        "from pathlib import Path\n"
        "from jezz.guards import ContainedPath\n"
        "def _unlink_target(target: Path) -> None:\n"
        "    target.unlink()\n"
        "def _purge_contained(root: Path, rel_name: str) -> None:\n"
        "    safe = ContainedPath.from_parts(root, rel_name)\n"
        "    _unlink_target(safe.resolved_path)\n",
    )
    assert "JBC-SEC-005" in _inspect_codes((py_bad,))
    assert "JBC-SEC-005" not in _inspect_codes((py_good,))

    ts_bad = parse_any_source_unit(
        "src/fs_bad.ts",
        'import * as fs from "node:fs";\n'
        'import * as path from "node:path";\n'
        "function joinRaw(root: string, rel: string): string { return path.join(root, rel); }\n"
        "function wipe(root: string, rel: string): void { fs.rmSync(joinRaw(root, rel)); }\n",
    )
    ts_good = parse_any_source_unit(
        "src/fs_good.ts",
        'import * as fs from "node:fs";\n'
        'import * as path from "node:path";\n'
        "function joinAnchored(root: string, rel: string): string {\n"
        "  const candidate = path.join(root, rel);\n"
        "  if (!candidate.startsWith(root)) { throw new Error('escape'); }\n"
        "  return candidate;\n"
        "}\n"
        "function wipe(root: string, rel: string): void { fs.rmSync(joinAnchored(root, rel)); }\n",
    )
    assert "JBC-SEC-005" in _inspect_codes((ts_bad,))
    assert "JBC-SEC-005" not in _inspect_codes((ts_good,))

    rs_bad = parse_any_source_unit(
        "src/fs_bad.rs",
        "use std::path::{Path, PathBuf};\n"
        "fn raw_join(root: &Path, rel: &str) -> PathBuf { root.join(rel) }\n"
        "fn wipe(root: &Path, rel: &str) -> std::io::Result<()> {\n"
        "    std::fs::remove_file(raw_join(root, rel))\n"
        "}\n",
    )
    rs_good = parse_any_source_unit(
        "src/fs_good.rs",
        "use std::path::{Path, PathBuf};\n"
        "fn safe_join(root: &Path, rel: &str) -> std::io::Result<PathBuf> {\n"
        "    let cand = root.join(rel).canonicalize()?;\n"
        "    if !cand.starts_with(root) {\n"
        '        return Err(std::io::Error::other("escape"));\n'
        "    }\n"
        "    Ok(cand)\n"
        "}\n"
        "fn wipe(root: &Path, rel: &str) -> std::io::Result<()> {\n"
        "    std::fs::remove_file(safe_join(root, rel)?)\n"
        "}\n",
    )
    assert "JBC-SEC-005" in _inspect_codes((rs_bad,))
    assert "JBC-SEC-005" not in _inspect_codes((rs_good,))

    go_bad = parse_any_source_unit(
        "app/fs_bad.go",
        "package app\n\n"
        'import (\n    "os"\n    "path/filepath"\n)\n\n'
        "func rawJoin(root string, rel string) string { return filepath.Join(root, rel) }\n"
        "func wipe(root string, rel string) error { return os.Remove(rawJoin(root, rel)) }\n",
    )
    go_good = parse_any_source_unit(
        "app/fs_good.go",
        "package app\n\n"
        'import (\n    "os"\n    "path/filepath"\n    "strings"\n)\n\n'
        "func safeJoin(root string, rel string) string {\n"
        "    cand := filepath.Join(root, rel)\n"
        '    if !strings.HasPrefix(cand, root) { return "" }\n'
        "    return cand\n"
        "}\n"
        "func wipe(root string, rel string) error {\n"
        "    return os.Remove(safeJoin(root, rel))\n"
        "}\n",
    )
    assert "JBC-SEC-005" in _inspect_codes((go_bad,))
    assert "JBC-SEC-005" not in _inspect_codes((go_good,))


def test_interprocedural_validator_helper_delegation_precision() -> None:
    """INV-019-02: Workspace validator helpers satisfy DBC-001/004/005; non-guards still fail."""
    py_ok = parse_any_source_unit(
        "src/app/math_ok.py",
        "from typing import Annotated\n"
        "from pydantic import Field\n"
        "from jezz.guards import jezz_callable\n"
        "type BoundedInt = Annotated[int, Field(ge=-10_000, le=10_000)]\n"
        "def _ensure_non_zero(divisor: BoundedInt) -> None:\n"
        "    if divisor == 0:\n"
        "        raise ValueError('zero divisor')\n"
        "@jezz_callable\n"
        "def divide_pool(total: BoundedInt, divisor: BoundedInt) -> BoundedInt:\n"
        "    _ensure_non_zero(divisor)\n"
        "    return total // divisor\n",
    )
    py_bad = parse_any_source_unit(
        "src/app/math_bad.py",
        "from typing import Annotated\n"
        "from pydantic import Field\n"
        "from jezz.guards import jezz_callable\n"
        "type BoundedInt = Annotated[int, Field(ge=-10_000, le=10_000)]\n"
        "def _touch_only(divisor: BoundedInt) -> BoundedInt:\n"
        "    return divisor\n"
        "@jezz_callable\n"
        "def divide_pool(total: BoundedInt, divisor: BoundedInt) -> BoundedInt:\n"
        "    _touch_only(divisor)\n"
        "    return total // divisor\n",
    )
    assert "JBC-DBC-001" not in _inspect_codes((py_ok,))
    assert "JBC-DBC-001" in _inspect_codes((py_bad,))

    ts_ok = parse_any_source_unit(
        "src/pool_ok.ts",
        "function checkBounds(items: readonly bigint[], label: string, div: bigint): void {\n"
        "  if (items.length === 0 || items.length > 64 || label.length === 0 || div === 0n) {\n"
        "    throw new RangeError('invalid domain args');\n"
        "  }\n"
        "}\n"
        "export function splitPool(\n"
        "  items: readonly bigint[], label: string, div: bigint\n"
        "): bigint {\n"
        "  checkBounds(items, label, div);\n"
        "  return items[0]! / div;\n"
        "}\n",
    )
    ts_bad = parse_any_source_unit(
        "src/pool_bad.ts",
        "function checkItemsOnly(items: readonly bigint[]): void {\n"
        "  if (items.length === 0 || items.length > 64) { throw new RangeError('items'); }\n"
        "}\n"
        "export function splitPool(\n"
        "  items: readonly bigint[], label: string, div: bigint\n"
        "): bigint {\n"
        "  checkItemsOnly(items);\n"
        "  return items[0]! / div;\n"
        "}\n",
    )
    assert not ({"JBC-DBC-001", "JBC-DBC-004", "JBC-DBC-005"} & _inspect_codes((ts_ok,)))
    assert {"JBC-DBC-001", "JBC-DBC-004", "JBC-DBC-005"} <= _inspect_codes((ts_bad,))

    rs_ok = parse_any_source_unit(
        "src/pool_ok.rs",
        "fn compute_checked(items: &[i64], label: &str, div: i64) -> Result<i64, String> {\n"
        "    if items.is_empty() || items.len() > 64 || label.is_empty() || div == 0 {\n"
        '        return Err(String::from("bounds"));\n'
        "    }\n"
        "    Ok(items[0] / div)\n"
        "}\n"
        "pub fn split_pool(items: &[i64], label: &str, div: i64) -> Result<i64, String> {\n"
        "    compute_checked(items, label, div)\n"
        "}\n",
    )
    rs_bad = parse_any_source_unit(
        "src/pool_bad.rs",
        "fn compute_unchecked(items: &[i64], _label: &str) -> Result<i64, String> {\n"
        "    Ok(items.len() as i64)\n"
        "}\n"
        "pub fn split_pool(items: &[i64], label: &str) -> Result<i64, String> {\n"
        "    compute_unchecked(items, label)\n"
        "}\n",
    )
    assert not ({"JBC-DBC-001", "JBC-DBC-004", "JBC-DBC-005"} & _inspect_codes((rs_ok,)))
    assert {"JBC-DBC-004", "JBC-DBC-005"} <= _inspect_codes((rs_bad,))

    go_ok = parse_any_source_unit(
        "app/pool_ok.go",
        "package app\n\n"
        'import "errors"\n\n'
        "func computeChecked(items []int64, label string, div int64) (int64, error) {\n"
        "    if len(items) == 0 || len(items) > 64 || len(label) == 0 || div == 0 {\n"
        '        return 0, errors.New("bounds")\n'
        "    }\n"
        "    return items[0] / div, nil\n"
        "}\n\n"
        "func SplitPool(items []int64, label string, div int64) (int64, error) {\n"
        "    return computeChecked(items, label, div)\n"
        "}\n",
    )
    go_bad = parse_any_source_unit(
        "app/pool_bad.go",
        "package app\n\n"
        "func computeUnchecked(items []int64, label string) int64 {\n"
        "    return int64(len(items) + len(label))\n"
        "}\n\n"
        "func SplitPool(items []int64, label string) int64 {\n"
        "    return computeUnchecked(items, label)\n"
        "}\n",
    )
    assert not ({"JBC-DBC-001", "JBC-DBC-004", "JBC-DBC-005"} & _inspect_codes((go_ok,)))
    assert {"JBC-DBC-004", "JBC-DBC-005"} <= _inspect_codes((go_bad,))


def test_differential_ratchet_catches_cross_module_taint_regression() -> None:
    """INV-019-03: Differential ratchet flags cross-module taint anchored on touched files."""
    inspector = BuildingInspector()
    cases = (
        (_build_python_sql_units(dynamic=True), "src/app/service.py"),
        (_build_ts_sql_units(dynamic=True), "src/service.ts"),
        (_build_rust_sql_units(dynamic=True), "src/service.rs"),
        (_build_go_sql_units(dynamic=True), "app/service.go"),
    )
    for units, touched_path in cases:
        diff = inspect_differential_rough_in(
            inspector,
            units,
            DifferentialScope(changed_paths=frozenset({touched_path})),
        )
        assert any(
            f.code_id == "JBC-SEC-001" and f.location.file_path == touched_path
            for f in diff.report.unwaived_violations()
        )
