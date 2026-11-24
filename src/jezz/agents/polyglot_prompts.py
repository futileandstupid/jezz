"""Language-conditioned JBC v1.2 statutory briefs, exemplars, and path helpers for PCE agents."""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import Field

from jezz.codes.polyglot_ir import (
    InspectedUnit,
    PolyglotUnit,
    SupportedLanguage,
    detect_language_for_path,
)
from jezz.guards import (
    WIRE_FROZENSET_VALIDATOR,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    jezz_callable,
)

_TEST_DIR_SEGMENTS: Final[frozenset[str]] = frozenset({"tests", "test", "__tests__", "benches"})

_CODE_FENCES: Final[Mapping[SupportedLanguage, str]] = MappingProxyType(
    {
        SupportedLanguage.PYTHON: "python",
        SupportedLanguage.TYPESCRIPT: "typescript",
        SupportedLanguage.RUST: "rust",
        SupportedLanguage.GO: "go",
    }
)

_MANIFEST_LANGUAGE_ORDER: Final[tuple[tuple[str, SupportedLanguage], ...]] = (
    ("pyproject.toml", SupportedLanguage.PYTHON),
    ("package.json", SupportedLanguage.TYPESCRIPT),
    ("Cargo.toml", SupportedLanguage.RUST),
    ("go.mod", SupportedLanguage.GO),
)

_LANGUAGE_PRIORITY: Final[tuple[SupportedLanguage, ...]] = (
    SupportedLanguage.PYTHON,
    SupportedLanguage.TYPESCRIPT,
    SupportedLanguage.RUST,
    SupportedLanguage.GO,
)

_LAYOUT_HINTS: Final[Mapping[SupportedLanguage, str]] = MappingProxyType(
    {
        SupportedLanguage.PYTHON: "src/<pkg>/<mod>.py paired with tests/test_<mod>.py",
        SupportedLanguage.TYPESCRIPT: (
            "src/<mod>.ts paired with tests/<mod>.test.ts (test('<verification_symbol>', ...))"
        ),
        SupportedLanguage.RUST: (
            "src/lib.rs (and/or src/<mod>.rs) paired with tests/<mod>_test.rs "
            "(#[test] fn <verification_symbol>())"
        ),
        SupportedLanguage.GO: (
            "<pkg>/<mod>.go (or internal/<pkg>/<mod>.go) paired with <pkg>/<mod>_test.go "
            "(func <verification_symbol>(t *testing.T))"
        ),
    }
)

JBC_TYPESCRIPT_BRIEF: Final[str] = (
    "JBC v1.2 TypeScript Statutory Requirements: "
    "(1) Annotate 100% of function/method parameters and return types explicitly; never use "
    "'any', 'as unknown as', 'object', or 'Record<string, unknown>'. "
    "(2) Every public function accepting string/number/array inputs MUST enforce an explicit "
    "runtime guard (e.g. 'if (!Number.isSafeInteger(n) || n < 0) { throw new RangeError(...); }' "
    "or Zod '.parse(...)'). If 'zod' is declared in package.json, call '.strict()' on every "
    "'z.object({...})' and bound every z.string().min(...).max(...), z.number().int().min(...), "
    "and z.array(...).max(...); never import undeclared npm packages (JBC-VRF-003). "
    "(3) Use 'const' (zero module-level 'let'/'var'); rethrow caught errors via "
    "'new Error(msg, { cause: err })' (zero empty catch); guard every 'while' loop with a "
    "LoopBudget '.tick()' call; never use 'console.log', '@ts-ignore', 'eslint-disable', "
    "deferred markers, or hardcoded OS paths (derive paths from dynamic root parameters). "
    "(4) Respect structural limits: <= 35 statements, <= 6 parameters, nesting <= 4, Cognitive "
    "Complexity <= 15, <= 1 'else if' per 'if' (use 'Record<K, V>' tables, 'switch', or early "
    "returns), no 'else' after 'return'/'throw', and keep stateless helpers at module level "
    "(LCOM4 <= 1)."
)

