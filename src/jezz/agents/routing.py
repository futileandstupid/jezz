"""Multi-model specialist routing across [tool.jezz.models], CLI tiers, and PceModelSuite."""

import re
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final, Self

import icontract
from pydantic import BaseModel, BeforeValidator, Field, model_validator

from jezz.agents.specialists import GuardedModel, PceModelSuite
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    jezz_callable,
)
from jezz.secrets import canonical_provider_family, read_pyproject_jezz_table

_CREDENTIAL_LIKE_SPEC_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:pass://|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,})"
)


class SpecialistRoleSlot(StrEnum):
    """Canonical generative specialist slots composing ``PceModelSuite``."""

    FRAME = "frame"
    DECOMPOSE = "decompose"
    INSPECT = "inspect"
    SPECIFY = "specify"
    INSTRUMENT = "instrument"
    SYNTHESIZE = "synthesize"
    REFACTOR = "refactor"
    HYPOTHESIZE = "hypothesize"


class SpecialistTier(StrEnum):
    """Coarse capability tiers grouping PCE specialist roles."""

    ARCHITECT = "architect"
    BUILDER = "builder"
    INSPECTOR = "inspector"


ROLE_TIER_MAP: Final[Mapping[SpecialistRoleSlot, SpecialistTier]] = MappingProxyType(
    {
        SpecialistRoleSlot.FRAME: SpecialistTier.ARCHITECT,
        SpecialistRoleSlot.DECOMPOSE: SpecialistTier.ARCHITECT,
        SpecialistRoleSlot.SPECIFY: SpecialistTier.ARCHITECT,
        SpecialistRoleSlot.HYPOTHESIZE: SpecialistTier.ARCHITECT,
        SpecialistRoleSlot.INSTRUMENT: SpecialistTier.BUILDER,
        SpecialistRoleSlot.SYNTHESIZE: SpecialistTier.BUILDER,
        SpecialistRoleSlot.REFACTOR: SpecialistTier.BUILDER,
        SpecialistRoleSlot.INSPECT: SpecialistTier.INSPECTOR,
    }
)

type ModelInstanceFactory = Callable[[IdentifierStr], GuardedModel]


def _has_control_or_space(value: str) -> bool:
    if value != value.strip():
        return True
    return any(ord(ch) <= 32 or ord(ch) == 127 for ch in value)


def _validate_model_spec_text(raw_spec: str, field_label: str) -> None:
    if _has_control_or_space(raw_spec):
        msg = (
            f"Invalid model identifier '{raw_spec}' in '{field_label}'; "
            "whitespace and control characters are prohibited."
        )
        raise ValueError(msg)
    if _CREDENTIAL_LIKE_SPEC_RE.match(raw_spec):
        msg = (
            f"Refusing credential or 'pass://' URI in model slot '{field_label}'; "
            "use --api-key-uri or [tool.jezz.secrets.providers] for API keys."
        )
        raise ValueError(msg)


def _coerce_role_map_keys(raw_value: object) -> object:
    if not isinstance(raw_value, dict):
        return raw_value
    valid_values = {slot.value: slot for slot in SpecialistRoleSlot}
    coerced: dict[SpecialistRoleSlot, object] = {}
    for raw_key, val in raw_value.items():
        if isinstance(raw_key, SpecialistRoleSlot):
            coerced[raw_key] = val
            continue
        if isinstance(raw_key, str) and raw_key in valid_values:
            coerced[valid_values[raw_key]] = val
            continue
        allowed = sorted(valid_values)
        msg = (
            f"Unknown specialist role '{raw_key}' in [tool.jezz.models.roles]; allowed: {allowed}."
        )
        raise ValueError(msg)
    return coerced


_ROLE_KEY_VALIDATOR: Final[BeforeValidator] = BeforeValidator(_coerce_role_map_keys)


class JezzModelsConfig(BaseModel):
    """Project-level ``[tool.jezz.models]`` specialist model routing configuration."""

    model_config = STRICT_FROZEN_CONFIG

    default: IdentifierStr | None = None
    architect: IdentifierStr | None = None
    builder: IdentifierStr | None = None
    inspector: IdentifierStr | None = None
    roles: Annotated[
        dict[SpecialistRoleSlot, IdentifierStr],
        _ROLE_KEY_VALIDATOR,
        Field(max_length=16),
    ] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_configured_models(self) -> Self:
        tier_pairs = (
            ("default", self.default),
            ("architect", self.architect),
            ("builder", self.builder),
            ("inspector", self.inspector),
        )
        for label, spec in tier_pairs:
            if spec is not None:
                _validate_model_spec_text(spec, f"tool.jezz.models.{label}")
        for role_slot, spec in self.roles.items():
            _validate_model_spec_text(spec, f"tool.jezz.models.roles.{role_slot.value}")
        return self

    def _tier_model(self, tier: SpecialistTier) -> IdentifierStr | None:
        if tier == SpecialistTier.ARCHITECT:
            return self.architect
        if tier == SpecialistTier.BUILDER:
            return self.builder
        return self.inspector

    @jezz_callable
    def resolve_slot(self, slot: SpecialistRoleSlot) -> IdentifierStr | None:
        """Resolve ``slot`` via ``roles.<slot> -> <tier> -> default`` precedence."""
        role_override = self.roles.get(slot)
        if role_override is not None:
            return role_override
        tier_val = self._tier_model(ROLE_TIER_MAP[slot])
        return tier_val if tier_val is not None else self.default


