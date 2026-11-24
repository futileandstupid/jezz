"""Track 13: Shared Tarjan/matcher kernel, PermitProfile thresholds, and packaging hygiene."""

import io
import textwrap
import tomllib
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from jezz.cli import main
from jezz.codes import (
    COGNITIVE_COMPLEXITY_LIMIT,
    MAX_STATEMENT_COUNT_LIMIT,
    STRICT_COGNITIVE_COMPLEXITY_LIMIT,
    STRICT_STATEMENT_COUNT_LIMIT,
    BuildingInspector,
    CodePermit,
    CodeThresholdOverrides,
    DifferentialScope,
    GateAttestation,
    GateKind,
    ManifestDependencies,
    OccupancyRuling,
    PermitProfile,
    PolyglotImport,
    SupportedLanguage,
    find_tarjan_cycles,
    inspect_differential_final,
    inspect_differential_rough_in,
    match_known_module,
    parse_go_unit,
    parse_source_unit,
    resolve_complexity_limits,
    resolve_polyglot_import_target,
)
from jezz.guards import ContainedPath, read_bounded_text

_HOST_ROOT_TOKENS = ("/" + "Users" + "/", "/" + "home" + "/", "C:" + "\\\\")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write_contained(root: Path, rel: str, content: str) -> Path:
    target = ContainedPath.from_parts(root, rel).resolved_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _build_borderline_python_source() -> str:
    stmts = "\n".join(f"    acc_{idx} = x + {idx}" for idx in range(31))
    sum_expr = " + ".join(f"acc_{idx}" for idx in range(6))
    header = textwrap.dedent(
        """\
        from jezz.guards import BoundedInt, jezz_callable

        @jezz_callable
        def borderline_branchy(x: BoundedInt) -> BoundedInt:
            total = 0
            if x > 0:
                if x > 2:
                    if x > 4:
                        total += 1
                    if x > 6:
                        total += 2
            if x < 0:
                if x < -2:
                    total -= 1
            return total

        @jezz_callable
        def borderline_span(x: BoundedInt) -> BoundedInt:
        """
    )
    return f"{header}{stmts}\n    return {sum_expr}\n"


def _build_borderline_go_source() -> str:
    assigns = "\n".join(f"\tv{idx} := x + {idx}" for idx in range(30))
    return (
        "package sample\n\n"
        "func BorderlineGo(x int64) int64 {\n"
        "\tif x < -1000 || x > 1000 {\n"
        "\t\treturn 0\n"
        "\t}\n"
        "\ttotal := int64(0)\n"
        "\tif x > 0 {\n"
        "\t\tif x > 2 {\n"
        "\t\t\tif x > 4 {\n"
        "\t\t\t\ttotal += 1\n"
        "\t\t\t}\n"
        "\t\t\tif x > 6 {\n"
        "\t\t\t\ttotal += 2\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\t}\n"
        "\tif x < 0 {\n"
        "\t\tif x < -2 {\n"
        "\t\t\ttotal -= 1\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn total\n"
        "}\n\n"
        "func BorderlineGoSpan(x int64) int64 {\n"
        "\tif x < -1000 || x > 1000 {\n"
        "\t\treturn 0\n"
        "\t}\n"
        f"{assigns}\n"
        "\treturn v0 + v1 + v29\n"
        "}\n"
    )


