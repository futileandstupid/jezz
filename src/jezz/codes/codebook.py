"""Edition 1.1 of the Jezz Software Building Code (JBC) — 30 provisions across 6 Articles."""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from types import MappingProxyType
from typing import Final

import icontract

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
from jezz.guards import IdentifierStr, jezz_callable

CODEBOOK_EDITION: Final[str] = "1.1"

_RAW_CODES: tuple[BuildingCode, ...] = (
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
    # --- Article IV: COG — Complexity, Dispatch Tables & Class Duty Isolation (5) ---
    BuildingCode(
        code_id="JBC-COG-001",
        article=CodeArticle.COG,
        title="Callable Cognitive Complexity Ceiling (<= 15)",
        normative_rule=(
            "No function or method SHALL exceed a Sonar-style Cognitive Complexity score of 15 "
            "accounting for control-flow breaks, boolean operator chains, recursion, and "
            "nesting depth penalties."
        ),
        hazard=(
            "Opaque, deeply tangled control flow where neither human reviewers nor coding "
            "agents can reason soundly about all execution paths."
        ),
        pass_criteria="Every function and method has computed Cognitive Complexity <= 15.",
        severity=CodeSeverity.WORKMANSHIP,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.SONAR_COMPLEXITY,
                clause="Campbell 2017",
                summary="Penalize nested breaks in linear control flow.",
                quote=(
                    "Increment (add one) for each break in the linear flow of the code; "
                    "Increment when flow-breaking structures are nested."
                ),
            ),
            StandardReference(
                body=StandardBody.ISO_IEC_25010,
                clause="§4.2.7",
                summary="Maintainability, analysability, and modifiability.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-COG-002",
        article=CodeArticle.COG,
        title="Control-Flow Nesting, Parameter Arity, and Statement Span Limits",
        normative_rule=(
            "Callables SHALL NOT exceed control-flow nesting depth 4, non-receiver parameter "
            "count 6, or 35 executable AST statements."
        ),
        hazard=(
            "Arrow-code nesting pyramids, positional parameter confusion, and monolithic "
            "multi-responsibility procedures."
        ),
        pass_criteria=(
            "Every callable satisfies max_nesting_depth <= 4, parameter_count <= 6, and "
            "statement_count <= 35."
        ),
        severity=CodeSeverity.WORKMANSHIP,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.NASA_JPL_P10,
                clause="Rule 1 & Rule 4",
                summary="Simple control flow and single-sheet callable size.",
                quote="Restrict all code to very simple control flow constructs",
            ),
            StandardReference(
                body=StandardBody.IEEE_1016,
                clause="§5",
                summary="Bounded module interface arity and procedural cohesion.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-COG-003",
        article=CodeArticle.COG,
        title="Acyclic Module Topology, Wildcard Ban & Sealed Private Enclosures",
        normative_rule=(
            "Internal runtime module import graphs SHALL form a strict DAG; modules SHALL NOT "
            "use wildcard imports; and modules SHALL NOT import _private symbols across module "
            "boundaries."
        ),
        hazard=(
            "Import-time initialization cycles, namespace pollution, and callers bypassing "
            "public @jezz_callable breakers via cross-module _private imports."
        ),
        pass_criteria=(
            "Zero runtime import cycles, zero wildcard imports, and zero cross-module _private "
            "symbol imports."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.MODULE_GRAPH,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.PARNAS_MODULARITY,
                clause="Parnas 1972",
                summary="Modules hide internal secrets behind sealed public interfaces.",
                quote=(
                    "Every module in the second decomposition is characterized by its "
                    "knowledge of a design decision which it hides from all others."
                ),
            ),
            StandardReference(
                body=StandardBody.ISO_IEC_25010,
                clause="§4.2.7",
                summary="Modularity and bounded inter-component coupling.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-COG-004",
        article=CodeArticle.COG,
        title="Table-Driven Dispatch ('Circuit Board') & Guard-Clause Discipline",
        normative_rule=(
            "Conditional chains SHALL NOT exceed 1 elif branch (use MappingProxyType dispatch "
            "tables, @singledispatch, or exhaustive match); if blocks ending in return/raise/"
            "break/continue SHALL NOT be followed by else/elif; and module-level callable "
            "dispatch tables SHALL be immutable via MappingProxyType."
        ),
        hazard=(
            "Unreadable if/elif ladders that evade Cognitive Complexity, redundant post-return "
            "else indentation, and mutable dispatch dicts vulnerable to runtime tampering."
        ),
        pass_criteria=(
            "Zero if statements with >= 2 elif branches, zero else/elif blocks after terminal "
            "jumps, and zero mutable module-level callable dispatch dicts."
        ),
        severity=CodeSeverity.WORKMANSHIP,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.MCCONNELL_DISPATCH,
                clause="Code Complete Ch. 18",
                summary="Replace multi-branch conditionals with declarative lookup tables.",
            ),
            StandardReference(
                body=StandardBody.SONAR_COMPLEXITY,
                clause="Campbell 2017",
                summary="Closes Sonar's flat-elif blind spot via hard dispatch limits.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-COG-005",
        article=CodeArticle.COG,
        title="Single-Duty Class Cohesion (LCOM4 == 1) & Panel Capacity Limits",
        normative_rule=(
            "Classes SHALL serve strictly one duty: BaseModel/value schemas SHALL NOT perform "
            "external I/O, and behavioral classes SHALL satisfy LCOM4 <= 1, zero stateless "
            "instance methods, <= 5 instance attributes, <= 7 public methods, and WMC <= 35."
        ),
        hazard=(
            "Scope-creeping God classes, shoehorned stateless utility methods, Active-Record "
            "schema/IO hybrids, and multi-domain orchestrators."
        ),
        pass_criteria=(
            "100% of classes satisfy role purity, LCOM4 <= 1, zero stateless methods, "
            "<= 5 behavioral fields, <= 7 public methods, and WMC <= 35."
        ),
        severity=CodeSeverity.WORKMANSHIP,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.HITZ_MONTAZERI_LCOM4,
                clause="Hitz & Montazeri 1995",
                summary="LCOM4 > 1 proves disconnected method/state islands inside one class.",
            ),
            StandardReference(
                body=StandardBody.CHIDAMBER_KEMERER,
                clause="WMC & CBO (1994)",
                summary="Bound Weighted Methods per Class and collaborator fan-out.",
            ),
            StandardReference(
                body=StandardBody.PARNAS_MODULARITY,
                clause="Parnas 1972",
                summary="One design secret per class/module.",
            ),
        ),
    ),
    # --- Article V: OBS — Observability, State Hygiene & Determinism (3) ---
    BuildingCode(
        code_id="JBC-OBS-001",
        article=CodeArticle.OBS,
        title="Structured Diagnostic Telemetry & Debug Trap Prohibition",
        normative_rule=(
            "Production modules SHALL NOT invoke unstructured print() output or interactive "
            "debugger traps (breakpoint, pdb.set_trace)."
        ),
        hazard=(
            "Production process suspension on stdin debugger traps and uncorrelatable stdout "
            "noise lacking severity, timestamps, or trace context."
        ),
        pass_criteria="Zero print(), breakpoint(), or pdb/ipdb set_trace() calls in non-test code.",
        severity=CodeSeverity.WORKMANSHIP,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-489",
                summary="Active debug code left in deployable software artifacts.",
            ),
            StandardReference(
                body=StandardBody.ISO_IEC_25010,
                clause="§4.2.7",
                summary="Structured diagnostic analysability.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-OBS-002",
        article=CodeArticle.OBS,
        title="Immutable Default State & Global State Prohibition",
        normative_rule=(
            "Callable signatures SHALL NOT bind mutable default objects (list, dict, set, "
            "deque, bytearray), and functions SHALL NOT mutate module globals via the "
            "'global' statement."
        ),
        hazard=(
            "Cross-call state contamination, non-reentrant race conditions, and hidden "
            "action-at-a-distance side effects."
        ),
        pass_criteria=(
            "Zero mutable literal/constructor default arguments and zero 'global' statements."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.ESTABLISHED_STANDARD,
        standard_refs=(
            StandardReference(
                body=StandardBody.NASA_JPL_P10,
                clause="Rule 6",
                summary="Declare data objects at the smallest possible scope.",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-665",
                summary="Improper initialization and shared mutable state across invocations.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-OBS-003",
        article=CodeArticle.OBS,
        title="Deterministic Execution & Controlled Nondeterminism",
        normative_rule=(
            "Modules SHALL NOT invoke unseeded module-global random.* functions or mutate "
            "process-global os.environ state inside production logic."
        ),
        hazard=(
            "Irreproducible Heisenbugs, flaky verification runs, and process-wide environment "
            "corruption across concurrent tasks."
        ),
        pass_criteria=(
            "Zero direct calls to global random.* generators and zero os.environ mutations "
            "in non-test source files."
        ),
        severity=CodeSeverity.WORKMANSHIP,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.ISO_IEC_25010,
                clause="§4.2.7",
                summary="Deterministic testability and replayable execution.",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-330",
                summary="Uncontrolled nondeterministic PRNG usage.",
            ),
        ),
    ),
    # --- Article VI: VRF — Anti-Theatre Verification, Grounding & Watchdogs (6) ---
    BuildingCode(
        code_id="JBC-VRF-001",
        article=CodeArticle.VRF,
        title="Prohibition of Verification Theatre & Tautological Assertions",
        normative_rule=(
            "Every test_* function SHALL evaluate at least one non-vacuous verification check "
            "and SHALL NOT assert constant literals, tuple-trap expressions, or reflexive "
            "identities (e.g., assert True, assert x == x)."
        ),
        hazard=(
            "QA theatre and LLM reward-hacking where suites report 100% green while testing "
            "zero actual postconditions."
        ),
        pass_criteria=(
            "Every test_* callable contains >= 1 real assertion/raises check and zero "
            "constant, tuple-trap, or reflexive assertions."
        ),
        severity=CodeSeverity.LIFE_SAFETY,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.VERIFICATION_SUITE}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.AST_STATIC,
        origin=CodeOrigin.NET_NEW_AGENTIC,
        standard_refs=(
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-THEATRE-01",
                summary="Eliminates assertion-free and tautological LLM test generation.",
            ),
            StandardReference(
                body=StandardBody.IEEE_29148,
                clause="§5.2.8",
                summary="Requirements and tests must be objectively falsifiable.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-VRF-002",
        article=CodeArticle.VRF,
        title="Prohibition of Hollow Stubs & Deferred Placeholders",
        normative_rule=(
            "Non-abstract production callables SHALL NOT consist solely of pass, ellipsis, or "
            "NotImplementedError stubs, and source comments SHALL NOT retain unresolved "
            "TODO, FIXME, XXX, HACK, or PLACEHOLDER markers."
        ),
        hazard=(
            "Premature completion claims where coding agents leave unimplemented stub bodies "
            "behind seemingly typed interfaces."
        ),
        pass_criteria=(
            "Zero non-abstract stub callables and zero deferred placeholder comment tokens."
        ),
        severity=CodeSeverity.LIFE_SAFETY,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.TOKEN_LEXICAL,
        origin=CodeOrigin.NET_NEW_AGENTIC,
        standard_refs=(
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-STUB-01",
                summary="Detects LLM elision, lazy stubs, and deferred implementation markers.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-VRF-003",
        article=CodeArticle.VRF,
        title="Epistemic Import & Declared Dependency Grounding",
        normative_rule=(
            "Every imported root package SHALL resolve to the Python standard library, a "
            "first-party workspace package, or a dependency explicitly declared for that "
            "artifact's scope in pyproject.toml."
        ),
        hazard=(
            "Hallucinated third-party packages (slopsquatting attack surface), dev-only test "
            "packages leaked into production modules, and undeclared transitive coupling."
        ),
        pass_criteria=(
            "100% of import statements resolve to stdlib, workspace roots, or declared "
            "manifest dependencies matching production vs. test scope."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.MODULE_GRAPH,
        origin=CodeOrigin.NET_NEW_AGENTIC,
        standard_refs=(
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-HALLUC-01",
                summary="Prevents hallucinated imports and undeclared shadow dependencies.",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-1357",
                summary="Reliance on undeclared or insufficiently governed components.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-VRF-004",
        article=CodeArticle.VRF,
        title="Prohibition of Unpermitted Gate Suppression Directives",
        normative_rule=(
            "Source and test files SHALL NOT embed inline gate-silencing directives "
            "(# noqa, # type: ignore, # pyright: ignore, # pragma: no cover) or unconditional "
            "test skips without an active authority-approved CodeVariance."
        ),
        hazard=(
            "Agents silencing typecheckers, linters, coverage, or failing tests instead of "
            "remediating the underlying defect."
        ),
        pass_criteria=(
            "Zero unwaived suppression comments or test-skip decorators across all artifacts."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL, CodeTrigger.VERIFICATION_SUITE}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.TOKEN_LEXICAL,
        origin=CodeOrigin.NET_NEW_AGENTIC,
        standard_refs=(
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-TAMPER-01",
                summary="Prevents autonomous suppression of static and dynamic safety gates.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-VRF-005",
        article=CodeArticle.VRF,
        title="Mandatory Static Soundness Gate Attestation",
        normative_rule=(
            "Final occupancy certification SHALL include a passing static type-verification "
            "attestation (exit_code == 0) over the current workspace state."
        ),
        hazard=(
            "Issuing certification on code that passes local AST heuristics while failing "
            "cross-module type soundness."
        ),
        pass_criteria="At least one passing 'static_types' GateAttestation with exit_code == 0.",
        severity=CodeSeverity.LIFE_SAFETY,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.VERIFICATION_SUITE}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC, BehaviorKind.CERTIFY}),
        inspection_method=InspectionMethod.GATE_ATTESTATION,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.ISO_IEC_25010,
                clause="§4.2.5",
                summary="Static soundness verification prior to release.",
            ),
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-ATTEST-01",
                summary="Requires empirical typechecker execution proof prior to CERTIFY.",
            ),
        ),
    ),
    BuildingCode(
        code_id="JBC-VRF-006",
        article=CodeArticle.VRF,
        title="Mandatory Dynamic Suite, Watchdog Timeout & Memory Attestation",
        normative_rule=(
            "Final occupancy certification SHALL include a passing dynamic suite attestation "
            "(exit_code == 0, checks_executed >= 1) within configured wall-clock duration and "
            "peak memory watchdog ceilings."
        ),
        hazard=(
            "Certifying unexecuted or zero-test suites, or suites exhibiting runaway latency "
            "or memory blowups under dynamic execution."
        ),
        pass_criteria=(
            "At least one passing 'dynamic_suite' GateAttestation with checks_executed >= 1, "
            "0 < duration_ms <= max_duration_ms, and 0 < peak_memory_kb <= max_memory_kb."
        ),
        severity=CodeSeverity.LIFE_SAFETY,
        variance_permitted=False,
        triggers=frozenset({CodeTrigger.VERIFICATION_SUITE}),
        enforced_at=frozenset({BehaviorKind.VERIFY_DYNAMIC, BehaviorKind.CERTIFY}),
        inspection_method=InspectionMethod.GATE_ATTESTATION,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.MEYER_DBC,
                clause="OOSC2 §11",
                summary="Runtime contract monitoring verifies supplier postconditions.",
            ),
            StandardReference(
                body=StandardBody.MITRE_CWE,
                clause="CWE-400",
                summary="Empirical runtime and memory watchdog enforcement.",
            ),
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-ATTEST-02",
                summary="Blocks zero-test collection and unmeasured suite certification.",
            ),
        ),
    ),
)


