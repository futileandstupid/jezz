"""Proton Pass CLI (``pass-cli``) secret resolution with JBC-SEC-002/OBS-003 containment."""

import os
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final, Protocol, Self, runtime_checkable

import icontract
from pydantic import BaseModel, Field, SecretStr, model_validator

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    ShortText,
    jezz_callable,
    read_bounded_text,
)

DEFAULT_AGENT_REASON: Final[str] = "jezz PCE runtime resolving model API credential"
_PASS_URI_PREFIX: Final[str] = "pass://"
_PROVIDER_FAMILY_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "google-decisions": "google",
        "openai-chat": "openai",
        "openai-responses": "openai",
        "openai-decisions": "openai",
        "azure-responses": "azure",
    }
)


class SecretResolutionError(RuntimeError):
    """Raised when ``pass-cli`` fails to resolve a ``pass://`` secret reference."""


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _validate_uri_segment(segment: str, label: str) -> None:
    if not segment or len(segment) > 192:
        msg = f"Proton Pass URI {label} segment must be 1..192 characters."
        raise ValueError(msg)
    if segment != segment.strip():
        msg = f"Proton Pass URI {label} segment '{segment}' cannot have leading/trailing spaces."
        raise ValueError(msg)
    if segment in {".", ".."} or segment.startswith("-") or "/" in segment:
        msg = f"Proton Pass URI {label} segment '{segment}' contains prohibited syntax."
        raise ValueError(msg)
    if _has_control_chars(segment):
        msg = f"Proton Pass URI {label} segment contains control characters."
        raise ValueError(msg)


