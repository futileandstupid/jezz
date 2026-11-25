# Empirical Validation & Longitudinal Testing Roadmap

With Pillars 1–4 implemented, self-audited, and passing (`jezz.behaviors`, `jezz.codes`, `jezz.methodology`, `jezz.agents`), this roadmap tracks progressive empirical validation and architectural hardening stages evaluating `jezz` against brownfield Python and polyglot (TypeScript, Rust, Go) repositories, live single- and multi-mandate LLM workflows across all four languages, industry "Spec-Driven Development" ("SDD") baselines, full $F_8 = 21$ runtime closure, and cross-provider prompt-caching compression.

---

## Track 1 — CLI, MCP, & Antigravity Lifecycle Harness (`jezz inspect` / `audit` / `run` / `renovate` / `init` / `mcp` / `hook`) — *Completed*

**Goal:** Expose `BuildingInspector`, `audit_pce_dossier`, `PceWorkflowOrchestrator`, the read-only `stdio` MCP gate, and the Antigravity `Stop` lifecycle hook via a zero-variance CLI entrypoint (`pyproject.toml` `[project.scripts]` -> `src/jezz/cli.py`).

- **Commands:**
  - `uv run jezz inspect <repo_path> [--changed-since <ref> | --staged]` — Run full or differential Rough-In (`JBC` Edition `1.2`, `29` static / `31` total provisions) inspection (`0` on `APPROVED` / `APPROVED_WITH_VARIANCES`, `1` on `RED_TAG`, `2` on input error).
  - `uv run jezz audit <dossier_json> [--repo <repo_path>]` — Verify all five `PCE` phases and SHA-256 `CertificateOfOccupancy` seals (including `ratchet_scoped` brownfield slices) via `audit_pce_dossier`.
  - `uv run jezz run` / `uv run jezz renovate [--ratchet]` — Execute greenfield or brownfield `PceWorkflowOrchestrator` workflows (supporting `--plan-only`, `--write-mandate`, `--interactive`, `--progress`, zero-credential `antigravity:<slug>` routing, and role-tiered `[tool.jezz.models]` + Proton Pass `pass-cli` secret resolution).
  - `uv run jezz init` / `uv run jezz mcp` / `uv run jezz hook stop` — Install portable `${PLUGIN_ROOT}`-anchored Antigravity plugins (`JBC-SEC-006`) and enforce turn-boundary Differential Occupancy Ratchets.
- **Status:** Verified (`src/jezz/cli.py`, `src/jezz/cli_interactive.py`, `src/jezz/mcp_server.py`, `src/jezz/hooks.py`, `src/jezz/plugin.py`) with zero `JBC` v1.2 variances.

---

## Track 2 — Brownfield Inspector Field Trial (Zero LLM Cost) — *Completed (Waves 2A & 2B)*

**Goal:** Stress-test the 28 static `JBC` Edition 1.1 AST/lexical/graph rules against **6 real-world Python repositories** (`github/spec-kit`, `pydantic-graph`, `vstorm-co/pydantic-deepagents`, `pydantic/pydantic`, `kirodotdev/KiroCrew`, and `aws-samples/sample-e2e-product-development-with-kiro` — **17,609 files / 7,414,726 LOC**) to calibrate rule precision and monorepo scaling before live agent benchmarking.

- **Report & Calibrations:** [track-2-brownfield-field-trial.md](track-2-brownfield-field-trial.md) — Upgraded `JBC-COG-003` cycle detection to $O(V + E)$ iterative Tarjan SCC, added `setup.cfg` + PEP 621 feature-extra manifest parsing, singular `test/` suite discovery, stdlib `ast.NodeVisitor` idiom awareness, and 7 AST precision refinements with zero crashes across 7.41M LOC.

---

## Track 3 — First Live-Model Occupancy Trial (`PceWorkflowOrchestrator`) — *Completed*

**Goal:** Connect a live LLM provider (`google:gemini-3.8-flash` via `pydantic-ai` v2.71.0) + `UvSubprocessGateRunner` (`pytest` + `Hypothesis` + `mypy --strict`) to `PceWorkflowOrchestrator` inside isolated scratch workspaces across three diagnostic probes:

