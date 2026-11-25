"""Pillar 3 (PCE): Pre-flight lot-line shear, floorplate projection & fission disposition."""

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Final

from pydantic import BaseModel, Field

from jezz.codes.ast_graph import build_workspace_ast_graph
from jezz.codes.ast_utils import SourceUnit
from jezz.codes.compartment import (
    CompartmentLimits,
    analyze_workspace_compartments,
    classify_unit_placement,
    resolve_compartment_limits,
)
from jezz.codes.complexity import PermitProfile
from jezz.codes.inspector import (
    ManifestDependencies,
    detect_hazard_triggers,
    normalize_pep508_to_import_root,
)
from jezz.codes.polyglot_ir import (
    InspectedUnit,
    PolyglotManifest,
    SupportedLanguage,
    detect_language_for_path,
    is_supported_source_path,
)
from jezz.codes.taxonomy import CodeTrigger
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    IdentifierStr,
    NonNegativeInt,
    PositiveInt,
    ShortText,
    jezz_callable,
)
from jezz.methodology.mandate import DependencyScope, EngineeringMandate

_MIN_ESTABLISHED_PACKAGE_MODULES: Final[int] = 5

_EXTERNAL_DEP_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "aiohttp",
        "asyncpg",
        "axios",
        "axum",
        "boto3",
        "botocore",
        "express",
        "fastapi",
        "flask",
        "grpcio",
        "httpx",
        "hyper",
        "mcp",
        "psycopg",
        "pymongo",
        "redis",
        "requests",
        "reqwest",
        "sqlalchemy",
        "sqlx",
        "starlette",
        "tokio",
        "tonic",
        "undici",
        "urllib3",
    }
)
_SENSITIVE_DEP_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "argon2",
        "argon2_cffi",
        "bcrypt",
        "cryptography",
        "hvac",
        "jose",
        "jsonwebtoken",
        "jwcrypto",
        "keyring",
        "pyjwt",
        "pynacl",
        "ring",
        "rustls",
    }
)
_EXTERNAL_PROSE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:subprocess|https?|rest\s+api|graphql|webhook|socket|network|database|sql|"
    r"external\s+api|cloud\s+sync|rpc|grpc)\b",
    re.IGNORECASE,
)
_SENSITIVE_PROSE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:secret|credential|password|vault|keychain|api[_\s-]?key|oauth|jwt|"
    r"bearer\s+token|encryption|credit\s+card)\b",
    re.IGNORECASE,
)


class LotLineDisposition(StrEnum):
    """Architectural placement ruling for a proposed EngineeringMandate."""

    IN_PACKAGE = "in_package"
    WORKSPACE_COMPARTMENT = "workspace_compartment"
    LOT_LINE_SPLIT = "lot_line_split"


class LotLineAssessment(BaseModel):
    """Deterministic pre-synthesis package/compartment lot-line shear assessment."""

    model_config = STRICT_FROZEN_CONFIG

    mandate_id: IdentifierStr
    disposition: LotLineDisposition
    existing_prod_modules: NonNegativeInt
    projected_package_modules: NonNegativeInt
    max_package_modules: PositiveInt
    exceeds_package_ceiling: bool
    projected_compartment_modules: NonNegativeInt
    max_compartment_modules: PositiveInt
    exceeds_compartment_ceiling: bool
    inbound_core_importers: NonNegativeInt
    is_zero_fan_in_satellite: bool
    public_api_sufficient: bool
    net_new_runtime_deps: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=128),
    ] = ()
    baseline_triggers: Annotated[
        tuple[CodeTrigger, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=5),
    ]
    mandate_triggers: Annotated[
        tuple[CodeTrigger, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=5),
    ]
    escalated_triggers: Annotated[
        tuple[CodeTrigger, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=5),
    ] = ()
    rationale: ShortText

    @property
    def requires_boundary_review(self) -> bool:
        """Return True iff mandate exceeds in-package bounds and requires placement review."""
        return self.disposition != LotLineDisposition.IN_PACKAGE


