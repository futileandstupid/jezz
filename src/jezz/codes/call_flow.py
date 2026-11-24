"""Whole-workspace polyglot inter-procedural call-flow & taint propagation engine (JBC v1.2)."""

import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Final

from pydantic import BaseModel, Field

from jezz.codes.ast_utils import RawDiscrepancy, SourceSpan, SourceUnit
from jezz.codes.call_flow_extract import (
    ArgFlowFact,
    CallSiteFlowFact,
    ExtractedCallableFlow,
    extract_polyglot_unit_flows,
)
from jezz.codes.call_flow_py import extract_python_unit_flows
from jezz.codes.dep_graph import match_known_module
from jezz.codes.polyglot_ir import InspectedUnit, SupportedLanguage
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_FROZENSET_VALIDATOR,
    WIRE_TUPLE_VALIDATOR,
    IdentifierStr,
    LoopBudget,
    NonNegativeInt,
    jezz_callable,
)

type CallableKey = tuple[str, str]

_DIVISOR_MSG_RE: Final[re.Pattern[str]] = re.compile(r"^Divisor parameter '([^']+)' in '")
_UNBOUNDED_PARAM_MSG_RE: Final[re.Pattern[str]] = re.compile(r"^Public parameter '([^']+)' in '")


class CallableFlowSummary(BaseModel):
    """Fixed-point inter-procedural summary for a single workspace callable."""

    model_config = STRICT_FROZEN_CONFIG

    file_path: IdentifierStr
    qualname: IdentifierStr
    language: SupportedLanguage
    guarded_param_indices: Annotated[
        frozenset[NonNegativeInt],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=64),
    ] = frozenset()
    sql_sink_param_indices: Annotated[
        frozenset[NonNegativeInt],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=64),
    ] = frozenset()
    path_sink_param_indices: Annotated[
        frozenset[NonNegativeInt],
        WIRE_FROZENSET_VALIDATOR,
        Field(max_length=64),
    ] = frozenset()
    returns_dynamic_sql: bool = False
    returns_uncontained_path: bool = False


class WorkspaceCallFlow(BaseModel):
    """Deterministic inter-procedural call-flow summary table across all inspected units."""

    model_config = STRICT_FROZEN_CONFIG

    summaries: Annotated[
        tuple[CallableFlowSummary, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=100_000),
    ] = ()


@dataclass
class _MutableSummary:
    """Mutable monotone lattice element updated during fixed-point iteration."""

    guarded_params: set[int]
    sql_sink_params: set[int]
    path_sink_params: set[int]
    returns_dynamic_sql: bool
    returns_uncontained_path: bool


@dataclass(frozen=True)
class _WorkspaceFlowIndex:
    """Lookup tables mapping intra- and cross-module call names to ``CallableKey``s."""

    nodes: Mapping[CallableKey, ExtractedCallableFlow]
    by_file_name: Mapping[tuple[str, str], CallableKey]
    by_lang_mod_name: Mapping[tuple[SupportedLanguage, str, str], CallableKey]
    known_mods_by_lang: Mapping[SupportedLanguage, frozenset[str]]


@dataclass(frozen=True)
class _SolvedCallFlow:
    """Paired workspace callable index and fixed-point summary table."""

    index: _WorkspaceFlowIndex
    summaries: Mapping[CallableKey, CallableFlowSummary]


def _extract_all_nodes(units: Sequence[InspectedUnit]) -> tuple[ExtractedCallableFlow, ...]:
    nodes: list[ExtractedCallableFlow] = []
    for unit in units:
        if isinstance(unit, SourceUnit):
            nodes.extend(extract_python_unit_flows(unit))
        else:
            nodes.extend(extract_polyglot_unit_flows(unit))
    return tuple(nodes)


