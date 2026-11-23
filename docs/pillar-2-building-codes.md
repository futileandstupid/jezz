# Pillar 2 — Enforceable Software Building Codes (`JBC` Edition 1.2)

## 1. Statutory Rationale: The Building Inspector Model

In civil and electrical engineering, passing an energized functional check ("the lamp turns on") does not make an installation safe to energize. An ungrounded receptacle, an undersized conductor, or a bypassed breaker works on the happy path until a fault burns the structure down. Licensure-backed **building codes** (NFPA 70 / NEC, International Building Code) make grounding, fault isolation, ampacity bounds, and inspectability mandatory conditions of occupancy.

Existing software standards fail to serve as an enforceable code book for three structural reasons:
1. **Quality Taxonomies Without Decidable Predicates:** ISO/IEC 25010 and IEEE 1016/29148 name desirable nouns (*Analysability*, *Modifiability*, *Reliability*) without objective thresholds or machine-checkable pass/fail rules.
2. **Post-Mortem Pathology Catalogs:** MITRE CWE enumerates ~900 overlapping weaknesses at inconsistent abstraction levels rather than affirmative construction standards.
3. **Human-Era Blind Spots:** Existing standards assume human authorship cost structures. Coding agents exhibit fundamentally different failure modes—hallucinating nonexistent packages, authoring assertion-free or tautological test theatre (`assert True`), leaving `pass`/`NotImplementedError` stubs behind typed headers, and injecting `Any` or `# type: ignore` to silence static gates.

Pillar 2 establishes the **Jezz Software Building Code (`JBC` Edition 1.2 — 31 provisions across 6 Articles)** and an automated **Building Inspector** (`BuildingInspector`) that gates occupancy across Pillar 1's behavioral state machine.

---

## 2. Key Architectural Design Decisions in Edition 1.2

### A. Option A Runtime Signature Breakers (`JBC-DBC-004` + `JBC-COG-003`)
In Python, static annotations (`def charge(acct: str, amt: float) -> Receipt:`) are inert `__annotations__` metadata at runtime—labeling a panel slot `"20A"` with a Sharpie while installing no breaker.
- **Public & Boundary Feeders (`@jezz_callable`):** Mandatory on all public module functions and public class methods (enforcing `strict=True`, `allow_inf_nan=False`, and `validate_return=True` on non-generators via `pydantic-core`).
- **Why `_private` Intra-Module Helpers Are Excluded:** Validating `Sequence[T]` or `Mapping[K, V]` walks every element ($O(N)$). Validating on every internal recursive/loop helper turns $O(N)$ graph/tree algorithms into $O(N^2)$, exhausts `Iterator` parameters, pollutes internal AST/handle helpers with `arbitrary_types_allowed`, and creates `CodeVariance` fatigue.
- **Sealed Module Enclosures (`JBC-COG-003`):** Cross-module `_private` imports (`from pkg.a import _helper`) are outlawed so callers cannot bypass a module's validated public feeders.

### B. Bounded Termination & Resource Envelopes (`JBC-FLT-004`..`006`)
Because arbitrary `while cond:` termination is undecidable, `JBC` adopts the safety-critical verifier pattern:
- **`LoopBudget` on `while` Loops (`CWE-835`):** Every `while` loop must invoke `budget.tick()` against a deterministic step cap.
- **Fuel-Bounded Recursion Permitted; Unbounded Recursion Banned (`CWE-674`):** Forcing post-order tree/AST algorithms into manual heap-stack state machines explodes Cognitive Complexity (`JBC-COG-001`). Recursion is legal *iff* governed by an explicit decreasing fuel parameter (`remaining_depth` / `max_depth`) or `RecursionBudget`.
- **Structured Concurrency & Allocation Caps (`CWE-400`, `CWE-770`):** Bare `create_task` / `Thread.start` are banned in favor of `TaskGroup`; async external I/O requires wall-clock `asyncio.timeout` scopes (defeating Slowloris drip reads); `@functools.cache` and uncapped `.read()` / `.read_text()` / `.fetchall()` are replaced by bounded caches and `read_bounded_text(ContainedPath, max_bytes=...)`.

