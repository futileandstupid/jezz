"""Unit, anti-theatre, and end-to-end dossier verification tests for Pillar 3 (PCE)."""

import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from jezz.behaviors import (
    BehaviorEdge,
    BehaviorGraph,
    BehaviorKind,
    BehaviorNode,
    build_abductive_bugfix_graph,
    build_greenfield_contract_first_graph,
)
from jezz.codes import (
    BuildingInspector,
    CodePermit,
    CodeTrigger,
    GateAttestation,
    GateKind,
    ManifestDependencies,
    SourceUnit,
    compute_units_digest,
    issue_certificate,
    load_workspace_units,
    parse_source_unit,
)
from jezz.guards import ContainedPath, read_bounded_text
from jezz.methodology import (
    METHODOLOGY_ACRONYM,
    METHODOLOGY_NAME,
    DomainInvariant,
    DossierViolationCode,
    EngineeringDossier,
    EngineeringMandate,
    ModuleWriteScope,
    PcePhase,
    audit_pce_dossier,
    validate_engineering_prose,
)
from jezz.plugin import PluginInstallSpec, initialize_jezz_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_valid_sample_units() -> tuple[SourceUnit, SourceUnit]:
    prod = parse_source_unit(
        "src/ledger/transfer.py",
        textwrap.dedent(
            """\
            import icontract
            from pydantic import BaseModel
            from jezz.guards import STRICT_FROZEN_CONFIG, PositiveInt, jezz_callable

            class TransferReceipt(BaseModel):
                model_config = STRICT_FROZEN_CONFIG
                debited_cents: PositiveInt
                remaining_cents: PositiveInt

            @icontract.require(lambda balance_cents, amount_cents: balance_cents > amount_cents)
            @icontract.ensure(
                lambda balance_cents, amount_cents, result: (
                    result.remaining_cents == balance_cents - amount_cents
                )
            )
            @jezz_callable
            def execute_transfer(
                balance_cents: PositiveInt,
                amount_cents: PositiveInt,
            ) -> TransferReceipt:
                return TransferReceipt(
                    debited_cents=amount_cents,
                    remaining_cents=balance_cents - amount_cents,
                )
            """
        ),
    )
    test_mod = parse_source_unit(
        "tests/test_transfer.py",
        textwrap.dedent(
            """\
            from ledger.transfer import execute_transfer

            def test_transfer_conservation() -> None:
                receipt = execute_transfer(1000, 250)
                assert receipt.remaining_cents == 750
            """
        ),
    )
    return prod, test_mod


def _standard_attestations() -> tuple[GateAttestation, GateAttestation]:
    return (
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
            checks_executed=4,
            duration_ms=8.5,
            peak_memory_kb=512,
            summary="4 passed.",
        ),
    )


# ---------------------------------------------------------------------------
# 1. Identity & Anti-Agile / Anti-SDD Mandate Guards
# ---------------------------------------------------------------------------


def test_pce_identity_and_anti_theatre_mandate_guards() -> None:
    assert METHODOLOGY_NAME == "Permit-and-Contract Engineering"
    assert METHODOLOGY_ACRONYM == "PCE"
    assert "Spec-Driven" not in METHODOLOGY_NAME

    # Rejects AWS Kiro / Scrum persona user-story theatre
    with pytest.raises(ValidationError, match="user-story theatre"):
        DomainInvariant(
            invariant_id="INV-001",
            statement="As a banking user, I want transfers validated, so that funds stay safe.",
            verification_symbol="execute_transfer",
        )

    # Rejects spec-kit / OpenSpec / Kiro Markdown checkbox theatre
    with pytest.raises(ValidationError, match="checkbox theatre"):
        EngineeringMandate(
            mandate_id="MND-BAD-TASKS",
            title="Transfer Service",
            objective="- [x] Implement transfer\n- [ ] Write tests",
            invariants=(
                DomainInvariant(
                    invariant_id="INV-001",
                    statement="Post-transfer balance equals prior balance minus amount.",
                    verification_symbol="execute_transfer",
                ),
            ),
            write_scopes=(
                ModuleWriteScope(
                    scope_id="ledger_core",
                    owned_files=frozenset({"src/ledger/transfer.py"}),
                    contract_symbols=("TransferReceipt",),
                ),
            ),
        )

    # Rejects Agile ceremony / SDD marketing terminology and weasel modals
    with pytest.raises(ValidationError, match="ceremony"):
        EngineeringMandate(
            mandate_id="MND-BAD-AGILE",
            title="Estimated at 5 story points for sprint velocity",
            objective="Enforce conservation of funds.",
            invariants=(
                DomainInvariant(
                    invariant_id="INV-001",
                    statement="Balance conservation holds across transfers.",
                    verification_symbol="execute_transfer",
                ),
            ),
            write_scopes=(
                ModuleWriteScope(
                    scope_id="ledger_core",
                    owned_files=frozenset({"src/ledger/transfer.py"}),
                    contract_symbols=("TransferReceipt",),
                ),
            ),
        )

    with pytest.raises(ValidationError, match="weasel"):
        DomainInvariant(
            invariant_id="INV-002",
            statement="Transfer latency should ideally stay under 50ms.",
            verification_symbol="execute_transfer",
        )


