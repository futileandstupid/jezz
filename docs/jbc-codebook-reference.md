# Jezz Software Building Code (`JBC` Edition 1.3) — Reference Manual

> **Auto-generated from [`src/jezz/codes/codebook.py`](https://github.com/futileandstupid/jezz/blob/main/src/jezz/codes/codebook.py)** via `uv run python benchmarks/generate_codebook_docs.py`. Verified in CI against documentation drift (`INV-030-01`).

Edition `1.3` codifies **32 machine-checkable provisions** (`30` static AST/CST/graph Rough-In rules + `2` Final Gate compiler/suite attestations) enforced across **Python (`.py`), TypeScript (`.ts`/`.tsx`), Rust (`.rs`), and Go (`.go`)**.

## Summary Schedule

| Code ID | Article | Severity | Waivable | Method | Title |
| :--- | :---: | :---: | :---: | :---: | :--- |
| [`JBC-COG-001`](#jbc-cog-001) | `COG` | `WORKMANSHIP` | Yes | `ast_static` | Callable Cognitive Complexity Ceiling (<= 15) |
| [`JBC-COG-002`](#jbc-cog-002) | `COG` | `WORKMANSHIP` | Yes | `ast_static` | Control-Flow Nesting, Parameter Arity, and Statement Span Limits |
| [`JBC-COG-003`](#jbc-cog-003) | `COG` | `STRUCTURAL` | **No** | `module_graph` | Acyclic Module Topology, Wildcard Ban & Sealed Private Enclosures |
| [`JBC-COG-004`](#jbc-cog-004) | `COG` | `WORKMANSHIP` | Yes | `ast_static` | Table-Driven Dispatch ('Circuit Board') & Guard-Clause Discipline |
| [`JBC-COG-005`](#jbc-cog-005) | `COG` | `WORKMANSHIP` | Yes | `ast_static` | Single-Duty Class Cohesion (LCOM4 == 1) & Panel Capacity Limits |
| [`JBC-COG-006`](#jbc-cog-006) | `COG` | `STRUCTURAL` | Yes | `module_graph` | Subpackage Quotient Acyclicity, Floorplate Ceilings & Compartment Fire-Walls |
| [`JBC-DBC-001`](#jbc-dbc-001) | `DBC` | `STRUCTURAL` | Yes | `ast_static` | Executable Preconditions, Relational Postconditions & Validators |
| [`JBC-DBC-002`](#jbc-dbc-002) | `DBC` | `STRUCTURAL` | Yes | `ast_static` | Total Static Type Grounding & Type-Erasure Prohibition |
| [`JBC-DBC-003`](#jbc-dbc-003) | `DBC` | `STRUCTURAL` | **No** | `ast_static` | Explicit Optionality & Total Return Paths |
| [`JBC-DBC-004`](#jbc-dbc-004) | `DBC` | `STRUCTURAL` | Yes | `ast_static` | Public Callable Runtime Signature & Return Breakers (Option A) |
| [`JBC-DBC-005`](#jbc-dbc-005) | `DBC` | `STRUCTURAL` | Yes | `ast_static` | Bounded Scalar & Collection Domain Primitives |
| [`JBC-FLT-001`](#jbc-flt-001) | `FLT` | `LIFE_SAFETY` | **No** | `ast_static` | Prohibition of Silent Fault Suppression |
| [`JBC-FLT-002`](#jbc-flt-002) | `FLT` | `STRUCTURAL` | Yes | `ast_static` | Causal Exception Chaining & Actionable Error Diagnostics |
| [`JBC-FLT-003`](#jbc-flt-003) | `FLT` | `STRUCTURAL` | Yes | `ast_static` | Managed Resource Handles, Sync Timeouts & Bounded Retry Discipline |
| [`JBC-FLT-004`](#jbc-flt-004) | `FLT` | `STRUCTURAL` | Yes | `ast_static` | Guaranteed Loop & Recursion Termination (LoopBudget & Fuel Bounds) |
| [`JBC-FLT-005`](#jbc-flt-005) | `FLT` | `STRUCTURAL` | Yes | `ast_static` | Structured Concurrency & Wall-Clock Cancel Deadlines |
| [`JBC-FLT-006`](#jbc-flt-006) | `FLT` | `STRUCTURAL` | Yes | `ast_static` | Bounded Memory Caches & Stream Allocation Ceilings |
| [`JBC-OBS-001`](#jbc-obs-001) | `OBS` | `WORKMANSHIP` | Yes | `ast_static` | Structured Diagnostic Telemetry & Debug Trap Prohibition |
| [`JBC-OBS-002`](#jbc-obs-002) | `OBS` | `STRUCTURAL` | **No** | `ast_static` | Immutable Default State & Global State Prohibition |
| [`JBC-OBS-003`](#jbc-obs-003) | `OBS` | `WORKMANSHIP` | Yes | `ast_static` | Deterministic Execution & Controlled Nondeterminism |
| [`JBC-SEC-001`](#jbc-sec-001) | `SEC` | `LIFE_SAFETY` | **No** | `ast_static` | Parameterized Sink Execution & Dynamic Eval Prohibition |
| [`JBC-SEC-002`](#jbc-sec-002) | `SEC` | `LIFE_SAFETY` | **No** | `ast_static` | Secret Redaction & Sensitive Payload Containment |
| [`JBC-SEC-003`](#jbc-sec-003) | `SEC` | `STRUCTURAL` | Yes | `ast_static` | Strict Closed Boundary Schemas (extra='forbid', strict=True, allow_inf_nan=False) |
| [`JBC-SEC-004`](#jbc-sec-004) | `SEC` | `STRUCTURAL` | Yes | `ast_static` | Typed Wire Ingress & Explicit Egress Projection |
| [`JBC-SEC-005`](#jbc-sec-005) | `SEC` | `LIFE_SAFETY` | **No** | `ast_static` | Taint Separation & Canonicalized Sink Types |
| [`JBC-SEC-006`](#jbc-sec-006) | `SEC` | `STRUCTURAL` | Yes | `ast_static` | Dynamic Path Anchoring & Hardcoded Path Prohibition |
| [`JBC-VRF-001`](#jbc-vrf-001) | `VRF` | `LIFE_SAFETY` | **No** | `ast_static` | Prohibition of Verification Theatre & Tautological Assertions |
| [`JBC-VRF-002`](#jbc-vrf-002) | `VRF` | `LIFE_SAFETY` | **No** | `token_lexical` | Prohibition of Hollow Stubs & Deferred Placeholders |
| [`JBC-VRF-003`](#jbc-vrf-003) | `VRF` | `STRUCTURAL` | **No** | `module_graph` | Epistemic Import & Declared Dependency Grounding |
| [`JBC-VRF-004`](#jbc-vrf-004) | `VRF` | `STRUCTURAL` | Yes | `token_lexical` | Prohibition of Unpermitted Gate Suppression Directives |
| [`JBC-VRF-005`](#jbc-vrf-005) | `VRF` | `LIFE_SAFETY` | **No** | `gate_attestation` | Mandatory Static Soundness Gate Attestation |
| [`JBC-VRF-006`](#jbc-vrf-006) | `VRF` | `LIFE_SAFETY` | **No** | `gate_attestation` | Mandatory Dynamic Suite, Watchdog Timeout & Memory Attestation |

---

## Article I — Design by Contract & Signature Breakers (`DBC`)

### `JBC-DBC-001` — Executable Preconditions, Relational Postconditions & Validators {#jbc-dbc-001}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `constrained_domain`, `universal` | **Enforced At:** `verify_dynamic`, `verify_static`
- **Normative Rule:** Callables dividing by parameters SHALL enforce preconditions or zero-guards; @icontract.require/@ensure predicates SHALL NOT be constant literals or omit 'result' in postconditions; and Pydantic validators SHALL NOT be no-ops.
- **Prevented Hazard:** ZeroDivisionError crashes, silent invariant drift, and false assurance from vacuous contract or validator decorators.
- **Objective Pass Criteria:** Zero unguarded parameter divisors, zero constant-literal or result-ignoring icontract lambdas, and zero no-op Pydantic validators.
- **Authoritative Standards Lineage:**
  - `Design by Contract (Meyer)` (OOSC2 §11): Preconditions bind callers; postconditions bind supplier return values.
  - `MITRE CWE` (CWE-369): Divide by zero and unvalidated denominator hazards.

### `JBC-DBC-002` — Total Static Type Grounding & Type-Erasure Prohibition {#jbc-dbc-002}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Every callable parameter (excluding receiver self/cls) and return signature SHALL carry an explicit static type annotation, and source artifacts SHALL NOT erase types via typing.Any.
- **Prevented Hazard:** Unchecked runtime AttributeError/TypeError faults and agentic type-erasure bypasses that blind static verification.
- **Objective Pass Criteria:** 100% parameter and return annotation coverage across all functions/methods and zero imports or annotation uses of Any.
- **Authoritative Standards Lineage:**
  - `IEEE 1016` (§5.6): Module interface specifications require explicit typed signatures.
  - `Jezz Agentic Safety` (AGT-TYPE-01): Prevents LLMs from injecting Any to silence strict typecheckers.

### `JBC-DBC-003` — Explicit Optionality & Total Return Paths {#jbc-dbc-003}

- **Severity:** `STRUCTURAL` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Value-returning callables SHALL terminate every control-flow path with an explicit return or raise statement and SHALL NOT mix value returns with bare return statements.
- **Prevented Hazard:** Accidental implicit None fallthrough causing downstream nil-dereference panics (AttributeError on NoneType).
- **Objective Pass Criteria:** Zero mixed value/bare returns and zero implicit end-of-function fallthroughs on non-None-annotated callables.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-476): NULL/None dereference from partial return paths.
  - `SEI CERT` (MSC01-C): Ensure conditional and return paths are logically complete.

### `JBC-DBC-004` — Public Callable Runtime Signature & Return Breakers (Option A) {#jbc-dbc-004}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `universal` | **Enforced At:** `verify_dynamic`, `verify_static`
- **Normative Rule:** Public functions and public methods in production modules SHALL enforce strict runtime argument and return alignment via @jezz_callable (or @jezz_generator on iterators); intra-module _private helpers are excluded to prevent O(N^2) container re-validation, iterator exhaustion, and variance fatigue.
- **Prevented Hazard:** Inert Python __annotations__ allowing invalid dynamic inputs, bool-as-int leaks, or broken supplier return values to cross module boundaries undetected.
- **Objective Pass Criteria:** 100% of public non-exempt production callables carry @jezz_callable, @jezz_generator, or strict @validate_call.
- **Authoritative Standards Lineage:**
  - `NASA JPL Power of 10` (Rule 7): Validate all parameters and check non-void return values.
  - `MITRE CWE` (CWE-20): Runtime validation of incoming callable arguments.

### `JBC-DBC-005` — Bounded Scalar & Collection Domain Primitives {#jbc-dbc-005}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `constrained_domain`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Production BaseModel fields, TypeAliases, and public scalar parameters over str, bytes, int, float, list, set, frozenset, tuple[..., ...], Sequence, or Mapping SHALL declare explicit size/range/finiteness bounds via Field() or Annotated domain aliases.
- **Prevented Hazard:** Primitive obsession allowing 500 MB strings, unbounded collections, negative counts, or NaN/Inf floats to trigger OOM or domain corruption.
- **Objective Pass Criteria:** Zero bare unbounded str/bytes/int/float/collection fields on BaseModels or public production parameters.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-770): Bound size and cardinality of accepted data structures.
  - `OWASP ASVS` (V5.1.3): Positive allowlist validation of lengths and ranges.

---

## Article II — Fault Containment, Resource Ceilings, & Termination (`FLT`)

### `JBC-FLT-001` — Prohibition of Silent Fault Suppression {#jbc-flt-001}

- **Severity:** `LIFE_SAFETY` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Exception handlers SHALL NOT use bare except clauses, empty pass/ellipsis bodies, or broad Exception/BaseException suppression that discards fault state without re-raising or structured fault recording.
- **Prevented Hazard:** Corrupted program state continuing past a tripped fault ('penny behind the fuse'), destroying debuggability and masking root causes.
- **Objective Pass Criteria:** Zero bare except blocks, zero pass/ellipsis exception bodies, and zero silent broad Exception catches or contextlib.suppress(Exception) calls.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-390): Error detected with zero handling action.
  - `SEI CERT` (ERR00-J): Never ignore caught exceptions silently.
  - `Jezz Agentic Safety` (AGT-SWALLOW-01): Prevents agents from wrapping failing calls in try/except/pass.

### `JBC-FLT-002` — Causal Exception Chaining & Actionable Error Diagnostics {#jbc-flt-002}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Raised exceptions SHALL instantiate specific exception subclasses with non-empty diagnostic messages, and replacement exceptions raised inside except handlers SHALL preserve causal provenance via 'raise ... from ...'.
- **Prevented Hazard:** Severed stack-trace causality and opaque, message-free crashes that prevent post-mortem fault tracing.
- **Objective Pass Criteria:** Zero unchained replacement raises in except blocks, zero generic Exception() raises, and zero empty-message exception instantiations.
- **Authoritative Standards Lineage:**
  - `ISO/IEC 25010` (§4.2.5 & §4.2.7): Fault tolerance and diagnostic analysability of failure transitions.
  - `MITRE CWE` (CWE-755): Improper handling of exceptional conditions.

### `JBC-FLT-003` — Managed Resource Handles, Sync Timeouts & Bounded Retry Discipline {#jbc-flt-003}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `external_boundary`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** System handles (open, socket) SHALL be acquired inside 'with' blocks; blocking sync subprocess/HTTP calls SHALL specify 'timeout='; and @retry decorators or try/except/continue loops SHALL declare finite attempt caps and narrow exceptions.
- **Prevented Hazard:** File-descriptor leaks, stalled sync worker deadlocks, and infinite retry storms (such as bare @tenacity.retry on Exception).
- **Objective Pass Criteria:** Zero unmanaged open/socket calls, zero timeout-free sync subprocess/HTTP calls, and zero unbounded or broad-Exception retries.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-400): Bound resource lifecycles, I/O waits, and retry storms.

### `JBC-FLT-004` — Guaranteed Loop & Recursion Termination (LoopBudget & Fuel Bounds) {#jbc-flt-004}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Every while loop SHALL invoke a deterministic LoopBudget.tick() step guard, and direct or mutual recursion (including AST NodeVisitors) SHALL enforce an explicit strictly decreasing fuel/depth bound or RecursionBudget; unbounded recursion is prohibited.
- **Prevented Hazard:** Non-progressing infinite while loops (CWE-835) and RecursionError stack exhaustion on deeply nested adversarial inputs (CWE-674).
- **Objective Pass Criteria:** 100% of while loops tick a LoopBudget and 100% of recursive call cycles / visitors enforce an explicit depth/fuel ceiling.
- **Authoritative Standards Lineage:**
  - `NASA JPL Power of 10` (Rule 1 & Rule 2): Bounded control flow and statically verifiable loop upper bounds.
  - `MITRE CWE` (CWE-835): Prevent infinite loops with unreachable exit conditions.
  - `MITRE CWE` (CWE-674): Bound recursion depth with explicit fuel.

### `JBC-FLT-005` — Structured Concurrency & Wall-Clock Cancel Deadlines {#jbc-flt-005}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `external_boundary`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Modules SHALL NOT spawn fire-and-forget tasks or threads (asyncio.create_task, ensure_future, loop.create_task, Thread.start) outside structured TaskGroups, and async functions awaiting external I/O SHALL enforce an asyncio.timeout or fail_after wall-clock deadline.
- **Prevented Hazard:** Orphaned background coroutines draining CPU/sockets and Slowloris drip-read hangs that bypass per-packet socket timeouts.
- **Objective Pass Criteria:** Zero unstructured task/thread spawns and 100% wall-clock deadline coverage on external async I/O coroutines.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-400): Prevent Slowloris hangs and leaked background execution.

### `JBC-FLT-006` — Bounded Memory Caches & Stream Allocation Ceilings {#jbc-flt-006}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `external_boundary`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Modules SHALL NOT use unbounded memoization (@functools.cache or @lru_cache(maxsize=None)), and production modules SHALL NOT invoke unbounded read(), readlines(), read_text(), read_bytes(), or fetchall() calls without explicit byte or row limits.
- **Prevented Hazard:** Monotonic cache memory leaks in long-lived processes and container OOM crashes when reading oversized files, streams, or query result sets.
- **Objective Pass Criteria:** Zero unbounded cache decorators and zero uncapped stream/file/DB slurp calls in production code.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-770): Impose explicit byte and count ceilings on memory allocations.

---

## Article III — Security, Schema Closure, & Path Containment (`SEC`)

### `JBC-SEC-001` — Parameterized Sink Execution & Dynamic Eval Prohibition {#jbc-sec-001}

- **Severity:** `LIFE_SAFETY` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `external_boundary`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Source artifacts SHALL NOT invoke eval, exec, unsafe deserializers (pickle, marshal, unsafe yaml.load), shell=True subprocesses, or string-interpolated SQL execution sinks.
- **Prevented Hazard:** Arbitrary code execution (RCE), OS command injection, and SQL injection across untrusted data boundaries.
- **Objective Pass Criteria:** Zero calls to banned dynamic execution/deserialization primitives, zero shell=True invocations, and zero interpolated/concatenated SQL execute queries.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-89): Neutralize SQL injection via parameterized queries.
  - `OWASP ASVS` (V5.3.4): Parameterized database queries prevent injection.

### `JBC-SEC-002` — Secret Redaction & Sensitive Payload Containment {#jbc-sec-002}

- **Severity:** `LIFE_SAFETY` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `sensitive_context`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Source code SHALL NOT embed credential literals, interpolate sensitive variables (passwords, tokens, keys, SSNs) into log/print/exception strings, or expose plain sensitive fields on Pydantic models without SecretStr or repr=False.
- **Prevented Hazard:** Credential exfiltration through VCS history, crash tracebacks, structured logs, or serialized model representations.
- **Objective Pass Criteria:** Zero hardcoded secrets, zero sensitive variable interpolations in logs/exceptions, and 100% redaction on sensitive BaseModel fields.
- **Authoritative Standards Lineage:**
  - `OWASP ASVS` (V7.1.1): Credentials and tokens must never be logged in cleartext.
  - `MITRE CWE` (CWE-209 / CWE-532 / CWE-798): Sensitive information exposure in error messages, logs, and literals.

### `JBC-SEC-003` — Strict Closed Boundary Schemas (extra='forbid', strict=True, allow_inf_nan=False) {#jbc-sec-003}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `constrained_domain`, `external_boundary` | **Enforced At:** `verify_static`
- **Normative Rule:** Every Pydantic BaseModel schema SHALL enforce extra='forbid', strict=True, and allow_inf_nan=False (via STRICT_FROZEN_CONFIG, STRICT_MUTABLE_CONFIG, or explicit ConfigDict) so undeclared keys, implicit type coercions, and NaN/Inf fail closed.
- **Prevented Hazard:** Mass-assignment smuggling, silent type coercion ('false' -> True), and NaN/Inf poisoning of downstream domain calculations.
- **Objective Pass Criteria:** 100% of BaseModel subclasses enforce extra='forbid', strict=True, and allow_inf_nan=False.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-754): Reject exceptional NaN/Infinity and coerced boundary conditions.
  - `OWASP ASVS` (V5.1.3): Strict positive schema validation.

### `JBC-SEC-004` — Typed Wire Ingress & Explicit Egress Projection {#jbc-sec-004}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `external_boundary`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Production modules SHALL NOT parse JSON via untyped json.loads/json.load (use model_validate_json or TypeAdapter.validate_json), and public callables SHALL NOT accept or return untyped dict[str, object] / Mapping[str, object] payload bags.
- **Prevented Hazard:** Unvalidated dict soup entering domain logic and accidental over-exposure of sensitive internal attributes across egress boundaries (OWASP API3:2023).
- **Objective Pass Criteria:** Zero json.load/loads calls in production code and zero untyped object-bag dict/Mapping parameters or return types on public callables.
- **Authoritative Standards Lineage:**
  - `OWASP API Security Top 10` (API3:2023): Prevent Excessive Data Exposure and Mass Assignment via explicit schemas.
  - `MITRE CWE` (CWE-200): Block unauthorized exposure of internal fields at egress.

### `JBC-SEC-005` — Taint Separation & Canonicalized Sink Types {#jbc-sec-005}

- **Severity:** `LIFE_SAFETY` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `external_boundary`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Production filesystem and network sink operations SHALL require canonicalized containment types (such as ContainedPath verified via is_relative_to or validated URL types) and SHALL NOT pass raw str paths or raw str URLs directly to sinks.
- **Prevented Hazard:** Directory traversal ('../../etc/passwd') escaping workspace sandboxes (CWE-22) and Server-Side Request Forgery (SSRF, CWE-918).
- **Objective Pass Criteria:** Zero raw str filesystem sink calls (open/remove/unlink/rmtree) and zero raw str URL HTTP client calls in production modules.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-22): Canonicalize and confine paths inside an explicit root directory.

### `JBC-SEC-006` — Dynamic Path Anchoring & Hardcoded Path Prohibition {#jbc-sec-006}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `external_boundary`, `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Modules SHALL NOT embed hardcoded host or OS filesystem path literals (such as '/Users/...', '/home/...', '/tmp/...', '~/...', or drive roots) and production modules SHALL NOT pass string literals directly to Path/os.path constructors; all filesystem paths SHALL be calculated dynamically from known file, package, or injected root anchors.
- **Prevented Hazard:** Machine-coupled artifacts and CWD-fragile filesystem lookups that fail across clones, containers, CI runners, and working directories.
- **Objective Pass Criteria:** Zero hardcoded host/OS filesystem path literals across source and tests, and zero unanchored string-literal Path/os.path constructor calls in production modules.
- **Authoritative Standards Lineage:**
  - `ISO/IEC 25010` (§4.2.8): Portability, adaptability, and environment-independent installation.
  - `MITRE CWE` (CWE-426): Untrusted or unanchored search path resolution hazards.
  - `Jezz Agentic Safety` (AGT-PATH-01): Prevents coding agents from baking machine-local or CWD-relative paths.

---

## Article IV — Cognitive Complexity, Cohesion, & Compartmentation (`COG`)

### `JBC-COG-001` — Callable Cognitive Complexity Ceiling (<= 15) {#jbc-cog-001}

- **Severity:** `WORKMANSHIP` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** No function or method SHALL exceed a Sonar-style Cognitive Complexity score of 15 accounting for control-flow breaks, boolean operator chains, recursion, and nesting depth penalties.
- **Prevented Hazard:** Opaque, deeply tangled control flow where neither human reviewers nor coding agents can reason soundly about all execution paths.
- **Objective Pass Criteria:** Every function and method has computed Cognitive Complexity <= 15.
- **Authoritative Standards Lineage:**
  - `Sonar Cognitive Complexity` (Campbell 2017): Penalize nested breaks in linear control flow.
  - `ISO/IEC 25010` (§4.2.7): Maintainability, analysability, and modifiability.

### `JBC-COG-002` — Control-Flow Nesting, Parameter Arity, and Statement Span Limits {#jbc-cog-002}

- **Severity:** `WORKMANSHIP` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Callables SHALL NOT exceed control-flow nesting depth 4, non-receiver parameter count 6, or 35 executable AST statements.
- **Prevented Hazard:** Arrow-code nesting pyramids, positional parameter confusion, and monolithic multi-responsibility procedures.
- **Objective Pass Criteria:** Every callable satisfies max_nesting_depth <= 4, parameter_count <= 6, and statement_count <= 35.
- **Authoritative Standards Lineage:**
  - `NASA JPL Power of 10` (Rule 1 & Rule 4): Simple control flow and single-sheet callable size.
  - `IEEE 1016` (§5): Bounded module interface arity and procedural cohesion.

### `JBC-COG-003` — Acyclic Module Topology, Wildcard Ban & Sealed Private Enclosures {#jbc-cog-003}

- **Severity:** `STRUCTURAL` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `module_graph` | **Origin:** `extended_hybrid`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Internal runtime module import graphs SHALL form a strict DAG; modules SHALL NOT use wildcard imports; and modules SHALL NOT import _private symbols across module boundaries.
- **Prevented Hazard:** Import-time initialization cycles, namespace pollution, and callers bypassing public @jezz_callable breakers via cross-module _private imports.
- **Objective Pass Criteria:** Zero runtime import cycles, zero wildcard imports, and zero cross-module _private symbol imports.
- **Authoritative Standards Lineage:**
  - `Parnas Modularity` (Parnas 1972): Modules hide internal secrets behind sealed public interfaces.
  - `ISO/IEC 25010` (§4.2.7): Modularity and bounded inter-component coupling.

### `JBC-COG-004` — Table-Driven Dispatch ('Circuit Board') & Guard-Clause Discipline {#jbc-cog-004}

- **Severity:** `WORKMANSHIP` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Conditional chains SHALL NOT exceed 1 elif branch (use MappingProxyType dispatch tables, @singledispatch, or exhaustive match); if blocks ending in return/raise/break/continue SHALL NOT be followed by else/elif; and module-level callable dispatch tables SHALL be immutable via MappingProxyType.
- **Prevented Hazard:** Unreadable if/elif ladders that evade Cognitive Complexity, redundant post-return else indentation, and mutable dispatch dicts vulnerable to runtime tampering.
- **Objective Pass Criteria:** Zero if statements with >= 2 elif branches, zero else/elif blocks after terminal jumps, and zero mutable module-level callable dispatch dicts.
- **Authoritative Standards Lineage:**
  - `McConnell Table-Driven Methods` (Code Complete Ch. 18): Replace multi-branch conditionals with declarative lookup tables.
  - `Sonar Cognitive Complexity` (Campbell 2017): Closes Sonar's flat-elif blind spot via hard dispatch limits.

### `JBC-COG-005` — Single-Duty Class Cohesion (LCOM4 == 1) & Panel Capacity Limits {#jbc-cog-005}

- **Severity:** `WORKMANSHIP` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Classes SHALL serve strictly one duty: BaseModel/value schemas SHALL NOT perform external I/O, and behavioral classes SHALL satisfy LCOM4 <= 1, zero stateless instance methods, <= 5 instance attributes, <= 7 public methods, and WMC <= 35.
- **Prevented Hazard:** Scope-creeping God classes, shoehorned stateless utility methods, Active-Record schema/IO hybrids, and multi-domain orchestrators.
- **Objective Pass Criteria:** 100% of classes satisfy role purity, LCOM4 <= 1, zero stateless methods, <= 5 behavioral fields, <= 7 public methods, and WMC <= 35.
- **Authoritative Standards Lineage:**
  - `Hitz & Montazeri LCOM4` (Hitz & Montazeri 1995): LCOM4 > 1 proves disconnected method/state islands inside one class.
  - `Chidamber & Kemerer OO Metrics` (WMC & CBO (1994)): Bound Weighted Methods per Class and collaborator fan-out.
  - `Parnas Modularity` (Parnas 1972): One design secret per class/module.

### `JBC-COG-006` — Subpackage Quotient Acyclicity, Floorplate Ceilings & Compartment Fire-Walls {#jbc-cog-006}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `module_graph` | **Origin:** `extended_hybrid`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Subpackage compartment quotient graphs (G/~) SHALL form a strict DAG; compartments and packages SHALL NOT exceed profile floorplate or external portal-ratio ceilings; and workspace members SHALL NOT traverse sibling relative paths or private internals.
- **Prevented Hazard:** Cross-folder import cycles that evade file-level DAG checks, sprawling unpartitioned god-packages, sham folder splits with porous surfaces, and leaky monorepo boundaries.
- **Objective Pass Criteria:** Zero Tarjan SCCs in compartment quotient graph G/~, module counts and portal ratios within profile ceilings, and zero cross-member relative or internal imports.
- **Authoritative Standards Lineage:**
  - `Parnas Modularity` (Parnas 1972): Information-hiding compartment walls and minimal exported portals.
  - `ISO/IEC 25010` (§4.2.7): Modularity and bounded inter-subsystem propagation cost.
  - `Jezz Agentic Safety` (AGT-COMPART-01): Bounds cold-start localization search and closes cross-folder cycles.

---

## Article V — Observability, Determinism, & State Hygiene (`OBS`)

### `JBC-OBS-001` — Structured Diagnostic Telemetry & Debug Trap Prohibition {#jbc-obs-001}

- **Severity:** `WORKMANSHIP` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Production modules SHALL NOT invoke unstructured print() output or interactive debugger traps (breakpoint, pdb.set_trace).
- **Prevented Hazard:** Production process suspension on stdin debugger traps and uncorrelatable stdout noise lacking severity, timestamps, or trace context.
- **Objective Pass Criteria:** Zero print(), breakpoint(), or pdb/ipdb set_trace() calls in non-test code.
- **Authoritative Standards Lineage:**
  - `MITRE CWE` (CWE-489): Active debug code left in deployable software artifacts.
  - `ISO/IEC 25010` (§4.2.7): Structured diagnostic analysability.

### `JBC-OBS-002` — Immutable Default State & Global State Prohibition {#jbc-obs-002}

- **Severity:** `STRUCTURAL` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `ast_static` | **Origin:** `established_standard`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Callable signatures SHALL NOT bind mutable default objects (list, dict, set, deque, bytearray), and functions SHALL NOT mutate module globals via the 'global' statement.
- **Prevented Hazard:** Cross-call state contamination, non-reentrant race conditions, and hidden action-at-a-distance side effects.
- **Objective Pass Criteria:** Zero mutable literal/constructor default arguments and zero 'global' statements.
- **Authoritative Standards Lineage:**
  - `NASA JPL Power of 10` (Rule 6): Declare data objects at the smallest possible scope.
  - `MITRE CWE` (CWE-665): Improper initialization and shared mutable state across invocations.

### `JBC-OBS-003` — Deterministic Execution & Controlled Nondeterminism {#jbc-obs-003}

- **Severity:** `WORKMANSHIP` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `ast_static` | **Origin:** `extended_hybrid`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Modules SHALL NOT invoke unseeded module-global random.* functions or mutate process-global os.environ state inside production logic.
- **Prevented Hazard:** Irreproducible Heisenbugs, flaky verification runs, and process-wide environment corruption across concurrent tasks.
- **Objective Pass Criteria:** Zero direct calls to global random.* generators and zero os.environ mutations in non-test source files.
- **Authoritative Standards Lineage:**
  - `ISO/IEC 25010` (§4.2.7): Deterministic testability and replayable execution.
  - `MITRE CWE` (CWE-330): Uncontrolled nondeterministic PRNG usage.

---

## Article VI — Verification Non-Vacuity & Final Gate Attestations (`VRF`)

### `JBC-VRF-001` — Prohibition of Verification Theatre & Tautological Assertions {#jbc-vrf-001}

- **Severity:** `LIFE_SAFETY` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `ast_static` | **Origin:** `net_new_agentic`
- **Triggers:** `verification_suite` | **Enforced At:** `verify_static`
- **Normative Rule:** Every test_* function SHALL evaluate at least one non-vacuous verification check and SHALL NOT assert constant literals, tuple-trap expressions, or reflexive identities (e.g., assert True, assert x == x).
- **Prevented Hazard:** QA theatre and LLM reward-hacking where suites report 100% green while testing zero actual postconditions.
- **Objective Pass Criteria:** Every test_* callable contains >= 1 real assertion/raises check and zero constant, tuple-trap, or reflexive assertions.
- **Authoritative Standards Lineage:**
  - `Jezz Agentic Safety` (AGT-THEATRE-01): Eliminates assertion-free and tautological LLM test generation.
  - `IEEE 29148` (§5.2.8): Requirements and tests must be objectively falsifiable.

### `JBC-VRF-002` — Prohibition of Hollow Stubs & Deferred Placeholders {#jbc-vrf-002}

- **Severity:** `LIFE_SAFETY` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `token_lexical` | **Origin:** `net_new_agentic`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Non-abstract production callables SHALL NOT consist solely of pass, ellipsis, or NotImplementedError stubs, and source comments SHALL NOT retain unresolved TODO, FIXME, XXX, HACK, or PLACEHOLDER markers.
- **Prevented Hazard:** Premature completion claims where coding agents leave unimplemented stub bodies behind seemingly typed interfaces.
- **Objective Pass Criteria:** Zero non-abstract stub callables and zero deferred placeholder comment tokens.
- **Authoritative Standards Lineage:**
  - `Jezz Agentic Safety` (AGT-STUB-01): Detects LLM elision, lazy stubs, and deferred implementation markers.

### `JBC-VRF-003` — Epistemic Import & Declared Dependency Grounding {#jbc-vrf-003}

- **Severity:** `STRUCTURAL` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `module_graph` | **Origin:** `net_new_agentic`
- **Triggers:** `universal` | **Enforced At:** `verify_static`
- **Normative Rule:** Every imported root package SHALL resolve to the Python standard library, a first-party workspace package, or a dependency explicitly declared for that artifact's scope in pyproject.toml.
- **Prevented Hazard:** Hallucinated third-party packages (slopsquatting attack surface), dev-only test packages leaked into production modules, and undeclared transitive coupling.
- **Objective Pass Criteria:** 100% of import statements resolve to stdlib, workspace roots, or declared manifest dependencies matching production vs. test scope.
- **Authoritative Standards Lineage:**
  - `Jezz Agentic Safety` (AGT-HALLUC-01): Prevents hallucinated imports and undeclared shadow dependencies.
  - `MITRE CWE` (CWE-1357): Reliance on undeclared or insufficiently governed components.

### `JBC-VRF-004` — Prohibition of Unpermitted Gate Suppression Directives {#jbc-vrf-004}

- **Severity:** `STRUCTURAL` | **Waivable:** Yes (`CodeVariance` with justification/sunset)
- **Inspection Method:** `token_lexical` | **Origin:** `net_new_agentic`
- **Triggers:** `universal`, `verification_suite` | **Enforced At:** `verify_static`
- **Normative Rule:** Source and test files SHALL NOT embed inline gate-silencing directives (# noqa, # type: ignore, # pyright: ignore, # pragma: no cover) or unconditional test skips without an active authority-approved CodeVariance.
- **Prevented Hazard:** Agents silencing typecheckers, linters, coverage, or failing tests instead of remediating the underlying defect.
- **Objective Pass Criteria:** Zero unwaived suppression comments or test-skip decorators across all artifacts.
- **Authoritative Standards Lineage:**
  - `Jezz Agentic Safety` (AGT-TAMPER-01): Prevents autonomous suppression of static and dynamic safety gates.

### `JBC-VRF-005` — Mandatory Static Soundness Gate Attestation {#jbc-vrf-005}

- **Severity:** `LIFE_SAFETY` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `gate_attestation` | **Origin:** `extended_hybrid`
- **Triggers:** `verification_suite` | **Enforced At:** `certify`, `verify_static`
- **Normative Rule:** Final occupancy certification SHALL include a passing static type-verification attestation (exit_code == 0) over the current workspace state.
- **Prevented Hazard:** Issuing certification on code that passes local AST heuristics while failing cross-module type soundness.
- **Objective Pass Criteria:** At least one passing 'static_types' GateAttestation with exit_code == 0.
- **Authoritative Standards Lineage:**
  - `ISO/IEC 25010` (§4.2.5): Static soundness verification prior to release.
  - `Jezz Agentic Safety` (AGT-ATTEST-01): Requires empirical typechecker execution proof prior to CERTIFY.

### `JBC-VRF-006` — Mandatory Dynamic Suite, Watchdog Timeout & Memory Attestation {#jbc-vrf-006}

- **Severity:** `LIFE_SAFETY` | **Waivable:** **No** (Non-Waivable)
- **Inspection Method:** `gate_attestation` | **Origin:** `extended_hybrid`
- **Triggers:** `verification_suite` | **Enforced At:** `certify`, `verify_dynamic`
- **Normative Rule:** Final occupancy certification SHALL include a passing dynamic suite attestation (exit_code == 0, checks_executed >= 1) within configured wall-clock duration and peak memory watchdog ceilings.
- **Prevented Hazard:** Certifying unexecuted or zero-test suites, or suites exhibiting runaway latency or memory blowups under dynamic execution.
- **Objective Pass Criteria:** At least one passing 'dynamic_suite' GateAttestation with checks_executed >= 1, 0 < duration_ms <= max_duration_ms, and 0 < peak_memory_kb <= max_memory_kb.
- **Authoritative Standards Lineage:**
  - `Design by Contract (Meyer)` (OOSC2 §11): Runtime contract monitoring verifies supplier postconditions.
  - `MITRE CWE` (CWE-400): Empirical runtime and memory watchdog enforcement.
  - `Jezz Agentic Safety` (AGT-ATTEST-02): Blocks zero-test collection and unmeasured suite certification.