def _validate_agent_reason_text(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > 256:
        msg = "Proton Pass agent audit reason must be 1..256 non-whitespace characters."
        raise ValueError(msg)
    if _has_control_chars(cleaned):
        msg = "Proton Pass agent audit reason cannot contain control characters or newlines."
        raise ValueError(msg)
    return cleaned


class ParsedPassUri(BaseModel):
    """Validated 3-segment Proton Pass ``pass://<vault>/<item>/<field>`` reference."""

    model_config = STRICT_FROZEN_CONFIG

    uri: Annotated[str, Field(min_length=11, max_length=512)]
    vault: Annotated[str, Field(min_length=1, max_length=192)]
    item: Annotated[str, Field(min_length=1, max_length=192)]
    field: Annotated[str, Field(min_length=1, max_length=192)]

    @model_validator(mode="after")
    def _verify_canonical_form(self) -> Self:
        _validate_uri_segment(self.vault, "vault")
        _validate_uri_segment(self.item, "item")
        _validate_uri_segment(self.field, "field")
        expected = f"{_PASS_URI_PREFIX}{self.vault}/{self.item}/{self.field}"
        if self.uri != expected:
            msg = f"Non-canonical Proton Pass URI '{self.uri}'; expected '{expected}'."
            raise ValueError(msg)
        return self


@icontract.require(lambda raw_uri: len(raw_uri) >= 1, "raw_uri must be non-empty.")
@icontract.ensure(
    lambda result: result.uri == f"pass://{result.vault}/{result.item}/{result.field}"
)
@jezz_callable
def parse_pass_uri(raw_uri: ShortText) -> ParsedPassUri:
    """Parse and validate a ``pass://<vault>/<item>/<field>`` secret reference URI."""
    if not raw_uri.startswith(_PASS_URI_PREFIX):
        msg = (
            "Secret reference must begin with 'pass://' in format "
            "'pass://<vault>/<item>/<field>' (never pass a plaintext credential)."
        )
        raise ValueError(msg)
    segments = raw_uri[len(_PASS_URI_PREFIX) :].split("/")
    if len(segments) != 3:
        msg = (
            f"Proton Pass URI '{raw_uri}' must contain exactly 3 path segments: "
            "'pass://<vault>/<item>/<field>'."
        )
        raise ValueError(msg)
    return ParsedPassUri(
        uri=raw_uri,
        vault=segments[0],
        item=segments[1],
        field=segments[2],
    )


class JezzSecretsConfig(BaseModel):
    """Project-level ``[tool.jezz.secrets]`` mapping model providers to ``pass://`` URIs."""

    model_config = STRICT_FROZEN_CONFIG

    agent_reason: Annotated[str, Field(min_length=1, max_length=256)] = DEFAULT_AGENT_REASON
    timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 15.0
    providers: Annotated[dict[IdentifierStr, ShortText], Field(max_length=64)] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _validate_entries(self) -> Self:
        _validate_agent_reason_text(self.agent_reason)
        for prov_key, raw_uri in self.providers.items():
            if _has_control_chars(prov_key) or prov_key != prov_key.strip():
                msg = f"Invalid provider key '{prov_key}' in [tool.jezz.secrets.providers]."
                raise ValueError(msg)
            parse_pass_uri(raw_uri)
        return self


@jezz_callable
def lookup_provider_pass_uri(
    config: JezzSecretsConfig,
    provider_name: IdentifierStr,
) -> ShortText | None:
    """Resolve ``provider_name`` (or its canonical provider family) in ``config.providers``."""
    direct = config.providers.get(provider_name)
    if direct is not None:
        return direct
    if provider_name.startswith("gateway/"):
        return config.providers.get("gateway")
    canonical = _PROVIDER_FAMILY_ALIASES.get(provider_name)
    return config.providers.get(canonical) if canonical is not None else None


@jezz_callable
def load_pyproject_secrets_config(pyproject_target: ContainedPath) -> JezzSecretsConfig | None:
    """Load ``[tool.jezz.secrets]`` from ``pyproject_target``, or return ``None`` if absent."""
    raw_toml = read_bounded_text(pyproject_target, max_bytes=524_288)
    data = tomllib.loads(raw_toml)
    tool_tbl = data.get("tool")
    if not isinstance(tool_tbl, dict):
        return None
    jezz_tbl = tool_tbl.get("jezz")
    if not isinstance(jezz_tbl, dict):
        return None
    sec_tbl = jezz_tbl.get("secrets")
    if sec_tbl is None:
        return None
    if not isinstance(sec_tbl, dict):
        msg = f"Expected '[tool.jezz.secrets]' table in '{pyproject_target.relative_posix}'."
        raise ValueError(msg)
    return JezzSecretsConfig.model_validate(sec_tbl)


@jezz_callable
def discover_secrets_config(
    search_roots: Annotated[Sequence[Path], Field(min_length=1, max_length=16)],
    *,
    provider_name: IdentifierStr | None = None,
) -> JezzSecretsConfig | None:
    """Search ``pyproject.toml`` files across ``search_roots`` for ``[tool.jezz.secrets]``."""
    seen: set[Path] = set()
    first_found: JezzSecretsConfig | None = None
    for raw_root in search_roots:
        resolved = raw_root.resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        pyproject = ContainedPath.from_parts(resolved, "pyproject.toml")
        if not pyproject.resolved_path.is_file():
            continue
        cfg = load_pyproject_secrets_config(pyproject)
        if cfg is None:
            continue
        if first_found is None:
            first_found = cfg
        if provider_name is None or lookup_provider_pass_uri(cfg, provider_name) is not None:
            return cfg
    return first_found


@runtime_checkable
class SecretProviderPort(Protocol):
    """Port resolving a validated ``pass://`` reference into a redacted ``SecretStr``."""

    def resolve_uri(
        self,
        pass_uri: ShortText,
        *,
        reason: ShortText = DEFAULT_AGENT_REASON,
    ) -> SecretStr:
        """Resolve ``pass_uri`` and return the non-empty secret wrapped in ``SecretStr``."""
        ...


type GuardedSecretProvider = Annotated[SecretProviderPort, InstanceGuard(SecretProviderPort)]
type GuardedSecretStr = Annotated[SecretStr, InstanceGuard(SecretStr)]


def _sanitize_cli_stderr(raw_stderr: str) -> str:
    lines = [ln.strip() for ln in raw_stderr.splitlines() if ln.strip()]
    if not lines:
        return "pass-cli exited with non-zero status and empty stderr."
    tail = lines[-1]
    return tail[:240]


class ProtonPassSecretProvider:
    """Subprocess adapter resolving ``pass://`` URIs via ``pass-cli item view``."""

    @jezz_callable
    def __init__(
        self,
        *,
        binary_name: IdentifierStr = "pass-cli",
        timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 15.0,
    ) -> None:
        if binary_name.startswith("-") or _has_control_chars(binary_name):
            msg = f"Invalid pass-cli executable name '{binary_name}'."
            raise ValueError(msg)
        self._binary_name = binary_name
        self._timeout_sec = timeout_sec

    @icontract.ensure(lambda result: len(result.get_secret_value()) >= 1)
    @jezz_callable
    def resolve_uri(
        self,
        pass_uri: ShortText,
        *,
        reason: ShortText = DEFAULT_AGENT_REASON,
    ) -> GuardedSecretStr:
        """Invoke ``pass-cli item view`` with ``PROTON_PASS_AGENT_REASON`` and return SecretStr."""
        parsed = parse_pass_uri(pass_uri)
        clean_reason = _validate_agent_reason_text(reason)
        child_env = {**os.environ, "PROTON_PASS_AGENT_REASON": clean_reason}
        try:
            proc = subprocess.run(
                (self._binary_name, "item", "view", parsed.uri),
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_sec,
                env=child_env,
            )
        except FileNotFoundError as exc:
            msg = (
                f"Proton Pass CLI executable '{self._binary_name}' was not found on PATH. "
                "Install Proton Pass CLI and authenticate with an agent session."
            )
            raise SecretResolutionError(msg) from exc
        except subprocess.TimeoutExpired as exc:
            msg = (
                f"Proton Pass CLI timed out after {self._timeout_sec}s while resolving "
                f"'{parsed.uri}'."
            )
            raise SecretResolutionError(msg) from exc
        except OSError as exc:
            msg = f"OS error invoking '{self._binary_name}' for '{parsed.uri}': {exc}"
            raise SecretResolutionError(msg) from exc

        if proc.returncode != 0:
            detail = _sanitize_cli_stderr(proc.stderr)
            msg = (
                f"Proton Pass CLI ('{self._binary_name}') failed to resolve "
                f"'{parsed.uri}' (exit {proc.returncode}): {detail}"
            )
            raise SecretResolutionError(msg)

        resolved_text = proc.stdout.rstrip("\r\n")
        if not resolved_text:
            msg = f"Proton Pass CLI returned an empty value for '{parsed.uri}'."
            raise SecretResolutionError(msg)
        return SecretStr(resolved_text)


@jezz_callable
def resolve_model_api_key(
    model_name: IdentifierStr,
    *,
    explicit_pass_uri: ShortText | None = None,
    search_roots: Annotated[Sequence[Path], Field(max_length=16)] = (),
    provider: GuardedSecretProvider | None = None,
    reason_override: ShortText | None = None,
) -> GuardedSecretStr | None:
    """Resolve a ``SecretStr`` API key for ``model_name`` via CLI URI or ``pyproject.toml``."""
    if model_name == "test" and explicit_pass_uri is None:
        return None
    provider_prefix = model_name.split(":", maxsplit=1)[0] if ":" in model_name else ""
    roots = tuple(search_roots) if search_roots else (Path.cwd(),)
    cfg = discover_secrets_config(roots, provider_name=provider_prefix or None)
    target_uri = explicit_pass_uri
    if target_uri is None and cfg is not None and provider_prefix:
        target_uri = lookup_provider_pass_uri(cfg, provider_prefix)
    if target_uri is None:
        return None
    active_reason = reason_override or (
        cfg.agent_reason if cfg is not None else DEFAULT_AGENT_REASON
    )
    active_provider: SecretProviderPort = (
        provider
        if provider is not None
        else ProtonPassSecretProvider(timeout_sec=cfg.timeout_sec if cfg is not None else 15.0)
    )
    return active_provider.resolve_uri(target_uri, reason=active_reason)