JBC_TYPESCRIPT_EXEMPLAR: Final[str] = (
    "Canonical JBC v1.2 TypeScript Idioms:\n"
    "```typescript\n"
    "export interface CreditRequest {\n"
    "  readonly accountId: string;\n"
    "  readonly cents: number;\n"
    "}\n"
    "export class StepBudget {\n"
    "  private steps: number = 0;\n"
    "  private readonly ceiling: number;\n"
    "  public constructor(maxSteps: number) {\n"
    "    if (!Number.isSafeInteger(maxSteps) || maxSteps < 1) {\n"
    "      throw new RangeError('maxSteps must be >= 1');\n"
    "    }\n"
    "    this.ceiling = maxSteps;\n"
    "  }\n"
    "  public tick(): void {\n"
    "    this.steps += 1;\n"
    "    if (this.steps > this.ceiling) {\n"
    "      throw new RangeError('step budget exceeded');\n"
    "    }\n"
    "  }\n"
    "}\n"
    "export function validateCredit(req: CreditRequest): CreditRequest {\n"
    "  if (req.accountId.trim().length === 0 || req.accountId.length > 128) {\n"
    "    throw new RangeError('accountId must be 1..128 chars');\n"
    "  }\n"
    "  if (!Number.isSafeInteger(req.cents) || req.cents <= 0) {\n"
    "    throw new RangeError('cents must be a positive safe integer');\n"
    "  }\n"
    "  return { accountId: req.accountId, cents: req.cents };\n"
    "}\n"
    "// In tests/*.test.ts (use 'node:test' + 'node:assert/strict' unless vitest is declared):\n"
    "// import { test } from 'node:test';\n"
    "// import assert from 'node:assert/strict';\n"
    "// test('test_credit_invariants', () => { assert.equal(validateCredit(...).cents, 100); });\n"
    "```"
)

JBC_RUST_BRIEF: Final[str] = (
    "JBC v1.2 Rust Statutory Requirements: "
    "(1) Zero 'unsafe', zero '.unwrap()' or '.expect(...)' in non-test src/ modules, zero "
    "'panic!(...)'/'todo!(...)'/'unimplemented!(...)', zero 'println!'/'dbg!', zero "
    "'#[allow(...)]', and zero 'static mut'. "
    "(2) Define typed domain error enums ('#[derive(Debug, Clone, PartialEq, Eq)] pub enum "
    "...Error') and return 'Result<T, DomainError>'; never discard errors via 'let _ = ...', "
    "'.ok()', or '.map_err(|_| ...)'. When 'serde' is in Cargo.toml, annotate deserialized "
    "structs with '#[serde(deny_unknown_fields)]'; never import crates absent from Cargo.toml. "
    "(3) Every public function accepting '&str', 'String', 'Vec<T>', '&[T]', or floats MUST "
    "enforce explicit bounds/finiteness checks returning 'Err(...)'; guard any 'while'/'loop' "
    "with a '.tick()?' budget helper; guard divisor parameters with "
    "'if d == 0 { return Err(...); }'. "
    "(4) Respect structural limits: <= 35 statements, <= 6 parameters, nesting <= 4, Cognitive "
    "Complexity <= 15, <= 1 'else if' (prefer 'match' or early 'return'), no 'else' after "
    "'return'/'break'/'continue', and keep stateless helpers outside 'impl' blocks or as "
    "associated constructors (LCOM4 <= 1). Place Phase III integration tests in 'tests/*.rs' "
    "using '#[test] fn <verification_symbol>()' importing from the crate root."
)