1. **Probe 3A — The Ambiguity Trap (`BHV-01 FRAME -> BHV-02 ELICIT`):**
   - **Result:** Passed (`HALTED_FOR_ELICITATION` in `2` requests / `1` tool call, surfacing 4 concrete webhook retry/persistence/timeout/idempotency ambiguities with zero disk mutations).
2. **Probe 3B — Adversarial Domain Synthesis (`SPECIFY -> INSTRUMENT -> SYNTHESIZE`):**
   - **Result:** Passed (`OCCUPANCY_CERTIFIED` on `MND-FEE-LEDGER-3B`, `0` violations across all 30 `JBC` v1.1 codes, `0` Tier-2 `RED_TAG` rollbacks due to Tier-1 `_tool_preview_rough_in` + `ModelRetry` absorption, sealed `sha256:739814a0...`).
3. **Probe 3C — Multi-Scope Parallel Construction (`BHV-10 DELEGATE -> BHV-11 RECONCILE`):**
   - **Result:** Passed (`OCCUPANCY_CERTIFIED` on `MND-ESCROW-GRID-3C` across 3 concurrent `asyncio.TaskGroup` workers with zero signature drift or write-set collisions, sealed `sha256:5cf5aad1...`).
- **Report & Runtime Calibrations:** [track-3-live-occupancy-trial.md](track-3-live-occupancy-trial.md).

---

## Track 4 — Controlled A/B Benchmark via `pydantic-evals` (Prose "SDD" vs. `jezz` `PCE`) — *Completed (Waves 4A–4E)*

**Goal:** Quantitatively test Pillar 3A's core thesis using `pydantic-evals` over 6 adversarial benchmark tasks (`46` live end-to-end workspace builds across Waves 4A–4E on uniform `google:gemini-3.8-flash` and symmetric tiered `google:gemini-4-hydrogen` + `google:gemini-3.8-flash`) seeded with classic LLM failure modes (SQL injection sinks, IEEE-754 $>2^{53}$ float corruption, path/symlink escapes, silent exception swallowing, unbounded loops, class duty creep, and multi-scope state transitions).

- **Experimental Arms (Matched Models & Workspace Manifests):**
  - **Control Arm (Marketed Prose SDD):** Steelmanned 7-article `constitution.md` + `spec.md` $\to$ `design.md` $\to$ `tasks.md` $\to$ implementation + `- [x]` checklist (`jezz.evals.sdd_baseline`).
  - **Treatment Arm (`jezz` `PCE` + `JBC` v1.1 / v1.2):** Matched `EngineeringMandate` executed through `PceWorkflowOrchestrator` + `UvSubprocessGateRunner`.
- **Headline Empirical Findings ([track-4-sdd-vs-pce-benchmark.md](track-4-sdd-vs-pce-benchmark.md)):**
  1. **100% Suite Closure on Both Uniform & Tiered Routing with Zero Silent Defect Escape:** Marketed SDD passed `OpenSpec`/Kiro Markdown validation (`[x]` + `SHALL` + `Scenario`) on **`23/23` (`100%`)** of runs while achieving **`0/23` (`0%`)** `JBC` occupancy (`36` `LIFE_SAFETY` violations / `375` total violations), failing `mypy --strict` on `56.5%` (`13/23`), failing its own `pytest` suites on `39.1%` (`9/23`), and escaping with held-out oracle crashes on **`4/23` (`17.4%`)**. Conversely, `jezz` `PCE` achieved **`6/6` (`100%`) task closure** on both uniform `gemini-3.8-flash` (Waves 4B+4C) and symmetric tiered `gemini-4-hydrogen` + `gemini-3.8-flash` (Waves 4D+4E), with **`0` unwaived `JBC` violations** across all `15` certified builds and **`100%` (`12/12` post-calibration builds = `36/36` checks) held-out `Hypothesis` oracle survival (`0.0%` silent defect escape)**.
  2. **Over-Specified Prose Drift vs. Executable Contract Leverage:** Upgrading SDD's Architect to frontier `gemini-4-hydrogen` (`Wave 4D`) *increased* `JBC` violations by `+61.0%` (`3.8x` more `LIFE_SAFETY` defects), whereas channeling `gemini-4-hydrogen` into compiled Phase III `PCE` contracts cut mean latency by `-48.5%` and requests by `-37.1%`.

