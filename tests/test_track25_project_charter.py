"""Track 25 verification suite: RealityProbeEvidence, 3-axis elicitation, and ProjectCharter."""

import io
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from mcp import Client
from pydantic import ValidationError

from jezz.cli import main
from jezz.codes.taxonomy import CodeTrigger
from jezz.guards import ContainedPath, read_bounded_text
from jezz.mcp_server import build_jezz_mcp_server, dispatch_reloaded_mcp_call
from jezz.methodology import (
    REQUIRED_ELICITATION_AXES,
    AxisResolution,
    CharterIntakeSession,
    CharterSealInput,
    DomainInvariant,
    ElicitationAxis,
    EngineeringMandate,
    HazardMutationPolicy,
    MandateDependency,
    ModuleWriteScope,
    MutationPosture,
    ProjectCharter,
    RealityProbeEvidence,
    compile_phased_mandate_queue,
    probe_workspace_reality,
    seal_project_charter,
    start_charter_intake,
    topologically_order_mandates,
)
from jezz.plugin import PluginInstallSpec, initialize_jezz_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROTON_VISION = (
    "Model all Proton Pass entries (email aliases, logins, and credit cards), cross-link "
    "them under strict cardinality invariants, and update non-conforming vault items via CLI."
)


def _sample_resolutions() -> tuple[AxisResolution, AxisResolution, AxisResolution]:
    return (
        AxisResolution(
            axis=ElicitationAxis.DOMAIN_ONTOLOGY,
            summary="Model EmailAlias, LoginItem, and CardItem keyed by immutable item_uuid.",
            decided_constraints=(
                "Every LoginItem binds to zero or one active EmailAlias.",
                "Orphaned alias links are flagged deterministically.",
            ),
        ),
        AxisResolution(
            axis=ElicitationAxis.HAZARD_MUTATION_POLICY,
            summary="Route vault access through pass:// URIs with dry-run plan/apply and journal.",
            decided_constraints=(
                "Mutations require dry-run diff approval and 30s subprocess timeout.",
            ),
        ),
        AxisResolution(
            axis=ElicitationAxis.SCOPE_AND_EXCLUSIONS,
            summary="Single Python package vault_gov covering models, policy linker, and mutator.",
            decided_constraints=("Exclude browser extension autofill and GUI attachment sync.",),
        ),
    )


def _build_single_mandate(mandate_id: str, inv_id: str, mod_stem: str) -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id=mandate_id,
        title=f"Permit {mod_stem}",
        objective=f"Implement {mod_stem} contract module.",
        invariants=(
            DomainInvariant(
                invariant_id=inv_id,
                statement=f"Module {mod_stem} enforces strict closed schema invariants.",
                verification_symbol=f"vault_gov.{mod_stem}.verify_{mod_stem}",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id=f"scope_{mod_stem}",
                owned_files=frozenset({f"src/vault_gov/{mod_stem}.py"}),
                contract_symbols=(f"vault_gov.{mod_stem}.verify_{mod_stem}",),
            ),
        ),
    )


def test_inv_025_01_reality_probe_digest_and_phantom_binary_detection(tmp_path: Path) -> None:
    """INV-025-01: Reality probe captures empirical substrate state and verifies SHA-256 digest."""
    pyproject = ContainedPath.from_parts(tmp_path, "pyproject.toml").resolved_path
    pyproject.write_text(
        '[project]\nname = "vault-gov"\nversion = "0.1.0"\ndependencies = ["pydantic>=2.10"]\n',
        encoding="utf-8",
    )
    probe = probe_workspace_reality(
        tmp_path,
        candidate_binaries=("python3", "nonexistent_phantom_pass_cli_xyz"),
        candidate_packages=("pydantic", "nonexistent_phantom_sdk_xyz"),
    )
    assert "pyproject.toml" in probe.detected_manifests
    assert "python3" in probe.available_binaries
    assert "nonexistent_phantom_pass_cli_xyz" in probe.missing_binaries
    assert "pydantic" in probe.verified_packages
    assert "nonexistent_phantom_sdk_xyz" in probe.missing_packages

    with pytest.raises(ValidationError, match="digest mismatch"):
        RealityProbeEvidence(
            detected_manifests=probe.detected_manifests,
            evidence_digest="sha256:" + ("0" * 64),
        )
    with pytest.raises(ValueError, match="bare executable names"):
        probe_workspace_reality(tmp_path, candidate_binaries=("/usr/bin/env",))

    intake = start_charter_intake("Proton Vault Governor", _PROTON_VISION, probe)
    assert CodeTrigger.EXTERNAL_BOUNDARY in intake.detected_triggers
    assert CodeTrigger.SENSITIVE_CONTEXT in intake.detected_triggers
    assert intake.unresolved_axes == REQUIRED_ELICITATION_AXES
    assert not intake.is_ready_to_seal
    hazard_q = next(q for q in intake.questions if q.axis == ElicitationAxis.HAZARD_MUTATION_POLICY)
    assert "nonexistent_phantom_pass_cli_xyz" in hazard_q.prompt


