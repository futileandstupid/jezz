"""Package-fingerprint & fresh-interpreter hot-reload supervisor for long-lived MCP daemons."""

import hashlib
import importlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal

import icontract
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    BoundedText,
    ContainedPath,
    FiniteFloat,
    IdentifierStr,
    InstanceGuard,
    LoopBudget,
    NonNegativeInt,
    ShortText,
    SourceCodeText,
    jezz_callable,
    read_bounded_text,
)


class ReloadWorkerRequest(BaseModel):
    """Typed IPC request sent over stdin to a fresh-interpreter reload worker."""

    model_config = STRICT_FROZEN_CONFIG

    package_parent: ShortText
    workspace_root: ShortText
    worker_module: IdentifierStr
    worker_entrypoint: IdentifierStr
    tool_name: IdentifierStr
    arguments_json: SourceCodeText


class ReloadWorkerResponse(BaseModel):
    """Typed IPC response emitted on stdout by a fresh-interpreter reload worker."""

    model_config = STRICT_FROZEN_CONFIG

    status: Literal["ok", "tool_error", "import_failed"]
    payload_json: SourceCodeText = ""
    error: BoundedText = ""


@dataclass(frozen=True)
class _SupervisorSpec:
    """Immutable configuration binding workspace, watched package root, and worker target."""

    workspace_root: Path
    package_root: Path
    worker_module: str
    worker_entrypoint: str
    timeout_sec: float
    startup_fp: str


@dataclass
class _SnapshotState:
    """Mutable last-known-healthy source snapshot and reload generation ledger."""

    healthy_fp: str
    healthy_sources: dict[str, str]
    reload_generation: int
    last_reload_error: str


def _iter_package_py_files(resolved_pkg: Path) -> list[tuple[str, Path]]:
    budget = LoopBudget(max_steps=50_000)
    discovered: list[tuple[str, Path]] = []
    for py_path in sorted(resolved_pkg.rglob("*.py")):
        budget.tick()
        if "__pycache__" in py_path.parts or not py_path.is_file():
            continue
        rel = py_path.relative_to(resolved_pkg).as_posix()
        discovered.append((rel, py_path))
    return discovered


@icontract.ensure(lambda result: len(result) == 64)
@jezz_callable
def compute_package_fingerprint(
    package_root: Path,
) -> Annotated[str, Field(min_length=64, max_length=64)]:
    """Compute a SHA-256 digest of ``(rel_path, st_mtime_ns, st_size)`` across ``*.py`` files."""
    resolved = package_root.resolve()
    if not resolved.is_dir():
        msg = f"Package directory does not exist: '{package_root}'."
        raise FileNotFoundError(msg)
    hasher = hashlib.sha256()
    for rel, py_path in _iter_package_py_files(resolved):
        st = py_path.stat()
        hasher.update(f"{rel}:{st.st_mtime_ns}:{st.st_size}\n".encode())
    return hasher.hexdigest()


@jezz_callable
def capture_package_sources(package_root: Path) -> Mapping[str, str]:
    """Snapshot compile-verified UTF-8 Python sources under ``package_root``."""
    resolved = package_root.resolve()
    sources: dict[str, str] = {}
    for rel, _ in _iter_package_py_files(resolved):
        contained = ContainedPath.from_parts(resolved, rel)
        src = read_bounded_text(contained)
        compile(src, rel, "exec")
        sources[rel] = src
    return MappingProxyType(sources)


def _write_snapshot_tree(
    tmp_root: Path,
    package_name: str,
    sources: Mapping[str, str],
) -> Path:
    resolved_tmp = tmp_root.resolve()
    for rel, src in sources.items():
        contained = ContainedPath.from_parts(resolved_tmp, f"{package_name}/{rel}")
        contained.resolved_path.parent.mkdir(parents=True, exist_ok=True)
        contained.resolved_path.write_text(src, encoding="utf-8")
    return resolved_tmp


def _build_worker_env(package_parent: Path) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parent_str = str(package_parent.resolve())
    env["PYTHONPATH"] = f"{parent_str}{os.pathsep}{existing}" if existing else parent_str
    return env


