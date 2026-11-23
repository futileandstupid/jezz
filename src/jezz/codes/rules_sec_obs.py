"""Static AST inspectors for Article III (SEC) and Article V (OBS) Building Codes."""

import ast
import re
from typing import Final

from jezz.codes.ast_utils import (
    CallableContext,
    RawDiscrepancy,
    SourceSpan,
    SourceUnit,
    dotted_expr_name,
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


def _inspect_sec_001_call(unit: SourceUnit, call: ast.Call) -> RawDiscrepancy | None:
    callee = dotted_expr_name(call.func)
    span = SourceSpan(file_path=unit.file_path, line=call.lineno, column=call.col_offset)

    if callee in _BANNED_EXEC_CALLS:
        return RawDiscrepancy(
            code_id="JBC-SEC-001",
            location=span,
            message=f"Prohibited dynamic execution or deserialization sink '{callee}()'.",
            remediation="Use safe typed parsers (tomllib/Pydantic) or arg-list subprocesses.",
        )
    if callee == "yaml.load":
        has_safe = any(
            kw.arg == "Loader"
            and dotted_expr_name(kw.value).rsplit(".", maxsplit=1)[-1] in _SAFE_YAML_LOADERS
            for kw in call.keywords
        )
        if not has_safe:
            return RawDiscrepancy(
                code_id="JBC-SEC-001",
                location=span,
                message="yaml.load() invoked without SafeLoader/CSafeLoader.",
                remediation="Use yaml.safe_load() or pass Loader=yaml.SafeLoader.",
            )
    if callee.startswith("subprocess.") and any(
        kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    ):
        return RawDiscrepancy(
            code_id="JBC-SEC-001",
            location=span,
            message=f"'{callee}()' invoked with shell=True command-injection hazard.",
            remediation="Pass an explicit argument sequence with shell=False.",
        )
    if (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in _SQL_SINK_METHODS
        and call.args
        and _is_dynamic_sql_query(call.args[0])
    ):
        return RawDiscrepancy(
            code_id="JBC-SEC-001",
            location=span,
            message=f"SQL sink '.{call.func.attr}()' receives a dynamically interpolated query.",
            remediation="Use parameterized query placeholders and bound parameter tuples/dicts.",
        )
    return None


@jezz_callable
def check_sec_001(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-001 (injection immunity & eval/deserialization ban)."""
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Call):
            finding = _inspect_sec_001_call(unit, node)
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


def _extract_assigned_str(node: ast.AST) -> tuple[str, str, int, int]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        first_target = node.targets[0]
        if (
            isinstance(first_target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return first_target.id, node.value.value, node.lineno, node.col_offset
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        return node.target.id, node.value.value, node.lineno, node.col_offset
    return "", "", 1, 0


def _check_hardcoded_credentials(unit: SourceUnit) -> list[RawDiscrepancy]:
    enum_node_ids = {
        id(sub)
        for cls_node in unit.classes
        if is_enum_class(cls_node)
        for sub in ast.walk(cls_node)
    }
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if id(node) in enum_node_ids:
            continue
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _CREDENTIAL_LITERAL_RE.search(node.value)
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-002",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message="Source contains a hardcoded credential or private-key literal.",
                    remediation="Load secrets from a vault or injected SecretStr configuration.",
                )
            )
        target_name, assigned_val, a_line, a_col = _extract_assigned_str(node)
        if (
            target_name
            and assigned_val
            and _is_sensitive_identifier(target_name)
            and assigned_val.lower() != target_name.lower()
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-002",
                    location=SourceSpan(file_path=unit.file_path, line=a_line, column=a_col),
                    message=f"Sensitive identifier '{target_name}' assigned a hardcoded literal.",
                    remediation="Inject credentials at runtime rather than embedding literals.",
                )
            )
    return discrepancies


def _sink_args_and_pos(node: ast.AST) -> tuple[list[ast.expr], int, int]:
    if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
        return list(node.exc.args), node.lineno, node.col_offset
    if isinstance(node, ast.Call):
        callee = dotted_expr_name(node.func)
        tail = callee.rsplit(".", maxsplit=1)[-1]
        if callee == "print" or tail in _LOG_METHODS:
            return list(node.args), node.lineno, node.col_offset
    return [], 1, 0


def _check_sensitive_sink_interpolation(unit: SourceUnit) -> list[RawDiscrepancy]:
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        sink_args, line, col = _sink_args_and_pos(node)
        for arg in sink_args:
            leaked = _expr_references_sensitive_var(arg)
            if leaked is not None:
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-SEC-002",
                        location=SourceSpan(file_path=unit.file_path, line=line, column=col),
                        message=f"Sensitive variable '{leaked}' leaked into log/error output.",
                        remediation="Redact sensitive values before logging or raising errors.",
                    )
                )
    return discrepancies


def _field_is_redacted(stmt: ast.AnnAssign) -> bool:
    for sub in ast.walk(stmt.annotation):
        if isinstance(sub, ast.Name) and sub.id in {"SecretStr", "SecretBytes"}:
            return True
    if isinstance(stmt.value, ast.Call) and dotted_expr_name(stmt.value.func).endswith("Field"):
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
                discrepancies.append(
                    RawDiscrepancy(
                        code_id="JBC-SEC-002",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=stmt.lineno,
                            column=stmt.col_offset,
                            symbol=f"{cls_node.name}.{stmt.target.id}",
                        ),
                        message=(
                            f"Sensitive field '{cls_node.name}.{stmt.target.id}' lacks "
                            "SecretStr or Field(repr=False)."
                        ),
                        remediation="Type field as SecretStr or pass repr=False to Field().",
                    )
                )
    return discrepancies


@jezz_callable
def check_sec_002(unit: SourceUnit) -> list[RawDiscrepancy]:
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
        if dotted_expr_name(val).rsplit(".", maxsplit=1)[-1] in _STRICT_CONFIG_PRESETS:
            return True
        if isinstance(val, ast.Call) and _keywords_satisfy_strict_schema(val.keywords):
            return True
    return False


@jezz_callable
def check_sec_003(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-003 (extra='forbid', strict=True, allow_inf_nan=False)."""
    discrepancies: list[RawDiscrepancy] = []
    for cls_node in unit.classes:
        if is_pydantic_model_class(cls_node) and not _model_is_strictly_closed(cls_node):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-003",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=cls_node.lineno,
                        column=cls_node.col_offset,
                        symbol=cls_node.name,
                    ),
                    message=(
                        f"BaseModel '{cls_node.name}' omits extra='forbid', strict=True, "
                        "or allow_inf_nan=False."
                    ),
                    remediation="Set model_config = STRICT_FROZEN_CONFIG or STRICT_MUTABLE_CONFIG.",
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
    root = dotted_expr_name(expr.value).rsplit(".", maxsplit=1)[-1]
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
                RawDiscrepancy(
                    code_id="JBC-SEC-004",
                    location=SourceSpan(
                        file_path=unit.file_path,
                        line=arg.lineno,
                        column=arg.col_offset,
                        symbol=ctx.qualname,
                    ),
                    message=(
                        f"Public parameter '{arg.arg}' in '{ctx.qualname}' accepts an "
                        "untyped dict/Mapping object bag."
                    ),
                    remediation="Accept a closed Pydantic BaseModel ingress schema.",
                )
            )
    if ctx.node.returns is not None and _is_untyped_object_bag(ctx.node.returns):
        discrepancies.append(
            RawDiscrepancy(
                code_id="JBC-SEC-004",
                location=SourceSpan(
                    file_path=unit.file_path,
                    line=ctx.node.returns.lineno,
                    column=ctx.node.returns.col_offset,
                    symbol=ctx.qualname,
                ),
                message=(
                    f"Public callable '{ctx.qualname}' returns an untyped dict/Mapping "
                    "egress bag (OWASP API3:2023)."
                ),
                remediation="Project egress data through a dedicated frozen BaseModel.",
            )
        )
    return discrepancies