class CliModelOverrides(BaseModel):
    """Explicit CLI model flags overriding ``[tool.jezz.models]`` project configuration."""

    model_config = STRICT_FROZEN_CONFIG

    model: IdentifierStr | None = None
    architect_model: IdentifierStr | None = None
    builder_model: IdentifierStr | None = None
    inspector_model: IdentifierStr | None = None

    @model_validator(mode="after")
    def _validate_cli_specs(self) -> Self:
        pairs = (
            ("--model", self.model),
            ("--architect-model", self.architect_model),
            ("--builder-model", self.builder_model),
            ("--inspector-model", self.inspector_model),
        )
        for flag_name, spec in pairs:
            if spec is not None:
                _validate_model_spec_text(spec, flag_name)
        return self

    @jezz_callable
    def tier_override_for(self, slot: SpecialistRoleSlot) -> IdentifierStr | None:
        """Return the explicit CLI tier flag governing ``slot``, if provided."""
        tier = ROLE_TIER_MAP[slot]
        if tier == SpecialistTier.ARCHITECT:
            return self.architect_model
        if tier == SpecialistTier.BUILDER:
            return self.builder_model
        return self.inspector_model


class ResolvedModelRouting(BaseModel):
    """Concrete ``provider:model`` assignment across all 8 generative PCE specialist slots."""

    model_config = STRICT_FROZEN_CONFIG

    frame: IdentifierStr
    decompose: IdentifierStr
    inspect: IdentifierStr
    specify: IdentifierStr
    instrument: IdentifierStr
    synthesize: IdentifierStr
    refactor: IdentifierStr
    hypothesize: IdentifierStr

    @model_validator(mode="after")
    def _validate_all_slots(self) -> Self:
        for slot_name, spec in (
            ("frame", self.frame),
            ("decompose", self.decompose),
            ("inspect", self.inspect),
            ("specify", self.specify),
            ("instrument", self.instrument),
            ("synthesize", self.synthesize),
            ("refactor", self.refactor),
            ("hypothesize", self.hypothesize),
        ):
            _validate_model_spec_text(spec, slot_name)
        return self

    @property
    def is_uniform(self) -> bool:
        """Return True iff every generative specialist slot uses the identical model ID."""
        return len(self.unique_model_names()) == 1

    @jezz_callable
    def slot_mapping(
        self,
    ) -> Annotated[dict[SpecialistRoleSlot, IdentifierStr], Field(min_length=8, max_length=8)]:
        """Return the bijective mapping from ``SpecialistRoleSlot`` to model identifier."""
        return {
            SpecialistRoleSlot.FRAME: self.frame,
            SpecialistRoleSlot.DECOMPOSE: self.decompose,
            SpecialistRoleSlot.INSPECT: self.inspect,
            SpecialistRoleSlot.SPECIFY: self.specify,
            SpecialistRoleSlot.INSTRUMENT: self.instrument,
            SpecialistRoleSlot.SYNTHESIZE: self.synthesize,
            SpecialistRoleSlot.REFACTOR: self.refactor,
            SpecialistRoleSlot.HYPOTHESIZE: self.hypothesize,
        }

    @icontract.ensure(lambda result: 1 <= len(result) <= 8)
    @jezz_callable
    def unique_model_names(
        self,
    ) -> Annotated[tuple[IdentifierStr, ...], Field(min_length=1, max_length=8)]:
        """Return deduplicated model identifiers in canonical specialist-slot order."""
        seen: set[str] = set()
        ordered: list[str] = []
        for spec in (
            self.frame,
            self.decompose,
            self.inspect,
            self.specify,
            self.instrument,
            self.synthesize,
            self.refactor,
            self.hypothesize,
        ):
            if spec not in seen:
                seen.add(spec)
                ordered.append(spec)
        return tuple(ordered)

    @jezz_callable
    def unique_provider_families(
        self,
    ) -> Annotated[frozenset[IdentifierStr], Field(max_length=8)]:
        """Return distinct canonical provider families requiring API credentials."""
        families: set[str] = set()
        for model_name in self.unique_model_names():
            fam = canonical_provider_family(model_name)
            if fam:
                families.add(fam)
        return frozenset(families)

    @jezz_callable
    def summary_label(self) -> IdentifierStr:
        """Render a bounded human-readable summary label for reports and benchmarks."""
        if self.is_uniform:
            return self.synthesize
        arch_uniform = self.frame == self.decompose == self.specify == self.hypothesize
        build_uniform = self.instrument == self.synthesize == self.refactor
        if arch_uniform and build_uniform:
            raw = f"tiered(arch={self.frame}, build={self.synthesize}, inspect={self.inspect})"
            return raw[:256]
        joined = ",".join(self.unique_model_names())
        return f"multi({joined})"[:256]

    @jezz_callable
    def build_suite(
        self,
        factory: ModelInstanceFactory,
    ) -> Annotated[PceModelSuite, InstanceGuard(PceModelSuite)]:
        """Instantiate each distinct model once via ``factory`` and bind ``PceModelSuite``."""
        instances = {name: factory(name) for name in self.unique_model_names()}
        return PceModelSuite(
            frame_model=instances[self.frame],
            inspect_model=instances[self.inspect],
            specify_model=instances[self.specify],
            instrument_model=instances[self.instrument],
            synthesize_model=instances[self.synthesize],
            hypothesize_model=instances[self.hypothesize],
            refactor_model=instances[self.refactor],
            decompose_model=instances[self.decompose],
        )


