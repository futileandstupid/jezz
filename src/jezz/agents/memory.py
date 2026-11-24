"""Persistent cross-mandate workspace memory (``.jezz/memory.json``) and consultation grounding."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final

import icontract
from pydantic import Field

from jezz.agents.env_introspect import inspect_polyglot_package_api
from jezz.agents.schemas import (
    CuratedMemoryDigest,
    SealedMandateMemoryEntry,
    WorkspaceMemoryLedger,
)
from jezz.behaviors.runtime import AgentExecutionState
from jezz.codes.codebook import CODE_BOOK
from jezz.codes.polyglot_ir import SupportedLanguage
from jezz.guards import (
    BoundedText,
    ContainedPath,
    IdentifierStr,
    ShortText,
    jezz_callable,
    read_bounded_text,
)
from jezz.methodology.mandate import ConsultTargetStr, EngineeringMandate

WORKSPACE_MEMORY_REL_PATH: Final[str] = ".jezz/memory.json"
_CONSULT_ECOSYSTEM_PREFIXES: Final[Mapping[str, SupportedLanguage]] = MappingProxyType(
    {
        "py": SupportedLanguage.PYTHON,
        "python": SupportedLanguage.PYTHON,
        "npm": SupportedLanguage.TYPESCRIPT,
        "ts": SupportedLanguage.TYPESCRIPT,
        "crate": SupportedLanguage.RUST,
        "rust": SupportedLanguage.RUST,
        "go": SupportedLanguage.GO,
    }
)


def _dedupe_bounded_strings(
    items: Sequence[str],
    *,
    max_items: int,
    max_len: int = 1024,
) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in items:
        cleaned = raw.strip()[:max_len]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return tuple(ordered[-max_items:])


@jezz_callable
def load_workspace_memory(repo_root: Path) -> WorkspaceMemoryLedger:
    """Load ``.jezz/memory.json`` from ``repo_root``, returning an empty ledger when absent."""
    target = ContainedPath.from_parts(repo_root.resolve(), WORKSPACE_MEMORY_REL_PATH)
    if not target.resolved_path.is_file():
        return WorkspaceMemoryLedger()
    raw_json = read_bounded_text(target, max_bytes=524_288)
    return WorkspaceMemoryLedger.model_validate_json(raw_json)


@jezz_callable
def save_workspace_memory(
    repo_root: Path,
    ledger: WorkspaceMemoryLedger,
) -> ContainedPath:
    """Persist ``ledger`` atomically inside ``repo_root/.jezz/memory.json``."""
    target = ContainedPath.from_parts(repo_root.resolve(), WORKSPACE_MEMORY_REL_PATH)
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (ledger.model_dump_json(indent=2) + "\n").encode("utf-8")
    with target.resolved_path.open("wb") as fh:
        fh.write(payload)
    return target


@icontract.ensure(
    lambda mandate, result: any(m.mandate_id == mandate.mandate_id for m in result.sealed_mandates)
)
@jezz_callable
def record_certified_mandate_memory(
    ledger: WorkspaceMemoryLedger,
    mandate: EngineeringMandate,
    *,
    artifact_digest: IdentifierStr,
    architectural_notes: Annotated[Sequence[ShortText], Field(max_length=64)] = (),
    falsified_paths: Annotated[Sequence[ShortText], Field(max_length=512)] = (),
) -> WorkspaceMemoryLedger:
    """Append or refresh ``mandate`` in ``ledger`` while retaining prior negations and notes."""
    entry = SealedMandateMemoryEntry(
        mandate_id=mandate.mandate_id,
        title=mandate.title,
        artifact_digest=artifact_digest,
        invariants=tuple(
            f"{inv.invariant_id} ({inv.verification_symbol}): {inv.statement}"[:1024]
            for inv in mandate.invariants
        ),
        contract_symbols=tuple(
            sym for scope in mandate.write_scopes for sym in scope.contract_symbols
        )[:256],
    )
    retained = [m for m in ledger.sealed_mandates if m.mandate_id != mandate.mandate_id]
    retained.append(entry)
    merged_notes = _dedupe_bounded_strings(
        (*ledger.architectural_notes, *architectural_notes),
        max_items=128,
    )
    merged_falsified = _dedupe_bounded_strings(
        (*ledger.falsified_paths, *falsified_paths),
        max_items=512,
    )
    return WorkspaceMemoryLedger(
        sealed_mandates=tuple(retained[-256:]),
        architectural_notes=merged_notes,
        falsified_paths=merged_falsified,
    )


@jezz_callable
def format_memory_prompt_context(ledger: WorkspaceMemoryLedger) -> BoundedText:
    """Render concise longitudinal memory context for downstream PCE specialist prompts."""
    if not ledger.sealed_mandates and not ledger.architectural_notes and not ledger.falsified_paths:
        return ""
    lines: list[str] = ["\nPersistent Workspace Memory (.jezz/memory.json):"]
    for sealed in ledger.sealed_mandates[-8:]:
        syms = ", ".join(sealed.contract_symbols[:12])
        lines.append(
            f"- Sealed {sealed.mandate_id} ({sealed.title}, sha256:{sealed.artifact_digest[:12]}): "
            f"symbols=[{syms}]"
        )
    for note in ledger.architectural_notes[-8:]:
        lines.append(f"- Architectural Note: {note}")
    for neg in ledger.falsified_paths[-8:]:
        lines.append(f"- Prior Falsified Path (DO NOT REPEAT): {neg}")
    return "\n".join(lines)[:8_000]


@jezz_callable
def validate_curated_digest_invariants(
    mandate: EngineeringMandate,
    prior_falsified_paths: Annotated[Sequence[ShortText], Field(max_length=512)],
    digest: CuratedMemoryDigest,
) -> ShortText | None:
    """Verify ``BHV-07 CURATE_MEMORY`` preserves all active invariants and falsified negations."""
    expected_inv = {inv.invariant_id for inv in mandate.invariants}
    missing_inv = sorted(expected_inv - set(digest.retained_invariants))
    if missing_inv:
        return f"CuratedMemoryDigest dropped active mandate invariant_id(s): {missing_inv}."
    retained_neg = set(digest.retained_falsified_paths)
    missing_neg = [p for p in prior_falsified_paths if p not in retained_neg]
    if missing_neg:
        return (
            "CuratedMemoryDigest evicted falsified_paths negation(s); all prior negations "
            f"must be retained (missing {len(missing_neg)} item(s))."
        )
    return None


@jezz_callable
def compact_world_model_deterministically(
    state: AgentExecutionState,
    mandate: EngineeringMandate,
) -> CuratedMemoryDigest:
    """Deterministically compact ``state.world_model`` while preserving invariants and negations."""
    compacted_obs = _dedupe_bounded_strings(state.world_model.observations, max_items=12)
    notes = _dedupe_bounded_strings(state.world_model.active_hypotheses, max_items=8)
    retained_neg = _dedupe_bounded_strings(state.world_model.falsified_paths, max_items=512)
    return CuratedMemoryDigest(
        compacted_observations=compacted_obs,
        retained_invariants=tuple(inv.invariant_id for inv in mandate.invariants),
        retained_falsified_paths=retained_neg,
        architectural_notes=notes,
        summary=(
            f"Compacted {len(state.world_model.observations)} observation(s) -> "
            f"{len(compacted_obs)} while preserving {len(mandate.invariants)} invariant(s) "
            f"and {len(retained_neg)} falsified negation(s)."
        )[:240],
    )


@jezz_callable
def apply_curated_digest_to_state(
    state: AgentExecutionState,
    mandate: EngineeringMandate,
    digest: CuratedMemoryDigest,
) -> None:
    """Validate and apply ``digest`` to ``state.world_model`` without losing negations."""
    err = validate_curated_digest_invariants(
        mandate,
        tuple(state.world_model.falsified_paths),
        digest,
    )
    if err is not None:
        raise ValueError(err)
    merged_obs = _dedupe_bounded_strings(
        (*digest.compacted_observations, *digest.architectural_notes),
        max_items=32,
    )
    state.world_model.observations = list(merged_obs)
    state.world_model.falsified_paths = list(digest.retained_falsified_paths)


def _split_prefixed_consult_target(target: str) -> tuple[SupportedLanguage, str] | None:
    if ":" not in target:
        return None
    prefix, remainder = target.split(":", maxsplit=1)
    lang = _CONSULT_ECOSYSTEM_PREFIXES.get(prefix.lower())
    cleaned = remainder.strip()
    if lang is None or not cleaned:
        return None
    return lang, cleaned


def _resolve_package_target(repo_root: Path, label: str, lang: SupportedLanguage, spec: str) -> str:
    try:
        pkg_summary = inspect_polyglot_package_api(repo_root, lang, spec)
    except ImportError, ValueError:
        return f"[Target {label}] Unresolved reference; verify via read-only tools."
    return f"[Installed API {label}]\n{pkg_summary.to_compact_text()[:1_500]}"


def _resolve_single_consult_target(repo_root: Path, target: str) -> str:
    upper = target.strip().upper()
    if upper in CODE_BOOK:
        code = CODE_BOOK[upper]
        return f"[{code.code_id} {code.title}] {code.normative_rule} Pass: {code.pass_criteria}"
    prefixed = _split_prefixed_consult_target(target)
    if prefixed is not None:
        return _resolve_package_target(repo_root, target, prefixed[0], prefixed[1])
    try:
        contained = ContainedPath.from_parts(repo_root, target)
    except ValueError:
        contained = None
    if contained is not None and contained.resolved_path.is_file():
        excerpt = read_bounded_text(contained, max_bytes=32_768)[:1_500]
        return f"[Workspace Reference {contained.relative_posix}]\n{excerpt}"
    return _resolve_package_target(repo_root, target, SupportedLanguage.PYTHON, target)


@jezz_callable
def build_consultation_reference_pack(
    repo_root: Path,
    targets: Annotated[Sequence[ConsultTargetStr], Field(max_length=32)],
) -> BoundedText:
    """Resolve JBC code provisions, confined workspace docs, and package APIs for BHV-06 CONSULT."""
    resolved_root = repo_root.resolve()
    sections = [_resolve_single_consult_target(resolved_root, item) for item in targets]
    return "\n\n".join(sections)[:24_000]
