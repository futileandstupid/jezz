"""Universal JBC v1.2 rule evaluators over normalized PolyglotUnits (TS, Rust, Go)."""

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import Field

from jezz.codes.ast_utils import RawDiscrepancy, SourceSpan
from jezz.codes.cohesion import (
    MAX_BEHAVIORAL_FIELDS_LIMIT,
    MAX_CLASS_WMC_LIMIT,
    MAX_LCOM4_COMPONENTS,
    MAX_PUBLIC_METHODS_LIMIT,
)
from jezz.codes.complexity import ResolvedComplexityLimits
from jezz.codes.dep_graph import find_tarjan_cycles, resolve_polyglot_import_target
from jezz.codes.polyglot_ir import (
    EcosystemManifest,
    GuardedPolyglotUnit,
    PolyglotCallable,
    PolyglotImport,
    PolyglotManifest,
    PolyglotTypeDecl,
    PolyglotUnit,
    SupportedLanguage,
)
from jezz.codes.polyglot_manifests import (
    RUST_BUILTIN_CRATES,
    TS_BUILTIN_MODULES,
    is_go_import_grounded,
)
from jezz.guards import WIRE_FROZENSET_VALIDATOR, IdentifierStr, LoopBudget, jezz_callable

type _UnitRuleFn = Callable[[PolyglotUnit], list[RawDiscrepancy]]

_DEFAULT_POLY_LIMITS: Final[ResolvedComplexityLimits] = ResolvedComplexityLimits()


def _callable_span(unit: PolyglotUnit, fn: PolyglotCallable) -> SourceSpan:
    return SourceSpan(
        file_path=unit.file_path,
        line=fn.start_line,
        column=fn.column,
        symbol=fn.qualname,
    )


def _type_span(unit: PolyglotUnit, decl: PolyglotTypeDecl) -> SourceSpan:
    return SourceSpan(
        file_path=unit.file_path,
        line=decl.line,
        column=decl.column,
        symbol=decl.name,
    )


def _check_poly_dbc_001(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    for fn in unit.callables:
        span = _callable_span(unit, fn)
        for msg in fn.vacuous_contracts:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-001",
                    location=span,
                    message=f"{msg} ('{fn.qualname}').",
                    remediation="Write a falsifiable precondition/validator over runtime inputs.",
                )
            )
        for div_param in fn.unguarded_divisors:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-001",
                    location=span,
                    message=(
                        f"Divisor parameter '{div_param}' in '{fn.qualname}' lacks a "
                        "zero-guard precondition."
                    ),
                    remediation=f"Enforce an explicit '{div_param} != 0' check before division.",
                )
            )
    return out


def _check_callable_dbc_002(unit: PolyglotUnit, fn: PolyglotCallable) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    for p in fn.params:
        p_span = SourceSpan(
            file_path=unit.file_path, line=p.line, column=p.column, symbol=fn.qualname
        )
        if not p.has_type:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-002",
                    location=p_span,
                    message=f"Parameter '{p.name}' in '{fn.qualname}' lacks a type annotation.",
                    remediation=f"Annotate '{p.name}' with a concrete static type.",
                )
            )
        if p.has_any_erasure:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-002",
                    location=p_span,
                    message=f"Parameter '{p.name}' in '{fn.qualname}' uses type-erasing Any/any.",
                    remediation="Replace Any/any/interface{} with a concrete type or bound.",
                )
            )
    fn_span = _callable_span(unit, fn)
    if not fn.has_return_type:
        out.append(
            RawDiscrepancy(
                code_id="JBC-DBC-002",
                location=fn_span,
                message=f"Callable '{fn.qualname}' omits an explicit return type annotation.",
                remediation="Declare an explicit return type (or void/() for procedures).",
            )
        )
    if fn.return_has_any_erasure:
        out.append(
            RawDiscrepancy(
                code_id="JBC-DBC-002",
                location=fn_span,
                message=(
                    f"Callable '{fn.qualname}' erases its return type via Any/any/interface{{}}."
                ),
                remediation="Return a concrete schema, Result, or bounded generic type.",
            )
        )
    return out


def _check_poly_dbc_002(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    for fn in unit.callables:
        out.extend(_check_callable_dbc_002(unit, fn))
    for decl in unit.types:
        if decl.has_any_erasure_field:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-002",
                    location=_type_span(unit, decl),
                    message=f"Type '{decl.name}' contains an Any/any/interface{{}} erased field.",
                    remediation="Replace dynamic type erasure with explicit domain types.",
                )
            )
    return out