def test_inv_025_02_blocks_incomplete_axes_theatre_and_unsafe_hazard_posture(
    tmp_path: Path,
) -> None:
    """INV-025-02: Charter refuses to seal when any axis, secret scheme, or posture is unsafe."""
    probe = probe_workspace_reality(tmp_path)
    intake = start_charter_intake("Proton Vault Governor", _PROTON_VISION, probe)
    res_all = _sample_resolutions()
    good_policy = HazardMutationPolicy(
        mutation_posture=MutationPosture.DRY_RUN_PLAN_APPLY,
        allowed_uri_schemes=("pass",),
        requires_rollback_journal=True,
        rate_or_timeout_bound_sec=30,
    )

    with pytest.raises(ValueError, match="unresolved elicitation axis"):
        seal_project_charter(
            CharterSealInput(
                intake=intake,
                resolutions=res_all[:2],
                hazard_policy=good_policy,
                scope_exclusions=("No GUI",),
                package_slug="vault_gov",
            )
        )

    with pytest.raises(ValidationError, match="user-story theatre"):
        AxisResolution(
            axis=ElicitationAxis.DOMAIN_ONTOLOGY,
            summary="As a vault user, I want aliases linked automatically.",
            decided_constraints=("Link aliases.",),
        )

    with pytest.raises(ValueError, match="cannot be 'local_workspace_only'"):
        seal_project_charter(
            CharterSealInput(
                intake=intake,
                resolutions=res_all,
                hazard_policy=HazardMutationPolicy(
                    mutation_posture=MutationPosture.LOCAL_WORKSPACE_ONLY,
                    allowed_uri_schemes=("pass",),
                ),
                scope_exclusions=("No GUI",),
                package_slug="vault_gov",
            )
        )

    with pytest.raises(ValueError, match="allowed_uri_schemes"):
        seal_project_charter(
            CharterSealInput(
                intake=intake,
                resolutions=res_all,
                hazard_policy=HazardMutationPolicy(
                    mutation_posture=MutationPosture.DRY_RUN_PLAN_APPLY,
                    rate_or_timeout_bound_sec=30,
                ),
                scope_exclusions=("No GUI",),
                package_slug="vault_gov",
            )
        )


def test_hazard_policy_and_dependency_edge_validation_guards(tmp_path: Path) -> None:
    """Verify scheme validation, rollback journal requirement, and self-edge rejection."""
    with pytest.raises(ValidationError, match="Unsupported secret URI scheme"):
        HazardMutationPolicy(
            mutation_posture=MutationPosture.READ_ONLY,
            allowed_uri_schemes=("vault_unknown",),
        )
    with pytest.raises(ValidationError, match="Duplicate secret URI scheme"):
        HazardMutationPolicy(
            mutation_posture=MutationPosture.READ_ONLY,
            allowed_uri_schemes=("pass", "pass"),
        )
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        MandateDependency(mandate_id="MND-SELF-01", depends_on="MND-SELF-01")

    probe = probe_workspace_reality(tmp_path)
    intake = start_charter_intake("Proton Vault Governor", _PROTON_VISION, probe)
    with pytest.raises(ValueError, match="requires_rollback_journal=True"):
        seal_project_charter(
            CharterSealInput(
                intake=intake,
                resolutions=_sample_resolutions(),
                hazard_policy=HazardMutationPolicy(
                    mutation_posture=MutationPosture.TRANSACTIONAL_ROLLBACK,
                    allowed_uri_schemes=("pass",),
                    requires_rollback_journal=False,
                    rate_or_timeout_bound_sec=30,
                ),
                scope_exclusions=("No GUI",),
                package_slug="vault_gov",
            )
        )