JBC_RUST_EXEMPLAR: Final[str] = (
    "Canonical JBC v1.2 Rust Idioms:\n"
    "```rust\n"
    "#[derive(Debug, Clone, PartialEq, Eq)]\n"
    "pub enum LedgerError {\n"
    "    InvalidAccountId,\n"
    "    NonPositiveAmount(i64),\n"
    "    DivisionByZero,\n"
    "}\n"
    "#[derive(Debug, Clone, PartialEq, Eq)]\n"
    "pub struct EscrowVault {\n"
    "    balance_cents: i64,\n"
    "}\n"
    "impl EscrowVault {\n"
    "    pub fn new(initial_cents: i64) -> Result<Self, LedgerError> {\n"
    "        if initial_cents < 0 {\n"
    "            return Err(LedgerError::NonPositiveAmount(initial_cents));\n"
    "        }\n"
    "        Ok(Self { balance_cents: initial_cents })\n"
    "    }\n"
    "    pub fn credit(&mut self, account_id: &str, cents: i64) -> Result<i64, LedgerError> {\n"
    "        if account_id.is_empty() || account_id.len() > 128 {\n"
    "            return Err(LedgerError::InvalidAccountId);\n"
    "        }\n"
    "        if cents <= 0 {\n"
    "            return Err(LedgerError::NonPositiveAmount(cents));\n"
    "        }\n"
    "        self.balance_cents += cents;\n"
    "        Ok(self.balance_cents)\n"
    "    }\n"
    "}\n"
    "```"
)

JBC_GO_BRIEF: Final[str] = (
    "JBC v1.2 Go Statutory Requirements: "
    "(1) Zero 'any' / 'interface{}', zero 'map[string]any', zero 'unsafe.Pointer', zero "
    "'panic(...)' in library code, zero 'fmt.Println', zero '//nolint', and zero package-level "
    "mutable 'var' (sentinel 'var ErrX = errors.New(\"...\")' is permitted). "
    "(2) Never discard returned values/errors with '_' in non-test files; wrap upstream errors "
    "with '%w' ('fmt.Errorf(\"context: %w\", err)', never '%v'); never import packages absent "
    "from stdlib or go.mod (JBC-VRF-003). "
    "(3) Every JSON-tagged struct ('`json:\"...\"`') MUST define 'func (r StructName) Validate() "
    "error' and decode via 'dec.DisallowUnknownFields()'. Every exported function accepting "
    "'string', '[]T', 'map[K]V', or floats MUST validate bounds explicitly via an 'if' guard "
    "returning 'error'; guard divisor parameters with 'if d == 0 { return 0, Err... }'; use "
    "'for range' or bounded index clauses (or tick a '.Tick()' budget on condition loops). "
    "(4) Respect structural limits: <= 35 statements, <= 6 parameters, nesting <= 4, Cognitive "
    "Complexity <= 15, <= 1 'else if' (use 'switch' or early 'return'), no 'else' after "
    "'return'/'break'/'continue', and keep stateless helpers as package functions rather than "
    "receiver methods (LCOM4 <= 1). Author Phase III tests in '*_test.go' with exact "
    "'func <verification_symbol>(t *testing.T)' names."
)

JBC_GO_EXEMPLAR: Final[str] = (
    "Canonical JBC v1.2 Go Idioms:\n"
    "```go\n"
    "package ledger\n\n"
    "import (\n"
    '\t"bytes"\n'
    '\t"encoding/json"\n'
    '\t"errors"\n'
    '\t"fmt"\n'
    ")\n\n"
    'var ErrInvalidCredit = errors.New("credit request failed domain bounds")\n\n'
    "type CreditRequest struct {\n"
    '\tAccountID string `json:"account_id"`\n'
    '\tCents     int64  `json:"cents"`\n'
    "}\n\n"
    "func (r CreditRequest) Validate() error {\n"
    "\tif len(r.AccountID) == 0 || len(r.AccountID) > 128 || r.Cents <= 0 {\n"
    "\t\treturn ErrInvalidCredit\n"
    "\t}\n"
    "\treturn nil\n"
    "}\n\n"
    "func DecodeStrictCredit(payload []byte) (CreditRequest, error) {\n"
    "\tif len(payload) == 0 || len(payload) > 65536 {\n"
    "\t\treturn CreditRequest{}, ErrInvalidCredit\n"
    "\t}\n"
    "\tdec := json.NewDecoder(bytes.NewReader(payload))\n"
    "\tdec.DisallowUnknownFields()\n"
    "\tvar req CreditRequest\n"
    "\tif err := dec.Decode(&req); err != nil {\n"
    '\t\treturn CreditRequest{}, fmt.Errorf("decode credit: %w", err)\n'
    "\t}\n"
    "\tif err := req.Validate(); err != nil {\n"
    '\t\treturn CreditRequest{}, fmt.Errorf("validate credit: %w", err)\n'
    "\t}\n"
    "\treturn req, nil\n"
    "}\n"
    "```"
)

