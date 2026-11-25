"""Scheme-dispatched secret resolution (env://, pass://, op://, keychain://) under JBC-SEC-002."""

import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final, Protocol, Self, runtime_checkable

import icontract
import platformdirs
from pydantic import BaseModel, Field, SecretStr, model_validator

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    InstanceGuard,
    ShortText,
    jezz_callable,
    read_bounded_text,
)

DEFAULT_AGENT_REASON: Final[str] = "jezz PCE runtime resolving model API credential"
USER_CONFIG_FILENAME: Final[str] = "config.toml"
WORKSPACE_CONFIG_FILENAME: Final[str] = "jezz.toml"
BUILTIN_SECRET_SCHEMES: Final[frozenset[str]] = frozenset({"env", "pass", "op", "keychain"})

_ENV_URI_PREFIX: Final[str] = "env://"
_PASS_URI_PREFIX: Final[str] = "pass://"
_OP_URI_PREFIX: Final[str] = "op://"
_KEYCHAIN_URI_PREFIX: Final[str] = "keychain://"

_SCHEME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9+.-]{0,31}$")
_ENV_VAR_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

_PROVIDER_FAMILY_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "google-decisions": "google",
        "openai-chat": "openai",
        "openai-responses": "openai",
        "openai-decisions": "openai",
        "azure-responses": "azure",
    }
)

PROVIDER_DEFAULT_ENV_VARS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "google-cloud": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "groq": ("GROQ_API_KEY",),
        "mistral": ("MISTRAL_API_KEY",),
        "cohere": ("CO_API_KEY", "COHERE_API_KEY"),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "xai": ("XAI_API_KEY",),
        "cerebras": ("CEREBRAS_API_KEY",),
        "fireworks": ("FIREWORKS_API_KEY",),
        "together": ("TOGETHER_API_KEY",),
        "gateway": ("PYDANTIC_AI_GATEWAY_API_KEY",),
    }
)


class SecretResolutionError(RuntimeError):
    """Raised when a secret provider fails to resolve a ``<scheme>://`` reference."""


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _validate_scheme_name(raw_scheme: str) -> str:
    if not _SCHEME_RE.match(raw_scheme):
        msg = (
            f"Invalid secret URI scheme '{raw_scheme}'; expected lowercase RFC-3986 scheme "
            "matching '[a-z][a-z0-9+.-]{0,31}'."
        )
        raise ValueError(msg)
    return raw_scheme


def _validate_uri_segment(segment: str, label: str, *, scheme_label: str = "Secret") -> None:
    if not segment or len(segment) > 192:
        msg = f"{scheme_label} URI {label} segment must be 1..192 characters."
        raise ValueError(msg)
    if segment != segment.strip():
        msg = f"{scheme_label} URI {label} segment '{segment}' cannot have leading/trailing spaces."
        raise ValueError(msg)
    if segment in {".", ".."} or segment.startswith("-") or "/" in segment:
        msg = f"{scheme_label} URI {label} segment '{segment}' contains prohibited syntax."
        raise ValueError(msg)
    if _has_control_chars(segment):
        msg = f"{scheme_label} URI {label} segment contains control characters."
        raise ValueError(msg)