def _build_flow_index(nodes: Sequence[ExtractedCallableFlow]) -> _WorkspaceFlowIndex:
    by_key: dict[CallableKey, ExtractedCallableFlow] = {}
    by_file_name: dict[tuple[str, str], CallableKey] = {}
    by_lang_mod_name: dict[tuple[SupportedLanguage, str, str], CallableKey] = {}
    mods_by_lang: dict[SupportedLanguage, set[str]] = {}

    for node in nodes:
        key: CallableKey = (node.file_path, node.qualname)
        by_key[key] = node
        if "." not in node.qualname:
            by_file_name[(node.file_path, node.name)] = key
        if node.module_name and "." not in node.qualname:
            by_lang_mod_name[(node.language, node.module_name, node.name)] = key
            mods_by_lang.setdefault(node.language, set()).add(node.module_name)

    return _WorkspaceFlowIndex(
        nodes=MappingProxyType(by_key),
        by_file_name=MappingProxyType(by_file_name),
        by_lang_mod_name=MappingProxyType(by_lang_mod_name),
        known_mods_by_lang=MappingProxyType(
            {lang: frozenset(mods) for lang, mods in mods_by_lang.items()}
        ),
    )


def _match_module_hint(mod_hint: str, known_mods: frozenset[str]) -> str:
    if not mod_hint or not known_mods:
        return ""
    exact = match_known_module(mod_hint, known_mods)
    if exact:
        return exact
    suffix_hits = [
        cand
        for cand in known_mods
        if cand.endswith((f".{mod_hint}", f"/{mod_hint}")) or mod_hint.endswith(f"/{cand}")
    ]
    if not suffix_hits:
        return ""
    suffix_hits.sort(key=len, reverse=True)
    return suffix_hits[0]


def _resolve_imported_callee(
    caller: ExtractedCallableFlow,
    raw_callee: str,
    index: _WorkspaceFlowIndex,
) -> CallableKey | None:
    known_mods = index.known_mods_by_lang.get(caller.language, frozenset())
    if raw_callee in caller.import_aliases:
        mod_hint, sym = caller.import_aliases[raw_callee]
        resolved_mod = _match_module_hint(mod_hint, known_mods)
        if resolved_mod:
            return index.by_lang_mod_name.get((caller.language, resolved_mod, sym or raw_callee))
    if "." in raw_callee:
        head, tail = raw_callee.split(".", maxsplit=1)
        if head in caller.import_aliases:
            mod_hint, _ = caller.import_aliases[head]
            resolved_mod = _match_module_hint(mod_hint, known_mods)
            if resolved_mod:
                return index.by_lang_mod_name.get((caller.language, resolved_mod, tail))
    return None


def _resolve_rust_path_callee(
    caller: ExtractedCallableFlow,
    raw_callee: str,
    index: _WorkspaceFlowIndex,
) -> CallableKey | None:
    if caller.language != SupportedLanguage.RUST or "::" not in raw_callee:
        return None
    segs = [s for s in raw_callee.split("::") if s and s not in {"crate", "self", "super"}]
    if len(segs) < 2:
        return None
    known_mods = index.known_mods_by_lang.get(SupportedLanguage.RUST, frozenset())
    resolved_mod = _match_module_hint(".".join(segs[:-1]), known_mods)
    if not resolved_mod:
        return None
    return index.by_lang_mod_name.get((SupportedLanguage.RUST, resolved_mod, segs[-1]))


def _resolve_callee(
    caller: ExtractedCallableFlow,
    raw_callee: str,
    index: _WorkspaceFlowIndex,
) -> CallableKey | None:
    if caller.enclosing_type and raw_callee.startswith(("self.", "cls.", "this.")):
        tail = raw_callee.split(".", maxsplit=1)[1]
        method_key = (caller.file_path, f"{caller.enclosing_type}.{tail}")
        if method_key in index.nodes:
            return method_key
    same_qual = (caller.file_path, raw_callee)
    if same_qual in index.nodes:
        return same_qual
    if (caller.file_path, raw_callee) in index.by_file_name:
        return index.by_file_name[(caller.file_path, raw_callee)]
    if caller.language == SupportedLanguage.GO and "." not in raw_callee and caller.module_name:
        go_hit = index.by_lang_mod_name.get((SupportedLanguage.GO, caller.module_name, raw_callee))
        if go_hit is not None:
            return go_hit
    imp_hit = _resolve_imported_callee(caller, raw_callee, index)
    if imp_hit is not None:
        return imp_hit
    return _resolve_rust_path_callee(caller, raw_callee, index)


