"""Track 16: Multi-ecosystem PROVISION, polyglot BHV-06 CONSULT, and guard/taint precision."""

import json
import os
import textwrap
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from jezz.agents import (
    ConstructionPatchBundle,
    ConsultationBrief,
    ContractSpecificationBundle,
    EnvironmentObservation,
    EpistemicObservation,
    FilePatchArtifact,
    InProcessGateRunner,
    InProcessProvisionRunner,
    InstrumentationSuiteBundle,
    PceRunStatus,
    PceWorkflowOrchestrator,
    PolyglotSubprocessProvisionRunner,
    ProvisionPlan,
    WorkspaceSandbox,
    build_consultation_reference_pack,
    compute_missing_dependencies,
    group_requirements_by_ecosystem,
    inspect_polyglot_package_api,
    refresh_inspector_after_provision,
    uniform_model_suite,
)
from jezz.behaviors import BehaviorKind
from jezz.codes import (
    BuildingInspector,
    InspectedUnit,
    PolyglotManifest,
    SupportedLanguage,
    parse_go_unit,
    parse_rust_unit,
    parse_source_unit,
    parse_typescript_unit,
)
from jezz.guards import ContainedPath
from jezz.methodology import (
    ConsultTargetStr,
    DependencyRequirement,
    DomainInvariant,
    EngineeringMandate,
    ModuleWriteScope,
)

_CONSULT_ADAPTER = TypeAdapter(ConsultTargetStr)

_CLEAN_CALC_PY = textwrap.dedent(
    """\
    from typing import Annotated
    from pydantic import Field, validate_call

    type AmountCents = Annotated[int, Field(ge=1, le=10_000_000)]

    @validate_call(validate_return=True)
    def double_cents(amount: AmountCents) -> int:
        \"\"\"Return doubled positive integer cent amount.\"\"\"
        return amount * 2
    """
)

_CLEAN_CALC_TEST_PY = textwrap.dedent(
    """\
    from app.calc import double_cents

    def test_double_cents() -> None:
        assert double_cents(25) == 50
    """
)


def _write_contained(root: Path, rel: str, text: str) -> Path:
    target = ContainedPath.from_parts(root, rel)
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    target.resolved_path.write_text(text, encoding="utf-8")
    return target.resolved_path


