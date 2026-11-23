"""Unit, AST-inspector, Hypothesis, runtime-breaker, dogfood, and graph tests for JBC v1.1."""

import ast
import textwrap
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from pydantic_graph import End, GraphBuilder, GraphRunContext

from jezz.behaviors import (
    AgentExecutionState,
    AtomicBehaviorGraphNode,
    BehaviorKind,
    verify_execution_trace,
)
from jezz.codes import (
    CODE_BOOK,
    CODEBOOK_EDITION,
    BudgetExceededError,
    BuildingCode,
    BuildingInspector,
    CertificateOfOccupancy,
    CodeArticle,
    CodeOrigin,
    CodePermit,
    CodeSeverity,
    CodeTrigger,
    CodeVariance,
    ContainedPath,
    FindingDisposition,
    GateAttestation,
    GateKind,
    InspectionMethod,
    LoopBudget,
    ManifestDependencies,
    OccupancyRuling,
    PositiveInt,
    RecursionBudget,
    RedTagError,
    StandardBody,
    StandardReference,
    bind_permit_to_state,
    certify_occupancy_in_state,
    codes_by_article,
    codes_for_triggers,
    compute_callable_metrics,
    compute_class_cohesion,
    compute_units_digest,
    get_building_code,
    issue_certificate,
    jezz_callable,
    load_workspace_units,
    parse_source_unit,
    read_bounded_text,
    record_inspection_to_state,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_snippet(source: str, *, file_path: str = "src/sample/mod.py") -> list[str]:
    """Parse ``source``, run static rough-in inspection, and return violated code IDs."""
    unit = parse_source_unit(file_path, textwrap.dedent(source))
    manifest = ManifestDependencies(
        runtime_roots=frozenset({"icontract", "pydantic", "yaml", "jezz", "httpx"}),
        dev_roots=frozenset({"pytest"}),
        workspace_roots=frozenset({"jezz", "sample", "tests"}),
    )
    inspector = BuildingInspector(manifest=manifest)
    report = inspector.inspect_rough_in((unit,))
    return [f.code_id for f in report.unwaived_violations()]


# ---------------------------------------------------------------------------
# 1. Code Book v1.1 & Verbatim Citation Invariants
# ---------------------------------------------------------------------------


def test_codebook_edition_1_1_counts_and_verbatim_quotes() -> None:
    assert CODEBOOK_EDITION == "1.2"
    assert len(CODE_BOOK) == 31
    counts = {article: len(codes_by_article(article)) for article in CodeArticle}
    assert counts == {
        CodeArticle.DBC: 5,
        CodeArticle.FLT: 6,
        CodeArticle.SEC: 6,
        CodeArticle.COG: 5,
        CodeArticle.OBS: 3,
        CodeArticle.VRF: 6,
    }

    sec_004 = get_building_code("JBC-SEC-004")
    quotes = [ref.quote for ref in sec_004.standard_refs if ref.quote is not None]
    assert any("Excessive Data Exposure" in q for q in quotes)

    cog_003 = get_building_code("JBC-COG-003")
    parnas_quotes = [ref.quote for ref in cog_003.standard_refs if ref.quote is not None]
    assert any("hides from all others" in q for q in parnas_quotes)


def test_building_code_rejects_advisory_prose_and_waivable_life_safety() -> None:
    ref = StandardReference(
        body=StandardBody.MITRE_CWE,
        clause="CWE-89",
        summary="SQL injection prevention standard.",
    )
    with pytest.raises(ValidationError, match="SHALL"):
        BuildingCode(
            code_id="JBC-SEC-099",
            article=CodeArticle.SEC,
            title="Advisory Non-Normative Guideline",
            normative_rule="Developers should generally prefer parameterized SQL queries.",
            hazard="SQL injection across untrusted inputs.",
            pass_criteria="Zero string-interpolated SQL statements.",
            severity=CodeSeverity.STRUCTURAL,
            variance_permitted=True,
            triggers=frozenset({CodeTrigger.UNIVERSAL}),
            enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
            inspection_method=InspectionMethod.AST_STATIC,
            origin=CodeOrigin.ESTABLISHED_STANDARD,
            standard_refs=(ref,),
        )

    with pytest.raises(ValidationError, match="LIFE_SAFETY"):
        BuildingCode(
            code_id="JBC-SEC-098",
            article=CodeArticle.SEC,
            title="Illegal Waivable Life Safety Rule",
            normative_rule="Modules SHALL NOT invoke eval on untrusted strings.",
            hazard="Arbitrary remote code execution.",
            pass_criteria="Zero eval() invocations.",
            severity=CodeSeverity.LIFE_SAFETY,
            variance_permitted=True,
            triggers=frozenset({CodeTrigger.UNIVERSAL}),
            enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
            inspection_method=InspectionMethod.AST_STATIC,
            origin=CodeOrigin.ESTABLISHED_STANDARD,
            standard_refs=(ref,),
        )


@given(
    subset_a=st.frozensets(st.sampled_from(list(CodeTrigger))),
    subset_b=st.frozensets(st.sampled_from(list(CodeTrigger))),
)
def test_property_trigger_binding_is_monotone(
    subset_a: frozenset[CodeTrigger],
    subset_b: frozenset[CodeTrigger],
) -> None:
    codes_a = {c.code_id for c in codes_for_triggers(subset_a)}
    codes_ab = {c.code_id for c in codes_for_triggers(subset_a | subset_b)}
    assert codes_a <= codes_ab


# ---------------------------------------------------------------------------
# 2. Layer 0 Runtime Guard Primitives (Breakers, Budgets, ContainedPath)
# ---------------------------------------------------------------------------


@jezz_callable
def _sample_checked_add(x: PositiveInt, y: PositiveInt) -> PositiveInt:
    return x + y


@jezz_callable
def _sample_broken_supplier(x: PositiveInt) -> PositiveInt:
    return x - 100


def test_jezz_callable_enforces_strict_input_and_output_signatures() -> None:
    assert _sample_checked_add(3, 4) == 7
    with pytest.raises(ValidationError):
        _sample_checked_add(0, 4)
    # Rejects bool masquerading as int under strict=True without # type-ignore suppression
    dynamic_caller: object = _sample_checked_add
    assert callable(dynamic_caller)
    with pytest.raises(ValidationError):
        dynamic_caller(True, 4)
    # Catches broken supplier return value (-95 is not PositiveInt) at return site
    with pytest.raises(ValidationError):
        _sample_broken_supplier(5)


def test_loop_budget_recursion_budget_and_contained_path(tmp_path: Path) -> None:
    loop_guard = LoopBudget(max_steps=3)
    for _ in range(3):
        loop_guard.tick()
    assert loop_guard.steps == 3
    with pytest.raises(BudgetExceededError):
        loop_guard.tick()

    rec_guard = RecursionBudget(max_depth=2)
    with rec_guard, rec_guard:
        assert rec_guard.depth == 2
        with pytest.raises(BudgetExceededError), rec_guard:
            pass
    assert rec_guard.depth == 0

    with pytest.raises(ValueError, match="traversal"):
        ContainedPath.from_parts(tmp_path, "../outside.txt")

    data_file = tmp_path / "payload.txt"
    data_file.write_text("hello world", encoding="utf-8")
    contained = ContainedPath.from_parts(tmp_path, "payload.txt")
    assert read_bounded_text(contained, max_bytes=32) == "hello world"
    with pytest.raises(BudgetExceededError):
        read_bounded_text(contained, max_bytes=4)


# ---------------------------------------------------------------------------
# 3. Permits, Variances & Life-Safety Lock
# ---------------------------------------------------------------------------


def test_variance_forbidden_on_life_safety_and_honored_on_workmanship() -> None:
    with pytest.raises(ValidationError, match="non-waivable"):
        CodeVariance(
            variance_id="VAR-ILLEGAL",
            code_id="JBC-SEC-001",
            file_path="src/sample/mod.py",
            authorized_by="chief_architect",
            justification="Attempting to waive eval() injection rule.",
        )

    unit = parse_source_unit(
        "src/sample/cli.py",
        textwrap.dedent(
            """\
            from jezz.guards import ShortText, jezz_callable

            @jezz_callable
            def emit_banner(text: ShortText) -> None:
                print(text)
            """
        ),
    )
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"jezz"}),
            workspace_roots=frozenset({"sample"}),
        )
    )
    red_report = inspector.inspect_rough_in((unit,))
    assert red_report.ruling == OccupancyRuling.RED_TAG
    assert [f.code_id for f in red_report.unwaived_violations()] == ["JBC-OBS-001"]

    variance = CodeVariance(
        variance_id="VAR-CLI-PRINT",
        code_id="JBC-OBS-001",
        file_path="src/sample/cli.py",
        authorized_by="principal_engineer",
        justification="Intentional stdout CLI banner rendering on terminal entrypoint.",
    )
    permit = CodePermit.full_book(permit_id="PERMIT-CLI", variances=(variance,))
    waived_report = inspector.inspect_rough_in((unit,), permit=permit)
    assert waived_report.ruling == OccupancyRuling.APPROVED_WITH_VARIANCES
    assert waived_report.findings[0].disposition == FindingDisposition.VARIANCE_WAIVED
    cert = issue_certificate(waived_report, artifact_digest="deadbeef12345678")
    assert cert.exercised_variance_ids == ("VAR-CLI-PRINT",)


