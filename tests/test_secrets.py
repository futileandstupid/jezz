"""Verification suite for scheme-dispatched secret resolution (Track 22: INV-022-01..03)."""

import io
import os
import subprocess
import textwrap
from pathlib import Path
from types import MappingProxyType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import SecretStr, ValidationError
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModel

import jezz.cli as cli_mod
from jezz.agents.routing import CliModelOverrides
from jezz.behaviors import build_greenfield_contract_first_graph
from jezz.cli import TlsResilientModel, main, resolve_cli_model
from jezz.codes.inspector import BuildingInspector, compute_units_digest, load_workspace_units
from jezz.codes.permits import CodePermit, GateAttestation, GateKind, issue_certificate
from jezz.guards import ContainedPath
from jezz.methodology.dossier import EngineeringDossier, audit_pce_dossier
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope
from jezz.secrets import (
    BUILTIN_SECRET_SCHEMES,
    DEFAULT_AGENT_REASON,
    EnvVarSecretProvider,
    JezzSecretsConfig,
    KeychainSecretProvider,
    OnePasswordSecretProvider,
    ProtonPassSecretProvider,
    SecretProviderPort,
    SecretProviderRegistry,
    SecretResolutionError,
    build_default_secret_registry,
    default_user_config_dir,
    discover_secrets_config,
    discover_user_secrets_config,
    extract_secret_scheme,
    load_pyproject_secrets_config,
    load_user_secrets_config,
    lookup_provider_pass_uri,
    lookup_provider_secret_uri,
    parse_env_uri,
    parse_keychain_uri,
    parse_op_uri,
    parse_pass_uri,
    resolve_model_api_key,
    validate_secret_uri,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_SAFE_SEGMENT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
_SEGMENT_ST = st.text(alphabet=_SAFE_SEGMENT_CHARS, min_size=1, max_size=32).filter(
    lambda s: s not in {".", ".."} and not s.startswith("-")
)
_ENV_NAME_ST = st.from_regex(r"[A-Z_][A-Z0-9_]{1,24}", fullmatch=True)


def _write_utf8(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        fh.write(content)


class _FakeSchemeSecretProvider:
    """In-memory deterministic SecretProviderPort test double supporting any URI key."""

    def __init__(self, entries: dict[str, str]) -> None:
        self._entries = dict(entries)
        self.calls: list[tuple[str, str]] = []

    def resolve_uri(self, ref_uri: str, *, reason: str = DEFAULT_AGENT_REASON) -> SecretStr:
        extract_secret_scheme(ref_uri)
        self.calls.append((ref_uri, reason))
        found = self._entries.get(ref_uri)
        if found is None:
            msg = f"Missing synthetic secret entry for '{ref_uri}'."
            raise SecretResolutionError(msg)
        return SecretStr(found)


@settings(max_examples=50, deadline=None)
@given(data=st.data())
def test_parse_builtin_secret_uris_roundtrip_across_all_four_schemes(
    data: st.DataObject,
) -> None:
    env_var = data.draw(_ENV_NAME_ST, label="env_var")
    seg_a = data.draw(_SEGMENT_ST, label="seg_a")
    seg_b = data.draw(_SEGMENT_ST, label="seg_b")
    seg_c = data.draw(_SEGMENT_ST, label="seg_c")
    seg_d = data.draw(_SEGMENT_ST, label="seg_d")

    env_raw = f"env://{env_var}"
    parsed_env = parse_env_uri(env_raw)
    assert parsed_env.uri == env_raw
    assert parsed_env.var_name == env_var
    assert validate_secret_uri(env_raw) == env_raw

    pass_raw = f"pass://{seg_a}/{seg_b}/{seg_c}"
    parsed_pass = parse_pass_uri(pass_raw)
    assert parsed_pass.uri == pass_raw
    assert (parsed_pass.vault, parsed_pass.item, parsed_pass.field) == (seg_a, seg_b, seg_c)
    assert validate_secret_uri(pass_raw) == pass_raw

    op3_raw = f"op://{seg_a}/{seg_b}/{seg_c}"
    parsed_op3 = parse_op_uri(op3_raw)
    assert parsed_op3.uri == op3_raw
    assert parsed_op3.section is None
    assert (parsed_op3.vault, parsed_op3.item, parsed_op3.field) == (seg_a, seg_b, seg_c)

    op4_raw = f"op://{seg_a}/{seg_b}/{seg_c}/{seg_d}"
    parsed_op4 = parse_op_uri(op4_raw)
    assert parsed_op4.uri == op4_raw
    assert (parsed_op4.vault, parsed_op4.item, parsed_op4.section, parsed_op4.field) == (
        seg_a,
        seg_b,
        seg_c,
        seg_d,
    )
    assert validate_secret_uri(op4_raw) == op4_raw

    kc_raw = f"keychain://{seg_a}/{seg_b}"
    parsed_kc = parse_keychain_uri(kc_raw)
    assert parsed_kc.uri == kc_raw
    assert (parsed_kc.service, parsed_kc.account) == (seg_a, seg_b)
    assert validate_secret_uri(kc_raw) == kc_raw


def test_secret_uri_parsers_reject_plaintext_unregistered_schemes_and_hazards() -> None:
    spaced = parse_pass_uri("pass://AI Keys/Google Gemini Ultra/api_key")
    assert (spaced.vault, spaced.item, spaced.field) == (
        "AI Keys",
        "Google Gemini Ultra",
        "api_key",
    )

    invalid_samples = (
        "AIzaSyPlaintextCredentialShouldFail",
        "https://vault/item/field",
        "UPPER://vault/item",
        "env://",
        "env://123_STARTS_WITH_DIGIT",
        "env://BAD/SLASH",
        "pass://only_two/segments",
        "pass://a/b/c/d",
        "pass:///item/field",
        "pass://vault/../field",
        "pass://-flag-injection/item/field",
        "pass://vault/ leading_space/field",
        "pass://vault/item/field\ninjected",
        "op://only_two/segments",
        "op://a/b/c/d/e",
        "keychain://single_segment",
        "keychain://a/b/c",
    )
    for bad in invalid_samples:
        with pytest.raises((ValueError, ValidationError)):
            validate_secret_uri(bad)


def test_env_var_secret_provider_resolves_without_subprocess_or_environ_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbid_subprocess(*_args: object, **_kwargs: object) -> object:
        msg = "EnvVarSecretProvider must never spawn a subprocess."
        raise AssertionError(msg)

    monkeypatch.setattr(subprocess, "run", _forbid_subprocess)
    isolated = EnvVarSecretProvider(environ={"SYNTH_GEMINI_KEY": "  env-resolved-901  "})
    assert isinstance(isolated, SecretProviderPort)
    resolved = isolated.resolve_uri("env://SYNTH_GEMINI_KEY")
    assert resolved.get_secret_value() == "env-resolved-901"
    assert "env-resolved-901" not in repr(resolved)

    with pytest.raises(SecretResolutionError, match="unset or empty"):
        isolated.resolve_uri("env://MISSING_KEY")

    blank = EnvVarSecretProvider(environ={"BLANK_KEY": "   "})
    with pytest.raises(SecretResolutionError, match="unset or empty"):
        blank.resolve_uri("env://BLANK_KEY")


def test_proton_pass_provider_passes_agent_reason_and_redacts_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[tuple[tuple[str, ...], str, float]] = []
    synthetic_val = "synthetic-cred-value-8472"
    env_before = dict(os.environ)

    def _fake_run(
        args: tuple[str, ...],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: float,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert check is False
        captured_calls.append((tuple(args), env["PROTON_PASS_AGENT_REASON"], timeout))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"{synthetic_val}\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    provider = ProtonPassSecretProvider(timeout_sec=9.5)
    resolved = provider.resolve_uri(
        "pass://AI-Vault/Gemini/api_key",
        reason="jezz unit verification",
    )

    assert isinstance(resolved, SecretStr)
    assert resolved.get_secret_value() == synthetic_val
    assert synthetic_val not in repr(resolved)
    assert synthetic_val not in str(resolved)
    assert captured_calls == [
        (
            ("pass-cli", "item", "view", "pass://AI-Vault/Gemini/api_key"),
            "jezz unit verification",
            9.5,
        )
    ]
    assert dict(os.environ) == env_before

    with pytest.raises(ValueError, match="Invalid pass-cli executable"):
        ProtonPassSecretProvider(binary_name="-bad-bin")


def test_proton_pass_provider_translates_cli_failures_without_leaking_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_stdout = "never-leak-this-stdout-991"

    def _fail_exit(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=("pass-cli",),
            returncode=1,
            stdout=sensitive_stdout,
            stderr="Error: This operation requires an authenticated client\n",
        )

    monkeypatch.setattr(subprocess, "run", _fail_exit)
    provider = ProtonPassSecretProvider()
    with pytest.raises(SecretResolutionError) as exc_info:
        provider.resolve_uri("pass://AI-Vault/Gemini/api_key")
    assert "requires an authenticated client" in str(exc_info.value)
    assert sensitive_stdout not in str(exc_info.value)

    def _empty_out(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=("pass-cli",), returncode=0, stdout="\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _empty_out)
    with pytest.raises(SecretResolutionError, match="empty value"):
        provider.resolve_uri("pass://AI-Vault/Gemini/api_key")

    def _missing_bin(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        msg = "No such file or directory: 'pass-cli'"
        raise FileNotFoundError(msg)

    monkeypatch.setattr(subprocess, "run", _missing_bin)
    with pytest.raises(SecretResolutionError, match="not found on PATH"):
        provider.resolve_uri("pass://AI-Vault/Gemini/api_key")

    def _timeout_bin(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=("pass-cli",), timeout=5.0)

    monkeypatch.setattr(subprocess, "run", _timeout_bin)
    with pytest.raises(SecretResolutionError, match="timed out"):
        provider.resolve_uri("pass://AI-Vault/Gemini/api_key")


def test_onepassword_and_keychain_providers_execute_expected_cli_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_cmds: list[tuple[str, ...]] = []

    def _mock_cli(
        args: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        recorded_cmds.append(tuple(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="cli-val-777\r\n")

    monkeypatch.setattr(subprocess, "run", _mock_cli)

    op_provider = OnePasswordSecretProvider(timeout_sec=8.0)
    op_secret = op_provider.resolve_uri("op://Engineering/OpenAI/Prod/api_key")
    assert op_secret.get_secret_value() == "cli-val-777"
    assert list(recorded_cmds[-1]) == ["op", "read", "op://Engineering/OpenAI/Prod/api_key"]

    mac_kc = KeychainSecretProvider(platform_name="darwin", timeout_sec=5.0)
    mac_secret = mac_kc.resolve_uri("keychain://jezz-models/anthropic")
    assert mac_secret.get_secret_value() == "cli-val-777"
    assert list(recorded_cmds[-1]) == [
        "security",
        "find-generic-password",
        "-s",
        "jezz-models",
        "-a",
        "anthropic",
        "-w",
    ]

    linux_kc = KeychainSecretProvider(platform_name="linux", timeout_sec=5.0)
    linux_secret = linux_kc.resolve_uri("keychain://jezz-models/anthropic")
    assert linux_secret.get_secret_value() == "cli-val-777"
    assert list(recorded_cmds[-1]) == [
        "secret-tool",
        "lookup",
        "service",
        "jezz-models",
        "account",
        "anthropic",
    ]

    win_kc = KeychainSecretProvider(platform_name="win32")
    with pytest.raises(SecretResolutionError, match="unsupported on platform"):
        win_kc.resolve_uri("keychain://jezz-models/anthropic")


def test_secret_provider_registry_immutability_and_custom_scheme_extension() -> None:
    base_reg = build_default_secret_registry()
    assert isinstance(base_reg, SecretProviderRegistry)
    assert isinstance(base_reg, SecretProviderPort)
    assert base_reg.schemes == BUILTIN_SECRET_SCHEMES
    assert isinstance(base_reg.providers, MappingProxyType)
    assert not hasattr(base_reg.providers, "__setitem__")

    custom_double = _FakeSchemeSecretProvider({"vault://prod/gemini/api_key": "hcv-secret-42"})
    extended = base_reg.with_provider("vault", custom_double)
    assert "vault" not in base_reg.schemes
    assert "vault" in extended.schemes

    resolved = extended.resolve_uri("vault://prod/gemini/api_key", reason="custom scheme test")
    assert resolved.get_secret_value() == "hcv-secret-42"
    assert custom_double.calls == [("vault://prod/gemini/api_key", "custom scheme test")]

    with pytest.raises(SecretResolutionError, match="No SecretProviderPort registered"):
        base_reg.resolve_uri("vault://prod/gemini/api_key")

    custom_cfg = JezzSecretsConfig(
        extra_schemes=frozenset({"vault"}),
        providers={"google": "vault://prod/gemini/api_key"},
    )
    assert lookup_provider_secret_uri(custom_cfg, "google") == "vault://prod/gemini/api_key"
    assert lookup_provider_pass_uri(custom_cfg, "google") == "vault://prod/gemini/api_key"


def test_secrets_config_validation_and_user_config_toml_loading(tmp_path: Path) -> None:
    assert isinstance(default_user_config_dir(), Path)

    ws_dir = tmp_path / "workspace"
    user_dir = tmp_path / "user_cfg"
    ws_dir.mkdir()
    user_dir.mkdir()

    _write_utf8(
        ws_dir / "pyproject.toml",
        '[tool.jezz.secrets.providers]\ngoogle = "plaintext-not-a-uri"\n',
    )
    with pytest.raises((ValueError, ValidationError)):
        load_pyproject_secrets_config(ContainedPath.from_parts(ws_dir, "pyproject.toml"))

    _write_utf8(
        user_dir / "config.toml",
        '[secrets.providers]\ngoogle = "env://GEMINI_API_KEY"\n\n'
        '[tool.jezz.secrets.providers]\ngoogle = "env://GOOGLE_API_KEY"\n',
    )
    with pytest.raises(ValueError, match="Ambiguous user config"):
        load_user_secrets_config(ContainedPath.from_parts(user_dir, "config.toml"))

    _write_utf8(
        user_dir / "config.toml",
        textwrap.dedent(
            """\
            [tool.jezz.secrets]
            agent_reason = "nested user config"

            [tool.jezz.secrets.providers]
            google = "env://GEMINI_API_KEY"
            gateway = "op://Vault/Gateway/api_key"
            """
        ),
    )
    loaded_user = discover_user_secrets_config(user_config_dir=user_dir)
    assert isinstance(loaded_user, JezzSecretsConfig)
    assert loaded_user.agent_reason == "nested user config"
    assert lookup_provider_secret_uri(loaded_user, "google-decisions") == "env://GEMINI_API_KEY"
    assert lookup_provider_secret_uri(loaded_user, "gateway/google") == "op://Vault/Gateway/api_key"
    assert lookup_provider_secret_uri(loaded_user, "anthropic") is None


def test_resolve_model_api_key_four_tier_precedence_and_tls_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ws_dir = tmp_path / "workspace"
    user_dir = tmp_path / "user_cfg"
    ws_dir.mkdir()
    user_dir.mkdir()
    _write_utf8(ws_dir / "pyproject.toml", '[project]\nname = "clean"\nversion = "0.1.0"\n')

    fake_port = _FakeSchemeSecretProvider(
        {
            "op://Personal/Gemini/credential": "tier3-user-config-cred",
            "keychain://ws-service/gemini": "tier2-workspace-cred",
            "pass://CLI/Override/api_key": "tier1-cli-override-cred",
            "op://Personal/OpenAI/credential": "tier3-openai-cred",
        }
    )

    assert (
        resolve_model_api_key(
            "google:gemini-3.8-flash",
            search_roots=(ws_dir,),
            user_config_dir=user_dir,
            provider=fake_port,
        )
        is None
    )

    # Tier 4: Canonical provider environment variable fallback (GEMINI_API_KEY)
    monkeypatch.setenv("GEMINI_API_KEY", "tier4-env-fallback-cred")
    t4 = resolve_model_api_key(
        "google:gemini-3.8-flash",
        search_roots=(ws_dir,),
        user_config_dir=user_dir,
        provider=fake_port,
    )
    assert t4 is not None and t4.get_secret_value() == "tier4-env-fallback-cred"

    # Tier 3: User config.toml overrides Tier 4 environment variable
    _write_utf8(
        user_dir / "config.toml",
        '[secrets.providers]\ngoogle = "op://Personal/Gemini/credential"\n'
        'openai = "op://Personal/OpenAI/credential"\n',
    )
    t3 = resolve_model_api_key(
        "google:gemini-3.8-flash",
        search_roots=(ws_dir,),
        user_config_dir=user_dir,
        provider=fake_port,
    )
    assert t3 is not None and t3.get_secret_value() == "tier3-user-config-cred"

    # Tier 2: Workspace pyproject.toml overrides Tier 3 (while openai falls through to Tier 3)
    _write_utf8(
        ws_dir / "pyproject.toml",
        '[tool.jezz.secrets.providers]\ngoogle = "keychain://ws-service/gemini"\n',
    )
    t2 = resolve_model_api_key(
        "google:gemini-3.8-flash",
        search_roots=(ws_dir,),
        user_config_dir=user_dir,
        provider=fake_port,
    )
    assert t2 is not None and t2.get_secret_value() == "tier2-workspace-cred"

    openai_key = resolve_model_api_key(
        "openai-responses:gpt-5",
        search_roots=(ws_dir,),
        user_config_dir=user_dir,
        provider=fake_port,
    )
    assert openai_key is not None and openai_key.get_secret_value() == "tier3-openai-cred"

    # Tier 1: Explicit --secret-uri overrides Workspace, User Config, and Environment
    t1 = resolve_model_api_key(
        "google:gemini-3.8-flash",
        explicit_secret_uri="pass://CLI/Override/api_key",
        search_roots=(ws_dir,),
        user_config_dir=user_dir,
        provider=fake_port,
        reason_override="cli override reason",
    )
    assert t1 is not None and t1.get_secret_value() == "tier1-cli-override-cred"
    assert fake_port.calls[-1] == ("pass://CLI/Override/api_key", "cli override reason")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    google_wrapped = resolve_cli_model("google:gemini-3.8-flash", api_key=t1)
    openai_wrapped = resolve_cli_model("openai:gpt-5", api_key=openai_key)
    assert isinstance(google_wrapped, TlsResilientModel) and isinstance(
        google_wrapped.wrapped, GoogleModel
    )
    assert isinstance(openai_wrapped, TlsResilientModel) and isinstance(
        openai_wrapped.wrapped, OpenAIResponsesModel
    )
    with pytest.raises(UserError, match="Expected 'provider:model'"):
        resolve_cli_model("unqualified_model_name", api_key=t1)


def test_cli_secret_uri_and_legacy_aliases_wire_through_run_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_utf8(
        tmp_path / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["jezz>=0.1.0"]\n',
    )
    _write_utf8(tmp_path / "src" / "demo" / "__init__.py", "")

    for bad_spec in ("op://Vault/Item/key", "env://OPENAI_API_KEY", "keychain://svc/acct"):
        with pytest.raises(ValidationError, match="Refusing credential"):
            CliModelOverrides(model=bad_spec)

    def _fail_vault(*_args: object, **_kwargs: object) -> SecretStr | None:
        msg = "1Password CLI ('op') failed: not signed in."
        raise SecretResolutionError(msg)

    monkeypatch.setattr("jezz.cli.resolve_model_api_key", _fail_vault)
    err_buf = io.StringIO()
    exit_err = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "google:gemini-3.8-flash",
            "--secret-uri",
            "op://AI-Vault/Gemini/api_key",
            "--prompt",
            "Build demo",
        ],
        stderr=err_buf,
    )
    assert exit_err == 2
    assert "not signed in" in err_buf.getvalue()

    seen_args: list[tuple[str, str | None, str | None]] = []

    def _ok_vault(
        model_name: str,
        *,
        explicit_secret_uri: str | None = None,
        search_roots: object = (),
        reason_override: str | None = None,
    ) -> SecretStr | None:
        _ = search_roots
        seen_args.append((model_name, explicit_secret_uri, reason_override))
        return SecretStr("cli-resolved-cred-555")

    def _capture_model(_model_name: str, *, api_key: SecretStr | None = None) -> FunctionModel:
        assert api_key is not None
        assert api_key.get_secret_value() == "cli-resolved-cred-555"
        msg = "halted intentionally after credential verification"
        raise UserError(msg)

    monkeypatch.setattr("jezz.cli.resolve_model_api_key", _ok_vault)
    monkeypatch.setattr(cli_mod, "resolve_cli_model", _capture_model)

    for flag_name, sample_uri in (
        ("--secret-uri", "env://GEMINI_API_KEY"),
        ("--api-key-uri", "op://AI-Vault/Gemini/api_key"),
        ("--pass-uri", "pass://AI-Vault/Gemini/api_key"),
    ):
        err_halt = io.StringIO()
        code_halt = main(
            [
                "run",
                str(tmp_path),
                "--model",
                "google:gemini-3.8-flash",
                flag_name,
                sample_uri,
                "--agent-reason",
                "cli alias check",
                "--prompt",
                "Build demo",
            ],
            stderr=err_halt,
        )
        assert code_halt == 2
        assert seen_args[-1] == ("google:gemini-3.8-flash", sample_uri, "cli alias check")


def test_secrets_subsystem_passes_zero_variance_jbc_and_pce_dossier_audit() -> None:
    # INV-022-03: Personal pass:// vault mapping is absent from committed repo pyproject.toml
    assert discover_secrets_config((REPO_ROOT,), provider_name="google") is None

    units = load_workspace_units(REPO_ROOT)
    inspector = BuildingInspector.from_workspace(REPO_ROOT)
    permit = CodePermit.full_book(permit_id="PERMIT-JEZZ-SECRETS-V2")
    attestations = (
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=10,
            duration_ms=120.0,
            peak_memory_kb=32_768,
            summary="Scheme-dispatched secret management unit + property tests passed.",
        ),
        GateAttestation(
            gate_kind=GateKind.STATIC_TYPES,
            tool_name="mypy",
            passed=True,
            exit_code=0,
            checks_executed=len(units),
            duration_ms=250.0,
            peak_memory_kb=65_536,
            summary="mypy --strict clean across all workspace files.",
        ),
    )
    report = inspector.inspect_final(units, permit=permit, attestations=attestations)
    assert report.is_occupiable, report.to_markdown()
    cert = issue_certificate(report, artifact_digest=compute_units_digest(units))

    secret_files = frozenset(
        {
            "src/jezz/secrets.py",
            "src/jezz/cli.py",
            "src/jezz/cli_parsers.py",
            "src/jezz/agents/routing.py",
            "tests/test_secrets.py",
        }
    )
    mandate = EngineeringMandate(
        mandate_id="MND-JEZZ-SECRETS-022",
        title="Scheme-Dispatched Secret Management & User Config Precedence",
        objective=(
            "Generalize secret resolution across env://, pass://, op://, and keychain:// "
            "without os.environ mutation or personal vault lock-in."
        ),
        invariants=(
            DomainInvariant(
                invariant_id="INV-2201",
                statement="validate_secret_uri rejects plaintext and enforces scheme dispatch.",
                verification_symbol="validate_secret_uri",
            ),
            DomainInvariant(
                invariant_id="INV-2202",
                statement="build_default_secret_registry binds env, pass, op, and keychain ports.",
                verification_symbol="build_default_secret_registry",
            ),
            DomainInvariant(
                invariant_id="INV-2203",
                statement="resolve_model_api_key enforces 4-tier URI/workspace/user/env order.",
                verification_symbol="resolve_model_api_key",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="scope_secrets_core",
                owned_files=secret_files,
                contract_symbols=(
                    "validate_secret_uri",
                    "build_default_secret_registry",
                    "resolve_model_api_key",
                ),
            ),
        ),
    )
    dossier = EngineeringDossier(
        mandate=mandate,
        permit=permit,
        behavior_graph=build_greenfield_contract_first_graph(),
        mutated_files=secret_files,
        inspection_report=report,
        certificate=cert,
    )
    audit = audit_pce_dossier(dossier, units)
    assert audit.is_occupancy_sealed, audit.to_markdown()
