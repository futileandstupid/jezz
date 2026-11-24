"""Shared O(V + E) iterative Tarjan SCC and module-prefix resolution kernel for JBC-COG-003."""

from collections.abc import Iterator, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Annotated

import icontract
from pydantic import Field

from jezz.codes.polyglot_ir import PolyglotImport, SupportedLanguage
from jezz.guards import InstanceGuard, LoopBudget, jezz_callable

type ModuleRefStr = Annotated[str, Field(max_length=1024)]


@dataclass
class _TarjanState:
    """Mutable bookkeeping state for iterative O(V + E) Tarjan SCC discovery."""

    next_index: int
    indices: dict[str, int]
    lowlink: dict[str, int]
    stack: list[str]
    on_stack: set[str]


def _enter_tarjan_vertex(
    vertex: str,
    graph: Mapping[str, frozenset[str]],
    state: _TarjanState,
    frames: list[tuple[str, Iterator[str]]],
) -> None:
    state.indices[vertex] = state.next_index
    state.lowlink[vertex] = state.next_index
    state.next_index += 1
    state.stack.append(vertex)
    state.on_stack.add(vertex)
    frames.append((vertex, iter(sorted(graph.get(vertex, frozenset())))))


def _pop_tarjan_scc(root: str, state: _TarjanState) -> tuple[str, ...]:
    members: list[str] = []
    pop_budget = LoopBudget(max_steps=max(16, len(state.stack) + 4))
    while state.stack:
        pop_budget.tick()
        member = state.stack.pop()
        state.on_stack.discard(member)
        members.append(member)
        if member == root:
            break
    return tuple(sorted(members))


def _finish_tarjan_vertex(
    curr: str,
    state: _TarjanState,
    frames: list[tuple[str, Iterator[str]]],
    cycles: list[tuple[str, ...]],
) -> None:
    if frames:
        parent = frames[-1][0]
        state.lowlink[parent] = min(state.lowlink[parent], state.lowlink[curr])
    if state.lowlink[curr] == state.indices[curr]:
        scc = _pop_tarjan_scc(curr, state)
        if len(scc) >= 2:
            cycles.append(scc)


def _step_tarjan_edge(
    curr: str,
    nxt: str,
    graph: Mapping[str, frozenset[str]],
    state: _TarjanState,
    frames: list[tuple[str, Iterator[str]]],
) -> None:
    if nxt not in state.indices:
        _enter_tarjan_vertex(nxt, graph, state, frames)
        return
    if nxt in state.on_stack:
        state.lowlink[curr] = min(state.lowlink[curr], state.indices[nxt])


@icontract.ensure(lambda result: all(len(scc) >= 2 for scc in result))
@jezz_callable
def find_tarjan_cycles(
    graph: Annotated[Mapping[str, frozenset[str]], InstanceGuard(Mapping)],
) -> list[tuple[str, ...]]:
    """Return canonical sorted nontrivial SCC cycles in O(V + E) time without recursion."""
    state = _TarjanState(next_index=0, indices={}, lowlink={}, stack=[], on_stack=set())
    cycles: list[tuple[str, ...]] = []
    edge_total = sum(len(targets) for targets in graph.values())
    dfs_budget = LoopBudget(max_steps=max(64, len(graph) * 4 + edge_total * 4))

    for seed in sorted(graph):
        if seed in state.indices:
            continue
        frames: list[tuple[str, Iterator[str]]] = []
        _enter_tarjan_vertex(seed, graph, state, frames)
        while frames:
            dfs_budget.tick()
            curr, neighbors = frames[-1]
            nxt = next(neighbors, None)
            if nxt is None:
                frames.pop()
                _finish_tarjan_vertex(curr, state, frames, cycles)
                continue
            _step_tarjan_edge(curr, nxt, graph, state, frames)
    return sorted(cycles)


@icontract.ensure(
    lambda candidate, result: (
        not result or candidate == result or candidate.startswith(f"{result}.")
    )
)
@jezz_callable
def match_known_module(
    candidate: ModuleRefStr,
    known_modules: Annotated[AbstractSet[str], InstanceGuard(AbstractSet)],
) -> ModuleRefStr:
    """Resolve the longest dotted module prefix of ``candidate`` present in ``known_modules``."""
    probe = candidate
    budget = LoopBudget(max_steps=32)
    while probe:
        budget.tick()
        if probe in known_modules:
            return probe
        if "." not in probe:
            return ""
        probe = probe.rsplit(".", maxsplit=1)[0]
    return ""


@jezz_callable
def resolve_polyglot_import_target(
    language: SupportedLanguage,
    imp: PolyglotImport,
    known_modules: Annotated[AbstractSet[str], InstanceGuard(AbstractSet)],
) -> ModuleRefStr:
    """Resolve an intra-workspace polyglot import against ``known_modules``."""
    dotted_hit = match_known_module(imp.resolved_module, known_modules)
    if dotted_hit:
        return dotted_hit
    if language != SupportedLanguage.GO:
        return ""
    matches = [
        cand
        for cand in known_modules
        if imp.raw_target == cand or imp.raw_target.endswith(f"/{cand}")
    ]
    if not matches:
        return ""
    matches.sort(key=len, reverse=True)
    return matches[0]


__all__ = [
    "ModuleRefStr",
    "find_tarjan_cycles",
    "match_known_module",
    "resolve_polyglot_import_target",
]