@jezz_callable
def load_pyproject_models_config(pyproject_target: ContainedPath) -> JezzModelsConfig | None:
    """Load ``[tool.jezz.models]`` from ``pyproject_target``, or return ``None`` if absent."""
    models_tbl = read_pyproject_jezz_table(pyproject_target, "models")
    return JezzModelsConfig.model_validate(models_tbl) if models_tbl is not None else None


@jezz_callable
def discover_models_config(
    search_roots: Annotated[Sequence[Path], Field(min_length=1, max_length=16)],
) -> JezzModelsConfig | None:
    """Search ``pyproject.toml`` files across ``search_roots`` for ``[tool.jezz.models]``."""
    seen: set[Path] = set()
    for raw_root in search_roots:
        resolved = raw_root.resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        pyproject = ContainedPath.from_parts(resolved, "pyproject.toml")
        if not pyproject.resolved_path.is_file():
            continue
        cfg = load_pyproject_models_config(pyproject)
        if cfg is not None:
            return cfg
    return None


def _resolve_single_slot(
    slot: SpecialistRoleSlot,
    cli_overrides: CliModelOverrides,
    project_config: JezzModelsConfig | None,
) -> str | None:
    cli_tier = cli_overrides.tier_override_for(slot)
    if cli_tier is not None:
        return cli_tier
    if cli_overrides.model is not None:
        return cli_overrides.model
    if project_config is not None:
        return project_config.resolve_slot(slot)
    return None


@jezz_callable
def resolve_specialist_routing(
    cli_overrides: CliModelOverrides,
    *,
    project_config: JezzModelsConfig | None = None,
) -> ResolvedModelRouting:
    """Resolve all 7 specialist slots across CLI flags and ``[tool.jezz.models]``."""
    resolved_by_slot: dict[SpecialistRoleSlot, str] = {}
    missing_slots: list[str] = []
    for slot in SpecialistRoleSlot:
        chosen = _resolve_single_slot(slot, cli_overrides, project_config)
        if chosen is None:
            missing_slots.append(slot.value)
            continue
        resolved_by_slot[slot] = chosen
    if missing_slots:
        msg = (
            f"No model configured for specialist role(s) {missing_slots}; "
            "pass --model or configure [tool.jezz.models] in pyproject.toml."
        )
        raise ValueError(msg)
    return ResolvedModelRouting(
        frame=resolved_by_slot[SpecialistRoleSlot.FRAME],
        decompose=resolved_by_slot[SpecialistRoleSlot.DECOMPOSE],
        inspect=resolved_by_slot[SpecialistRoleSlot.INSPECT],
        specify=resolved_by_slot[SpecialistRoleSlot.SPECIFY],
        instrument=resolved_by_slot[SpecialistRoleSlot.INSTRUMENT],
        synthesize=resolved_by_slot[SpecialistRoleSlot.SYNTHESIZE],
        refactor=resolved_by_slot[SpecialistRoleSlot.REFACTOR],
        hypothesize=resolved_by_slot[SpecialistRoleSlot.HYPOTHESIZE],
    )
