"""Pillar 3 (PCE): Reality probing, 3-axis intake, and phased ProjectCharter compilation."""

import importlib.metadata
import re
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final

import icontract
from pydantic import Field

from jezz.codes.ast_utils import SourceUnit
from jezz.codes.complexity import PermitProfile
from jezz.codes.discovery import build_workspace_inspector, discover_workspace_units
from jezz.codes.inspector import ManifestDependencies, detect_hazard_triggers
from jezz.codes.polyglot_ir import (
    InspectedUnit,
    PolyglotManifest,
    PolyglotUnit,
    SupportedLanguage,
)
from jezz.codes.taxonomy import CodeTrigger
from jezz.guards import (
    BoundedText,
    ContainedPath,
    IdentifierStr,
    LoopBudget,
    ShortText,
    jezz_callable,
)
from jezz.methodology.charter_schemas import (
    AUTHORIZED_URI_SCHEMES,
    REQUIRED_ELICITATION_AXES,
    AxisQuestion,
    AxisResolution,
    CharterIntakeSession,
    CharterSealInput,
    ElicitationAxis,
    HazardMutationPolicy,
    MandateDependency,
    MutationPosture,
    ProjectCharter,
    RealityProbeEvidence,
    build_charter_seal_base,
    compute_reality_base_digest,
    compute_reality_probe_digest,
    finalize_charter_seal,
    validate_canonical_resolutions,
    validate_hazard_policy_against_triggers,
)
from jezz.methodology.lot_line import assess_mandate_lot_line
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope

_SAFE_BINARY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_PKG_QUERY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9._/-]{0,126}$")
_PACKAGE_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,48}$")
_NON_ID_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9]+")
_MANIFEST_NAMES: Final[tuple[str, ...]] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
)
_EXTERNAL_VISION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:subprocess|https?|rest\s+api|graphql|webhook|socket|network|database|sql|"
    r"external\s+api|cloud\s+sync|rpc|grpc|proton\s*pass|pass-cli|1password|op\s+cli|"
    r"vault\s+sync|external\s+cli)\b",
    re.IGNORECASE,
)
_SENSITIVE_VISION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:secret|credential|password|vault|keychain|api[_\s-]?key|oauth|jwt|"
    r"bearer\s+token|encryption|credit\s+card|logins?|email\s+alias(?:es)?|"
    r"proton\s*pass|1password)\b",
    re.IGNORECASE,
)
_CONSTRAINED_VISION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:ledger|escrow|accounting|double-entry|settlement|billing|quota|"
    r"rate[_\s-]?limit|telemetry|safety-critical)\b",
    re.IGNORECASE,
)


def _collect_unit_languages(units: Sequence[InspectedUnit]) -> tuple[SupportedLanguage, ...]:
    langs: set[SupportedLanguage] = set()
    for unit in units:
        if isinstance(unit, SourceUnit):
            langs.add(SupportedLanguage.PYTHON)
        elif isinstance(unit, PolyglotUnit):
            langs.add(unit.language)
    return tuple(sorted(langs, key=str))


def _collect_declared_deps(resolved_root: Path, units: Sequence[InspectedUnit]) -> tuple[str, ...]:
    inspector = build_workspace_inspector(resolved_root, units)
    deps: set[str] = set()
    deps |= inspector.manifest.runtime_roots | inspector.manifest.dev_roots
    pm = inspector.polyglot_manifest
    for eco in (pm.python, pm.typescript, pm.rust, pm.go):
        deps |= eco.runtime_roots | eco.dev_roots
    return tuple(sorted(d[:256] for d in deps if d))[:512]


