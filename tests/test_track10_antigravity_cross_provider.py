"""Track 10: Zero-credential Antigravity CLI (``antigravity:<model>``) cross-provider tests."""

from pathlib import Path
from typing import Annotated

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError

from jezz.agents.agy_model import (
    AGY_PROVIDER_PREFIX,
    AgyTurnResult,
    AntigravityCliModel,
    normalize_agy_usage,
    parse_antigravity_model_id,
)
from jezz.agents.caching import PromptCacheMode, resolve_cache_model_settings
from jezz.agents.providers import TlsResilientModel, infer_authenticated_model
from jezz.agents.routing import CliModelOverrides, resolve_specialist_routing
from jezz.evals.cross_provider import (
    DEFAULT_WIRE_PROBE_MODELS,
    parse_provider_suite_spec,
    verify_cross_provider_agent_compatibility,
)
from jezz.guards import STRICT_FROZEN_CONFIG, IdentifierStr, NonNegativeInt
from jezz.secrets import canonical_provider_family, resolve_model_api_key


class _DemoOutput(BaseModel):
    model_config = STRICT_FROZEN_CONFIG

    computed_total: NonNegativeInt
    note: IdentifierStr


class _RecordingAgyRunner:
    """Deterministic in-memory ``AntigravityRunnerPort`` recording turn invocations."""

    def __init__(self, scripted_responses: tuple[AgyTurnResult, ...]) -> None:
        self._queue = list(scripted_responses)
        self.calls: list[tuple[str, str, str | None]] = []

    async def run_turn(
        self,
        agy_model: IdentifierStr,
        prompt: Annotated[str, Field(min_length=1, max_length=524_288)],
        *,
        conversation_id: IdentifierStr | None = None,
        timeout_sec: Annotated[float, Field(gt=0.0, le=900.0, allow_inf_nan=False)] = 240.0,
    ) -> AgyTurnResult:
        _ = timeout_sec
        self.calls.append((agy_model, prompt, conversation_id))
        return self._queue.pop(0)


@given(
    raw_in=st.integers(min_value=0, max_value=250_000),
    raw_out=st.integers(min_value=0, max_value=50_000),
    raw_cache=st.integers(min_value=0, max_value=250_000),
)
def test_normalize_agy_usage_enforces_inclusive_input_law(
    raw_in: int,
    raw_out: int,
    raw_cache: int,
) -> None:
    usage = normalize_agy_usage(raw_in, raw_out, raw_cache)
    assert usage.input_tokens >= raw_in
    assert usage.input_tokens >= raw_cache
    assert usage.cache_read_tokens == raw_cache
    assert usage.output_tokens == raw_out
    assert usage.cache_write_tokens == 0


def test_parse_antigravity_model_id_and_zero_secret_resolution(tmp_path: Path) -> None:
    assert parse_antigravity_model_id("antigravity:claude-opus-5-5-medium") == (
        "claude-opus-5-5-medium"
    )
    assert parse_antigravity_model_id("gpt-oss-120b-medium") == "gpt-oss-120b-medium"
    with pytest.raises(UserError):
        parse_antigravity_model_id("antigravity:--flag-inject")
    with pytest.raises(UserError):
        parse_antigravity_model_id("antigravity:bad space")

    for spec in (
        "antigravity:claude-sonnet-5-5-medium",
        "antigravity:claude-opus-5-5-medium",
        "antigravity:gpt-oss-120b-medium",
    ):
        assert canonical_provider_family(spec) == ""
        assert resolve_model_api_key(spec, search_roots=(tmp_path,)) is None
        inferred = infer_authenticated_model(spec)
        assert isinstance(inferred, AntigravityCliModel)
        assert inferred.system == AGY_PROVIDER_PREFIX
        assert f"{inferred.system}:{inferred.model_name}" == spec