@dataclass(frozen=True)
class _ShearSignals:
    """Bundled pre-flight signals evaluated by ``_decide_disposition_and_rationale``."""

    existing_prod: int
    exceeds_pkg: bool
    exceeds_comp: bool
    is_satellite: bool
    api_sufficient: bool
    new_deps: tuple[str, ...]
    escalated: tuple[CodeTrigger, ...]


def _is_test_source_path(rel_path: str) -> bool:
    posix = PurePosixPath(rel_path.strip("/"))
    if any(seg in {"tests", "test", "__tests__"} for seg in posix.parts[:-1]):
        return True
    name = posix.name.lower()
    if name.startswith("test_") or name.endswith(("_test.py", "_test.go", "_test.rs")):
        return True
    return ".test." in name or ".spec." in name


def _module_slot_for_path(file_path: str, existing_by_path: dict[str, InspectedUnit]) -> str:
    existing = existing_by_path.get(file_path)
    if existing is not None:
        lang = SupportedLanguage.PYTHON if isinstance(existing, SourceUnit) else existing.language
        ident = existing.module_name if existing.module_name else existing.file_path
        return f"{lang}:{ident}"
    lang = detect_language_for_path(file_path) or SupportedLanguage.PYTHON
    if lang == SupportedLanguage.GO:
        parent = PurePosixPath(file_path).parent.as_posix()
        return f"go:{parent}"
    return f"{lang}:{file_path}"


def _project_floorplates(
    prod_units: Sequence[InspectedUnit],
    mandate_prod_paths: Sequence[str],
) -> tuple[int, int, int]:
    existing_by_path = {u.file_path: u for u in prod_units}
    existing_slots = {_module_slot_for_path(u.file_path, existing_by_path) for u in prod_units}
    all_paths = sorted({u.file_path for u in prod_units} | set(mandate_prod_paths))

    pkg_slots: dict[str, set[str]] = {}
    comp_slots: dict[str, set[str]] = {}
    for fp in all_paths:
        pl = classify_unit_placement(fp)
        slot = _module_slot_for_path(fp, existing_by_path)
        pkg_slots.setdefault(pl.package_key, set()).add(slot)
        if pl.compartment_key:
            comp_slots.setdefault(pl.compartment_key, set()).add(slot)

    touched_pkgs = {classify_unit_placement(fp).package_key for fp in mandate_prod_paths}
    touched_comps = {
        pl.compartment_key
        for fp in mandate_prod_paths
        if (pl := classify_unit_placement(fp)).compartment_key
    }
    proj_pkg = max(
        (len(pkg_slots.get(pk, set())) for pk in touched_pkgs),
        default=max((len(v) for v in pkg_slots.values()), default=0),
    )
    proj_comp = max((len(comp_slots.get(ck, set())) for ck in touched_comps), default=0)
    return len(existing_slots), proj_pkg, proj_comp


def _is_non_dunder_private(name: str) -> bool:
    is_dunder = name.startswith("__") and name.endswith("__")
    return name.startswith("_") and not is_dunder


def _unit_has_cross_private_imports(unit: InspectedUnit) -> bool:
    if not isinstance(unit, SourceUnit):
        return any(bool(imp.private_symbols) for imp in unit.imports)
    import_froms = [n for n in ast.walk(unit.tree) if isinstance(n, ast.ImportFrom)]
    return any(_is_non_dunder_private(a.name) for n in import_froms for a in n.names)


def _count_direct_external_importers(
    prod_units: Sequence[InspectedUnit],
    mandate_set: set[str],
) -> int:
    if not mandate_set or not prod_units:
        return 0
    graph = build_workspace_ast_graph(prod_units)
    touched_mods = {
        m.module_name for m in graph.modules if m.file_path in mandate_set and m.module_name
    }
    if not touched_mods:
        return 0
    external = [
        m.file_path
        for m in graph.modules
        if m.file_path not in mandate_set and (set(m.imports) & touched_mods)
    ]
    return len(external)