def _probe_binaries(candidates: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    avail: list[str] = []
    missing: list[str] = []
    for raw in dict.fromkeys(candidates):
        cleaned = raw.strip()
        if _SAFE_BINARY_RE.fullmatch(cleaned) is None:
            msg = (
                f"Invalid binary name '{raw}' for reality probe; pass bare executable names "
                "only (never filesystem paths or shell flags)."
            )
            raise ValueError(msg)
        (avail if shutil.which(cleaned) is not None else missing).append(cleaned)
    return tuple(sorted(avail)), tuple(sorted(missing))


def _is_package_installed_or_declared(pkg_name: str, declared_lower: set[str]) -> bool:
    norm_underscore = re.sub(r"[-.]+", "_", pkg_name).lower()
    if pkg_name.lower() in declared_lower or norm_underscore in declared_lower:
        return True
    for candidate in (pkg_name, pkg_name.replace("_", "-")):
        try:
            importlib.metadata.version(candidate)
            return True
        except importlib.metadata.PackageNotFoundError:
            continue
    return False


def _probe_packages(
    candidates: Sequence[str],
    declared_deps: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    declared_lower = {d.lower() for d in declared_deps}
    verified: list[str] = []
    missing: list[str] = []
    for raw in dict.fromkeys(candidates):
        cleaned = raw.strip()
        if _SAFE_PKG_QUERY_RE.fullmatch(cleaned) is None:
            msg = f"Invalid package identifier '{raw}' in candidate_packages."
            raise ValueError(msg)
        target = verified if _is_package_installed_or_declared(cleaned, declared_lower) else missing
        target.append(cleaned)
    return tuple(sorted(verified)), tuple(sorted(missing))


@jezz_callable
def probe_workspace_reality(
    repo_root: Path,
    *,
    candidate_binaries: Annotated[Sequence[IdentifierStr], Field(max_length=64)] = (),
    candidate_packages: Annotated[Sequence[IdentifierStr], Field(max_length=64)] = (),
) -> RealityProbeEvidence:
    """Capture empirical workspace manifests, languages, binaries, and packages prior to intake."""
    resolved_root = repo_root.resolve()
    if not resolved_root.is_dir():
        msg = f"Workspace directory does not exist: '{repo_root}'."
        raise FileNotFoundError(msg)
    manifests = tuple(
        name
        for name in _MANIFEST_NAMES
        if ContainedPath.from_parts(resolved_root, name).resolved_path.is_file()
    )
    units = discover_workspace_units(resolved_root)
    prod_count = sum(1 for u in units if not u.is_test_file)
    langs = _collect_unit_languages(units)
    declared = _collect_declared_deps(resolved_root, units)
    avail_bins, miss_bins = _probe_binaries(candidate_binaries)
    ver_pkgs, miss_pkgs = _probe_packages(candidate_packages, declared)
    base = compute_reality_base_digest(
        manifests, langs, prod_count, declared, avail_bins, miss_bins
    )
    digest = compute_reality_probe_digest(base, ver_pkgs, miss_pkgs)
    return RealityProbeEvidence(
        detected_manifests=manifests,
        detected_languages=langs,
        existing_prod_modules=prod_count,
        declared_dependencies=declared,
        available_binaries=avail_bins,
        missing_binaries=miss_bins,
        verified_packages=ver_pkgs,
        missing_packages=miss_pkgs,
        evidence_digest=digest,
    )


@jezz_callable
def detect_vision_triggers(
    vision: BoundedText,
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)] = (),
) -> Annotated[tuple[CodeTrigger, ...], Field(min_length=1, max_length=5)]:
    """Infer statutory JBC CodeTriggers from project vision prose and existing workspace units."""
    prod_units = tuple(u for u in units if not u.is_test_file)
    triggers: set[CodeTrigger] = set(detect_hazard_triggers(prod_units))
    triggers.add(CodeTrigger.UNIVERSAL)
    if _EXTERNAL_VISION_RE.search(vision) is not None:
        triggers.add(CodeTrigger.EXTERNAL_BOUNDARY)
    if _SENSITIVE_VISION_RE.search(vision) is not None:
        triggers.add(CodeTrigger.SENSITIVE_CONTEXT)
    if _CONSTRAINED_VISION_RE.search(vision) is not None:
        triggers.add(CodeTrigger.CONSTRAINED_DOMAIN)
    return tuple(sorted(triggers, key=str))


def _derive_charter_id(title: str) -> str:
    slug = _NON_ID_CHARS_RE.sub("-", title.strip()).strip("-").upper()[:32]
    return f"CHR-{slug or 'PROJECT'}"