def test_mandate_rejects_overlapping_write_scopes() -> None:
    with pytest.raises(ValidationError, match="Write-scope collision"):
        EngineeringMandate(
            mandate_id="MND-COLLISION",
            title="Conflicting Worker Scopes",
            objective="Partition ledger modules safely.",
            invariants=(
                DomainInvariant(
                    invariant_id="INV-001",
                    statement="Receipts preserve positive cent conservation.",
                    verification_symbol="TransferReceipt",
                ),
            ),
            write_scopes=(
                ModuleWriteScope(
                    scope_id="worker_1",
                    owned_files=frozenset({"src/ledger/transfer.py"}),
                    contract_symbols=("TransferReceipt",),
                ),
                ModuleWriteScope(
                    scope_id="worker_2",
                    owned_files=frozenset({"src/ledger/transfer.py", "src/ledger/audit.py"}),
                    contract_symbols=("AuditRecord",),
                ),
            ),
        )


# ---------------------------------------------------------------------------
# 2. Full 5-Phase PCE Dossier Audit & Violation Detection
# ---------------------------------------------------------------------------


def test_pce_dossier_certifies_valid_package_and_detects_tampering() -> None:
    units = _build_valid_sample_units()
    prod_unit, test_unit = units

    mandate = EngineeringMandate(
        mandate_id="MND-LEDGER-001",
        title="Atomic Positive-Cent Transfer Execution",
        objective="Enforce strict positive cent conservation on account debits.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Remaining balance equals prior balance minus debited amount.",
                verification_symbol="execute_transfer",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="Suite falsifies conservation drift across transfer calls.",
                verification_symbol="test_transfer_conservation",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="ledger_scope",
                owned_files=frozenset({"src/ledger/transfer.py", "tests/test_transfer.py"}),
                contract_symbols=("TransferReceipt", "execute_transfer"),
            ),
        ),
    )

    permit = CodePermit.full_book(permit_id="PERMIT-LEDGER-PCE")
    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"icontract", "pydantic", "jezz"}),
            dev_roots=frozenset({"pytest"}),
            workspace_roots=frozenset({"ledger", "tests"}),
        )
    )
    report = inspector.inspect_final(units, permit=permit, attestations=_standard_attestations())
    cert = issue_certificate(report, artifact_digest=compute_units_digest(units))

    dossier = EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        mutated_files=frozenset({"src/ledger/transfer.py", "tests/test_transfer.py"}),
        inspection_report=report,
        certificate=cert,
    )
    audit = audit_pce_dossier(dossier, units)
    assert audit.is_occupancy_sealed, audit.to_markdown()
    assert audit.highest_verified_phase == PcePhase.OCCUPANCY
    assert set(audit.verified_symbols) == {
        "TransferReceipt",
        "execute_transfer",
        "test_transfer_conservation",
    }

    # Tampering with source after certificate issuance invalidates occupancy seal
    tampered_prod = parse_source_unit(
        "src/ledger/transfer.py",
        prod_unit.source + "\n# post-seal edit\n",
    )
    tampered_audit = audit_pce_dossier(dossier, (tampered_prod, test_unit))
    assert not tampered_audit.is_occupancy_sealed
    assert tampered_audit.highest_verified_phase == PcePhase.CONSTRUCTION
    assert [v.code for v in tampered_audit.violations] == [
        DossierViolationCode.INVALID_OCCUPANCY_SEAL
    ]


