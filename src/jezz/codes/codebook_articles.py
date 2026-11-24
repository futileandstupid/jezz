"""Articles I-III (DBC, FLT, SEC) of Edition 1.2 of the Jezz Software Building Code (JBC)."""

from typing import Final

from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.taxonomy import (
    BuildingCode,
    CodeArticle,
    CodeOrigin,
    CodeSeverity,
    CodeTrigger,
    InspectionMethod,
    StandardBody,
    StandardReference,
)

ARTICLES_I_TO_III_CODES: Final[tuple[BuildingCode, ...]] = (
    # --- Article I: DBC — Design by Contract, Signature Breakers & Bounded Domains (5) ---
    BuildingCode(
        code_id="JBC-DBC-001",
        article=CodeArticle.DBC,
        title="Executable Preconditions, Relational Postconditions & Validators",
        normative_rule=(
            "Callables dividing by parameters SHALL enforce preconditions or zero-guards; "
            "@icontract.require/@ensure predicates SHALL NOT be constant literals or omit "
            "'result' in postconditions; and Pydantic validators SHALL NOT be no-ops."
        ),
        hazard=(
            "ZeroDivisionError crashes, silent invariant drift, and false assurance from "
            "vacuous contract or validator decorators."
        ),
        pass_criteria=(
            "Zero unguarded parameter divisors, zero constant-literal or result-ignoring "
            "icontract lambdas, and zero no-op Pydantic validators."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.CONSTRAINED_DOMAIN}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC, BehaviorKind.VERIFY_DYNAMIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.MEYER_DBC,
                clause="OOSC2 §11",
                summary="Preconditions bind callers; postconditions bind supplier return values.",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-369",
                summary="Divide by zero and unvalidated denominator hazards.",
                quote="The product divides a value by zero.",
                source_url="https://cwe.mitre.org/data/definitions/369.html",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-DBC-002",
        article=CodeArticle.DBC,
        title="Total Static Type Grounding & Type-Erasure Prohibition",
        normative_rule=(
            "Every callable parameter (excluding receiver self/cls) and return signature SHALL "
            "carry an explicit static type annotation, and source artifacts SHALL NOT erase "
            "types via typing.Any."
        ),
        hazard=(
            "Unchecked runtime AttributeError/TypeError faults and agentic type-erasure "
            "bypasses that blind static verification."
        ),
        pass_criteria=(
            "100% parameter and return annotation coverage across all functions/methods and "
            "zero imports or annotation uses of Any."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.IEEE_1016,
                clause="§5.6",
                summary="Module interface specifications require explicit typed signatures.",
            ),
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-TYPE-01",
                summary="Prevents LLMs from injecting Any to silence strict typecheckers.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-DBC-003",
        article=CodeArticle.DBC,
        title="Explicit Optionality & Total Return Paths",
        normative_rule=(
            "Value-returning callables SHALL terminate every control-flow path with an explicit "
            "return or raise statement and SHALL NOT mix value returns with bare return "
            "statements."
        ),
        hazard=(
            "Accidental implicit None fallthrough causing downstream nil-dereference panics "
            "(AttributeError on NoneType)."
        ),
        pass_criteria=(
            "Zero mixed value/bare returns and zero implicit end-of-function fallthroughs on "
            "non-None-annotated callables."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-476",
                summary="NULL/None dereference from partial return paths.",
                quote="The product dereferences a pointer that it expects to be valid but is NULL.",
                source_url="https://cwe.mitre.org/data/definitions/476.html",
            ),
            StandardReference(
                body=StandardBody.SEI_CERT,
                clause="MSC01-C",
                summary="Ensure conditional and return paths are logically complete.",
                quote="Strive for logical completeness",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-DBC-004",
        article=CodeArticle.DBC,
        title="Public Callable Runtime Signature & Return Breakers (Option A)",
        normative_rule=(
            "Public functions and public methods in production modules SHALL enforce strict "
            "runtime argument and return alignment via @jezz_callable (or @jezz_generator on "
            "iterators); intra-module _private helpers are excluded to prevent O(N^2) container "
            "re-validation, iterator exhaustion, and variance fatigue."
        ),
        hazard=(
            "Inert Python __annotations__ allowing invalid dynamic inputs, bool-as-int leaks, "
            "or broken supplier return values to cross module boundaries undetected."
        ),
        pass_criteria=(
            "100% of public non-exempt production callables carry @jezz_callable, "
            "@jezz_generator, or strict @validate_call."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC, BehaviorKind.VERIFY_DYNAMIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.NASA_JPL_P10,
                clause="Rule 7",
                summary="Validate all parameters and check non-void return values.",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-20",
                summary="Runtime validation of incoming callable arguments.",
                quote=(
                    "The product receives input or data, but it does not validate or incorrectly "
                    "validates that the input has the properties that are required to process "
                    "the data safely and correctly."
                ),
                source_url="https://cwe.mitre.org/data/definitions/20.html",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-DBC-005",
        article=CodeArticle.DBC,
        title="Bounded Scalar & Collection Domain Primitives",
        normative_rule=(
            "Production BaseModel fields, TypeAliases, and public scalar parameters over str, "
            "bytes, int, float, list, set, frozenset, tuple[..., ...], Sequence, or Mapping "
            "SHALL declare explicit size/range/finiteness bounds via Field() or Annotated "
            "domain aliases."
        ),
        hazard=(
            "Primitive obsession allowing 500 MB strings, unbounded collections, negative "
            "counts, or NaN/Inf floats to trigger OOM or domain corruption."
        ),
        pass_criteria=(
            "Zero bare unbounded str/bytes/int/float/collection fields on BaseModels or "
            "public production parameters."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.CONSTRAINED_DOMAIN}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-770",
                summary="Bound size and cardinality of accepted data structures.",
                quote=(
                    "The product allocates a reusable resource or group of resources on behalf "
                    "of an actor without imposing any intended restrictions on the size or "
                    "number of resources that can be allocated."
                ),
                source_url="https://cwe.mitre.org/data/definitions/770.html",
            ),
            StandardReference(
                body=StandardBody.OWASP_ASVS,
                clause="V5.1.3",
                summary="Positive allowlist validation of lengths and ranges.",
                quote=(
                    "Verify that all input (HTML form fields, REST requests, URL parameters, "
                    "HTTP headers, cookies, batch files, RSS feeds, etc.) is validated using "
                    "positive validation (allowlisting)."
                ),
            ),
        ),
    ),
    # --- Article II: FLT — Fault Isolation, Termination & Resource Envelopes (6) ---
    BuildingCode(
        code_id="JBC-FLT-001",
        article=CodeArticle.FLT,
        title="Prohibition of Silent Fault Suppression",
        normative_rule=(
            "Exception handlers SHALL NOT use bare except clauses, empty pass/ellipsis bodies, "
            "or broad Exception/BaseException suppression that discards fault state without "
            "re-raising or structured fault recording."
        ),
        hazard=(
            "Corrupted program state continuing past a tripped fault ('penny behind the fuse'), "
            "destroying debuggability and masking root causes."
        ),
        pass_criteria=(
            "Zero bare except blocks, zero pass/ellipsis exception bodies, and zero silent "
            "broad Exception catches or contextlib.suppress(Exception) calls."
        ),
        severity=CodeSeverity.LIFE_SAFETY,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-390",
                summary="Error detected with zero handling action.",
                quote=(
                    "The product detects a specific error, but takes no actions to handle "
                    "the error."
                ),
                source_url="https://cwe.mitre.org/data/definitions/390.html",
            ),
            StandardReference(
                body=StandardBody.SEI_CERT,
                clause="ERR00-J",
                summary="Never ignore caught exceptions silently.",
                quote="Do not suppress or ignore checked exceptions",
            ),
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-SWALLOW-01",
                summary="Prevents agents from wrapping failing calls in try/except/pass.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-FLT-002",
        article=CodeArticle.FLT,
        title="Causal Exception Chaining & Actionable Error Diagnostics",
        normative_rule=(
            "Raised exceptions SHALL instantiate specific exception subclasses with non-empty "
            "diagnostic messages, and replacement exceptions raised inside except handlers "
            "SHALL preserve causal provenance via 'raise ... from ...'."
        ),
        hazard=(
            "Severed stack-trace causality and opaque, message-free crashes that prevent "
            "post-mortem fault tracing."
        ),
        pass_criteria=(
            "Zero unchained replacement raises in except blocks, zero generic Exception() "
            "raises, and zero empty-message exception instantiations."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.ISO_IEC_25010,
                clause="§4.2.5 & §4.2.7",
                summary="Fault tolerance and diagnostic analysability of failure transitions.",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-755",
                summary="Improper handling of exceptional conditions.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-FLT-003",
        article=CodeArticle.FLT,
        title="Managed Resource Handles, Sync Timeouts & Bounded Retry Discipline",
        normative_rule=(
            "System handles (open, socket) SHALL be acquired inside 'with' blocks; blocking "
            "sync subprocess/HTTP calls SHALL specify 'timeout='; and @retry decorators or "
            "try/except/continue loops SHALL declare finite attempt caps and narrow exceptions."
        ),
        hazard=(
            "File-descriptor leaks, stalled sync worker deadlocks, and infinite retry storms "
            "(such as bare @tenacity.retry on Exception)."
        ),
        pass_criteria=(
            "Zero unmanaged open/socket calls, zero timeout-free sync subprocess/HTTP calls, "
            "and zero unbounded or broad-Exception retries."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.EXTERNAL_BOUNDARY}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-400",
                summary="Bound resource lifecycles, I/O waits, and retry storms.",
                quote=(
                    "The product does not properly control the allocation and maintenance "
                    "of a limited resource."
                ),
                source_url="https://cwe.mitre.org/data/definitions/400.html",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-FLT-004",
        article=CodeArticle.FLT,
        title="Guaranteed Loop & Recursion Termination (LoopBudget & Fuel Bounds)",
        normative_rule=(
            "Every while loop SHALL invoke a deterministic LoopBudget.tick() step guard, and "
            "direct or mutual recursion (including AST NodeVisitors) SHALL enforce an explicit "
            "strictly decreasing fuel/depth bound or RecursionBudget; unbounded recursion is "
            "prohibited."
        ),
        hazard=(
            "Non-progressing infinite while loops (CWE-835) and RecursionError stack exhaustion "
            "on deeply nested adversarial inputs (CWE-674)."
        ),
        pass_criteria=(
            "100% of while loops tick a LoopBudget and 100% of recursive call cycles / visitors "
            "enforce an explicit depth/fuel ceiling."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.NASA_JPL_P10,
                clause="Rule 1 & Rule 2",
                summary="Bounded control flow and statically verifiable loop upper bounds.",
                quote="All loops must have a fixed upper-bound",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-835",
                summary="Prevent infinite loops with unreachable exit conditions.",
                quote=(
                    "The product contains an iteration or loop with an exit condition that "
                    "cannot be reached, i.e., an infinite loop."
                ),
                source_url="https://cwe.mitre.org/data/definitions/835.html",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-674",
                summary="Bound recursion depth with explicit fuel.",
                quote=(
                    "The product does not properly control the amount of recursion that takes "
                    "place, consuming excessive resources, such as allocated memory or the "
                    "program stack."
                ),
                source_url="https://cwe.mitre.org/data/definitions/674.html",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-FLT-005",
        article=CodeArticle.FLT,
        title="Structured Concurrency & Wall-Clock Cancel Deadlines",
        normative_rule=(
            "Modules SHALL NOT spawn fire-and-forget tasks or threads (asyncio.create_task, "
            "ensure_future, loop.create_task, Thread.start) outside structured TaskGroups, and "
            "async functions awaiting external I/O SHALL enforce an asyncio.timeout or "
            "fail_after wall-clock deadline."
        ),
        hazard=(
            "Orphaned background coroutines draining CPU/sockets and Slowloris drip-read "
            "hangs that bypass per-packet socket timeouts."
        ),
        pass_criteria=(
            "Zero unstructured task/thread spawns and 100% wall-clock deadline coverage on "
            "external async I/O coroutines."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.EXTERNAL_BOUNDARY}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-400",
                summary="Prevent Slowloris hangs and leaked background execution.",
                quote=(
                    "The product does not properly control the allocation and maintenance "
                    "of a limited resource."
                ),
                source_url="https://cwe.mitre.org/data/definitions/400.html",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-FLT-006",
        article=CodeArticle.FLT,
        title="Bounded Memory Caches & Stream Allocation Ceilings",
        normative_rule=(
            "Modules SHALL NOT use unbounded memoization (@functools.cache or "
            "@lru_cache(maxsize=None)), and production modules SHALL NOT invoke unbounded "
            "read(), readlines(), read_text(), read_bytes(), or fetchall() calls without "
            "explicit byte or row limits."
        ),
        hazard=(
            "Monotonic cache memory leaks in long-lived processes and container OOM crashes "
            "when reading oversized files, streams, or query result sets."
        ),
        pass_criteria=(
            "Zero unbounded cache decorators and zero uncapped stream/file/DB slurp calls in "
            "production code."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.EXTERNAL_BOUNDARY}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-770",
                summary="Impose explicit byte and count ceilings on memory allocations.",
                quote=(
                    "The product allocates a reusable resource or group of resources on behalf "
                    "of an actor without imposing any intended restrictions on the size or "
                    "number of resources that can be allocated."
                ),
                source_url="https://cwe.mitre.org/data/definitions/770.html",
            ),
        ),
    ),
    # --- Article III: SEC — Trust Boundaries, Ingress/Egress & Sink Containment (5) ---
    BuildingCode(
        code_id="JBC-SEC-001",
        article=CodeArticle.SEC,
        title="Parameterized Sink Execution & Dynamic Eval Prohibition",
        normative_rule=(
            "Source artifacts SHALL NOT invoke eval, exec, unsafe deserializers (pickle, "
            "marshal, unsafe yaml.load), shell=True subprocesses, or string-interpolated "
            "SQL execution sinks."
        ),
        hazard=(
            "Arbitrary code execution (RCE), OS command injection, and SQL injection across "
            "untrusted data boundaries."
        ),
        pass_criteria=(
            "Zero calls to banned dynamic execution/deserialization primitives, zero shell=True "
            "invocations, and zero interpolated/concatenated SQL execute queries."
        ),
        severity=CodeSeverity.LIFE_SAFETY,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.EXTERNAL_BOUNDARY}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-89",
                summary="Neutralize SQL injection via parameterized queries.",
                quote=(
                    "The product constructs all or part of an SQL command using "
                    "externally-influenced input from an upstream component, but it does not "
                    "neutralize or incorrectly neutralizes special elements that could modify "
                    "the intended SQL command when it is sent to a downstream component."
                ),
                source_url="https://cwe.mitre.org/data/definitions/89.html",
            ),
            StandardReference(
                body=StandardBody.OWASP_ASVS,
                clause="V5.3.4",
                summary="Parameterized database queries prevent injection.",
                quote=(
                    "Verify that data selection or database queries (e.g. SQL, HQL, ORM, NoSQL) "
                    "use parameterized queries, ORMs, entity frameworks, or are otherwise "
                    "protected from database injection attacks."
                ),
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-SEC-002",
        article=CodeArticle.SEC,
        title="Secret Redaction & Sensitive Payload Containment",
        normative_rule=(
            "Source code SHALL NOT embed credential literals, interpolate sensitive variables "
            "(passwords, tokens, keys, SSNs) into log/print/exception strings, or expose plain "
            "sensitive fields on Pydantic models without SecretStr or repr=False."
        ),
        hazard=(
            "Credential exfiltration through VCS history, crash tracebacks, structured logs, "
            "or serialized model representations."
        ),
        pass_criteria=(
            "Zero hardcoded secrets, zero sensitive variable interpolations in logs/exceptions, "
            "and 100% redaction on sensitive BaseModel fields."
        ),
        severity=CodeSeverity.LIFE_SAFETY,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.SENSITIVE_CONTEXT}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.OWASP_ASVS,
                clause="V7.1.1",
                summary="Credentials and tokens must never be logged in cleartext.",
                quote=(
                    "Verify that the application does not log credentials or payment details. "
                    "Session tokens should only be stored in logs in an irreversible, hashed form."
                ),
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-209 / CWE-532 / CWE-798",
                summary="Sensitive information exposure in error messages, logs, and literals.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-SEC-003",
        article=CodeArticle.SEC,
        title="Strict Closed Boundary Schemas (extra='forbid', strict=True, allow_inf_nan=False)",
        normative_rule=(
            "Every Pydantic BaseModel schema SHALL enforce extra='forbid', strict=True, and "
            "allow_inf_nan=False (via STRICT_FROZEN_CONFIG, STRICT_MUTABLE_CONFIG, or explicit "
            "ConfigDict) so undeclared keys, implicit type coercions, and NaN/Inf fail closed."
        ),
        hazard=(
            "Mass-assignment smuggling, silent type coercion ('false' -> True), and NaN/Inf "
            "poisoning of downstream domain calculations."
        ),
        pass_criteria=(
            "100% of BaseModel subclasses enforce extra='forbid', strict=True, and "
            "allow_inf_nan=False."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.CONSTRAINED_DOMAIN, CodeTrigger.EXTERNAL_BOUNDARY}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-754",
                summary="Reject exceptional NaN/Infinity and coerced boundary conditions.",
                quote=(
                    "The product does not check or incorrectly checks for unusual or "
                    "exceptional conditions that are not expected to occur frequently during "
                    "day to day operation of the product."
                ),
                source_url="https://cwe.mitre.org/data/definitions/754.html",
            ),
            StandardReference(
                body=StandardBody.OWASP_ASVS,
                clause="V5.1.3",
                summary="Strict positive schema validation.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-SEC-004",
        article=CodeArticle.SEC,
        title="Typed Wire Ingress & Explicit Egress Projection",
        normative_rule=(
            "Production modules SHALL NOT parse JSON via untyped json.loads/json.load (use "
            "model_validate_json or TypeAdapter.validate_json), and public callables SHALL NOT "
            "accept or return untyped dict[str, object] / Mapping[str, object] payload bags."
        ),
        hazard=(
            "Unvalidated dict soup entering domain logic and accidental over-exposure of "
            "sensitive internal attributes across egress boundaries (OWASP API3:2023)."
        ),
        pass_criteria=(
            "Zero json.load/loads calls in production code and zero untyped object-bag "
            "dict/Mapping parameters or return types on public callables."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.EXTERNAL_BOUNDARY}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.OWASP_API_TOP_10,
                clause="API3:2023",
                summary="Prevent Excessive Data Exposure and Mass Assignment via explicit schemas.",
                quote=(
                    "This category combines API3:2019 Excessive Data Exposure and API6:2019 - "
                    "Mass Assignment, focusing on the root cause: the lack of or improper "
                    "authorization validation at the object property level."
                ),
                source_url="https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-200",
                summary="Block unauthorized exposure of internal fields at egress.",
                quote=(
                    "The product exposes sensitive information to an actor that is not "
                    "explicitly authorized to have access to that information."
                ),
                source_url="https://cwe.mitre.org/data/definitions/200.html",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-SEC-005",
        article=CodeArticle.SEC,
        title="Taint Separation & Canonicalized Sink Types",
        normative_rule=(
            "Production filesystem and network sink operations SHALL require canonicalized "
            "containment types (such as ContainedPath verified via is_relative_to or validated "
            "URL types) and SHALL NOT pass raw str paths or raw str URLs directly to sinks."
        ),
        hazard=(
            "Directory traversal ('../../etc/passwd') escaping workspace sandboxes (CWE-22) "
            "and Server-Side Request Forgery (SSRF, CWE-918)."
        ),
        pass_criteria=(
            "Zero raw str filesystem sink calls (open/remove/unlink/rmtree) and zero raw str "
            "URL HTTP client calls in production modules."
        ),
        severity=CodeSeverity.LIFE_SAFETY,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.EXTERNAL_BOUNDARY}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-22",
                summary="Canonicalize and confine paths inside an explicit root directory.",
                quote=(
                    "The product uses external input to construct a pathname that is intended "
                    "to identify a file or directory that is located underneath a restricted "
                    "parent directory, but it does not properly neutralize special elements "
                    "that can resolve to a location outside of that directory."
                ),
                source_url="https://cwe.mitre.org/data/definitions/22.html",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-SEC-006",
        article=CodeArticle.SEC,
        title="Dynamic Path Anchoring & Hardcoded Path Prohibition",
        normative_rule=(
            "Modules SHALL NOT embed hardcoded host or OS filesystem path literals (such as "
            "'/Users/...', '/home/...', '/tmp/...', '~/...', or drive roots) and production "
            "modules SHALL NOT pass string literals directly to Path/os.path constructors; all "
            "filesystem paths SHALL be calculated dynamically from known file, package, or "
            "injected root anchors."
        ),
        hazard=(
            "Machine-coupled artifacts and CWD-fragile filesystem lookups that fail across "
            "clones, containers, CI runners, and working directories."
        ),
        pass_criteria=(
            "Zero hardcoded host/OS filesystem path literals across source and tests, and zero "
            "unanchored string-literal Path/os.path constructor calls in production modules."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.EXTERNAL_BOUNDARY}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.ISO_IEC_25010,
                clause="§4.2.8",
                summary="Portability, adaptability, and environment-independent installation.",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-426",
                summary="Untrusted or unanchored search path resolution hazards.",
                quote=(
                    "The application searches for critical resources using an untrusted search path"
                ),
                source_url="https://cwe.mitre.org/data/definitions/426.html",
            ),
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-PATH-01",
                summary="Prevents coding agents from baking machine-local or CWD-relative paths.",
            ),
        ),
    ),
)