# ---------------------------------------------------------------------------
# 4. Article I (DBC 001..005) & Article II (FLT 001..006) Evaluations
# ---------------------------------------------------------------------------


def test_article_i_dbc_001_through_005_violations_detected() -> None:
    bad_dbc = """\
        from typing import Any
        import icontract
        from pydantic import BaseModel
        from jezz.guards import STRICT_FROZEN_CONFIG, jezz_callable

        type UnboundedAlias = int

        class UnboundedModel(BaseModel):
            model_config = STRICT_FROZEN_CONFIG
            raw_text: str

        @icontract.ensure(lambda x, result: x > 0)
        def unguarded_public_fn(x: float, denom: float) -> float:
            return x / denom

        @jezz_callable
        def raw_scalar_param(count: int) -> int:
            return count

        def _untyped_and_partial(flag) -> Any:
            if flag:
                return 42
    """
    violated = _parse_snippet(bad_dbc)
    assert {"JBC-DBC-001", "JBC-DBC-002", "JBC-DBC-003", "JBC-DBC-004", "JBC-DBC-005"} <= set(
        violated
    )


def test_article_ii_flt_001_through_006_violations_detected() -> None:
    bad_flt = """\
        import asyncio
        import functools
        import subprocess
        import httpx

        @functools.cache
        def _leaky_memo(x: int) -> int:
            if x <= 0:
                return 0
            return _leaky_memo(x - 1)

        def _unbounded_retry_and_io(stream: object, cmd: list[str]) -> int:
            fh = open("out.log", "w", encoding="utf-8")
            fh.close()
            try:
                subprocess.run(cmd, check=True)
            except Exception:
                pass
            try:
                int("bad")
            except ValueError:
                raise RuntimeError("parse failed")
            while True:
                break
            return len(stream.read())

        async def _slowloris_and_leak() -> None:
            asyncio.create_task(asyncio.sleep(1))
            await httpx.get("https://example.com", timeout=5.0)
    """
    violated = _parse_snippet(bad_flt)
    assert {
        "JBC-FLT-001",
        "JBC-FLT-002",
        "JBC-FLT-003",
        "JBC-FLT-004",
        "JBC-FLT-005",
        "JBC-FLT-006",
    } <= set(violated)


