"""Class duty isolation, LCOM4 state-cohesion graph, and WMC panel capacity analysis."""

import ast
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Annotated, Final

from pydantic import BaseModel, Field

from jezz.codes.ast_utils import (
    dotted_expr_name,
    is_enum_class,
    is_node_visitor_class,
    is_protocol_class,
    is_pydantic_model_class,
    iter_local_scope_nodes,
)
from jezz.codes.complexity import compute_callable_metrics
from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    IdentifierStr,
    LineNumber,
    LoopBudget,
    NonNegativeInt,
    jezz_callable,
)

MAX_CLASS_WMC_LIMIT: Final[int] = 35
MAX_PUBLIC_METHODS_LIMIT: Final[int] = 7
MAX_BEHAVIORAL_FIELDS_LIMIT: Final[int] = 5
MAX_LCOM4_COMPONENTS: Final[int] = 1

_EXTERNAL_IO_CALLS: Final[frozenset[str]] = frozenset(
    {
        "open",
        "read_bounded_text",
        "socket.socket",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_output",
        "urllib.request.urlopen",
        "requests.get",
        "requests.post",
        "httpx.get",
        "httpx.post",
    }
)
_EXEMPT_METHOD_DEC_TAILS: Final[frozenset[str]] = frozenset(
    {"classmethod", "staticmethod", "abstractmethod", "overload", "property"}
)


class ClassCohesionMetrics(BaseModel):
    """Cohesion, role-purity, and structural panel-capacity measurements for a class."""

    model_config = STRICT_FROZEN_CONFIG

    class_name: IdentifierStr
    line: LineNumber
    is_schema_or_value: bool
    performs_external_io: bool
    wmc: NonNegativeInt
    public_method_count: NonNegativeInt
    instance_field_count: NonNegativeInt
    lcom4: NonNegativeInt
    stateless_methods: Annotated[tuple[IdentifierStr, ...], Field(max_length=128)]


def _direct_methods(cls_node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        stmt for stmt in cls_node.body if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _decorator_tails(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {dotted_expr_name(dec).rsplit(".", maxsplit=1)[-1] for dec in func.decorator_list}


def _is_pure_dataclass(cls_node: ast.ClassDef, methods: Sequence[ast.AST]) -> bool:
    dec_tails = {dotted_expr_name(d).rsplit(".", maxsplit=1)[-1] for d in cls_node.decorator_list}
    return "dataclass" in dec_tails and len(methods) == 0


def _class_performs_io(methods: Sequence[ast.FunctionDef | ast.AsyncFunctionDef]) -> bool:
    for method in methods:
        for node in iter_local_scope_nodes(method):
            if not isinstance(node, ast.Call):
                continue
            callee = dotted_expr_name(node.func)
            if callee in _EXTERNAL_IO_CALLS:
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "execute",
                "executemany",
            }:
                return True
    return False


def _collect_written_instance_fields(
    cls_node: ast.ClassDef,
    methods: Sequence[ast.FunctionDef | ast.AsyncFunctionDef],
) -> set[str]:
    fields: set[str] = set()
    for stmt in cls_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            ann_name = dotted_expr_name(stmt.annotation).rsplit(".", maxsplit=1)[-1]
            if ann_name != "ClassVar":
                fields.add(stmt.target.id)
    for method in methods:
        for node in iter_local_scope_nodes(method):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and isinstance(node.ctx, ast.Store)
            ):
                fields.add(node.attr)
    return fields


def _extract_method_self_footprint(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    all_method_names: frozenset[str],
) -> tuple[set[str], set[str], bool]:
    """Return ``(accessed_fields, called_self_methods, uses_super_or_self_ref)``."""
    fields: set[str] = set()
    calls: set[str] = set()
    uses_self_or_super = False
    for node in iter_local_scope_nodes(method):
        if isinstance(node, ast.Call) and dotted_expr_name(node.func) == "super":
            uses_self_or_super = True
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            uses_self_or_super = True
            if node.attr in all_method_names:
                calls.add(node.attr)
            else:
                fields.add(node.attr)
    return fields, calls, uses_self_or_super


