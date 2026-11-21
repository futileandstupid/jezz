"""Automated Software Building Inspector (JBC v1.1) and Pillar 1 runtime governance bridge."""

import ast
import configparser
import hashlib
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final, Literal

import icontract
from pydantic import BaseModel, Field

from jezz.behaviors.runtime import AgentExecutionState
from jezz.codes.ast_utils import RawDiscrepancy, SourceUnit, parse_source_unit
from jezz.codes.codebook import CODE_BOOK, codes_for_triggers
from jezz.codes.permits import (
    CertificateOfOccupancy,
    CodePermit,
    CodeSectionVerdict,
    FindingDisposition,
    GateAttestation,
    InspectionFinding,
    InspectionReport,
    OccupancyRuling,
    RedTagError,
    issue_certificate,
)
from jezz.codes.rules_cog import (
    check_cog_001,
    check_cog_002,
    check_cog_003,
    check_cog_004,
    check_cog_005,
)
from jezz.codes.rules_dbc import (
    check_dbc_001,
    check_dbc_002,
    check_dbc_003,
    check_dbc_004,
    check_dbc_005,
)
from jezz.codes.rules_flt import (
    check_flt_001,
    check_flt_002,
    check_flt_003,
    check_flt_004,
    check_flt_005,
    check_flt_006,
)
from jezz.codes.rules_sec_obs import (
    check_obs_001,
    check_obs_002,
    check_obs_003,
    check_sec_001,
    check_sec_002,
    check_sec_003,
    check_sec_004,
    check_sec_005,
)
from jezz.codes.rules_vrf import (
    check_vrf_001,
    check_vrf_002,
    check_vrf_003,
    check_vrf_004,
    check_vrf_005,
    check_vrf_006,
)
from jezz.codes.taxonomy import CodeTrigger, InspectionMethod
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    jezz_callable,
    read_bounded_text,
)

_PEP508_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PYPI_IMPORT_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "agent_client_protocol": "acp",
        "attrs": "attr",
        "beautifulsoup4": "bs4",
        "gitpython": "git",
        "logfire_api": "logfire",
        "opencv_python": "cv2",
        "pillow": "PIL",
        "py_key_value_aio": "key_value",
        "pydantic_ai_backend": "pydantic_ai_backends",
        "pydantic_ai_slim": "pydantic_ai",
        "pyjwt": "jwt",
        "python_dotenv": "dotenv",
        "python_multipart": "multipart",
        "pyyaml": "yaml",
        "scikit_learn": "sklearn",
    }
)
_DEV_EXTRA_GROUP_NAMES: Final[frozenset[str]] = frozenset(
    {"dev", "test", "tests", "testing", "lint", "docs", "typing", "typecheck"}
)

type _RuleFn = Callable[[SourceUnit], list[RawDiscrepancy]]


def _build_per_unit_rules() -> Mapping[str, _RuleFn]:
    rules: dict[str, _RuleFn] = {
        "JBC-DBC-001": check_dbc_001,
        "JBC-DBC-002": check_dbc_002,
        "JBC-DBC-003": check_dbc_003,
        "JBC-DBC-004": check_dbc_004,
        "JBC-DBC-005": check_dbc_005,
        "JBC-FLT-001": check_flt_001,
        "JBC-FLT-002": check_flt_002,
        "JBC-FLT-003": check_flt_003,
        "JBC-FLT-004": check_flt_004,
        "JBC-FLT-005": check_flt_005,
        "JBC-FLT-006": check_flt_006,
        "JBC-SEC-001": check_sec_001,
        "JBC-SEC-002": check_sec_002,
        "JBC-SEC-003": check_sec_003,
        "JBC-SEC-004": check_sec_004,
        "JBC-SEC-005": check_sec_005,
        "JBC-COG-001": check_cog_001,
        "JBC-COG-002": check_cog_002,
        "JBC-COG-004": check_cog_004,
        "JBC-COG-005": check_cog_005,
        "JBC-OBS-001": check_obs_001,
        "JBC-OBS-002": check_obs_002,
        "JBC-OBS-003": check_obs_003,
        "JBC-VRF-001": check_vrf_001,
        "JBC-VRF-002": check_vrf_002,
        "JBC-VRF-004": check_vrf_004,
    }
    return MappingProxyType(rules)