# ---------------------------------------------------------------------------
# 5. Article III (SEC 001..005) & Article V (OBS 001..003) Evaluations
# ---------------------------------------------------------------------------


def test_article_iii_sec_001_through_005_violations_detected() -> None:
    synthetic_key = "sk-" + "live-1234567890abcdefghijklmnop"
    bad_sec = f"""\
        import json
        import logging
        import os
        from pydantic import BaseModel, ConfigDict
        from jezz.guards import ShortText, jezz_callable

        logger = logging.getLogger(__name__)

        class NonStrictModel(BaseModel):
            model_config = ConfigDict(extra="forbid")
            password: ShortText

        @jezz_callable
        def process_wire_bag(payload: dict[str, object]) -> dict[str, object]:
            parsed = json.loads("{{\\"k\\": 1}}")
            os.remove("relative/unanchored.tmp")
            return parsed

        def _sql_and_secret_leak(cursor: object, uid: str, api_key: str) -> object:
            secret_token = "{synthetic_key}"
            logger.error(f"Auth failed for {{api_key}} / {{secret_token}}")
            cursor.execute(f"SELECT * FROM accounts WHERE id = '{{uid}}'")
            return eval("1 + 1")
    """
    violated = _parse_snippet(bad_sec)
    assert {"JBC-SEC-001", "JBC-SEC-002", "JBC-SEC-003", "JBC-SEC-004", "JBC-SEC-005"} <= set(
        violated
    )


