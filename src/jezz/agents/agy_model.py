"""Zero-credential Antigravity CLI (``agy``) Pydantic AI Model adapter (``antigravity:<model>``)."""

import asyncio
import hashlib
import json
import tempfile
from collections.abc import Sequence
from typing import Annotated, Final, Protocol, runtime_checkable

import icontract
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic_ai.exceptions import ModelHTTPError, UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    RetryFeedbackPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    BoundedText,
    IdentifierStr,
    InstanceGuard,
    LoopBudget,
    NonNegativeInt,
    jezz_callable,
)

AGY_PROVIDER_PREFIX: Final[str] = "antigravity"
_MAX_CACHED_CONVERSATIONS: Final[int] = 32

_TURN_PROTOCOL_BANNER: Final[str] = (
    "CRITICAL HARNESS CONTRACT:\n"
    "1. You are executing as a structured JSON model backend inside an external "
    "Pydantic AI graph.\n"
    "2. NEVER invoke any built-in Antigravity host tools (do NOT call read_file, edit_file, "
    "write_file, run_command, grep_search, find_by_name, browser tools, or subagents).\n"
    "3. Respond IMMEDIATELY in plain text with ONLY one raw JSON object (no prose, no markdown "
    "fences) calling one or more declared Pydantic AI tools below:\n"
    '{"tool_calls": [{"tool_name": "<declared_tool_name>", "args": {<json_arguments>}}]}\n'
    "4. To return your final structured answer, invoke the declared FINAL OUTPUT TOOL in "
    '"tool_calls" with "args" matching its JSON Schema.'
)

_CONTINUATION_REMINDER: Final[str] = (
    "REMINDER: Do NOT invoke built-in Antigravity host tools. Reply ONLY with one raw JSON "
    'object: {"tool_calls": [{"tool_name": "<declared_tool_name>", "args": {...}}]}'
)

type _JsonScalar = str | int | float | bool | None
type _JsonValue = _JsonScalar | list["_JsonValue"] | dict[str, "_JsonValue"]

_AGY_ENVELOPE_ADAPTER: Final[TypeAdapter[dict[str, _JsonValue]]] = TypeAdapter(
    dict[str, _JsonValue]
)
type PromptBodyStr = Annotated[str, Field(min_length=1, max_length=524_288)]


class AgyTurnResult(BaseModel):
    """Normalized stdout/usage record returned by a single ``agy --print`` invocation."""

    model_config = STRICT_FROZEN_CONFIG

    conversation_id: BoundedText
    response_text: Annotated[str, Field(max_length=524_288)]
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_read_tokens: NonNegativeInt = 0


@runtime_checkable
class AntigravityRunnerPort(Protocol):
    """Hexagonal port executing one ``agy --print --output-format json`` turn."""

    async def run_turn(
        self,
        agy_model: IdentifierStr,
        prompt: PromptBodyStr,
        *,
        conversation_id: IdentifierStr | None = None,
        timeout_sec: Annotated[float, Field(gt=0.0, le=900.0, allow_inf_nan=False)] = 240.0,
    ) -> AgyTurnResult:
        """Execute ``prompt`` against ``agy_model`` and return structured turn telemetry."""
        ...


type GuardedAgyRunner = Annotated[AntigravityRunnerPort, InstanceGuard(AntigravityRunnerPort)]


def _has_unsafe_model_chars(slug: str) -> bool:
    if not slug or slug != slug.strip() or slug.startswith("-"):
        return True
    return any(ord(ch) <= 32 or ord(ch) == 127 for ch in slug)


@icontract.ensure(lambda result: len(result) >= 1 and not result.startswith("-"))
@jezz_callable
def parse_antigravity_model_id(model_name: IdentifierStr) -> IdentifierStr:
    """Extract and validate the ``agy`` CLI model slug from ``antigravity:<slug>``."""
    prefix_token = f"{AGY_PROVIDER_PREFIX}:"
    slug = (
        model_name.removeprefix(prefix_token) if model_name.startswith(prefix_token) else model_name
    )
    if _has_unsafe_model_chars(slug):
        msg = f"Invalid Antigravity CLI model identifier '{model_name}'."
        raise UserError(msg)
    return slug


