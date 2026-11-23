"""Portable Antigravity plugin generator and workspace bootstrapper for ``jezz init``."""

import json
import shutil
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final

from pydantic import BaseModel, Field

from jezz.guards import (
    STRICT_FROZEN_CONFIG,
    WIRE_TUPLE_VALIDATOR,
    BoundedText,
    ContainedPath,
    IdentifierStr,
    jezz_callable,
    read_bounded_text,
)

JEZZ_REPO_URL: Final[str] = "https://github.com/futileandstupid/jezz"

_RULES_AGENTS_MD: Final[str] = """# Jezz Statutory Engineering Rules (JBC v1.2 & PCE)

## Methodology & Framing
- Govern engineering work via **Permit-and-Contract Engineering (`PCE`)**.
- Never frame deliverables around Agile ceremonies, persona user stories (`As a ..., I want ...`),
  or Markdown task checklists (`- [ ]`). Express requirements as falsifiable `DomainInvariant`s
  and disjoint `ModuleWriteScope`s.
- Never hardcode filesystem paths (`JBC-SEC-006`). Calculate every path dynamically from known
  file, package, or injected root anchors (`Path(__file__).resolve()`, `importlib.resources`,
  `tmp_path` / `tempfile`, or `ContainedPath.from_parts(anchor, rel)`).

## Statutory Verification Workflow
- Before writing or editing non-trivial Python modules under `src/` or `tests/`, dry-run candidate
  source buffers in memory via MCP tool `jbc_preview_source` (`0` unwaived `RED_TAG` violations,
  including whole-workspace `JBC-COG-003` cycle and private-import checks).
- Use `jbc_get_code` or `jbc_list_codes` to inspect normative **Jezz Software Building Code
  (`JBC` Edition 1.2)** requirements and remediations whenever a provision is cited.
- Before ending any turn that modifies Python files, verify clean occupancy via
  `jbc_inspect_differential` and run workspace dynamic gates (`uv run pytest`, `uv run mypy .`,
  `uv run ruff check .`).
"""

_PCE_SKILL_MD: Final[str] = """---
name: pce
description: >-
  Executes the 5-phase Permit-and-Contract Engineering (PCE) workflow and enforces Jezz Software
  Building Code (JBC Edition 1.2) occupancy gates. Activate when planning features, writing or
  renovating Python code under JBC governance, or auditing PCE engineering dossiers.
---

# Permit-and-Contract Engineering (`PCE`) Runbook

Execute Python engineering tasks through the five statutory `PCE` phases rather than unverified
prose specifications.

## Mode 1: Interactive Co-Engineering via `jezz` MCP Tools

1. **Phase I — Mandate (`BHV-01 FRAME` / `BHV-02 ELICIT`):**
   - Enumerate open domain ambiguities and ask the user to resolve them before mutating code.
   - Establish falsifiable invariants (`INV-001`, ...) mapped to concrete Python verification
     symbols and pairwise-disjoint `ModuleWriteScope`s.
2. **Phase II — Permit (`BHV-03 BIND_CODES` / `BHV-04 INSPECT_CODE`):**
   - Inspect baseline compliance with `jbc_inspect_workspace` or `jbc_inspect_differential`.
   - Verify any multi-step behavior topology with `jbc_verify_behavior_graph`.
3. **Phase III — Contract & Instrumentation (`BHV-14 SPECIFY` / `BHV-15 INSTRUMENT`):**
   - Author strict Pydantic models (`extra="forbid"`, `strict=True`, `allow_inf_nan=False`),
     `@jezz_callable` / `@icontract` preconditions & postconditions, and `pytest` + `Hypothesis`
     falsification suites before implementation bodies.
   - Validate candidate files in memory with `jbc_preview_source` prior to writing to disk.
4. **Phase IV — Scoped Construction (`BHV-16 SYNTHESIZE` / `BHV-17 REFACTOR`):**
   - Implement or refactor strictly within authorized `ModuleWriteScope`s; preview each module
     via `jbc_preview_source`.
5. **Phase V — Occupancy Verification (`BHV-18` / `BHV-19` / `BHV-21`):**
   - Confirm zero unwaived `JBC` violations via `jbc_inspect_differential`.
   - Execute dynamic gates: `uv run pytest`, `uv run mypy .`, and `uv run ruff check .`.
   - When an `EngineeringDossier` JSON is produced, verify seal integrity via `jbc_audit_dossier`.

## Mode 2: Autonomous `jezz` CLI Delegation

- **Plan & Mandate Preview (No Workspace Mutation):**
  `uv run jezz run <repo_path> --prompt "<objective>" --plan-only --write-mandate mandate.json`
- **Greenfield Synthesis from Prompt or Mandate:**
  `uv run jezz run <repo_path> --mandate mandate.json --write-dossier dossier.json`
- **Brownfield JBC Renovation:**
  `uv run jezz renovate <repo_path> --prompt "<renovation objective>"`
- **Differential Occupancy Ratchet:**
  `uv run jezz inspect <repo_path> --changed-since HEAD` (or `--staged`)
"""


