"""Verification suite for Proton Pass CLI secret resolution and JBC-SEC-002/OBS-003 containment."""

import io
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import SecretStr, ValidationError
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModel

import jezz.cli as cli_mod
from jezz.behaviors import build_greenfield_contract_first_graph
from jezz.cli import TlsResilientModel, main, resolve_cli_model
from jezz.codes.inspector import BuildingInspector, compute_units_digest, load_workspace_units
from jezz.codes.permits import CodePermit, GateAttestation, GateKind, issue_certificate
from jezz.guards import ContainedPath
from jezz.methodology.dossier import EngineeringDossier, audit_pce_dossier
from jezz.methodology.mandate import DomainInvariant, EngineeringMandate, ModuleWriteScope
from jezz.secrets import (
    DEFAULT_AGENT_REASON,
    JezzSecretsConfig,
    ProtonPassSecretProvider,
    SecretResolutionError,
    discover_secrets_config,
    load_pyproject_secrets_config,
    lookup_provider_pass_uri,
    parse_pass_uri,
    resolve_model_api_key,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_SAFE_SEGMENT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
_SEGMENT_ST = st.text(alphabet=_SAFE_SEGMENT_CHARS, min_size=1, max_size=32).filter(
    lambda s: s not in {".", ".."} and not s.startswith("-")
)


def _write_utf8(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        fh.write(content)


class _FakeSecretProvider:
    """In-memory deterministic SecretProviderPort test double."""

    def __init__(self, entries: dict[str, str]) -> None:
        self._entries = dict(entries)
        self.calls: list[tuple[str, str]] = []

    def resolve_uri(self, pass_uri: str, *, reason: str = DEFAULT_AGENT_REASON) -> SecretStr:
        parsed = parse_pass_uri(pass_uri)
        self.calls.append((parsed.uri, reason))
        found = self._entries.get(parsed.uri)
        if found is None:
            msg = f"Missing synthetic vault entry for '{parsed.uri}'."
            raise SecretResolutionError(msg)
        return SecretStr(found)


@settings(max_examples=50, deadline=None)
@given(data=st.data())
def test_parse_pass_uri_roundtrips_valid_three_segment_uris(data: st.DataObject) -> None:
    vault = data.draw(_SEGMENT_ST, label="vault")
    item = data.draw(_SEGMENT_ST, label="item")
    field = data.draw(_SEGMENT_ST, label="field")
    raw = f"pass://{vault}/{item}/{field}"

    parsed = parse_pass_uri(raw)
    assert parsed.uri == raw
    assert parsed.vault == vault
    assert parsed.item == item
    assert parsed.field == field


def test_parse_pass_uri_supports_internal_spaces_and_rejects_hazards() -> None:
    spaced = parse_pass_uri("pass://AI Keys/Google Gemini Ultra/api_key")
    assert spaced.vault == "AI Keys"
    assert spaced.item == "Google Gemini Ultra"
    assert spaced.field == "api_key"

    invalid_samples = (
        "AIzaSyPlaintextCredentialShouldFail",
        "https://vault/item/field",
        "pass://only_two/segments",
        "pass://a/b/c/d",
        "pass:///item/field",
        "pass://vault/../field",
        "pass://-flag-injection/item/field",
        "pass://vault/ leading_space/field",
        "pass://vault/item/field\ninjected",
    )
    for bad in invalid_samples:
        with pytest.raises((ValueError, ValidationError)):
            parse_pass_uri(bad)


def test_pyproject_secrets_config_rejects_plaintext_and_resolves_aliases(tmp_path: Path) -> None:
    bad_toml = textwrap.dedent(
        """\
        [tool.jezz.secrets.providers]
        google = "plaintext-credential-not-a-uri"
        """
    )
    _write_utf8(tmp_path / "pyproject.toml", bad_toml)
    with pytest.raises((ValueError, ValidationError)):
        load_pyproject_secrets_config(ContainedPath.from_parts(tmp_path, "pyproject.toml"))

    good_toml = textwrap.dedent(
        """\
        [tool.jezz.secrets]
        agent_reason = "jezz benchmark credential read"
        timeout_sec = 12.0

        [tool.jezz.secrets.providers]
        google = "pass://AI-Vault/Gemini/api_key"
        openai = "pass://AI-Vault/OpenAI/api_key"
        gateway = "pass://AI-Vault/Gateway/api_key"
        """
    )
    _write_utf8(tmp_path / "pyproject.toml", good_toml)
    cfg = discover_secrets_config((tmp_path,), provider_name="google")
    assert isinstance(cfg, JezzSecretsConfig)
    assert cfg.agent_reason == "jezz benchmark credential read"
    assert lookup_provider_pass_uri(cfg, "google") == "pass://AI-Vault/Gemini/api_key"
    assert lookup_provider_pass_uri(cfg, "google-decisions") == "pass://AI-Vault/Gemini/api_key"
    assert lookup_provider_pass_uri(cfg, "openai-responses") == "pass://AI-Vault/OpenAI/api_key"
    assert lookup_provider_pass_uri(cfg, "gateway/google") == "pass://AI-Vault/Gateway/api_key"
    assert lookup_provider_pass_uri(cfg, "anthropic") is None


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


def test_resolve_model_api_key_precedence_and_tls_model_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ws_dir = tmp_path / "scratch_ws"
    cfg_dir = tmp_path / "project_cfg"
    ws_dir.mkdir()
    _write_utf8(
        cfg_dir / "pyproject.toml",
        textwrap.dedent(
            """\
            [tool.jezz.secrets]
            agent_reason = "project default reason"

            [tool.jezz.secrets.providers]
            google = "pass://VaultA/Gemini/api_key"
            openai = "pass://VaultA/OpenAI/api_key"
            """
        ),
    )
    fake_port = _FakeSecretProvider(
        {
            "pass://VaultA/Gemini/api_key": "vault-google-cred-111",
            "pass://VaultB/Override/api_key": "override-google-cred-222",
            "pass://VaultA/OpenAI/api_key": "vault-openai-cred-333",
        }
    )

    assert resolve_model_api_key("test", search_roots=(ws_dir, cfg_dir), provider=fake_port) is None

    from_toml = resolve_model_api_key(
        "google:gemini-3.8-flash",
        search_roots=(ws_dir, cfg_dir),
        provider=fake_port,
    )
    assert from_toml is not None
    assert from_toml.get_secret_value() == "vault-google-cred-111"

    from_flag = resolve_model_api_key(
        "google:gemini-3.8-flash",
        explicit_pass_uri="pass://VaultB/Override/api_key",
        search_roots=(ws_dir, cfg_dir),
        provider=fake_port,
        reason_override="cli override reason",
    )
    assert from_flag is not None
    assert from_flag.get_secret_value() == "override-google-cred-222"
    assert fake_port.calls[-1] == ("pass://VaultB/Override/api_key", "cli override reason")

    # Verify TlsResilientModel constructs GoogleModel & OpenAIResponsesModel with zero env vars
    google_wrapped = resolve_cli_model("google:gemini-3.8-flash", api_key=from_flag)
    assert isinstance(google_wrapped, TlsResilientModel)
    assert isinstance(google_wrapped.wrapped, GoogleModel)
    assert os.environ.get("GOOGLE_API_KEY") is None
    assert os.environ.get("GEMINI_API_KEY") is None

    openai_key = resolve_model_api_key(
        "openai:gpt-5",
        search_roots=(cfg_dir,),
        provider=fake_port,
    )
    openai_wrapped = resolve_cli_model("openai:gpt-5", api_key=openai_key)
    assert isinstance(openai_wrapped, TlsResilientModel)
    assert isinstance(openai_wrapped.wrapped, OpenAIResponsesModel)
    assert os.environ.get("OPENAI_API_KEY") is None

    with pytest.raises(UserError, match="Expected 'provider:model'"):
        resolve_cli_model("unqualified_model_name", api_key=from_flag)


def test_cli_run_wires_api_key_uri_and_reports_vault_errors_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_utf8(
        tmp_path / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["jezz>=0.1.0"]\n',
    )
    _write_utf8(tmp_path / "src" / "demo" / "__init__.py", "")

    def _fail_vault(*_args: object, **_kwargs: object) -> SecretStr | None:
        msg = "Proton Pass CLI ('pass-cli') failed: no active agent session."
        raise SecretResolutionError(msg)

    monkeypatch.setattr("jezz.cli.resolve_model_api_key", _fail_vault)
    err_buf = io.StringIO()
    exit_err = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "google:gemini-3.8-flash",
            "--api-key-uri",
            "pass://AI-Vault/Gemini/api_key",
            "--agent-reason",
            "cli test audit",
            "--prompt",
            "Build demo",
        ],
        stderr=err_buf,
    )
    assert exit_err == 2
    assert "no active agent session" in err_buf.getvalue()

    seen_args: list[tuple[str, str | None, str | None]] = []

    def _ok_vault(
        model_name: str,
        *,
        explicit_pass_uri: str | None = None,
        search_roots: object = (),
        reason_override: str | None = None,
    ) -> SecretStr | None:
        _ = search_roots
        seen_args.append((model_name, explicit_pass_uri, reason_override))
        return SecretStr("cli-resolved-cred-555")

    captured_models: list[tuple[str, str]] = []

    def _capture_model(model_name: str, *, api_key: SecretStr | None = None) -> FunctionModel:
        assert api_key is not None
        captured_models.append((model_name, api_key.get_secret_value()))
        msg = "halted intentionally after credential verification"
        raise UserError(msg)

    monkeypatch.setattr("jezz.cli.resolve_model_api_key", _ok_vault)
    monkeypatch.setattr(cli_mod, "resolve_cli_model", _capture_model)
    err_halt = io.StringIO()
    code_halt = main(
        [
            "run",
            str(tmp_path),
            "--model",
            "google:gemini-3.8-flash",
            "--api-key-uri",
            "pass://AI-Vault/Gemini/api_key",
            "--agent-reason",
            "cli test audit",
            "--prompt",
            "Build demo",
        ],
        stderr=err_halt,
    )
    assert code_halt == 2
    assert seen_args == [
        ("google:gemini-3.8-flash", "pass://AI-Vault/Gemini/api_key", "cli test audit")
    ]
    assert captured_models == [("google:gemini-3.8-flash", "cli-resolved-cred-555")]