def _count_connected_components(adjacency: Mapping[str, set[str]]) -> int:
    """Count connected components in an undirected method-cohesion graph."""
    if not adjacency:
        return 0
    visited: set[str] = set()
    components = 0
    budget = LoopBudget(max_steps=max(32, len(adjacency) * 8))
    for seed in sorted(adjacency):
        if seed in visited:
            continue
        components += 1
        visited.add(seed)
        queue: deque[str] = deque([seed])
        while queue:
            budget.tick()
            curr = queue.popleft()
            for nxt in adjacency[curr]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
    return components


def _build_method_adjacency(
    method_fields: Mapping[str, set[str]],
    method_calls: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    active = sorted(method_fields)
    adjacency: dict[str, set[str]] = {name: set() for name in active}
    for idx, m1 in enumerate(active):
        for m2 in active[idx + 1 :]:
            if (method_fields[m1] & method_fields[m2]) or (
                m2 in method_calls[m1] or m1 in method_calls[m2]
            ):
                adjacency[m1].add(m2)
                adjacency[m2].add(m1)
    return adjacency


def _analyze_lcom4_and_stateless(
    methods: Sequence[ast.FunctionDef | ast.AsyncFunctionDef],
    *,
    is_visitor: bool,
) -> tuple[int, tuple[str, ...]]:
    all_names = frozenset(m.name for m in methods)
    method_fields: dict[str, set[str]] = {}
    method_calls: dict[str, set[str]] = {}
    stateless: list[str] = []

    for m in methods:
        is_dunder = m.name.startswith("__") and m.name.endswith("__")
        decs = _decorator_tails(m)
        if is_dunder or (decs & {"classmethod", "staticmethod", "abstractmethod", "overload"}):
            continue
        fields, calls, touched = _extract_method_self_footprint(m, all_names)
        if not touched:
            if not (is_visitor and m.name.startswith("visit_")):
                stateless.append(m.name)
            continue
        if is_visitor and m.name == "visit":
            calls |= {other.name for other in methods if other.name.startswith("visit_")}
        method_fields[m.name] = fields
        method_calls[m.name] = calls

    adjacency = _build_method_adjacency(method_fields, method_calls)
    return _count_connected_components(adjacency), tuple(stateless)


@jezz_callable
def compute_class_cohesion(cls_node: ast.ClassDef) -> ClassCohesionMetrics:
    """Compute role purity, WMC, public method arity, field count, and LCOM4 for ``cls_node``."""
    methods = _direct_methods(cls_node)
    is_visitor = is_node_visitor_class(cls_node)
    is_schema = (
        is_pydantic_model_class(cls_node)
        or is_enum_class(cls_node)
        or is_protocol_class(cls_node)
        or _is_pure_dataclass(cls_node, methods)
    )
    wmc = sum(
        compute_callable_metrics(
            m,
            qualname=f"{cls_node.name}.{m.name}",
            is_method=True,
        ).cognitive_complexity
        for m in methods
    )
    public_count = sum(
        1
        for m in methods
        if not m.name.startswith("_")
        and not (_decorator_tails(m) & _EXEMPT_METHOD_DEC_TAILS)
        and not (is_visitor and m.name.startswith("visit_"))
    )
    fields = _collect_written_instance_fields(cls_node, methods)
    lcom4, stateless = (
        (0, ()) if is_schema else _analyze_lcom4_and_stateless(methods, is_visitor=is_visitor)
    )
    return ClassCohesionMetrics(
        class_name=cls_node.name,
        line=cls_node.lineno,
        is_schema_or_value=is_schema,
        performs_external_io=_class_performs_io(methods),
        wmc=wmc,
        public_method_count=public_count,
        instance_field_count=len(fields),
        lcom4=lcom4,
        stateless_methods=stateless,
    )