def _check_poly_dbc_003(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    return [
        RawDiscrepancy(
            code_id="JBC-DBC-003",
            location=_callable_span(unit, fn),
            message=(
                f"Callable '{fn.qualname}' has mixed bare/value returns or an implicit "
                "fallthrough return path."
            ),
            remediation="Terminate every control-flow path with an explicit typed return or error.",
        )
        for fn in unit.callables
        if not fn.is_abstract_or_stub and fn.has_mixed_or_missing_return
    ]


def _check_poly_dbc_004(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    if unit.is_test_file:
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-DBC-004",
            location=_callable_span(unit, fn),
            message=(
                f"Exported callable '{fn.qualname}' accepts unvalidated boundary inputs "
                "without precondition or schema guards."
            ),
            remediation=(
                "Validate boundary arguments via schema.parse(), domain newtypes, or explicit "
                "entry guard clauses."
            ),
        )
        for fn in unit.callables
        if fn.is_public and not fn.is_abstract_or_stub and not fn.has_boundary_contract
    ]


def _check_poly_dbc_005(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    if unit.is_test_file:
        return []
    out: list[RawDiscrepancy] = []
    for decl in unit.types:
        for fld in decl.unbounded_fields:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-DBC-005",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=decl.line,
                        column=decl.column,
                        symbol=f"{decl.name}.{fld}"[:256],
                    ),
                    message=f"Schema/wire field '{decl.name}.{fld}' is an unbounded primitive.",
                    remediation="Enforce explicit length, range, or finiteness domain bounds.",
                )
            )
    for fn in unit.callables:
        if not fn.is_public or fn.is_abstract_or_stub:
            continue
        for p in fn.params:
            if p.is_unbounded_primitive:
                out.append(
                    RawDiscrepancy(
                        code_id="JBC-DBC-005",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=p.line,
                            column=p.column,
                            symbol=fn.qualname,
                        ),
                        message=(
                            f"Public parameter '{p.name}' in '{fn.qualname}' accepts an "
                            "unbounded scalar/collection primitive."
                        ),
                        remediation="Accept a bounded newtype/schema or check finiteness/bounds.",
                    )
                )
    return out