def test_tiered_antigravity_routing_and_suite_parsing() -> None:
    suite_spec = parse_provider_suite_spec(
        "claude_tiered=arch:antigravity:claude-opus-5-5-medium,"
        "build:antigravity:claude-sonnet-5-5-medium@auto"
    )
    assert suite_spec.routing.specify == "antigravity:claude-opus-5-5-medium"
    assert suite_spec.routing.synthesize == "antigravity:claude-sonnet-5-5-medium"
    assert suite_spec.routing.unique_provider_families() == frozenset()

    hybrid = resolve_specialist_routing(
        CliModelOverrides(
            architect_model="antigravity:claude-opus-5-5-medium",
            builder_model="google:gemini-3.8-flash",
            inspector_model="google:gemini-3.8-flash",
        )
    )
    assert hybrid.unique_provider_families() == frozenset({"google"})


@pytest.mark.asyncio
async def test_antigravity_cli_model_tool_loop_and_conversation_cache_reuse() -> None:
    scripted = (
        AgyTurnResult(
            conversation_id="conv-alpha-01",
            response_text='{"tool_calls":[{"tool_name":"fetch_bonus","args":{"tier":"gold"}}]}',
            input_tokens=16_500,
            output_tokens=90,
            cache_read_tokens=0,
        ),
        AgyTurnResult(
            conversation_id="conv-alpha-01",
            response_text=(
                'Checking intermediate sample {"unrelated": 1} before final answer:\n'
                "```json\n"
                '{"computed_total": 125, "note": "settled_ok"}\n'
                "```"
            ),
            input_tokens=17_200,
            output_tokens=110,
            cache_read_tokens=16_100,
        ),
        AgyTurnResult(
            conversation_id="conv-cold-02",
            response_text=(
                '{"tool_calls":[{"tool_name":"final_result",'
                '"args":{"computed_total":42,"note":"uncached_ok"}}]}'
            ),
            input_tokens=16_400,
            output_tokens=80,
            cache_read_tokens=0,
        ),
    )
    runner = _RecordingAgyRunner(scripted)
    agy_model = AntigravityCliModel("antigravity:claude-sonnet-5-5-medium", runner=runner)
    agent: Agent[None, _DemoOutput] = Agent(
        agy_model,
        output_type=_DemoOutput,
        instructions="Compute deterministic bonus totals.",
    )

    @agent.tool_plain
    def fetch_bonus(tier: str) -> int:
        return 25 if tier == "gold" else 0

    res_cached = await agent.run(
        "Base is 100 on gold tier.",
        model_settings=resolve_cache_model_settings(PromptCacheMode.AUTO),
    )
    assert res_cached.output.computed_total == 125
    assert res_cached.output.note == "settled_ok"
    assert len(runner.calls) == 2
    assert runner.calls[0][2] is None
    assert "=== AGENT SYSTEM INSTRUCTIONS ===" in runner.calls[0][1]
    assert runner.calls[1][2] == "conv-alpha-01"
    assert "=== AGENT SYSTEM INSTRUCTIONS ===" not in runner.calls[1][1]
    assert "[TOOL_RETURN name=fetch_bonus" in runner.calls[1][1]
    assert res_cached.usage.cache_read_tokens == 16_100

    res_off = await agent.run(
        "Direct 42.",
        model_settings=resolve_cache_model_settings(PromptCacheMode.OFF),
    )
    assert res_off.output.computed_total == 42
    assert runner.calls[2][2] is None


@pytest.mark.asyncio
async def test_tls_resilient_wrapper_preserves_antigravity_conversation_state() -> None:
    wrapper = TlsResilientModel("antigravity:gpt-oss-120b-medium")
    assert wrapper.wrapped.system == AGY_PROVIDER_PREFIX
    assert wrapper.configured_model_id == "antigravity:gpt-oss-120b-medium"

    report = await verify_cross_provider_agent_compatibility(
        model_ids=(
            "antigravity:claude-sonnet-5-5-medium",
            "antigravity:claude-opus-5-5-medium",
            "antigravity:gpt-oss-120b-medium",
        )
    )
    assert report.all_compatible is True
    assert report.total_wire_probes == 3 * 4 * 16
    assert "antigravity:claude-opus-5-5-medium" in DEFAULT_WIRE_PROBE_MODELS
