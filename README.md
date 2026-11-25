# `jezz` — Licensure-Grade Software Engineering & Coding-Agent Framework

> **Technical White Paper:** **[Permit-and-Contract Engineering: Licensure-Grade Software Building Codes, Behavioral Control-Flow Verification, and Empirical Evaluation of Coding Agents](docs/whitepaper.md)**

`jezz` (`v0.5.0`) is an experimental software engineering and coding-agent framework structured around four integrated pillars:

1. **[Pillar 1 — Complete Agent Behavior Basis (`jezz.behaviors`)](docs/pillar-1-behavior-basis.md):**
   An exhaustive taxonomy of atomic coding-agent behaviors with Fibonacci cardinality ($F_8 = 21$, `BHV-01`..`BHV-21`), local state-machine transition legality, and Kildall monotone dataflow verification.
2. **[Pillar 2 — Enforceable Software Building Codes & Blueprint `LVS` (`jezz.codes`, `@jezz/blueprint`)](docs/pillar-2-building-codes.md):**
   The **Jezz Software Building Code (`JBC` Edition 1.3)**—32 machine-checkable provisions across 6 Articles (`DBC`, `FLT`, `SEC`, `COG`, `OBS`, `VRF`), including subpackage compartment quotient-graph acyclicity, floorplate ceilings, and workspace fire-walls (`JBC-COG-006`), deterministic AST/CST **`BlueprintGraph`** netlist extraction, switchable **Sheet `A-1` (Floor Plan) $\leftrightarrow$ Sheet `E-1` (`elkjs` Manhattan Wiring Schematic)** CAD projections (`jezz blueprint` & `packages/jezz-blueprint`), coarse-grained **Layout-Versus-Schematic (`LVS`)** boundary verification (`verify_blueprint_lvs`), and dual-digest SHA-256 `CertificateOfOccupancy` seals across **Python (`.py`), TypeScript (`.ts`/`.tsx`), Rust (`.rs`), and Go (`.go`)**.
3. **Pillar 3 — SDD Forensic Teardown & Permit-and-Contract Engineering (`jezz.methodology`):**
   - **[Pillar 3A — Forensic Teardown of Industry "SDD"](docs/pillar-3a-sdd-teardown.md):** Primary-source critique of GitHub `spec-kit`, Fission `OpenSpec`, and AWS Kiro, plus controlled TDD vs. Iterative Test-Last (`ITL`) empirical grounding.
   - **[Pillar 3B — Permit-and-Contract Engineering (`PCE`)](docs/pillar-3b-pce-methodology.md):** A 5-phase, anti-Agile, anti-theatre engineering process (`Mandate` $\to$ `Permit` $\to$ `Contract` $\to$ `Construction` $\to$ `Occupancy`) featuring reality-probed Phase 0/I **`ProjectCharter`** 3-axis intake (`jezz charter`), pre-flight **`LotLineAssessment`** package-split shear gates, and deterministic `audit_pce_dossier` + `BlueprintGraph` `LVS` verification.
4. **[Pillar 4 — Pydantic AI & Pydantic-Graph Runtime (`jezz.agents`)](docs/pillar-4-pydantic-ai-runtime.md):**
   Polyglot executable agent runtime enacting all $F_8 = 21$ (`100%`) behaviors across **Python, TypeScript, Rust, and Go** as typed `AtomicBehaviorGraphNode` vertices, combining least-privilege `WorkspaceSandbox` toolsets, `WorkspaceAstGraph` & installed-package API introspection, permit-gated `BHV-13 PROVISION` (`uv add`, `npm install`, `cargo add`, `go get`), real-compiler `PolyglotSubprocessGateRunner` gates (`mypy`/`pytest`, `tsc`/`vitest`/`node --test`, `cargo check`/`cargo test`, `go vet`/`go test`), scheme-dispatched credential isolation (`env://`, `pass://`, `op://`, `keychain://`), persistent `.jezz/memory.json` longitudinal memory (`BHV-07 CURATE_MEMORY`), AST-grounded topological DAG planning + lot-line shear pre-flight (`DECOMPOSE`), parallel `asyncio.TaskGroup` `INSTRUMENT` and multi-wave `DELEGATE`/`RECONCILE`, mandatory `RED_TAG` $\to$ `REVERT` $\to$ `HYPOTHESIZE` $\to$ `CURATE_MEMORY` self-healing, and full `PCE` Dossier certification.