def _build_intake_questions(
    triggers: Sequence[CodeTrigger],
    probe: RealityProbeEvidence,
) -> tuple[AxisQuestion, ...]:
    trig_set = set(triggers)
    missing_note = ""
    if probe.missing_binaries or probe.missing_packages:
        missing_note = (
            f" Missing probed dependencies/CLIs detected "
            f"(binaries={list(probe.missing_binaries)}, packages={list(probe.missing_packages)})."
        )
    hazard_prompt = (
        "Specify the external mutation posture (read_only, dry_run_plan_apply, or "
        "transactional_rollback), authorized secret URI schemes (env/pass/op/keychain), "
        "and subprocess/network timeout bounds."
        if (CodeTrigger.EXTERNAL_BOUNDARY in trig_set or CodeTrigger.SENSITIVE_CONTEXT in trig_set)
        else "Confirm local_workspace_only or read_only mutation posture and verify whether "
        "any secret or external I/O boundaries exist."
    )
    return (
        AxisQuestion(
            question_id="Q-ONTOLOGY-01",
            axis=ElicitationAxis.DOMAIN_ONTOLOGY,
            prompt=(
                "Define the core domain entities, canonical identity keys, cardinality "
                "relationships, and cross-entity invariants."
            ),
            rationale="Grounds closed schemas (JBC-DBC-001) and falsifiable DomainInvariants.",
        ),
        AxisQuestion(
            question_id="Q-HAZARD-01",
            axis=ElicitationAxis.HAZARD_MUTATION_POLICY,
            prompt=f"{hazard_prompt}{missing_note}"[:512],
            rationale="Prevents unguarded irreversible external mutations and secret leaks.",
        ),
        AxisQuestion(
            question_id="Q-SCOPE-01",
            axis=ElicitationAxis.SCOPE_AND_EXCLUSIONS,
            prompt=(
                "Specify the target package/compartment layout, initial phased capabilities, "
                "and explicit out-of-scope exclusions."
            ),
            rationale="Bounds ModuleWriteScopes and prevents unpermitted scope creep.",
        ),
    )


@jezz_callable
def start_charter_intake(
    title: ShortText,
    vision: Annotated[str, Field(min_length=12, max_length=65_536)],
    reality_probe: RealityProbeEvidence,
    *,
    charter_id: IdentifierStr | None = None,
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)] = (),
) -> CharterIntakeSession:
    """Initialize a Phase 0/I CharterIntakeSession anchored to a verified RealityProbeEvidence."""
    resolved_id = charter_id if charter_id is not None else _derive_charter_id(title)
    triggers = detect_vision_triggers(vision, units)
    questions = _build_intake_questions(triggers, reality_probe)
    return CharterIntakeSession(
        charter_id=resolved_id,
        title=title,
        vision=vision,
        reality_probe=reality_probe,
        detected_triggers=triggers,
        questions=questions,
    )


@icontract.ensure(lambda mandates, result: len(result) == len(mandates))
@jezz_callable
def topologically_order_mandates(
    mandates: Annotated[Sequence[EngineeringMandate], Field(min_length=1, max_length=16)],
    dependencies: Annotated[Sequence[MandateDependency], Field(max_length=64)] = (),
) -> Annotated[tuple[EngineeringMandate, ...], Field(min_length=1, max_length=16)]:
    """Order ``mandates`` into a valid topological execution sequence via Kahn peeling."""
    by_id: dict[str, EngineeringMandate] = {}
    for item in mandates:
        if item.mandate_id in by_id:
            msg = f"Duplicate mandate_id '{item.mandate_id}'."
            raise ValueError(msg)
        by_id[item.mandate_id] = item

    prereqs: dict[str, set[str]] = {m.mandate_id: set() for m in mandates}
    for dep in dependencies:
        if dep.mandate_id not in prereqs or dep.depends_on not in prereqs:
            msg = (
                f"MandateDependency '{dep.depends_on} -> {dep.mandate_id}' references "
                "unknown mandate_id."
            )
            raise ValueError(msg)
        prereqs[dep.mandate_id].add(dep.depends_on)

    completed: set[str] = set()
    remaining: list[EngineeringMandate] = list(mandates)
    ordered: list[EngineeringMandate] = []
    budget = LoopBudget(max_steps=len(remaining) + 2)
    while remaining:
        budget.tick()
        ready = [m for m in remaining if prereqs[m.mandate_id] <= completed]
        if not ready:
            stalled = [m.mandate_id for m in remaining]
            msg = f"Cyclic MandateDependency graph detected across mandates: {stalled}."
            raise ValueError(msg)
        ordered.extend(ready)
        completed |= {m.mandate_id for m in ready}
        remaining = [m for m in remaining if m.mandate_id not in completed]
    return tuple(ordered)


