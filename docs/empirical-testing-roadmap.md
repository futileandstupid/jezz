# Empirical Validation & Longitudinal Testing Roadmap

With Pillars 1–4 implemented, self-audited, and passing (`jezz.behaviors`, `jezz.codes`, `jezz.methodology`, `jezz.agents`), this roadmap tracks five progressive empirical validation stages evaluating `jezz` against brownfield Python repositories, live single- and multi-mandate LLM workflows, and industry "Spec-Driven Development" ("SDD") baselines.

---

## Track 1 — CLI, MCP, & Antigravity Lifecycle Harness (`jezz inspect` / `audit` / `run` / `renovate` / `init` / `mcp` / `hook`) — *Implemented*

**Goal:** Expose `BuildingInspector`, `audit_pce_dossier`, `PceWorkflowOrchestrator`, the read-only `stdio` MCP gate, and the Antigravity `Stop` lifecycle hook via a zero-variance CLI entrypoint (`pyproject.toml` `[project.scripts]` -> `src/jezz/cli.py`).

- **Commands:**
  - `uv run jezz inspect <repo_path> [--changed-since <ref> | --staged]` — Run full or differential Rough-In (`JBC` Edition `1.2`, `29` static / `31` total provisions) inspection (`0` on `APPROVED` / `APPROVED_WITH_VARIANCES`, `1` on `RED_TAG`, `2` on input error).
  - `uv run jezz audit <dossier_json> [--repo <repo_path>]` — Verify all five `PCE` phases and SHA-256 `CertificateOfOccupancy` seals via `audit_pce_dossier`.
  - `uv run jezz run` / `uv run jezz renovate` — Execute greenfield or brownfield `PceWorkflowOrchestrator` workflows (supporting `--plan-only`, `--write-mandate`, `--interactive`, `--progress`, and role-tiered `[tool.jezz.models]` + Proton Pass `pass-cli` secret resolution).
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