def _propagate_call_site_to_caller(
    caller: ExtractedCallableFlow,
    cs: CallSiteFlowFact,
    caller_sum: _MutableSummary,
    callee_sum: _MutableSummary,
) -> bool:
    changed = False
    for arg_idx, arg in enumerate(cs.args):
        if (
            cs.is_entry_level
            and arg.direct_param_index is not None
            and arg_idx in callee_sum.guarded_params
            and arg.direct_param_index not in caller_sum.guarded_params
        ):
            caller_sum.guarded_params.add(arg.direct_param_index)
            changed = True
        if arg_idx in callee_sum.sql_sink_params:
            new_sql = arg.referenced_params - caller_sum.sql_sink_params
            if new_sql:
                caller_sum.sql_sink_params.update(new_sql)
                changed = True
        if not caller.has_containment_guard and arg_idx in callee_sum.path_sink_params:
            new_path = arg.referenced_params - caller_sum.path_sink_params
            if new_path:
                caller_sum.path_sink_params.update(new_path)
                changed = True
    return changed


def _propagate_return_calls(
    caller: ExtractedCallableFlow,
    caller_sum: _MutableSummary,
    index: _WorkspaceFlowIndex,
    summaries: Mapping[CallableKey, _MutableSummary],
) -> bool:
    changed = False
    for ret_callee in caller.return_calls:
        target = _resolve_callee(caller, ret_callee, index)
        if target is None:
            continue
        t_sum = summaries[target]
        if t_sum.returns_dynamic_sql and not caller_sum.returns_dynamic_sql:
            caller_sum.returns_dynamic_sql = True
            changed = True
        if (
            not caller.has_containment_guard
            and t_sum.returns_uncontained_path
            and not caller_sum.returns_uncontained_path
        ):
            caller_sum.returns_uncontained_path = True
            changed = True
    return changed


def _solve_summaries(index: _WorkspaceFlowIndex) -> _SolvedCallFlow:
    mutable: dict[CallableKey, _MutableSummary] = {
        key: _MutableSummary(
            guarded_params=set(node.direct_guarded_params),
            sql_sink_params=set(node.direct_sql_sink_params),
            path_sink_params=set(node.direct_path_sink_params),
            returns_dynamic_sql=node.direct_returns_dynamic_sql,
            returns_uncontained_path=node.direct_returns_uncontained_path,
        )
        for key, node in index.nodes.items()
    }
    ordered_keys = sorted(index.nodes)
    budget = LoopBudget(max_steps=max(64, len(ordered_keys) * 32 + 16))
    changed = True
    while changed:
        budget.tick()
        changed = False
        for key in ordered_keys:
            caller = index.nodes[key]
            caller_sum = mutable[key]
            for cs in caller.call_sites:
                target = _resolve_callee(caller, cs.callee, index)
                if target is not None and _propagate_call_site_to_caller(
                    caller, cs, caller_sum, mutable[target]
                ):
                    changed = True
            if _propagate_return_calls(caller, caller_sum, index, mutable):
                changed = True

    frozen = MappingProxyType(
        {
            key: CallableFlowSummary(
                file_path=index.nodes[key].file_path,
                qualname=index.nodes[key].qualname,
                language=index.nodes[key].language,
                guarded_param_indices=frozenset(m.guarded_params),
                sql_sink_param_indices=frozenset(m.sql_sink_params),
                path_sink_param_indices=frozenset(m.path_sink_params),
                returns_dynamic_sql=m.returns_dynamic_sql,
                returns_uncontained_path=m.returns_uncontained_path,
            )
            for key, m in mutable.items()
        }
    )
    return _SolvedCallFlow(index=index, summaries=frozen)