---

## Track 5 — Multi-Mandate Longitudinal Crucible (`jezz-crucible-ledger`) & `JBC` Edition `1.2` — *Completed*

**Goal:** Stress-test `jezz` longitudinally across 4 sequential, interdependent mandates in a single repository (**`jezz-crucible-ledger`** — Double-Entry Integer-Cent Ledger & Escrow Engine, `11` Python units / `2,693` LOC) spanning greenfield `--mandate`, raw `--prompt` with AST-grounded `DECOMPOSE`, 2-scope / 2-wave topological DAG `DELEGATE -> RECONCILE`, interactive co-engineering + brownfield `jezz renovate` (`BHV-17 REFACTOR`), and live `jezz hook stop` enforcement.

- **Report & Calibrations:** [track-5-multi-mandate-crucible.md](track-5-multi-mandate-crucible.md) — Sealed all 4 sequential `EngineeringDossier`s with `0` unwaived `JBC` violations and `0` reverts (`21/21` `pytest` + `Hypothesis`, `mypy --strict` clean); landed 8 runtime/introspection calibrations (including parallel `InstrumentNode`, read-only upstream AST signature injection, test-only `mypy` remediation routing, and class-method / `TypeAliasType` package introspection closing `Wave 4E`); and codified **`JBC-SEC-006` (Edition `1.2` — `31` provisions)** banning hardcoded paths across code and generated plugins.

---

## Track 6 — Full $F_8 = 21$ Behavior Runtime Closure & Persistent Workspace Memory — *Completed*

**Goal:** Close the remaining 4 `BehaviorKind`s (`BHV-05 INSPECT_ENV`, `BHV-06 CONSULT`, `BHV-07 CURATE_MEMORY`, `BHV-13 PROVISION`) as first-class `AtomicBehaviorGraphNode` subclasses in `PceWorkflowOrchestrator`, achieving **`21 / 21` (`100%`) runtime basis coverage**, rollback-safe dependency provisioning (`uv add` + `BuildingInspector` manifest reload), symmetric `12`-slot specialist routing, and cross-mandate `.jezz/memory.json` persistence.

- **Report & Verification:** [track-6-full-basis-runtime.md](track-6-full-basis-runtime.md) — Verified `21/21` node bijection (`validate_runtime_node_coverage`), composite 21-behavior Kildall validity, PEP 508 flag/URL/path injection blocks, atomic `pyproject.toml`/`uv.lock` rollback, cross-`.venv` smoke-import discovery, and non-lossy `falsified_paths` preservation across sequential mandates (`tests/test_track6_full_basis_runtime.py`).

---

## Track 7 — Token Cost Compression & Cross-Provider Generalizability — *Completed (`Waves 7A & 7B`)*

**Goal:** Compress `PCE`'s statutory prompt overhead via unified cross-provider prompt caching (`PromptCacheMode.AUTO` enabled by default) and establish continuous multi-provider wire/schema compatibility and matrix benchmarking across Google Gemini, Anthropic Claude, OpenAI (Chat & Responses), and OpenRouter.

- **Report & Verification:** [track-7-prompt-caching-and-providers.md](track-7-prompt-caching-and-providers.md) — Canonicalized static `InstructionPart` prefixes (`JBC_STATUTORY_BRIEF`, `JBC_CANONICAL_EXEMPLAR`) across all `4` `JBC` construction specialists (and symmetric `SDD_CONSTITUTION_MD` across all `4` Control SDD stages), partitioned shared `Mandate + Snapshots` prefixes via `CachePoint` with provider-aware `normalize_prompt_for_model` coalescing on implicit-prefix models, verified **`448 / 448` offline cross-provider wire probes** (`16` agents $\times$ `7` provider models $\times$ `4` cache modes), and executed **`10` live Gemini workspace builds (`Waves 7A & 7B`)** proving **`68.7%–72.5%` `PCE` cache hit ratios** (`-61.8%` to `-65.2%` effective billed-input compression, vs. **`0.0%`** cache hits on open-loop Control SDD) with `100%` post-calibration `JBC` v1.2 occupancy and oracle survival.