def _write_shim(bin_dir: Path, name: str, script_body: str) -> Path:
    shim = ContainedPath.from_parts(bin_dir, name).resolved_path
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(f"#!/bin/sh\n{script_body}\n", encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _violation_codes(unit: InspectedUnit) -> set[str]:
    inspector = BuildingInspector(polyglot_manifest=PolyglotManifest())
    report = inspector.inspect_rough_in((unit,))
    return {f.code_id for f in report.unwaived_violations()}


# ---------------------------------------------------------------------------
# 1. INV-1601: Multi-Ecosystem PROVISION & Atomic Rollback
# ---------------------------------------------------------------------------


def _seed_quad_manifests(ws: Path) -> tuple[str, str]:
    orig_py = '[project]\nname = "quad-demo"\nversion = "0.1.0"\ndependencies = []\n'
    orig_pkg = json.dumps({"name": "quad-web", "version": "0.1.0", "dependencies": {}}) + "\n"
    _write_contained(ws, "pyproject.toml", orig_py)
    _write_contained(ws, "package.json", orig_pkg)
    _write_contained(
        ws,
        "Cargo.toml",
        '[package]\nname = "quad_rs"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write_contained(ws, "go.mod", "module example.com/quaddemo\n\ngo 1.23\n")
    return orig_py, orig_pkg


def _build_quad_requirements() -> tuple[DependencyRequirement, ...]:
    return (
        DependencyRequirement(
            package="pydantic",
            version_constraint=">=2.0",
            ecosystem=SupportedLanguage.PYTHON,
        ),
        DependencyRequirement(
            package="zod",
            version_constraint="^3.23.0",
            ecosystem=SupportedLanguage.TYPESCRIPT,
        ),
        DependencyRequirement(
            package="serde",
            version_constraint="1.0",
            ecosystem=SupportedLanguage.RUST,
        ),
        DependencyRequirement(
            package="github.com/go-chi/chi/v5",
            version_constraint="v5.1.0",
        ),
    )


def _select_mixed_payload(props: dict[str, object]) -> BaseModel:
    table: tuple[tuple[str, BaseModel], ...] = (
        (
            "python_version",
            EnvironmentObservation(python_version="3.14", summary="Inspected mixed env."),
        ),
        (
            "citations",
            ConsultationBrief(
                citations=("JBC-SEC-005", "npm:zod"),
                normative_constraints=("Confine relative paths and enforce strict schemas.",),
                summary="Anchored JBC and local npm API references.",
            ),
        ),
        (
            "approved_specs",
            ProvisionPlan(
                approved_specs=("pydantic>=2.0", "zod@^3.23.0"),
                rationale="Provision missing Python and TypeScript runtime packages.",
            ),
        ),
        (
            "inspected_files",
            EpistemicObservation(
                inspected_files=(),
                discovered_symbols=(),
                summary="Inspected empty workspace.",
            ),
        ),
        (
            "contract_patches",
            ContractSpecificationBundle(
                contract_patches=(
                    FilePatchArtifact(
                        file_path="src/app/__init__.py",
                        source_code='"""App package."""\n',
                        rationale="Package root.",
                    ),
                    FilePatchArtifact(
                        file_path="src/app/calc.py",
                        source_code=_CLEAN_CALC_PY,
                        rationale="Contract signature.",
                    ),
                ),
                declared_symbols=("double_cents",),
                summary="Specified contracts.",
            ),
        ),
        (
            "test_patches",
            InstrumentationSuiteBundle(
                test_patches=(
                    FilePatchArtifact(
                        file_path="tests/test_calc.py",
                        source_code=_CLEAN_CALC_TEST_PY,
                        rationale="Test suite.",
                    ),
                ),
                covered_invariant_ids=("INV-001",),
                summary="Authored tests.",
            ),
        ),
    )
    for key, model_obj in table:
        if key in props:
            return model_obj
    return ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/app/calc.py",
                source_code=_CLEAN_CALC_PY,
                rationale="Synthesized calc.",
            ),
        ),
        summary="Synthesized implementation.",
    )


def _build_mixed_provision_consult_model() -> FunctionModel:
    def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages
        props = (
            info.output_tools[0].parameters_json_schema.get("properties", {})
            if info.output_tools
            else {}
        )
        payload = _select_mixed_payload(props)
        if info.output_tools:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=info.output_tools[0].name,
                        args=payload.model_dump(mode="json"),
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content=payload.model_dump_json())])

    return FunctionModel(_respond)


def _assert_mandate_ecosystem_uniqueness() -> None:
    same_name_cross_eco = EngineeringMandate(
        mandate_id="MND-CROSS-ECO-01",
        title="Cross-Ecosystem Telemetry Provisioning",
        objective="Provision sentry packages across Python and TypeScript manifests.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Telemetry spans flush deterministically.",
                verification_symbol="test_double_cents",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_core",
                owned_files=frozenset({"src/app/calc.py"}),
                contract_symbols=("double_cents",),
            ),
        ),
        required_dependencies=(
            DependencyRequirement(package="sentry", ecosystem=SupportedLanguage.PYTHON),
            DependencyRequirement(package="sentry", ecosystem=SupportedLanguage.TYPESCRIPT),
        ),
    )
    assert len(same_name_cross_eco.required_dependencies) == 2
    with pytest.raises(ValidationError):
        EngineeringMandate(
            mandate_id="MND-DUP-ECO-01",
            title="Duplicate Ecosystem Requirement",
            objective="Reject duplicate package within identical ecosystem and scope.",
            invariants=same_name_cross_eco.invariants,
            write_scopes=same_name_cross_eco.write_scopes,
            required_dependencies=(
                DependencyRequirement(package="zod", ecosystem=SupportedLanguage.TYPESCRIPT),
                DependencyRequirement(package="zod", ecosystem=SupportedLanguage.TYPESCRIPT),
            ),
        )


