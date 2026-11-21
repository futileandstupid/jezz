"""Verification suite for Track 1 CLI harness (jezz inspect, jezz audit, jezz run)."""

import io
import textwrap
from pathlib import Path

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import jezz.cli as cli_mod
from jezz.agents.schemas import (
    ConstructionPatchBundle,
    ContractSpecificationBundle,
    EpistemicObservation,
    FilePatchArtifact,
    FramedMandateProposal,
    InstrumentationSuiteBundle,
)
from jezz.behaviors.graph import BehaviorEdge, BehaviorGraph, BehaviorNode
from jezz.behaviors.taxonomy import BehaviorKind
from jezz.cli import discover_workspace_units, main
from jezz.codes.inspector import BuildingInspector, compute_units_digest
from jezz.codes.permits import (
    CodePermit,
    CodeVariance,
    GateAttestation,
    GateKind,
    InspectionReport,
    OccupancyRuling,
    issue_certificate,
)
from jezz.methodology.dossier import DossierAuditReport, EngineeringDossier
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope

REPO_ROOT = Path(__file__).resolve().parents[1]

_CLEAN_MODULE_SRC = textwrap.dedent(
    """\
    import icontract
    from jezz.guards import NonNegativeInt, PositiveInt, jezz_callable

    @icontract.ensure(lambda amount, result: result == amount * 2)
    @jezz_callable
    def double_cents(amount: PositiveInt) -> NonNegativeInt:
        return amount * 2
    """
)

_CLEAN_TEST_SRC = textwrap.dedent(
    """\
    from sample_pkg.calc import double_cents

    def test_double_cents_doubles_positive_amount() -> None:
        assert double_cents(25) == 50
    """
)

_PYPROJECT_SRC = textwrap.dedent(
    """\
    [project]
    name = "sample-pkg"
    version = "0.1.0"
    dependencies = ["icontract>=2.7.3", "jezz>=0.1.0"]

    [dependency-groups]
    dev = ["pytest>=9.2.0"]
    """
)


def _write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        fh.write(content)


def _seed_clean_workspace(root: Path) -> None:
    _write_text(root / "pyproject.toml", _PYPROJECT_SRC)
    _write_text(root / "src" / "sample_pkg" / "__init__.py", "")
    _write_text(root / "src" / "sample_pkg" / "calc.py", _CLEAN_MODULE_SRC)
    _write_text(root / "tests" / "test_calc.py", _CLEAN_TEST_SRC)


def test_cli_inspect_dogfoods_jezz_repository_and_exits_zero() -> None:
    out_md = io.StringIO()
    err_md = io.StringIO()
    exit_md = main(["inspect", str(REPO_ROOT)], stdout=out_md, stderr=err_md)
    assert exit_md == 0
    assert err_md.getvalue() == ""
    assert "# Building Inspection Report" in out_md.getvalue()
    assert "**Ruling:** `APPROVED`" in out_md.getvalue()

    out_json = io.StringIO()
    exit_json = main(["inspect", str(REPO_ROOT), "--format", "json"], stdout=out_json)
    assert exit_json == 0
    parsed = InspectionReport.model_validate_json(out_json.getvalue())
    assert parsed.ruling == OccupancyRuling.APPROVED
    assert len(parsed.unwaived_violations()) == 0


def test_cli_inspect_red_tag_exits_one_and_variance_permit_exits_zero(tmp_path: Path) -> None:
    _seed_clean_workspace(tmp_path)
    noisy_src = _CLEAN_MODULE_SRC + '\n@jezz_callable\ndef banner() -> None:\n    print("ready")\n'
    _write_text(tmp_path / "src" / "sample_pkg" / "calc.py", noisy_src)

    out_fail = io.StringIO()
    assert main(["inspect", str(tmp_path)], stdout=out_fail) == 1
    assert "`JBC-OBS-001`" in out_fail.getvalue()

    permit = CodePermit.full_book(
        permit_id="PERMIT-CLI-VAR",
        variances=(
            CodeVariance(
                variance_id="VAR-OBS-001",
                code_id="JBC-OBS-001",
                file_path="src/sample_pkg/calc.py",
                authorized_by="Principal Engineer",
                justification="Temporary legacy CLI banner retained during migration.",
            ),
        ),
    )
    permit_file = tmp_path / "permit.json"
    _write_text(permit_file, permit.model_dump_json(indent=2))

    out_waived = io.StringIO()
    exit_waived = main(
        ["inspect", str(tmp_path), "--permit", str(permit_file)],
        stdout=out_waived,
    )
    assert exit_waived == 0
    assert "**Ruling:** `APPROVED_WITH_VARIANCES`" in out_waived.getvalue()