@jezz_callable
def check_sec_004(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-004 (typed wire ingress & explicit egress projection)."""
    if unit.is_test_file:
        return []
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Call) and dotted_expr_name(node.func) in _UNTYPED_JSON_CALLS:
            callee = dotted_expr_name(node.func)
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-004",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message=f"Untyped '{callee}()' produces unvalidated dict/list trees.",
                    remediation=(
                        "Use Model.model_validate_json() or TypeAdapter(T).validate_json()."
                    ),
                )
            )
    for ctx in unit.callables:
        if not ctx.node.name.startswith("_"):
            discrepancies.extend(_check_public_bag_signatures(unit, ctx))
    return discrepancies


@jezz_callable
def check_sec_005(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-005 (taint separation & canonicalized path/URL sinks)."""
    if unit.is_test_file:
        return []
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = dotted_expr_name(node.func)
        first_arg = node.args[0]
        span = SourceSpan(file_path=unit.file_path, line=node.lineno, column=node.col_offset)
        if callee in _RAW_PATH_SINKS and isinstance(
            first_arg, ast.Name | ast.Constant | ast.JoinedStr | ast.BinOp
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-005",
                    location=span,
                    message=f"Filesystem sink '{callee}()' invoked with uncontained raw path.",
                    remediation="Anchor path via ContainedPath.from_parts() before sink access.",
                )
            )
        if callee in _RAW_URL_SINKS and isinstance(
            first_arg, ast.Name | ast.Constant | ast.JoinedStr | ast.BinOp
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-005",
                    location=span,
                    message=f"Network sink '{callee}()' invoked with unvalidated raw URL.",
                    remediation="Validate scheme/host via HttpUrl/AnyHttpUrl before request.",
                )
            )
    return discrepancies


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
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
    callee = dotted_expr_name(call.func)
    if not call.args or callee not in _UNANCHORED_PATH_CTORS:
        return None, None
    prefix, consumed_id = _extract_static_arg_prefix(call.args[0])
    if prefix is None:
        return None, None
    if unit.is_test_file and not (
        prefix.startswith(("/", "~")) or _HARDCODED_FS_PATH_RE.search(prefix) is not None
    ):
        return None, None
    span = SourceSpan(file_path=unit.file_path, line=call.lineno, column=call.col_offset)
    return (
        RawDiscrepancy(
            code_id="JBC-SEC-006",
            location=span,
            message=(
                f"Path constructor '{callee}()' invoked with hardcoded literal '{prefix[:64]}' "
                "instead of a dynamic anchor."
            ),
            remediation=(
                "Derive paths from Path(__file__).resolve(), importlib.resources, Path.home(), "
                "Path.cwd(), tempfile, or ContainedPath.from_parts(anchor, rel)."
            ),
        ),
        consumed_id,
    )