### C. Table-Driven Dispatch (`circuit_board`) & Single-Duty Classes (`JBC-COG-004`..`005`)
- **Closing Sonar's `elif` Blind Spot (`JBC-COG-004`):** Sonar Cognitive Complexity charges only `+1` per `elif` with zero nesting penalty. `JBC-COG-004` caps chains at **$\le 1$ `elif`** (forcing 3+ way routing onto immutable `MappingProxyType` dispatch tables or `match`), bans `else`/`elif` after terminal jumps (`return`/`raise`/`break`/`continue`), and bans mutable module-level dispatch dicts.
- **Mathematical Single Responsibility (`JBC-COG-005`):** Replaces subjective "one reason to change" prose with objective graph & capacity laws: **Role Purity** (no external I/O on `BaseModel`s), **`LCOM4 <= 1`** (Hitz & Montazeri connected state/method components), **zero stateless instance methods**, **$\le 5$ behavioral instance attributes**, **$\le 7$ public methods**, and **class `WMC <= 35`**.

### D. Time-Bounded Sunset Variances & Differential Occupancy Ratchet (`jezz.codes.ratchet`)
- **Statutory Sunset Expiration (`CodeVariance.expires_on`):** Non-`LIFE_SAFETY` variances support an optional `expires_on: datetime.date` sunset deadline evaluated against an explicit `reference_date` (`--as-of YYYY-MM-DD`, defaulting to UTC today). Once `reference_date > expires_on`, `BuildingInspector` automatically flips the discrepancy from `VARIANCE_WAIVED` back to `VIOLATION` (`RED_TAG`) with an explicit `[Expired variance '...' (sunset YYYY-MM-DD)]` citation so temporary waivers never calcify into permanent `# noqa` debt.
- **Differential Occupancy Ratchet (`jezz inspect --changed-since <ref>` / `--staged`):** Enables immediate CI adoption on unrenovated brownfield repositories. `inspect_differential_rough_in` parses the full workspace so cross-module provisions (`JBC-COG-003` Tarjan cycle detection and intra-package enclosure rules) retain whole-graph visibility—catching cycles introduced between a touched module and an untouched legacy module—while scoping per-unit `RED_TAG` enforcement strictly to touched Python files resolved via `ContainedPath`-validated `git merge-base` or staged-index diffs.

### E. Dynamic Path Anchoring & Hardcoded Path Prohibition (`JBC-SEC-006`)
Coding agents trained on ad-hoc scripts frequently bake machine-local absolute paths, OS temp prefixes, home-tilde expansions, or CWD-fragile string-literal `Path("...")` calls directly into modules and generated hooks (`AGT-PATH-01`, `CWE-426`, `ISO/IEC 25010 §4.2.8`):
- **Zero Hardcoded Host/OS Path Literals (`src/` & `tests/`):** `check_sec_006` rejects embedded host-root, home-directory, temporary-directory, and Windows drive-letter path literals across both production and test units (exempting URL schemes, MIME/REST route strings, and non-path identifiers).
- **Dynamic Anchor Derivation in Production Modules:** Production code is prohibited from passing string literals directly to `Path(...)` or `os.path.*(...)` constructors; every filesystem path must be computed dynamically from an explicit anchor (`Path(__file__).resolve()`, `importlib.resources`, `tempfile` / `tmp_path`, injected runtime environment anchors such as `${PLUGIN_ROOT}`, or `ContainedPath.from_parts(anchor, rel)`).

### F. Polyglot CST Inspection & Ecosystem Manifest Grounding (`jezz.codes.adapters`, `polyglot_*`)
While `JBC` Edition `1.2` was originally implemented against Python's stdlib `ast` + `tokenize`, its 31 provisions express **structural, control-flow, boundary-contract, and epistemic laws** rather than Python-only lint style. Track 8 ([track-8-polyglot-inspector.md](track-8-polyglot-inspector.md)) generalizes `BuildingInspector`, `WorkspaceAstGraph`, `inspect_differential_rough_in`, `jezz mcp`, `jezz hook stop`, and `audit_pce_dossier` across **four systems and application languages**:
- **Hexagonal `LanguageAdapterPort` & `InspectedUnit` Union (`adapters.py`, `polyglot_ir.py`):** Pairs `PythonLanguageAdapter` (`SourceUnit`) with `TreeSitterLanguageAdapter` backends backed by official C-extension `tree-sitter` grammars for **TypeScript** (`.ts`, `.tsx`), **Rust** (`.rs`), and **Go** (`.go`), normalizing callables, nominal/schema types, imports, comments, and language-idiomatic CST hazards into `PolyglotUnit` with fail-closed `SyntaxError` rejection on malformed CSTs.
- **Universal Structural Evaluators + Language-Idiomatic Hazard Lowering (`polyglot_cst.py`, `polyglot_rules.py`, `polyglot_ts.py`, `polyglot_rust.py`, `polyglot_go.py`):** Evaluates SonarSource Cognitive Complexity (`JBC-COG-001`), nesting/arity/span (`JBC-COG-002`), iterative Tarjan SCC cycles & private/internal module breaches (`JBC-COG-003`), `else if` dispatch caps (`JBC-COG-004`), Hitz–Montazeri `LCOM4` / `WMC` / schema purity (`JBC-COG-005`), signature/schema bounds (`JBC-DBC-001`..`005`), fault/concurrency/resource envelopes (`JBC-FLT-001`..`006`), sink/schema/path containment (`JBC-SEC-001`..`006`), state determinism (`JBC-OBS-001`..`003`), and anti-theatre verification (`JBC-VRF-001`..`004`) uniformly across all four languages.
- **Four-Ecosystem Manifest Grounding (`polyglot_manifests.py`, `JBC-VRF-003`):** Grounds third-party and workspace imports against `pyproject.toml`/`setup.cfg` (Python), `package.json` + `packages/*`/`apps/*` monorepo workspaces & `@types/*` companions (TypeScript), `Cargo.toml` + `[workspace.members]`/`[workspace.dependencies]` crate renames (Rust), and `go.mod` `module`/`require`/`replace` directives (Go).