_PER_UNIT_RULES: Final[Mapping[str, _RuleFn]] = _build_per_unit_rules()


class ManifestDependencies(BaseModel):
    """Normalized root import names declared in ``pyproject.toml``."""

    model_config = STRICT_FROZEN_CONFIG

    runtime_roots: Annotated[frozenset[IdentifierStr], Field(max_length=1024)] = frozenset()
    dev_roots: Annotated[frozenset[IdentifierStr], Field(max_length=1024)] = frozenset()
    workspace_roots: Annotated[frozenset[IdentifierStr], Field(max_length=256)] = frozenset()


@jezz_callable
def normalize_pep508_to_import_root(requirement: BoundedText) -> BoundedText:
    """Convert a PEP 508 dependency specifier (e.g., ``pydantic-ai>=2.0``) to import root."""
    match = _PEP508_NAME_RE.match(requirement)
    if match is None:
        return ""
    normalized = re.sub(r"[-.]+", "_", match.group(1)).lower()
    return _PYPI_IMPORT_ALIASES.get(normalized, normalized)


def _extract_dep_strings(raw_group: object) -> list[str]:
    if not isinstance(raw_group, list):
        return []
    return [item for item in raw_group if isinstance(item, str)]


def _collect_group_deps(section: object) -> list[str]:
    if not isinstance(section, dict):
        return []
    collected: list[str] = []
    for group_items in section.values():
        collected.extend(_extract_dep_strings(group_items))
    return collected


def _partition_optional_deps(section: object) -> tuple[list[str], list[str]]:
    if not isinstance(section, dict):
        return [], []
    runtime_extras: list[str] = []
    dev_extras: list[str] = []
    for group_name, group_items in section.items():
        specs = _extract_dep_strings(group_items)
        target = dev_extras if str(group_name).lower() in _DEV_EXTRA_GROUP_NAMES else runtime_extras
        target.extend(specs)
    return runtime_extras, dev_extras


def _expand_import_roots(specs: Sequence[str]) -> set[str]:
    roots: set[str] = set()
    for spec in specs:
        match = _PEP508_NAME_RE.match(spec)
        if match is None:
            continue
        raw_norm = re.sub(r"[-.]+", "_", match.group(1)).lower()
        roots.add(raw_norm)
        roots.add(_PYPI_IMPORT_ALIASES.get(raw_norm, raw_norm))
    return roots


@jezz_callable
def load_setup_cfg_dependencies(
    setup_cfg_target: ContainedPath,
) -> tuple[frozenset[IdentifierStr], frozenset[IdentifierStr]]:
    """Parse ``setup.cfg`` ``[options]`` and ``[options.extras_require]`` dependency roots."""
    raw_cfg = read_bounded_text(setup_cfg_target, max_bytes=524_288)
    parser = configparser.ConfigParser()
    parser.read_string(raw_cfg)
    raw_install = parser.get("options", "install_requires", fallback="")
    runtime_lines = [ln.strip() for ln in raw_install.splitlines() if ln.strip()]
    dev_lines: list[str] = []
    if parser.has_section("options.extras_require"):
        for group_name, raw_val in parser.items("options.extras_require"):
            specs = [ln.strip() for ln in raw_val.splitlines() if ln.strip()]
            dest = dev_lines if group_name.lower() in _DEV_EXTRA_GROUP_NAMES else runtime_lines
            dest.extend(specs)
    return frozenset(_expand_import_roots(runtime_lines)), frozenset(
        _expand_import_roots(dev_lines)
    )