def _check_poly_flt_004(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    return [
        RawDiscrepancy(
            code_id="JBC-FLT-004",
            location=_callable_span(unit, fn),
            message=f"Recursive callable '{fn.qualname}' lacks a fuel/max_depth ceiling.",
            remediation="Pass a decreasing remaining_depth/fuel parameter or explicit budget.",
        )
        for fn in unit.callables
        if fn.is_recursive_unguarded
    ]


def _check_poly_sec_002(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    return [
        RawDiscrepancy(
            code_id="JBC-SEC-002",
            location=SourceSpan(
                file_path=unit.file_path,
                line=decl.line,
                column=decl.column,
                symbol=f"{decl.name}.{fld}"[:256],
            ),
            message=f"Sensitive field '{decl.name}.{fld}' lacks redaction/debug suppression.",
            remediation="Wrap secret in a redacted type or mark skip/redacted on serialization.",
        )
        for decl in unit.types
        for fld in decl.sensitive_unredacted_fields
    ]


def _check_poly_sec_003(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    return [
        RawDiscrepancy(
            code_id="JBC-SEC-003",
            location=_type_span(unit, decl),
            message=f"Wire schema '{decl.name}' permits undeclared extra fields or NaN/Inf.",
            remediation=(
                "Close schema strictly (.strict(), #[serde(deny_unknown_fields)], or "
                "DisallowUnknownFields)."
            ),
        )
        for decl in unit.types
        if decl.is_schema_or_value and not decl.is_strict_schema
    ]


def _check_poly_sec_004(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    if unit.is_test_file:
        return []
    out: list[RawDiscrepancy] = []
    for fn in unit.callables:
        if not fn.is_public:
            continue
        for p in fn.params:
            if p.is_untyped_bag:
                out.append(
                    RawDiscrepancy(
                        code_id="JBC-SEC-004",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=p.line,
                            column=p.column,
                            symbol=fn.qualname,
                        ),
                        message=(
                            f"Public parameter '{p.name}' in '{fn.qualname}' accepts an "
                            "untyped Record/Map/Value object bag."
                        ),
                        remediation="Accept a closed typed ingress struct/schema.",
                    )
                )
        if fn.return_is_untyped_bag:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-004",
                    location=_callable_span(unit, fn),
                    message=(
                        f"Public callable '{fn.qualname}' returns an untyped Record/Map/Value "
                        "egress bag."
                    ),
                    remediation="Project egress data through an explicit typed struct/schema.",
                )
            )
    return out


def _check_poly_cog_001_with_limits(
    unit: PolyglotUnit,
    limits: ResolvedComplexityLimits,
) -> list[RawDiscrepancy]:
    cc_limit = limits.max_cognitive_complexity
    return [
        RawDiscrepancy(
            code_id="JBC-COG-001",
            location=_callable_span(unit, fn),
            message=(
                f"Callable '{fn.qualname}' has Cognitive Complexity "
                f"{fn.cognitive_complexity} (limit {cc_limit})."
            ),
            remediation="Decompose nested branches/loops into focused helper functions.",
        )
        for fn in unit.callables
        if fn.cognitive_complexity > cc_limit
    ]


def _check_poly_cog_001(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    return _check_poly_cog_001_with_limits(unit, _DEFAULT_POLY_LIMITS)


def _check_poly_cog_002_with_limits(
    unit: PolyglotUnit,
    limits: ResolvedComplexityLimits,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    for fn in unit.callables:
        span = _callable_span(unit, fn)
        if fn.max_nesting_depth > limits.max_nesting_depth:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-COG-002",
                    location=span,
                    message=(
                        f"Callable '{fn.qualname}' has control-flow nesting depth "
                        f"{fn.max_nesting_depth} (limit {limits.max_nesting_depth})."
                    ),
                    remediation="Use early guard clauses or extract nested control blocks.",
                )
            )
        if len(fn.params) > limits.max_parameter_count:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-COG-002",
                    location=span,
                    message=(
                        f"Callable '{fn.qualname}' takes {len(fn.params)} parameters "
                        f"(limit {limits.max_parameter_count})."
                    ),
                    remediation=(
                        "Group cohesive parameters into an immutable options/config struct."
                    ),
                )
            )
        if fn.statement_count > limits.max_statement_count:
            out.append(
                RawDiscrepancy(
                    code_id="JBC-COG-002",
                    location=span,
                    message=(
                        f"Callable '{fn.qualname}' spans {fn.statement_count} statements "
                        f"(limit {limits.max_statement_count})."
                    ),
                    remediation="Split multi-step procedure into single-purpose helpers.",
                )
            )
    return out


def _check_poly_cog_002(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    return _check_poly_cog_002_with_limits(unit, _DEFAULT_POLY_LIMITS)


def _compute_polyglot_lcom4(
    methods: Sequence[PolyglotCallable],
    known_fields: frozenset[str],
) -> int:
    stateful = [m for m in methods if (m.accessed_fields & known_fields)]
    if len(stateful) <= 1:
        return len(stateful)
    method_by_name = {m.name: m for m in stateful}
    adj: dict[str, set[str]] = {m.name: set() for m in stateful}
    for idx, left in enumerate(stateful):
        left_fields = left.accessed_fields & known_fields
        for right in stateful[idx + 1 :]:
            if left_fields & (right.accessed_fields & known_fields):
                adj[left.name].add(right.name)
                adj[right.name].add(left.name)
        for callee in left.calls:
            tail = callee.rsplit(".", maxsplit=1)[-1].rsplit("::", maxsplit=1)[-1]
            if tail in method_by_name and tail != left.name:
                adj[left.name].add(tail)
                adj[tail].add(left.name)
    return _count_connected_components(adj)


def _count_connected_components(adj: Mapping[str, set[str]]) -> int:
    visited: set[str] = set()
    components = 0
    budget = LoopBudget(max_steps=max(32, len(adj) * 8))
    for seed in sorted(adj):
        if seed in visited:
            continue
        components += 1
        visited.add(seed)
        queue: deque[str] = deque([seed])
        while queue:
            budget.tick()
            curr = queue.popleft()
            for nxt in adj[curr]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
    return components


def _evaluate_poly_type_cohesion(
    unit: PolyglotUnit,
    decl: PolyglotTypeDecl,
    methods: Sequence[PolyglotCallable],
) -> list[RawDiscrepancy]:
    span = _type_span(unit, decl)
    out: list[RawDiscrepancy] = []
    wmc = sum(m.cognitive_complexity + 1 for m in methods)
    if decl.is_schema_or_value and any(m.performs_io for m in methods):
        out.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=f"Value/schema type '{decl.name}' performs external I/O.",
                remediation="Keep data schemas pure and move I/O to a service/function.",
            )
        )
    if wmc > MAX_CLASS_WMC_LIMIT:
        out.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=f"Type '{decl.name}' has WMC {wmc} (limit {MAX_CLASS_WMC_LIMIT}).",
                remediation="Decompose excess method complexity into focused collaborator units.",
            )
        )
    if decl.is_schema_or_value or decl.kind in {"interface", "enum", "type_alias"}:
        return out
    out.extend(_evaluate_behavioral_type_limits(span, decl, methods))
    return out