_POLYGLOT_SPECIFY_ROLE: Final[str] = (
    "Author Phase III data/type contracts in authorized non-test implementation file(s): "
    "exported interfaces/types/strict Zod schemas in TypeScript, pub structs/enums/error types "
    "(and module exports in src/lib.rs when owned) in Rust, or exported structs + Validate() "
    "methods + sentinel errors in Go. Authorized paths and existing file snapshots are already "
    "inline—do NOT call read/list tools on absent or already-inlined files. Never write test "
    "files and never emit unimplemented stub functions (concrete implementation callables belong "
    "in Phase IV SYNTHESIZE). Include only symbols defined in contract_patches inside "
    "declared_symbols, and preview each complete file once via _tool_preview_rough_in."
)

_POLYGLOT_INSTRUMENT_ROLE: Final[str] = (
    "Use the inline 'Existing Authorized File Snapshots' directly (do NOT call read/list tools "
    "on already-inlined or absent files) so imports and field names match Phase III contracts "
    "exactly; mandated callable contract_symbols not yet present in Phase III snapshots will be "
    "synthesized in Phase IV—import/call them directly. Author falsification suites strictly in "
    "authorized test file(s) defining every mandated invariant verification_symbol: "
    "(a) TypeScript ('tests/*.test.ts'): use 'vitest' ('import { it, expect } from \"vitest\"') "
    "only if declared in package.json, otherwise 'import { test } from \"node:test\"' + "
    "'import assert from \"node:assert/strict\"' (importing '../src/<mod>.ts' when Node "
    "native TypeScript execution is active), naming each test '<verification_symbol>' verbatim; "
    "(b) Rust ('tests/*.rs'): define '#[test] fn <verification_symbol>()' importing from the "
    "crate root ('use <crate_name>::...;') with 'assert_eq!'/'assert!' checks; "
    "(c) Go ('*_test.go'): define 'func <verification_symbol>(t *testing.T)' in the target "
    "package with 'if ... { t.Fatalf(...) }' checks. Preview each complete test file once via "
    "_tool_preview_rough_in before returning."
)

_POLYGLOT_SYNTHESIZE_ROLE: Final[str] = (
    "Use the inline 'Existing Authorized File Snapshots' directly (do NOT call read/list tools "
    "on already-inlined or absent files) so types, exports, error contracts, and signatures "
    "align with locked Phase III contracts and falsification suites. Emit complete non-test "
    "implementation patches strictly within your authorized ModuleWriteScope without modifying "
    "locked test files or removing Phase III contract symbols; keep stateless helpers outside "
    "classes/structs/receivers (LCOM4 <= 1). Preview each complete file once via "
    "_tool_preview_rough_in before returning."
)

_POLYGLOT_REFACTOR_ROLE: Final[str] = (
    "Renovate the existing non-test implementation file(s) shown in 'Existing Authorized File "
    "Snapshots' so every file achieves zero JBC v1.2 violations while preserving public symbol "
    "names, call signatures compatible with locked tests, and observable domain semantics. Do "
    "NOT modify test files or call read/list tools on already-inlined files. Remediate every "
    "cited baseline RED_TAG finding (explicit types, boundary guards, strict schemas, total "
    "error propagation, loop budgets, <= 1 else-if, LCOM4 <= 1 cohesion, and dynamic path "
    "containment). Preview each complete file once via _tool_preview_rough_in before returning."
)