## Empirical Validation & Architectural Roadmap
- **[Empirical Validation & Longitudinal Testing Roadmap](docs/empirical-testing-roadmap.md):** Complete Track 1–26 validation ledger spanning CLI/MCP/Stop-hook harnesses, brownfield field trials, controlled `pydantic-evals` A/B benchmarks against prose "SDD", polyglot call-flow taint engines, `ProjectCharter` intake, and `BlueprintGraph` `LVS` gates.
- **[Track 2 — Brownfield Inspector Field Trial Report](docs/track-2-brownfield-field-trial.md):** Empirical audit across 6 repositories totaling 17,609 files / 7,414,726 LOC (`github/spec-kit`, `pydantic`, `pydantic-graph`, `pydantic-deepagents`, `KiroCrew`, and AWS Kiro E2E sample) and resulting engine/AST calibrations.
- **[Track 3 — First Live-Model Occupancy Trial Report](docs/track-3-live-occupancy-trial.md):** End-to-end `PceWorkflowOrchestrator` + `UvSubprocessGateRunner` trials on `google:gemini-3.8-flash` across ambiguity halting (`FRAME -> ELICIT`), adversarial integer-cent synthesis (`SPECIFY -> INSTRUMENT -> SYNTHESIZE`), and 3-worker parallel `TaskGroup` construction (`DELEGATE -> RECONCILE`).
- **[Track 4 — Controlled `pydantic-evals` A/B Benchmark Report](docs/track-4-sdd-vs-pce-benchmark.md):** 46-workspace live A/B evaluation across Waves 4A–4E (`Control: Prose SDD` vs. `Treatment: Jezz PCE` on uniform `google:gemini-3.8-flash` and symmetric tiered `google:gemini-4-hydrogen` + `google:gemini-3.8-flash`) achieving `6/6` (`100%`) task occupancy closure, `0.0%` silent defect escape (`36/36` held-out `Hypothesis` checks across Waves 4B–4E), and `-48.5%` tiered latency against `375` unwaived `JBC` violations (`36` `LIFE_SAFETY`, `0/23` occupiable) in `23/23` checked-off `- [x]` SDD builds.
- **[Track 5 — Multi-Mandate Longitudinal Crucible & `JBC` Edition `1.2` Report](docs/track-5-multi-mandate-crucible.md):** 4-mandate longitudinal build of `jezz-crucible-ledger` (`11` units / `2,693` LOC across domain algebra, parameterized savepoint SQLite store, 2-wave topological DAG escrow + SHA-256 reconciliation, and brownfield CLI renovation under live `jezz hook stop` gates), 8 runtime/introspection calibrations, and `JBC-SEC-006` dynamic path anchoring.
- **[Track 6 — Full $F_8 = 21$ Behavior Runtime Closure & Persistent Memory](docs/track-6-full-basis-runtime.md):** Complete `21 / 21` (`100%`) `AtomicBehaviorGraphNode` runtime coverage (`InspectEnvNode`, `ConsultNode`, `CurateMemoryNode`, `ProvisionNode`), rollback-safe `ProvisionPort` (`uv add`) + `BuildingInspector` manifest reload, symmetric `12`-slot specialist routing, and cross-mandate `.jezz/memory.json` continuity.
- **[Track 7 — Token Cost Compression & Cross-Provider Generalizability](docs/track-7-prompt-caching-and-providers.md):** Enabled-by-default cross-provider prompt caching (`PromptCacheMode`, static `InstructionPart` canonicalization, provider-aware `CachePoint` slice partitioning), `448`-probe offline wire compatibility across Google/Anthropic/OpenAI/OpenRouter, repeatable `jezz benchmark --suite` matrix evaluation, and live Gemini Waves 7A–7B (`10` workspace builds) achieving `68.7%–72.5%` `PCE` cache hit ratios (`-61.8%` to `-65.2%` effective billed-input compression vs. `0.0%` on open-loop SDD).
- **[Track 8 — Polyglot `JBC` Inspector & Six-Repository Field Trial](docs/track-8-polyglot-inspector.md):** Hexagonal `LanguageAdapterPort` + official `tree-sitter` CST backends extending `JBC`, `WorkspaceAstGraph`, CLI/MCP/Stop-hook ratchets, and `PCE` dossier seals across Python (`.py`), TypeScript (`.ts`/`.tsx`), Rust (`.rs`), and Go (`.go`), validated on a 4-language `PCE` crucible and `6` open-source repositories (`zod`, `hono`, `ripgrep`, `serde_json`, `chi`, `cobra` — `1,110` units / `300,067` LOC, `0` crashes).
- **[Track 9 — Polyglot Autonomous `PCE` Runtime (`jezz run` & `jezz renovate`)](docs/track-9-polyglot-pce-runtime.md):** Full 4-language `PceWorkflowOrchestrator` closure (`PolyglotSubprocessGateRunner`, `PolyglotSubprocessProvisionRunner`, universal `all(...)` `JBC-VRF-005`/`006` conjunction, fail-closed zero-test `cargo test`/`go test` guards, and language-scoped `JBC` briefs), achieving `4 / 4` (`100%`) Pass-1 `OCCUPANCY_CERTIFIED` & `OCCUPANCY_SEALED` live builds across TypeScript, Rust, Go, and brownfield Rust `renovate`.
- **[Track 10 — Zero-Credential `antigravity:<model>` Adapter & Cross-Provider Empirical Benchmark](docs/track-10-cross-provider-benchmark.md):** Zero-API-key `AntigravityCliModel` (`antigravity:claude-opus-5-5-medium`, `antigravity:claude-sonnet-5-5-medium`, `antigravity:gpt-oss-120b-medium`), `640 / 640` offline cross-provider wire probes, and `19` live workspace builds (`Waves 10A & 10B`) confirming Wave 4D's "Over-Specified Prose Drift" on Anthropic Claude while `jezz` `PCE` achieved `100%` `JBC` occupancy across Claude, `GPT-OSS 120B`, and Gemini.
- **[Track 11 — External Brownfield OSS Renovation Crucible (`jezz renovate --ratchet`)](docs/track-11-brownfield-renovation-crucible.md):** Differential Final Occupancy Ratchet (`inspect_differential_final`, `--ratchet`, `ratchet_scoped`) and `3 / 3` (`100%`) certified & `OCCUPANCY_SEALED` surgical renovations across external Go (`go-chi/chi`), Rust (`serde-rs/json`), and Python (`github/spec-kit` — `291,626` LOC, `76.9%–79.8%` cache hit ratios).
- **[Track 12 — Turnkey Distribution, Reusable CI Gate, & Release Packaging](docs/track-12-distribution-and-ci-gate.md):** Reusable composite GitHub Action (`action.yml`, dogfooded in `.github/workflows/jbc-gate.yml`) enforcing polyglot `jezz inspect` ratchets and `jezz audit` dossier seals with zero raw `${{ inputs.* }}` shell interpolation and zero hardcoded paths (`JBC-SEC-006`).
- **Tracks 13–20 (`v0.2.1`–`v0.4.0` Hardening, Inter-Procedural Call-Flow, & Hot-Reload Daemon — [Empirical Roadmap](docs/empirical-testing-roadmap.md)):** Unified `dep_graph.py` cycle/layer kernel, `PermitProfile` (`standard` / `strict_internal`), scoped polyglot `--ratchet` gates with `(0, 600s]` `--gate-timeout` watchdogs and $O(|\Delta|)$ overlay smoke-import staging, `>=92%` CI coverage ratchet + `RuleBasedStateMachine` / 4-language parser fuzzing, multi-ecosystem `BHV-13 PROVISION` + offline polyglot `BHV-06 CONSULT`, strict `<= 1,000` LOC production module ceiling, whole-workspace polyglot inter-procedural call-flow & taint propagation (`call_flow.py`), and live MCP daemon hot-reload supervision (`mcp_reload.py`).
- **[Track 21 — Live Polyglot SDD-vs-`PCE` Benchmark (`TypeScript`, `Rust`, & `Go`)](docs/track-21-polyglot-live-benchmark.md):** `24` live workspace builds (`Wave 21A`) across TypeScript, Rust, and Go on `claude_sonnet` and `claude_tiered` (`Opus 5.5 + Sonnet 5.5`) achieving `12 / 12` (`100.0%`) `JBC` occupancy, `12 / 12` (`100.0%`) held-out property/fuzz oracle survival, `0` rollbacks, `-4.2%` lower effective billed input (`54.3%` cache hit ratio), `-56.7%` fewer output tokens, and `-60.8%` lower latency (`84.93s` vs. `216.87s`) than Prose SDD (`1 / 12` occupiable, `33.3%` silent oracle escape).
- **Tracks 22–26 (`v0.5.0` — Compartmentation, Project Charters, Switchable CAD Blueprints, & Coarse-Grained `LVS` — [Empirical Roadmap](docs/empirical-testing-roadmap.md)):** Scheme-routed multi-backend secrets (`env://`, `pass://`, `op://`, `keychain://` + user `config.toml`), empirical `RED -> GREEN` defect-repro vs. Contract + Property-First greenfield bifurcation, **`JBC` Edition `1.3` (`JBC-COG-006`)** subpackage quotient acyclicity + floorplate/portal/firewall laws & `LotLineAssessment` shear pre-flight, reality-probed Phase 0/I **`ProjectCharter`** 3-axis intake (`jezz charter`), and deterministic **`BlueprintGraph`** netlist extraction + switchable **Sheet `A-1` (Floor Plan) $\leftrightarrow$ Sheet `E-1` (`elkjs` Manhattan Wiring Schematic)** CAD viewer (`jezz blueprint` & `@jezz/blueprint`) + coarse-grained **Layout-Versus-Schematic (`LVS`)** boundary equivalence (`verify_blueprint_lvs`).