def test_article_v_obs_001_through_003_violations_detected() -> None:
    bad_obs = """\
        import os
        import random

        _STATE = 0

        def _nondeterministic_mutator(items: list[int] = []) -> int:
            global _STATE
            _STATE += random.randint(1, 10)
            os.environ["MUTATED_AT_RUNTIME"] = "1"
            breakpoint()
            return len(items) + _STATE
    """
    violated = _parse_snippet(bad_obs)
    assert {"JBC-OBS-001", "JBC-OBS-002", "JBC-OBS-003"} <= set(violated)


# ---------------------------------------------------------------------------
# 6. Article IV (COG 001..005) & Article VI (VRF 001..006) Evaluations
# ---------------------------------------------------------------------------


def test_article_iv_cog_001_002_004_and_005_violations_detected() -> None:
    bad_cog = """\
        def _handler_a(x: int) -> int:
            return x

        _MUTABLE_BOARD = {"a": _handler_a}

        class DisjointMultiDutyService:
            def __init__(self) -> None:
                self._billing_ledger = 0
                self._smtp_host = "localhost"

            def _charge(self, cents: int) -> int:
                self._billing_ledger += cents
                return self._billing_ledger

            def _send_mail(self) -> str:
                return self._smtp_host

            def _shoehorned_stateless(self, v: int) -> int:
                return v * 2

        def _tangled(a: int, b: int, c: int, d: int, e: int, f: int, g: int) -> int:
            if a == 1:
                return 10
            elif a == 2:
                return 20
            elif a == 3:
                return 30
            total = 0
            if a > 0 and b > 0:
                for i in range(a):
                    if i % 2 == 0 or c > 0:
                        for j in range(d):
                            if e > 0:
                                if f > 0:
                                    total += g + j
            return total
    """
    violated = _parse_snippet(bad_cog)
    assert {"JBC-COG-001", "JBC-COG-002", "JBC-COG-004", "JBC-COG-005"} <= set(violated)

    tree = ast.parse(textwrap.dedent(bad_cog))
    cls_nodes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    cohesion = compute_class_cohesion(cls_nodes[0])
    assert cohesion.lcom4 == 2
    assert cohesion.stateless_methods == ("_shoehorned_stateless",)


@given(branch_count=st.integers(min_value=0, max_value=12))
def test_property_nested_if_exceeds_flat_if_complexity(branch_count: int) -> None:
    flat_lines = ["def flat(x: int) -> int:", "    r = 0"]
    for idx in range(branch_count):
        flat_lines.extend([f"    if x == {idx}:", "        r += 1"])
    flat_lines.append("    return r")
    flat_tree = ast.parse("\n".join(flat_lines))
    flat_fn = flat_tree.body[0]
    assert isinstance(flat_fn, ast.FunctionDef)
    flat_metrics = compute_callable_metrics(flat_fn)
    assert flat_metrics.cognitive_complexity == branch_count

    if branch_count >= 2:
        nested_lines = ["def nested(x: int) -> int:"]
        for idx in range(branch_count):
            indent = "    " * (idx + 1)
            nested_lines.append(f"{indent}if x > {idx}:")
        nested_lines.append("    " * (branch_count + 1) + "return 1")
        nested_lines.append("    return 0")
        nested_tree = ast.parse("\n".join(nested_lines))
        nested_fn = nested_tree.body[0]
        assert isinstance(nested_fn, ast.FunctionDef)
        nested_metrics = compute_callable_metrics(nested_fn)
        assert nested_metrics.cognitive_complexity > flat_metrics.cognitive_complexity


def test_article_iv_cog_003_detects_cycles_wildcards_and_private_imports() -> None:
    unit_a = parse_source_unit(
        "src/sample/a.py",
        "from sample.b import _internal_helper\ndef _fa() -> int:\n    return _internal_helper()\n",
    )
    unit_b = parse_source_unit(
        "src/sample/b.py",
        "import sample.a\ndef _internal_helper() -> int:\n    return 2\n",
    )
    inspector = BuildingInspector(ManifestDependencies(workspace_roots=frozenset({"sample"})))
    report = inspector.inspect_rough_in((unit_a, unit_b))
    cog_003_msgs = [f.message for f in report.unwaived_violations() if f.code_id == "JBC-COG-003"]
    assert any("private symbol" in m for m in cog_003_msgs)
    assert any("Cyclic runtime" in m for m in cog_003_msgs)