---

## 3. Edition 1.2 Code Book — 31 Provisions Across 6 Articles

### Article I: `DBC` — Design by Contract, Signature Breakers & Bounded Domains (5)
| Code | Title | Severity | Waivable | Primary Lineage & Verbatim Anchor |
| :--- | :--- | :--- | :---: | :--- |
| `JBC-DBC-001` | Executable Preconditions, Relational Postconditions & Validators | `structural` | Yes | Meyer DbC §11; MITRE CWE-369 (*"The product divides a value by zero."*) |
| `JBC-DBC-002` | Total Static Type Grounding & Type-Erasure Prohibition | `structural` | Yes | IEEE 1016 §5.6; `AGT-TYPE-01` |
| `JBC-DBC-003` | Explicit Optionality & Total Return Paths | `structural` | No | CWE-476 (*"The product dereferences a pointer that it expects to be valid but is NULL."*); CERT MSC01-C (*"Strive for logical completeness"*) |
| `JBC-DBC-004` | Public Callable Runtime Signature & Return Breakers (Option A) | `structural` | Yes | NASA P10 Rule 7; CWE-20 (*"...does not validate or incorrectly validates that the input has the properties that are required..."*) |
| `JBC-DBC-005` | Bounded Scalar & Collection Domain Primitives | `structural` | Yes | CWE-770 (*"...without imposing any intended restrictions on the size or number of resources..."*); OWASP ASVS V5.1.3 |

### Article II: `FLT` — Fault Isolation, Termination & Resource Envelopes (6)
| Code | Title | Severity | Waivable | Primary Lineage & Verbatim Anchor |
| :--- | :--- | :--- | :---: | :--- |
| `JBC-FLT-001` | Prohibition of Silent Fault Suppression | `life_safety` | No | CWE-390 (*"The product detects a specific error, but takes no actions to handle the error."*); CERT ERR00-J |
| `JBC-FLT-002` | Causal Exception Chaining & Actionable Error Diagnostics | `structural` | Yes | ISO/IEC 25010 §4.2.5; CWE-755 |
| `JBC-FLT-003` | Managed Resource Handles, Sync Timeouts & Bounded Retries | `structural` | Yes | CWE-400 (*"The product does not properly control the allocation and maintenance of a limited resource."*) |
| `JBC-FLT-004` | Guaranteed Loop & Recursion Termination (`LoopBudget` & Fuel) | `structural` | Yes | NASA P10 Rule 2 (*"All loops must have a fixed upper-bound"*); CWE-835; CWE-674 |
| `JBC-FLT-005` | Structured Concurrency & Wall-Clock Cancel Deadlines | `structural` | Yes | CWE-400 (Slowloris & orphan task prevention) |
| `JBC-FLT-006` | Bounded Memory Caches & Stream Allocation Ceilings | `structural` | Yes | CWE-770 |