@jezz_callable
def is_polyglot_test_path(rel_path: IdentifierStr) -> bool:
    """Return True iff ``rel_path`` is a Python, TypeScript, Rust, or Go test module."""
    posix = PurePosixPath(rel_path)
    lowered_parts = {part.lower() for part in posix.parts[:-1]}
    if lowered_parts & _TEST_DIR_SEGMENTS:
        return True
    name_low = posix.name.lower()
    if name_low.endswith(("_test.go", "_test.rs", "_test.py")):
        return True
    if name_low.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")):
        return True
    return name_low.startswith("test_") and name_low.endswith(".py")


@jezz_callable
def code_fence_for_path(rel_path: IdentifierStr) -> IdentifierStr:
    """Return Markdown code-fence language label for ``rel_path``."""
    lang = detect_language_for_path(rel_path)
    if lang is None:
        return "text"
    return _CODE_FENCES[lang]


def _languages_from_paths(paths: AbstractSet[str]) -> set[SupportedLanguage]:
    found: set[SupportedLanguage] = set()
    for rel in paths:
        lang = detect_language_for_path(rel)
        if lang is not None:
            found.add(lang)
    return found


def _languages_from_units(units: Sequence[InspectedUnit]) -> set[SupportedLanguage]:
    found: set[SupportedLanguage] = set()
    for unit in units:
        if isinstance(unit, PolyglotUnit):
            found.add(unit.language)
        else:
            found.add(SupportedLanguage.PYTHON)
    return found


def _languages_from_manifests(repo_root: Path) -> set[SupportedLanguage]:
    resolved = repo_root.resolve()
    if not resolved.is_dir():
        return set()
    found: set[SupportedLanguage] = set()
    for rel_manifest, lang in _MANIFEST_LANGUAGE_ORDER:
        if ContainedPath.from_parts(resolved, rel_manifest).resolved_path.is_file():
            found.add(lang)
    return found


@jezz_callable
def detect_workspace_languages(
    repo_root: Path,
    *,
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)] = (),
    authorized_files: Annotated[
        AbstractSet[IdentifierStr],
        WIRE_FROZENSET_VALIDATOR,
    ] = frozenset(),
) -> tuple[SupportedLanguage, ...]:
    """Return ordered ``SupportedLanguage`` tuple active in scope, units, or manifests."""
    active = _languages_from_paths(authorized_files) or _languages_from_units(units)
    if not active:
        active = _languages_from_manifests(repo_root)
    if not active:
        return (SupportedLanguage.PYTHON,)
    return tuple(lang for lang in _LANGUAGE_PRIORITY if lang in active)


@jezz_callable
def is_pure_python_languages(
    languages: Annotated[AbstractSet[SupportedLanguage], WIRE_FROZENSET_VALIDATOR] | None,
) -> bool:
    """Return True when ``languages`` is unset, empty, or exclusively Python."""
    return not languages or set(languages) == {SupportedLanguage.PYTHON}


@jezz_callable
def select_polyglot_brief_and_exemplar(
    languages: Annotated[AbstractSet[SupportedLanguage], WIRE_FROZENSET_VALIDATOR] | None,
    python_brief: BoundedText,
    python_exemplar: BoundedText,
) -> tuple[BoundedText, BoundedText]:
    """Select canonical static InstructionPart texts matched to ``languages``."""
    if is_pure_python_languages(languages):
        return python_brief, python_exemplar
    effective = set(languages or ())
    brief_map: dict[SupportedLanguage, tuple[str, str]] = {
        SupportedLanguage.PYTHON: (python_brief, python_exemplar),
        SupportedLanguage.TYPESCRIPT: (JBC_TYPESCRIPT_BRIEF, JBC_TYPESCRIPT_EXEMPLAR),
        SupportedLanguage.RUST: (JBC_RUST_BRIEF, JBC_RUST_EXEMPLAR),
        SupportedLanguage.GO: (JBC_GO_BRIEF, JBC_GO_EXEMPLAR),
    }
    ordered = [lang for lang in _LANGUAGE_PRIORITY if lang in effective]
    briefs = "\n\n".join(brief_map[lang][0] for lang in ordered)[:65_536]
    exemplars = "\n\n".join(brief_map[lang][1] for lang in ordered)[:65_536]
    return briefs, exemplars


