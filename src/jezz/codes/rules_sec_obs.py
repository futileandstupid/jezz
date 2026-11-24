"""Static AST inspectors for Article III (SEC) and Article V (OBS) Building Codes."""

import ast
import re
from collections.abc import Sequence
from typing import Final

from jezz.codes.ast_utils import (
    CallableContext,
    GuardedSourceUnit,
    RawDiscrepancy,
    SourceSpan,
    SourceUnit,
    is_enum_class,
    is_pydantic_model_class,
)
from jezz.guards import jezz_callable

_BANNED_EXEC_CALLS: Final[frozenset[str]] = frozenset(
    {
        "eval",
        "exec",
        "pickle.load",
        "pickle.loads",
        "marshal.load",
        "marshal.loads",
        "os.system",
        "os.popen",
    }
)
_SQL_SINK_METHODS: Final[frozenset[str]] = frozenset(
    {"execute", "executemany", "executescript", "raw"}
)
_SAFE_YAML_LOADERS: Final[frozenset[str]] = frozenset({"SafeLoader", "CSafeLoader"})
_SHELL_BINARIES: Final[frozenset[str]] = frozenset(
    {"sh", "/bin/sh", "bash", "/bin/bash", "zsh", "/bin/zsh"}
)
_SAFE_ANCHOR_TYPES: Final[frozenset[str]] = frozenset(
    {"Path", "PurePath", "PurePosixPath", "ContainedPath", "HttpUrl", "AnyHttpUrl"}
)
_PATH_METHOD_SINKS: Final[frozenset[str]] = frozenset(
    {"open", "read_text", "read_bytes", "write_text", "write_bytes", "unlink", "rmdir"}
)

_SENSITIVE_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|_)(?:password|passwd|secret|api_key|apikey|access_token|"
    r"auth_token|private_key|client_secret|bearer_token|ssn)(?:$|_)",
    re.IGNORECASE,
)
_CREDENTIAL_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
)
_LOG_METHODS: Final[frozenset[str]] = frozenset(
    {"debug", "info", "warning", "warn", "error", "critical", "exception"}
)
_STRICT_CONFIG_PRESETS: Final[frozenset[str]] = frozenset(
    {"STRICT_FROZEN_CONFIG", "STRICT_MUTABLE_CONFIG"}
)
_UNTYPED_JSON_CALLS: Final[frozenset[str]] = frozenset({"json.loads", "json.load"})
_RAW_PATH_SINKS: Final[frozenset[str]] = frozenset(
    {"open", "os.remove", "os.unlink", "shutil.rmtree"}
)
_RAW_URL_SINKS: Final[frozenset[str]] = frozenset(
    {"urllib.request.urlopen", "requests.get", "requests.post", "httpx.get", "httpx.post"}
)
_UNANCHORED_PATH_CTORS: Final[frozenset[str]] = frozenset(
    {
        "Path",
        "pathlib.Path",
        "PosixPath",
        "pathlib.PosixPath",
        "WindowsPath",
        "pathlib.WindowsPath",
        "os.path.abspath",
        "os.path.realpath",
        "os.path.expanduser",
    }
)
_HARDCODED_FS_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r"/(?:Users|home|root|tmp|var|etc|opt|private|Volumes|mnt|srv|workspace)(?:/|$)|"
    r"~/|"
    r"[A-Za-z]:[\\/]"
    r")"
)
_DEBUG_TRAP_CALLS: Final[frozenset[str]] = frozenset(
    {"breakpoint", "pdb.set_trace", "ipdb.set_trace", "bdb.set_trace"}
)
_MUTABLE_DEFAULT_CALLS: Final[frozenset[str]] = frozenset(
    {"list", "dict", "set", "bytearray", "deque", "collections.deque"}
)
_GLOBAL_PRNG_CALLS: Final[frozenset[str]] = frozenset(
    {
        "random.random",
        "random.randint",
        "random.randrange",
        "random.choice",
        "random.choices",
        "random.shuffle",
        "random.sample",
        "random.uniform",
        "random.gauss",
    }
)