## CLI Usage

```bash
# Run polyglot JBC v1.3 Rough-In inspection (.py, .ts, .tsx, .rs, .go; exit 0 = APPROVED, 1 = RED_TAG)
uv run jezz inspect <repo_path> [--language python|typescript|rust|go] [--profile standard|strict_internal] [--permit permit.json] [--as-of YYYY-MM-DD] [--format markdown|json]

# Differential Occupancy Ratchet: gate only Git-modified or staged source files (plus whole-repo cycles & firewalls)
uv run jezz inspect <repo_path> (--changed-since origin/main | --staged) [--language ...] [--profile standard|strict_internal] [--permit permit.json]

# Extract BlueprintGraph netlist, render Switchable A-1/E-1 CAD projections, or verify coarse-grained target LVS
uv run jezz blueprint <repo_path> [--title "..."] [--redlines redlines.json] [--verify-target target_bp.json] [--write-html blueprint.html] [--write-json blueprint.json] [--write-svg-a1 a1.svg] [--write-svg-e1 e1.svg] [--format markdown|json|svg-a1|svg-e1|html]

# Probe workspace reality, run 3-axis formal elicitation, and seal a phased ProjectCharter permit queue
uv run jezz charter <repo_path> --title "..." --vision "..." [--interactive | --resolutions answers.json] [--package-slug <pkg>] [--write-charter charter.json] [--write-mandates-dir permits/]

# Audit a 5-phase PCE EngineeringDossier (including CertificateOfOccupancy dual-digest & target BlueprintGraph LVS)
uv run jezz audit <dossier.json> [--repo <repo_path>] [--format markdown|json]

# Bootstrap Antigravity plugin (MCP + Stop hook + PCE skill + rules), [tool.jezz.models], & optional pre-commit ratchet
uv run jezz init [repo_path] [--plugin-scope workspace|global|none] [--pre-commit] [--overwrite]

# Execute a greenfield PCE prompt or mandate (interactive TTY ELICIT + live progress + optional --plan-only review)
uv run jezz run <repo_path> [--model <provider:model>] [--architect-model <provider:model>] [--secret-uri <scheme>://...] [--profile standard|strict_internal] [--gate-timeout 120] [--prompt-cache auto|off|5m|1h] (--prompt "..." | --mandate mandate.json | --resume-elicitation elicit.json --clarification "...") [--plan-only --write-mandate mandate.json] [--interactive] [--progress]

# Renovate an existing brownfield repository (or scoped slice via --ratchet) to JBC v1.3 occupancy via BHV-17 REFACTOR
uv run jezz renovate <repo_path> [--ratchet] [--profile standard|strict_internal] [--permit permit.json] [--gate-timeout 120] [--model <provider:model>] [--builder-model <provider:model>] [--secret-uri <scheme>://...] [--prompt-cache auto|off|5m|1h] (--prompt "..." | --mandate mandate.json) [--plan-only --write-mandate mandate.json] [--write-dossier dossier.json]

# Execute controlled pydantic-evals A/B benchmark across Python, TypeScript, Rust, Go, or all 12 tasks
uv run jezz benchmark <work_dir> [--language python|typescript|rust|go|all] [--model <provider:model> | --suite <label>=<model>[@<cache>]] [--prompt-cache auto|off|5m|1h] [--max-concurrency 2] [--write-json ab.json]

# Launch the read-only JBC v1.3 & PCE statutory MCP server or Antigravity Stop lifecycle gate over stdio
uv run jezz mcp [--root <repo_path>]
uv run jezz hook stop [--root <repo_path>] [--max-continuations 3] [--require-marker]
```