def _build_foundation_mandate(
    stem: str,
    pkg: str,
    ontology: AxisResolution,
    exclusions: tuple[str, ...],
) -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id=f"MND-{stem}-01",
        title="Permit 1: Closed Domain Ontology & Read-Only Parser",
        objective=ontology.summary,
        invariants=(
            DomainInvariant(
                invariant_id="INV-101",
                statement=(
                    "Closed domain models reject unknown fields and enforce canonical "
                    "entity identity invariants."
                ),
                verification_symbol=f"{pkg}.models.DomainEntity",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_domain_models",
                owned_files=frozenset({f"src/{pkg}/__init__.py", f"src/{pkg}/models.py"}),
                contract_symbols=(f"{pkg}.models.DomainEntity",),
            ),
            ModuleWriteScope(
                scope_id="scope_domain_models_tests",
                owned_files=frozenset({"tests/test_models.py"}),
                contract_symbols=("test_domain_entity_invariants",),
                depends_on=frozenset({"scope_domain_models"}),
            ),
        ),
        out_of_scope=exclusions,
    )


def _build_policy_mandate(
    stem: str,
    pkg: str,
    scope_res: AxisResolution,
    exclusions: tuple[str, ...],
) -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id=f"MND-{stem}-02",
        title="Permit 2: Cross-Entity Invariant & Policy Engine",
        objective=scope_res.summary,
        invariants=(
            DomainInvariant(
                invariant_id="INV-201",
                statement=(
                    "Cross-entity policy evaluation deterministically identifies all "
                    "cardinality and alignment violations without side effects."
                ),
                verification_symbol=f"{pkg}.policy.evaluate_domain_policy",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_policy_engine",
                owned_files=frozenset({f"src/{pkg}/policy.py"}),
                contract_symbols=(f"{pkg}.policy.evaluate_domain_policy",),
            ),
            ModuleWriteScope(
                scope_id="scope_policy_tests",
                owned_files=frozenset({"tests/test_policy.py"}),
                contract_symbols=("test_policy_alignment_invariants",),
                depends_on=frozenset({"scope_policy_engine"}),
            ),
        ),
        out_of_scope=exclusions,
    )


def _build_mutator_mandate(
    stem: str,
    pkg: str,
    hazard_res: AxisResolution,
    exclusions: tuple[str, ...],
) -> EngineeringMandate:
    return EngineeringMandate(
        mandate_id=f"MND-{stem}-03",
        title="Permit 3: Guarded External Mutator & Rollback Adapter",
        objective=hazard_res.summary,
        invariants=(
            DomainInvariant(
                invariant_id="INV-301",
                statement=(
                    "External mutations execute strictly from verified dry-run plans "
                    "within bounded timeouts and preserve rollback recovery records."
                ),
                verification_symbol=f"{pkg}.mutator.apply_mutation_plan",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_guarded_mutator",
                owned_files=frozenset({f"src/{pkg}/mutator.py"}),
                contract_symbols=(f"{pkg}.mutator.apply_mutation_plan",),
            ),
            ModuleWriteScope(
                scope_id="scope_mutator_tests",
                owned_files=frozenset({"tests/test_mutator.py"}),
                contract_symbols=("test_mutator_plan_and_rollback",),
                depends_on=frozenset({"scope_guarded_mutator"}),
            ),
        ),
        out_of_scope=exclusions,
    )


@jezz_callable
def compile_phased_mandate_queue(
    package_slug: IdentifierStr,
    resolutions: Annotated[Sequence[AxisResolution], Field(max_length=8)],
    hazard_policy: HazardMutationPolicy,
    *,
    scope_exclusions: Annotated[Sequence[ShortText], Field(max_length=64)] = (),
    charter_id: IdentifierStr = "CHR-PROJECT",
) -> tuple[tuple[EngineeringMandate, ...], tuple[MandateDependency, ...]]:
    """Compile a topologically ordered 2- or 3-permit EngineeringMandate queue from resolutions."""
    if _PACKAGE_SLUG_RE.fullmatch(package_slug) is None:
        msg = (
            f"Invalid package_slug '{package_slug}'; expected lowercase snake_case identifier "
            "matching ^[a-z][a-z0-9_]{1,48}$."
        )
        raise ValueError(msg)
    canonical = validate_canonical_resolutions(resolutions)
    by_axis = {r.axis: r for r in canonical}
    stem = _NON_ID_CHARS_RE.sub("-", charter_id.removeprefix("CHR-")).strip("-").upper()[:24]
    norm_stem = stem or "PROJECT"
    excl_tuple = tuple(scope_exclusions)

    m1 = _build_foundation_mandate(
        norm_stem, package_slug, by_axis[ElicitationAxis.DOMAIN_ONTOLOGY], excl_tuple
    )
    m2 = _build_policy_mandate(
        norm_stem, package_slug, by_axis[ElicitationAxis.SCOPE_AND_EXCLUSIONS], excl_tuple
    )
    dep12 = MandateDependency(mandate_id=m2.mandate_id, depends_on=m1.mandate_id)
    if hazard_policy.mutation_posture in {
        MutationPosture.DRY_RUN_PLAN_APPLY,
        MutationPosture.TRANSACTIONAL_ROLLBACK,
    }:
        m3 = _build_mutator_mandate(
            norm_stem, package_slug, by_axis[ElicitationAxis.HAZARD_MUTATION_POLICY], excl_tuple
        )
        dep23 = MandateDependency(mandate_id=m3.mandate_id, depends_on=m2.mandate_id)
        return (m1, m2, m3), (dep12, dep23)
    return (m1, m2), (dep12,)