def _validate_agent_reason_text(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > 256:
        msg = "Secret audit reason must be 1..256 non-whitespace characters."
        raise ValueError(msg)
    if _has_control_chars(cleaned):
        msg = "Secret audit reason cannot contain control characters or newlines."
        raise ValueError(msg)
    return cleaned


def _validate_binary_name(binary_name: str, label: str) -> None:
    if binary_name.startswith("-") or _has_control_chars(binary_name) or "/" in binary_name:
        msg = f"Invalid {label} executable name '{binary_name}'."
        raise ValueError(msg)


@icontract.require(lambda raw_uri: len(raw_uri) >= 1, "raw_uri must be non-empty.")
@icontract.ensure(lambda result: bool(_SCHEME_RE.match(result)))
@jezz_callable
def extract_secret_scheme(raw_uri: ShortText) -> IdentifierStr:
    """Extract and validate the ``<scheme>`` prefix from ``<scheme>://<target>``."""
    if raw_uri != raw_uri.strip() or _has_control_chars(raw_uri):
        msg = "Secret URI cannot contain control characters or leading/trailing whitespace."
        raise ValueError(msg)
    if "://" not in raw_uri:
        msg = (
            "Secret reference must begin with '<scheme>://' (e.g. 'env://VAR', "
            "'pass://vault/item/field', 'op://vault/item/field', or "
            "'keychain://service/account'); plaintext credentials are prohibited."
        )
        raise ValueError(msg)
    scheme_part, remainder = raw_uri.split("://", maxsplit=1)
    if not remainder:
        msg = f"Secret URI '{raw_uri}' is missing target path after '{scheme_part}://'."
        raise ValueError(msg)
    return _validate_scheme_name(scheme_part)


class ParsedEnvUri(BaseModel):
    """Validated environment-variable secret reference ``env://<VAR_NAME>``."""

    model_config = STRICT_FROZEN_CONFIG

    uri: Annotated[str, Field(min_length=7, max_length=140)]
    var_name: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def _verify_canonical_form(self) -> Self:
        if not _ENV_VAR_RE.match(self.var_name):
            msg = (
                f"Invalid environment variable identifier '{self.var_name}' in env:// URI; "
                "expected POSIX '[A-Za-z_][A-Za-z0-9_]*'."
            )
            raise ValueError(msg)
        expected = f"{_ENV_URI_PREFIX}{self.var_name}"
        if self.uri != expected:
            msg = f"Non-canonical env:// URI '{self.uri}'; expected '{expected}'."
            raise ValueError(msg)
        return self


class ParsedPassUri(BaseModel):
    """Validated 3-segment Proton Pass ``pass://<vault>/<item>/<field>`` reference."""

    model_config = STRICT_FROZEN_CONFIG

    uri: Annotated[str, Field(min_length=11, max_length=512)]
    vault: Annotated[str, Field(min_length=1, max_length=192)]
    item: Annotated[str, Field(min_length=1, max_length=192)]
    field: Annotated[str, Field(min_length=1, max_length=192)]

    @model_validator(mode="after")
    def _verify_canonical_form(self) -> Self:
        _validate_uri_segment(self.vault, "vault", scheme_label="Proton Pass")
        _validate_uri_segment(self.item, "item", scheme_label="Proton Pass")
        _validate_uri_segment(self.field, "field", scheme_label="Proton Pass")
        expected = f"{_PASS_URI_PREFIX}{self.vault}/{self.item}/{self.field}"
        if self.uri != expected:
            msg = f"Non-canonical Proton Pass URI '{self.uri}'; expected '{expected}'."
            raise ValueError(msg)
        return self


class ParsedOpUri(BaseModel):
    """Validated 1Password ``op://<vault>/<item>[/<section>]/<field>`` reference."""

    model_config = STRICT_FROZEN_CONFIG

    uri: Annotated[str, Field(min_length=9, max_length=512)]
    vault: Annotated[str, Field(min_length=1, max_length=192)]
    item: Annotated[str, Field(min_length=1, max_length=192)]
    section: Annotated[str, Field(min_length=1, max_length=192)] | None = None
    field: Annotated[str, Field(min_length=1, max_length=192)]

    @model_validator(mode="after")
    def _verify_canonical_form(self) -> Self:
        _validate_uri_segment(self.vault, "vault", scheme_label="1Password")
        _validate_uri_segment(self.item, "item", scheme_label="1Password")
        if self.section is not None:
            _validate_uri_segment(self.section, "section", scheme_label="1Password")
        _validate_uri_segment(self.field, "field", scheme_label="1Password")
        expected = (
            f"{_OP_URI_PREFIX}{self.vault}/{self.item}/{self.section}/{self.field}"
            if self.section is not None
            else f"{_OP_URI_PREFIX}{self.vault}/{self.item}/{self.field}"
        )
        if self.uri != expected:
            msg = f"Non-canonical 1Password URI '{self.uri}'; expected '{expected}'."
            raise ValueError(msg)
        return self


class ParsedKeychainUri(BaseModel):
    """Validated OS keychain ``keychain://<service>/<account>`` reference."""

    model_config = STRICT_FROZEN_CONFIG

    uri: Annotated[str, Field(min_length=14, max_length=512)]
    service: Annotated[str, Field(min_length=1, max_length=192)]
    account: Annotated[str, Field(min_length=1, max_length=192)]

    @model_validator(mode="after")
    def _verify_canonical_form(self) -> Self:
        _validate_uri_segment(self.service, "service", scheme_label="Keychain")
        _validate_uri_segment(self.account, "account", scheme_label="Keychain")
        expected = f"{_KEYCHAIN_URI_PREFIX}{self.service}/{self.account}"
        if self.uri != expected:
            msg = f"Non-canonical Keychain URI '{self.uri}'; expected '{expected}'."
            raise ValueError(msg)
        return self


@icontract.require(lambda raw_uri: len(raw_uri) >= 1, "raw_uri must be non-empty.")
@icontract.ensure(lambda result: result.uri == f"env://{result.var_name}")
@jezz_callable
def parse_env_uri(raw_uri: ShortText) -> ParsedEnvUri:
    """Parse and validate an ``env://<VAR_NAME>`` secret reference URI."""
    if not raw_uri.startswith(_ENV_URI_PREFIX):
        msg = "Environment secret reference must begin with 'env://<VAR_NAME>'."
        raise ValueError(msg)
    var_name = raw_uri[len(_ENV_URI_PREFIX) :]
    return ParsedEnvUri(uri=raw_uri, var_name=var_name)


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


@icontract.require(lambda raw_uri: len(raw_uri) >= 1, "raw_uri must be non-empty.")
@icontract.ensure(lambda result: result.uri.startswith("op://"))
@jezz_callable
def parse_op_uri(raw_uri: ShortText) -> ParsedOpUri:
    """Parse and validate an ``op://<vault>/<item>[/<section>]/<field>`` reference URI."""
    if not raw_uri.startswith(_OP_URI_PREFIX):
        msg = "1Password secret reference must begin with 'op://<vault>/<item>/<field>'."
        raise ValueError(msg)
    segments = raw_uri[len(_OP_URI_PREFIX) :].split("/")
    if len(segments) == 3:
        return ParsedOpUri(uri=raw_uri, vault=segments[0], item=segments[1], field=segments[2])
    if len(segments) == 4:
        return ParsedOpUri(
            uri=raw_uri,
            vault=segments[0],
            item=segments[1],
            section=segments[2],
            field=segments[3],
        )
    msg = (
        f"1Password URI '{raw_uri}' must contain 3 or 4 path segments: "
        "'op://<vault>/<item>[/<section>]/<field>'."
    )
    raise ValueError(msg)


@icontract.require(lambda raw_uri: len(raw_uri) >= 1, "raw_uri must be non-empty.")
@icontract.ensure(lambda result: result.uri == f"keychain://{result.service}/{result.account}")
@jezz_callable
def parse_keychain_uri(raw_uri: ShortText) -> ParsedKeychainUri:
    """Parse and validate a ``keychain://<service>/<account>`` secret reference URI."""
    if not raw_uri.startswith(_KEYCHAIN_URI_PREFIX):
        msg = "Keychain secret reference must begin with 'keychain://<service>/<account>'."
        raise ValueError(msg)
    segments = raw_uri[len(_KEYCHAIN_URI_PREFIX) :].split("/")
    if len(segments) != 2:
        msg = (
            f"Keychain URI '{raw_uri}' must contain exactly 2 path segments: "
            "'keychain://<service>/<account>'."
        )
        raise ValueError(msg)
    return ParsedKeychainUri(uri=raw_uri, service=segments[0], account=segments[1])


_BUILTIN_URI_PARSERS: Final[Mapping[str, Callable[[str], object]]] = MappingProxyType(
    {
        "env": parse_env_uri,
        "pass": parse_pass_uri,
        "op": parse_op_uri,
        "keychain": parse_keychain_uri,
    }
)


def _validate_custom_scheme_segments(raw_uri: str, scheme: str) -> None:
    remainder = raw_uri[len(scheme) + 3 :]
    segments = remainder.split("/")
    if not segments or len(segments) > 8:
        msg = f"Custom '{scheme}://' URI '{raw_uri}' must contain 1..8 path segments."
        raise ValueError(msg)
    for seg in segments:
        _validate_uri_segment(seg, "path", scheme_label=scheme)


@icontract.require(lambda raw_uri: len(raw_uri) >= 1, "raw_uri must be non-empty.")
@icontract.ensure(lambda raw_uri, result: result == raw_uri)
@jezz_callable
def validate_secret_uri(
    raw_uri: ShortText,
    *,
    allowed_schemes: Annotated[frozenset[IdentifierStr], Field(min_length=1, max_length=32)] = (
        BUILTIN_SECRET_SCHEMES
    ),
) -> ShortText:
    """Validate ``raw_uri`` against registered ``allowed_schemes`` and per-scheme grammar."""
    scheme = extract_secret_scheme(raw_uri)
    if scheme not in allowed_schemes:
        allowed_sorted = sorted(allowed_schemes)
        msg = f"Unsupported secret URI scheme '{scheme}://'; registered schemes: {allowed_sorted}."
        raise ValueError(msg)
    builtin_parser = _BUILTIN_URI_PARSERS.get(scheme)
    if builtin_parser is not None:
        builtin_parser(raw_uri)
    else:
        _validate_custom_scheme_segments(raw_uri, scheme)
    return raw_uri


class JezzSecretsConfig(BaseModel):
    """Workspace or user ``secrets`` table mapping model providers to ``<scheme>://`` URIs."""

    model_config = STRICT_FROZEN_CONFIG

    agent_reason: Annotated[str, Field(min_length=1, max_length=256)] = DEFAULT_AGENT_REASON
    timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 15.0
    providers: Annotated[dict[IdentifierStr, ShortText], Field(max_length=64)] = Field(
        default_factory=dict
    )
    extra_schemes: Annotated[frozenset[IdentifierStr], Field(max_length=16)] = frozenset()

    @model_validator(mode="after")
    def _validate_entries(self) -> Self:
        _validate_agent_reason_text(self.agent_reason)
        for extra in self.extra_schemes:
            _validate_scheme_name(extra)
        permitted = BUILTIN_SECRET_SCHEMES | set(self.extra_schemes)
        for prov_key, raw_uri in self.providers.items():
            if _has_control_chars(prov_key) or prov_key != prov_key.strip():
                msg = f"Invalid provider key '{prov_key}' in secrets.providers."
                raise ValueError(msg)
            validate_secret_uri(raw_uri, allowed_schemes=frozenset(permitted))
        return self


@jezz_callable
def canonical_provider_family(model_name: IdentifierStr) -> BoundedText:
    """Return canonical provider family key for ``model_name`` (or ``""`` when unprefixed)."""
    if model_name == "test" or ":" not in model_name:
        return ""
    prefix = model_name.split(":", maxsplit=1)[0]
    if prefix == "antigravity":
        return ""
    if prefix.startswith("gateway/"):
        return "gateway"
    return _PROVIDER_FAMILY_ALIASES.get(prefix, prefix)


@jezz_callable
def lookup_provider_secret_uri(
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
def lookup_provider_pass_uri(
    config: JezzSecretsConfig,
    provider_name: IdentifierStr,
) -> ShortText | None:
    """Backwards-compatible alias for ``lookup_provider_secret_uri``."""
    return lookup_provider_secret_uri(config, provider_name)


@jezz_callable
def read_pyproject_jezz_table(
    pyproject_target: ContainedPath,
    table_key: IdentifierStr,
) -> dict[IdentifierStr, object] | None:
    """Read ``[tool.jezz.<table_key>]`` from ``pyproject_target``, or ``None`` if absent."""
    raw_toml = read_bounded_text(pyproject_target, max_bytes=524_288)
    data = tomllib.loads(raw_toml)
    tool_tbl = data.get("tool")
    if not isinstance(tool_tbl, dict):
        return None
    jezz_tbl = tool_tbl.get("jezz")
    if not isinstance(jezz_tbl, dict):
        return None
    sub_tbl = jezz_tbl.get(table_key)
    if sub_tbl is None:
        return None
    if not isinstance(sub_tbl, dict):
        msg = f"Expected '[tool.jezz.{table_key}]' table in '{pyproject_target.relative_posix}'."
        raise ValueError(msg)
    return {str(k): v for k, v in sub_tbl.items()}


@jezz_callable
def read_jezz_toml_table(
    jezz_toml_target: ContainedPath,
    table_key: IdentifierStr,
) -> dict[IdentifierStr, object] | None:
    """Read ``[<table_key>]`` (or ``[tool.jezz.<table_key>]``) from ``jezz.toml``."""
    raw_toml = read_bounded_text(jezz_toml_target, max_bytes=524_288)
    data = tomllib.loads(raw_toml)
    top_tbl = data.get(table_key)
    tool_tbl = data.get("tool")
    nested_tbl = (
        tool_tbl.get("jezz", {}).get(table_key)
        if isinstance(tool_tbl, dict) and isinstance(tool_tbl.get("jezz"), dict)
        else None
    )
    if top_tbl is not None and nested_tbl is not None:
        msg = (
            f"Ambiguous '{jezz_toml_target.relative_posix}': define '[{table_key}]' or "
            f"'[tool.jezz.{table_key}]', not both."
        )
        raise ValueError(msg)
    chosen = top_tbl if top_tbl is not None else nested_tbl
    if chosen is None:
        return None
    if not isinstance(chosen, dict):
        msg = f"Expected '[{table_key}]' table in '{jezz_toml_target.relative_posix}'."
        raise ValueError(msg)
    return {str(k): v for k, v in chosen.items()}


@jezz_callable
def read_workspace_jezz_table(
    workspace_root: Path,
    table_key: IdentifierStr,
) -> dict[IdentifierStr, object] | None:
    """Resolve ``table_key`` from ``jezz.toml`` first, falling back to ``pyproject.toml``."""
    resolved = workspace_root.resolve()
    jezz_toml = ContainedPath.from_parts(resolved, WORKSPACE_CONFIG_FILENAME)
    if jezz_toml.resolved_path.is_file():
        hit = read_jezz_toml_table(jezz_toml, table_key)
        if hit is not None:
            return hit
    pyproject = ContainedPath.from_parts(resolved, "pyproject.toml")
    if pyproject.resolved_path.is_file():
        return read_pyproject_jezz_table(pyproject, table_key)
    return None


@jezz_callable
def load_pyproject_secrets_config(pyproject_target: ContainedPath) -> JezzSecretsConfig | None:
    """Load ``[tool.jezz.secrets]`` from ``pyproject_target``, or return ``None`` if absent."""
    sec_tbl = read_pyproject_jezz_table(pyproject_target, "secrets")
    return JezzSecretsConfig.model_validate(sec_tbl) if sec_tbl is not None else None


@jezz_callable
def discover_secrets_config(
    search_roots: Annotated[Sequence[Path], Field(min_length=1, max_length=16)],
    *,
    provider_name: IdentifierStr | None = None,
) -> JezzSecretsConfig | None:
    """Search ``jezz.toml`` then ``pyproject.toml`` across ``search_roots`` for secrets config."""
    seen: set[Path] = set()
    first_found: JezzSecretsConfig | None = None
    for raw_root in search_roots:
        resolved = raw_root.resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        sec_tbl = read_workspace_jezz_table(resolved, "secrets")
        if sec_tbl is None:
            continue
        cfg = JezzSecretsConfig.model_validate(sec_tbl)
        if first_found is None:
            first_found = cfg
        if provider_name is None or lookup_provider_secret_uri(cfg, provider_name) is not None:
            return cfg
    return first_found


@jezz_callable
def default_user_config_dir() -> Path:
    """Return the platform-canonical user configuration directory for ``jezz``."""
    return platformdirs.user_config_path("jezz").resolve()


def _extract_tool_jezz_secrets_dict(data: Mapping[str, object]) -> dict[str, object] | None:
    tool_tbl = data.get("tool")
    if not isinstance(tool_tbl, dict):
        return None
    jezz_tbl = tool_tbl.get("jezz")
    if not isinstance(jezz_tbl, dict):
        return None
    nested = jezz_tbl.get("secrets")
    if nested is None:
        return None
    if not isinstance(nested, dict):
        msg = "Expected '[tool.jezz.secrets]' in user config.toml to be a TOML table."
        raise ValueError(msg)
    return {str(k): v for k, v in nested.items()}


@jezz_callable
def load_user_secrets_config(config_target: ContainedPath) -> JezzSecretsConfig | None:
    """Load ``[secrets]`` (or ``[tool.jezz.secrets]``) from user ``config.toml``."""
    raw_toml = read_bounded_text(config_target, max_bytes=524_288)
    data = tomllib.loads(raw_toml)
    top_sec = data.get("secrets")
    nested_sec = _extract_tool_jezz_secrets_dict(data)
    if top_sec is not None and nested_sec is not None:
        msg = (
            f"Ambiguous user config '{config_target.relative_posix}': define '[secrets]' "
            "or '[tool.jezz.secrets]', not both."
        )
        raise ValueError(msg)
    if top_sec is not None:
        if not isinstance(top_sec, dict):
            msg = f"Expected '[secrets]' table in '{config_target.relative_posix}'."
            raise ValueError(msg)
        return JezzSecretsConfig.model_validate({str(k): v for k, v in top_sec.items()})
    return JezzSecretsConfig.model_validate(nested_sec) if nested_sec is not None else None


@jezz_callable
def discover_user_secrets_config(
    *, user_config_dir: Path | None = None
) -> JezzSecretsConfig | None:
    """Load ``config.toml`` from ``user_config_dir`` (or ``default_user_config_dir()``)."""
    base_dir = (
        user_config_dir.resolve() if user_config_dir is not None else default_user_config_dir()
    )
    if not base_dir.is_dir():
        return None
    cfg_path = ContainedPath.from_parts(base_dir, USER_CONFIG_FILENAME)
    if not cfg_path.resolved_path.is_file():
        return None
    return load_user_secrets_config(cfg_path)


@runtime_checkable
class SecretProviderPort(Protocol):
    """Port resolving a validated ``<scheme>://`` reference into a redacted ``SecretStr``."""

    def resolve_uri(
        self,
        ref_uri: ShortText,
        /,
        *,
        reason: ShortText = DEFAULT_AGENT_REASON,
    ) -> SecretStr:
        """Resolve ``ref_uri`` and return the non-empty secret wrapped in ``SecretStr``."""
        ...


type GuardedSecretProvider = Annotated[SecretProviderPort, InstanceGuard(SecretProviderPort)]
type GuardedSecretStr = Annotated[SecretStr, InstanceGuard(SecretStr)]


def _sanitize_cli_stderr(raw_stderr: str, binary_name: str) -> str:
    lines = [ln.strip() for ln in raw_stderr.splitlines() if ln.strip()]
    if not lines:
        return f"{binary_name} exited with non-zero status and empty stderr."
    return lines[-1][:240]


def _run_secret_cli(
    args: tuple[str, ...],
    uri_label: str,
    tool_label: str,
    timeout_sec: float,
    extra_env: Mapping[str, str] | None = None,
) -> SecretStr:
    binary_name = args[0]
    child_env = {**os.environ, **extra_env} if extra_env is not None else dict(os.environ)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
            env=child_env,
        )
    except FileNotFoundError as exc:
        msg = (
            f"{tool_label} executable '{binary_name}' was not found on PATH "
            f"while resolving '{uri_label}'."
        )
        raise SecretResolutionError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"{tool_label} timed out after {timeout_sec}s while resolving '{uri_label}'."
        raise SecretResolutionError(msg) from exc
    except OSError as exc:
        msg = f"OS error invoking '{binary_name}' for '{uri_label}': {exc}"
        raise SecretResolutionError(msg) from exc

    if proc.returncode != 0:
        detail = _sanitize_cli_stderr(proc.stderr, binary_name)
        msg = (
            f"{tool_label} ('{binary_name}') failed to resolve "
            f"'{uri_label}' (exit {proc.returncode}): {detail}"
        )
        raise SecretResolutionError(msg)

    resolved_text = proc.stdout.rstrip("\r\n")
    if not resolved_text:
        msg = f"{tool_label} returned an empty value for '{uri_label}'."
        raise SecretResolutionError(msg)
    return SecretStr(resolved_text)


class EnvVarSecretProvider:
    """Zero-subprocess adapter resolving ``env://<VAR_NAME>`` from an environment mapping."""

    @jezz_callable
    def __init__(self, *, environ: Mapping[str, str] | None = None) -> None:
        self._environ: Mapping[str, str] | None = (
            MappingProxyType(dict(environ)) if environ is not None else None
        )

    @icontract.ensure(lambda result: len(result.get_secret_value()) >= 1)
    @jezz_callable
    def resolve_uri(
        self,
        env_uri: ShortText,
        *,
        reason: ShortText = DEFAULT_AGENT_REASON,
    ) -> GuardedSecretStr:
        """Read ``env://<VAR_NAME>`` without mutating ``os.environ`` or spawning processes."""
        parsed = parse_env_uri(env_uri)
        _validate_agent_reason_text(reason)
        source_map: Mapping[str, str] = self._environ if self._environ is not None else os.environ
        raw_val = source_map.get(parsed.var_name, "").strip()
        if not raw_val:
            msg = (
                f"Environment variable '{parsed.var_name}' referenced by '{parsed.uri}' "
                "is unset or empty."
            )
            raise SecretResolutionError(msg)
        return SecretStr(raw_val)


class ProtonPassSecretProvider:
    """Subprocess adapter resolving ``pass://`` URIs via ``pass-cli item view``."""

    @jezz_callable
    def __init__(
        self,
        *,
        binary_name: IdentifierStr = "pass-cli",
        timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 15.0,
    ) -> None:
        _validate_binary_name(binary_name, "pass-cli")
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
        return _run_secret_cli(
            (self._binary_name, "item", "view", parsed.uri),
            parsed.uri,
            "Proton Pass CLI",
            self._timeout_sec,
            extra_env={"PROTON_PASS_AGENT_REASON": clean_reason},
        )


class OnePasswordSecretProvider:
    """Subprocess adapter resolving ``op://`` URIs via 1Password CLI ``op read``."""

    @jezz_callable
    def __init__(
        self,
        *,
        binary_name: IdentifierStr = "op",
        timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 15.0,
    ) -> None:
        _validate_binary_name(binary_name, "1Password CLI")
        self._binary_name = binary_name
        self._timeout_sec = timeout_sec

    @icontract.ensure(lambda result: len(result.get_secret_value()) >= 1)
    @jezz_callable
    def resolve_uri(
        self,
        op_uri: ShortText,
        *,
        reason: ShortText = DEFAULT_AGENT_REASON,
    ) -> GuardedSecretStr:
        """Invoke ``op read`` for ``op://<vault>/<item>[/<section>]/<field>``."""
        parsed = parse_op_uri(op_uri)
        _validate_agent_reason_text(reason)
        return _run_secret_cli(
            (self._binary_name, "read", parsed.uri),
            parsed.uri,
            "1Password CLI",
            self._timeout_sec,
        )


def _build_keychain_command(
    parsed: ParsedKeychainUri,
    platform_name: str,
) -> tuple[tuple[str, ...], str]:
    plat = platform_name.lower()
    if plat == "darwin":
        return (
            ("security", "find-generic-password", "-s", parsed.service, "-a", parsed.account, "-w"),
            "macOS Keychain",
        )
    if plat.startswith("linux"):
        return (
            ("secret-tool", "lookup", "service", parsed.service, "account", parsed.account),
            "Freedesktop Secret Service",
        )
    msg = (
        f"Native 'keychain://' secret resolution is unsupported on platform '{platform_name}'; "
        "use 'env://', 'pass://', or 'op://'."
    )
    raise SecretResolutionError(msg)


class KeychainSecretProvider:
    """Native OS credential-store adapter resolving ``keychain://<service>/<account>``."""

    @jezz_callable
    def __init__(
        self,
        *,
        platform_name: IdentifierStr | None = None,
        timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 10.0,
    ) -> None:
        self._platform_name = platform_name if platform_name is not None else sys.platform
        self._timeout_sec = timeout_sec

    @icontract.ensure(lambda result: len(result.get_secret_value()) >= 1)
    @jezz_callable
    def resolve_uri(
        self,
        keychain_uri: ShortText,
        *,
        reason: ShortText = DEFAULT_AGENT_REASON,
    ) -> GuardedSecretStr:
        """Query macOS ``security`` or Linux ``secret-tool`` and return ``SecretStr``."""
        parsed = parse_keychain_uri(keychain_uri)
        _validate_agent_reason_text(reason)
        cmd_args, label = _build_keychain_command(parsed, self._platform_name)
        return _run_secret_cli(cmd_args, parsed.uri, label, self._timeout_sec)


class SecretProviderRegistry:
    """Immutable ``MappingProxyType`` registry routing ``<scheme>://`` URIs to providers."""

    @jezz_callable
    def __init__(
        self,
        providers: Annotated[
            Mapping[IdentifierStr, GuardedSecretProvider],
            Field(min_length=1, max_length=32),
        ],
    ) -> None:
        validated: dict[str, SecretProviderPort] = {}
        for scheme_key, adapter in providers.items():
            clean_scheme = _validate_scheme_name(scheme_key)
            validated[clean_scheme] = adapter
        self._providers: Mapping[str, SecretProviderPort] = MappingProxyType(validated)

    @property
    def schemes(self) -> frozenset[str]:
        """Return the immutable set of registered secret URI schemes."""
        return frozenset(self._providers)

    @property
    def providers(self) -> Mapping[str, SecretProviderPort]:
        """Return the read-only ``MappingProxyType`` of scheme-to-provider bindings."""
        return self._providers

    @jezz_callable
    def with_provider(
        self,
        scheme: IdentifierStr,
        provider: GuardedSecretProvider,
    ) -> SecretProviderRegistry:
        """Return a new ``SecretProviderRegistry`` binding ``scheme`` to ``provider``."""
        clean_scheme = _validate_scheme_name(scheme)
        merged = {**self._providers, clean_scheme: provider}
        return SecretProviderRegistry(merged)

    @icontract.ensure(lambda result: len(result.get_secret_value()) >= 1)
    @jezz_callable
    def resolve_uri(
        self,
        ref_uri: ShortText,
        *,
        reason: ShortText = DEFAULT_AGENT_REASON,
    ) -> GuardedSecretStr:
        """Validate ``ref_uri`` and dispatch resolution to the registered scheme adapter."""
        scheme = extract_secret_scheme(ref_uri)
        adapter = self._providers.get(scheme)
        if adapter is None:
            msg = (
                f"No SecretProviderPort registered for scheme '{scheme}://'; "
                f"available schemes: {sorted(self._providers)}."
            )
            raise SecretResolutionError(msg)
        validate_secret_uri(ref_uri, allowed_schemes=frozenset(self._providers))
        resolved = adapter.resolve_uri(ref_uri, reason=reason)
        if not resolved.get_secret_value():
            msg = f"Provider for scheme '{scheme}://' returned an empty secret for '{ref_uri}'."
            raise SecretResolutionError(msg)
        return resolved


@jezz_callable
def build_default_secret_registry(
    *,
    timeout_sec: Annotated[float, Field(gt=0.0, le=120.0, allow_inf_nan=False)] = 15.0,
) -> Annotated[SecretProviderRegistry, InstanceGuard(SecretProviderRegistry)]:
    """Construct the immutable default registry for ``env``, ``pass``, ``op``, and ``keychain``."""
    keychain_timeout = min(timeout_sec, 10.0)
    return SecretProviderRegistry(
        {
            "env": EnvVarSecretProvider(),
            "pass": ProtonPassSecretProvider(timeout_sec=timeout_sec),
            "op": OnePasswordSecretProvider(timeout_sec=timeout_sec),
            "keychain": KeychainSecretProvider(timeout_sec=keychain_timeout),
        }
    )


def _select_configured_secret_uri(
    provider_prefix: str,
    explicit_uri: str | None,
    ws_cfg: JezzSecretsConfig | None,
    user_cfg: JezzSecretsConfig | None,
) -> str | None:
    if explicit_uri is not None:
        return explicit_uri
    if not provider_prefix:
        return None
    if ws_cfg is not None:
        ws_hit = lookup_provider_secret_uri(ws_cfg, provider_prefix)
        if ws_hit is not None:
            return ws_hit
    if user_cfg is not None:
        return lookup_provider_secret_uri(user_cfg, provider_prefix)
    return None


def _effective_reason_and_timeout(
    reason_override: str | None,
    ws_cfg: JezzSecretsConfig | None,
    user_cfg: JezzSecretsConfig | None,
) -> tuple[str, float]:
    active_cfg = ws_cfg if ws_cfg is not None else user_cfg
    reason = (
        reason_override
        if reason_override is not None
        else (active_cfg.agent_reason if active_cfg is not None else DEFAULT_AGENT_REASON)
    )
    timeout = active_cfg.timeout_sec if active_cfg is not None else 15.0
    return reason, timeout


def _select_fallback_env_uri(model_name: str) -> str | None:
    family = canonical_provider_family(model_name)
    for env_var in PROVIDER_DEFAULT_ENV_VARS.get(family, ()):
        if os.environ.get(env_var, "").strip():
            return f"{_ENV_URI_PREFIX}{env_var}"
    return None


@jezz_callable
def resolve_model_api_key(
    model_name: IdentifierStr,
    *,
    explicit_secret_uri: ShortText | None = None,
    search_roots: Annotated[Sequence[Path], Field(max_length=16)] = (),
    user_config_dir: Path | None = None,
    provider: GuardedSecretProvider | None = None,
    reason_override: ShortText | None = None,
) -> GuardedSecretStr | None:
    """Resolve ``SecretStr`` via CLI URI -> workspace pyproject -> user config.toml -> env."""
    if (model_name == "test" or model_name.startswith("antigravity:")) and (
        explicit_secret_uri is None
    ):
        return None
    provider_prefix = model_name.split(":", maxsplit=1)[0] if ":" in model_name else ""
    roots = tuple(search_roots) if search_roots else (Path.cwd(),)
    ws_cfg = discover_secrets_config(roots, provider_name=provider_prefix or None)
    user_cfg = discover_user_secrets_config(user_config_dir=user_config_dir)
    target_uri = _select_configured_secret_uri(
        provider_prefix,
        explicit_secret_uri,
        ws_cfg,
        user_cfg,
    )
    active_reason, active_timeout = _effective_reason_and_timeout(
        reason_override,
        ws_cfg,
        user_cfg,
    )
    if target_uri is not None:
        active_provider: SecretProviderPort = (
            provider
            if provider is not None
            else build_default_secret_registry(timeout_sec=active_timeout)
        )
        return active_provider.resolve_uri(target_uri, reason=active_reason)
    fallback_env_uri = _select_fallback_env_uri(model_name)
    if fallback_env_uri is None:
        return None
    return EnvVarSecretProvider().resolve_uri(fallback_env_uri, reason=active_reason)
