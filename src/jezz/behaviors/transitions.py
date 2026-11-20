"""Local edge-legality matrix over the 21 atomic coding-agent behaviors."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

import icontract
from pydantic import BaseModel

from jezz.behaviors.taxonomy import BEHAVIOR_BASIS, BehaviorKind, WorkspaceEffect
from jezz.guards import STRICT_FROZEN_CONFIG, ShortText, jezz_callable

EPISTEMIC_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.INSPECT_CODE,
        BehaviorKind.INSPECT_ENV,
        BehaviorKind.CONSULT,
        BehaviorKind.CURATE_MEMORY,
    }
)

MUTATE_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.PROVISION,
        BehaviorKind.SPECIFY,
        BehaviorKind.INSTRUMENT,
        BehaviorKind.SYNTHESIZE,
        BehaviorKind.REFACTOR,
    }
)

MUTATION_STRATUM_KINDS: Final[frozenset[BehaviorKind]] = MUTATE_KINDS | {BehaviorKind.CHECKPOINT}

VERIFY_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.VERIFY_STATIC,
        BehaviorKind.VERIFY_DYNAMIC,
    }
)

TERMINAL_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.CERTIFY,
        BehaviorKind.ELICIT,
    }
)

_EPISTEMIC_OUTGOING: Final[frozenset[BehaviorKind]] = (
    frozenset(
        {
            BehaviorKind.FRAME,
            BehaviorKind.ELICIT,
            BehaviorKind.BIND_CODES,
            BehaviorKind.HYPOTHESIZE,
            BehaviorKind.DECOMPOSE,
            BehaviorKind.RECONCILE,
            BehaviorKind.REVERT,
            BehaviorKind.CERTIFY,
        }
    )
    | EPISTEMIC_KINDS
    | MUTATION_STRATUM_KINDS
    | VERIFY_KINDS
)

_MUTATE_OUTGOING: Final[frozenset[BehaviorKind]] = (
    MUTATION_STRATUM_KINDS
    | EPISTEMIC_KINDS
    | frozenset({BehaviorKind.DECOMPOSE, BehaviorKind.DELEGATE, BehaviorKind.REVERT})
    | VERIFY_KINDS
)

_VERIFY_OUTGOING: Final[frozenset[BehaviorKind]] = (
    VERIFY_KINDS
    | EPISTEMIC_KINDS
    | MUTATION_STRATUM_KINDS
    | frozenset(
        {
            BehaviorKind.CERTIFY,
            BehaviorKind.REVERT,
            BehaviorKind.ELICIT,
            BehaviorKind.HYPOTHESIZE,
            BehaviorKind.DECOMPOSE,
            BehaviorKind.DELEGATE,
        }
    )
)

_RAW_TRANSITIONS: Final[Mapping[BehaviorKind, frozenset[BehaviorKind]]] = MappingProxyType(
    {
        # Stratum I: Governance
        BehaviorKind.FRAME: (
            frozenset({BehaviorKind.ELICIT, BehaviorKind.BIND_CODES}) | EPISTEMIC_KINDS
        ),
        BehaviorKind.ELICIT: frozenset({BehaviorKind.FRAME}),
        BehaviorKind.BIND_CODES: (
            frozenset(
                {
                    BehaviorKind.ELICIT,
                    BehaviorKind.HYPOTHESIZE,
                    BehaviorKind.DECOMPOSE,
                    BehaviorKind.CHECKPOINT,
                    BehaviorKind.CERTIFY,
                }
            )
            | EPISTEMIC_KINDS
            | VERIFY_KINDS
        ),
        # Stratum II: Epistemic
        BehaviorKind.INSPECT_CODE: _EPISTEMIC_OUTGOING,
        BehaviorKind.INSPECT_ENV: _EPISTEMIC_OUTGOING,
        BehaviorKind.CONSULT: _EPISTEMIC_OUTGOING,
        BehaviorKind.CURATE_MEMORY: _EPISTEMIC_OUTGOING | frozenset({BehaviorKind.DELEGATE}),
        # Stratum III: Topology
        BehaviorKind.HYPOTHESIZE: (
            frozenset(
                {
                    BehaviorKind.ELICIT,
                    BehaviorKind.BIND_CODES,
                    BehaviorKind.DECOMPOSE,
                    BehaviorKind.CERTIFY,
                }
            )
            | EPISTEMIC_KINDS
            | MUTATION_STRATUM_KINDS
            | VERIFY_KINDS
        ),
        BehaviorKind.DECOMPOSE: (
            frozenset(
                {
                    BehaviorKind.ELICIT,
                    BehaviorKind.BIND_CODES,
                    BehaviorKind.HYPOTHESIZE,
                    BehaviorKind.DELEGATE,
                    BehaviorKind.CERTIFY,
                }
            )
            | EPISTEMIC_KINDS
            | MUTATION_STRATUM_KINDS
            | VERIFY_KINDS
        ),
        BehaviorKind.DELEGATE: frozenset(
            {
                BehaviorKind.DELEGATE,
                BehaviorKind.RECONCILE,
                BehaviorKind.INSPECT_ENV,
                BehaviorKind.CURATE_MEMORY,
            }
        ),
        BehaviorKind.RECONCILE: (
            frozenset(
                {
                    BehaviorKind.ELICIT,
                    BehaviorKind.BIND_CODES,
                    BehaviorKind.HYPOTHESIZE,
                    BehaviorKind.DECOMPOSE,
                    BehaviorKind.DELEGATE,
                    BehaviorKind.REVERT,
                    BehaviorKind.CERTIFY,
                }
            )
            | EPISTEMIC_KINDS
            | MUTATION_STRATUM_KINDS
            | VERIFY_KINDS
        ),
        # Stratum IV: Mutation
        BehaviorKind.CHECKPOINT: (
            MUTATE_KINDS
            | EPISTEMIC_KINDS
            | frozenset({BehaviorKind.HYPOTHESIZE, BehaviorKind.DECOMPOSE, BehaviorKind.DELEGATE})
        ),
        BehaviorKind.PROVISION: _MUTATE_OUTGOING,
        BehaviorKind.SPECIFY: _MUTATE_OUTGOING,
        BehaviorKind.INSTRUMENT: _MUTATE_OUTGOING,
        BehaviorKind.SYNTHESIZE: _MUTATE_OUTGOING,
        BehaviorKind.REFACTOR: _MUTATE_OUTGOING,
        # Stratum V: Verification, Recovery & Certification
        BehaviorKind.VERIFY_STATIC: _VERIFY_OUTGOING,
        BehaviorKind.VERIFY_DYNAMIC: _VERIFY_OUTGOING,
        BehaviorKind.REVERT: EPISTEMIC_KINDS
        | frozenset(
            {
                BehaviorKind.FRAME,
                BehaviorKind.ELICIT,
                BehaviorKind.HYPOTHESIZE,
                BehaviorKind.DECOMPOSE,
            }
        ),
        BehaviorKind.CERTIFY: frozenset(),
    }
)


def _validate_transition_matrix(
    matrix: Mapping[BehaviorKind, frozenset[BehaviorKind]],
) -> Mapping[BehaviorKind, frozenset[BehaviorKind]]:
    if set(matrix.keys()) != set(BehaviorKind):
        msg = "Transition matrix must cover every BehaviorKind exactly once."
        raise ValueError(msg)

    if matrix[BehaviorKind.CERTIFY]:
        msg = "CERTIFY must be a strict terminal sink with out-degree 0."
        raise ValueError(msg)

    if matrix[BehaviorKind.ELICIT] != frozenset({BehaviorKind.FRAME}):
        msg = "Resuming from ELICIT must pass exclusively through FRAME."
        raise ValueError(msg)

    for source, targets in matrix.items():
        effect = BEHAVIOR_BASIS[source].workspace_effect
        if effect == WorkspaceEffect.MUTATE and BehaviorKind.CERTIFY in targets:
            msg = f"Mutating behavior {source} must never transition directly to CERTIFY."
            raise ValueError(msg)
        if source == BehaviorKind.REVERT and (targets & MUTATION_STRATUM_KINDS):
            msg = "REVERT must not transition directly to mutation (bans blind stochastic retry)."
            raise ValueError(msg)

    return MappingProxyType(dict(matrix))


ALLOWED_TRANSITIONS: Final[Mapping[BehaviorKind, frozenset[BehaviorKind]]] = (
    _validate_transition_matrix(_RAW_TRANSITIONS)
)

_FIXED_REJECTION_TEMPLATES: Final[Mapping[BehaviorKind, str]] = MappingProxyType(
    {
        BehaviorKind.CERTIFY: (
            "CERTIFY is a strict terminal sink; certified runs cannot execute further steps."
        ),
        BehaviorKind.ELICIT: "External authority responses from ELICIT must re-enter via FRAME.",
        BehaviorKind.FRAME: (
            "FRAME cannot jump directly to {target}; bind codes or gather initial epistemic "
            "context before planning, mutating, or verifying."
        ),
        BehaviorKind.DELEGATE: (
            "DELEGATE cannot transition to {target} before synchronizing via RECONCILE."
        ),
    }
)


class TransitionVerdict(BaseModel):
    """Typed explanation of whether a directed edge (source -> target) is locally legal."""

    model_config = STRICT_FROZEN_CONFIG

    source: BehaviorKind
    target: BehaviorKind
    allowed: bool
    reason: ShortText


def _rejection_reason(source: BehaviorKind, target: BehaviorKind) -> str:
    """Resolve diagnostic rejection message via immutable table + flat guard clauses."""
    template = _FIXED_REJECTION_TEMPLATES.get(source)
    if template is not None:
        return template.format(target=target)
    if source in MUTATE_KINDS and target == BehaviorKind.CERTIFY:
        return f"Mutating node {source} cannot transition to CERTIFY without verification gates."
    if source in MUTATE_KINDS and target in {BehaviorKind.ELICIT, BehaviorKind.FRAME}:
        return f"Mutating node {source} must verify or REVERT workspace state before {target}."
    if source == BehaviorKind.REVERT and target in MUTATION_STRATUM_KINDS:
        return (
            f"REVERT -> {target} is banned to prevent blind retry loops; update world model, "
            "hypothesis, or decomposition before mutating again."
        )
    return f"Transition {source} -> {target} is disallowed by the statutory adjacency matrix."


@icontract.ensure(lambda source, target, result: result == (target in ALLOWED_TRANSITIONS[source]))
@jezz_callable
def is_transition_allowed(source: BehaviorKind, target: BehaviorKind) -> bool:
    """Return True iff local transition ``source -> target`` is permitted."""
    return target in ALLOWED_TRANSITIONS[source]


@icontract.ensure(
    lambda source, target, result: result.allowed == is_transition_allowed(source, target)
)
@jezz_callable
def explain_transition(source: BehaviorKind, target: BehaviorKind) -> TransitionVerdict:
    """Return a structured verdict explaining why ``source -> target`` is legal or forbidden."""
    if is_transition_allowed(source, target):
        return TransitionVerdict(
            source=source,
            target=target,
            allowed=True,
            reason=(
                f"Transition {source} -> {target} satisfies local state-machine adjacency rules."
            ),
        )
    return TransitionVerdict(
        source=source,
        target=target,
        allowed=False,
        reason=_rejection_reason(source, target),
    )