def test_pce_dossier_catches_floating_prose_specs_scope_escapes_and_skipped_contracts() -> None:
    units = _build_valid_sample_units()

    ambiguous_mandate = EngineeringMandate(
        mandate_id="MND-FLOATING-SPEC",
        title="Unlinked Spec & Out-of-Scope Edit",
        objective="Demonstrate detection of unimplemented invariant symbols.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Nonexistent phantom verifier symbol must fail CONTRACT audit.",
                verification_symbol="nonexistent_phantom_contract_fn",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="ledger_only",
                owned_files=frozenset({"src/ledger/transfer.py"}),
                contract_symbols=("TransferReceipt",),
            ),
        ),
        open_ambiguities=("Should overdraft trigger fee or hard rejection?",),
    )

    # Valid Pillar 1 graph that skips SPECIFY and INSTRUMENT before SYNTHESIZE
    no_contract_graph = BehaviorGraph(
        name="synthesize_without_specify",
        entry_id="f",
        nodes=(
            BehaviorNode(id="f", kind=BehaviorKind.FRAME, label="Frame"),
            BehaviorNode(id="b", kind=BehaviorKind.BIND_CODES, label="Bind"),
            BehaviorNode(id="cp", kind=BehaviorKind.CHECKPOINT, label="Checkpoint"),
            BehaviorNode(id="syn", kind=BehaviorKind.SYNTHESIZE, label="Synthesize directly"),
            BehaviorNode(id="vs", kind=BehaviorKind.VERIFY_STATIC, label="Static"),
            BehaviorNode(id="vd", kind=BehaviorKind.VERIFY_DYNAMIC, label="Dynamic"),
            BehaviorNode(id="cert", kind=BehaviorKind.CERTIFY, label="Certify"),
        ),
        edges=(
            BehaviorEdge(source="f", target="b"),
            BehaviorEdge(source="b", target="cp"),
            BehaviorEdge(source="cp", target="syn"),
            BehaviorEdge(source="syn", target="vs"),
            BehaviorEdge(source="vs", target="vd"),
            BehaviorEdge(source="vd", target="cert"),
        ),
    )

    inspector = BuildingInspector(
        ManifestDependencies(
            runtime_roots=frozenset({"icontract", "pydantic", "jezz"}),
            dev_roots=frozenset({"pytest"}),
            workspace_roots=frozenset({"ledger", "tests"}),
        )
    )
    partial_permit = CodePermit.for_triggers(
        "PERMIT-MIN",
        frozenset({CodeTrigger.UNIVERSAL}),
    )
    rough_only_report = inspector.inspect_rough_in(units, permit=partial_permit)
    full_permit = CodePermit.full_book("PERMIT-FULL")

    bad_dossier = EngineeringDossier(
        mandate=ambiguous_mandate,
        permit=full_permit,
        behavior_graph=no_contract_graph,
        mutated_files=frozenset({"src/ledger/transfer.py", "src/unauthorized/secret.py"}),
        inspection_report=rough_only_report,
        certificate=None,
    )

    audit = audit_pce_dossier(bad_dossier, units)
    assert not audit.is_occupancy_sealed
    assert audit.highest_verified_phase == PcePhase.MANDATE
    codes = {v.code for v in audit.violations}
    assert DossierViolationCode.UNRESOLVED_AMBIGUITIES in codes
    assert DossierViolationCode.MISSING_CONTRACT_STAGE in codes
    assert DossierViolationCode.UNBOUND_INVARIANT_SYMBOL in codes
    assert DossierViolationCode.UNDECLARED_WRITE_MUTATION in codes
    assert DossierViolationCode.UNINSPECTED_PERMIT_CODES in codes
    assert DossierViolationCode.INVALID_OCCUPANCY_SEAL in codes
    assert "PCE Dossier Audit" in audit.to_markdown()


def test_jezz_pillar_3_itself_seals_a_valid_pce_engineering_dossier() -> None:
    inspector = BuildingInspector.from_workspace(REPO_ROOT)
    units = load_workspace_units(REPO_ROOT, target_dirs=("src", "tests"))
    permit = CodePermit.full_book(permit_id="PERMIT-JEZZ-PCE-SELF-SEAL")
    report = inspector.inspect_final(units, permit=permit, attestations=_standard_attestations())
    cert = issue_certificate(report, artifact_digest=compute_units_digest(units))

    pce_files = frozenset(
        {
            "src/jezz/methodology/__init__.py",
            "src/jezz/methodology/mandate.py",
            "src/jezz/methodology/dossier.py",
            "tests/test_methodology.py",
        }
    )
    mandate = EngineeringMandate(
        mandate_id="MND-JEZZ-PILLAR-3",
        title="Permit-and-Contract Engineering Runtime Enforcement",
        objective="Enforce symbol-grounded invariants, disjoint scopes, and occupancy seals.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="Mandates reject Agile user-story and checkbox theatre.",
                verification_symbol="validate_engineering_prose",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="Dossier audit verifies AST symbol grounding and occupancy seal.",
                verification_symbol="audit_pce_dossier",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="pce_methodology_pkg",
                owned_files=pce_files,
                contract_symbols=("EngineeringMandate", "EngineeringDossier", "audit_pce_dossier"),
            ),
        ),
    )

    dossier = EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        mutated_files=pce_files,
        inspection_report=report,
        certificate=cert,
    )
    audit = audit_pce_dossier(dossier, units)
    assert audit.is_occupancy_sealed, audit.to_markdown()
    assert audit.highest_verified_phase == PcePhase.OCCUPANCY