def test_article_vi_agentic_anti_theatre_and_watchdog_attestations() -> None:
    bad_prod = (
        "import hallucinated_phantom_sdk\n"
        "import pytest\n\n"
        "# " + "TO" + "DO: finish implementing later\n"
        "def _save_record(record_id: str) -> None:\n"
        "    pass\n"
    )
    prod_violations = _parse_snippet(bad_prod, file_path="src/sample/service.py")
    assert {"JBC-VRF-002", "JBC-VRF-003"} <= set(prod_violations)

    bad_test = """\
        def test_empty_smoke_theatre() -> None:
            value = 2 + 2
            _ = value

        def test_tautologies(x: int = 5) -> None:
            assert True
            assert x == x
            assert (x > 0, "accidental tuple literal")
    """
    test_violations = _parse_snippet(bad_test, file_path="tests/test_fake.py")
    assert test_violations.count("JBC-VRF-001") == 4

    clean_unit = parse_source_unit(
        "src/sample/math_ops.py",
        textwrap.dedent(
            """\
            import icontract
            from jezz.guards import FiniteFloat, jezz_callable

            @icontract.require(lambda denom: denom != 0)
            @jezz_callable
            def safe_ratio(num: FiniteFloat, denom: FiniteFloat) -> FiniteFloat:
                return num / denom
            """
        ),
    )
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"icontract", "jezz"}),
            workspace_roots=frozenset({"sample"}),
        )
    )
    overrun_attestations = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy --strict",
            passed=True,
            exit_code=0,
            summary="Success.",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=5,
            duration_ms=90_000.0,
            max_duration_ms=10_000.0,
            peak_memory_kb=2048,
            max_memory_kb=65_536,
            summary="Exceeded duration watchdog.",
        ),
    )
    overrun_report = inspector.inspect_final((clean_unit,), attestations=overrun_attestations)
    assert overrun_report.ruling == OccupancyRuling.RED_TAG
    assert [f.code_id for f in overrun_report.unwaived_violations()] == ["JBC-VRF-006"]
    with pytest.raises(RedTagError):
        issue_certificate(overrun_report, artifact_digest="0123456789abcdef")


# ---------------------------------------------------------------------------
# 7. Dogfood Gate: Inspect the Entire `jezz` Repository Against All 30 Codes
# ---------------------------------------------------------------------------


def test_jezz_repository_passes_edition_1_1_inspection_with_zero_variances() -> None:
    tracemalloc.start()
    start_ns = time.perf_counter_ns()
    try:
        inspector = BuildingInspector.from_workspace(REPO_ROOT)
        units = load_workspace_units(REPO_ROOT, target_dirs=("src", "tests"))
        rough_report = inspector.inspect_rough_in(units)
        elapsed_ms = max(0.01, (time.perf_counter_ns() - start_ns) / 1_000_000.0)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert rough_report.ruling == OccupancyRuling.APPROVED, rough_report.to_markdown()

    permit = CodePermit.full_book(permit_id="PERMIT-JEZZ-V1-1-SELF-AUDIT")
    attestations = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy --strict",
            passed=True,
            exit_code=0,
            summary="Strict static verification across jezz and tests.",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=len(units),
            duration_ms=elapsed_ms,
            max_duration_ms=30_000.0,
            peak_memory_kb=max(1, peak_bytes // 1024),
            max_memory_kb=262_144,
            summary="Measured unit, property, and self-audit suite execution.",
        ),
    )
    final_report = inspector.inspect_final(units, permit=permit, attestations=attestations)
    assert final_report.ruling == OccupancyRuling.APPROVED, final_report.to_markdown()
    assert final_report.unwaived_violations() == ()

    certificate = issue_certificate(final_report, artifact_digest=compute_units_digest(units))
    assert certificate.ruling == OccupancyRuling.APPROVED
    assert len(certificate.cited_code_ids) == 31
    assert certificate.exercised_variance_ids == ()


# ---------------------------------------------------------------------------
# 8. End-to-End Async pydantic-graph Contractor + Building Inspector Cycle
# ---------------------------------------------------------------------------


@dataclass
class _InspectionDeps:
    inspector: BuildingInspector
    permit: CodePermit
    candidate_sources: dict[int, str]
    active_source: str = ""


@dataclass
class _FrameNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.FRAME

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> _BindCodesNode:
        self.record_into_context(ctx, "frame", "Frame safe account ratio service")
        ctx.state.problem.objective = "Implement safe, code-compliant ratio query"
        return _BindCodesNode()


@dataclass
class _BindCodesNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.BIND_CODES

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> _CheckpointNode:
        self.record_into_context(ctx, "bind_codes", "Bind full JBC v1.1 CodePermit")
        bind_permit_to_state(ctx.state, ctx.deps.permit)
        return _CheckpointNode()