def test_inv_025_03_phased_permit_queue_topological_lock_and_tamper_seal(tmp_path: Path) -> None:
    """INV-025-03: External mutating charters require >=2 phased permits and seal integrity."""
    probe = probe_workspace_reality(tmp_path)
    intake = start_charter_intake("Proton Vault Governor", _PROTON_VISION, probe)
    res_all = _sample_resolutions()
    policy = HazardMutationPolicy(
        mutation_posture=MutationPosture.TRANSACTIONAL_ROLLBACK,
        allowed_uri_schemes=("pass", "env"),
        requires_rollback_journal=True,
        rate_or_timeout_bound_sec=30,
    )

    m_only = _build_single_mandate("MND-VAULT-01", "INV-101", "monolith")
    with pytest.raises(ValueError, match="at least 2 phased EngineeringMandates"):
        seal_project_charter(
            CharterSealInput(
                intake=intake,
                resolutions=res_all,
                hazard_policy=policy,
                scope_exclusions=("Exclude browser autofill.",),
                mandates=(m_only,),
            )
        )

    charter = seal_project_charter(
        CharterSealInput(
            intake=intake.with_resolutions(res_all),
            hazard_policy=policy,
            scope_exclusions=("Exclude browser autofill.",),
            package_slug="vault_gov",
        )
    )
    assert len(charter.mandate_queue) == 3
    assert [m.mandate_id for m in charter.mandate_queue] == [
        "MND-PROTON-VAULT-GOVERNOR-01",
        "MND-PROTON-VAULT-GOVERNOR-02",
        "MND-PROTON-VAULT-GOVERNOR-03",
    ]
    assert "Sealed PCE ProjectCharter" in charter.to_markdown()

    raw_payload = charter.model_dump(mode="json")
    raw_payload["scope_exclusions"] = ["Tampered scope exclusion"]
    with pytest.raises(ValidationError, match="seal mismatch"):
        ProjectCharter.model_validate_json(json.dumps(raw_payload))


def test_topologically_order_mandates_sorts_dag_and_rejects_cycles() -> None:
    """Verify Kahn topological ordering across MandateDependency edges."""
    m1 = _build_single_mandate("MND-ORD-01", "INV-101", "models")
    m2 = _build_single_mandate("MND-ORD-02", "INV-201", "policy")
    m3 = _build_single_mandate("MND-ORD-03", "INV-301", "mutator")
    deps = (
        MandateDependency(mandate_id="MND-ORD-03", depends_on="MND-ORD-02"),
        MandateDependency(mandate_id="MND-ORD-02", depends_on="MND-ORD-01"),
    )
    ordered = topologically_order_mandates((m3, m1, m2), deps)
    assert [m.mandate_id for m in ordered] == ["MND-ORD-01", "MND-ORD-02", "MND-ORD-03"]

    cyclic = (
        MandateDependency(mandate_id="MND-ORD-02", depends_on="MND-ORD-01"),
        MandateDependency(mandate_id="MND-ORD-01", depends_on="MND-ORD-02"),
    )
    with pytest.raises(ValueError, match="Cyclic MandateDependency"):
        topologically_order_mandates((m1, m2), cyclic)


@given(
    present_flags=st.tuples(st.booleans(), st.booleans(), st.booleans()),
)
def test_property_three_axis_completeness_iff_all_three_axes_supplied(
    present_flags: tuple[bool, bool, bool],
) -> None:
    """Property: compile_phased_mandate_queue succeeds iff all 3 ElicitationAxis entries exist."""
    all_three = _sample_resolutions()
    subset = tuple(res for res, keep in zip(all_three, present_flags, strict=True) if keep)
    policy = HazardMutationPolicy(mutation_posture=MutationPosture.READ_ONLY)
    if all(present_flags):
        mandates, deps = compile_phased_mandate_queue(
            "vault_gov",
            subset,
            policy,
            scope_exclusions=("No GUI",),
        )
        assert len(mandates) == 2
        assert len(deps) == 1
    else:
        with pytest.raises(ValueError, match="unresolved elicitation axis"):
            compile_phased_mandate_queue("vault_gov", subset, policy)