## Statutory MCP Gate Server (`jezz mcp`)

External interactive coding agents (Antigravity, Claude Code, Cursor, Codex, JetBrains AI) can mount `jezz` as a zero-mutation, zero-shell `stdio` MCP server (`src/jezz/mcp_server.py`) backed by a compile-verified hot-reload supervisor (`src/jezz/mcp_reload.py` — automatically picking up `src/jezz/**/*.py` mutations in a clean `sys.executable -P` subprocess while surviving transient mid-edit `SyntaxError`s via healthy-snapshot fallback), exposing **13 structured read-only tools** jailed inside `--root` via `ContainedPath` (`JBC-SEC-005`):

- `jbc_preview_source`: Pre-write in-memory Rough-In inspection of an unsaved `.py`, `.ts`, `.tsx`, `.rs`, or `.go` buffer overlaid onto the workspace module graph (catching per-unit violations, cross-module `JBC-COG-003` / `JBC-COG-006` cycles, compartment breaches, and inter-procedural taint leaks before touching disk).
- `jbc_inspect_differential`: Git-aware polyglot Differential Occupancy Ratchet (`--changed-since`, defaulting to `HEAD`, or `staged=True`).
- `jbc_inspect_workspace`: Full repository `JBC` Edition `1.3` Rough-In inspection across `.py`, `.ts`, `.tsx`, `.rs`, and `.go` units with `CodePermit` and sunset `as_of` support.
- `jbc_extract_blueprint` / `jbc_verify_blueprint_lvs`: Deterministic AST/CST `BlueprintGraph` netlist extraction (with optional `RedlineDelta` composition $B^* = B_0 \oplus \Delta B$) and coarse-grained boundary Layout-Versus-Schematic (`LVS`) verification (`OPEN_CIRCUIT` / `SHORT_CIRCUIT`).
- `jbc_assess_lot_line`: Pre-flight `LotLineAssessment` evaluating `JBC-COG-006` floorplate headroom, Public-API Sufficiency, and `CRP` dependency/hazard shear (`IN_PACKAGE`, `WORKSPACE_COMPARTMENT`, `LOT_LINE_SPLIT`).
- `pce_start_intake` / `pce_seal_charter`: Empirical workspace/binary/SDK `RealityProbeEvidence` capture, 3-axis formal elicitation (`domain_ontology`, `hazard_mutation_policy`, `scope_and_exclusions`), and SHA-256-sealed phased `ProjectCharter` permit queue generation.
- `jbc_query_ast_graph`: Deterministic polyglot AST/CST import/topological-layer map, cross-module symbol call-site lookup (`caller_of`), and exact line-slice extraction (`symbol_query`).
- `jbc_list_codes` / `jbc_get_code`: On-demand statutory catalog and verbatim ISO/IEEE/CWE/CERT lineage lookup across all 32 `JBC` provisions.
- `jbc_verify_behavior_graph`: Pillar 1 `BehaviorGraph` transition-matrix and Kildall monotone dataflow verifier.
- `jbc_audit_dossier`: Pillar 3 `EngineeringDossier` 5-phase `PCE` auditor (verifying symbol grounding, write-scope confinement, dual-digest `CertificateOfOccupancy` seal, and optional `target_blueprint` `LVS` equivalence).