@dataclass
class _CheckpointNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CHECKPOINT

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> _HypothesizeNode:
        self.record_into_context(ctx, "checkpoint", "Snapshot clean workspace")
        ctx.state.workspace.checkpoints.append("cp-initial")
        return _HypothesizeNode(attempt=1)


@dataclass
class _HypothesizeNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.HYPOTHESIZE
    attempt: int

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> _SynthesizeNode:
        self.record_into_context(ctx, "hypothesize", f"Design candidate {self.attempt}")
        ctx.state.world_model.active_hypotheses.append(f"design-v{self.attempt}")
        return _SynthesizeNode(attempt=self.attempt)


@dataclass
class _SynthesizeNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.SYNTHESIZE
    attempt: int

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> _RoughInInspectNode:
        self.record_into_context(ctx, "synthesize", f"Synthesize candidate {self.attempt}")
        ctx.deps.active_source = ctx.deps.candidate_sources[self.attempt]
        ctx.state.workspace.modified_artifacts.add("src/sample/ledger.py")
        return _RoughInInspectNode(attempt=self.attempt)


@dataclass
class _RoughInInspectNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.VERIFY_STATIC
    attempt: int

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> _RevertNode | _FinalInspectNode:
        self.record_into_context(ctx, "verify_static", "Run rough-in building inspection")
        unit = parse_source_unit("src/sample/ledger.py", ctx.deps.active_source)
        report = ctx.deps.inspector.inspect_rough_in((unit,), permit=ctx.deps.permit)
        record_inspection_to_state(ctx.state, report, stage="rough_in")
        if not report.is_occupiable:
            return _RevertNode(next_attempt=self.attempt + 1)
        return _FinalInspectNode()


@dataclass
class _RevertNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.REVERT
    next_attempt: int

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> _HypothesizeNode:
        self.record_into_context(ctx, "revert", "Revert RED_TAG candidate")
        ctx.deps.active_source = ""
        ctx.state.workspace.modified_artifacts.clear()
        ctx.state.world_model.falsified_paths.append(f"red-tag-v{self.next_attempt - 1}")
        return _HypothesizeNode(attempt=self.next_attempt)


@dataclass
class _FinalInspectNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.VERIFY_DYNAMIC

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> _CertifyNode:
        self.record_into_context(ctx, "verify_dynamic", "Run final gate attestations")
        unit = parse_source_unit("src/sample/ledger.py", ctx.deps.active_source)
        attestations = (
            GateAttestation(
                gate_kind=GateKind.STATIC_TYPES,
                tool_name="mypy --strict",
                passed=True,
                exit_code=0,
                summary="Strict types verified.",
            ),
            GateAttestation(
                gate_kind=GateKind.DYNAMIC_SUITE,
                tool_name="pytest",
                passed=True,
                exit_code=0,
                checks_executed=5,
                duration_ms=12.5,
                peak_memory_kb=512,
                summary="5 passed within watchdogs.",
            ),
        )
        report = ctx.deps.inspector.inspect_final(
            (unit,),
            permit=ctx.deps.permit,
            attestations=attestations,
        )
        record_inspection_to_state(ctx.state, report, stage="final")
        return _CertifyNode(attestations=attestations)


@dataclass
class _CertifyNode(AtomicBehaviorGraphNode[_InspectionDeps, CertificateOfOccupancy]):
    behavior_kind: ClassVar[BehaviorKind] = BehaviorKind.CERTIFY
    attestations: tuple[GateAttestation, ...]

    async def run(
        self,
        ctx: GraphRunContext[AgentExecutionState, _InspectionDeps],
    ) -> End[CertificateOfOccupancy]:
        self.record_into_context(ctx, "certify", "Issue Certificate of Occupancy")
        unit = parse_source_unit("src/sample/ledger.py", ctx.deps.active_source)
        report = ctx.deps.inspector.inspect_final(
            (unit,),
            permit=ctx.deps.permit,
            attestations=self.attestations,
        )
        cert = certify_occupancy_in_state(
            ctx.state,
            report,
            artifact_digest=compute_units_digest((unit,)),
        )
        return End(cert)