def _evaluate_attachment_and_api_sufficiency(
    prod_units: Sequence[InspectedUnit],
    mandate_prod_paths: Sequence[str],
) -> tuple[int, bool, bool]:
    comp_summary = analyze_workspace_compartments(prod_units)
    comp_by_key = {c.compartment_key: c for c in comp_summary.compartments}
    mandate_set = set(mandate_prod_paths)
    touched_comps = {
        pl.compartment_key
        for fp in mandate_prod_paths
        if (pl := classify_unit_placement(fp)).compartment_key
    }

    comp_importers = sum(
        comp_by_key[ck].inbound_external_importers for ck in touched_comps if ck in comp_by_key
    )
    direct_importers = _count_direct_external_importers(prod_units, mandate_set)
    inbound_total = max(comp_importers, direct_importers)
    is_satellite = inbound_total == 0

    existing_touched = [u for u in prod_units if u.file_path in mandate_set]
    has_priv_breach = any(_unit_has_cross_private_imports(u) for u in existing_touched)
    api_sufficient = (not has_priv_breach) and (direct_importers == 0)
    return inbound_total, is_satellite, api_sufficient


def _declared_runtime_roots(
    manifest: ManifestDependencies | None,
    polyglot_manifest: PolyglotManifest | None,
) -> set[str]:
    roots: set[str] = set()
    if manifest is not None:
        roots |= {r.lower() for r in manifest.runtime_roots}
    if polyglot_manifest is not None:
        for lang in SupportedLanguage:
            eco = polyglot_manifest.for_language(lang)
            roots |= {r.lower() for r in eco.runtime_roots}
    expanded = set(roots)
    for item in roots:
        expanded.add(re.sub(r"[-.]+", "_", item).lower())
    return expanded


def _compute_net_new_runtime_deps(
    mandate: EngineeringMandate,
    declared_runtime: set[str],
) -> tuple[str, ...]:
    added: list[str] = []
    for dep in mandate.required_dependencies:
        if dep.scope != DependencyScope.RUNTIME:
            continue
        norm_pkg = re.sub(r"[-.]+", "_", dep.package).lower()
        pep_root = normalize_pep508_to_import_root(dep.package).lower()
        imp_root = dep.effective_import_root().lower()
        candidates = {dep.package.lower(), norm_pkg, pep_root, imp_root} - {""}
        if not (candidates & declared_runtime):
            added.append(dep.package)
    return tuple(sorted(set(added)))


def _infer_mandate_triggers(
    mandate: EngineeringMandate,
    existing_units: Sequence[InspectedUnit],
) -> set[CodeTrigger]:
    auth = mandate.authorized_files()
    touched_existing = [u for u in existing_units if u.file_path in auth]
    triggers = set(detect_hazard_triggers(touched_existing))
    if any(_is_test_source_path(p) for p in auth):
        triggers.add(CodeTrigger.VERIFICATION_SUITE)

    combined_prose = " ".join(
        (mandate.title, mandate.objective, *(inv.statement for inv in mandate.invariants))
    )
    if _EXTERNAL_PROSE_RE.search(combined_prose) is not None:
        triggers.add(CodeTrigger.EXTERNAL_BOUNDARY)
    if _SENSITIVE_PROSE_RE.search(combined_prose) is not None:
        triggers.add(CodeTrigger.SENSITIVE_CONTEXT)

    for dep in mandate.required_dependencies:
        if dep.scope != DependencyScope.RUNTIME:
            continue
        candidates = {
            re.sub(r"[-.]+", "_", dep.package).lower(),
            dep.effective_import_root().lower(),
        }
        if candidates & _EXTERNAL_DEP_ROOTS:
            triggers.add(CodeTrigger.EXTERNAL_BOUNDARY)
        if candidates & _SENSITIVE_DEP_ROOTS:
            triggers.add(CodeTrigger.SENSITIVE_CONTEXT)
    return triggers