def _evaluate_behavioral_type_limits(
    span: SourceSpan,
    decl: PolyglotTypeDecl,
    methods: Sequence[PolyglotCallable],
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    known_fields = frozenset(decl.fields)
    non_ctor = [m for m in methods if m.name not in {"constructor", "new"} and m.is_method]
    lcom4 = _compute_polyglot_lcom4(non_ctor, known_fields)
    if lcom4 > MAX_LCOM4_COMPONENTS:
        out.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=(
                    f"Type '{decl.name}' has LCOM4 = {lcom4} disconnected state islands "
                    f"(limit {MAX_LCOM4_COMPONENTS})."
                ),
                remediation="Split disconnected method/field duties into separate types.",
            )
        )
    stateless = [m.name for m in non_ctor if not m.is_abstract_or_stub and not m.is_receiver_used]
    if stateless:
        shown = ", ".join(stateless[:6])
        out.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=f"Type '{decl.name}' contains stateless instance method(s): {shown}.",
                remediation="Extract methods that never access receiver state to functions.",
            )
        )
    if len(decl.fields) > MAX_BEHAVIORAL_FIELDS_LIMIT and methods:
        out.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=(
                    f"Type '{decl.name}' holds {len(decl.fields)} fields "
                    f"(limit {MAX_BEHAVIORAL_FIELDS_LIMIT})."
                ),
                remediation="Reduce collaborator fan-out by decomposing sub-structures.",
            )
        )
    pub_methods = sum(1 for m in methods if m.is_public)
    if pub_methods > MAX_PUBLIC_METHODS_LIMIT:
        out.append(
            RawDiscrepancy(
                code_id="JBC-COG-005",
                location=span,
                message=(
                    f"Type '{decl.name}' exposes {pub_methods} public methods "
                    f"(limit {MAX_PUBLIC_METHODS_LIMIT})."
                ),
                remediation="Split broad interface into single-duty capabilities.",
            )
        )
    return out


