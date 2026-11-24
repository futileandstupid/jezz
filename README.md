# `jezz` — Licensure-Grade Software Engineering & Coding-Agent Framework

> **Technical White Paper:** **[Permit-and-Contract Engineering: Licensure-Grade Software Building Codes, Behavioral Control-Flow Verification, and Empirical Evaluation of Coding Agents](docs/whitepaper.md)**

`jezz` is an experimental software engineering and coding-agent framework structured around four integrated pillars:

1. **[Pillar 1 — Complete Agent Behavior Basis (`jezz.behaviors`)](docs/pillar-1-behavior-basis.md):**
   An exhaustive taxonomy of atomic coding-agent behaviors with Fibonacci cardinality ($F_8 = 21$, `BHV-01`..`BHV-21`), local state-machine transition legality, and Kildall monotone dataflow verification.
2. **[Pillar 2 — Enforceable Software Building Codes (`jezz.codes`)](docs/pillar-2-building-codes.md):**
   The **Jezz Software Building Code (`JBC` Edition 1.2)**—31 machine-checkable provisions across 6 Articles (`DBC`, `FLT`, `SEC`, `COG`, `OBS`, `VRF`), enforced across **Python (`.py`), TypeScript (`.ts`/`.tsx`), Rust (`.rs`), and Go (`.go`)** via `LanguageAdapterPort`, ecosystem manifest grounding (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`), `CodePermit`, `CodeVariance`, Rough-In & Final `BuildingInspector` gates, and SHA-256 `CertificateOfOccupancy` seals.
3. **Pillar 3 — SDD Forensic Teardown & Permit-and-Contract Engineering (`jezz.methodology`):**
   - **[Pillar 3A — Forensic Teardown of Industry "SDD"](docs/pillar-3a-sdd-teardown.md):** Primary-source critique of GitHub `spec-kit`, Fission `OpenSpec`, and AWS Kiro.
   - **[Pillar 3B — Permit-and-Contract Engineering (`PCE`)](docs/pillar-3b-pce-methodology.md):** A 5-phase, anti-Agile, anti-theatre engineering process (`Mandate` $\to$ `Permit` $\to$ `Contract` $\to$ `Construction` $\to$ `Occupancy`) audited deterministically by `audit_pce_dossier`.
4. **[Pillar 4 — Pydantic AI & Pydantic-Graph Runtime (`jezz.agents`)](docs/pillar-4-pydantic-ai-runtime.md):**
   Polyglot executable agent runtime enacting all $F_8 = 21$ (`100%`) behaviors across **Python, TypeScript, Rust, and Go** as typed `AtomicBehaviorGraphNode` vertices, combining least-privilege `WorkspaceSandbox` toolsets, `WorkspaceAstGraph` & installed-package API introspection, permit-gated `BHV-13 PROVISION` (`uv add`, `npm install`, `cargo add`, `go get`), real-compiler `PolyglotSubprocessGateRunner` gates (`mypy`/`pytest`, `tsc`/`vitest`/`node --test`, `cargo check`/`cargo test`, `go vet`/`go test`), persistent `.jezz/memory.json` longitudinal memory (`BHV-07 CURATE_MEMORY`), AST-grounded topological DAG planning (`DECOMPOSE`), parallel `asyncio.TaskGroup` `INSTRUMENT` and multi-wave `DELEGATE`/`RECONCILE`, mandatory `RED_TAG` $\to$ `REVERT` $\to$ `HYPOTHESIZE` $\to$ `CURATE_MEMORY` self-healing, and full `PCE` Dossier certification.

## Empirical Validation Roadmap
- **[Empirical Validation & Longitudinal Testing Roadmap](docs/empirical-testing-roadmap.md):** CLI/MCP/Stop-hook harness, zero-cost brownfield `BuildingInspector` field trials, live-LLM `PceWorkflowOrchestrator` occupancy probes, controlled `pydantic-evals` A/B benchmarks against prose "SDD", and longitudinal multi-mandate crucible validation.
- **[Track 2 — Brownfield Inspector Field Trial Report](docs/track-2-brownfield-field-trial.md):** Empirical audit across 6 repositories totaling 17,609 files / 7,414,726 LOC (`github/spec-kit`, `pydantic`, `pydantic-graph`, `pydantic-deepagents`, `KiroCrew`, and AWS Kiro E2E sample) and resulting engine/AST calibrations.
- **[Track 3 — First Live-Model Occupancy Trial Report](docs/track-3-live-occupancy-trial.md):** End-to-end `PceWorkflowOrchestrator` + `UvSubprocessGateRunner` trials on `google:gemini-3.8-flash` across ambiguity halting (`FRAME -> ELICIT`), adversarial integer-cent synthesis (`SPECIFY -> INSTRUMENT -> SYNTHESIZE`), and 3-worker parallel `TaskGroup` construction (`DELEGATE -> RECONCILE`).
- **[Track 4 — Controlled `pydantic-evals` A/B Benchmark Report](docs/track-4-sdd-vs-pce-benchmark.md):** 46-workspace live A/B evaluation across Waves 4A–4E (`Control: Prose SDD` vs. `Treatment: Jezz PCE` on uniform `google:gemini-3.8-flash` and symmetric tiered `google:gemini-4-hydrogen` + `google:gemini-3.8-flash`) achieving `6/6` (`100%`) task occupancy closure on both uniform and tiered routing, `0.0%` silent defect escape (`36/36` held-out `Hypothesis` checks across Waves 4B–4E), and `-48.5%` tiered latency against `375` unwaived `JBC` violations (`36` `LIFE_SAFETY`, `0/23` occupiable) in `23/23` checked-off `- [x]` SDD builds.
- **[Track 5 — Multi-Mandate Longitudinal Crucible & `JBC` Edition `1.2` Report](docs/track-5-multi-mandate-crucible.md):** 4-mandate longitudinal build of `jezz-crucible-ledger` (`11` units / `2,693` LOC across domain algebra, parameterized savepoint SQLite store, 2-wave topological DAG escrow + SHA-256 reconciliation, and brownfield CLI renovation under live `jezz hook stop` gates), 8 runtime/introspection calibrations, and `JBC-SEC-006` dynamic path anchoring.
- **[Track 6 — Full $F_8 = 21$ Behavior Runtime Closure & Persistent Memory](docs/track-6-full-basis-runtime.md):** Complete `21 / 21` (`100%`) `AtomicBehaviorGraphNode` runtime coverage (`InspectEnvNode`, `ConsultNode`, `CurateMemoryNode`, `ProvisionNode`), rollback-safe `ProvisionPort` (`uv add`) + `BuildingInspector` manifest reload, symmetric `12`-slot specialist routing, and cross-mandate `.jezz/memory.json` continuity.
- **[Track 7 — Token Cost Compression & Cross-Provider Generalizability](docs/track-7-prompt-caching-and-providers.md):** Enabled-by-default cross-provider prompt caching (`PromptCacheMode`, static `InstructionPart` canonicalization, provider-aware `CachePoint` slice partitioning), `448`-probe offline wire compatibility across Google/Anthropic/OpenAI/OpenRouter, repeatable `jezz benchmark --suite` matrix evaluation, and live Gemini Waves 7A–7B (`10` workspace builds) achieving `68.7%–72.5%` `PCE` cache hit ratios (`-61.8%` to `-65.2%` effective billed-input compression vs. `0.0%` on open-loop SDD).
- **[Track 8 — Polyglot `JBC` Inspector & Six-Repository Field Trial](docs/track-8-polyglot-inspector.md):** Hexagonal `LanguageAdapterPort` + official `tree-sitter` CST backends extending `JBC` Edition `1.2`, `WorkspaceAstGraph`, CLI/MCP/Stop-hook ratchets, and `PCE` dossier seals across Python (`.py`), TypeScript (`.ts`/`.tsx`), Rust (`.rs`), and Go (`.go`), validated on a 4-language `PCE` crucible and `6` open-source repositories (`zod`, `hono`, `ripgrep`, `serde_json`, `chi`, `cobra` — `1,110` units / `300,067` LOC, `0` crashes).
- **[Track 9 — Polyglot Autonomous `PCE` Runtime (`jezz run` & `jezz renovate`)](docs/track-9-polyglot-pce-runtime.md):** Full 4-language `PceWorkflowOrchestrator` closure (`PolyglotSubprocessGateRunner`, `PolyglotSubprocessProvisionRunner`, universal `all(...)` `JBC-VRF-005`/`006` conjunction, fail-closed zero-test `cargo test`/`go test` guards, and language-scoped `JBC` briefs), achieving `4 / 4` (`100%`) Pass-1 `OCCUPANCY_CERTIFIED` & `OCCUPANCY_SEALED` live builds across TypeScript (`tsc` + `node --test`), Rust (`cargo check` + `cargo test`), Go (`go vet` + `go test`), and brownfield Rust `renovate` in `11–19` requests and `47.7K–82.5K` gross input tokens (`~4x–5x` native-idiom token compression vs. Python).
- **[Track 10 — Zero-Credential `antigravity:<model>` Adapter & Cross-Provider Empirical Benchmark](docs/track-10-cross-provider-benchmark.md):** Zero-API-key `AntigravityCliModel` (`antigravity:claude-opus-5-5-medium`, `antigravity:claude-sonnet-5-5-medium`, `antigravity:gpt-oss-120b-medium`), `640 / 640` offline cross-provider wire probes, and `19` live workspace builds (`Waves 10A & 10B`) confirming Wave 4D's "Over-Specified Prose Drift" on Anthropic Claude (`Opus 5.5` bloated SDD specs `+80.5%` and *increased* `JBC` violations `+34.3%`, `0 / 8` occupiable across all SDD builds) while `jezz` `PCE` achieved `100%` `JBC` v1.2 occupancy across Claude (`Sonnet 5.5` and `Opus 5.5 + Sonnet 5.5`), `GPT-OSS 120B`, and Gemini (`-34.0%` faster than SDD on tiered Claude with `49.6%–57.8%` cache hit ratios).
- **[Track 11 — External Brownfield OSS Renovation Crucible (`jezz renovate --ratchet`)](docs/track-11-brownfield-renovation-crucible.md):** Differential Final Occupancy Ratchet (`inspect_differential_final`, `--ratchet`, `ratchet_scoped`), monorepo unit/preview calibrations (`Calibrations 11A–11C`), and `3 / 3` (`100%`) certified & `OCCUPANCY_SEALED` surgical renovations across external Go (`go-chi/chi`), Rust (`serde-rs/json`, `APPROVED_WITH_VARIANCES`), and Python (`github/spec-kit` — `291,626` LOC, `76.9%–79.8%` cache hit ratios).
- **[Track 12 — Turnkey Distribution, Reusable CI Gate, & `v0.2.0` Release Packaging](docs/track-12-distribution-and-ci-gate.md):** Reusable composite GitHub Action (`action.yml`, dogfooded in `.github/workflows/jbc-gate.yml`) enforcing polyglot `jezz inspect` ratchets and `jezz audit` dossier seals with zero raw `${{ inputs.* }}` shell interpolation and zero hardcoded paths (`JBC-SEC-006`).

## CLI Usage

```bash
# Run polyglot JBC v1.2 Rough-In inspection (.py, .ts, .tsx, .rs, .go; exit 0 = APPROVED, 1 = RED_TAG)
uv run jezz inspect <repo_path> [--language python|typescript|rust|go] [--permit permit.json] [--as-of YYYY-MM-DD] [--format markdown|json]

# Differential Occupancy Ratchet: gate only Git-modified or staged source files (plus whole-repo import cycles)
uv run jezz inspect <repo_path> (--changed-since origin/main | --staged) [--language ...] [--permit permit.json]

# Audit a 5-phase PCE EngineeringDossier against compiled workspace symbols and SHA-256 digest
uv run jezz audit <dossier.json> [--repo <repo_path>] [--format markdown|json]

# Bootstrap Antigravity plugin (MCP + Stop hook + PCE skill + rules), [tool.jezz.models], & optional pre-commit ratchet
uv run jezz init [repo_path] [--plugin-scope workspace|global|none] [--pre-commit] [--overwrite]

# Execute a greenfield PCE prompt or mandate (interactive TTY ELICIT + live progress + optional --plan-only review)
uv run jezz run <repo_path> [--model <provider:model>] [--architect-model <provider:model>] [--prompt-cache auto|off|5m|1h] (--prompt "..." | --mandate mandate.json | --resume-elicitation elicit.json --clarification "...") [--plan-only --write-mandate mandate.json] [--interactive] [--progress]

# Renovate an existing brownfield repository (or scoped slice via --ratchet) to JBC v1.2 occupancy via BHV-17 REFACTOR
uv run jezz renovate <repo_path> [--ratchet] [--permit permit.json] [--model <provider:model>] [--builder-model <provider:model>] [--prompt-cache auto|off|5m|1h] (--prompt "..." | --mandate mandate.json) [--plan-only --write-mandate mandate.json] [--write-dossier dossier.json]

# Execute controlled pydantic-evals A/B benchmark (single suite or multi-provider / cache-ablation --suite matrix)
uv run jezz benchmark <work_dir> [--model <provider:model> | --suite <label>=<model>[@<cache>]] [--prompt-cache auto|off|5m|1h] [--max-concurrency 2] [--write-json ab.json]

# Launch the read-only JBC v1.2 & PCE statutory MCP server or Antigravity Stop lifecycle gate over stdio
uv run jezz mcp [--root <repo_path>]
uv run jezz hook stop [--root <repo_path>] [--max-continuations 3] [--require-marker]
```

## Statutory MCP Gate Server (`jezz mcp`)

External interactive coding agents (Antigravity, Claude Code, Cursor, Codex, JetBrains AI) can mount `jezz` as a zero-mutation, zero-shell `stdio` MCP server (`src/jezz/mcp_server.py`), exposing 8 structured tools jailed inside `--root` via `ContainedPath` (`JBC-SEC-005`):

- `jbc_preview_source`: Pre-write in-memory Rough-In inspection of an unsaved `.py`, `.ts`, `.tsx`, `.rs`, or `.go` buffer overlaid onto the workspace module graph (catching both per-unit violations and cross-module `JBC-COG-003` circular imports / `_private` / `/internal/` leaks before touching disk).
- `jbc_inspect_differential`: Git-aware polyglot Differential Occupancy Ratchet (`--changed-since`, defaulting to `HEAD`, or `staged=True`).
- `jbc_inspect_workspace`: Full repository `JBC` Edition 1.2 Rough-In inspection across `.py`, `.ts`, `.tsx`, `.rs`, and `.go` units with `CodePermit` and sunset `as_of` support.
- `jbc_query_ast_graph`: Deterministic polyglot AST/CST import/topological-layer map, cross-module symbol call-site lookup (`caller_of`), and exact line-slice extraction (`symbol_query`).
- `jbc_list_codes` / `jbc_get_code`: On-demand statutory catalog and verbatim ISO/IEEE/CWE/CERT lineage lookup across all 31 `JBC` provisions.
- `jbc_verify_behavior_graph`: Pillar 1 `BehaviorGraph` transition-matrix and Kildall monotone dataflow verifier.
- `jbc_audit_dossier`: Pillar 3 `EngineeringDossier` 5-phase Permit-and-Contract Engineering (`PCE`) auditor (accepting `dossier_path` or fileless `dossier_json`).

## Multi-Model Specialist Routing & Proton Pass CLI (`pass-cli`) Secrets

`jezz` supports role-tiered model routing across all 12 generative `PceModelSuite` / `SpecialistRoleSlot` slots (`frame`, `decompose`, `inspect`, `inspect_env`, `consult`, `curate_memory`, `specify`, `provision`, `instrument`, `synthesize`, `refactor`, `hypothesize`) via `[tool.jezz.models]` in `pyproject.toml` and CLI flags (`--architect-model`, `--builder-model`, `--inspector-model`, `--model`), resolving per-provider credentials once per provider family from Proton Pass CLI (`pass-cli item view pass://<vault>/<item>/<field>`) into `pydantic.SecretStr` without mutating `os.environ` (`JBC-SEC-002`, `JBC-OBS-003`).

```toml
# Project-level specialist routing and Proton Pass vault mapping in pyproject.toml:
[tool.jezz.models]
default = "google:gemini-3.8-flash"
prompt_cache = "auto" # auto | off | prefix-only | 5m | 30m | 1h | 12h
# architect = "google:gemini-4-hydrogen"             # or "antigravity:claude-opus-5-5-medium" (zero API key)
# builder = "google:gemini-3.8-flash"                # or "antigravity:claude-sonnet-5-5-medium" / "antigravity:gpt-oss-120b-medium"
# inspector = "google:gemini-3.8-flash"              # INSPECT_CODE, INSPECT_ENV, CONSULT, CURATE_MEMORY

# [tool.jezz.models.roles]
# hypothesize = "antigravity:claude-opus-5-5-medium"

[tool.jezz.secrets]
agent_reason = "jezz PCE runtime resolving model API credential"

[tool.jezz.secrets.providers]
google = "pass://AI-Keys/Gemini Developer API/API Key"
```

Models prefixed with `antigravity:<slug>` execute through the local Antigravity CLI (`AntigravityCliModel`, preserving multi-turn `--conversation <id>` prefix caches) with zero external API keys or provider accounts. When authenticated with a Proton Pass **AI Agent token** (`PROTON_PASS_PERSONAL_ACCESS_TOKEN`), every vault read transmits `PROTON_PASS_AGENT_REASON` (configurable via `[tool.jezz.secrets].agent_reason` or `--agent-reason`) to Proton Pass's server-side audit log.

## Reusable GitHub Action (`action.yml`)

```yaml
- name: Enforce JBC Edition 1.2 Differential Occupancy Ratchet
  uses: futileandstupid/jezz@v0.2.0
  with:
    working-directory: "."
    mode: "differential" # differential | full | both
```

## Verification Gates

```bash
uv run pytest
uv run mypy .
uv run ruff check .
uv run ruff format --check .
```