def _check_article_contiguity(codes_by_id: Mapping[str, BuildingCode]) -> None:
    """Verify each CodeArticle is populated and numbered contiguously from 001."""
    for article in CodeArticle:
        matching = sorted(code.code_id for code in codes_by_id.values() if code.article == article)
        if not matching:
            msg = f"CodeArticle {article} has zero BuildingCodes registered."
            raise ValueError(msg)
        expected = [f"JBC-{article.value}-{idx:03d}" for idx in range(1, len(matching) + 1)]
        if matching != expected:
            msg = f"Article {article} codes must be contiguous {expected}, got {matching}."
            raise ValueError(msg)


@icontract.ensure(lambda book, result: result is None and len(book) >= len(CodeArticle))
@jezz_callable
def validate_codebook_invariants(book: Mapping[IdentifierStr, BuildingCode]) -> None:
    """Verify structural, lineage, and trigger-coverage invariants of a Code Book."""
    for key, code in book.items():
        if key != code.code_id:
            msg = f"CodeBook key '{key}' mismatches BuildingCode.code_id '{code.code_id}'."
            raise ValueError(msg)

    _check_article_contiguity(book)

    origins = {code.origin for code in book.values()}
    if CodeOrigin.NET_NEW_AGENTIC not in origins or CodeOrigin.ESTABLISHED_STANDARD not in origins:
        msg = "CodeBook must integrate both ESTABLISHED_STANDARD and NET_NEW_AGENTIC provisions."
        raise ValueError(msg)

    universal_codes = [c for c in book.values() if CodeTrigger.UNIVERSAL in c.triggers]
    if not universal_codes:
        msg = "CodeBook must define a non-empty UNIVERSAL safety baseline."
        raise ValueError(msg)