def test_cli_discover_units_supports_flat_layout_and_prunes_virtualenvs(tmp_path: Path) -> None:
    _write_text(
        tmp_path / "pyproject.toml",
        '[project]\nname = "acme-service"\nversion = "0.1.0"\ndependencies = ["jezz>=0.1.0"]\n',
    )
    _write_text(
        tmp_path / "acme_pkg" / "core.py",
        "from jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\ndef inc(x: PositiveInt) -> PositiveInt:\n    return x + 1\n",
    )
    _write_text(
        tmp_path / "acme_pkg" / "api.py",
        "from acme_pkg.core import inc\nfrom jezz.guards import PositiveInt, jezz_callable\n\n"
        "@jezz_callable\ndef step(x: PositiveInt) -> PositiveInt:\n    return inc(x)\n",
    )
    _write_text(
        tmp_path / "tests" / "test_api.py",
        "from acme_pkg.api import step\n\ndef test_step() -> None:\n    assert step(2) == 3\n",
    )
    # Seed broken/hostile files inside pruned directories (.venv and build)
    _write_text(tmp_path / ".venv" / "lib" / "bad.py", "def broken(:\n")
    _write_text(tmp_path / "build" / "generated.py", "eval('1+1')\n")

    units = discover_workspace_units(tmp_path)
    discovered_paths = {u.file_path for u in units}
    assert discovered_paths == {
        "acme_pkg/api.py",
        "acme_pkg/core.py",
        "tests/test_api.py",
    }

    out = io.StringIO()
    assert main(["inspect", str(tmp_path)], stdout=out) == 0
    assert "**Ruling:** `APPROVED`" in out.getvalue()


def _build_valid_dossier_for_workspace(root: Path) -> EngineeringDossier:
    units = discover_workspace_units(root)
    permit = CodePermit.full_book(permit_id="PERMIT-AUDIT-CLI")
    attestations = (
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy",
            passed=True,
            exit_code=0,
            checks_executed=4,
            summary="Strict types clean.",
        ),
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=1,
            summary="1 passed.",
        ),
    )
    inspector = BuildingInspector.from_workspace(root)
    report = inspector.inspect_final(units, permit=permit, attestations=attestations)
    cert = issue_certificate(report, artifact_digest=compute_units_digest(units))
    graph = BehaviorGraph(
        name="pce_cli_audit",
        entry_id="n1",
        nodes=(
            BehaviorNode(id="n1", kind=BehaviorKind.FRAME, label="Frame mandate"),
            BehaviorNode(id="n2", kind=BehaviorKind.BIND_CODES, label="Bind JBC permit"),
            BehaviorNode(id="n3", kind=BehaviorKind.INSPECT_CODE, label="Inspect workspace"),
            BehaviorNode(id="n4", kind=BehaviorKind.CHECKPOINT, label="Checkpoint workspace"),
            BehaviorNode(id="n5", kind=BehaviorKind.SPECIFY, label="Specify contracts"),
            BehaviorNode(id="n6", kind=BehaviorKind.INSTRUMENT, label="Instrument tests"),
            BehaviorNode(id="n7", kind=BehaviorKind.SYNTHESIZE, label="Synthesize calc"),
            BehaviorNode(id="n8", kind=BehaviorKind.VERIFY_STATIC, label="Verify rough-in"),
            BehaviorNode(id="n9", kind=BehaviorKind.VERIFY_DYNAMIC, label="Verify dynamic"),
            BehaviorNode(id="n10", kind=BehaviorKind.CERTIFY, label="Certify occupancy"),
        ),
        edges=(
            BehaviorEdge(source="n1", target="n2"),
            BehaviorEdge(source="n2", target="n3"),
            BehaviorEdge(source="n3", target="n4"),
            BehaviorEdge(source="n4", target="n5"),
            BehaviorEdge(source="n5", target="n6"),
            BehaviorEdge(source="n6", target="n7"),
            BehaviorEdge(source="n7", target="n8"),
            BehaviorEdge(source="n8", target="n9"),
            BehaviorEdge(source="n9", target="n10"),
        ),
    )
    mandate = EngineeringMandate(
        mandate_id="MND-CLI-001",
        title="Double cents",
        objective="Implement verified double_cents arithmetic.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="double_cents(x) == 2 * x",
                verification_symbol="test_double_cents_doubles_positive_amount",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="calc_scope",
                owned_files=frozenset({"src/sample_pkg/calc.py", "tests/test_calc.py"}),
                contract_symbols=("double_cents", "test_double_cents_doubles_positive_amount"),
            ),
        ),
    )
    return EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=graph,
        mutated_files=frozenset({"src/sample_pkg/calc.py", "tests/test_calc.py"}),
        inspection_report=report,
        certificate=cert,
    )