def test_sdd_laundering_exhibit_is_deterministically_red_tagged_by_jezz() -> None:
    """Verify the exact Section 4B exhibit in pillar-3a-sdd-teardown.md is Red-Tagged."""
    with pytest.raises(ValueError, match="user-story theatre"):
        validate_engineering_prose(
            "As an account holder, I want to transfer money, so that I can pay peers.",
            field_label="sdd_spec",
        )
    with pytest.raises(ValueError, match="checkbox theatre"):
        validate_engineering_prose(
            "- [x] 1.1 Implement transfer_funds and unit test",
            field_label="sdd_tasks",
        )

    sdd_prod = parse_source_unit(
        "src/bank/transfer.py",
        textwrap.dedent(
            """\
            def transfer_funds(db, sender_id, recipient_id, amount: float):
                try:
                    db.execute(
                        f"UPDATE accts SET bal = bal - {amount} WHERE id = '{sender_id}'"
                    )
                    db.execute(
                        f"UPDATE accts SET bal = bal + {amount} WHERE id = '{recipient_id}'"
                    )
                    return True
                except Exception:
                    pass
                return False
            """
        ),
    )
    sdd_test = parse_source_unit(
        "tests/test_transfer.py",
        textwrap.dedent(
            """\
            def test_transfer_funds():
                assert True
            """
        ),
    )
    inspector = BuildingInspector(
        ManifestDependencies(workspace_roots=frozenset({"bank", "tests"}))
    )
    report = inspector.inspect_rough_in((sdd_prod, sdd_test))
    assert not report.is_occupiable
    violated_codes = {f.code_id for f in report.unwaived_violations()}
    assert {
        "JBC-DBC-002",
        "JBC-DBC-004",
        "JBC-DBC-005",
        "JBC-SEC-001",
        "JBC-FLT-001",
        "JBC-VRF-001",
    } <= violated_codes


@given(
    prefix_num=st.integers(min_value=1, max_value=999),
    theatre_token=st.sampled_from(
        (
            "As a customer, I want instant refunds",
            "- [ ] Implement endpoint",
            "Estimated at 8 story points",
        )
    ),
)
def test_property_validate_engineering_prose_rejects_injected_theatre(
    prefix_num: int,
    theatre_token: str,
) -> None:
    clean_clause = f"Invariant {prefix_num} preserves conservation of cents."
    assert validate_engineering_prose(clean_clause, field_label="stmt") is None
    with pytest.raises(ValueError):
        validate_engineering_prose(f"{theatre_token} ({prefix_num})", field_label="stmt")


def test_track23_tdd_bifurcation_and_plugin_skill_sync(tmp_path: Path) -> None:
    """Verify INV-023-01/02 empirical grounding, archetype bifurcation, and SKILL.md sync."""
    bugfix_edges = {(e.source, e.target) for e in build_abductive_bugfix_graph().edges}
    assert ("write_repro_test", "confirm_repro_red") in bugfix_edges
    assert ("confirm_repro_red", "apply_candidate_fix") in bugfix_edges

    green_edges = {(e.source, e.target) for e in build_greenfield_contract_first_graph().edges}
    assert ("specify_contracts", "author_property_tests") in green_edges
    assert ("author_property_tests", "synthesize_module") in green_edges

    initialize_jezz_workspace(PluginInstallSpec(repo_root=tmp_path, bootstrap_pyproject=False))
    gen_skill = read_bounded_text(
        ContainedPath.from_parts(tmp_path, ".agents/plugins/jezz/skills/pce/SKILL.md")
    )
    local_skill = ContainedPath.from_parts(REPO_ROOT, ".agents/plugins/jezz/skills/pce/SKILL.md")
    if local_skill.resolved_path.is_file():
        assert gen_skill == read_bounded_text(local_skill)
    assert "build_abductive_bugfix_graph" in gen_skill
    assert "build_greenfield_contract_first_graph" in gen_skill

    for doc_rel in (
        "docs/pillar-3a-sdd-teardown.md",
        "docs/pillar-3b-pce-methodology.md",
        "docs/whitepaper.md",
    ):
        doc_text = read_bounded_text(ContainedPath.from_parts(REPO_ROOT, doc_rel))
        assert "Fucci" in doc_text and "Rafique" in doc_text
        assert "build_abductive_bugfix_graph" in doc_text
        assert "build_greenfield_contract_first_graph" in doc_text
