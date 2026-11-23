"""Prompt-caching configuration, breakpoint partitioning, and billed-token accounting."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final, Literal

import icontract
from pydantic import BeforeValidator, Field
from pydantic_ai.messages import CachePoint
from pydantic_ai.models import Model
from pydantic_ai.settings import CacheConfig, ModelSettings

from jezz.guards import (
    BoundedText,
    InstanceGuard,
    NonEmptyStr,
    NonNegativeInt,
    jezz_callable,
)

CACHE_READ_DISCOUNT_MULTIPLIER: Final[float] = 0.10
CACHE_WRITE_SURCHARGE_MULTIPLIER: Final[float] = 1.25

type BoundedPromptText = Annotated[str, Field(min_length=1, max_length=65_536)]
type GuardedCachePoint = Annotated[CachePoint, InstanceGuard(CachePoint)]
type GuardedModelSettings = Annotated[ModelSettings, InstanceGuard(dict)]
type GuardedCacheModel = Annotated[Model, InstanceGuard(Model)]
type CachedUserPrompt = BoundedPromptText | tuple[BoundedPromptText, GuardedCachePoint, NonEmptyStr]


class PromptCacheMode(StrEnum):
    """Cross-provider prompt-caching policy mapped onto ``pydantic_ai.settings.ModelSettings``."""

    AUTO = "auto"
    OFF = "off"
    PREFIX_ONLY = "prefix-only"
    TTL_5M = "5m"
    TTL_30M = "30m"
    TTL_1H = "1h"
    TTL_12H = "12h"


def _coerce_prompt_cache_mode(raw: object) -> object:
    if isinstance(raw, str) and not isinstance(raw, PromptCacheMode):
        return PromptCacheMode(raw)
    return raw


PROMPT_CACHE_MODE_VALIDATOR: Final[BeforeValidator] = BeforeValidator(_coerce_prompt_cache_mode)
type CoercedPromptCacheMode = Annotated[PromptCacheMode, PROMPT_CACHE_MODE_VALIDATOR]


_CACHE_SETTINGS_BY_MODE: Final[Mapping[PromptCacheMode, ModelSettings]] = MappingProxyType(
    {
        PromptCacheMode.AUTO: ModelSettings(cache=True),
        PromptCacheMode.OFF: ModelSettings(cache=False),
        PromptCacheMode.PREFIX_ONLY: ModelSettings(cache=CacheConfig(messages=False)),
        PromptCacheMode.TTL_5M: ModelSettings(cache="5m"),
        PromptCacheMode.TTL_30M: ModelSettings(cache="30m"),
        PromptCacheMode.TTL_1H: ModelSettings(cache="1h"),
        PromptCacheMode.TTL_12H: ModelSettings(cache="12h"),
    }
)

_EXTENDED_TTL_MODES: Final[frozenset[PromptCacheMode]] = frozenset(
    {PromptCacheMode.TTL_1H, PromptCacheMode.TTL_12H}
)


@icontract.ensure(lambda result: "cache" in result)
@jezz_callable
def resolve_cache_model_settings(mode: PromptCacheMode) -> GuardedModelSettings:
    """Return a fresh ``ModelSettings`` dict enacting ``mode`` across providers."""
    template = _CACHE_SETTINGS_BY_MODE[mode]
    return ModelSettings(**template)


@jezz_callable
def cache_point_for_mode(mode: PromptCacheMode) -> GuardedCachePoint | None:
    """Return an inline ``CachePoint`` for conversation-caching modes, or ``None``."""
    if mode in {PromptCacheMode.OFF, PromptCacheMode.PREFIX_ONLY}:
        return None
    ttl_val: Literal["5m", "1h"] = "1h" if mode in _EXTENDED_TTL_MODES else "5m"
    return CachePoint(ttl=ttl_val)


@jezz_callable
def partition_cached_user_prompt(
    shared_prefix: BoundedPromptText,
    trailing_suffix: BoundedText = "",
    *,
    mode: PromptCacheMode = PromptCacheMode.AUTO,
) -> CachedUserPrompt:
    """Insert an inline ``CachePoint`` between ``shared_prefix`` and ``trailing_suffix``."""
    if not trailing_suffix:
        return shared_prefix[:65_536]
    marker = cache_point_for_mode(mode)
    if marker is None:
        return f"{shared_prefix}{trailing_suffix}"[:65_536]
    trimmed_prefix = shared_prefix[:57_344]
    trimmed_suffix = trailing_suffix[:8192]
    return trimmed_prefix, marker, trimmed_suffix


@jezz_callable
def model_supports_inline_cache_points(model: GuardedCacheModel) -> bool:
    """Return True when ``model`` translates inline ``CachePoint`` markers into breakpoints."""
    if model.system in {"function", "test"}:
        return True
    return bool(model.profile.get("supports_cache", False))


@jezz_callable
def normalize_prompt_for_model(
    prompt: CachedUserPrompt,
    model: GuardedCacheModel | None,
) -> CachedUserPrompt:
    """Coalesce unsupported inline ``CachePoint`` tuples into one contiguous text block."""
    if isinstance(prompt, str) or model is None or model_supports_inline_cache_points(model):
        return prompt
    prefix, _marker, suffix = prompt
    return f"{prefix}{suffix}"[:65_536]


@icontract.ensure(lambda result: 0.0 <= result <= 1.0)
@jezz_callable
def compute_cache_hit_ratio(
    input_tokens: NonNegativeInt,
    cache_read_tokens: NonNegativeInt,
) -> Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]:
    """Return normalized ``cache_read_tokens / input_tokens`` clamped to ``[0.0, 1.0]``."""
    if input_tokens == 0:
        return 0.0
    return round(min(1.0, cache_read_tokens / input_tokens), 4)


@icontract.ensure(lambda result: result >= 0.0)
@jezz_callable
def compute_effective_billed_input_tokens(
    input_tokens: NonNegativeInt,
    cache_read_tokens: NonNegativeInt,
    cache_write_tokens: NonNegativeInt = 0,
) -> Annotated[float, Field(ge=0.0, le=1e18, allow_inf_nan=False)]:
    """Convert inclusive ``input_tokens`` into uncached-equivalent billed input tokens."""
    uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    weighted = (
        uncached
        + (CACHE_READ_DISCOUNT_MULTIPLIER * cache_read_tokens)
        + (CACHE_WRITE_SURCHARGE_MULTIPLIER * cache_write_tokens)
    )
    return round(weighted, 2)


@icontract.ensure(lambda result: -2.0 <= result <= 1.0)
@jezz_callable
def compute_input_cost_savings_ratio(
    input_tokens: NonNegativeInt,
    cache_read_tokens: NonNegativeInt,
    cache_write_tokens: NonNegativeInt = 0,
) -> Annotated[float, Field(ge=-2.0, le=1.0, allow_inf_nan=False)]:
    """Compute fractional billed-input reduction relative to uncached ``input_tokens``."""
    if input_tokens == 0:
        return 0.0
    effective = compute_effective_billed_input_tokens(
        input_tokens,
        cache_read_tokens,
        cache_write_tokens,
    )
    raw_ratio = (input_tokens - effective) / input_tokens
    return round(max(-2.0, min(1.0, raw_ratio)), 4)
