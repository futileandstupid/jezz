"""Pillar 3 (PCE): Closed schemas and seal validators for Phase 0/I ProjectCharters."""

import hashlib
import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import BaseModel, Field, model_validator

from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.codes.taxonomy import CodeTrigger
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    IdentifierStr,
    NonNegativeInt,
    ShortText,
    jezz_callable,
)
from jezz.methodology.lot_line import LotLineAssessment
from jezz.methodology.mandate import EngineeringMandate, validate_engineering_prose

AUTHORIZED_URI_SCHEMES: Final[frozenset[str]] = frozenset(
    {"env", "pass", "op", "keychain", "caller_injected"}
)


class ElicitationAxis(StrEnum):
    """Three mandatory project-level intake axes required prior to sealing a ProjectCharter."""

    DOMAIN_ONTOLOGY = "domain_ontology"
    HAZARD_MUTATION_POLICY = "hazard_mutation_policy"
    SCOPE_AND_EXCLUSIONS = "scope_and_exclusions"


REQUIRED_ELICITATION_AXES: Final[tuple[ElicitationAxis, ...]] = (
    ElicitationAxis.DOMAIN_ONTOLOGY,
    ElicitationAxis.HAZARD_MUTATION_POLICY,
    ElicitationAxis.SCOPE_AND_EXCLUSIONS,
)


class MutationPosture(StrEnum):
    """Declared external mutation posture governing irreversible side-effect boundaries."""

    LOCAL_WORKSPACE_ONLY = "local_workspace_only"
    READ_ONLY = "read_only"
    DRY_RUN_PLAN_APPLY = "dry_run_plan_apply"
    TRANSACTIONAL_ROLLBACK = "transactional_rollback"


def _sha256_canonical_json(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@jezz_callable
def compute_reality_base_digest(
    manifests: Annotated[Sequence[IdentifierStr], Field(max_length=16)],
    languages: Annotated[Sequence[SupportedLanguage], Field(max_length=8)],
    existing_prod: NonNegativeInt,
    declared_deps: Annotated[Sequence[IdentifierStr], Field(max_length=512)],
    avail_bins: Annotated[Sequence[IdentifierStr], Field(max_length=64)],
    miss_bins: Annotated[Sequence[IdentifierStr], Field(max_length=64)],
) -> IdentifierStr:
    """Compute canonical intermediate SHA-256 digest over workspace and binary observations."""
    return _sha256_canonical_json(
        {
            "manifests": list(manifests),
            "languages": [str(lang) for lang in languages],
            "existing_prod": existing_prod,
            "declared_deps": list(declared_deps),
            "avail_bins": list(avail_bins),
            "miss_bins": list(miss_bins),
        }
    )


@jezz_callable
def compute_reality_probe_digest(
    base_digest: IdentifierStr,
    verified_pkgs: Annotated[Sequence[IdentifierStr], Field(max_length=64)],
    missing_pkgs: Annotated[Sequence[IdentifierStr], Field(max_length=64)],
) -> IdentifierStr:
    """Finalize canonical SHA-256 ``evidence_digest`` for ``RealityProbeEvidence``."""
    return _sha256_canonical_json(
        {
            "base": base_digest,
            "verified_pkgs": list(verified_pkgs),
            "missing_pkgs": list(missing_pkgs),
        }
    )


class RealityProbeEvidence(BaseModel):
    """Empirical environment, CLI, and package observation sealed prior to domain interview."""

    model_config = STRICT_FROZEN_CONFIG

    detected_manifests: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=16),
    ] = ()
    detected_languages: Annotated[
        tuple[SupportedLanguage, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=8),
    ] = ()
    existing_prod_modules: NonNegativeInt = 0
    declared_dependencies: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=512),
    ] = ()
    available_binaries: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    missing_binaries: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    verified_packages: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    missing_packages: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    evidence_digest: Annotated[
        str,
        Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"),
    ]

    @model_validator(mode="after")
    def _verify_evidence_digest(self) -> Self:
        base = compute_reality_base_digest(
            self.detected_manifests,
            self.detected_languages,
            self.existing_prod_modules,
            self.declared_dependencies,
            self.available_binaries,
            self.missing_binaries,
        )
        expected = compute_reality_probe_digest(base, self.verified_packages, self.missing_packages)
        if self.evidence_digest != expected:
            msg = (
                f"RealityProbeEvidence digest mismatch: got '{self.evidence_digest}', "
                f"expected '{expected}'."
            )
            raise ValueError(msg)
        return self