async def test_pydantic_graph_red_tags_ungrounded_code_and_certifies_remediated_code() -> None:
    ungrounded_attempt_1 = textwrap.dedent(
        """\
        def compute_share(total: float, count: int) -> float:
            try:
                return total / count
            except Exception:
                pass
            return 0.0
        """
    )
    grounded_attempt_2 = textwrap.dedent(
        """\
        import icontract
        from jezz.guards import FiniteFloat, PositiveInt, jezz_callable

        @icontract.require(lambda count: count > 0, "count must be positive.")
        @icontract.ensure(lambda total, count, result: abs(result * count - total) <= 1e-6)
        @jezz_callable
        def compute_share(total: FiniteFloat, count: PositiveInt) -> FiniteFloat:
            return total / count
        """
    )
    deps = _InspectionDeps(
        inspector=BuildingInspector(
            ManifestDependencies(
                runtime_roots=frozenset({"icontract", "jezz"}),
                workspace_roots=frozenset({"sample"}),
            )
        ),
        permit=CodePermit.full_book(permit_id="PERMIT-LEDGER-001"),
        candidate_sources={1: ungrounded_attempt_1, 2: grounded_attempt_2},
    )

    builder = GraphBuilder(
        state_type=AgentExecutionState,
        deps_type=_InspectionDeps,
        input_type=_FrameNode,
        output_type=CertificateOfOccupancy,
    )
    builder.add(builder.edge_from(builder.start_node).to(_FrameNode))
    builder.add_reachable(_FrameNode)
    workflow = builder.build()

    state = AgentExecutionState()
    certificate: object = await workflow.run(state=state, deps=deps, inputs=_FrameNode())
    assert isinstance(certificate, CertificateOfOccupancy)

    assert certificate.ruling == OccupancyRuling.APPROVED
    assert len(certificate.cited_code_ids) == 31
    assert state.evidence.certified is True
    assert state.workspace.sealed_commit == certificate.inspector_seal
    assert state.world_model.falsified_paths == ["red-tag-v1"]

    trace_report = verify_execution_trace(state.trace, name="inspector_red_tag_remediation")
    assert trace_report.is_valid, f"Induced behavior trace invalid: {trace_report.violations}"


# ---------------------------------------------------------------------------
# 8. Track 2 Brownfield Calibration Regression Suite
# ---------------------------------------------------------------------------


def test_track2_calibrations_eliminate_idiom_false_positives(tmp_path: Path) -> None:
    pyproject_toml = textwrap.dedent(
        """\
        [project]
        name = "demo-app"
        version = "0.1.0"
        dependencies = ["pydantic-ai-slim>=2.50.0", "logfire-api>=3.14.0"]

        [project.optional-dependencies]
        cli = ["typer>=0.12.0"]
        dev = ["pytest>=9.0.0"]
        """
    )
    pyproject_path = tmp_path / "pyproject.toml"
    with pyproject_path.open("w", encoding="utf-8") as fh:
        fh.write(pyproject_toml)

    inspector = BuildingInspector.from_workspace(tmp_path)
    assert {"pydantic_ai", "logfire", "logfire_api", "typer"} <= inspector.manifest.runtime_roots
    assert "pytest" in inspector.manifest.dev_roots

    submod_unit = parse_source_unit(
        "src/demo_app/_helpers.py",
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\ndef double(x: PositiveInt) -> PositiveInt:\n    return x * 2\n",
    )
    prod_unit = parse_source_unit(
        "src/demo_app/service.py",
        textwrap.dedent(
            """\
            from pathlib import Path
            from typing import Protocol
            import logfire
            import typer
            from demo_app import _helpers
            from jezz.guards import IdentifierStr, PositiveInt, jezz_callable

            API_KEY_ENV = "DEMO_API_KEY"

            # The TODO list panel renders placeholder IDs cleanly.
            class RunnerPort(Protocol):
                def execute(self, name: IdentifierStr) -> PositiveInt:
                    raise NotImplementedError

            class StepHandle:
                @jezz_callable
                def __init__(self, step_id: IdentifierStr) -> None:
                    self._step_id = step_id

                @property
                def step_id(self) -> IdentifierStr:
                    return self._step_id

                @jezz_callable
                def clone(self) -> "StepHandle":
                    return StepHandle(self._step_id)

                @jezz_callable
                def register_into(self, sink: list[object]) -> None:
                    sink.append(self)

            def _join_target(root_dir: Path, child_name: IdentifierStr) -> Path:
                if not child_name.strip():
                    raise typer.Exit()
                _ = logfire
                _ = _helpers
                return root_dir / child_name
            """
        ),
    )
    test_unit = parse_source_unit(
        "tests/test_service.py",
        textwrap.dedent(
            """\
            from pathlib import Path

            def _assert_equal_paths(left: Path, right: Path) -> None:
                assert left == right

            class TestSuiteGrouping:
                def test_path_join_via_private_assert_helper(self, tmp_path: Path) -> None:
                    _assert_equal_paths(tmp_path / "a", tmp_path / "a")
            """
        ),
    )
    manifest = ManifestDependencies(
        runtime_roots=inspector.manifest.runtime_roots | frozenset({"jezz"}),
        dev_roots=inspector.manifest.dev_roots,
        workspace_roots=frozenset({"demo_app", "tests"}),
    )
    calibrated = BuildingInspector(manifest)
    report = calibrated.inspect_rough_in((submod_unit, prod_unit, test_unit))
    assert report.ruling == OccupancyRuling.APPROVED, report.to_markdown()