### Article III: `SEC` — Trust Boundaries, Ingress/Egress & Sink Containment (6)
| Code | Title | Severity | Waivable | Primary Lineage & Verbatim Anchor |
| :--- | :--- | :--- | :---: | :--- |
| `JBC-SEC-001` | Parameterized Sink Execution & Dynamic Eval Prohibition | `life_safety` | No | CWE-89; OWASP ASVS V5.3.4 (*"Verify that data selection or database queries... use parameterized queries..."*) |
| `JBC-SEC-002` | Secret Redaction & Sensitive Payload Containment | `life_safety` | No | OWASP ASVS V7.1.1 (*"Verify that the application does not log credentials or payment details."*); CWE-209/532/798 |
| `JBC-SEC-003` | Strict Closed Boundary Schemas (`extra='forbid'`, `strict=True`, `allow_inf_nan=False`) | `structural` | Yes | CWE-754 (*"...does not check or incorrectly checks for unusual or exceptional conditions..."*); OWASP ASVS V5.1.3 |
| `JBC-SEC-004` | Typed Wire Ingress & Explicit Egress Projection | `structural` | Yes | OWASP `API3:2023` (*"This category combines API3:2019 Excessive Data Exposure and API6:2019 - Mass Assignment..."*); CWE-200 |
| `JBC-SEC-005` | Taint Separation & Canonicalized Sink Types | `life_safety` | No | CWE-22 (*"...uses external input to construct a pathname... underneath a restricted parent directory, but it does not properly neutralize special elements..."*) |
| `JBC-SEC-006` | Dynamic Path Anchoring & Hardcoded Path Prohibition | `structural` | Yes | ISO/IEC 25010 §4.2.8; CWE-426 (*"The application searches for critical resources using an untrusted search path"*); `AGT-PATH-01` |

### Article IV: `COG` — Complexity, Dispatch Tables & Class Duty Isolation (5)
| Code | Title | Severity | Waivable | Primary Lineage & Verbatim Anchor |
| :--- | :--- | :--- | :---: | :--- |
| `JBC-COG-001` | Callable Cognitive Complexity Ceiling ($\le 15$) | `workmanship` | Yes | Sonar (Campbell 2017: *"Increment (add one) for each break in the linear flow... Increment when flow-breaking structures are nested."*) |
| `JBC-COG-002` | Control-Flow Nesting ($\le 4$), Arity ($\le 6$), and Span ($\le 35$ stmts) | `workmanship` | Yes | NASA P10 Rule 1 (*"Restrict all code to very simple control flow constructs"*) & Rule 4; IEEE 1016 |
| `JBC-COG-003` | Acyclic Module Topology, Wildcard Ban & Sealed `_private` Enclosures | `structural` | No | Parnas 1972 (*"Every module in the second decomposition is characterized by its knowledge of a design decision which it hides from all others."*) |
| `JBC-COG-004` | Table-Driven Dispatch (`circuit_board`) & Guard-Clause Discipline | `workmanship` | Yes | McConnell *Code Complete* Ch. 18; Object Calisthenics Rule 2 |
| `JBC-COG-005` | Single-Duty Class Cohesion (`LCOM4 == 1`) & Panel Capacity | `workmanship` | Yes | Hitz & Montazeri `LCOM4` (1995); Chidamber & Kemerer `WMC`/`CBO` (1994) |

### Article V: `OBS` — Observability, State Hygiene & Determinism (3)
| Code | Title | Severity | Waivable | Primary Lineage |
| :--- | :--- | :--- | :---: | :--- |
| `JBC-OBS-001` | Structured Diagnostic Telemetry & Debug Trap Prohibition | `workmanship` | Yes | CWE-489; ISO/IEC 25010 §4.2.7 |
| `JBC-OBS-002` | Immutable Default State & Global State Prohibition | `structural` | No | NASA P10 Rule 6; CWE-665 |
| `JBC-OBS-003` | Deterministic Execution & Controlled Nondeterminism | `workmanship` | Yes | ISO/IEC 25010 Testability; CWE-330 |

### Article VI: `VRF` — Anti-Theatre Verification, Grounding & Watchdogs (6)
| Code | Title | Severity | Waivable | Primary Lineage |
| :--- | :--- | :--- | :---: | :--- |
| `JBC-VRF-001` | Prohibition of Verification Theatre & Tautological Assertions | `life_safety` | No | `AGT-THEATRE-01`; IEEE 29148 §5.2.8 |
| `JBC-VRF-002` | Prohibition of Hollow Stubs & Deferred Placeholders | `life_safety` | No | `AGT-STUB-01` |
| `JBC-VRF-003` | Epistemic Import & Declared Dependency Grounding | `structural` | No | `AGT-HALLUC-01`; CWE-1357 |
| `JBC-VRF-004` | Prohibition of Unpermitted Gate Suppression Directives | `structural` | Yes | `AGT-TAMPER-01` |
| `JBC-VRF-005` | Mandatory Static Soundness Gate Attestation | `life_safety` | No | ISO/IEC 25010 §4.2.5; `AGT-ATTEST-01` |
| `JBC-VRF-006` | Mandatory Dynamic Suite, Watchdog Timeout & Memory Attestation | `life_safety` | No | Meyer DbC §11; CWE-400; `AGT-ATTEST-02` |