def _effective_tainted_vars(
    caller: ExtractedCallableFlow,
    solved: _SolvedCallFlow,
) -> tuple[frozenset[str], frozenset[str]]:
    sql_vars = set(caller.local_sql_vars)
    path_vars = set() if caller.has_containment_guard else set(caller.local_uncontained_vars)
    for lhs, cs in caller.call_assigns:
        target = _resolve_callee(caller, cs.callee, solved.index)
        if target is None:
            continue
        t_sum = solved.summaries[target]
        if t_sum.returns_dynamic_sql:
            sql_vars.add(lhs)
        if not caller.has_containment_guard and t_sum.returns_uncontained_path:
            path_vars.add(lhs)
    return frozenset(sql_vars), frozenset(path_vars)


def _arg_carries_dynamic_sql(
    caller: ExtractedCallableFlow,
    arg: ArgFlowFact,
    eff_sql_vars: frozenset[str],
    solved: _SolvedCallFlow,
) -> bool:
    if arg.is_inline_dynamic_sql or bool(arg.referenced_vars & eff_sql_vars):
        return True
    return any(
        (t := _resolve_callee(caller, nc, solved.index)) is not None
        and solved.summaries[t].returns_dynamic_sql
        for nc in arg.nested_callees
    )


def _arg_carries_helper_uncontained_path(
    caller: ExtractedCallableFlow,
    arg: ArgFlowFact,
    eff_path_vars: frozenset[str],
    solved: _SolvedCallFlow,
    *,
    include_inline_param: bool,
) -> bool:
    if caller.has_containment_guard or caller.is_test_file:
        return False
    if include_inline_param and arg.is_inline_uncontained_path:
        return True
    if bool(arg.referenced_vars & eff_path_vars):
        return True
    return any(
        (t := _resolve_callee(caller, nc, solved.index)) is not None
        and solved.summaries[t].returns_uncontained_path
        for nc in arg.nested_callees
    )


def _emit_call_site_taint_findings(
    caller: ExtractedCallableFlow,
    cs: CallSiteFlowFact,
    eff_sql_vars: frozenset[str],
    eff_path_vars: frozenset[str],
    solved: _SolvedCallFlow,
) -> list[RawDiscrepancy]:
    out: list[RawDiscrepancy] = []
    span = SourceSpan(
        file_path=caller.file_path,
        line=cs.line,
        column=cs.column,
        symbol=caller.qualname,
    )
    if cs.is_sql_sink and len(cs.args) > cs.sql_query_arg_index:
        q_arg = cs.args[cs.sql_query_arg_index]
        if _arg_carries_dynamic_sql(caller, q_arg, eff_sql_vars, solved):
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-001",
                    location=span,
                    message=(
                        f"SQL sink '{cs.callee}()' receives inter-procedurally interpolated query."
                    ),
                    remediation="Return static SQL templates and bind parameters separately.",
                )
            )
    if (
        cs.is_path_sink
        and cs.args
        and _arg_carries_helper_uncontained_path(
            caller, cs.args[0], eff_path_vars, solved, include_inline_param=False
        )
    ):
        out.append(
            RawDiscrepancy(
                code_id="JBC-SEC-005",
                location=span,
                message=(
                    f"Sink '{cs.callee}()' receives uncontained path returned by helper call."
                ),
                remediation="Verify canonical root containment inside path helper before sink.",
            )
        )

    target = _resolve_callee(caller, cs.callee, solved.index)
    if target is None:
        return out
    t_sum = solved.summaries[target]
    for arg_idx, arg in enumerate(cs.args):
        if arg_idx in t_sum.sql_sink_param_indices and _arg_carries_dynamic_sql(
            caller, arg, eff_sql_vars, solved
        ):
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-001",
                    location=span,
                    message=(
                        f"Call to '{cs.callee}()' forwards dynamically interpolated SQL to sink "
                        f"'{t_sum.qualname}'."
                    ),
                    remediation="Pass parameterized query strings and bound parameter tuples.",
                )
            )
        if arg_idx in t_sum.path_sink_param_indices and _arg_carries_helper_uncontained_path(
            caller, arg, eff_path_vars, solved, include_inline_param=True
        ):
            out.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-005",
                    location=span,
                    message=(
                        f"Call to '{cs.callee}()' forwards uncontained path to filesystem sink "
                        f"'{t_sum.qualname}'."
                    ),
                    remediation="Anchor relative paths via ContainedPath/canonical root check.",
                )
            )
    return out


