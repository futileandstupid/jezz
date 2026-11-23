"""Static AST inspectors for Article II (FLT — Fault Isolation, Termination & Resources)."""

import ast
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Final

from jezz.codes.ast_utils import (
    CallableContext,
    GuardedSourceUnit,
    RawDiscrepancy,
    SourceSpan,
    SourceUnit,
    dotted_expr_name,
    is_abstract_or_overload,
    is_node_visitor_class,
    iter_local_scope_nodes,
)
from jezz.guards import LoopBudget, jezz_callable

_BROAD_EXCEPTIONS: Final[frozenset[str]] = frozenset({"Exception", "BaseException"})
_CONTROL_FLOW_EXITS: Final[frozenset[str]] = frozenset(
    {"SystemExit", "Exit", "Abort", "StopIteration", "StopAsyncIteration", "KeyboardInterrupt"}
)
_MANAGED_RESOURCE_CALLS: Final[frozenset[str]] = frozenset(
    {"open", "socket.socket", "tempfile.NamedTemporaryFile"}
)
_TIMEOUT_REQUIRED_CALLS: Final[frozenset[str]] = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "urllib.request.urlopen",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.request",
        "httpx.get",
        "httpx.post",
        "httpx.request",
    }
)
_FUEL_PARAM_NAMES: Final[frozenset[str]] = frozenset(
    {"max_depth", "remaining_depth", "fuel", "depth_budget", "budget"}
)
_FIRE_AND_FORGET_CALLS: Final[frozenset[str]] = frozenset(
    {"asyncio.create_task", "create_task", "asyncio.ensure_future", "ensure_future"}
)
_EXTERNAL_ASYNC_IO_PREFIXES: Final[tuple[str, ...]] = (
    "httpx.",
    "aiohttp.",
    "asyncio.open_connection",
    "asyncio.create_subprocess",
    "anyio.connect_",
)
_ASYNC_DEADLINE_MANAGERS: Final[frozenset[str]] = frozenset(
    {"timeout", "timeout_at", "fail_after", "move_on_after"}
)
_UNBOUNDED_READ_METHODS: Final[frozenset[str]] = frozenset({"fetchall", "read_text", "read_bytes"})


def _catches_broad_type(exc_type: ast.expr, *, remaining_depth: int = 8) -> bool:
    if remaining_depth <= 0:
        return False
    if isinstance(exc_type, ast.Tuple):
        return any(
            _catches_broad_type(elt, remaining_depth=remaining_depth - 1) for elt in exc_type.elts
        )
    tail = dotted_expr_name(exc_type).rsplit(".", maxsplit=1)[-1]
    return tail in _BROAD_EXCEPTIONS


def _is_noop_body(stmts: Sequence[ast.stmt]) -> bool:
    for stmt in stmts:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        return False
    return True


def _inspect_except_handler(
    unit: SourceUnit,
    node: ast.ExceptHandler,
) -> RawDiscrepancy | None:
    span = SourceSpan(file_path=unit.file_path, line=node.lineno, column=node.col_offset)
    if node.type is None:
        return RawDiscrepancy(
            code_id="JBC-FLT-001",
            location=span,
            message="Bare 'except:' clause catches SystemExit/KeyboardInterrupt.",
            remediation="Catch narrow exception types and log or re-raise with cause.",
        )
    if _is_noop_body(node.body):
        return RawDiscrepancy(
            code_id="JBC-FLT-001",
            location=span,
            message="Exception handler silently discards fault with pass/ellipsis.",
            remediation="Propagate exception, translate with 'from', or log context.",
        )
    has_action = any(isinstance(sub, ast.Raise | ast.Call) for sub in iter_local_scope_nodes(node))
    if _catches_broad_type(node.type) and not has_action:
        return RawDiscrepancy(
            code_id="JBC-FLT-001",
            location=span,
            message="Broad Exception handler returns fallback without fault action.",
            remediation="Narrow caught exception type or record structured diagnostics.",
        )
    return None