def test_cli_audit_verifies_sealed_dossier_and_flags_tampering(tmp_path: Path) -> None:
    _seed_clean_workspace(tmp_path)
    dossier = _build_valid_dossier_for_workspace(tmp_path)
    dossier_path = tmp_path / "dossier.json"
    _write_text(dossier_path, dossier.model_dump_json(indent=2))

    out_ok = io.StringIO()
    exit_ok = main(["audit", str(dossier_path), "--format", "json"], stdout=out_ok)
    assert exit_ok == 0
    parsed = DossierAuditReport.model_validate_json(out_ok.getvalue())
    assert parsed.is_occupancy_sealed

    # Tamper with source after seal issuance -> digest mismatch must return exit code 1
    _write_text(
        tmp_path / "src" / "sample_pkg" / "calc.py",
        _CLEAN_MODULE_SRC.replace("amount * 2", "amount * 3"),
    )
    out_tampered = io.StringIO()
    exit_tampered = main(["audit", str(dossier_path)], stdout=out_tampered)
    assert exit_tampered == 1
    assert "`OCCUPANCY_SEALED`" not in out_tampered.getvalue()
    assert "invalid_occupancy_seal" in out_tampered.getvalue()


def _mock_pce_router(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    _ = messages
    tool = info.output_tools[0]
    schema_title = str(tool.parameters_json_schema.get("title", ""))
    if schema_title == "FramedMandateProposal":
        proposal = FramedMandateProposal(
            mandate=EngineeringMandate(
                mandate_id="MND-AMBIGUOUS",
                title="Under-determined prompt",
                objective="Deliver webhooks.",
                invariants=(
                    DomainInvariant(
                        invariant_id="INV-002",
                        statement="Deliveries preserve payload.",
                        verification_symbol="test_webhook",
                    ),
                ),
                write_scopes=(
                    ModuleWriteScope(
                        scope_id="webhook_scope",
                        owned_files=frozenset({"src/sample_pkg/calc.py"}),
                        contract_symbols=("double_cents",),
                    ),
                ),
                open_ambiguities=("Specify retry backoff curve and max attempts.",),
            ),
            framing_notes="Detected missing retry policy parameters.",
        )
        json_args = proposal.model_dump_json()
    elif schema_title == "EpistemicObservation":
        obs = EpistemicObservation(
            inspected_files=("src/sample_pkg/__init__.py",),
            discovered_symbols=(),
            summary="Inspected sample_pkg workspace.",
        )
        json_args = obs.model_dump_json()
    else:
        json_args = _build_stage_json(schema_title)
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool.name, args=json_args, tool_call_id="call_cli")]
    )


def _build_stage_json(schema_title: str) -> str:
    if schema_title == "ContractSpecificationBundle":
        return ContractSpecificationBundle(
            contract_patches=(
                FilePatchArtifact(
                    file_path="src/sample_pkg/calc.py",
                    source_code=_CLEAN_MODULE_SRC,
                    rationale="Contracted double_cents.",
                ),
            ),
            declared_symbols=("double_cents",),
            summary="Specified double_cents contract.",
        ).model_dump_json()
    if schema_title == "InstrumentationSuiteBundle":
        return InstrumentationSuiteBundle(
            test_patches=(
                FilePatchArtifact(
                    file_path="tests/test_calc.py",
                    source_code=_CLEAN_TEST_SRC,
                    rationale="Falsifiable test suite.",
                ),
            ),
            covered_invariant_ids=("INV-001",),
            summary="Instrumented INV-001 test suite.",
        ).model_dump_json()
    return ConstructionPatchBundle(
        implementation_patches=(
            FilePatchArtifact(
                file_path="src/sample_pkg/calc.py",
                source_code=_CLEAN_MODULE_SRC,
                rationale="Verified implementation.",
            ),
        ),
        summary="Synthesized double_cents.",
    ).model_dump_json()