## Multi-Model Specialist Routing & Scheme-Routed Secret Providers (`secrets.py`)

`jezz` supports role-tiered model routing across all 12 generative `PceModelSuite` / `SpecialistRoleSlot` slots (`frame`, `decompose`, `inspect`, `inspect_env`, `consult`, `curate_memory`, `specify`, `provision`, `instrument`, `synthesize`, `refactor`, `hypothesize`) via `[tool.jezz.models]` in `pyproject.toml` and CLI flags (`--architect-model`, `--builder-model`, `--inspector-model`, `--model`).

Per-provider API credentials resolve through an immutable `SecretProviderRegistry` (`src/jezz/secrets.py`) into `pydantic.SecretStr` without mutating `os.environ` (`JBC-SEC-002`, `JBC-OBS-003`), supporting four built-in URI schemes (`env://`, `pass://`, `op://`, `keychain://`) and 4-tier precedence (`--secret-uri` $\to$ workspace `[tool.jezz.secrets]` $\to$ user `config.toml` under `platformdirs.user_config_path("jezz")` $\to$ canonical provider environment variables `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`):

```toml
# Workspace routing in pyproject.toml:
[tool.jezz.models]
default = "google:gemini-3.8-flash"
prompt_cache = "auto" # auto | off | prefix-only | 5m | 30m | 1h | 12h
# architect = "antigravity:claude-opus-5-5-medium"   # zero API key via local agy CLI
# builder = "antigravity:claude-sonnet-5-5-medium"

[tool.jezz.gates]
timeout_sec = 120.0 # (0.0, 600.0] per-subprocess gate watchdog (overridden by --gate-timeout)

# User-scope credential mappings (e.g. ~/.config/jezz/config.toml or ~/Library/Application Support/jezz/config.toml):
# [secrets]
# agent_reason = "jezz PCE runtime resolving model API credential"
# [secrets.providers]
# google = "env://GEMINI_API_KEY"                        # or "pass://Vault/Item/Field"
# anthropic = "op://Engineering/Anthropic/credential"    # or "keychain://jezz/anthropic"
```

## Reusable GitHub Action (`action.yml`)

```yaml
- name: Enforce JBC Edition 1.3 Differential Occupancy Ratchet
  uses: futileandstupid/jezz@v0.5.0
  with:
    working-directory: "."
    mode: "differential" # differential | full | both
    profile: "standard"  # standard | strict_internal
```

## Verification Gates

```bash
uv run pytest --cov=jezz --cov-fail-under=92
uv run mypy .
uv run ruff check .
uv run ruff format --check .
uv run jezz inspect .
npm --prefix packages/jezz-blueprint run typecheck && npm --prefix packages/jezz-blueprint test
```

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