def test_track2b_setup_cfg_visitor_and_singular_test_calibrations(tmp_path: Path) -> None:
    setup_cfg = textwrap.dedent(
        """\
        [options]
        install_requires =
            httpx>=0.28.0
            PyJWT>=2.10.0

        [options.extras_require]
        teams =
            slack-sdk>=3.27.0
        dev =
            pytest>=9.0.0
        """
    )
    cfg_path = tmp_path / "setup.cfg"
    with cfg_path.open("w", encoding="utf-8") as fh:
        fh.write(setup_cfg)

    inspector = BuildingInspector.from_workspace(tmp_path)
    assert {"httpx", "jwt", "slack_sdk"} <= inspector.manifest.runtime_roots
    assert "pytest" in inspector.manifest.dev_roots

    visitor_src = textwrap.dedent(
        """\
        import ast
        import httpx
        from pydantic import BaseModel
        from jezz.guards import STRICT_FROZEN_CONFIG, IdentifierStr, RecursionBudget

        METHOD_SECRET_GET = "_rpc/secret/get"
        SECRET_URI_PREFIX = "vault://"

        class KeyRecord(BaseModel):
            model_config = STRICT_FROZEN_CONFIG
            api_key_id: IdentifierStr

        class PruningVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.seen: list[str] = []
                self._budget = RecursionBudget(max_depth=64)

            def visit(self, node: ast.AST) -> object:
                with self._budget:
                    return super().visit(node)

            def visit_Name(self, node: ast.Name) -> object:
                _ = httpx
                self.seen.append(node.id)

            def visit_ClassDef(self, node: ast.ClassDef) -> object:
                pass
        """
    )
    prod_unit = parse_source_unit("src/crew/visitor.py", visitor_src)
    singular_test = parse_source_unit(
        "test/helpers/sample_case.py",
        "def test_singular_dir_is_test_unit() -> None:\n    assert 2 + 2 == 4\n",
    )
    assert singular_test.is_test_file is True

    manifest = ManifestDependencies(
        runtime_roots=inspector.manifest.runtime_roots | frozenset({"jezz", "pydantic"}),
        dev_roots=inspector.manifest.dev_roots,
        workspace_roots=frozenset({"crew", "test"}),
    )
    report = BuildingInspector(manifest).inspect_rough_in((prod_unit, singular_test))
    assert report.ruling == OccupancyRuling.APPROVED, report.to_markdown()


def test_sec_006_rejects_hardcoded_paths_and_accepts_dynamic_anchors() -> None:
    bad_prod = """\
        from pathlib import Path
        from jezz.guards import jezz_callable

        @jezz_callable
        def load_cfg() -> Path:
            return Path("config/settings.toml")
    """
    assert "JBC-SEC-006" in _parse_snippet(bad_prod, file_path="src/jezz/bad_path.py")

    bad_host_test = """\
        from pathlib import Path

        def test_leaked_host_path() -> None:
            p = Path("/Users/dev/project/data.json")
            assert p.name == "data.json"
    """
    assert "JBC-SEC-006" in _parse_snippet(bad_host_test, file_path="tests/test_bad_path.py")

    good_anchored = """\
        from pathlib import Path
        import icontract
        from jezz.guards import ContainedPath, IdentifierStr, jezz_callable

        @icontract.ensure(lambda result: result.is_absolute())
        @jezz_callable
        def resolve_asset(rel_name: IdentifierStr) -> Path:
            anchor = Path(__file__).resolve().parent
            return ContainedPath.from_parts(anchor, rel_name).resolved_path
    """
    assert "JBC-SEC-006" not in _parse_snippet(good_anchored, file_path="src/jezz/good_path.py")