def _is_satisfied_by_interprocedural_guard(
    disc: RawDiscrepancy,
    index: _WorkspaceFlowIndex,
    guarded_names_by_key: Mapping[CallableKey, frozenset[str]],
) -> bool:
    key: CallableKey = (disc.location.file_path, disc.location.symbol or "")
    node = index.nodes.get(key)
    inter_guarded = guarded_names_by_key.get(key, frozenset())
    if node is None or not inter_guarded:
        return False
    if disc.code_id == "JBC-DBC-001":
        m = _DIVISOR_MSG_RE.match(disc.message)
        return m is not None and m.group(1) in inter_guarded
    if node.language == SupportedLanguage.PYTHON:
        return False
    if disc.code_id == "JBC-DBC-005":
        m = _UNBOUNDED_PARAM_MSG_RE.match(disc.message)
        return m is not None and m.group(1) in inter_guarded
    if disc.code_id == "JBC-DBC-004":
        all_guarded = node.intra_guarded_param_names | inter_guarded
        return (
            node.all_params_typed
            and node.unbounded_param_names <= all_guarded
            and node.erased_or_bag_param_names <= all_guarded
        )
    return False


def _append_interprocedural_taint_findings(
    solved: _SolvedCallFlow,
    refined: list[RawDiscrepancy],
    active_code_ids: AbstractSet[str],
    target_paths: AbstractSet[str] | None,
) -> None:
    seen_sites = {(d.code_id, d.location.file_path, d.location.line) for d in refined}
    for key in sorted(solved.index.nodes):
        caller = solved.index.nodes[key]
        if target_paths is not None and caller.file_path not in target_paths:
            continue
        eff_sql, eff_path = _effective_tainted_vars(caller, solved)
        for cs in caller.call_sites:
            for item in _emit_call_site_taint_findings(caller, cs, eff_sql, eff_path, solved):
                site_key = (item.code_id, item.location.file_path, item.location.line)
                if item.code_id in active_code_ids and site_key not in seen_sites:
                    seen_sites.add(site_key)
                    refined.append(item)


@jezz_callable
def build_workspace_call_flow(
    units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
) -> WorkspaceCallFlow:
    """Compute fixed-point inter-procedural call-flow summaries over ``units``."""
    if not units:
        return WorkspaceCallFlow()
    solved = _solve_summaries(_build_flow_index(_extract_all_nodes(units)))
    ordered = tuple(solved.summaries[k] for k in sorted(solved.summaries))
    return WorkspaceCallFlow(summaries=ordered)


@jezz_callable
def refine_discrepancies_with_call_flow(
    all_units: Annotated[Sequence[InspectedUnit], Field(max_length=100_000)],
    discrepancies: Annotated[Sequence[RawDiscrepancy], Field(max_length=250_000)],
    active_code_ids: Annotated[AbstractSet[IdentifierStr], WIRE_FROZENSET_VALIDATOR],
    *,
    target_paths: Annotated[AbstractSet[IdentifierStr], WIRE_FROZENSET_VALIDATOR] | None = None,
) -> list[RawDiscrepancy]:
    """Propagate inter-procedural SEC-001/005 taint and DBC-001/004/005 validator guards."""
    relevant = {"JBC-SEC-001", "JBC-SEC-005", "JBC-DBC-001", "JBC-DBC-004", "JBC-DBC-005"}
    if not all_units or not (active_code_ids & relevant):
        return list(discrepancies)

    solved = _solve_summaries(_build_flow_index(_extract_all_nodes(all_units)))
    guarded_names_by_key = {
        key: frozenset(
            solved.index.nodes[key].param_names[i]
            for i in summary.guarded_param_indices
            if i < len(solved.index.nodes[key].param_names)
        )
        for key, summary in solved.summaries.items()
    }
    refined = [
        d
        for d in discrepancies
        if not _is_satisfied_by_interprocedural_guard(d, solved.index, guarded_names_by_key)
    ]
    if active_code_ids & {"JBC-SEC-001", "JBC-SEC-005"}:
        _append_interprocedural_taint_findings(solved, refined, active_code_ids, target_paths)
    return refined