@jezz_callable
def load_pyproject_dependencies(pyproject_target: ContainedPath) -> ManifestDependencies:
    """Parse ``pyproject.toml`` to extract runtime, dev, and project import roots."""
    raw_toml = read_bounded_text(pyproject_target, max_bytes=524_288)
    data = tomllib.loads(raw_toml)
    project = data.get("project", {})
    opt_runtime_specs, opt_dev_specs = _partition_optional_deps(
        project.get("optional-dependencies", {})
    )
    runtime_specs = [
        *_extract_dep_strings(project.get("dependencies", [])),
        *opt_runtime_specs,
    ]
    runtime_roots = _expand_import_roots(runtime_specs)

    dev_specs = [
        *_collect_group_deps(data.get("dependency-groups", {})),
        *opt_dev_specs,
    ]
    dev_roots = _expand_import_roots(dev_specs)

    ws_roots: set[str] = {"tests"}
    proj_name = project.get("name")
    if isinstance(proj_name, str) and proj_name:
        ws_roots.add(normalize_pep508_to_import_root(proj_name))

    return ManifestDependencies(
        runtime_roots=frozenset(runtime_roots),
        dev_roots=frozenset(dev_roots),
        workspace_roots=frozenset(ws_roots),
    )


@jezz_callable
def load_workspace_units(
    repo_root: Path,
    *,
    target_dirs: Sequence[IdentifierStr] = ("src", "tests"),
) -> tuple[SourceUnit, ...]:
    """Load and parse all Python ``.py`` files under ``target_dirs`` inside ``repo_root``."""
    resolved_root = repo_root.resolve()
    units: list[SourceUnit] = []
    for rel_dir in target_dirs:
        dir_contained = ContainedPath.from_parts(resolved_root, rel_dir)
        if not dir_contained.resolved_path.exists():
            continue
        for py_path in sorted(dir_contained.resolved_path.rglob("*.py")):
            rel_posix = py_path.relative_to(resolved_root).as_posix()
            file_contained = ContainedPath.from_parts(resolved_root, rel_posix)
            source = read_bounded_text(file_contained)
            units.append(parse_source_unit(file_contained.relative_posix, source))
    return tuple(units)


