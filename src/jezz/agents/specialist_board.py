"""21-behavior execution board invariants and canonical Python JBC specialist prompts."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

import icontract

from jezz.agents.schemas import (
    BehaviorExecutionBinding,
    ExecutionEngineKind,
    ToolsetAccessLevel,
)
from jezz.behaviors.fibonacci import FIBONACCI_BASIS_CARDINALITY, is_fibonacci
from jezz.behaviors.taxonomy import BEHAVIOR_BASIS, BehaviorKind, WorkspaceEffect
from jezz.guards import jezz_callable

DETERMINISTIC_BEHAVIOR_KINDS: Final[frozenset[BehaviorKind]] = frozenset(
    {
        BehaviorKind.BIND_CODES,
        BehaviorKind.RECONCILE,
        BehaviorKind.CHECKPOINT,
        BehaviorKind.VERIFY_STATIC,
        BehaviorKind.VERIFY_DYNAMIC,
        BehaviorKind.REVERT,
        BehaviorKind.CERTIFY,
    }
)

BEHAVIOR_EXECUTION_BOARD: Final[Mapping[BehaviorKind, BehaviorExecutionBinding]] = MappingProxyType(
    {
        BehaviorKind.FRAME: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.FRAME,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="mandate_framer",
            rationale="Translates user intent into a typed anti-Agile EngineeringMandate.",
        ),
        BehaviorKind.ELICIT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.ELICIT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="ambiguity_elicitor",
            rationale="Halts construction to request human disambiguation on open ambiguities.",
        ),
        BehaviorKind.BIND_CODES: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.BIND_CODES,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="permit_issuer",
            rationale="Zero-entropy JBC v1.2 CodePermit and hazard-trigger binder.",
        ),
        BehaviorKind.INSPECT_CODE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSPECT_CODE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="code_inspector",
            rationale="Grounds WorldModelState via read-only AST and file inspection tools.",
        ),
        BehaviorKind.INSPECT_ENV: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSPECT_ENV,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="env_inspector",
            rationale="Reads runtime toolchain and manifest metadata without mutation capability.",
        ),
        BehaviorKind.CONSULT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CONSULT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="standards_consultant",
            rationale="Queries primary technical references and standards citations.",
        ),
        BehaviorKind.CURATE_MEMORY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CURATE_MEMORY,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="memory_curator",
            rationale="Compacts grounded observations and negative knowledge in WorldModelState.",
        ),
        BehaviorKind.HYPOTHESIZE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.HYPOTHESIZE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="causal_diagnostician",
            rationale="Diagnoses RED_TAG citations after REVERT to form a remediation plan.",
        ),
        BehaviorKind.DECOMPOSE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.DECOMPOSE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="scope_architect",
            rationale="Partitions work across pairwise-disjoint ModuleWriteScopes.",
        ),
        BehaviorKind.DELEGATE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.DELEGATE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.READ_ONLY,
            specialist_role="delegation_coordinator",
            rationale="Dispatches parallel TaskGroup subagents over disjoint ModuleWriteScopes.",
        ),
        BehaviorKind.RECONCILE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.RECONCILE,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="scope_reconciler",
            rationale="Verifies pairwise disjointness of delegated write sets before commit.",
        ),
        BehaviorKind.CHECKPOINT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CHECKPOINT,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="snapshot_keeper",
            rationale="Captures deterministic pre-mutation workspace snapshots.",
        ),
        BehaviorKind.PROVISION: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.PROVISION,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="dependency_provisioner",
            rationale="Updates declared manifest dependencies within authorized scope.",
        ),
        BehaviorKind.SPECIFY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.SPECIFY,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="contract_specifier",
            rationale="Authors Phase III strict schemas and runtime contract obligations.",
        ),
        BehaviorKind.INSTRUMENT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.INSTRUMENT,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="falsification_instrumenter",
            rationale="Authors Phase III property and falsification test suites.",
        ),
        BehaviorKind.SYNTHESIZE: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.SYNTHESIZE,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="code_synthesizer",
            rationale="Synthesizes Phase IV implementation satisfying locked contracts.",
        ),
        BehaviorKind.REFACTOR: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.REFACTOR,
            engine_kind=ExecutionEngineKind.PYDANTIC_AI_AGENT,
            toolset_access=ToolsetAccessLevel.SCOPED_WRITE,
            specialist_role="structural_refactorer",
            rationale="Applies behavior-preserving structural transformations within scope.",
        ),
        BehaviorKind.VERIFY_STATIC: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.VERIFY_STATIC,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="rough_in_inspector",
            rationale="Runs deterministic JBC v1.2 AST, complexity, cohesion, and security gates.",
        ),
        BehaviorKind.VERIFY_DYNAMIC: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.VERIFY_DYNAMIC,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="final_gate_inspector",
            rationale="Executes dynamic tests, typecheck, lint, and runtime budget watchdogs.",
        ),
        BehaviorKind.REVERT: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.REVERT,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="rollback_enforcer",
            rationale="Restores clean checkpoint on RED_TAG and records falsified paths.",
        ),
        BehaviorKind.CERTIFY: BehaviorExecutionBinding(
            behavior_kind=BehaviorKind.CERTIFY,
            engine_kind=ExecutionEngineKind.DETERMINISTIC_ENGINE,
            toolset_access=ToolsetAccessLevel.NONE,
            specialist_role="occupancy_certifier",
            rationale="Issues SHA-256 CertificateOfOccupancy and audits PCE Dossier.",
        ),
    }
)


@icontract.ensure(
    lambda result: is_fibonacci(len(result)) and len(result) == FIBONACCI_BASIS_CARDINALITY
)
@jezz_callable
def validate_execution_board_invariants() -> tuple[BehaviorExecutionBinding, ...]:
    """Verify 21-behavior coverage, write-capability isolation, and zero-entropy gate locks."""
    if set(BEHAVIOR_EXECUTION_BOARD) != set(BehaviorKind):
        msg = "BEHAVIOR_EXECUTION_BOARD must map all 21 BehaviorKinds bijectively."
        raise ValueError(msg)

    for kind, binding in BEHAVIOR_EXECUTION_BOARD.items():
        if binding.behavior_kind != kind:
            msg = f"Board key '{kind}' mismatches binding.behavior_kind '{binding.behavior_kind}'."
            raise ValueError(msg)
        is_mutate = BEHAVIOR_BASIS[kind].workspace_effect == WorkspaceEffect.MUTATE
        has_write = binding.toolset_access == ToolsetAccessLevel.SCOPED_WRITE
        if is_mutate != has_write:
            msg = (
                f"Capability violation on '{kind}': SCOPED_WRITE ({has_write}) must match "
                f"WorkspaceEffect.MUTATE ({is_mutate})."
            )
            raise ValueError(msg)
        if kind in DETERMINISTIC_BEHAVIOR_KINDS and (
            binding.engine_kind != ExecutionEngineKind.DETERMINISTIC_ENGINE
            or binding.toolset_access != ToolsetAccessLevel.NONE
        ):
            msg = f"Gate behavior '{kind}' must run as a toolless DETERMINISTIC_ENGINE."
            raise ValueError(msg)

    return tuple(BEHAVIOR_EXECUTION_BOARD.values())


JBC_CANONICAL_EXEMPLAR: Final[str] = (
    "Canonical JBC v1.2 Idioms (copy exact jezz.guards signatures):\n"
    "```python\n"
    "from pathlib import Path\n"
    "from types import MappingProxyType\n"
    "from typing import Annotated, Final\n"
    "import icontract\n"
    "from pydantic import BaseModel, Field\n"
    "from jezz.guards import (\n"
    "    STRICT_FROZEN_CONFIG, BudgetExceededError, ContainedPath, IdentifierStr,\n"
    "    LoopBudget, NonNegativeInt, PositiveInt, ShortText, jezz_callable, read_bounded_text,\n"
    ")\n"
    "type RateBps = Annotated[int, Field(ge=0, le=10_000)]  # NEVER subscript BoundedInt[...]\n"
    "_RATES: Final = MappingProxyType({'a': 10, 'b': 20})   # <=1 elif; use MappingProxyType\n"
    "class Order(BaseModel):\n"
    "    model_config = STRICT_FROZEN_CONFIG\n"
    "    item_id: IdentifierStr\n"
    "    rate_bps: RateBps\n"
    "@jezz_callable\n"
    "def load_confined(\n"
    "    root_dir: Path, rel: IdentifierStr, *, max_bytes: PositiveInt = 65_536,\n"
    ") -> str:\n"
    "    # ContainedPath.from_parts raises ValueError on '../', outside, or escaping symlinks;\n"
    "    # read_bounded_text raises BudgetExceededError(RuntimeError) when file > max_bytes.\n"
    "    target = ContainedPath.from_parts(root_dir, rel)\n"
    "    return read_bounded_text(target, max_bytes=max_bytes)\n"
    "@icontract.require(lambda max_steps: max_steps >= 1)\n"
    "@icontract.ensure(lambda result: result >= 0)\n"
    "@jezz_callable\n"
    "def step_loop(\n"
    "    items: Annotated[tuple[IdentifierStr, ...], Field(min_length=1, max_length=512)],\n"
    "    *, max_steps: PositiveInt = 1000,\n"
    ") -> NonNegativeInt:\n"
    "    budget = LoopBudget(max_steps=max_steps)  # required on every while in src/ and tests/\n"
    "    rem = list(items)\n"
    "    while rem:\n"
    "        budget.tick()  # raises BudgetExceededError(RuntimeError) on overrun\n"
    "        rem.pop()\n"
    "    return len(items)\n"
    "```"
)

JBC_STATUTORY_BRIEF: Final[str] = (
    "JBC v1.2 Statutory Requirements: "
    "(1) Every BaseModel must set model_config = STRICT_FROZEN_CONFIG (from jezz.guards) or "
    "ConfigDict(frozen=True, extra='forbid', strict=True, allow_inf_nan=False), and bound every "
    "field via jezz.guards (PositiveInt 1..2**63-1, NonNegativeInt 0..2**63-1, BoundedInt, "
    "FiniteFloat, IdentifierStr 1..256, ShortText 1..1024, BoundedText 0..65_536 — PEP 695 "
    "TypeAliasType aliases, NEVER subscript BoundedInt[...] or call IdentifierStr('x') at "
    "runtime; prefer NonNegativeInt / PositiveInt when only a lower bound >=0 or >0 is "
    "specified rather than inventing narrower le= caps; use Annotated[int, Field(ge=..., "
    "le=...)] only when an explicit upper domain bound is stated), "
    "StrEnum/Literal/BaseModel, or Annotated[..., Field(...)]. "
    "(2) Every callable requires 100% parameter and return type annotations with zero Any and no "
    "untyped dict/Mapping[str, object] parameters or returns; decorate every public non-test "
    "callable with @jezz_callable (from jezz.guards) and bound all public scalar parameters. "
    "(3) Attach falsifiable @icontract.require / @icontract.ensure predicates (@ensure must "
    "reference result; guard divisor parameters); raise specific exceptions with diagnostic "
    "messages ('from err' inside except); never use silent except, while without "
    "LoopBudget.tick(), print(), TODO/FIXME, or # noqa/# type: ignore. "
    "(4) Never hardcode filesystem paths (JBC-SEC-006): never use '/Users/...', '/home/...', "
    "'/tmp/...', '~/...', or string-literal Path('...') constructors; derive all paths "
    "dynamically from Path(__file__).resolve(), importlib.resources, tmp_path/tempfile, or "
    "ContainedPath.from_parts(anchor, rel). "
    "(5) Respect structural limits: <= 35 statements, <= 6 parameters, nesting <= 4, Cognitive "
    "Complexity <= 15, <= 1 elif per if (use MappingProxyType dispatch tables, match, or early "
    "guard returns), and never place else/elif after return/raise/break/continue."
)

PY_FRAME_INSTRUCTIONS: Final[str] = (
    "Convert the engineering request into a concise EngineeringMandate (mandate_id "
    "matching ^MND-[A-Za-z0-9_-]+$, invariant_id matching ^INV-[0-9]{3,6}$). "
    "Use pure Python identifiers for verification_symbol (e.g. 'test_quota_limit') and "
    "contract_symbols (never file paths or 'file.py:sym' pytest nodeids). Every "
    "ModuleWriteScope must own at least one non-test src/.../*.py file (place paired "
    "tests/test_*.py files inside the same ModuleWriteScope rather than a separate "
    "test-only scope). State invariants as strict falsifiable conditions (prefer 3..5 "
    "cohesive DomainInvariants per module rather than fragmenting trivial field/enum "
    "checks into separate invariants); never emit Agile user stories ('As a ..., I want "
    "...'), Markdown checkboxes ('- [ ]'), ceremony jargon, or weasel modals "
    "('should'/'might'/'ideally'/'maybe'/'try to'). Leave required_dependencies=() and "
    "consultation_targets=() unless the prompt explicitly requests adding a third-party "
    "package or consulting specific standards/docs. "
    "Populate open_ambiguities whenever material domain constraints, bounds, failure "
    "policies, or edge-case semantics are under-determined rather than guessing (note: "
    "JBC v1.2 conventions are already fixed and never ambiguous — all BaseModels use "
    "STRICT_FROZEN_CONFIG from jezz.guards, IdentifierStr is 1..256 strict str, "
    "PositiveInt >= 1, NonNegativeInt >= 0, and public callables use @jezz_callable). When "
    "'Authoritative Clarifications Resolving Open Ambiguities' is provided, integrate "
    "those answers into concrete invariants/write_scopes and set open_ambiguities=() "
    "once resolved."
)

PY_DECOMPOSE_INSTRUCTIONS: Final[str] = (
    "Refine the EngineeringMandate's ModuleWriteScopes using the grounded Workspace AST "
    "Graph and epistemic observations provided in the prompt. Partition files into "
    "pairwise-disjoint ModuleWriteScopes where each scope owns at least one non-test "
    "src/.../*.py file (pair tests/test_*.py files inside the scope they verify). "
    "Populate `depends_on` with upstream `scope_id`s whenever a scope imports types or "
    "callables synthesized in an upstream scope, forming a strict DAG (zero cycles, "
    "preventing JBC-COG-003 circular imports by design). Preserve all mandated "
    "contract_symbols."
)

PY_SPECIFY_ROLE: Final[str] = (
    "Author Phase III data contracts (strict Pydantic BaseModels, StrEnums, and "
    "bounded Annotated type aliases) in authorized non-test src/... contract "
    "file(s). Authorized paths and existing file contents are already provided "
    "inline in the prompt—do NOT call read/list tools on absent or "
    "already-inlined files. Never write tests/... files and never emit "
    "unimplemented pass/.../NotImplementedError function stubs (concrete "
    "implementation callables belong in Phase IV SYNTHESIZE). Include only "
    "symbols defined in contract_patches inside declared_symbols. Draft the "
    "complete file in one pass and call _tool_preview_rough_in once on the full "
    "candidate file before returning."
)

PY_INSTRUMENT_ROLE: Final[str] = (
    "Use the inline 'Existing Authorized File Snapshots' directly (do NOT call "
    "read/list tools on already-inlined or absent files) so imports and field "
    "names match Phase III contracts exactly; mandated callable contract_symbols "
    "not yet present in Phase III src/... snapshots will be synthesized in Phase "
    "IV—import them directly from their target src/... module without searching "
    "the workspace. Author pytest and Hypothesis (@given) falsification suites "
    "under authorized tests/... file(s) defining every mandated invariant "
    "verification_symbol. Every test and helper callable in tests/ must have "
    "100% parameter and '-> None' return annotations with zero Any, <= 6 "
    "parameters (when @given needs >6 values, use '@given(st.data())' with "
    "'data: st.DataObject' instead of >6 args or untyped @st.composite), and "
    "<= 35 statements; name any delegated assertion helper '_assert_*', "
    "'_verify_*', '_check_*', or '_expect_*' (JBC-VRF-001). Under mypy --strict "
    "(strict_equality), assert StrEnum.MEMBER.value == 'literal' rather than "
    "StrEnum.MEMBER == 'literal' ([comparison-overlap]). When testing that "
    "invalid inputs or step-budget overruns raise an error, expect (ValueError, "
    "TypeError, icontract.ViolationError, BudgetExceededError); never combine "
    "@given with function-scoped fixtures like tmp_path, and guard any helper "
    "while loop in tests/ with LoopBudget(max_steps=...). Preview each complete "
    "test file once via _tool_preview_rough_in before returning."
)

PY_SYNTHESIZE_ROLE: Final[str] = (
    "Use the inline 'Existing Authorized File Snapshots' directly (do NOT call "
    "read/list tools on already-inlined or absent files) so imports, types, and "
    "postconditions align with locked Phase III contracts and falsification "
    "suites. Emit complete src/... implementation patches strictly within your "
    "authorized ModuleWriteScope without modifying locked tests/... files or "
    "removing Phase III contract symbols; keep helper functions that do not read "
    "'self' at module level rather than as stateless instance methods on classes "
    "(JBC-COG-005). Preview each complete file once via _tool_preview_rough_in "
    "before returning."
)

PY_REFACTOR_ROLE: Final[str] = (
    "Renovate the existing non-test src/... file(s) shown in 'Existing "
    "Authorized File Snapshots' so every file achieves zero JBC v1.2 violations "
    "while preserving public symbol names, call signatures compatible with locked "
    "tests, and observable domain semantics. Do NOT modify tests/... files or "
    "call read/list tools on already-inlined files. Remediate every cited "
    "baseline RED_TAG finding (attach @jezz_callable, STRICT_FROZEN_CONFIG, "
    "bounded jezz.guards / Annotated[..., Field(...)] types, falsifiable "
    "@icontract.require/@icontract.ensure, LoopBudget on while loops, "
    "MappingProxyType dispatch / <=1 elif, LCOM4 == 1 cohesion, narrow chained "
    "exceptions, and ContainedPath filesystem containment). Preview each complete "
    "candidate file once via _tool_preview_rough_in before returning."
)