class AxisQuestion(BaseModel):
    """Single targeted Phase 0/I elicitation question bound to an ElicitationAxis."""

    model_config = STRICT_FROZEN_CONFIG

    question_id: Annotated[
        str,
        Field(min_length=5, max_length=32, pattern=r"^Q-[A-Z0-9_-]+$"),
    ]
    axis: ElicitationAxis
    prompt: ShortText
    rationale: ShortText


class AxisResolution(BaseModel):
    """Normative engineering decision resolving a required ElicitationAxis."""

    model_config = STRICT_FROZEN_CONFIG

    axis: ElicitationAxis
    summary: Annotated[str, Field(min_length=8, max_length=512)]
    decided_constraints: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=32),
    ]

    @model_validator(mode="after")
    def _validate_resolution_prose(self) -> Self:
        if not self.summary.strip():
            msg = f"AxisResolution '{self.axis}' summary cannot be blank."
            raise ValueError(msg)
        validate_engineering_prose(self.summary, field_label=f"AxisResolution({self.axis}).summary")
        for item in self.decided_constraints:
            if not item.strip():
                msg = f"AxisResolution '{self.axis}' contains a blank constraint."
                raise ValueError(msg)
            validate_engineering_prose(
                item,
                field_label=f"AxisResolution({self.axis}).decided_constraint",
            )
        return self


class HazardMutationPolicy(BaseModel):
    """Explicit URI-scheme routing, dry-run/rollback, and timeout posture for external hazards."""

    model_config = STRICT_FROZEN_CONFIG

    mutation_posture: MutationPosture
    allowed_uri_schemes: Annotated[
        tuple[IdentifierStr, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=8),
    ] = ()
    requires_rollback_journal: bool = False
    rate_or_timeout_bound_sec: Annotated[int, Field(ge=1, le=3600)] | None = None

    @model_validator(mode="after")
    def _validate_schemes(self) -> Self:
        seen: set[str] = set()
        for scheme in self.allowed_uri_schemes:
            if scheme not in AUTHORIZED_URI_SCHEMES:
                msg = (
                    f"Unsupported secret URI scheme '{scheme}'; allowed: "
                    f"{sorted(AUTHORIZED_URI_SCHEMES)}."
                )
                raise ValueError(msg)
            if scheme in seen:
                msg = f"Duplicate secret URI scheme '{scheme}' in HazardMutationPolicy."
                raise ValueError(msg)
            seen.add(scheme)
        return self