def test_cli_run_executes_prompt_and_mandate_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_text(tmp_path / "pyproject.toml", _PYPROJECT_SRC)
    _write_text(tmp_path / "src" / "sample_pkg" / "__init__.py", "")
    monkeypatch.setattr(cli_mod, "resolve_cli_model", lambda _name: FunctionModel(_mock_pce_router))

    # 1. Ambiguous prompt halts at BHV-02 ELICIT and returns exit code 1
    out_halt = io.StringIO()
    exit_halt = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "test",
            "--prompt",
            "Build a webhook service",
            "--gate-runner",
            "in-process",
        ],
        stdout=out_halt,
    )
    assert exit_halt == 1
    assert "HALTED_FOR_ELICITATION" in out_halt.getvalue()

    # 2. Unambiguous mandate certifies occupancy (exit 0), writes dossier, and passes `jezz audit`
    mandate = _build_valid_dossier_for_workspace_mandate()
    mandate_file = tmp_path / "mandate.json"
    _write_text(mandate_file, mandate.model_dump_json(indent=2))

    out_cert = io.StringIO()
    exit_cert = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "test",
            "--mandate",
            str(mandate_file),
            "--gate-runner",
            "in-process",
            "--write-dossier",
            "artifacts/dossier.json",
        ],
        stdout=out_cert,
    )
    assert exit_cert == 0
    assert "OCCUPANCY_CERTIFIED" in out_cert.getvalue()

    out_audit = io.StringIO()
    exit_audit = main(
        ["audit", str(tmp_path / "artifacts" / "dossier.json"), "--repo", str(tmp_path)],
        stdout=out_audit,
    )
    assert exit_audit == 0
    assert "`OCCUPANCY_SEALED`" in out_audit.getvalue()


def _build_valid_dossier_for_workspace_mandate() -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id="MND-RUN-CLI",
        title="Double cents synthesis",
        objective="Synthesize and certify double_cents.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="double_cents(x) == 2 * x",
                verification_symbol="test_double_cents_doubles_positive_amount",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="calc_scope",
                owned_files=frozenset({"src/sample_pkg/calc.py", "tests/test_calc.py"}),
                contract_symbols=("double_cents", "test_double_cents_doubles_positive_amount"),
            ),
        ),
    )


def test_cli_help_and_error_handling_return_expected_codes(tmp_path: Path) -> None:
    help_out = io.StringIO()
    assert main(["--help"], stdout=help_out) == 0
    assert "inspect" in help_out.getvalue()

    err_missing = io.StringIO()
    assert main(["inspect", str(tmp_path / "nonexistent")], stderr=err_missing) == 2
    assert "jezz error:" in err_missing.getvalue()

    err_empty = io.StringIO()
    assert main(["inspect", str(tmp_path)], stderr=err_empty) == 2
    assert "Zero Python source files" in err_empty.getvalue()

    err_bad_flag = io.StringIO()
    assert main(["--unknown-flag"], stderr=err_bad_flag) == 2


def test_cli_run_formats_empty_timeout_and_usage_limit_errors_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_text(tmp_path / "pyproject.toml", _PYPROJECT_SRC)

    def _raise_timeout(*_args: object, **_kwargs: object) -> object:
        empty_msg = ""
        raise TimeoutError(empty_msg)

    monkeypatch.setattr(cli_mod, "_execute_pce_run", _raise_timeout)
    err_timeout = io.StringIO()
    code_timeout = main(
        ["run", str(tmp_path), "--model", "test", "--prompt", "Build calc"],
        stderr=err_timeout,
    )
    assert code_timeout == 2
    assert "jezz error: TimeoutError" in err_timeout.getvalue()

    def _raise_limit(*_args: object, **_kwargs: object) -> object:
        raise UsageLimitExceeded("Exceeded request_limit of 24")

    monkeypatch.setattr(cli_mod, "_execute_pce_run", _raise_limit)
    err_limit = io.StringIO()
    code_limit = main(
        ["run", str(tmp_path), "--model", "test", "--prompt", "Build calc"],
        stderr=err_limit,
    )
    assert code_limit == 2
    assert "Exceeded request_limit" in err_limit.getvalue()