def _decide_disposition_and_rationale(sig: _ShearSignals) -> tuple[LotLineDisposition, str]:
    if sig.exceeds_pkg:
        return (
            LotLineDisposition.LOT_LINE_SPLIT,
            "Projected package module count exceeds JBC-COG-006 package floorplate ceiling; "
            "extract a separate workspace package or standalone repository.",
        )
    is_established_satellite = (
        sig.existing_prod >= _MIN_ESTABLISHED_PACKAGE_MODULES
        and sig.is_satellite
        and sig.api_sufficient
    )
    if is_established_satellite and sig.new_deps and sig.escalated:
        return (
            LotLineDisposition.LOT_LINE_SPLIT,
            f"Zero-fan-in satellite introduces net-new runtime dependencies {list(sig.new_deps)} "
            f"and escalates hazard triggers {[str(t) for t in sig.escalated]}; "
            "split across a dedicated package/repository lot line.",
        )
    if sig.exceeds_comp:
        return (
            LotLineDisposition.WORKSPACE_COMPARTMENT,
            "Projected compartment module count exceeds JBC-COG-006 compartment ceiling; "
            "partition into a new subpackage compartment.",
        )
    if is_established_satellite and (sig.new_deps or sig.escalated):
        return (
            LotLineDisposition.WORKSPACE_COMPARTMENT,
            f"Zero-fan-in consumer capability introduces CRP dependency/trigger shear "
            f"(deps={list(sig.new_deps)}, triggers={[str(t) for t in sig.escalated]}); "
            "isolate in a dedicated workspace compartment or member wheel.",
        )
    return (
        LotLineDisposition.IN_PACKAGE,
        "Mandate fits within JBC-COG-006 floorplate ceilings without uncontained satellite shear.",
    )


@jezz_callable
def assess_mandate_lot_line(
    mandate: EngineeringMandate,
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
    *,
    manifest: ManifestDependencies | None = None,
    polyglot_manifest: PolyglotManifest | None = None,
    profile: PermitProfile = PermitProfile.STANDARD,
) -> LotLineAssessment:
    """Assess floorplate capacity, API sufficiency, and CRP shear for ``mandate``."""
    limits: CompartmentLimits = resolve_compartment_limits(profile)
    prod_units = tuple(u for u in units if not u.is_test_file)
    mandate_prod_paths = sorted(
        p
        for p in mandate.authorized_files()
        if is_supported_source_path(p) and not _is_test_source_path(p)
    )
    existing_prod, proj_pkg, proj_comp = _project_floorplates(prod_units, mandate_prod_paths)
    exceeds_pkg = proj_pkg > limits.max_package_modules
    exceeds_comp = proj_comp > limits.max_compartment_modules

    inbound_importers, is_satellite, api_sufficient = _evaluate_attachment_and_api_sufficiency(
        prod_units, mandate_prod_paths
    )
    declared_runtime = _declared_runtime_roots(manifest, polyglot_manifest)
    new_deps = _compute_net_new_runtime_deps(mandate, declared_runtime)

    baseline_set = set(detect_hazard_triggers(prod_units))
    mandate_set = _infer_mandate_triggers(mandate, units)
    escalated = tuple(
        sorted(
            (mandate_set - baseline_set) - {CodeTrigger.UNIVERSAL, CodeTrigger.VERIFICATION_SUITE},
            key=str,
        )
    )
    disposition, rationale = _decide_disposition_and_rationale(
        _ShearSignals(
            existing_prod=existing_prod,
            exceeds_pkg=exceeds_pkg,
            exceeds_comp=exceeds_comp,
            is_satellite=is_satellite,
            api_sufficient=api_sufficient,
            new_deps=new_deps,
            escalated=escalated,
        )
    )
    return LotLineAssessment(
        mandate_id=mandate.mandate_id,
        disposition=disposition,
        existing_prod_modules=existing_prod,
        projected_package_modules=proj_pkg,
        max_package_modules=limits.max_package_modules,
        exceeds_package_ceiling=exceeds_pkg,
        projected_compartment_modules=proj_comp,
        max_compartment_modules=limits.max_compartment_modules,
        exceeds_compartment_ceiling=exceeds_comp,
        inbound_core_importers=inbound_importers,
        is_zero_fan_in_satellite=is_satellite,
        public_api_sufficient=api_sufficient,
        net_new_runtime_deps=new_deps,
        baseline_triggers=tuple(sorted(baseline_set, key=str)),
        mandate_triggers=tuple(sorted(mandate_set, key=str)),
        escalated_triggers=escalated,
        rationale=rationale[:512],
    )


__all__ = [
    "LotLineAssessment",
    "LotLineDisposition",
    "assess_mandate_lot_line",
]