async def _assert_mixed_orchestrator_run(
    base_dir: Path,
    deps: tuple[DependencyRequirement, ...],
) -> None:
    orch_ws = ContainedPath.from_parts(base_dir, "orch_ws").resolved_path
    orch_ws.mkdir(parents=True)
    _seed_quad_manifests(orch_ws)
    _write_contained(
        orch_ws,
        "node_modules/zod/package.json",
        json.dumps({"name": "zod", "version": "3.23.8", "types": "index.d.ts"}),
    )
    _write_contained(
        orch_ws,
        "node_modules/zod/index.d.ts",
        "export declare function object(shape: Record<string, unknown>): unknown;\n",
    )
    _write_contained(orch_ws, "docs/spec.md", "# Settlement Domain Contract\n")

    mandate = EngineeringMandate(
        mandate_id="MND-MIXED-PROV-01",
        title="Mixed Ecosystem Provision and Consult Run",
        objective="Provision Python and TypeScript packages and synthesize verified module.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="double_cents returns twice the positive cent input.",
                verification_symbol="test_double_cents",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_py",
                owned_files=frozenset(
                    {"src/app/__init__.py", "src/app/calc.py", "tests/test_calc.py"}
                ),
                contract_symbols=("double_cents", "test_double_cents"),
            ),
        ),
        required_dependencies=deps,
        consultation_targets=("JBC-SEC-005", "docs/spec.md", "npm:zod"),
    )
    orchestrator = PceWorkflowOrchestrator(
        uniform_model_suite(_build_mixed_provision_consult_model()),
        gate_runner=InProcessGateRunner(),
        provision_runner=InProcessProvisionRunner(),
    )
    result = await orchestrator.execute_mandate(orch_ws, mandate)
    assert result.status == PceRunStatus.OCCUPANCY_CERTIFIED
    assert {
        BehaviorKind.INSPECT_ENV,
        BehaviorKind.CONSULT,
        BehaviorKind.PROVISION,
    } <= result.induced_graph.distinct_kinds()


async def test_multi_ecosystem_provision_and_atomic_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_mandate_ecosystem_uniqueness()
    ws = ContainedPath.from_parts(tmp_path, "quad_ws").resolved_path
    ws.mkdir(parents=True)
    orig_py, orig_pkg = _seed_quad_manifests(ws)
    reqs = _build_quad_requirements()

    assert tuple(group_requirements_by_ecosystem(reqs)) == (
        SupportedLanguage.PYTHON,
        SupportedLanguage.TYPESCRIPT,
        SupportedLanguage.RUST,
        SupportedLanguage.GO,
    )

    bin_dir = ContainedPath.from_parts(tmp_path, "stub_bins").resolved_path
    _write_shim(bin_dir, "uv", "printf '\\n# mutated-by-uv\\n' >> pyproject.toml\nexit 0")
    _write_shim(bin_dir, "npm", 'echo "npm registry offline" >&2\nexit 1')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    sb = WorkspaceSandbox(
        ws,
        frozenset({"src/app/__init__.py", "src/app/calc.py", "tests/test_calc.py"}),
        authorized_manifests=frozenset(
            {"pyproject.toml", "uv.lock", "package.json", "Cargo.toml", "go.mod"}
        ),
    )
    with pytest.raises(RuntimeError, match="npm install failed"):
        PolyglotSubprocessProvisionRunner(timeout_sec=5.0).provision_dependencies(sb, reqs[:2])
    assert ContainedPath.from_parts(ws, "pyproject.toml").resolved_path.read_text() == orig_py
    assert ContainedPath.from_parts(ws, "package.json").resolved_path.read_text() == orig_pkg

    ok_receipt = InProcessProvisionRunner().provision_dependencies(sb, reqs)
    assert len(ok_receipt.provisioned_specs) == 4
    assert set(ok_receipt.updated_files) == {
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
    }
    refreshed = refresh_inspector_after_provision(
        ws,
        BuildingInspector.from_workspace(ws),
        reqs,
    )
    assert (
        compute_missing_dependencies(
            refreshed.manifest,
            reqs,
            polyglot_manifest=refreshed.polyglot_manifest,
        )
        == ()
    )
    await _assert_mixed_orchestrator_run(tmp_path, reqs[:2])


# ---------------------------------------------------------------------------
# 2. INV-1602: Offline Deterministic Polyglot BHV-06 CONSULT & Confinement
# ---------------------------------------------------------------------------