def _check_poly_cog_005(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    methods_by_type: dict[str, list[PolyglotCallable]] = {}
    for fn in unit.callables:
        if fn.enclosing_type:
            methods_by_type.setdefault(fn.enclosing_type, []).append(fn)
    out: list[RawDiscrepancy] = []
    for decl in unit.types:
        if unit.is_test_file and decl.name.startswith("Test"):
            continue
        out.extend(_evaluate_poly_type_cohesion(unit, decl, methods_by_type.get(decl.name, ())))
    return out


def _check_poly_obs_002(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    return [
        RawDiscrepancy(
            code_id="JBC-OBS-002",
            location=SourceSpan(
                file_path=unit.file_path,
                line=p.line,
                column=p.column,
                symbol=fn.qualname,
            ),
            message=f"Callable '{fn.qualname}' uses a mutable default parameter '{p.name}'.",
            remediation=(
                "Default to undefined/None or construct a fresh collection inside the body."
            ),
        )
        for fn in unit.callables
        for p in fn.params
        if p.has_mutable_default
    ]


def _check_poly_vrf_001(unit: PolyglotUnit) -> list[RawDiscrepancy]:
    if not unit.is_test_file:
        return []
    return [
        RawDiscrepancy(
            code_id="JBC-VRF-001",
            location=_callable_span(unit, fn),
            message=f"Test '{fn.qualname}' contains zero assertions or failure checks.",
            remediation="Assert concrete behavioral postconditions or expected error outcomes.",
        )
        for fn in unit.callables
        if fn.is_test_callable and not fn.has_test_assertion
    ]


def _check_ts_or_rust_import_vrf_003(
    unit: PolyglotUnit,
    imp: PolyglotImport,
    eco: EcosystemManifest,
    builtins: frozenset[str],
    local_roots: AbstractSet[str],
) -> RawDiscrepancy | None:
    if imp.is_internal or not imp.root_pkg:
        return None
    pkg = imp.root_pkg
    if pkg.startswith(("node:", "bun:")) or pkg in builtins:
        return None
    base_allowed = set(eco.runtime_roots) | set(eco.workspace_roots) | set(local_roots)
    allowed = base_allowed | set(eco.dev_roots) if unit.is_test_file else base_allowed
    if pkg in allowed:
        return None
    span = SourceSpan(file_path=unit.file_path, line=imp.line, column=imp.column)
    if not unit.is_test_file and pkg in eco.dev_roots:
        return RawDiscrepancy(
            code_id="JBC-VRF-003",
            location=span,
            message=f"Production module imports dev-only dependency '{pkg}'.",
            remediation=f"Promote '{pkg}' to runtime dependencies or remove the import.",
        )
    return RawDiscrepancy(
        code_id="JBC-VRF-003",
        location=span,
        message=f"Ungrounded or undeclared import '{pkg}' (absent from stdlib and manifest).",
        remediation="Declare the dependency in package.json / Cargo.toml or remove phantom API.",
    )


def _check_poly_vrf_003(
    unit: PolyglotUnit,
    manifest: PolyglotManifest,
    local_roots: AbstractSet[str],
) -> list[RawDiscrepancy]:
    eco = manifest.for_language(unit.language)
    out: list[RawDiscrepancy] = []
    if unit.language == SupportedLanguage.GO:
        for imp in unit.imports:
            if not is_go_import_grounded(imp.raw_target, eco, local_roots):
                out.append(
                    RawDiscrepancy(
                        code_id="JBC-VRF-003",
                        location=SourceSpan(
                            file_path=unit.file_path, line=imp.line, column=imp.column
                        ),
                        message=(
                            f"Ungrounded Go import '{imp.raw_target}' (absent from stdlib, "
                            "module prefix, and go.mod)."
                        ),
                        remediation="Add module via 'go get' in go.mod or remove phantom import.",
                    )
                )
        return out
    builtins = (
        TS_BUILTIN_MODULES if unit.language == SupportedLanguage.TYPESCRIPT else RUST_BUILTIN_CRATES
    )
    for imp in unit.imports:
        finding = _check_ts_or_rust_import_vrf_003(unit, imp, eco, builtins, local_roots)
        if finding is not None:
            out.append(finding)
    return out


_POLYGLOT_RULE_DISPATCH: Final[Mapping[str, _UnitRuleFn]] = MappingProxyType(
    {
        "JBC-DBC-001": _check_poly_dbc_001,
        "JBC-DBC-002": _check_poly_dbc_002,
        "JBC-DBC-003": _check_poly_dbc_003,
        "JBC-DBC-004": _check_poly_dbc_004,
        "JBC-DBC-005": _check_poly_dbc_005,
        "JBC-FLT-004": _check_poly_flt_004,
        "JBC-SEC-002": _check_poly_sec_002,
        "JBC-SEC-003": _check_poly_sec_003,
        "JBC-SEC-004": _check_poly_sec_004,
        "JBC-COG-001": _check_poly_cog_001,
        "JBC-COG-002": _check_poly_cog_002,
        "JBC-COG-005": _check_poly_cog_005,
        "JBC-OBS-002": _check_poly_obs_002,
        "JBC-VRF-001": _check_poly_vrf_001,
    }
)


@jezz_callable
def check_polyglot_unit_code(
    code_id: IdentifierStr,
    unit: GuardedPolyglotUnit,
    manifest: PolyglotManifest,
    *,
    local_roots: Annotated[AbstractSet[IdentifierStr], WIRE_FROZENSET_VALIDATOR] = frozenset(),
    limits: ResolvedComplexityLimits | None = None,
) -> list[RawDiscrepancy]:
    """Evaluate a single per-unit JBC v1.2 provision on ``unit``."""
    direct = [h for h in unit.hazards if h.code_id == code_id]
    if code_id == "JBC-VRF-003":
        return [*direct, *_check_poly_vrf_003(unit, manifest, local_roots)]
    eff = limits or _DEFAULT_POLY_LIMITS
    if code_id == "JBC-COG-001":
        return [*direct, *_check_poly_cog_001_with_limits(unit, eff)]
    if code_id == "JBC-COG-002":
        return [*direct, *_check_poly_cog_002_with_limits(unit, eff)]
    evaluator = _POLYGLOT_RULE_DISPATCH.get(code_id)
    if evaluator is None:
        return direct
    return [*direct, *evaluator(unit)]


def _scan_unit_cog_003_imports(
    unit: PolyglotUnit,
    known_mods: AbstractSet[str],
) -> tuple[list[RawDiscrepancy], frozenset[str]]:
    discrepancies: list[RawDiscrepancy] = []
    deps: set[str] = set()
    for imp in unit.imports:
        span = SourceSpan(file_path=unit.file_path, line=imp.line, column=imp.column)
        if imp.is_wildcard:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-003",
                    location=span,
                    message=f"Wildcard import '{imp.raw_target}' pollutes module namespace.",
                    remediation="Import explicit public symbol names.",
                )
            )
        for priv in imp.private_symbols:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-COG-003",
                    location=span,
                    message=(
                        f"Cross-module import of private/internal symbol '{priv}' "
                        "breaches module encapsulation."
                    ),
                    remediation="Promote symbol to an explicit public contract or keep local.",
                )
            )
        target_mod = resolve_polyglot_import_target(unit.language, imp, known_mods)
        if unit.module_name and target_mod and target_mod != unit.module_name:
            deps.add(target_mod)
    return discrepancies, frozenset(deps)