@jezz_callable
def check_flt_001(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-FLT-001 (prohibition of silent fault suppression)."""
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.ExceptHandler):
            finding = _inspect_except_handler(unit, node)
            if finding is not None:
                discrepancies.append(finding)
        if isinstance(node, ast.Call):
            tail = dotted_expr_name(node.func).rsplit(".", maxsplit=1)[-1]
            if tail == "suppress" and any(_catches_broad_type(arg) for arg in node.args):
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-FLT-001",
                        location=SourceSpan(
                            file_path=unit.file_path, line=node.lineno, column=node.col_offset
                        ),
                        message="contextlib.suppress() used with broad Exception/BaseException.",
                        remediation="Restrict suppress() to a specific recoverable exception type.",
                    )
                )
    return discrepancies


def _iter_handler_raises(handler: ast.ExceptHandler) -> list[ast.Raise]:
    raises: list[ast.Raise] = []
    stack: list[ast.AST] = list(handler.body)
    budget = LoopBudget(max_steps=50_000)
    while stack:
        budget.tick()
        curr = stack.pop()
        if isinstance(curr, ast.Raise):
            raises.append(curr)
        if isinstance(
            curr, ast.ExceptHandler | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            continue
        stack.extend(ast.iter_child_nodes(curr))
    return raises


def _inspect_raise_expr(unit: SourceUnit, node: ast.Raise) -> list[RawDiscrepancy]:
    if node.exc is None:
        return []
    tail = dotted_expr_name(node.exc).rsplit(".", maxsplit=1)[-1]
    if tail in _CONTROL_FLOW_EXITS:
        return []
    discrepancies: list[RawDiscrepancy] = []
    span = SourceSpan(file_path=unit.file_path, line=node.lineno, column=node.col_offset)
    if tail in _BROAD_EXCEPTIONS:
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=span,
                message=f"Raising generic '{tail}' obscures failure semantics.",
                remediation="Raise a specific exception subclass (ValueError, etc.).",
            )
        )
    is_empty_call = isinstance(node.exc, ast.Call) and not node.exc.args and not node.exc.keywords
    is_bare_cls = isinstance(node.exc, ast.Name) and node.exc.id[:1].isupper()
    if is_empty_call or is_bare_cls:
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=span,
                message="Exception raised with an empty diagnostic message.",
                remediation="Provide an actionable message naming the violated invariant.",
            )
        )
    return discrepancies


def _inspect_handler_chaining(
    unit: SourceUnit,
    handler: ast.ExceptHandler,
) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    for r_node in _iter_handler_raises(handler):
        if r_node.exc is None or r_node.cause is not None:
            continue
        if isinstance(r_node.exc, ast.Name) and r_node.exc.id == handler.name:
            continue
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-FLT-002",
                location=SourceSpan(
                    file_path=unit.file_path,
                    line=r_node.lineno,
                    column=r_node.col_offset,
                ),
                message="Replacement exception raised in except block without 'from'.",
                remediation="Chain cause via 'raise NewError(...) from err'.",
            )
        )
    return discrepancies


@jezz_callable
def check_flt_002(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-FLT-002 (causal exception chaining & diagnostic messages)."""
    abstract_ids = {
        id(sub)
        for ctx in unit.callables
        if is_abstract_or_overload(ctx)
        for sub in ast.walk(ctx.node)
    }
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if id(node) in abstract_ids:
            continue
        if isinstance(node, ast.Raise):
            discrepancies.extend(_inspect_raise_expr(unit, node))
        if isinstance(node, ast.ExceptHandler):
            discrepancies.extend(_inspect_handler_chaining(unit, node))
    return discrepancies


def _managed_call_ids(tree: ast.Module) -> set[int]:
    managed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                managed.add(id(item.context_expr))
        if isinstance(node, ast.Call):
            tail = dotted_expr_name(node.func).rsplit(".", maxsplit=1)[-1]
            if tail in {"enter_context", "enter_async_context"} and node.args:
                managed.add(id(node.args[0]))
    return managed


def _check_retry_decorators(unit: SourceUnit, ctx: CallableContext) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    for dec in ctx.node.decorator_list:
        tail = dotted_expr_name(dec).rsplit(".", maxsplit=1)[-1]
        if tail != "retry":
            continue
        span = SourceSpan(
            file_path=unit.file_path,
            line=dec.lineno,
            column=dec.col_offset,
            symbol=ctx.qualname,
        )
        if not isinstance(dec, ast.Call):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-003",
                    location=span,
                    message=f"Bare '@retry' on '{ctx.qualname}' defaults to infinite retries.",
                    remediation="Specify finite stop/attempts cap and narrow exception filter.",
                )
            )
            continue
        kw_names = {kw.arg for kw in dec.keywords if kw.arg is not None}
        has_stop = bool(kw_names & {"stop", "attempts", "max_attempts"})
        has_filter = bool(kw_names & {"retry", "on", "retry_on"})
        if not (has_stop and has_filter):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-003",
                    location=span,
                    message=(
                        f"'@retry(...)' on '{ctx.qualname}' omits attempt cap or narrow filter."
                    ),
                    remediation="Pass both stop/max_attempts and narrow retry/on exception filter.",
                )
            )
    return discrepancies