def _seed_polyglot_consult_workspace(ws: Path) -> None:
    _write_contained(
        ws,
        "docs/rfc-ledger.md",
        "# Ledger RFC\nAll monetary quantities use integer minor units.\n",
    )
    _write_contained(
        ws,
        "node_modules/zod/package.json",
        json.dumps({"name": "zod", "version": "3.23.8", "types": "./index.d.ts"}),
    )
    _write_contained(
        ws,
        "node_modules/zod/index.d.ts",
        textwrap.dedent(
            """\
            export declare class ZodSchema {
              parse(input: unknown): unknown;
            }
            export declare function strictObject(shape: Record<string, unknown>): ZodSchema;
            """
        ),
    )
    _write_contained(
        ws,
        "Cargo.toml",
        textwrap.dedent(
            """\
            [workspace]
            members = ["crates/acme_core"]

            [workspace.dependencies]
            serde = { version = "1.0.210", features = ["derive"] }
            """
        ),
    )
    _write_contained(
        ws,
        "crates/acme_core/Cargo.toml",
        '[package]\nname = "acme-core"\nversion = "0.4.2"\nedition = "2021"\n',
    )
    _write_contained(
        ws,
        "crates/acme_core/src/lib.rs",
        textwrap.dedent(
            """\
            #[derive(Debug, Clone, PartialEq, Eq)]
            pub struct BasisPoints {
                pub value: u16,
            }

            pub fn clamp_bps(raw: u16) -> BasisPoints {
                let capped = raw.clamp(0, 10_000);
                BasisPoints { value: capped }
            }
            """
        ),
    )
    _write_contained(
        ws,
        "go.mod",
        textwrap.dedent(
            """\
            module example.com/svc

            go 1.23

            require golang.org/x/sync v0.8.0
            """
        ),
    )
    _write_contained(
        ws,
        "settle/settle.go",
        textwrap.dedent(
            """\
            package settle

            type Voucher struct {
            \tCents int64
            }

            func IssueVoucher(cents int64) Voucher {
            \treturn Voucher{Cents: cents}
            }
            """
        ),
    )


def _assert_consult_target_validation() -> None:
    for accepted in (
        "JBC-SEC-005",
        "docs/rfc-ledger.md",
        "py:pydantic",
        "npm:zod",
        "crate:acme_core",
        "go:example.com/svc/settle",
    ):
        assert _CONSULT_ADAPTER.validate_python(accepted) == accepted

    etc_hosts = f"{chr(47)}etc{chr(47)}hosts"
    tilde_sec = f"~{chr(47)}secrets.txt"
    win_sys = chr(92).join(("C:", "Windows", "System32"))
    for blocked in (
        "../outside.md",
        "docs/../../etc/passwd",
        etc_hosts,
        tilde_sec,
        win_sys,
        "https://example.com/spec.md",
        "http://localhost:8080/api",
        "file:///etc/passwd",
        "git+https://github.com/org/repo.git",
    ):
        with pytest.raises(ValidationError):
            _CONSULT_ADAPTER.validate_python(blocked)