def _resolve_mandates_for_seal(
    seal_input: CharterSealInput,
    canonical_res: tuple[AxisResolution, ...],
) -> tuple[tuple[EngineeringMandate, ...], tuple[MandateDependency, ...]]:
    if seal_input.mandates:
        ordered = topologically_order_mandates(
            seal_input.mandates,
            seal_input.mandate_dependencies,
        )
        return ordered, seal_input.mandate_dependencies
    if seal_input.package_slug is not None:
        return compile_phased_mandate_queue(
            seal_input.package_slug,
            canonical_res,
            seal_input.hazard_policy,
            scope_exclusions=seal_input.scope_exclusions,
            charter_id=seal_input.intake.charter_id,
        )
    msg = (
        "CharterSealInput requires either explicit 'mandates' or 'package_slug' to compile "
        "a phased EngineeringMandate queue."
    )
    raise ValueError(msg)


@jezz_callable
def seal_project_charter(
    seal_input: CharterSealInput,
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)] = (),
    *,
    manifest: ManifestDependencies | None = None,
    polyglot_manifest: PolyglotManifest | None = None,
    profile: PermitProfile = PermitProfile.STANDARD,
) -> ProjectCharter:
    """Validate 3-axis completeness, hazard posture, and lot lines, then seal a ProjectCharter."""
    active_res = (
        seal_input.resolutions if seal_input.resolutions else seal_input.intake.recorded_resolutions
    )
    canonical_res = validate_canonical_resolutions(active_res)
    validate_hazard_policy_against_triggers(
        seal_input.hazard_policy,
        seal_input.intake.detected_triggers,
    )
    ordered_mandates, deps = _resolve_mandates_for_seal(seal_input, canonical_res)
    lot_assessments = tuple(
        assess_mandate_lot_line(
            mandate,
            units,
            manifest=manifest,
            polyglot_manifest=polyglot_manifest,
            profile=profile,
        )
        for mandate in ordered_mandates
    )
    core = build_charter_seal_base(
        seal_input.intake.charter_id,
        seal_input.intake.reality_probe.evidence_digest,
        seal_input.intake.detected_triggers,
        canonical_res,
        seal_input.hazard_policy,
        ordered_mandates,
    )
    seal = finalize_charter_seal(
        core,
        seal_input.intake.title,
        seal_input.intake.vision,
        seal_input.scope_exclusions,
        deps,
    )
    return ProjectCharter(
        charter_id=seal_input.intake.charter_id,
        title=seal_input.intake.title,
        vision=seal_input.intake.vision,
        reality_probe=seal_input.intake.reality_probe,
        detected_triggers=seal_input.intake.detected_triggers,
        resolutions=canonical_res,
        hazard_policy=seal_input.hazard_policy,
        scope_exclusions=seal_input.scope_exclusions,
        mandate_queue=ordered_mandates,
        mandate_dependencies=deps,
        lot_line_assessments=lot_assessments,
        charter_seal=seal,
    )


__all__ = [
    "AUTHORIZED_URI_SCHEMES",
    "REQUIRED_ELICITATION_AXES",
    "AxisQuestion",
    "AxisResolution",
    "CharterIntakeSession",
    "CharterSealInput",
    "ElicitationAxis",
    "HazardMutationPolicy",
    "MandateDependency",
    "MutationPosture",
    "ProjectCharter",
    "RealityProbeEvidence",
    "compile_phased_mandate_queue",
    "detect_vision_triggers",
    "probe_workspace_reality",
    "seal_project_charter",
    "start_charter_intake",
    "topologically_order_mandates",
]