def _node_hit(
    code_id: str,
    unit: SourceUnit,
    node: ast.AST,
    message: str,
    remediation: str,
    *,
    symbol: str = "",
) -> RawDiscrepancy:
    return RawDiscrepancy(
        code_id=code_id,
        location=SourceSpan(
            file_path=unit.file_path,
            line=getattr(node, "lineno", 1),
            column=getattr(node, "col_offset", 0),
            symbol=symbol,
        ),
        message=message,
        remediation=remediation,
    )


def _dotted_expr(expr: ast.expr, *, remaining_depth: int = 32) -> str:
    if remaining_depth <= 0:
        return ""
    target = expr.value if isinstance(expr, ast.Subscript) else expr
    if isinstance(target, ast.Call):
        target = target.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        prefix = _dotted_expr(target.value, remaining_depth=remaining_depth - 1)
        return f"{prefix}.{target.attr}" if prefix else target.attr
    return ""


def _is_dynamic_sql_query(expr: ast.expr) -> bool:
    if isinstance(expr, ast.JoinedStr):
        return True
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add | ast.Mod):
        return True
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "format"
    )


def _extract_assign_pair(stmt: ast.AST) -> tuple[str, ast.expr | None]:
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        return stmt.targets[0].id, stmt.value
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return stmt.target.id, stmt.value
    return "", None


def _step_sec_001_pair(
    lhs: str,
    rhs: ast.expr,
    sql_vars: set[str],
    true_vars: set[str],
) -> None:
    if _is_dynamic_sql_query(rhs) or (isinstance(rhs, ast.Name) and rhs.id in sql_vars):
        sql_vars.add(lhs)
    if (isinstance(rhs, ast.Constant) and rhs.value is True) or (
        isinstance(rhs, ast.Name) and rhs.id in true_vars
    ):
        true_vars.add(lhs)


def _collect_sec_001_aliases(nodes: Sequence[ast.AST]) -> tuple[frozenset[str], frozenset[str]]:
    sql_vars: set[str] = set()
    true_vars: set[str] = set()
    pairs = [
        (lhs, rhs)
        for n in nodes
        if (rhs := (p := _extract_assign_pair(n))[1]) is not None and (lhs := p[0])
    ]
    for _ in range(4):
        for lhs, rhs in pairs:
            _step_sec_001_pair(lhs, rhs, sql_vars, true_vars)
    return frozenset(sql_vars), frozenset(true_vars)


def _is_subprocess_shell_hazard(
    call: ast.Call,
    true_vars: frozenset[str],
    is_test: bool,
) -> bool:
    for kw in call.keywords:
        if kw.arg == "shell" and (
            (isinstance(kw.value, ast.Constant) and kw.value.value is True)
            or (isinstance(kw.value, ast.Name) and kw.value.id in true_vars)
        ):
            return True
    if is_test or not call.args or not isinstance(call.args[0], ast.List | ast.Tuple):
        return False
    elts = call.args[0].elts
    if len(elts) < 3:
        return False
    bin_ok = isinstance(elts[0], ast.Constant) and elts[0].value in _SHELL_BINARIES
    flag_ok = isinstance(elts[1], ast.Constant) and elts[1].value == "-c"
    return bin_ok and flag_ok and not isinstance(elts[2], ast.Constant)