class CharterIntakeSession(BaseModel):
    """Phase 0/I project intake state pairing empirical reality probe with 3-axis questions."""

    model_config = STRICT_FROZEN_CONFIG

    charter_id: Annotated[
        str,
        Field(min_length=5, max_length=64, pattern=r"^CHR-[A-Za-z0-9_-]+$"),
    ]
    title: ShortText
    vision: Annotated[str, Field(min_length=12, max_length=65_536)]
    reality_probe: RealityProbeEvidence
    detected_triggers: Annotated[
        tuple[CodeTrigger, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=5),
    ]
    questions: Annotated[
        tuple[AxisQuestion, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=3, max_length=16),
    ]
    recorded_resolutions: Annotated[
        tuple[AxisResolution, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=3),
    ] = ()

    @model_validator(mode="after")
    def _validate_intake_session(self) -> Self:
        validate_engineering_prose(self.title, field_label="CharterIntakeSession.title")
        validate_engineering_prose(self.vision[:512], field_label="CharterIntakeSession.vision")
        if CodeTrigger.UNIVERSAL not in self.detected_triggers:
            msg = "CharterIntakeSession.detected_triggers must include CodeTrigger.UNIVERSAL."
            raise ValueError(msg)
        covered = {q.axis for q in self.questions}
        missing = [ax for ax in REQUIRED_ELICITATION_AXES if ax not in covered]
        if missing:
            msg = f"CharterIntakeSession.questions omits required axis(es): {missing}."
            raise ValueError(msg)
        seen_res: set[ElicitationAxis] = set()
        for res in self.recorded_resolutions:
            if res.axis in seen_res:
                msg = f"Duplicate AxisResolution for axis '{res.axis}'."
                raise ValueError(msg)
            seen_res.add(res.axis)
        return self

    @property
    def unresolved_axes(self) -> tuple[ElicitationAxis, ...]:
        """Return required elicitation axes not yet answered in ``recorded_resolutions``."""
        answered = {r.axis for r in self.recorded_resolutions}
        return tuple(ax for ax in REQUIRED_ELICITATION_AXES if ax not in answered)

    @property
    def is_ready_to_seal(self) -> bool:
        """Return True iff all three required elicitation axes have recorded resolutions."""
        return len(self.unresolved_axes) == 0

    @jezz_callable
    def with_resolutions(
        self,
        resolutions: Annotated[Sequence[AxisResolution], Field(max_length=3)],
    ) -> CharterIntakeSession:
        """Return an updated immutable ``CharterIntakeSession`` merging ``resolutions``."""
        by_axis = {r.axis: r for r in self.recorded_resolutions}
        for item in resolutions:
            by_axis[item.axis] = item
        ordered = tuple(by_axis[ax] for ax in REQUIRED_ELICITATION_AXES if ax in by_axis)
        return self.model_copy(update={"recorded_resolutions": ordered})

    @jezz_callable
    def to_markdown(self) -> BoundedText:
        """Render a concise Markdown summary of the intake session and open questions."""
        triggers_str = ", ".join(f"`{t}`" for t in self.detected_triggers)
        lines = [
            f"# PCE Project Intake — `{self.charter_id}` ({self.title})",
            f"- **Reality Probe Digest:** `{self.reality_probe.evidence_digest}`",
            f"- **Detected Hazard Triggers:** {triggers_str}",
            f"- **Unresolved Axes:** `{[str(a) for a in self.unresolved_axes]}`",
            "",
            "## 3-Axis Elicitation Questions",
        ]
        for q in self.questions:
            lines.append(f"- **`{q.question_id}` [`{q.axis}`]:** {q.prompt} *({q.rationale})*")
        return "\n".join(lines)[:65_536]


class MandateDependency(BaseModel):
    """Directed inter-permit prerequisite (`mandate_id` executes after `depends_on`)."""

    model_config = STRICT_FROZEN_CONFIG

    mandate_id: Annotated[
        str,
        Field(min_length=5, max_length=64, pattern=r"^MND-[A-Za-z0-9_-]+$"),
    ]
    depends_on: Annotated[
        str,
        Field(min_length=5, max_length=64, pattern=r"^MND-[A-Za-z0-9_-]+$"),
    ]

    @model_validator(mode="after")
    def _reject_self_edge(self) -> Self:
        if self.mandate_id == self.depends_on:
            msg = f"MandateDependency '{self.mandate_id}' cannot depend on itself."
            raise ValueError(msg)
        return self