@jezz_callable
def check_flt_003(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-FLT-003 (managed handles, sync timeouts, & bounded retries)."""
    discrepancies: list[RawDiscrepancy] = []
    managed_ids = _managed_call_ids(unit.tree)
    for ctx in unit.callables:
        discrepancies.extend(_check_retry_decorators(unit, ctx))

    for node in ast.walk(unit.tree):
        if not isinstance(node, ast.Call):
            continue
        callee = dotted_expr_name(node.func)
        span = SourceSpan(file_path=unit.file_path, line=node.lineno, column=node.col_offset)
        if callee in _MANAGED_RESOURCE_CALLS and id(node) not in managed_ids:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-003",
                    location=span,
                    message=f"Resource '{callee}()' acquired outside a 'with' context manager.",
                    remediation=f"Acquire '{callee}(...)' inside a 'with' statement.",
                )
            )
        if callee in _TIMEOUT_REQUIRED_CALLS and not any(
            kw.arg == "timeout" for kw in node.keywords
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-003",
                    location=span,
                    message=f"Blocking I/O call '{callee}()' omits an explicit 'timeout='.",
                    remediation="Pass an explicit numeric 'timeout=' keyword argument.",
                )
            )
    return discrepancies


def _while_ticks_budget(while_node: ast.While) -> bool:
    """Return True iff ``while_node`` calls ``.tick()`` directly inside its own loop body."""
    stack: list[ast.AST] = list(while_node.body)
    budget = LoopBudget(max_steps=50_000)
    while stack:
        budget.tick()
        curr = stack.pop()
        if (
            isinstance(curr, ast.Call)
            and isinstance(curr.func, ast.Attribute)
            and curr.func.attr == "tick"
        ):
            return True
        if isinstance(curr, ast.While | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        stack.extend(ast.iter_child_nodes(curr))
    return False


def _callable_has_fuel_guard(ctx: CallableContext) -> bool:
    """Return True iff ``ctx`` enforces a fuel parameter or class RecursionBudget."""
    params = {
        a.arg for a in (*ctx.node.args.posonlyargs, *ctx.node.args.args, *ctx.node.args.kwonlyargs)
    }
    if params & _FUEL_PARAM_NAMES:
        return True
    if ctx.enclosing_class is not None:
        return any(
            isinstance(sub, ast.Call) and dotted_expr_name(sub.func).endswith("RecursionBudget")
            for sub in ast.walk(ctx.enclosing_class)
        )
    return False


def _resolve_local_callee(
    sub: ast.AST,
    ctx: CallableContext,
    known: set[str],
) -> str | None:
    if not isinstance(sub, ast.Call):
        return None
    if isinstance(sub.func, ast.Name) and sub.func.id in known:
        return sub.func.id
    if (
        ctx.enclosing_class is not None
        and isinstance(sub.func, ast.Attribute)
        and isinstance(sub.func.value, ast.Name)
        and sub.func.value.id == "self"
    ):
        candidate = f"{ctx.enclosing_class.name}.{sub.func.attr}"
        if candidate in known:
            return candidate
    return None


def _build_local_call_graph(unit: SourceUnit) -> Mapping[str, set[str]]:
    known = {ctx.qualname for ctx in unit.callables}
    graph: dict[str, set[str]] = {ctx.qualname: set() for ctx in unit.callables}
    for ctx in unit.callables:
        for sub in iter_local_scope_nodes(ctx.node):
            callee = _resolve_local_callee(sub, ctx, known)
            if callee is not None:
                graph[ctx.qualname].add(callee)
    return graph


def _node_in_call_cycle(start: str, graph: Mapping[str, set[str]]) -> bool:
    visited: set[str] = set()
    queue: deque[str] = deque(graph.get(start, set()))
    budget = LoopBudget(max_steps=max(32, len(graph) * 4))
    while queue:
        budget.tick()
        curr = queue.popleft()
        if curr == start:
            return True
        if curr not in visited:
            visited.add(curr)
            queue.extend(graph.get(curr, set()))
    return False


@jezz_callable
def check_flt_004(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-FLT-004 (LoopBudget on while & fuel bounds on recursion)."""
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.While) and not _while_ticks_budget(node):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-004",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message="While loop omits a deterministic LoopBudget.tick() step guard.",
                    remediation="Instantiate LoopBudget(max_steps=...) and call budget.tick().",
                )
            )
    for cls_node in unit.classes:
        if is_node_visitor_class(cls_node):
            has_budget = any(
                isinstance(sub, ast.Call) and dotted_expr_name(sub.func).endswith("RecursionBudget")
                for sub in ast.walk(cls_node)
            )
            if not has_budget:
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-FLT-004",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=cls_node.lineno,
                            column=cls_node.col_offset,
                            symbol=cls_node.name,
                        ),
                        message=f"AST visitor '{cls_node.name}' lacks a RecursionBudget guard.",
                        remediation="Guard visit() with RecursionBudget(max_depth=...).",
                    )
                )

    call_graph = _build_local_call_graph(unit)
    for ctx in unit.callables:
        if _node_in_call_cycle(ctx.qualname, call_graph) and not _callable_has_fuel_guard(ctx):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-004",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=ctx.node.lineno,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=f"Recursive callable '{ctx.qualname}' lacks a fuel/max_depth ceiling.",
                    remediation="Pass a decreasing remaining_depth/max_depth parameter or budget.",
                )
            )
    return discrepancies