---

## Track 8 — Polyglot `JBC` Inspector (`Python`, `TypeScript`, `Rust`, & `Go`) — *Completed*

**Goal:** Generalize `JBC` Edition `1.2` static inspection, `WorkspaceAstGraph`, ecosystem manifest grounding, CLI/MCP/Stop-hook Differential Occupancy Ratchets, and `PCE` `EngineeringDossier` seal auditing across **Python (`.py`), TypeScript (`.ts`, `.tsx`), Rust (`.rs`), and Go (`.go`)** via a hexagonal `LanguageAdapterPort` and official C-extension `tree-sitter` grammars, validated on a 4-language `PCE` crucible and real-world brownfield repositories.

- **Report & Calibrations:** [track-8-polyglot-inspector.md](track-8-polyglot-inspector.md) — Implemented `PolyglotUnit` / `InspectedUnit`, `package.json` (monorepo `packages/*`/`apps/*` + `@types/*`), `Cargo.toml` (`[workspace.members]`), and `go.mod` manifest loaders (`JBC-VRF-003`), universal CST complexity/`LCOM4`/Tarjan SCC evaluators, and idiomatic TypeScript/Rust/Go hazard scanners; verified `OCCUPANCY_SEALED` on a 4-language `PCE` crucible (`tests/test_track8_polyglot_inspector.py`); and audited **6 open-source repositories (`colinhacks/zod`, `honojs/hono`, `BurntSushi/ripgrep`, `serde-rs/json`, `go-chi/chi`, `spf13/cobra` — `1,110` units / `300,067` LOC / `15,936` AST/CST symbols)** in `62.95s` (`0` crashes, `0` CST parse errors, `6,926` calibrated violations after Calibrations `8A–8D`).

---

## Track 9 — Polyglot Autonomous `PCE` Runtime (`jezz run` & `jezz renovate` Across Python, TypeScript, Rust, & Go) — *Completed*

**Goal:** Extend Pillar 4 (`PceWorkflowOrchestrator`, `WorkspaceSandbox`, `DynamicGatePort`, `ProvisionPort`, and specialist prompts/validators) so `jezz run` and `jezz renovate` execute closed-loop autonomous construction, real static compiler / test verification, and SHA-256 `CertificateOfOccupancy` sealing across **Python, TypeScript, Rust, and Go**.

- **Report & Live Empirical Trial:** [track-9-polyglot-pce-runtime.md](track-9-polyglot-pce-runtime.md) — Added `PolyglotSubprocessGateRunner` (`tsc --noEmit` + `vitest`/`node --test`, `cargo check --all-targets` + `cargo test`, `go vet ./...` + `go test -v ./...` with fail-closed zero-test guards), `PolyglotSubprocessProvisionRunner` (`uv add`, `npm install`, `cargo add`, `go get`), universal `all(...)` `JBC-VRF-005`/`006` conjunction, and language-routed `JBC` briefs (`polyglot_prompts.py` preserving Python `InstructionPart` byte-identity); verified 3-language parallel `DELEGATE -> RECONCILE` (`tests/test_track9_polyglot_runtime.py`); and achieved **`4 / 4` (`100%`) Pass-1 `OCCUPANCY_CERTIFIED` & `OCCUPANCY_SEALED`** on live tiered Gemini builds (`Probes 9A–9D`: TypeScript greenfield, Rust greenfield, Go greenfield, and brownfield Rust `renovate`) with **`0` rollbacks**, `26.5%–57.3%` prompt-cache hit ratios, and **`47.7K–82.5K` gross input tokens** (`~4x–5x` native-idiom token compression vs. Python).

---

## Track 10 — Zero-Credential `antigravity:<model>` Provider & Cross-Provider Empirical Benchmark (`Claude Opus 5.5`, `Claude Sonnet 5.5`, `GPT-OSS 120B`, & `Gemini`) — *Completed (`Waves 10A & 10B`)*

**Goal:** Enable zero-credential cross-provider execution across Anthropic Claude and OpenAI open-weight reasoning models via the local Antigravity CLI (`antigravity:<slug>` -> `AntigravityCliModel`), and empirically test whether Track 4's SDD vs. `PCE` divergence and Wave 4D's "Over-Specified Prose Drift" hold outside Google Gemini.

