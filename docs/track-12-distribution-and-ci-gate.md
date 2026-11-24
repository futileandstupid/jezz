# Track 12 — Turnkey Distribution, Reusable CI Gate, & `v0.2.0` Release Packaging

## 1. Objective

Tracks 1–11 closed all four architectural pillars of `jezz` across **Python, TypeScript, Rust, and Go**, verified zero-credential multi-provider execution (`antigravity:<model>` + Proton Pass `pass-cli`), and demonstrated surgical brownfield OSS renovation (`jezz renovate --ratchet`). Track 12 packages `jezz` `v0.2.0` for drop-in CI/CD enforcement and reproducible distribution.

---

## 2. Reusable Composite GitHub Action (`action.yml`)

Root `action.yml` exposes **Jezz Software Building Code (`JBC` Edition 1.2) & `PCE` Statutory Gate** as a zero-boilerplate composite GitHub Action consumable by any Python, TypeScript, Rust, Go, or polyglot repository:

```yaml
- name: Enforce JBC Edition 1.2 Differential Occupancy Ratchet
  uses: futileandstupid/jezz@v0.2.0
  with:
    working-directory: "."
    mode: "differential"   # differential | full | both
    # language: "rust,go"  # optional space- or comma-separated filter
    # permit: "permit.json"
    # dossier: "dossier.json"
    # enable-cache: "true" # optional astral-sh/setup-uv cache (default: "false")
```

### Security & Determinism Invariants
- **Zero Raw `${{ inputs.* }}` Shell Interpolation (`JBC-SEC-001`):** Every action input (`working-directory`, `mode`, `base-ref`, `language`, `permit`, `as-of`, `dossier`, `python-version`, `enable-cache`) is bound strictly through step `env:` variables (`JEZZ_*`) or boolean input expressions before bash execution, eliminating untrusted PR branch/input shell injection.
- **Zero Hardcoded Host Paths (`JBC-SEC-006`):** Invokes `uv run --python "$JEZZ_PYTHON_VERSION" --project "$JEZZ_ACTION_PATH" jezz ...` anchored dynamically to `${{ github.action_path }}`, isolating `jezz`'s Python `>=3.14` environment from the target repository's toolchain (`Cargo.toml`, `go.mod`, `package.json`, or older Python virtualenvs).
- **Fail-Fast Keyless CLI Preflight (`_load_workflow_inputs`, `src/jezz/cli.py`):** Validates local `--mandate` and `--resume-elicitation` JSON artifacts prior to resolving provider credentials (`pass-cli`), ensuring deterministic exit code `2` validation diagnostics on keyless CI runners.
- **Automatic PR/Push Base Resolution & Step Summaries:** Defaults `base-ref` to `origin/${{ github.base_ref }}` on `pull_request` events and `HEAD~1` on `push` events, appends structured Markdown inspection and dossier-seal reports to `$GITHUB_STEP_SUMMARY`, and exports `inspect-exit-code` and `audit-exit-code` via `$GITHUB_OUTPUT`.
- **CI Dogfooding (`.github/workflows/jbc-gate.yml`):** Gate 6 of `jezz`'s own CI workflow dogfoods `uses: ./` on every push and pull request.

---

## 3. `v0.2.0` Package Metadata & Verification (`tests/test_track12_packaging_and_action.py`)

- Synchronized release version `"0.2.0"` across `pyproject.toml` (`[project].version`), `src/jezz/__init__.py` (`__version__`), and `src/jezz/mcp_server.py` (`SERVER_VERSION = __version__`), alongside `[project.urls]` (`Homepage`, `Repository`, `Documentation`), `keywords`, and PyPI `classifiers`.
- Added deterministic verification in `tests/test_track12_packaging_and_action.py` asserting version parity, `action.yml` / `jbc-gate.yml` path and shell-interpolation hygiene, and end-to-end execution of the composite action's bash gate against a temporary multi-commit Go Git repository with a sealed `EngineeringDossier` (`inspect_rc=0`, `audit_rc=0`, `OCCUPANCY_SEALED`).