async def test_mcp_pce_start_intake_and_seal_charter_roundtrip(tmp_path: Path) -> None:
    """INV-025-03: MCP tools pce_start_intake and pce_seal_charter enforce Phase 0/I."""
    server = build_jezz_mcp_server(workspace_root=tmp_path)
    async with Client(server, mode="legacy") as client:
        intake_res = await client.call_tool(
            "pce_start_intake",
            {
                "title": "Proton Vault Governor",
                "vision": _PROTON_VISION,
                "candidate_binaries": ["python3"],
            },
        )
        assert intake_res.is_error is False
        intake = CharterIntakeSession.model_validate_json(json.dumps(intake_res.structured_content))
        assert intake.charter_id == "CHR-PROTON-VAULT-GOVERNOR"

        seal_payload = CharterSealInput(
            intake=intake,
            resolutions=_sample_resolutions(),
            hazard_policy=HazardMutationPolicy(
                mutation_posture=MutationPosture.DRY_RUN_PLAN_APPLY,
                allowed_uri_schemes=("pass",),
                rate_or_timeout_bound_sec=30,
            ),
            scope_exclusions=("Exclude GUI sync.",),
            package_slug="vault_gov",
        )
        seal_file = ContainedPath.from_parts(tmp_path, "seal_req.json").resolved_path
        seal_file.write_text(seal_payload.model_dump_json(indent=2), encoding="utf-8")

        seal_res = await client.call_tool(
            "pce_seal_charter",
            {"seal_input_path": "seal_req.json"},
        )
        assert seal_res.is_error is False
        sealed = ProjectCharter.model_validate_json(json.dumps(seal_res.structured_content))
        assert len(sealed.mandate_queue) == 3

        bad_res = await client.call_tool("pce_seal_charter", {})
        assert bad_res.is_error is True

    worker_intake_json = dispatch_reloaded_mcp_call(
        tmp_path,
        "pce_start_intake",
        '{"intake_args": {"title": "Local Calc", "vision": "Pure integer arithmetic library."}}',
    )
    worker_intake = CharterIntakeSession.model_validate_json(worker_intake_json)
    assert worker_intake.charter_id == "CHR-LOCAL-CALC"


def test_cli_jezz_charter_intake_only_and_interactive_sealing(tmp_path: Path) -> None:
    """Verify `jezz charter` CLI supports --intake-only, TTY interview, and permit emission."""
    out_intake = io.StringIO()
    err_intake = io.StringIO()
    rc_intake = main(
        [
            "charter",
            str(tmp_path),
            "--title",
            "Proton Vault Governor",
            "--vision",
            _PROTON_VISION,
            "--intake-only",
            "--write-intake",
            "intake.json",
        ],
        stdout=out_intake,
        stderr=err_intake,
    )
    assert rc_intake == 0
    assert "PCE Project Intake" in out_intake.getvalue()
    assert ContainedPath.from_parts(tmp_path, "intake.json").resolved_path.is_file()

    tty_in = io.StringIO(
        "Model Alias, Login, and Card entities keyed by UUID.\n"
        "Use pass:// URIs with dry-run plan/apply and 30s timeout.\n"
        "Target src/vault_gov package and exclude browser autofill.\n"
    )
    out_seal = io.StringIO()
    err_seal = io.StringIO()
    rc_seal = main(
        [
            "charter",
            str(tmp_path),
            "--title",
            "Proton Vault Governor",
            "--vision",
            _PROTON_VISION,
            "--interactive",
            "--mutation-posture",
            "dry_run_plan_apply",
            "--allow-scheme",
            "pass",
            "--timeout-bound",
            "30",
            "--exclusion",
            "Exclude browser extension autofill.",
            "--package-slug",
            "vault_gov",
            "--write-charter",
            "charter.json",
            "--write-mandates-dir",
            "permits",
            "--format",
            "json",
        ],
        stdout=out_seal,
        stderr=err_seal,
        stdin=tty_in,
    )
    assert rc_seal == 0, err_seal.getvalue()
    sealed = ProjectCharter.model_validate_json(out_seal.getvalue())
    assert len(sealed.mandate_queue) == 3
    permit_1 = ContainedPath.from_parts(tmp_path, "permits/01_MND-PROTON-VAULT-GOVERNOR-01.json")
    assert permit_1.resolved_path.is_file()


def test_plugin_rules_and_skill_sync_for_track25(tmp_path: Path) -> None:
    """Verify plugin.py generates AGENTS.md and SKILL.md with charter tools (and local sync)."""
    initialize_jezz_workspace(PluginInstallSpec(repo_root=tmp_path, bootstrap_pyproject=False))
    for rel in (
        ".agents/plugins/jezz/skills/pce/SKILL.md",
        ".agents/plugins/jezz/rules/AGENTS.md",
    ):
        generated = read_bounded_text(ContainedPath.from_parts(tmp_path, rel))
        assert "pce_start_intake" in generated
        assert "pce_seal_charter" in generated
        local_copy = ContainedPath.from_parts(REPO_ROOT, rel)
        if local_copy.resolved_path.is_file():
            assert generated == read_bounded_text(local_copy)