@icontract.require(lambda units: len(units) >= 1, "At least one SourceUnit is required.")
@icontract.ensure(lambda result: len(result) == 64)
@jezz_callable
def compute_units_digest(
    units: Annotated[Sequence[SourceUnit], Field(min_length=1, max_length=100_000)],
) -> Annotated[str, Field(min_length=64, max_length=64)]:
    """Compute a deterministic SHA-256 hex digest over sorted ``(file_path, source)`` pairs."""
    hasher = hashlib.sha256()
    for unit in sorted(units, key=lambda u: u.file_path):
        hasher.update(unit.file_path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(unit.source.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _unit_triggers(unit: SourceUnit) -> set[CodeTrigger]:
    triggers: set[CodeTrigger] = {CodeTrigger.UNIVERSAL}
    if unit.is_test_file:
        triggers.add(CodeTrigger.VERIFICATION_SUITE)
    if unit.classes:
        triggers.add(CodeTrigger.CONSTRAINED_DOMAIN)
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div | ast.FloorDiv | ast.Mod):
            triggers.add(CodeTrigger.CONSTRAINED_DOMAIN)
        if isinstance(node, ast.Call):
            triggers.add(CodeTrigger.EXTERNAL_BOUNDARY)
    return triggers


@jezz_callable
def detect_hazard_triggers(units: Sequence[SourceUnit]) -> frozenset[CodeTrigger]:
    """Auto-detect occupancy/hazard triggers across ``units`` so hazards cannot go unbound."""
    detected: set[CodeTrigger] = {CodeTrigger.UNIVERSAL}
    for unit in units:
        detected |= _unit_triggers(unit)
    return frozenset(detected)


def _apply_permit_variances(
    discrepancies: Sequence[RawDiscrepancy],
    permit: CodePermit,
) -> list[InspectionFinding]:
    findings: list[InspectionFinding] = []
    for raw in discrepancies:
        code = CODE_BOOK[raw.code_id]
        matched_var = permit.find_variance(raw.code_id, raw.location)
        if matched_var is not None:
            findings.append(
                InspectionFinding(
                    code_id=raw.code_id,
                    severity=code.severity,
                    disposition=FindingDisposition.VARIANCE_WAIVED,
                    location=raw.location,
                    message=raw.message,
                    remediation=raw.remediation,
                    variance_id=matched_var.variance_id,
                )
            )
            continue
        findings.append(
            InspectionFinding(
                code_id=raw.code_id,
                severity=code.severity,
                disposition=FindingDisposition.VIOLATION,
                location=raw.location,
                message=raw.message,
                remediation=raw.remediation,
            )
        )
    return findings


def _build_section_verdicts(
    effective_ids: AbstractSet[str],
    findings: Sequence[InspectionFinding],
    unit_count: int,
) -> tuple[CodeSectionVerdict, ...]:
    by_code: dict[str, list[InspectionFinding]] = {cid: [] for cid in effective_ids}
    for finding in findings:
        if finding.code_id in by_code:
            by_code[finding.code_id].append(finding)

    verdicts: list[CodeSectionVerdict] = []
    for cid in sorted(effective_ids):
        items = by_code[cid]
        violations = sum(1 for f in items if f.disposition == FindingDisposition.VIOLATION)
        waived = sum(1 for f in items if f.disposition == FindingDisposition.VARIANCE_WAIVED)
        code = CODE_BOOK[cid]
        summary = (
            f"Inspected {unit_count} unit(s) via {code.inspection_method}: "
            f"{violations} violation(s), {waived} waived."
        )
        verdicts.append(
            CodeSectionVerdict(
                code_id=cid,
                passed=(violations == 0),
                violation_count=violations,
                waived_count=waived,
                evidence_summary=summary,
            )
        )
    return tuple(verdicts)


def _derive_ruling(verdicts: Sequence[CodeSectionVerdict]) -> OccupancyRuling:
    if any(not v.passed for v in verdicts):
        return OccupancyRuling.RED_TAG
    if any(v.waived_count > 0 for v in verdicts):
        return OccupancyRuling.APPROVED_WITH_VARIANCES
    return OccupancyRuling.APPROVED


def _assemble_inspection_report(
    permit: CodePermit,
    detected_triggers: frozenset[CodeTrigger],
    effective_ids: frozenset[str],
    raw_discrepancies: Sequence[RawDiscrepancy],
    unit_count: int,
) -> InspectionReport:
    findings = _apply_permit_variances(raw_discrepancies, permit)
    verdicts = _build_section_verdicts(effective_ids, findings, unit_count)
    return InspectionReport(
        permit_id=permit.permit_id,
        ruling=_derive_ruling(verdicts),
        auto_detected_triggers=detected_triggers,
        effective_code_ids=effective_ids,
        section_verdicts=verdicts,
        findings=tuple(findings),
    )


class BuildingInspector:
    """Deterministic enforcement engine for the Jezz Software Building Code (JBC v1.1)."""

    @jezz_callable
    def __init__(self, manifest: ManifestDependencies | None = None) -> None:
        self.manifest = manifest or ManifestDependencies(workspace_roots=frozenset({"jezz"}))

    @classmethod
    @jezz_callable
    def from_workspace(cls, repo_root: Path) -> BuildingInspector:
        """Create a ``BuildingInspector`` grounded in ``pyproject.toml`` and ``setup.cfg``."""
        pyproject = ContainedPath.from_parts(repo_root, "pyproject.toml")
        base = (
            load_pyproject_dependencies(pyproject)
            if pyproject.resolved_path.is_file()
            else ManifestDependencies()
        )
        setup_cfg = ContainedPath.from_parts(repo_root, "setup.cfg")
        if not setup_cfg.resolved_path.is_file():
            return BuildingInspector(manifest=base)
        cfg_run, cfg_dev = load_setup_cfg_dependencies(setup_cfg)
        merged = ManifestDependencies(
            runtime_roots=frozenset(base.runtime_roots | cfg_run),
            dev_roots=frozenset(base.dev_roots | cfg_dev),
            workspace_roots=base.workspace_roots,
        )
        return BuildingInspector(manifest=merged)

    def _run_single_static_code(
        self,
        code_id: str,
        units: Sequence[SourceUnit],
    ) -> list[RawDiscrepancy]:
        rule_fn = _PER_UNIT_RULES.get(code_id)
        if rule_fn is not None:
            return [item for unit in units for item in rule_fn(unit)]
        if code_id == "JBC-COG-003":
            return check_cog_003(units)
        if code_id == "JBC-VRF-003":
            return [
                item
                for unit in units
                for item in check_vrf_003(
                    unit,
                    runtime_roots=self.manifest.runtime_roots,
                    dev_roots=self.manifest.dev_roots,
                    workspace_roots=self.manifest.workspace_roots,
                )
            ]
        return []

    def _collect_static_discrepancies(
        self,
        units: Sequence[SourceUnit],
        active_code_ids: AbstractSet[str],
    ) -> list[RawDiscrepancy]:
        discrepancies: list[RawDiscrepancy] = []
        for code_id in sorted(active_code_ids):
            discrepancies.extend(self._run_single_static_code(code_id, units))
        return discrepancies

    @jezz_callable
    def inspect_rough_in(
        self,
        units: Sequence[SourceUnit],
        *,
        permit: CodePermit | None = None,
    ) -> InspectionReport:
        """Run static/AST/graph/lexical rough-in inspection (BHV-18 VERIFY_STATIC)."""
        active_permit = permit or CodePermit.full_book(permit_id="PERMIT-ROUGH-IN")
        detected = detect_hazard_triggers(units)
        triggered_ids = {c.code_id for c in codes_for_triggers(detected)}
        candidate_ids = active_permit.bound_code_ids | triggered_ids
        static_ids = frozenset(
            cid
            for cid in candidate_ids
            if CODE_BOOK[cid].inspection_method != InspectionMethod.GATE_ATTESTATION
        )
        raw = self._collect_static_discrepancies(units, static_ids)
        return _assemble_inspection_report(active_permit, detected, static_ids, raw, len(units))

    @jezz_callable
    def inspect_final(
        self,
        units: Sequence[SourceUnit],
        *,
        permit: CodePermit | None = None,
        attestations: Sequence[GateAttestation] = (),
    ) -> InspectionReport:
        """Run full rough-in + gate-attestation final inspection for occupancy certification."""
        active_permit = permit or CodePermit.full_book(permit_id="PERMIT-FINAL")
        detected = detect_hazard_triggers(units)
        triggered_ids = {c.code_id for c in codes_for_triggers(detected)}
        effective_ids = frozenset(active_permit.bound_code_ids | triggered_ids)

        raw = self._collect_static_discrepancies(units, effective_ids)
        if "JBC-VRF-005" in effective_ids:
            raw.extend(check_vrf_005(attestations))
        if "JBC-VRF-006" in effective_ids:
            raw.extend(check_vrf_006(attestations))

        return _assemble_inspection_report(active_permit, detected, effective_ids, raw, len(units))


@jezz_callable
def bind_permit_to_state(state: AgentExecutionState, permit: CodePermit) -> None:
    """Bind a ``CodePermit`` into Pillar 1 ``AgentExecutionState`` (BHV-03 BIND_CODES)."""
    state.problem.bound_code_ids = set(permit.bound_code_ids)


@jezz_callable
def record_inspection_to_state(
    state: AgentExecutionState,
    report: InspectionReport,
    *,
    stage: Literal["rough_in", "final"],
) -> None:
    """Record an ``InspectionReport`` into Pillar 1 ``EvidenceState``."""
    state.evidence.static_checks_passed = report.is_occupiable
    if stage == "final":
        state.evidence.dynamic_checks_passed = report.is_occupiable
    state.evidence.verification_records.append(
        f"{stage}:{report.permit_id}:{report.ruling}:{len(report.unwaived_violations())}_violations"
    )


@jezz_callable
def certify_occupancy_in_state(
    state: AgentExecutionState,
    report: InspectionReport,
    *,
    artifact_digest: Annotated[str, Field(min_length=8, max_length=128)],
) -> CertificateOfOccupancy:
    """Enforce bound-code coverage and issue a ``CertificateOfOccupancy`` at BHV-21 CERTIFY."""
    if not state.problem.bound_code_ids:
        msg = "Cannot certify occupancy when ProblemState.bound_code_ids is empty."
        raise RedTagError(msg, report)

    uninspected = state.problem.bound_code_ids - set(report.effective_code_ids)
    if uninspected:
        msg = f"Bound codes were omitted from final inspection: {sorted(uninspected)}."
        raise RedTagError(msg, report)

    certificate = issue_certificate(report, artifact_digest=artifact_digest)
    state.evidence.certified = True
    state.workspace.sealed_commit = certificate.inspector_seal
    return certificate