@jezz_callable
def polyglot_frame_instructions(
    languages: Annotated[AbstractSet[SupportedLanguage], WIRE_FROZENSET_VALIDATOR] | None,
    python_instructions: BoundedText,
) -> BoundedText:
    """Return language-matched BHV-01 FRAME instructions."""
    if is_pure_python_languages(languages):
        return python_instructions
    effective = set(languages or ())
    layouts = "; ".join(_LAYOUT_HINTS[lang] for lang in _LANGUAGE_PRIORITY if lang in effective)
    return (
        "Convert the engineering request into a concise EngineeringMandate (mandate_id matching "
        "^MND-[A-Za-z0-9_-]+$, invariant_id matching ^INV-[0-9]{3,6}$). Use valid identifier "
        "symbols for verification_symbol (e.g. 'test_quota_limit' for Python/TS/Rust or "
        "'TestQuotaLimit' for Go) and contract_symbols (never file paths). Target workspace "
        f"language layout(s): {layouts}. Every ModuleWriteScope must own at least one non-test "
        "implementation file paired with its corresponding test file inside the same "
        "ModuleWriteScope. State invariants as strict falsifiable conditions (3..5 cohesive "
        "invariants per module); never emit Agile user stories ('As a ..., I want ...'), "
        "Markdown checkboxes ('- [ ]'), ceremony jargon, or weasel modals ('should'/'might'/"
        "'ideally'/'maybe'/'try to'). Leave required_dependencies=() and consultation_targets=() "
        "unless explicitly requested. Populate open_ambiguities whenever material domain "
        "bounds, error policies, or edge-case semantics are under-determined; integrate "
        "Authoritative Clarifications when provided and set open_ambiguities=() once resolved."
    )


@jezz_callable
def polyglot_decompose_instructions(
    languages: Annotated[AbstractSet[SupportedLanguage], WIRE_FROZENSET_VALIDATOR] | None,
    python_instructions: BoundedText,
) -> BoundedText:
    """Return language-matched BHV-09 DECOMPOSE instructions."""
    if is_pure_python_languages(languages):
        return python_instructions
    return (
        "Refine the EngineeringMandate's ModuleWriteScopes using the grounded Workspace AST/CST "
        "Graph and epistemic observations provided in the prompt. Partition files into "
        "pairwise-disjoint ModuleWriteScopes where each scope owns at least one non-test "
        "implementation module (.py/.ts/.tsx/.rs/.go) paired with the test file(s) verifying it. "
        "Populate `depends_on` with upstream `scope_id`s whenever a scope imports types or "
        "callables synthesized in an upstream scope, forming a strict DAG (zero cycles, "
        "preventing JBC-COG-003 circular imports by design). Preserve all mandated "
        "contract_symbols."
    )


@jezz_callable
def polyglot_construction_role_instructions(
    role_key: IdentifierStr,
    languages: Annotated[AbstractSet[SupportedLanguage], WIRE_FROZENSET_VALIDATOR] | None,
    python_role: BoundedText,
) -> BoundedText:
    """Return language-matched role InstructionPart text for Phase III/IV construction agents."""
    if is_pure_python_languages(languages):
        return python_role
    table: dict[str, str] = {
        "specify": _POLYGLOT_SPECIFY_ROLE,
        "instrument": _POLYGLOT_INSTRUMENT_ROLE,
        "synthesize": _POLYGLOT_SYNTHESIZE_ROLE,
        "refactor": _POLYGLOT_REFACTOR_ROLE,
    }
    return table.get(role_key, python_role)