class CharterSealInput(BaseModel):
    """Closed request payload for ``seal_project_charter``."""

    model_config = STRICT_FROZEN_CONFIG

    intake: CharterIntakeSession
    resolutions: Annotated[
        tuple[AxisResolution, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=8),
    ] = ()
    hazard_policy: HazardMutationPolicy
    scope_exclusions: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    mandates: Annotated[
        tuple[EngineeringMandate, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=16),
    ] = ()
    mandate_dependencies: Annotated[
        tuple[MandateDependency, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    package_slug: IdentifierStr | None = None


@jezz_callable
def validate_canonical_resolutions(
    resolutions: Annotated[Sequence[AxisResolution], Field(max_length=8)],
) -> tuple[AxisResolution, ...]:
    """Verify exact 3-axis coverage and return canonically ordered ``AxisResolution``s."""
    by_axis: dict[ElicitationAxis, AxisResolution] = {}
    for res in resolutions:
        if res.axis in by_axis:
            msg = f"Duplicate AxisResolution supplied for '{res.axis}'."
            raise ValueError(msg)
        by_axis[res.axis] = res
    missing = [str(ax) for ax in REQUIRED_ELICITATION_AXES if ax not in by_axis]
    if missing:
        msg = (
            f"ProjectCharter cannot seal with unresolved elicitation axis(es): {missing}; "
            "all three axes (domain_ontology, hazard_mutation_policy, scope_and_exclusions) "
            "are mandatory."
        )
        raise ValueError(msg)
    return tuple(by_axis[ax] for ax in REQUIRED_ELICITATION_AXES)


@jezz_callable
def validate_hazard_policy_against_triggers(
    policy: HazardMutationPolicy,
    triggers: Annotated[Sequence[CodeTrigger], Field(min_length=1, max_length=5)],
) -> None:
    """Enforce external mutation posture, timeout, rollback, and URI scheme obligations."""
    trig_set = set(triggers)
    if CodeTrigger.EXTERNAL_BOUNDARY in trig_set:
        if policy.mutation_posture == MutationPosture.LOCAL_WORKSPACE_ONLY:
            msg = (
                "EXTERNAL_BOUNDARY trigger is active; HazardMutationPolicy.mutation_posture "
                "cannot be 'local_workspace_only' (declare 'read_only', "
                "'dry_run_plan_apply', or 'transactional_rollback')."
            )
            raise ValueError(msg)
        if (
            policy.mutation_posture != MutationPosture.READ_ONLY
            and policy.rate_or_timeout_bound_sec is None
        ):
            msg = (
                "Mutating EXTERNAL_BOUNDARY posture requires an explicit "
                "rate_or_timeout_bound_sec (1..3600)."
            )
            raise ValueError(msg)
    if (
        policy.mutation_posture == MutationPosture.TRANSACTIONAL_ROLLBACK
        and not policy.requires_rollback_journal
    ):
        msg = "MutationPosture 'transactional_rollback' requires requires_rollback_journal=True."
        raise ValueError(msg)
    if CodeTrigger.SENSITIVE_CONTEXT in trig_set and not policy.allowed_uri_schemes:
        msg = (
            "SENSITIVE_CONTEXT trigger is active; HazardMutationPolicy.allowed_uri_schemes "
            "must declare at least one authorized scheme (env, pass, op, keychain, "
            "or caller_injected)."
        )
        raise ValueError(msg)


def _validate_topological_precedence(
    index_by_id: dict[str, int],
    dependencies: Sequence[MandateDependency],
) -> None:
    seen_edges: set[tuple[str, str]] = set()
    for dep in dependencies:
        edge = (dep.mandate_id, dep.depends_on)
        if edge in seen_edges:
            msg = f"Duplicate MandateDependency '{dep.depends_on} -> {dep.mandate_id}'."
            raise ValueError(msg)
        seen_edges.add(edge)
        src_idx = index_by_id.get(dep.depends_on)
        dst_idx = index_by_id.get(dep.mandate_id)
        if src_idx is None or dst_idx is None:
            msg = (
                f"MandateDependency '{dep.depends_on} -> {dep.mandate_id}' references "
                "mandate_id outside mandate_queue."
            )
            raise ValueError(msg)
        if src_idx >= dst_idx:
            msg = (
                f"Topological order violation: '{dep.depends_on}' (index {src_idx}) must "
                f"precede '{dep.mandate_id}' (index {dst_idx}) in mandate_queue."
            )
            raise ValueError(msg)


def _validate_mandate_queue_invariants(
    mandate_queue: Sequence[EngineeringMandate],
    dependencies: Sequence[MandateDependency],
    triggers: Sequence[CodeTrigger],
    policy: HazardMutationPolicy,
) -> None:
    index_by_id: dict[str, int] = {}
    seen_invs: set[str] = set()
    for idx, mandate in enumerate(mandate_queue):
        if mandate.mandate_id in index_by_id:
            msg = f"Duplicate mandate_id '{mandate.mandate_id}' in ProjectCharter."
            raise ValueError(msg)
        if mandate.requires_elicitation:
            msg = (
                f"Mandate '{mandate.mandate_id}' still has unresolved open_ambiguities; "
                "resolve all ambiguities prior to sealing ProjectCharter."
            )
            raise ValueError(msg)
        index_by_id[mandate.mandate_id] = idx
        for inv in mandate.invariants:
            if inv.invariant_id in seen_invs:
                msg = f"Duplicate invariant_id '{inv.invariant_id}' across charter mandates."
                raise ValueError(msg)
            seen_invs.add(inv.invariant_id)

    _validate_topological_precedence(index_by_id, dependencies)
    is_external_mutating = CodeTrigger.EXTERNAL_BOUNDARY in set(
        triggers
    ) and policy.mutation_posture in {
        MutationPosture.DRY_RUN_PLAN_APPLY,
        MutationPosture.TRANSACTIONAL_ROLLBACK,
    }
    if is_external_mutating and (len(mandate_queue) < 2 or not dependencies):
        msg = (
            "External mutating ProjectCharter requires at least 2 phased EngineeringMandates "
            "linked by MandateDependency so read-only modeling/invariants precede mutation."
        )
        raise ValueError(msg)


@jezz_callable
def build_charter_seal_base(
    charter_id: IdentifierStr,
    reality_digest: IdentifierStr,
    triggers: Annotated[Sequence[CodeTrigger], Field(min_length=1, max_length=5)],
    resolutions: Annotated[Sequence[AxisResolution], Field(min_length=3, max_length=3)],
    policy: HazardMutationPolicy,
    mandates: Annotated[Sequence[EngineeringMandate], Field(min_length=1, max_length=16)],
) -> BoundedText:
    """Serialize the core structural fields of a ProjectCharter into a canonical digest."""
    return _sha256_canonical_json(
        {
            "charter_id": charter_id,
            "reality_digest": reality_digest,
            "triggers": [str(t) for t in triggers],
            "resolutions": [r.model_dump(mode="json") for r in resolutions],
            "hazard_policy": policy.model_dump(mode="json"),
            "mandates": [m.model_dump(mode="json") for m in mandates],
        }
    )


@jezz_callable
def finalize_charter_seal(
    core_digest: IdentifierStr,
    title: ShortText,
    vision: BoundedText,
    exclusions: Annotated[Sequence[ShortText], Field(min_length=1, max_length=64)],
    dependencies: Annotated[Sequence[MandateDependency], Field(max_length=64)],
) -> IdentifierStr:
    """Compute final SHA-256 ``charter_seal`` over core digest, prose, exclusions, and DAG."""
    return _sha256_canonical_json(
        {
            "core_digest": core_digest,
            "title": title,
            "vision": vision,
            "exclusions": list(exclusions),
            "dependencies": [d.model_dump(mode="json") for d in dependencies],
        }
    )


class ProjectCharter(BaseModel):
    """Cryptographically sealed Phase 0/I charter governing phased PCE EngineeringMandates."""

    model_config = STRICT_FROZEN_CONFIG

    charter_id: Annotated[
        str,
        Field(min_length=5, max_length=64, pattern=r"^CHR-[A-Za-z0-9_-]+$"),
    ]
    title: ShortText
    vision: Annotated[str, Field(min_length=12, max_length=65_536)]
    reality_probe: RealityProbeEvidence
    detected_triggers: Annotated[
        tuple[CodeTrigger, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=5),
    ]
    resolutions: Annotated[
        tuple[AxisResolution, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=3, max_length=3),
    ]
    hazard_policy: HazardMutationPolicy
    scope_exclusions: Annotated[
        tuple[ShortText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=64),
    ]
    mandate_queue: Annotated[
        tuple[EngineeringMandate, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=16),
    ]
    mandate_dependencies: Annotated[
        tuple[MandateDependency, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    lot_line_assessments: Annotated[
        tuple[LotLineAssessment, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(min_length=1, max_length=16),
    ]
    charter_seal: Annotated[
        str,
        Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"),
    ]

    @model_validator(mode="after")
    def _validate_charter_integrity(self) -> Self:
        validate_engineering_prose(self.title, field_label="ProjectCharter.title")
        validate_engineering_prose(self.vision[:512], field_label="ProjectCharter.vision")
        for excl in self.scope_exclusions:
            validate_engineering_prose(excl, field_label="ProjectCharter.scope_exclusion")
        canonical_res = validate_canonical_resolutions(self.resolutions)
        if tuple(r.axis for r in self.resolutions) != tuple(r.axis for r in canonical_res):
            msg = (
                "ProjectCharter.resolutions must follow canonical REQUIRED_ELICITATION_AXES order."
            )
            raise ValueError(msg)
        validate_hazard_policy_against_triggers(self.hazard_policy, self.detected_triggers)
        _validate_mandate_queue_invariants(
            self.mandate_queue,
            self.mandate_dependencies,
            self.detected_triggers,
            self.hazard_policy,
        )
        if [a.mandate_id for a in self.lot_line_assessments] != [
            m.mandate_id for m in self.mandate_queue
        ]:
            msg = "ProjectCharter.lot_line_assessments must align 1:1 with mandate_queue."
            raise ValueError(msg)
        core = build_charter_seal_base(
            self.charter_id,
            self.reality_probe.evidence_digest,
            self.detected_triggers,
            self.resolutions,
            self.hazard_policy,
            self.mandate_queue,
        )
        expected = finalize_charter_seal(
            core,
            self.title,
            self.vision,
            self.scope_exclusions,
            self.mandate_dependencies,
        )
        if self.charter_seal != expected:
            msg = f"ProjectCharter seal mismatch: got '{self.charter_seal}', expected '{expected}'."
            raise ValueError(msg)
        return self

    @jezz_callable
    def to_markdown(self) -> BoundedText:
        """Render a concise Markdown summary of the sealed ProjectCharter and permit queue."""
        lines = [
            f"# Sealed PCE ProjectCharter — `{self.charter_id}` ({self.title})",
            f"- **Charter Seal:** `{self.charter_seal}`",
            f"- **Reality Probe Digest:** `{self.reality_probe.evidence_digest}`",
            f"- **Mutation Posture:** `{self.hazard_policy.mutation_posture}`",
            f"- **Queued Permits:** `{len(self.mandate_queue)}`",
            "",
            "## Phased Permit Queue",
        ]
        for idx, (mandate, lot) in enumerate(
            zip(self.mandate_queue, self.lot_line_assessments, strict=True),
            start=1,
        ):
            lines.append(
                f"{idx}. **`{mandate.mandate_id}`** (`{lot.disposition}`): {mandate.title}"
            )
        return "\n".join(lines)[:65_536]


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
    "build_charter_seal_base",
    "compute_reality_base_digest",
    "compute_reality_probe_digest",
    "finalize_charter_seal",
    "validate_canonical_resolutions",
    "validate_hazard_policy_against_triggers",
]