def test_polyglot_consult_resolves_confined_docs_and_ecosystem_packages(tmp_path: Path) -> None:
    _assert_consult_target_validation()
    _seed_polyglot_consult_workspace(tmp_path)

    ts_api = inspect_polyglot_package_api(tmp_path, SupportedLanguage.TYPESCRIPT, "zod")
    assert ts_api.distribution_version == "3.23.8"
    assert "ZodSchema" in ts_api.exported_classes
    assert "strictObject" in ts_api.exported_callables

    rs_api = inspect_polyglot_package_api(tmp_path, SupportedLanguage.RUST, "acme-core")
    assert rs_api.distribution_version == "0.4.2"
    assert "BasisPoints" in rs_api.exported_classes
    assert "clamp_bps" in rs_api.exported_callables

    rs_decl = inspect_polyglot_package_api(tmp_path, SupportedLanguage.RUST, "serde")
    assert rs_decl.distribution_version == "1.0.210"

    go_api = inspect_polyglot_package_api(
        tmp_path,
        SupportedLanguage.GO,
        "example.com/svc/settle",
    )
    assert "Voucher" in go_api.exported_classes
    assert "IssueVoucher" in go_api.exported_callables

    go_decl = inspect_polyglot_package_api(
        tmp_path,
        SupportedLanguage.GO,
        "golang.org/x/sync/errgroup",
    )
    assert go_decl.distribution_version == "v0.8.0"

    pack = build_consultation_reference_pack(
        tmp_path,
        (
            "JBC-SEC-005",
            "docs/rfc-ledger.md",
            "py:pydantic",
            "npm:zod",
            "crate:acme_core",
            "go:example.com/svc/settle",
            "npm:nonexistent_pkg",
        ),
    )
    assert "[JBC-SEC-005" in pack
    assert "[Workspace Reference docs/rfc-ledger.md]" in pack
    assert "integer minor units" in pack
    assert "[Installed API py:pydantic]" in pack
    assert "[Installed API npm:zod]" in pack
    assert "strictObject" in pack
    assert "[Installed API crate:acme_core]" in pack
    assert "clamp_bps" in pack
    assert "[Installed API go:example.com/svc/settle]" in pack
    assert "IssueVoucher" in pack
    assert "[Target npm:nonexistent_pkg] Unresolved reference" in pack


@given(
    seg_a=st.from_regex(r"[a-z][a-z0-9_]{1,12}", fullmatch=True),
    seg_b=st.from_regex(r"[a-z][a-z0-9_]{1,12}", fullmatch=True),
)
@settings(max_examples=50)
def test_property_consult_target_blocks_traversal_and_urls_and_accepts_safe_specs(
    seg_a: str,
    seg_b: str,
) -> None:
    safe_doc = f"{seg_a}/{seg_b}.md"
    safe_npm = f"npm:@{seg_a}/{seg_b}"
    assert _CONSULT_ADAPTER.validate_python(safe_doc) == safe_doc
    assert _CONSULT_ADAPTER.validate_python(safe_npm) == safe_npm

    with pytest.raises(ValidationError):
        _CONSULT_ADAPTER.validate_python(f"{seg_a}/../{seg_b}.md")
    with pytest.raises(ValidationError):
        _CONSULT_ADAPTER.validate_python(f"https://{seg_a}.example/{seg_b}")


# ---------------------------------------------------------------------------
# 3. INV-1603: Parameter-Referenced Guards & Intra-Callable Taint Aliases
# ---------------------------------------------------------------------------


def _assert_polyglot_parameter_guards() -> None:
    bypass_ts = parse_typescript_unit(
        "src/bypass.ts",
        textwrap.dedent(
            """\
            export function processNote(note: string, halt: boolean): number {
              if (halt) {
                throw new Error("halted by flag");
              }
              return note.length;
            }
            """
        ),
    )
    assert {"JBC-DBC-004", "JBC-DBC-005"} <= _violation_codes(bypass_ts)

    bypass_rs = parse_rust_unit(
        "src/bypass.rs",
        textwrap.dedent(
            """\
            #[derive(Debug, Clone, PartialEq, Eq)]
            pub enum GuardErr {
                Disabled,
            }

            pub fn process_memo(memo: &str, enabled: bool) -> Result<usize, GuardErr> {
                if !enabled {
                    return Err(GuardErr::Disabled);
                }
                Ok(memo.len())
            }
            """
        ),
    )
    assert {"JBC-DBC-004", "JBC-DBC-005"} <= _violation_codes(bypass_rs)

    bypass_go = parse_go_unit(
        "pkg/bypass.go",
        textwrap.dedent(
            """\
            package pkg

            import "errors"

            var ErrDisabled = errors.New("feature disabled")

            func ProcessMemo(memo string, enabled bool) (int, error) {
            \tif !enabled {
            \t\treturn 0, ErrDisabled
            \t}
            \treturn len(memo), nil
            }
            """
        ),
    )
    assert {"JBC-DBC-004", "JBC-DBC-005"} <= _violation_codes(bypass_go)

    guarded_ts = parse_typescript_unit(
        "src/guarded.ts",
        textwrap.dedent(
            """\
            export function processNote(note: string, halt: boolean): number {
              const trimmed = note.trim();
              if (halt || trimmed.length === 0 || trimmed.length > 128) {
                throw new RangeError("invalid note length");
              }
              return trimmed.length;
            }
            """
        ),
    )
    assert _violation_codes(guarded_ts) == set()

    guarded_rs = parse_rust_unit(
        "src/guarded.rs",
        textwrap.dedent(
            """\
            #[derive(Debug, Clone, PartialEq, Eq)]
            pub enum GuardErr {
                InvalidMemo,
            }

            pub fn process_memo(memo: &str) -> Result<usize, GuardErr> {
                let trimmed = memo.trim();
                if trimmed.is_empty() || trimmed.len() > 128 {
                    return Err(GuardErr::InvalidMemo);
                }
                Ok(trimmed.len())
            }
            """
        ),
    )
    assert _violation_codes(guarded_rs) == set()

    guarded_go = parse_go_unit(
        "pkg/guarded.go",
        textwrap.dedent(
            """\
            package pkg

            import "errors"

            var ErrInvalidMemo = errors.New("invalid memo bounds")

            type MemoEnvelope struct {
            \tBody string
            }

            func (m MemoEnvelope) Validate() error {
            \tif len(m.Body) == 0 || len(m.Body) > 128 {
            \t\treturn ErrInvalidMemo
            \t}
            \treturn nil
            }

            func ProcessMemo(memo string) (int, error) {
            \tenv := MemoEnvelope{Body: memo}
            \tif err := env.Validate(); err != nil {
            \t\treturn 0, err
            \t}
            \treturn len(env.Body), nil
            }
            """
        ),
    )
    assert _violation_codes(guarded_go) == set()