def _inspect_sec_001_call(
    unit: SourceUnit,
    call: ast.Call,
    sql_vars: frozenset[str],
    true_vars: frozenset[str],
) -> RawDiscrepancy | None:
    callee = _dotted_expr(call.func)
    if callee in _BANNED_EXEC_CALLS:
        return _node_hit(
            "JBC-SEC-001",
            unit,
            call,
            f"Prohibited dynamic execution or deserialization sink '{callee}()'.",
            "Use safe typed parsers (tomllib/Pydantic) or arg-list subprocesses.",
        )
    if callee == "yaml.load":
        has_safe = any(
            kw.arg == "Loader"
            and _dotted_expr(kw.value).rsplit(".", maxsplit=1)[-1] in _SAFE_YAML_LOADERS
            for kw in call.keywords
        )
        if not has_safe:
            return _node_hit(
                "JBC-SEC-001",
                unit,
                call,
                "yaml.load() invoked without SafeLoader/CSafeLoader.",
                "Use yaml.safe_load() or pass Loader=yaml.SafeLoader.",
            )
    if callee.startswith("subprocess.") and _is_subprocess_shell_hazard(
        call, true_vars, unit.is_test_file
    ):
        return _node_hit(
            "JBC-SEC-001",
            unit,
            call,
            f"'{callee}()' invoked with shell=True or '-c' command-injection hazard.",
            "Pass an explicit argument sequence with shell=False.",
        )
    if (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in _SQL_SINK_METHODS
        and call.args
        and (
            _is_dynamic_sql_query(call.args[0])
            or (isinstance(call.args[0], ast.Name) and call.args[0].id in sql_vars)
        )
    ):
        return _node_hit(
            "JBC-SEC-001",
            unit,
            call,
            f"SQL sink '.{call.func.attr}()' receives a dynamically interpolated query.",
            "Use parameterized query placeholders and bound parameter tuples/dicts.",
        )
    return None