def _invoke_worker_process(
    request: ReloadWorkerRequest,
    timeout_sec: float,
) -> ReloadWorkerResponse:
    worker_script = Path(__file__).resolve()
    try:
        proc = subprocess.run(
            (sys.executable, "-P", str(worker_script)),
            input=request.model_dump_json(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
            env=_build_worker_env(Path(request.package_parent)),
        )
    except subprocess.TimeoutExpired:
        return ReloadWorkerResponse(
            status="import_failed",
            error=f"Hot-reload worker timed out after {timeout_sec:.1f}s.",
        )
    except OSError as exc:
        return ReloadWorkerResponse(
            status="import_failed",
            error=f"OS failure spawning reload worker: {type(exc).__name__}: {exc}",
        )

    raw_out = proc.stdout.strip()
    if proc.returncode == 0 and raw_out:
        return ReloadWorkerResponse.model_validate_json(raw_out)
    stderr_tail = (proc.stderr.strip() or raw_out or f"exit {proc.returncode}")[-1500:]
    return ReloadWorkerResponse(status="import_failed", error=stderr_tail)


def _decode_worker_response[ResultT: BaseModel](
    resp: ReloadWorkerResponse,
    response_type: type[ResultT],
) -> ResultT:
    if resp.status == "tool_error":
        raise ToolError(resp.error or "MCP tool execution failed.")
    if resp.status != "ok":
        raise ToolError(f"Hot-reload fallback failed: {resp.error}")
    return response_type.model_validate_json(resp.payload_json)


class HotReloadSupervisor:
    """Supervises fresh-interpreter MCP reloads while surviving transient mid-edit syntax faults."""

    @jezz_callable
    def __init__(
        self,
        workspace_root: Path,
        package_root: Path | None = None,
        *,
        worker_module: IdentifierStr = "jezz.mcp_server",
        worker_entrypoint: IdentifierStr = "dispatch_reloaded_mcp_call",
        reload_timeout_sec: Annotated[FiniteFloat, Field(gt=0.0, le=120.0)] = 30.0,
    ) -> None:
        default_pkg = Path(__file__).resolve().parent
        resolved_pkg = (package_root if package_root is not None else default_pkg).resolve()
        if not resolved_pkg.is_dir():
            msg = f"Watched package root does not exist: '{resolved_pkg}'."
            raise FileNotFoundError(msg)
        startup_fp = compute_package_fingerprint(resolved_pkg)
        self._spec = _SupervisorSpec(
            workspace_root=workspace_root.resolve(),
            package_root=resolved_pkg,
            worker_module=worker_module,
            worker_entrypoint=worker_entrypoint,
            timeout_sec=reload_timeout_sec,
            startup_fp=startup_fp,
        )
        self._state = _SnapshotState(
            healthy_fp=startup_fp,
            healthy_sources=dict(capture_package_sources(resolved_pkg)),
            reload_generation=0,
            last_reload_error="",
        )

    @property
    def startup_fingerprint(self) -> str:
        """Return the SHA-256 package fingerprint captured at server startup."""
        return self._spec.startup_fp

    @property
    def reload_generation(self) -> NonNegativeInt:
        """Return count of distinct mutated healthy package generations loaded."""
        return self._state.reload_generation

    @property
    def last_reload_error(self) -> str:
        """Return diagnostic message from the latest failed reload attempt, or ``''``."""
        return self._state.last_reload_error

    def _record_healthy_if_changed(self, current_fp: str) -> None:
        if current_fp == self._state.healthy_fp:
            return
        self._state.healthy_sources = dict(capture_package_sources(self._spec.package_root))
        self._state.healthy_fp = current_fp
        self._state.reload_generation += 1

    def _fallback_to_last_healthy[ResultT: BaseModel](
        self,
        tool_name: str,
        arguments_json: str,
        response_type: type[ResultT],
        local_call: Callable[[], ResultT],
    ) -> ResultT:
        if self._state.healthy_fp == self._spec.startup_fp and self._state.reload_generation == 0:
            return local_call()
        with tempfile.TemporaryDirectory() as tmp_dir:
            snap_parent = _write_snapshot_tree(
                Path(tmp_dir),
                self._spec.package_root.name,
                self._state.healthy_sources,
            )
            req = ReloadWorkerRequest(
                package_parent=str(snap_parent),
                workspace_root=str(self._spec.workspace_root),
                worker_module=self._spec.worker_module,
                worker_entrypoint=self._spec.worker_entrypoint,
                tool_name=tool_name,
                arguments_json=arguments_json,
            )
            resp = _invoke_worker_process(req, self._spec.timeout_sec)
            return _decode_worker_response(resp, response_type)

    @jezz_callable
    def invoke_or_local[ResultT: BaseModel](
        self,
        tool_name: IdentifierStr,
        arguments_json: SourceCodeText,
        response_type: Annotated[type[ResultT], InstanceGuard(type)],
        local_call: Annotated[Callable[[], ResultT], InstanceGuard(object)],
    ) -> ResultT:
        """Run ``local_call`` when package is unchanged, or dispatch via reload worker."""
        current_fp = compute_package_fingerprint(self._spec.package_root)
        if current_fp == self._spec.startup_fp and self._state.reload_generation == 0:
            self._state.last_reload_error = ""
            return local_call()

        req = ReloadWorkerRequest(
            package_parent=str(self._spec.package_root.parent),
            workspace_root=str(self._spec.workspace_root),
            worker_module=self._spec.worker_module,
            worker_entrypoint=self._spec.worker_entrypoint,
            tool_name=tool_name,
            arguments_json=arguments_json,
        )
        resp = _invoke_worker_process(req, self._spec.timeout_sec)
        if resp.status in {"ok", "tool_error"}:
            self._record_healthy_if_changed(current_fp)
            self._state.last_reload_error = ""
            return _decode_worker_response(resp, response_type)

        self._state.last_reload_error = resp.error
        return self._fallback_to_last_healthy(
            tool_name,
            arguments_json,
            response_type,
            local_call,
        )


@jezz_callable
def run_reload_worker_stdio() -> NonNegativeInt:
    """Execute a single isolated MCP tool invocation from stdin and write JSON to stdout."""
    raw_in = sys.stdin.read(2_000_000)
    req = ReloadWorkerRequest.model_validate_json(raw_in)
    sys.path.insert(0, str(Path(req.package_parent).resolve()))
    try:
        mod = importlib.import_module(req.worker_module)
        dispatch_fn = getattr(mod, req.worker_entrypoint)
    except (SyntaxError, ImportError, AttributeError, ValueError, TypeError, OSError) as exc:
        err_resp = ReloadWorkerResponse(
            status="import_failed",
            error=f"{type(exc).__name__}: {exc}"[:4000],
        )
        sys.stdout.write(err_resp.model_dump_json())
        return 0

    try:
        payload_json = str(dispatch_fn(Path(req.workspace_root), req.tool_name, req.arguments_json))
    except ToolError as exc:
        tool_err = ReloadWorkerResponse(status="tool_error", error=str(exc)[:4000])
        sys.stdout.write(tool_err.model_dump_json())
        return 0

    ok_resp = ReloadWorkerResponse(status="ok", payload_json=payload_json)
    sys.stdout.write(ok_resp.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(run_reload_worker_stdio())