def test_secrets_subsystem_passes_zero_variance_jbc_and_pce_dossier_audit() -> None:
    repo_cfg = discover_secrets_config((REPO_ROOT,), provider_name="google")
    assert repo_cfg is not None
    assert lookup_provider_pass_uri(repo_cfg, "google") == (
        "pass://AI-Keys/Gemini Developer API/API Key"
    )

    units = load_workspace_units(REPO_ROOT)
    inspector = BuildingInspector.from_workspace(REPO_ROOT)
    permit = CodePermit.full_book(permit_id="PERMIT-JEZZ-SECRETS")
    attestations = (
        GateAttestation(
            gate_kind=GateKind.DYNAMIC_SUITE,
            tool_name="pytest",
            passed=True,
            exit_code=0,
            checks_executed=7,
            duration_ms=120.0,
            peak_memory_kb=32_768,
            summary="Proton Pass secret management unit + property tests passed.",
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
            "tests/test_secrets.py",
        }
    )
    mandate = EngineeringMandate(
        mandate_id="MND-JEZZ-SECRETS-001",
        title="Proton Pass CLI Secret Management with JBC-SEC-002/OBS-003 Containment",
        objective="Resolve model API credentials via pass-cli without os.environ mutation.",
        invariants=(
            DomainInvariant(
                invariant_id="INV-001",
                statement="parse_pass_uri accepts only canonical 3-segment pass:// references.",
                verification_symbol="parse_pass_uri",
            ),
            DomainInvariant(
                invariant_id="INV-002",
                statement="ProtonPassSecretProvider returns redacted SecretStr with audit reason.",
                verification_symbol="ProtonPassSecretProvider",
            ),
        ),
        write_scopes=(
            ModuleWriteScope(
                scope_id="jezz_secrets_scope",
                owned_files=secret_files,
                contract_symbols=(
                    "parse_pass_uri",
                    "ProtonPassSecretProvider",
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
