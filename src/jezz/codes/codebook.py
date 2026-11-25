"""Edition 1.3 of the Jezz Software Building Code (JBC) — 32 provisions across 6 Articles."""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from types import MappingProxyType
from typing import Final

import icontract

from jezz.behaviors.taxonomy import BehaviorKind
from jezz.codes.codebook_articles import ARTICLES_I_TO_III_CODES
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

CODEBOOK_EDITION: Final[str] = "1.3"

_ARTICLES_IV_TO_VI_CODES: tuple[BuildingCode, ...] = (
    # --- Article IV: COG — Complexity, Dispatch Tables & Class Duty Isolation (6) ---
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
    BuildingCode(
        code_id="JBC-COG-006",
        article=CodeArticle.COG,
        title="Subpackage Quotient Acyclicity, Floorplate Ceilings & Compartment Fire-Walls",
        normative_rule=(
            "Subpackage compartment quotient graphs (G/~) SHALL form a strict DAG; compartments "
            "and packages SHALL NOT exceed profile floorplate or external portal-ratio ceilings; "
            "and workspace members SHALL NOT traverse sibling relative paths or private internals."
        ),
        hazard=(
            "Cross-folder import cycles that evade file-level DAG checks, sprawling unpartitioned "
            "god-packages, sham folder splits with porous surfaces, and leaky monorepo boundaries."
        ),
        pass_criteria=(
            "Zero Tarjan SCCs in compartment quotient graph G/~, module counts and portal ratios "
            "within profile ceilings, and zero cross-member relative or internal imports."
        ),
        severity=CodeSeverity.STRUCTURAL,
        variance_permitted=True,
        triggers=frozenset({CodeTrigger.UNIVERSAL}),
        enforced_at=frozenset({BehaviorKind.VERIFY_STATIC}),
        inspection_method=InspectionMethod.MODULE_GRAPH,
        origin=CodeOrigin.EXTENDED_HYBRID,
        standard_refs=(
            StandardReference(
                body=StandardBody.PARNAS_MODULARITY,
                clause="Parnas 1972",
                summary="Information-hiding compartment walls and minimal exported portals.",
            ),
            StandardReference(
                body=StandardBody.ISO_IEC_25010,
                clause="§4.2.7",
                summary="Modularity and bounded inter-subsystem propagation cost.",
            ),
            StandardReference(
                body=StandardBody.JEZZ_AGENTIC,
                clause="AGT-COMPART-01",
                summary="Bounds cold-start localization search and closes cross-folder cycles.",
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
    for code in (*ARTICLES_I_TO_III_CODES, *_ARTICLES_IV_TO_VI_CODES):
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