def _scan_language_cog_003(
    lang_units: Sequence[PolyglotUnit],
) -> tuple[list[RawDiscrepancy], dict[str, PolyglotUnit], dict[str, frozenset[str]]]:
    discrepancies: list[RawDiscrepancy] = []
    units_by_mod: dict[str, PolyglotUnit] = {}
    for unit in lang_units:
        if unit.module_name and unit.module_name not in units_by_mod:
            units_by_mod[unit.module_name] = unit
    known_mods = frozenset(units_by_mod)
    deps_by_mod: dict[str, set[str]] = {mod: set() for mod in known_mods}

    for unit in lang_units:
        unit_disc, unit_deps = _scan_unit_cog_003_imports(unit, known_mods)
        discrepancies.extend(unit_disc)
        if unit.module_name in deps_by_mod:
            deps_by_mod[unit.module_name] |= set(unit_deps)

    frozen_graph = {mod: frozenset(targets) for mod, targets in deps_by_mod.items()}
    return discrepancies, units_by_mod, frozen_graph


@jezz_callable
def check_polyglot_cog_003(
    units: Annotated[Sequence[GuardedPolyglotUnit], Field(max_length=100_000)],
) -> list[RawDiscrepancy]:
    """Run whole-graph JBC-COG-003 cycle, wildcard, and private-import checks over ``units``."""
    return check_polyglot_cog_003_for_paths(
        units,
        frozenset(u.file_path for u in units),
    )


@jezz_callable
def check_polyglot_cog_003_for_paths(
    units: Annotated[Sequence[GuardedPolyglotUnit], Field(max_length=100_000)],
    target_paths: Annotated[AbstractSet[IdentifierStr], WIRE_FROZENSET_VALIDATOR],
) -> list[RawDiscrepancy]:
    """Run whole-graph JBC-COG-003 per language and return findings touching ``target_paths``."""
    by_lang: dict[SupportedLanguage, list[PolyglotUnit]] = {}
    for unit in units:
        by_lang.setdefault(unit.language, []).append(unit)

    findings: list[RawDiscrepancy] = [
        h
        for u in units
        if u.file_path in target_paths
        for h in u.hazards
        if h.code_id == "JBC-COG-003"
    ]
    for lang in sorted(by_lang, key=str):
        per_unit, units_by_mod, graph = _scan_language_cog_003(by_lang[lang])
        findings.extend(d for d in per_unit if d.location.file_path in target_paths)
        for cycle in find_tarjan_cycles(graph):
            touched = [mod for mod in cycle if units_by_mod[mod].file_path in target_paths]
            if not touched:
                continue
            anchor = units_by_mod[touched[0]]
            cycle_text = " <-> ".join(cycle[:8])
            findings.append(
                RawDiscrepancy(
                    code_id="JBC-COG-003",
                    location=SourceSpan(file_path=anchor.file_path, line=1, symbol=touched[0]),
                    message=f"Cyclic module/package dependency detected: {cycle_text}.",
                    remediation="Break dependency cycle by extracting shared types downward.",
                )
            )
    return findings