def _extract_int_field(mapping: dict[str, _JsonValue], key: str) -> int:
    val = mapping.get(key, 0)
    return int(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else 0


@icontract.ensure(lambda result: result.input_tokens >= (result.cache_read_tokens or 0))
@jezz_callable
def normalize_agy_usage(
    input_tokens: NonNegativeInt,
    output_tokens: NonNegativeInt,
    cache_read_tokens: NonNegativeInt = 0,
) -> Annotated[RequestUsage, InstanceGuard(RequestUsage)]:
    """Normalize ``agy`` token telemetry to inclusive ``RequestUsage`` invariants."""
    gross_in = max(input_tokens, cache_read_tokens)
    return RequestUsage(
        input_tokens=gross_in,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=0,
    )


def _find_json_envelope(raw_stdout: str) -> dict[str, _JsonValue] | None:
    lines = [ln.strip() for ln in raw_stdout.splitlines() if ln.strip()]
    for line in reversed(lines):
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            return _AGY_ENVELOPE_ADAPTER.validate_json(line)
        except ValidationError:
            continue
    return None


def _decode_agy_stdout(raw_stdout: str, raw_stderr: str, model_slug: str) -> AgyTurnResult:
    payload = _find_json_envelope(raw_stdout)
    if payload is None:
        detail = (raw_stderr.strip() or raw_stdout.strip())[:320]
        raise ModelHTTPError(
            status_code=502,
            model_name=f"{AGY_PROVIDER_PREFIX}:{model_slug}",
            body=f"Antigravity CLI emitted no JSON result line: {detail}",
        )
    status = str(payload.get("status", ""))
    if status != "SUCCESS":
        err_msg = str(payload.get("error") or payload.get("response") or status)[:320]
        raise ModelHTTPError(
            status_code=502,
            model_name=f"{AGY_PROVIDER_PREFIX}:{model_slug}",
            body=f"Antigravity CLI status={status}: {err_msg}",
        )
    raw_usage = payload.get("usage")
    usage_map = raw_usage if isinstance(raw_usage, dict) else {}
    return AgyTurnResult(
        conversation_id=str(payload.get("conversation_id", ""))[:4096],
        response_text=str(payload.get("response", ""))[:524_288],
        input_tokens=max(0, _extract_int_field(usage_map, "input_tokens")),
        output_tokens=max(0, _extract_int_field(usage_map, "output_tokens")),
        cache_read_tokens=max(0, _extract_int_field(usage_map, "cache_read_tokens")),
    )


def _build_agy_command(
    binary_name: str,
    agy_model: str,
    prompt: str,
    conversation_id: str | None,
) -> tuple[str, ...]:
    base = [
        binary_name,
        "--model",
        agy_model,
        "--disable-slash-commands",
        "--output-format",
        "json",
    ]
    if conversation_id:
        base.extend(("--conversation", conversation_id))
    base.extend(("--print", prompt))
    return tuple(base)


async def _terminate_timed_out_proc(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None or proc.returncode is not None:
        return
    proc.kill()
    await proc.communicate()


class SubprocessAntigravityRunner:
    """Async subprocess adapter invoking ``agy --print --output-format json`` in an empty dir."""

    @jezz_callable
    def __init__(self, *, binary_name: IdentifierStr = "agy") -> None:
        if _has_unsafe_model_chars(binary_name):
            msg = f"Invalid Antigravity CLI binary name '{binary_name}'."
            raise ValueError(msg)
        self._binary_name = binary_name

    @jezz_callable
    async def run_turn(
        self,
        agy_model: IdentifierStr,
        prompt: PromptBodyStr,
        *,
        conversation_id: IdentifierStr | None = None,
        timeout_sec: Annotated[float, Field(gt=0.0, le=900.0, allow_inf_nan=False)] = 240.0,
    ) -> AgyTurnResult:
        """Run one isolated ``agy`` turn with async watchdog protection."""
        cmd = _build_agy_command(self._binary_name, agy_model, prompt, conversation_id)
        proc: asyncio.subprocess.Process | None = None
        with tempfile.TemporaryDirectory() as isolated_cwd:
            try:
                async with asyncio.timeout(timeout_sec):
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        cwd=isolated_cwd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    out_bytes, err_bytes = await proc.communicate()
            except TimeoutError as exc:
                await _terminate_timed_out_proc(proc)
                msg = f"Antigravity CLI ('{agy_model}') timed out after {timeout_sec}s."
                raise TimeoutError(msg) from exc
        stdout_str = out_bytes.decode("utf-8", errors="replace")
        stderr_str = err_bytes.decode("utf-8", errors="replace")
        if proc.returncode != 0 and not stdout_str.strip():
            raise ModelHTTPError(
                status_code=502,
                model_name=f"{AGY_PROVIDER_PREFIX}:{agy_model}",
                body=f"agy exited with {proc.returncode}: {stderr_str.strip()[:320]}",
            )
        return _decode_agy_stdout(stdout_str, stderr_str, agy_model)


def _render_user_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        chunks = [item for item in content if isinstance(item, str)]
        return "".join(chunks)
    return str(content)


def _render_request_part(part: object, *, include_system: bool) -> str:
    if isinstance(part, SystemPromptPart):
        return f"[SYSTEM]\n{part.content}" if include_system else ""
    if isinstance(part, UserPromptPart):
        return f"[USER]\n{_render_user_content(part.content)}"
    if isinstance(part, ToolReturnPart):
        prefix = "VALIDATOR_RETRY" if part.outcome == "retried" else "TOOL_RETURN"
        return (
            f"[{prefix} name={part.tool_name} id={part.tool_call_id}]\n{part.model_response_str()}"
        )
    if isinstance(part, RetryFeedbackPart):
        return f"[VALIDATOR_RETRY target=output_validation]\n{part.model_response()}"
    if getattr(part, "part_kind", "") == "retry-prompt":
        label = str(getattr(part, "tool_name", "") or "output_validation")
        resp_fn = getattr(part, "model_response", None)
        body = str(resp_fn()) if callable(resp_fn) else ""
        return f"[VALIDATOR_RETRY target={label}]\n{body}"
    return ""


def _render_response_part(part: object) -> str:
    if isinstance(part, ToolCallPart):
        return (
            f"[ASSISTANT_TOOL_CALL name={part.tool_name} id={part.tool_call_id}]\n"
            f"{part.args_as_json_str()}"
        )
    if isinstance(part, TextPart):
        return f"[ASSISTANT_TEXT]\n{part.content}"
    return ""


def _render_message(msg: ModelMessage, *, include_system: bool) -> str:
    if isinstance(msg, ModelRequest):
        rendered = [_render_request_part(p, include_system=include_system) for p in msg.parts]
    else:
        rendered = [_render_response_part(p) for p in msg.parts]
    return "\n\n".join(chunk for chunk in rendered if chunk)


def _collect_system_instructions(
    messages: Sequence[ModelMessage],
    params: ModelRequestParameters,
) -> str:
    blocks = [p.content for p in (params.instruction_parts or ()) if p.content]
    for msg in messages:
        if isinstance(msg, ModelRequest):
            blocks.extend(
                p.content for p in msg.parts if isinstance(p, SystemPromptPart) and p.content
            )
    return "\n\n".join(blocks)


def _format_tool_def(tool: ToolDefinition, *, role_tag: str) -> str:
    schema_json = json.dumps(tool.parameters_json_schema, ensure_ascii=True, separators=(",", ":"))
    desc = tool.description.strip() if tool.description else "No description."
    return f"- {role_tag} `{tool.name}`: {desc}\n  JSON Schema: {schema_json}"


def _render_declared_tools(params: ModelRequestParameters) -> str:
    lines: list[str] = []
    for fn_tool in params.function_tools:
        lines.append(_format_tool_def(fn_tool, role_tag="WORKSPACE_TOOL"))
    for out_tool in params.output_tools:
        lines.append(_format_tool_def(out_tool, role_tag="FINAL_OUTPUT_TOOL"))
    if not lines and params.allow_text_output:
        return 'No tools declared; respond with {"text": "<your response>"}.'
    return "\n".join(lines)


def _history_fingerprint(
    instr_text: str,
    tools_text: str,
    history: Sequence[ModelMessage],
) -> str:
    hasher = hashlib.sha256()
    hasher.update(instr_text.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(tools_text.encode("utf-8"))
    for item in history:
        hasher.update(b"\x00")
        hasher.update(_render_message(item, include_system=False).encode("utf-8"))
    return hasher.hexdigest()


def _compose_full_prompt(
    instr_text: str,
    tools_text: str,
    messages: Sequence[ModelMessage],
) -> str:
    rendered_turns = [_render_message(m, include_system=False) for m in messages]
    turn_body = "\n\n---\n\n".join(t for t in rendered_turns if t)
    sections = [
        _TURN_PROTOCOL_BANNER,
        f"=== AGENT SYSTEM INSTRUCTIONS ===\n{instr_text}" if instr_text else "",
        f"=== DECLARED PYDANTIC AI TOOLS ===\n{tools_text}",
        f"=== CONVERSATION TRANSCRIPT ===\n{turn_body}",
        _CONTINUATION_REMINDER,
    ]
    return "\n\n".join(s for s in sections if s)[:500_000]


def _compose_delta_prompt(latest_message: ModelMessage) -> str:
    delta_body = _render_message(latest_message, include_system=False)
    return f"{delta_body}\n\n{_CONTINUATION_REMINDER}"[:500_000]


def _strip_code_fence(raw_text: str) -> str:
    stripped = raw_text.strip()
    if not stripped.startswith("```"):
        return stripped
    first_nl = stripped.find("\n")
    if first_nl == -1:
        return stripped.strip("`").strip()
    inner = stripped[first_nl + 1 :]
    end_fence = inner.rfind("```")
    return (inner[:end_fence] if end_fence != -1 else inner).strip()


def _extract_json_dicts(raw_text: str) -> list[dict[str, _JsonValue]]:
    cleaned = _strip_code_fence(raw_text)
    decoder = json.JSONDecoder()
    budget = LoopBudget(max_steps=2048)
    found: list[dict[str, _JsonValue]] = []
    cursor = 0
    while cursor < len(cleaned):
        budget.tick()
        brace_idx = cleaned.find("{", cursor)
        if brace_idx == -1:
            break
        try:
            parsed, end_idx = decoder.raw_decode(cleaned, brace_idx)
        except json.JSONDecodeError:
            cursor = brace_idx + 1
            continue
        if isinstance(parsed, dict):
            found.append(parsed)
        cursor = max(brace_idx + 1, end_idx)
    return found


def _coerce_tool_call_part(entry: dict[str, _JsonValue]) -> ToolCallPart | None:
    raw_name = entry.get("tool_name") or entry.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    raw_args = entry.get("args") if "args" in entry else entry.get("arguments", {})
    if isinstance(raw_args, (dict, str)):
        return ToolCallPart(tool_name=raw_name.strip(), args=raw_args)
    return ToolCallPart(tool_name=raw_name.strip(), args=json.dumps(raw_args))


def _extract_tool_call_list(parsed: dict[str, _JsonValue]) -> list[ModelResponsePart]:
    raw_calls = parsed.get("tool_calls")
    if isinstance(raw_calls, list) and raw_calls:
        parts: list[ModelResponsePart] = [
            p
            for item in raw_calls
            if isinstance(item, dict) and (p := _coerce_tool_call_part(item)) is not None
        ]
        return parts
    single = _coerce_tool_call_part(parsed)
    out: list[ModelResponsePart] = [single] if single is not None else []
    return out


def _matches_output_tool_schema(
    parsed: dict[str, _JsonValue],
    out_tool: ToolDefinition,
) -> bool:
    schema = out_tool.parameters_json_schema
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or not props:
        return False
    return any(key in props for key in parsed)


def _parts_from_parsed_dict(
    parsed: dict[str, _JsonValue],
    params: ModelRequestParameters,
) -> list[ModelResponsePart] | None:
    explicit_calls = _extract_tool_call_list(parsed)
    if explicit_calls:
        return explicit_calls
    if parsed.get("kind") == "final" and "final_output" in parsed:
        final_val = parsed["final_output"]
        if params.output_tools and isinstance(final_val, (dict, str)):
            return [ToolCallPart(tool_name=params.output_tools[0].name, args=final_val)]
        text_payload = final_val if isinstance(final_val, str) else json.dumps(final_val)
        return [TextPart(content=text_payload)]
    if len(params.output_tools) == 1 and _matches_output_tool_schema(
        parsed, params.output_tools[0]
    ):
        return [ToolCallPart(tool_name=params.output_tools[0].name, args=parsed)]
    raw_txt = parsed.get("text")
    if isinstance(raw_txt, str) and params.allow_text_output and not params.output_tools:
        return [TextPart(content=raw_txt)]
    return None


def _parse_response_parts(
    raw_text: str,
    params: ModelRequestParameters,
) -> list[ModelResponsePart]:
    candidates = _extract_json_dicts(raw_text)
    for parsed in reversed(candidates):
        matched = _parts_from_parsed_dict(parsed, params)
        if matched is not None:
            return matched
    return [TextPart(content=raw_text)]


class AntigravityCliModel(Model):
    """Zero-credential ``pydantic_ai.models.Model`` backed by ``agy`` CLI sessions."""

    @jezz_callable
    def __init__(
        self,
        model_name: IdentifierStr,
        *,
        runner: GuardedAgyRunner | None = None,
        timeout_sec: Annotated[float, Field(gt=0.0, le=900.0, allow_inf_nan=False)] = 240.0,
    ) -> None:
        super().__init__(
            profile=ModelProfile(
                supports_json_schema_output=False,
                supports_json_object_output=False,
            )
        )
        self._system = AGY_PROVIDER_PREFIX
        self._agy_slug = parse_antigravity_model_id(model_name)
        self._runner: AntigravityRunnerPort = (
            runner if runner is not None else SubprocessAntigravityRunner()
        )
        self._timeout_sec = timeout_sec
        self._conv_by_prefix: dict[str, str] = {}

    @property
    def model_name(self) -> BoundedText:
        """Return underlying ``agy`` CLI model slug."""
        return self._agy_slug

    @property
    def system(self) -> IdentifierStr:
        """Return provider system identifier."""
        return self._system

    def _record_conversation(self, prefix_digest: str, conversation_id: str) -> None:
        if not conversation_id:
            return
        if len(self._conv_by_prefix) >= _MAX_CACHED_CONVERSATIONS:
            oldest_key = next(iter(self._conv_by_prefix))
            del self._conv_by_prefix[oldest_key]
        self._conv_by_prefix[prefix_digest] = conversation_id

    @jezz_callable
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> Annotated[ModelResponse, InstanceGuard(ModelResponse)]:
        """Execute one turn via ``agy`` with multi-turn ``--conversation`` prefix reuse."""
        cache_enabled = (model_settings or {}).get("cache") is not False
        _, prep_params = self.prepare_request(model_settings, model_request_parameters)
        instr_text = _collect_system_instructions(messages, prep_params)
        tools_text = _render_declared_tools(prep_params)

        active_conv_id: str | None = None
        if cache_enabled and len(messages) >= 2:
            prior_key = _history_fingerprint(instr_text, tools_text, messages[:-1])
            active_conv_id = self._conv_by_prefix.get(prior_key)

        prompt_text = (
            _compose_delta_prompt(messages[-1])
            if active_conv_id is not None
            else _compose_full_prompt(instr_text, tools_text, messages)
        )
        turn = await self._runner.run_turn(
            self._agy_slug,
            prompt_text[:524_288],
            conversation_id=active_conv_id[:256] if active_conv_id else None,
            timeout_sec=self._timeout_sec,
        )
        parts = _parse_response_parts(turn.response_text, prep_params)
        usage = normalize_agy_usage(
            turn.input_tokens,
            turn.output_tokens,
            turn.cache_read_tokens,
        )
        response = ModelResponse(
            parts=parts,
            usage=usage,
            model_name=f"{self._system}:{self._agy_slug}",
        )
        if cache_enabled and turn.conversation_id:
            next_key = _history_fingerprint(instr_text, tools_text, [*messages, response])
            self._record_conversation(next_key, turn.conversation_id)
        return response