- **Report & Live Benchmark:** [track-10-cross-provider-benchmark.md](track-10-cross-provider-benchmark.md) — Implemented `AntigravityCliModel` (`src/jezz/agents/agy_model.py`, preserving multi-turn `--conversation <id>` prefix caches across `TlsResilientModel`), verified **`640 / 640` offline cross-provider wire probes** (`10` provider models), and executed **`19` live workspace builds (`Waves 10A & 10B`)**: Control SDD achieved `8/8` (`100%`) Markdown `[x]` compliance and **`0/8` (`0%`) `JBC` occupancy (`192` violations, `17` `LIFE_SAFETY`)**, with upgrading SDD's Architect from `Claude Sonnet 5.5` to `Claude Opus 5.5` bloating specs `+80.5%` and **increasing `JBC` violations `+34.3%`** (`+124.5%` latency); conversely, `jezz` `PCE` achieved **`100%` `JBC` v1.2 occupancy** across `Claude Sonnet 5.5`, `Claude Opus 5.5 + Sonnet 5.5` (`100%` `6/6` oracle pass post-`Calibration 10A`, **`-34.0%` faster than SDD**, `49.6%–57.8%` cache hit ratios), `GPT-OSS 120B` (`42.2%–56.3%` cache hits post-`Calibration 10B`), and tiered `Gemini`.

---

## Track 11 — External Brownfield OSS Renovation Crucible (`jezz renovate --ratchet`) — *Completed*

**Goal:** Enable surgical brownfield renovation inside large legacy repositories without requiring whole-monorepo remediation (`inspect_differential_final`, `jezz renovate --ratchet`, `EngineeringDossier.ratchet_scoped`), and validate live autonomous renovation across external open-source Go, Rust, and Python repositories.

- **Report & Crucible Results:** [track-11-brownfield-renovation-crucible.md](track-11-brownfield-renovation-crucible.md) — Applied `Calibrations 11A–11C` (`100,000`-unit monorepo ceilings, safe `ValidationError` formatting, Rust inline-`mod { use super::*; }` scope resolution, `@functools.lru_cache` unit memoization, and differential Rough-In preview cutting `291K`-LOC `spec-kit` warm preview latency to `1.61s`), and certified & sealed **`3 / 3` (`100%`) external OSS probes** (`go-chi/chi @ c64ed7d663`: `APPROVED` / `OCCUPANCY_SEALED`, `79.0%` cache hits; `serde-rs/json @ f51fa15a70`: `APPROVED_WITH_VARIANCES` / `OCCUPANCY_SEALED` under `CodePermit`, `79.8%` cache hits; `github/spec-kit @ a55782f1f1`: `APPROVED` / `OCCUPANCY_SEALED` across `700` units / `32,419` baseline violations, `76.9%` cache hits).

---

## Track 12 — Turnkey Distribution, Reusable CI Gate, & `v0.2.0` Release Packaging — *Completed*

**Goal:** Package `jezz` `v0.2.0` with a reusable composite GitHub Action (`action.yml`) so external Python, TypeScript, Rust, and Go repositories can enforce `JBC` Edition `1.2` Differential Occupancy Ratchets and `PCE` Dossier seals in CI/CD with a single step.

- **Report & Verification:** [track-12-distribution-and-ci-gate.md](track-12-distribution-and-ci-gate.md) — Shipped `action.yml` (dogfooded via `uses: ./` in `.github/workflows/jbc-gate.yml`), synchronized `"0.2.0"` across `pyproject.toml`, `src/jezz/__init__.py`, and `src/jezz/mcp_server.py`, and verified end-to-end composite gate execution, `$GITHUB_STEP_SUMMARY` rendering, and zero shell-interpolation / hardcoded-path hazards (`tests/test_track12_packaging_and_action.py`).

---

## Track 13 — Shared Graph Kernel, `PermitProfile` Thresholds, & `Apache-2.0` Release Pipeline (`v0.2.1`) — *Completed*