def _build_canonical_codebook() -> Mapping[str, BuildingCode]:
    mapping: dict[str, BuildingCode] = {}
    for code in _RAW_CODES:
        if code.code_id in mapping:
            msg = f"Duplicate BuildingCode '{code.code_id}'."
            raise ValueError(msg)
        mapping[code.code_id] = code
    validate_codebook_invariants(mapping)
    return MappingProxyType(mapping)


CODE_BOOK: Final[Mapping[str, BuildingCode]] = _build_canonical_codebook()


@icontract.require(lambda code_id: code_id in CODE_BOOK, "Unknown BuildingCode identifier.")
@icontract.ensure(lambda code_id, result: result.code_id == code_id)
@jezz_callable
def get_building_code(code_id: IdentifierStr) -> BuildingCode:
    """Return the canonical ``BuildingCode`` for ``code_id``."""
    return CODE_BOOK[code_id]


@icontract.ensure(lambda article, result: all(c.article == article for c in result))
@jezz_callable
def codes_by_article(article: CodeArticle) -> tuple[BuildingCode, ...]:
    """Return all ``BuildingCode`` provisions in ``article`` in canonical section order."""
    return tuple(CODE_BOOK[cid] for cid in sorted(CODE_BOOK) if CODE_BOOK[cid].article == article)


@icontract.ensure(
    lambda result: all(
        c.code_id in {r.code_id for r in result}
        for c in CODE_BOOK.values()
        if CodeTrigger.UNIVERSAL in c.triggers
    ),
    "Every trigger resolution must include the entire UNIVERSAL baseline.",
)
@jezz_callable
def codes_for_triggers(triggers: AbstractSet[CodeTrigger]) -> tuple[BuildingCode, ...]:
    """Return all ``BuildingCode`` provisions mandated by ``triggers`` plus ``UNIVERSAL``."""
    effective_triggers = set(triggers) | {CodeTrigger.UNIVERSAL}
    return tuple(
        CODE_BOOK[cid] for cid in sorted(CODE_BOOK) if CODE_BOOK[cid].triggers & effective_triggers
    )