def test_shared_tarjan_scc_and_module_matcher_parity() -> None:
    graph = {
        "alpha": frozenset({"beta"}),
        "beta": frozenset({"alpha", "delta"}),
        "delta": frozenset({"epsilon"}),
        "epsilon": frozenset({"gamma"}),
        "gamma": frozenset({"delta"}),
        "leaf": frozenset({"alpha"}),
    }
    cycles = find_tarjan_cycles(graph)
    assert cycles == [("alpha", "beta"), ("delta", "epsilon", "gamma")]

    known = frozenset({"pkg", "pkg.core", "pkg.core.ledger", "internal/auth"})
    assert match_known_module("pkg.core.ledger.Account", known) == "pkg.core.ledger"
    assert match_known_module("unknown.root.item", known) == ""

    ts_imp = PolyglotImport(raw_target="./ledger", resolved_module="pkg.core.ledger.item", line=1)
    assert (
        resolve_polyglot_import_target(SupportedLanguage.TYPESCRIPT, ts_imp, known)
        == "pkg.core.ledger"
    )
    go_imp = PolyglotImport(
        raw_target="github.com/acme/svc/internal/auth",
        resolved_module="github.com.acme.svc.internal.auth",
        line=3,
    )
    assert resolve_polyglot_import_target(SupportedLanguage.GO, go_imp, known) == "internal/auth"

    u_a = parse_source_unit(
        "src/pkg/a.py",
        "import pkg.b\nfrom jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\ndef fa(x: PositiveInt) -> PositiveInt:\n    return x\n",
    )
    u_b = parse_source_unit(
        "src/pkg/b.py",
        "import pkg.a\nfrom jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\ndef fb(x: PositiveInt) -> PositiveInt:\n    return x\n",
    )
    u_test = parse_source_unit(
        "tests/test_ab.py",
        "from pkg.a import fa\n\ndef test_fa() -> None:\n    assert fa(1) == 1\n",
    )
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"jezz"}),
            dev_roots=frozenset({"pytest"}),
            workspace_roots=frozenset({"pkg", "tests"}),
        )
    )
    scope = DifferentialScope(changed_paths=frozenset({"src/pkg/a.py", "tests/test_ab.py"}))
    rough = inspect_differential_rough_in(inspector, (u_a, u_b, u_test), scope)
    atts = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy",
            passed=True,
            exit_code=0,
            checks_executed=2,
            summary="ok",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=1,
            summary="1 passed",
        ),
    )
    final = inspect_differential_final(inspector, (u_a, u_b, u_test), scope, attestations=atts)
    assert rough.report.ruling == OccupancyRuling.RED_TAG
    assert final.report.ruling == OccupancyRuling.RED_TAG
    assert {f.code_id for f in rough.report.unwaived_violations()} == {"JBC-COG-003"}
    assert {f.code_id for f in final.report.unwaived_violations()} == {"JBC-COG-003"}


@given(
    edges=st.sets(
        st.tuples(
            st.sampled_from(("m0", "m1", "m2", "m3", "m4", "m5")),
            st.sampled_from(("m0", "m1", "m2", "m3", "m4", "m5")),
        ),
        max_size=20,
    )
)
def test_property_tarjan_cycles_canonical_and_dags_acyclic(
    edges: set[tuple[str, str]],
) -> None:
    nodes = ("m0", "m1", "m2", "m3", "m4", "m5")
    dag_graph = {u: frozenset(v for src, v in edges if src == u and u < v) for u in nodes}
    assert find_tarjan_cycles(dag_graph) == []

    any_graph = {u: frozenset(v for src, v in edges if src == u and u != v) for u in nodes}
    sccs = find_tarjan_cycles(any_graph)
    seen: set[str] = set()
    for scc in sccs:
        assert len(scc) >= 2
        assert scc == tuple(sorted(scc))
        assert not (seen & set(scc))
        seen.update(scc)