**Goal:** Consolidate duplicated import-cycle and topological layering logic across Python and polyglot analyzers, expose configurable `JBC-COG-001` / `JBC-COG-002` complexity profiles (`PermitProfile.STANDARD` vs. `PermitProfile.STRICT_INTERNAL`), eliminate unpinned runtime `uvx` fetches inside `action.yml`, and ship tag-triggered `sdist` / `wheel` / PyPI Trusted Publisher release workflows under `Apache-2.0`.

- **Verification:** Unified `src/jezz/codes/dep_graph.py` across `rules_cog.py`, `polyglot_rules.py`, and `ast_graph.py`; added `PermitProfile` (`--profile standard|strict_internal` on `jezz inspect`, `run`, `renovate`, and `action.yml`); pinned `action.yml` directly to `uv run --project "${JEZZ_ACTION_PATH}"`; and verified all invariants in `tests/test_track13_profiles_and_kernel.py`.

---

## Track 14 — Scoped Polyglot `--ratchet` Gates, `(0, 600s]` Watchdogs, & $O(|\Delta|)$ Overlay Smoke Staging (`v0.2.1`) — *Completed*

**Goal:** Scope subprocess compiler and dynamic test invocations to authorized/modified slices during `jezz renovate --ratchet`, expand configurable gate watchdogs up to `600s` (`--gate-timeout` / `[tool.jezz.gates].timeout_sec`), and replace $O(|repo|)$ Python smoke-import copies with thread-safe $O(|\Delta|)$ `sys.meta_path` overlay staging supporting both `src/` and flat package layouts.

- **Verification:** Scoped `UvSubprocessGateRunner`, `TypeScriptSubprocessGateRunner`, `CargoSubprocessGateRunner` (`-p <crate>`), and `GoSubprocessGateRunner` (`./<pkg>`) under `ratchet_to_scope=True`; added `_OverlayPatchFinder` in `src/jezz/agents/workspace.py`; and verified all invariants in `tests/test_track14_ratchet_gates_and_staging.py`.

---

## Track 15 — `>=92%` CI Coverage Ratchet, Adapter Harnesses, Stateful `Hypothesis`, & 4-Language Parser Fuzzing (`v0.2.1`) — *Completed*

**Goal:** Enforce a hard `--cov=jezz --cov-fail-under=92` statement-coverage ratchet in CI (`>=85%` per module without `# pragma: no cover` waivers), verify subprocess/HTTP/CLI boundary adapters deterministically, and expand `Hypothesis` verification to `100%` of test modules including `RuleBasedStateMachine` suites and 4-language parser crash-fuzzing.

- **Verification:** Enforced `pytest-cov` in `.github/workflows/jbc-gate.yml`, documented host-tool workspace boundaries on `AntigravityCliModel`, verified adapter contracts in `tests/test_track15_adapter_coverage.py`, and verified stateful `WorkspaceSandbox` / `CodePermit` machines plus Python/TS/Rust/Go parser fuzzing in `tests/test_track15_stateful_and_parser_fuzz.py`.

---

## Track 16 — Multi-Ecosystem `PROVISION`, Offline Polyglot `BHV-06 CONSULT`, & Parameter-Bound Guard / Taint Precision (`v0.3.0`) — *Completed*

**Goal:** Support multi-ecosystem `DependencyRequirement` provisioning and atomic manifest rollback within a single mandate, generalize deterministic offline `BHV-06 CONSULT` across confined workspace docs and `py:`/`npm:`/`crate:`/`go:` package signatures, and tighten polyglot `JBC-DBC-004`/`005` and `JBC-SEC-001`/`005` to require parameter-referenced guards and track intra-callable taint aliases.

- **Verification:** Added `DependencyRequirement.ecosystem` + `PolyglotSubprocessProvisionRunner` multi-manifest rollback, `ConsultTargetStr` + `inspect_polyglot_package_api` (`node_modules` `.d.ts`/`package.json`, `Cargo.toml`/`src/lib.rs`, `go.mod`/`.go`), backward-closed per-parameter guard sets in `polyglot_ts.py`/`polyglot_rust.py`/`polyglot_go.py`, and local SQL/shell/path taint-alias tracking in `rules_sec_obs.py` (`tests/test_track16_polyglot_consult_and_taint.py`).

---