@jezz_callable
def check_sec_006(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-SEC-006 (dynamic path anchoring & hardcoded path prohibition)."""
    skip_ids = _docstring_constant_ids(unit.tree)
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Call):
            finding, consumed_id = _inspect_sec_006_call(unit, node)
            if consumed_id is not None:
                skip_ids.add(consumed_id)
            if finding is not None:
                discrepancies.append(finding)
    for node in ast.walk(unit.tree):
        if (
            isinstance(node, ast.Constant)
            and id(node) not in skip_ids
            and isinstance(node.value, str)
            and _is_hardcoded_host_path_literal(node.value)
        ):
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-SEC-006",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message=f"Hardcoded host/OS filesystem path literal '{node.value[:64]}'.",
                    remediation=(
                        "Calculate paths dynamically from Path(__file__).resolve(), "
                        "importlib.resources, tmp_path/tempfile, or ContainedPath.from_parts()."
                    ),
                )
            )
    return discrepancies


@jezz_callable
def check_obs_001(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-OBS-001 (structured telemetry & debug trap ban)."""
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if not isinstance(node, ast.Call):
            continue
        callee = dotted_expr_name(node.func)
        if callee in _DEBUG_TRAP_CALLS:
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-OBS-001",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message=f"Interactive debugger trap '{callee}()' left in source.",
                    remediation="Remove debugger trap before certification.",
                )
            )
        if not unit.is_test_file and callee == "print":
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-OBS-001",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message="Unstructured print() call in production module.",
                    remediation="Emit structured log records via logging or typed return models.",
                )
            )
    return discrepancies


def _is_mutable_default_expr(expr: ast.expr | None) -> bool:
    if expr is None:
        return False
    if isinstance(expr, ast.List | ast.Dict | ast.Set):
        return True
    return isinstance(expr, ast.Call) and dotted_expr_name(expr.func) in _MUTABLE_DEFAULT_CALLS


@jezz_callable
def check_obs_002(unit: SourceUnit) -> list[RawDiscrepancy]:
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
                    RawDiscrepancy(
                        code_id="JBC-OBS-002",
                        location=SourceSpan(
                            file_path=unit.file_path,
                            line=default_expr.lineno,
                            column=default_expr.col_offset,
                            symbol=ctx.qualname,
                        ),
                        message=f"Callable '{ctx.qualname}' uses a mutable default argument.",
                        remediation="Default to None or an immutable tuple/frozenset.",
                    )
                )
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Global):
            names_preview = ", ".join(node.names[:8])
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-OBS-002",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message=f"'global {names_preview}' mutates module-level state.",
                    remediation="Thread explicit state parameters or return immutable values.",
                )
            )
    return discrepancies


def _is_os_environ_mutation(node: ast.AST) -> bool:
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del):
        return dotted_expr_name(node.value) in {"os.environ", "environ"}
    if isinstance(node, ast.Call):
        callee = dotted_expr_name(node.func)
        if callee == "os.putenv":
            return True
        if callee.startswith(("os.environ.", "environ.")):
            method = callee.rsplit(".", maxsplit=1)[-1]
            return method in {"pop", "popitem", "clear", "update", "setdefault"}
    return False


@jezz_callable
def check_obs_003(unit: SourceUnit) -> list[RawDiscrepancy]:
    """Inspect ``unit`` for JBC-OBS-003 (controlled nondeterminism & env isolation)."""
    discrepancies: list[RawDiscrepancy] = []
    for node in ast.walk(unit.tree):
        if isinstance(node, ast.Call) and dotted_expr_name(node.func) in _GLOBAL_PRNG_CALLS:
            callee = dotted_expr_name(node.func)
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-OBS-003",
                    location=SourceSpan(
                        file_path=unit.file_path, line=node.lineno, column=node.col_offset
                    ),
                    message=f"Unseeded global PRNG call '{callee}()' compromises determinism.",
                    remediation="Inject an explicit random.Random(seed) instance or use secrets.",
                )
            )
        if not unit.is_test_file and _is_os_environ_mutation(node):
            line = getattr(node, "lineno", 1)
            col = getattr(node, "col_offset", 0)
            discrepancies.append(
                RawDiscrepancy(
                    code_id="JBC-OBS-003",
                    location=SourceSpan(file_path=unit.file_path, line=line, column=col),
                    message="Direct mutation of process-global os.environ in production code.",
                    remediation=(
                        "Pass explicit configuration objects instead of mutating os.environ."
                    ),
                )
            )
    return discrepancies