class PluginInstallScope(StrEnum):
    """Target installation scope for the Antigravity ``jezz`` plugin bundle."""

    WORKSPACE = "workspace"
    GLOBAL = "global"
    NONE = "none"


class PluginInstallSpec(BaseModel):
    """Declarative specification for ``initialize_jezz_workspace``."""

    model_config = STRICT_FROZEN_CONFIG

    repo_root: Path
    scope: PluginInstallScope = PluginInstallScope.WORKSPACE
    global_config_dir: Path | None = None
    jezz_executable: Path | None = None
    default_model: IdentifierStr = "google:gemini-3.8-flash"
    overwrite: bool = False
    install_pre_commit: bool = False
    bootstrap_pyproject: bool = True


class WorkspaceInitReport(BaseModel):
    """Structured ledger of artifacts created or preserved by ``jezz init``."""

    model_config = STRICT_FROZEN_CONFIG

    repo_root: Path
    plugin_scope: PluginInstallScope
    plugin_dir: Path | None = None
    jezz_executable: Path
    written_files: Annotated[
        tuple[BoundedText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    skipped_files: Annotated[
        tuple[BoundedText, ...],
        WIRE_TUPLE_VALIDATOR,
        Field(max_length=64),
    ] = ()
    pyproject_updated: bool = False
    pre_commit_installed: bool = False

    @jezz_callable
    def to_markdown(self) -> BoundedText:
        """Render a concise Markdown summary of ``jezz init`` actions."""
        lines = [
            f"# Jezz Workspace Initialization — `{self.repo_root.name}`",
            f"- **Workspace Root:** `{self.repo_root}`",
            f"- **Plugin Scope:** `{self.plugin_scope}`",
            f"- **Jezz Executable:** `{self.jezz_executable}`",
            f"- **`pyproject.toml` Updated:** `{self.pyproject_updated}`",
            f"- **Git Pre-Commit Installed:** `{self.pre_commit_installed}`",
        ]
        if self.plugin_dir is not None:
            lines.append(f"- **Plugin Directory:** `{self.plugin_dir}`")
        if self.written_files:
            lines.extend(["", "## Written Artifacts"])
            lines.extend(f"- `{item}`" for item in self.written_files)
        if self.skipped_files:
            lines.extend(["", "## Preserved Existing Artifacts (Use `--overwrite` to Replace)"])
            lines.extend(f"- `{item}`" for item in self.skipped_files)
        return "\n".join(lines)


@jezz_callable
def resolve_jezz_executable(preferred_repo: Path | None = None) -> Path:
    """Locate the canonical ``jezz`` CLI executable for MCP and lifecycle hook commands."""
    if preferred_repo is not None:
        local_venv_bin = preferred_repo.resolve() / ".venv" / "bin" / "jezz"
        if local_venv_bin.is_file():
            return local_venv_bin
    sibling_bin = Path(sys.executable).resolve().parent / "jezz"
    if sibling_bin.is_file():
        return sibling_bin
    found = shutil.which("jezz")
    if found is not None:
        return Path(found).resolve()
    return sibling_bin


_DYNAMIC_PLUGIN_WORKSPACE_PREFIX: Final[str] = (
    'REPO_ROOT="$(CDPATH= cd -- "${PLUGIN_ROOT:-.}/../../.." && pwd)"; '
    'JEZZ_BIN="$REPO_ROOT/.venv/bin/jezz"; '
    '[ -x "$JEZZ_BIN" ] || JEZZ_BIN=jezz; '
)


def _build_mcp_config_json(scope: PluginInstallScope) -> str:
    if scope == PluginInstallScope.WORKSPACE:
        entry = {
            "command": "sh",
            "args": [
                "-c",
                f'{_DYNAMIC_PLUGIN_WORKSPACE_PREFIX}exec "$JEZZ_BIN" mcp --root "$REPO_ROOT"',
            ],
        }
    else:
        entry = {
            "command": "jezz",
            "args": ["mcp"],
        }
    return json.dumps({"mcpServers": {"jezz": entry}}, indent=2) + "\n"


def _build_hooks_json(scope: PluginInstallScope) -> str:
    hook_cmd = (
        f'{_DYNAMIC_PLUGIN_WORKSPACE_PREFIX}exec "$JEZZ_BIN" hook stop --root "$REPO_ROOT"'
        if scope == PluginInstallScope.WORKSPACE
        else "exec jezz hook stop --require-marker"
    )
    payload = {
        "jezz-jbc-stop-gate": {
            "Stop": [
                {
                    "type": "command",
                    "command": hook_cmd,
                    "timeout": 15,
                }
            ]
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def _build_plugin_manifest_json() -> str:
    payload = {
        "name": "jezz",
        "repository": JEZZ_REPO_URL,
    }
    return json.dumps(payload, indent=2) + "\n"


def _write_or_skip_contained(
    anchor_dir: Path,
    rel_path: str,
    content: str,
    *,
    overwrite: bool,
    executable: bool = False,
) -> tuple[bool, str]:
    target = ContainedPath.from_parts(anchor_dir, rel_path)
    display = str(target.resolved_path)
    if target.resolved_path.exists() and not overwrite:
        return False, display
    target.resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with target.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
    if executable:
        target.resolved_path.chmod(0o755)
    return True, display


def _resolve_plugin_target_dir(spec: PluginInstallSpec, resolved_repo: Path) -> Path | None:
    if spec.scope == PluginInstallScope.NONE:
        return None
    if spec.scope == PluginInstallScope.WORKSPACE:
        return ContainedPath.from_parts(resolved_repo, ".agents/plugins/jezz").resolved_path
    base_global = (
        spec.global_config_dir.resolve()
        if spec.global_config_dir is not None
        else (Path.home() / ".gemini" / "config").resolve()
    )
    base_global.mkdir(parents=True, exist_ok=True)
    return ContainedPath.from_parts(base_global, "plugins/jezz").resolved_path


def _install_plugin_bundle(
    plugin_dir: Path,
    spec: PluginInstallSpec,
) -> tuple[list[str], list[str]]:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    files = (
        ("plugin.json", _build_plugin_manifest_json()),
        ("mcp_config.json", _build_mcp_config_json(spec.scope)),
        ("hooks.json", _build_hooks_json(spec.scope)),
        ("rules/AGENTS.md", _RULES_AGENTS_MD),
        ("skills/pce/SKILL.md", _PCE_SKILL_MD),
    )
    written: list[str] = []
    skipped: list[str] = []
    for rel_name, text in files:
        did_write, full_str = _write_or_skip_contained(
            plugin_dir,
            rel_name,
            text,
            overwrite=spec.overwrite,
        )
        (written if did_write else skipped).append(full_str)
    return written, skipped


def _maybe_bootstrap_pyproject(
    resolved_repo: Path,
    default_model: str,
) -> tuple[bool, str | None]:
    pyproject = ContainedPath.from_parts(resolved_repo, "pyproject.toml")
    if not pyproject.resolved_path.is_file():
        return False, None
    existing = read_bounded_text(pyproject)
    if "[tool.jezz" in existing:
        return False, None
    sep = "" if existing.endswith("\n") else "\n"
    stanza = f'{sep}\n[tool.jezz.models]\ndefault = "{default_model}"\n'
    with pyproject.resolved_path.open("w", encoding="utf-8") as fh:
        fh.write(existing + stanza)
    return True, str(pyproject.resolved_path)


def _maybe_install_pre_commit(
    resolved_repo: Path,
    *,
    overwrite: bool,
) -> tuple[bool, str | None, str | None]:
    git_dir = ContainedPath.from_parts(resolved_repo, ".git")
    if not git_dir.resolved_path.is_dir():
        return False, None, None
    script = (
        "#!/bin/sh\n"
        "# Statutory JBC Differential Occupancy Ratchet (dynamically anchored to repo root)\n"
        'HOOK_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        'REPO_ROOT="$(CDPATH= cd -- "$HOOK_DIR/../.." && pwd)"\n'
        'JEZZ_BIN="$REPO_ROOT/.venv/bin/jezz"\n'
        '[ -x "$JEZZ_BIN" ] || JEZZ_BIN=jezz\n'
        'exec "$JEZZ_BIN" inspect "$REPO_ROOT" --staged\n'
    )
    did_write, path_str = _write_or_skip_contained(
        resolved_repo,
        ".git/hooks/pre-commit",
        script,
        overwrite=overwrite,
        executable=True,
    )
    if did_write:
        return True, path_str, None
    return False, None, path_str


@jezz_callable
def initialize_jezz_workspace(spec: PluginInstallSpec) -> WorkspaceInitReport:
    """Install the Antigravity ``jezz`` plugin bundle and optional workspace config hooks."""
    resolved_repo = spec.repo_root.resolve()
    if not resolved_repo.is_dir():
        msg = f"Target workspace directory does not exist: '{spec.repo_root}'."
        raise FileNotFoundError(msg)

    jezz_exe = (
        spec.jezz_executable.resolve()
        if spec.jezz_executable is not None
        else resolve_jezz_executable(resolved_repo)
    )
    written: list[str] = []
    skipped: list[str] = []

    plugin_dir = _resolve_plugin_target_dir(spec, resolved_repo)
    if plugin_dir is not None:
        p_written, p_skipped = _install_plugin_bundle(plugin_dir, spec)
        written.extend(p_written)
        skipped.extend(p_skipped)

    pyproject_updated = False
    if spec.bootstrap_pyproject:
        pyproject_updated, updated_path = _maybe_bootstrap_pyproject(
            resolved_repo, spec.default_model
        )
        if updated_path is not None:
            written.append(updated_path)

    pre_commit_installed = False
    if spec.install_pre_commit:
        pre_commit_installed, hook_written, hook_skipped = _maybe_install_pre_commit(
            resolved_repo,
            overwrite=spec.overwrite,
        )
        if hook_written is not None:
            written.append(hook_written)
        if hook_skipped is not None:
            skipped.append(hook_skipped)

    return WorkspaceInitReport(
        repo_root=resolved_repo,
        plugin_scope=spec.scope,
        plugin_dir=plugin_dir,
        jezz_executable=jezz_exe,
        written_files=tuple(written),
        skipped_files=tuple(skipped),
        pyproject_updated=pyproject_updated,
        pre_commit_installed=pre_commit_installed,
    )