## Track 17 — Polyglot Adversarial SDD vs. `PCE` Benchmark Suite (`jezz benchmark --language`, `v0.3.0`) — *Completed*

**Goal:** Generalize `jezz.evals` (`BenchmarkTaskSpec`, `sdd_baseline.py`, `verifier.py`, `runner.py`, and `jezz benchmark --language <python|typescript|rust|go|all>`) across all four `SupportedLanguage`s with matched prompts, `EngineeringMandate`s, and offline held-out property/fuzz oracles (`node:test` Mulberry32 property loops, Rust SplitMix64 integration tests, and Go `testing/quick` suites) while preserving backward compatibility with the 6 canonical Python tasks.

- **Verification:** Implemented 12-case polyglot benchmark dataset (`6` Python, `2` TypeScript, `2` Rust, `2` Go), CST syntax validation on non-Python held-out oracles, `PolyglotWorkspaceVerifier` + `InProcessWorkspaceVerifier` polyglot grading, and end-to-end A/B evaluation in `tests/test_track17_polyglot_benchmark.py`.

---

## Tracks 18–20 — `<= 1,000` LOC Decomposition, Polyglot Inter-Procedural Call-Flow Engine, & Live MCP Hot-Reload Supervisor (`v0.4.0`) — *Completed*

**Goal:** Eliminate internal module size debt (`<= 1,000` LOC per `src/jezz/**/*.py` file), lift static guard and taint analysis from intra-callable local aliases to whole-workspace inter-procedural call-flow summaries across Python, TypeScript, Rust, and Go, and equip `jezz mcp` with a crash-resilient hot-reload supervisor during self-hosted `jezz` development.

- **Verification:**
  - **Track 18 (`tests/test_track18_module_decomposition.py`):** Decomposed all 8 oversized modules into cohesive, acyclic `<= 1,000` LOC units (`codebook_articles.py`, `cli_parsers.py`, `overlay_staging.py`, `specialist_board.py`, `polyglot_{ts,rust,go}_hazards.py`, `nodes_context.py`, `nodes_exec.py`) with `0` `JBC-COG-003` violations.
  - **Track 19 (`src/jezz/codes/call_flow*.py`, `tests/test_track19_interprocedural_analysis.py`):** Implemented monotone fixed-point inter-procedural call-flow propagation (`LoopBudget`-bounded) across Python, TypeScript, Rust, and Go, detecting cross-callable and cross-module `JBC-SEC-001` / `JBC-SEC-005` sink/source taint chains, honoring workspace validator-helper delegation for `JBC-DBC-001`/`004`/`005`, and preserving differential ratchet anchoring.
  - **Track 20 (`src/jezz/mcp_reload.py`, `tests/test_track20_mcp_hot_reload.py`):** Implemented SHA-256 `(rel_path, st_mtime_ns, st_size)` package fingerprint supervision on `build_jezz_mcp_server`, spawning isolated `sys.executable -P` reload workers on source drift and serving seamlessly from a materialized compile-verified healthy snapshot across transient mid-edit `SyntaxError`s.

---

## Track 21 — Live 24-Build Polyglot SDD-vs-`PCE` Benchmark Wave (`v0.4.0`) — *Completed*

**Goal:** Execute a live, matched 24-workspace polyglot A/B benchmark across all 6 TypeScript, Rust, and Go tasks (`case_ts_01_bps_split`, `case_ts_02_escrow_vault`, `case_rs_01_bps_split`, `case_rs_02_escrow_fsm`, `case_go_01_bps_split`, `case_go_02_strict_receipt`) on `claude_sonnet` and `claude_tiered` (`Claude Opus 5.5` Architect + `Claude Sonnet 5.5` Builder) via zero-credential `antigravity:*` routing.

- **Report & Verification (`docs/track-21-polyglot-live-benchmark.md`, `tests/test_track21_polyglot_live_wave.py`):** `jezz` `PCE` achieved `12 / 12` (`100.0%`) `JBC` v1.2 occupancy (`0` violations), `12 / 12` (`100.0%`) held-out property/fuzz oracle survival, `0` rollbacks, `-4.2%` lower effective billed input (`54.3%` cache hit ratio), `-56.7%` fewer output tokens, and `-60.8%` faster mean latency (`84.93s` vs. `216.87s`) against Prose SDD (`1 / 12` occupiable, `23` violations, `33.3%` silent oracle escape, and monotonic Wave 4D violation growth under `Opus 5.5` across all three languages).