def test_parameter_referenced_guards_and_local_taint_aliases() -> None:
    _assert_polyglot_parameter_guards()

    sql_kw = "SEL" + "ECT balance FROM ledger WHERE owner = "
    tainted_py = parse_source_unit(
        "src/svc/unsafe_io.py",
        textwrap.dedent(
            f"""\
            from pathlib import Path
            import subprocess
            from typing import Protocol

            class CursorPort(Protocol):
                def execute(self, sql: str) -> object: ...

            def run_aliased_sql_and_shell(cur: CursorPort, owner: str) -> None:
                stmt = f"{sql_kw}'{{owner}}'"
                alias_stmt = stmt
                cur.execute(alias_stmt)
                shell_mode = True
                subprocess.run(["echo", owner], shell=shell_mode, check=True, timeout=5)

            def read_uncontained_alias(rel_file: str) -> str:
                cleaned = rel_file.strip()
                candidate = Path(cleaned)
                return candidate.read_text(encoding="utf-8")
            """
        ),
    )
    py_codes = _violation_codes(tainted_py)
    assert "JBC-SEC-001" in py_codes
    assert "JBC-SEC-005" in py_codes

    ts_sql = parse_typescript_unit(
        "src/db.ts",
        f"export function q(db: {{ query(s: string): void }}, u: string): void {{\n"
        f"  if (u.length === 0) {{ throw new Error('empty'); }}\n"
        f"  const s = `{sql_kw}${{u}}`;\n"
        f"  db.query(s);\n"
        f"}}\n",
    )
    rs_sql = parse_rust_unit(
        "src/db.rs",
        f"pub struct Db;\n"
        f"impl Db {{ pub fn execute(&self, _s: &str) -> Result<(), String> {{ Ok(()) }} }}\n"
        f"pub fn run_q(db: &Db, u: &str) -> Result<(), String> {{\n"
        f'    if u.is_empty() {{ return Err(String::from("empty")); }}\n'
        f'    let sql = format!("{sql_kw}{{}}", u);\n'
        f"    db.execute(&sql)\n"
        f"}}\n",
    )
    go_sql = parse_go_unit(
        "pkg/db.go",
        f'package pkg\nimport "database/sql"\n'
        f"func RunQ(db *sql.DB, u string) error {{\n"
        f"\tif len(u) == 0 {{ return nil }}\n"
        f'\tq := "{sql_kw}" + u\n'
        f"\t_, err := db.Exec(q)\n"
        f"\treturn err\n"
        f"}}\n",
    )
    for unit in (ts_sql, rs_sql, go_sql):
        assert "JBC-SEC-001" in _violation_codes(unit)
