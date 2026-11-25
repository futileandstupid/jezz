# CLI, MCP Server, Configuration, & GitHub Action Reference

Complete operational reference for `jezz` `v0.6.0`.

---

## 1. CLI Subcommands (`jezz <subcommand>`)

| Subcommand | Purpose | Exit Codes |
| :--- | :--- | :--- |
| `jezz inspect <repo>` | Static Rough-In or Git-differential (`--changed-since`, `--staged`) inspection across 30 `JBC` v1.3 provisions (`.py`, `.ts`, `.tsx`, `.rs`, `.go`). | `0` = `APPROVED` / `APPROVED_WITH_VARIANCES`; `1` = `RED_TAG`; `2` = CLI/usage error |
| `jezz prove <repo>` | Polyglot AST/CST $\to$ Z3 (`QF_LIA`/`QF_NRA`/`QF_SLIA`) contract non-vacuity and postcondition proof discharge (`--require-deductive`, `--symbol`, `--timeout-ms`). | `0` = verified; `1` = counterexample, vacuous contract, or missing deductive proof |
| `jezz blueprint <repo>` | Extract `BlueprintGraph` netlist, render switchable **Sheet `A-1` / `E-1`** SVG/HTML (`--write-html`), compose `RedlineDelta`s (`--redlines`), or enforce `LVS` (`--verify-target`). | `0` = extracted / `LVS_CLEAN`; `1` = `LVS` `OPEN_CIRCUIT` / `SHORT_CIRCUIT` / `ENCAPSULATION_BREACH` |
| `jezz charter <repo>` | Phase 0/I empirical `RealityProbeEvidence` + 3-axis elicitation (`domain_ontology`, `hazard_mutation_policy`, `scope_and_exclusions`) $\to$ sealed `ProjectCharter`. | `0` = intake/sealed; `2` = validation/missing exclusion error |
| `jezz audit <dossier.json>` | Independently verify 5-phase `EngineeringDossier` invariants, write-scope confinement, `LVS` target equivalence, and dual SHA-256 + Z3 `CertificateOfOccupancy` seal. | `0` = `OCCUPANCY_SEALED`; `1` = `BLOCKED` |
| `jezz init [repo]` | Install Antigravity plugin bundle, `.git/hooks/pre-commit`, `jezz.toml` / `pyproject.toml` stanza, or materialize a starter (`--scaffold python\|typescript\|rust\|go`). | `0` = initialized |
| `jezz run <repo>` | Execute greenfield 5-phase `PCE` synthesis (`pydantic-graph` EFSM over $F_8 = 21$ behaviors) from `--prompt`, `--mandate`, or `--resume-elicitation`. | `0` = `OCCUPANCY_CERTIFIED` / `PLAN_READY`; `1` = `ELICITATION_REQUIRED` / `FAILED_CLOSED` |
| `jezz renovate <repo>` | Brownfield refactoring (`BHV-17 REFACTOR`) to `JBC` v1.3 occupancy across full repository or touched slice (`--ratchet`). | `0` = `OCCUPANCY_CERTIFIED`; `1` = `FAILED_CLOSED` |
| `jezz benchmark <dir>` | Controlled `pydantic-evals` A/B trial (`Control: Prose SDD` vs. `Treatment: Jezz PCE`) across `python`, `typescript`, `rust`, `go`, or `all`. | `0` = completed |
| `jezz mcp` | Launch read-only `stdio` MCP gate server (`14` tools) jailed inside `--root` with hot-reload supervision. | `0` = clean shutdown |
| `jezz hook stop` | Antigravity `Stop` lifecycle hook blocking agent turn completion when modified files introduce unwaived `RED_TAG` findings. | `0` (`stdout` JSON decision) |

---

## 2. Read-Only `stdio` MCP Server (`14` Tools)

Start via `jezz mcp --root <workspace>`. Every file operation is confined to `--root` via `ContainedPath` (`JBC-SEC-005`), and zero shell commands execute inside the MCP server:

| MCP Tool | Phase / Role | Description |
| :--- | :---: | :--- |
| `jbc_preview_source` | III / IV | Overlay an unsaved `.py`/`.ts`/`.tsx`/`.rs`/`.go` buffer onto the workspace graph in memory to catch per-unit hazards, `JBC-COG-003`/`006` cycles, and taint flows prior to writing to disk. |
| `jbc_prove_contracts` | III / V | Run bounded Z3 SMT symbolic verification over workspace or unsaved in-memory buffer contracts (`PROVED_DEDUCTIVE`, `BOUNDED_BY_PBT`, `COUNTEREXAMPLE_FOUND`, `VACUOUS_CONTRACT`). |
| `jbc_inspect_differential` | II / V | Git-aware Differential Occupancy Ratchet (`changed_since="HEAD"` or `staged=True`). |
| `jbc_inspect_workspace` | II / V | Full-workspace `JBC` Edition `1.3` Rough-In inspection with optional `permit_path` and `as_of` sunset date. |
| `jbc_extract_blueprint` | I / II | Extract `BlueprintGraph` JSON/Markdown/HTML (`A-1` Floor Plan & `E-1` Wiring Schematic) with optional `redlines_path`. |
| `jbc_verify_blueprint_lvs` | V | Verify extracted implementation netlist against a target `BlueprintGraph` (`OPEN_CIRCUIT`, `SHORT_CIRCUIT`, `UNAUTHORIZED_RISER`, `ENCAPSULATION_BREACH`). |
| `jbc_assess_lot_line` | I | Pre-flight `LotLineAssessment` (`IN_PACKAGE`, `WORKSPACE_COMPARTMENT`, `LOT_LINE_SPLIT`) measuring `JBC-COG-006` floorplate headroom and `CRP` dependency shear. |
| `pce_start_intake` | 0 / I | Probe workspace reality (`RealityProbeEvidence`) and emit structured 3-axis charter elicitation questions. |
| `pce_seal_charter` | 0 / I | Validate 3-axis resolutions + `HazardMutationPolicy` and seal a topologically ordered `ProjectCharter` mandate queue. |
| `jbc_query_ast_graph` | I / II | Query polyglot AST/CST module layers, cross-module callers (`caller_of`), and line-numbered symbol slices (`symbol_query`). |
| `jbc_list_codes` | II | List all 32 `JBC` Edition `1.3` provisions filtered by article, severity, or trigger. |
| `jbc_get_code` | II | Retrieve full normative rule, hazard, pass criteria, and ISO/IEEE/CWE/CERT/NASA lineage for a `JBC-*` ID. |
| `jbc_verify_behavior_graph` | II | Verify a `BehaviorGraph` against the $F_8 = 21$ transition matrix and Kildall monotone dataflow fixed point. |
| `jbc_audit_dossier` | V | Audit a 5-phase `EngineeringDossier` JSON against live workspace source units and SHA-256 + Z3 digests. |

---

## 3. Workspace Configuration (`jezz.toml` & `pyproject.toml`)

`jezz` resolves workspace settings from root **`jezz.toml`** first, falling back to **`[tool.jezz]` in `pyproject.toml`**:

```toml
# jezz.toml (recommended for TypeScript, Rust, Go, or polyglot monorepos)
[models]
default = "google:gemini-3.8-flash"
architect = "antigravity:claude-opus-5-5-medium"
builder = "antigravity:claude-sonnet-5-5-medium"
prompt_cache = "auto" # auto | off | prefix-only | 5m | 30m | 1h | 12h

[gates]
timeout_sec = 120.0   # (0.0, 600.0] subprocess watchdog

[secrets]
agent_reason = "jezz PCE runtime resolving model API credential"

[secrets.providers]
google = "env://GEMINI_API_KEY"
anthropic = "op://Engineering/Anthropic/credential"
```

---

## 4. Composite GitHub Action (`action.yml`)

| Input | Default | Description |
| :--- | :---: | :--- |
| `working-directory` | `"."` | Relative path to target repository root. |
| `mode` | `"differential"` | `"differential"`, `"full"`, or `"both"` (gracefully falls back on 1-commit template repos). |
| `base-ref` | `""` | Explicit Git base ref (`origin/<base_ref>` on PRs or `HEAD~1` on push when unset). |
| `language` | `""` | Optional filter (`python`, `typescript`, `rust`, `go`, comma- or space-separated). |
| `profile` | `""` | `"standard"` or `"strict_internal"`. |
| `permit` | `""` | Relative path to a `CodePermit` JSON file. |
| `as-of` | `""` | ISO-8601 date (`YYYY-MM-DD`) for `CodeVariance` sunset evaluation. |
| `blueprint-target` | `""` | Relative path to target `BlueprintGraph` JSON for `LVS` gate enforcement. |
| `prove` | `""` | Z3 contract gate mode: `""` (off), `"check"`, or `"require-deductive"`. |
| `dossier` | `""` | Relative path to `EngineeringDossier` JSON verified via `jezz audit`. |