@jezz_callable
def check_sec_001(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-001 (injection immunity & eval/deserialization ban)."""
    sql_vars, true_vars = _collect_sec_001_aliases(unit.all_nodes)
    discrepancies: list[RawDiscrepancy] = []
    for node in unit.calls:
        finding = _inspect_sec_001_call(unit, node, sql_vars, true_vars)
        if finding is not None:
            discrepancies.append(finding)
    return discrepancies


_NON_SECRET_NAME_PREFIXES: Final[tuple[str, ...]] = (
    "method_",
    "rpc_",
    "route_",
    "event_",
    "metric_",
)
_NON_SECRET_NAME_SUFFIXES: Final[tuple[str, ...]] = (
    "_env",
    "_env_var",
    "_var",
    "_header",
    "_key_name",
    "_field_name",
    "_param_name",
    "_id",
    "_ids",
    "_prefix",
    "_get",
    "_store",
    "_delete",
    "_list",
    "_count",
    "_type",
    "_kind",
    "_status",
    "_mode",
    "_path",
)


def _is_sensitive_identifier(name: str) -> bool:
    low = name.lower().lstrip("_")
    if low.startswith(_NON_SECRET_NAME_PREFIXES) or low.endswith(_NON_SECRET_NAME_SUFFIXES):
        return False
    return _SENSITIVE_NAME_RE.search(name) is not None


def _expr_references_sensitive_var(expr: ast.expr) -> str | None:
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Name) and _is_sensitive_identifier(sub.id):
            return sub.id
        if isinstance(sub, ast.Attribute) and _is_sensitive_identifier(sub.attr):
            return sub.attr
    return None


def _extract_assigned_str(node: ast.AST) -> tuple[str, str]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        first_target = node.targets[0]
        if (
            isinstance(first_target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return first_target.id, node.value.value
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        return node.target.id, node.value.value
    return "", ""


def _check_hardcoded_credentials(unit: SourceUnit) -> list[RawDiscrepancy]:
    enum_node_ids = {
        id(sub)
        for cls_node in unit.classes
        if is_enum_class(cls_node)
        for sub in ast.walk(cls_node)
    }
    discrepancies: list[RawDiscrepancy] = []
    for node in unit.all_nodes:
        if id(node) in enum_node_ids:
            continue
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _CREDENTIAL_LITERAL_RE.search(node.value)
        ):
            discrepancies.append(
                _node_hit(
                    "JBC-SEC-002",
                    unit,
                    node,
                    "Source contains a hardcoded credential or private-key literal.",
                    "Load secrets from a vault or injected SecretStr configuration.",
                )
            )
        target_name, assigned_val = _extract_assigned_str(node)
        if (
            target_name
            and assigned_val
            and _is_sensitive_identifier(target_name)
            and assigned_val.lower() != target_name.lower()
        ):
            discrepancies.append(
                _node_hit(
                    "JBC-SEC-002",
                    unit,
                    node,
                    f"Sensitive identifier '{target_name}' assigned a hardcoded literal.",
                    "Inject credentials at runtime rather than embedding literals.",
                )
            )
    return discrepancies


def _sink_args_for_node(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
        return list(node.exc.args)
    if isinstance(node, ast.Call):
        callee = _dotted_expr(node.func)
        tail = callee.rsplit(".", maxsplit=1)[-1]
        if callee == "print" or tail in _LOG_METHODS:
            return list(node.args)
    return []


def _check_sensitive_sink_interpolation(unit: SourceUnit) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    for node in unit.all_nodes:
        for arg in _sink_args_for_node(node):
            leaked = _expr_references_sensitive_var(arg)
            if leaked is not None:
                discrepancies.append(
                    _node_hit(
                        "JBC-SEC-002",
                        unit,
                        node,
                        f"Sensitive variable '{leaked}' leaked into log/error output.",
                        "Redact sensitive values before logging or raising errors.",
                    )
                )
    return discrepancies


def _field_is_redacted(stmt: ast.AnnAssign) -> bool:
    for sub in ast.walk(stmt.annotation):
        if isinstance(sub, ast.Name) and sub.id in {"SecretStr", "SecretBytes"}:
            return True
    if isinstance(stmt.value, ast.Call) and _dotted_expr(stmt.value.func).endswith("Field"):
        return any(
            kw.arg == "repr" and isinstance(kw.value, ast.Constant) and kw.value.value is False
            for kw in stmt.value.keywords
        )
    return False


def _check_pydantic_sensitive_fields(unit: SourceUnit) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    for cls_node in unit.classes:
        if not is_pydantic_model_class(cls_node):
            continue
        for stmt in cls_node.body:
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and _is_sensitive_identifier(stmt.target.id)
                and not _field_is_redacted(stmt)
            ):
                sym = f"{cls_node.name}.{stmt.target.id}"
                discrepancies.append(
                    _node_hit(
                        "JBC-SEC-002",
                        unit,
                        stmt,
                        f"Sensitive field '{sym}' lacks SecretStr or Field(repr=False).",
                        "Type field as SecretStr or pass repr=False to Field().",
                        symbol=sym,
                    )
                )
    return discrepancies


@jezz_callable
def check_sec_002(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-002 (secret containment & redaction)."""
    return [
        *_check_hardcoded_credentials(unit),
        *_check_sensitive_sink_interpolation(unit),
        *_check_pydantic_sensitive_fields(unit),
    ]


def _extract_model_config_value(stmt: ast.stmt) -> ast.expr | None:
    if isinstance(stmt, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
    ):
        return stmt.value
    if (
        isinstance(stmt, ast.AnnAssign)
        and isinstance(stmt.target, ast.Name)
        and stmt.target.id == "model_config"
    ):
        return stmt.value
    return None


def _keywords_satisfy_strict_schema(keywords: list[ast.keyword]) -> bool:
    kw_map: dict[str, object] = {}
    for kw in keywords:
        val_node = kw.value
        if kw.arg is not None and isinstance(val_node, ast.Constant):
            kw_map[kw.arg] = val_node.value
    return (
        kw_map.get("extra") == "forbid"
        and kw_map.get("strict") is True
        and kw_map.get("allow_inf_nan") is False
    )


def _model_is_strictly_closed(cls_node: ast.ClassDef) -> bool:
    if _keywords_satisfy_strict_schema(cls_node.keywords):
        return True
    for stmt in cls_node.body:
        val = _extract_model_config_value(stmt)
        if val is None:
            continue
        if _dotted_expr(val).rsplit(".", maxsplit=1)[-1] in _STRICT_CONFIG_PRESETS:
            return True
        if isinstance(val, ast.Call) and _keywords_satisfy_strict_schema(val.keywords):
            return True
    return False


@jezz_callable
def check_sec_003(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-003 (extra='forbid', strict=True, allow_inf_nan=False)."""
    discrepancies: list[RawDiscrepancy] = []
    for cls_node in unit.classes:
        if is_pydantic_model_class(cls_node) and not _model_is_strictly_closed(cls_node):
            discrepancies.append(
                _node_hit(
                    "JBC-SEC-003",
                    unit,
                    cls_node,
                    (
                        f"BaseModel '{cls_node.name}' omits extra='forbid', strict=True, "
                        "or allow_inf_nan=False."
                    ),
                    "Set model_config = STRICT_FROZEN_CONFIG or STRICT_MUTABLE_CONFIG.",
                    symbol=cls_node.name,
                )
            )
    return discrepancies


def _is_untyped_object_bag(expr: ast.expr, *, remaining_depth: int = 16) -> bool:
    """Return True iff ``expr`` is bare dict/Mapping or dict[str, object]/Mapping[str, object]."""
    if remaining_depth <= 0:
        return False
    if isinstance(expr, ast.Name) and expr.id in {"dict", "Mapping"}:
        return True
    if not isinstance(expr, ast.Subscript):
        return False
    root = _dotted_expr(expr.value).rsplit(".", maxsplit=1)[-1]
    if root == "Annotated" and isinstance(expr.slice, ast.Tuple) and expr.slice.elts:
        return _is_untyped_object_bag(expr.slice.elts[0], remaining_depth=remaining_depth - 1)
    if root not in {"dict", "Mapping"}:
        return False
    if isinstance(expr.slice, ast.Tuple) and len(expr.slice.elts) == 2:
        val_type = expr.slice.elts[1]
        return isinstance(val_type, ast.Name) and val_type.id in {"object", "Any"}
    return False


def _check_public_bag_signatures(unit: SourceUnit, ctx: CallableContext) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    args = [*ctx.node.args.posonlyargs, *ctx.node.args.args, *ctx.node.args.kwonlyargs]
    for arg in args:
        if arg.annotation is not None and _is_untyped_object_bag(arg.annotation):
            discrepancies.append(
                _node_hit(
                    "JBC-SEC-004",
                    unit,
                    arg,
                    (
                        f"Public parameter '{arg.arg}' in '{ctx.qualname}' accepts an "
                        "untyped dict/Mapping object bag."
                    ),
                    "Accept a closed Pydantic BaseModel ingress schema.",
                    symbol=ctx.qualname,
                )
            )
    if ctx.node.returns is not None and _is_untyped_object_bag(ctx.node.returns):
        discrepancies.append(
            _node_hit(
                "JBC-SEC-004",
                unit,
                ctx.node.returns,
                (
                    f"Public callable '{ctx.qualname}' returns an untyped dict/Mapping "
                    "egress bag (OWASP API3:2023)."
                ),
                "Project egress data through a dedicated frozen BaseModel.",
                symbol=ctx.qualname,
            )
        )
    return discrepancies


@jezz_callable
def check_sec_004(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-004 (typed wire ingress & explicit egress projection)."""
    if unit.is_test_file:
        return []
    discrepancies: list[RawDiscrepancy] = []
    for node in unit.calls:
        callee = _dotted_expr(node.func)
        if callee in _UNTYPED_JSON_CALLS:
            discrepancies.append(
                _node_hit(
                    "JBC-SEC-004",
                    unit,
                    node,
                    f"Untyped '{callee}()' produces unvalidated dict/list trees.",
                    "Use Model.model_validate_json() or TypeAdapter(T).validate_json().",
                )
            )
    for ctx in unit.callables:
        if not ctx.node.name.startswith("_"):
            discrepancies.extend(_check_public_bag_signatures(unit, ctx))
    return discrepancies


def _names_in_expr(expr: ast.expr) -> set[str]:
    return {sub.id for sub in ast.walk(expr) if isinstance(sub, ast.Name)}


def _is_sanitizer_call(expr: ast.expr) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    callee = _dotted_expr(expr.func)
    return callee.endswith(("ContainedPath.from_parts", "HttpUrl", "AnyHttpUrl"))


def _is_path_wrap_expr(expr: ast.expr) -> bool:
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return True
    if not isinstance(expr, ast.Call):
        return False
    callee = _dotted_expr(expr.func)
    return callee in _UNANCHORED_PATH_CTORS or callee.endswith((".joinpath", ".resolve", "join"))


def _seed_untrusted_param_names(ctx: CallableContext) -> set[str]:
    args = [*ctx.node.args.posonlyargs, *ctx.node.args.args, *ctx.node.args.kwonlyargs]
    tainted: set[str] = set()
    for arg in args:
        if arg.arg in {"self", "cls"}:
            continue
        ann_names = _names_in_expr(arg.annotation) if arg.annotation is not None else set()
        if not (ann_names & _SAFE_ANCHOR_TYPES):
            tainted.add(arg.arg)
    return tainted


def _has_resolved_path_attr(expr: ast.expr) -> bool:
    return any(isinstance(s, ast.Attribute) and s.attr == "resolved_path" for s in ast.walk(expr))


def _step_path_taint_pair(
    lhs: str,
    rhs: ast.expr,
    tainted_strs: set[str],
    uncontained_paths: set[str],
) -> None:
    if _is_sanitizer_call(rhs) or _has_resolved_path_attr(rhs):
        return
    refs = _names_in_expr(rhs)
    if _is_path_wrap_expr(rhs) and (refs & (tainted_strs | uncontained_paths)):
        uncontained_paths.add(lhs)
    elif refs & tainted_strs:
        tainted_strs.add(lhs)


def _collect_callable_path_taint(ctx: CallableContext) -> tuple[frozenset[str], frozenset[str]]:
    tainted_strs = _seed_untrusted_param_names(ctx)
    uncontained_paths: set[str] = set()
    assigns = [
        (lhs, rhs)
        for n in ctx.local_nodes
        if (rhs := (p := _extract_assign_pair(n))[1]) is not None and (lhs := p[0])
    ]
    for _ in range(4):
        for lhs, rhs in assigns:
            _step_path_taint_pair(lhs, rhs, tainted_strs, uncontained_paths)
    return frozenset(tainted_strs), frozenset(uncontained_paths)


def _body_checks_containment(ctx: CallableContext) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "is_relative_to"
        for n in ctx.local_nodes
    )


def _check_callable_path_method_sink(
    unit: SourceUnit,
    ctx: CallableContext,
    call: ast.Call,
    tainted_strs: frozenset[str],
    uncontained_paths: frozenset[str],
) -> RawDiscrepancy | None:
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in _PATH_METHOD_SINKS:
        return None
    recv = call.func.value
    if isinstance(recv, ast.Attribute) and recv.attr == "resolved_path":
        return None
    is_tainted_recv = (isinstance(recv, ast.Name) and recv.id in uncontained_paths) or (
        _is_path_wrap_expr(recv) and bool(_names_in_expr(recv) & (tainted_strs | uncontained_paths))
    )
    if not is_tainted_recv:
        return None
    return _node_hit(
        "JBC-SEC-005",
        unit,
        call,
        (
            f"Filesystem sink '.{call.func.attr}()' invoked on uncontained Path derived from "
            "untrusted parameter."
        ),
        "Confine relative path via ContainedPath.from_parts(root, rel) before I/O.",
        symbol=ctx.qualname,
    )


def _check_callable_taint_aliases(unit: SourceUnit, ctx: CallableContext) -> list[RawDiscrepancy]:
    if ctx.qualname == "ContainedPath.from_parts" or _body_checks_containment(ctx):
        return []
    tainted_strs, uncontained_paths = _collect_callable_path_taint(ctx)
    if not tainted_strs and not uncontained_paths:
        return []
    out: list[RawDiscrepancy] = []
    for node in ctx.local_nodes:
        if not isinstance(node, ast.Call):
            continue
        method_hit = _check_callable_path_method_sink(
            unit, ctx, node, tainted_strs, uncontained_paths
        )
        if method_hit is not None:
            out.append(method_hit)
    return out


def _inspect_sec_005_raw_call(unit: SourceUnit, node: ast.Call) -> list[RawDiscrepancy]:
    if not node.args:
        return []
    callee = _dotted_expr(node.func)
    if callee not in _RAW_PATH_SINKS and callee not in _RAW_URL_SINKS:
        return []
    first_arg = node.args[0]
    is_raw_expr = isinstance(first_arg, ast.Name | ast.Constant | ast.JoinedStr | ast.BinOp)
    out: list[RawDiscrepancy] = []
    if callee in _RAW_PATH_SINKS and (
        is_raw_expr or (not _has_resolved_path_attr(first_arg) and bool(_names_in_expr(first_arg)))
    ):
        out.append(
            _node_hit(
                "JBC-SEC-005",
                unit,
                node,
                f"Filesystem sink '{callee}()' invoked with uncontained raw path.",
                "Anchor path via ContainedPath.from_parts() before sink access.",
            )
        )
    if callee in _RAW_URL_SINKS and is_raw_expr:
        out.append(
            _node_hit(
                "JBC-SEC-005",
                unit,
                node,
                f"Network sink '{callee}()' invoked with unvalidated raw URL.",
                "Validate scheme/host via HttpUrl/AnyHttpUrl before request.",
            )
        )
    return out


@jezz_callable
def check_sec_005(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-005 (taint separation & canonicalized path/URL sinks)."""
    if unit.is_test_file:
        return []
    discrepancies: list[RawDiscrepancy] = []
    for node in unit.calls:
        discrepancies.extend(_inspect_sec_005_raw_call(unit, node))
    for ctx in unit.callables:
        discrepancies.extend(_check_callable_taint_aliases(unit, ctx))
    return discrepancies


def _docstring_constant_ids(nodes: Sequence[ast.AST]) -> set[int]:
    doc_ids: set[int] = set()
    for node in nodes:
        if (
            isinstance(
                node,
                ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
            )
            and node.body
        ):
            first_stmt = node.body[0]
            if (
                isinstance(first_stmt, ast.Expr)
                and isinstance(first_stmt.value, ast.Constant)
                and isinstance(first_stmt.value.value, str)
            ):
                doc_ids.add(id(first_stmt.value))
    return doc_ids


def _extract_static_arg_prefix(expr: ast.expr) -> tuple[str | None, int | None]:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value, id(expr)
    if isinstance(expr, ast.JoinedStr) and expr.values:
        head = expr.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value, id(head)
    return None, None


def _is_hardcoded_host_path_literal(val: str) -> bool:
    return "\n" not in val and "://" not in val and _HARDCODED_FS_PATH_RE.search(val) is not None


def _inspect_sec_006_call(
    unit: SourceUnit,
    call: ast.Call,
) -> tuple[RawDiscrepancy | None, int | None]:
    callee = _dotted_expr(call.func)
    if not call.args or callee not in _UNANCHORED_PATH_CTORS:
        return None, None
    prefix, consumed_id = _extract_static_arg_prefix(call.args[0])
    if prefix is None:
        return None, None
    if unit.is_test_file and not (
        prefix.startswith(("/", "~")) or _HARDCODED_FS_PATH_RE.search(prefix) is not None
    ):
        return None, None
    return (
        _node_hit(
            "JBC-SEC-006",
            unit,
            call,
            (
                f"Path constructor '{callee}()' invoked with hardcoded literal '{prefix[:64]}' "
                "instead of a dynamic anchor."
            ),
            (
                "Derive paths from Path(__file__).resolve(), importlib.resources, Path.home(), "
                "Path.cwd(), tempfile, or ContainedPath.from_parts(anchor, rel)."
            ),
        ),
        consumed_id,
    )


@jezz_callable
def check_sec_006(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-006 (dynamic path anchoring & hardcoded path prohibition)."""
    skip_ids = _docstring_constant_ids(unit.all_nodes)
    discrepancies: list[RawDiscrepancy] = []
    for call_node in unit.calls:
        finding, consumed_id = _inspect_sec_006_call(unit, call_node)
        if consumed_id is not None:
            skip_ids.add(consumed_id)
        if finding is not None:
            discrepancies.append(finding)
    for node in unit.all_nodes:
        if (
            isinstance(node, ast.Constant)
            and id(node) not in skip_ids
            and isinstance(node.value, str)
            and _is_hardcoded_host_path_literal(node.value)
        ):
            discrepancies.append(
                _node_hit(
                    "JBC-SEC-006",
                    unit,
                    node,
                    f"Hardcoded host/OS filesystem path literal '{node.value[:64]}'.",
                    (
                        "Calculate paths dynamically from Path(__file__).resolve(), "
                        "importlib.resources, tmp_path/tempfile, or ContainedPath.from_parts()."
                    ),
                )
            )
    return discrepancies


@jezz_callable
def check_obs_001(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-OBS-001 (structured telemetry & debug trap ban)."""
    discrepancies: list[RawDiscrepancy] = []
    for node in unit.calls:
        callee = _dotted_expr(node.func)
        if callee in _DEBUG_TRAP_CALLS:
            discrepancies.append(
                _node_hit(
                    "JBC-OBS-001",
                    unit,
                    node,
                    f"Interactive debugger trap '{callee}()' left in source.",
                    "Remove debugger trap before certification.",
                )
            )
        if not unit.is_test_file and callee == "print":
            discrepancies.append(
                _node_hit(
                    "JBC-OBS-001",
                    unit,
                    node,
                    "Unstructured print() call in production module.",
                    "Emit structured log records via logging or typed return models.",
                )
            )
    return discrepancies


def _is_mutable_default_expr(expr: ast.expr | None) -> bool:
    if expr is None:
        return False
    if isinstance(expr, ast.List | ast.Dict | ast.Set):
        return True
    return isinstance(expr, ast.Call) and _dotted_expr(expr.func) in _MUTABLE_DEFAULT_CALLS


@jezz_callable
def check_obs_002(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-OBS-002 (no mutable default args & no global statement)."""
    discrepancies: list[RawDiscrepancy] = []
    for ctx in unit.callables:
        defaults: list[ast.expr | None] = [
            *ctx.node.args.defaults,
            *ctx.node.args.kw_defaults,
        ]
        for default_expr in defaults:
            if default_expr is not None and _is_mutable_default_expr(default_expr):
                discrepancies.append(
                    _node_hit(
                        "JBC-OBS-002",
                        unit,
                        default_expr,
                        f"Callable '{ctx.qualname}' uses a mutable default argument.",
                        "Default to None or an immutable tuple/frozenset.",
                        symbol=ctx.qualname,
                    )
                )
    for node in unit.all_nodes:
        if isinstance(node, ast.Global):
            names_preview = ", ".join(node.names[:8])
            discrepancies.append(
                _node_hit(
                    "JBC-OBS-002",
                    unit,
                    node,
                    f"'global {names_preview}' mutates module-level state.",
                    "Thread explicit state parameters or return immutable values.",
                )
            )
    return discrepancies


def _is_os_environ_mutation(node: ast.AST) -> bool:
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del):
        return _dotted_expr(node.value) in {"os.environ", "environ"}
    if isinstance(node, ast.Call):
        callee = _dotted_expr(node.func)
        if callee == "os.putenv":
            return True
        if callee.startswith(("os.environ.", "environ.")):
            method = callee.rsplit(".", maxsplit=1)[-1]
            return method in {"pop", "popitem", "clear", "update", "setdefault"}
    return False


@jezz_callable
def check_obs_003(unit: GuardedSourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-OBS-003 (controlled nondeterminism & env isolation)."""
    discrepancies: list[RawDiscrepancy] = []
    for node in unit.all_nodes:
        if isinstance(node, ast.Call):
            callee = _dotted_expr(node.func)
            if callee in _GLOBAL_PRNG_CALLS:
                discrepancies.append(
                    _node_hit(
                        "JBC-OBS-003",
                        unit,
                        node,
                        f"Unseeded global PRNG call '{callee}()' compromises determinism.",
                        "Inject an explicit random.Random(seed) instance or use secrets.",
                    )
                )
        if not unit.is_test_file and _is_os_environ_mutation(node):
            discrepancies.append(
                _node_hit(
                    "JBC-OBS-003",
                    unit,
                    node,
                    "Direct mutation of process-global os.environ in production code.",
                    "Pass explicit configuration objects instead of mutating os.environ.",
                )
            )
    return discrepancies