---

## Tracks 22–26 — Scheme-Routed Secrets, Empirical TDD Bifurcation, `JBC-COG-006` (`Edition 1.3`), `ProjectCharter` Intake, & `BlueprintGraph` Switchable CAD / `LVS` Gate (`v0.5.0`) — *Completed*

**Goal:** Elevate `jezz` from module-and-slice governance to whole-project architectural governance across Python, TypeScript, Rust, and Go without sacrificing idiomatic interior implementation freedom.

- **Verification (`268` Python tests at `93.89%` coverage + `4/4` TypeScript `vitest` CAD suites, `135/135` workspace units `APPROVED`):**
  - **Track 22 (`src/jezz/secrets.py`, `tests/test_secrets.py`):** Generalized scheme-dispatched credential resolution (`SecretProviderRegistry`) supporting `env://`, `pass://` (Proton Pass CLI with `PROTON_PASS_AGENT_REASON`), `op://` (1Password CLI), and `keychain://` (macOS Keychain / Freedesktop Secret Service) across 4-tier `--secret-uri -> workspace pyproject.toml -> user config.toml -> canonical env` precedence.
  - **Track 23 (`docs/pillar-3a-sdd-teardown.md`, `docs/pillar-3b-pce-methodology.md`, `src/jezz/plugin.py`, `tests/test_methodology.py`):** Grounded controlled TDD vs. Iterative Test-Last (`ITL`) literature (Rafique & Mišić 2013; Fucci et al. 2017) and codified `PCE`'s archetype bifurcation: mandatory `RED -> GREEN` fault reproduction on defect repair (`build_abductive_bugfix_graph`) vs. Contract + Property-First synthesis on greenfield/renovation (`build_greenfield_contract_first_graph`).
  - **Track 24 (`src/jezz/codes/compartment.py`, `src/jezz/methodology/lot_line.py`, `tests/test_track24_compartment_and_lot_line.py`):** Introduced `JBC-COG-006` (`Edition 1.3`, 32nd provision) enforcing subpackage quotient-graph acyclicity ($G/\sim$), compartment/package floorplate caps (`35`/`100` standard; `25`/`75` `strict_internal`), external portal ratios ($|\partial C|/|C| \le 0.65$), and multi-package workspace fire-walls, paired with pre-flight `LotLineAssessment` (`jbc_assess_lot_line` & `DecomposeNode` `LOT_LINE_SPLIT` escalation).
  - **Track 25 (`src/jezz/methodology/charter*.py`, `src/jezz/mcp_charter.py`, `tests/test_track25_project_charter.py`):** Added Phase 0/I `ProjectCharter` intake (`probe_workspace_reality` -> 3-axis elicitation across `domain_ontology`, `hazard_mutation_policy`, and `scope_and_exclusions` -> `seal_project_charter` topological `EngineeringMandate` queue), exposed via `pce_start_intake`, `pce_seal_charter`, and `jezz charter`.
  - **Track 26 (`src/jezz/codes/blueprint/`, `src/jezz/codes/blueprint_lvs.py`, `src/jezz/cli_blueprint.py`, `src/jezz/mcp_blueprint.py`, `packages/jezz-blueprint/`, `tests/test_track26_blueprint_and_lvs.py`):** Implemented deterministic AST/CST `BlueprintGraph` netlist extraction, redline composition ($B^* = B_0 \oplus \Delta B$), synchronized switchable **Sheet `A-1` (Floor Plan)** $\leftrightarrow$ **Sheet `E-1` (`elkjs` Orthogonal Manhattan Wiring Schematic)** CAD projections (`jezz blueprint` & `@jezz/blueprint`), coarse-grained boundary `verify_blueprint_lvs` (`OPEN_CIRCUIT` / `SHORT_CIRCUIT`, Hypothesis-proven interior refactoring invariance), and dual-digest `CertificateOfOccupancy` / `EngineeringDossier` sealing.