def test_permit_profile_strict_internal_enforces_tightened_thresholds(tmp_path: Path) -> None:
    py_unit = parse_source_unit("src/pkg/border.py", _build_borderline_python_source())
    go_unit = parse_go_unit("border.go", _build_borderline_go_source())
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"jezz"}),
            workspace_roots=frozenset({"pkg"}),
        )
    )

    std_permit = CodePermit.full_book("PERMIT-STD", profile=PermitProfile.STANDARD)
    std_rep = inspector.inspect_rough_in((py_unit, go_unit), permit=std_permit)
    assert std_rep.ruling == OccupancyRuling.APPROVED

    strict_permit = CodePermit.full_book("PERMIT-STRICT", profile=PermitProfile.STRICT_INTERNAL)
    strict_rep = inspector.inspect_rough_in((py_unit, go_unit), permit=strict_permit)
    assert strict_rep.ruling == OccupancyRuling.RED_TAG
    by_file_and_code = {(f.location.file_path, f.code_id) for f in strict_rep.unwaived_violations()}
    assert ("src/pkg/border.py", "JBC-COG-001") in by_file_and_code
    assert ("src/pkg/border.py", "JBC-COG-002") in by_file_and_code
    assert ("border.go", "JBC-COG-001") in by_file_and_code
    assert ("border.go", "JBC-COG-002") in by_file_and_code

    override_permit = CodePermit.full_book(
        "PERMIT-OVERRIDE",
        profile=PermitProfile.STRICT_INTERNAL,
        thresholds=CodeThresholdOverrides(
            max_cognitive_complexity=15,
            max_statement_count=35,
        ),
    )
    override_rep = inspector.inspect_rough_in((py_unit, go_unit), permit=override_permit)
    assert override_rep.ruling == OccupancyRuling.APPROVED

    _write_contained(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "pkg"\nversion = "0.1.0"\ndependencies = ["jezz>=0.1.0"]\n',
    )
    _write_contained(tmp_path, "src/pkg/__init__.py", "")
    _write_contained(tmp_path, "src/pkg/border.py", _build_borderline_python_source())
    _write_contained(
        tmp_path,
        "tests/test_border.py",
        "from pkg.border import borderline_branchy\n\n"
        "def test_b() -> None:\n    assert borderline_branchy(1) == 0\n",
    )
    out_std = io.StringIO()
    assert main(["inspect", str(tmp_path), "--profile", "standard"], stdout=out_std) == 0
    out_strict = io.StringIO()
    assert main(["inspect", str(tmp_path), "--profile", "strict_internal"], stdout=out_strict) == 1
    assert "JBC-COG-001" in out_strict.getvalue()


@given(
    profile=st.sampled_from((PermitProfile.STANDARD, PermitProfile.STRICT_INTERNAL)),
    cc_override=st.one_of(st.none(), st.integers(min_value=1, max_value=100)),
    stmt_override=st.one_of(st.none(), st.integers(min_value=1, max_value=250)),
)
def test_property_resolve_complexity_limits_precedence(
    profile: PermitProfile,
    cc_override: int | None,
    stmt_override: int | None,
) -> None:
    overrides = CodeThresholdOverrides(
        max_cognitive_complexity=cc_override,
        max_statement_count=stmt_override,
    )
    resolved = resolve_complexity_limits(profile, overrides)
    expected_cc = (
        cc_override
        if cc_override is not None
        else (
            STRICT_COGNITIVE_COMPLEXITY_LIMIT
            if profile == PermitProfile.STRICT_INTERNAL
            else COGNITIVE_COMPLEXITY_LIMIT
        )
    )
    expected_stmts = (
        stmt_override
        if stmt_override is not None
        else (
            STRICT_STATEMENT_COUNT_LIMIT
            if profile == PermitProfile.STRICT_INTERNAL
            else MAX_STATEMENT_COUNT_LIMIT
        )
    )
    assert resolved.max_cognitive_complexity == expected_cc
    assert resolved.max_statement_count == expected_stmts


def test_composite_action_omits_dev_deps_and_release_metadata_valid() -> None:
    root = _repo_root()
    action_txt = read_bounded_text(ContainedPath.from_parts(root, "action.yml"))
    assert "uv run --no-dev" in action_txt

    license_txt = read_bounded_text(ContainedPath.from_parts(root, "LICENSE"))
    assert "Apache License" in license_txt
    assert "Version 2.0, January 2004" in license_txt

    pyproject = tomllib.loads(read_bounded_text(ContainedPath.from_parts(root, "pyproject.toml")))
    project = pyproject["project"]
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]

    release_wf = read_bounded_text(ContainedPath.from_parts(root, ".github/workflows/release.yml"))
    assert not any(tok in release_wf for tok in _HOST_ROOT_TOKENS)
    assert "--cov=jezz --cov-fail-under=92" in release_wf
    assert "uv build" in release_wf
    assert "id-token: write" in release_wf