def _async_fn_awaits_external_io(func: ast.AsyncFunctionDef) -> bool:
    for node in iter_local_scope_nodes(func):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            callee = dotted_expr_name(node.value.func)
            if callee.startswith(_EXTERNAL_ASYNC_IO_PREFIXES):
                return True
    return False


def _async_fn_has_deadline_scope(func: ast.AsyncFunctionDef) -> bool:
    for node in iter_local_scope_nodes(func):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                tail = dotted_expr_name(item.context_expr).rsplit(".", maxsplit=1)[-1]
                if tail in _ASYNC_DEADLINE_MANAGERS:
                    return True
    return False


def _task_group_var_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        for item in node.items:
            tail = dotted_expr_name(item.context_expr).rsplit(".", maxsplit=1)[-1]
            if tail == "TaskGroup" and isinstance(item.optional_vars, ast.Name):
                names.add(item.optional_vars.id)
    return names


def _is_unstructured_spawn_call(call: ast.Call, tg_vars: set[str]) -> bool:
    callee = dotted_expr_name(call.func)
    is_tg_spawn = (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "create_task"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in tg_vars
    )
    if not is_tg_spawn and (callee in _FIRE_AND_FORGET_CALLS or callee.endswith(".create_task")):
        return True
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "start"
        and dotted_expr_name(call.func.value).rsplit(".", maxsplit=1)[-1] == "Thread"
    )


@jezz_callable
def check_flt_005(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-FLT-005 (structured concurrency & async wall-clock deadlines)."""
    discrepancies: list[RawDiscrepancy] = []
    tg_vars = _task_group_var_names(unit.tree)
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Call) and _is_unstructured_spawn_call(node, tg_vars):
            callee = dotted_expr_name(node.func)
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-005",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message=f"Unstructured background spawn '{callee}()' leaks task lifecycle.",
                    remediation="Spawn concurrent work inside 'async with asyncio.TaskGroup():'.",
                )
            )
    for ctx in unit.callables:
        if (
            isinstance(ctx.node, ast.AsyncFunctionDef)
            and _async_fn_awaits_external_io(ctx.node)
            and not _async_fn_has_deadline_scope(ctx.node)
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-005",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=ctx.node.lineno,
                        column=ctx.node.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=(
                        f"Async I/O coroutine '{ctx.qualname}' lacks an asyncio.timeout() "
                        "wall-clock deadline."
                    ),
                    remediation="Wrap external async I/O in 'async with asyncio.timeout(sec):'.",
                )
            )
    return discrepancies


def _is_unbounded_cache_decorator(dec: ast.expr) -> bool:
    tail = dotted_expr_name(dec).rsplit(".", maxsplit=1)[-1]
    if tail == "cache":
        return True
    if tail != "lru_cache":
        return False
    if not isinstance(dec, ast.Call):
        return False
    if dec.args:
        first_arg = dec.args[0]
        if isinstance(first_arg, ast.Constant) and first_arg.value is None:
            return True
    return any(
        kw.arg == "maxsize" and isinstance(kw.value, ast.Constant) and kw.value.value is None
        for kw in dec.keywords
    )


def _is_unbounded_stream_slurp(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    method = call.func.attr
    if method in _UNBOUNDED_READ_METHODS:
        return True
    return method in {"read", "readlines"} and not call.args and not call.keywords


@jezz_callable
def check_flt_006(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-FLT-006 (bounded caches & capped stream/file/DB reads)."""
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        for dec in ctx.node.decorator_list:
            if _is_unbounded_cache_decorator(dec):
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-FLT-006",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=dec.lineno,
                            column=dec.col_offset,
                            symbol=ctx.qualname,
                        ),
                        message=f"Unbounded memoization cache on '{ctx.qualname}' leaks memory.",
                        remediation="Use @functools.lru_cache(maxsize=N) with a finite integer N.",
                    )
                )
    if unit.is_test_file:
        return discrepancies
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Call) and _is_unbounded_stream_slurp(node):
            method = node.func.attr if isinstance(node.func, ast.Attribute) else "read"
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-FLT-006",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message=f"Unbounded '.{method}()' call risks memory exhaustion (OOM).",
                    remediation="Use read_bounded_text(), read(max_bytes), or fetchmany(limit).",
                )
            )
    return discrepancies
