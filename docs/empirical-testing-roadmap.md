# Empirical Validation & Early Testing Roadmap

With Pillars 1–4 implemented, self-audited, and passing (`jezz.behaviors`, `jezz.codes`, `jezz.methodology`, `jezz.agents`), this roadmap defines four progressive empirical testing tracks to evaluate `jezz` against real-world Python codebases, live LLMs, and industry "Spec-Driven Development" ("SDD") baselines.

---

## Track 1 — Thin CLI Harness (`jezz inspect` / `jezz audit` / `jezz run`) — *Implemented*

**Goal:** Expose `BuildingInspector`, `audit_pce_dossier`, and `PceWorkflowOrchestrator` via a minimal, zero-variance CLI entrypoint (`pyproject.toml` `[project.scripts]` -> `src/jezz/cli.py`) so external repositories and scratch workspaces can be inspected or executed from a single terminal command.

- **Commands:**
  - `uv run jezz inspect <repo_path>` — Run Rough-In (`JBC` v1.1 static/AST/cohesion/security) inspection over `<repo_path>` (supporting both `src/` and flat package layouts while pruning virtualenvs/caches) and emit a Markdown or JSON `InspectionReport` (`0` on `APPROVED` / `APPROVED_WITH_VARIANCES`, `1` on `RED_TAG`, `2` on input error).
  - `uv run jezz audit <dossier_json>` — Verify all five `PCE` phases via `audit_pce_dossier`.
  - `uv run jezz run <repo_path> --model <provider:model> (--prompt "..." | --mandate <json>)` — Execute `PceWorkflowOrchestrator` with optional `--write-dossier` output.
- **Status:** Verified (`src/jezz/cli.py`, `src/jezz/__main__.py`, `tests/test_cli.py`) with zero `JBC` v1.1 variances.

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

## Track 4 — Controlled A/B Benchmark via `pydantic-evals` (Prose "SDD" vs. `jezz` `PCE`)

**Goal:** Quantitatively test Pillar 3A's core thesis using `pydantic-evals` over 5–8 benchmark tasks seeded with classic LLM failure modes (injection sinks, IEEE-754 float corruption, silent exception swallowing, unbounded retries/loops, class duty creep, tautological tests).

- **Experimental Arms (Identical Base Model & Temperature):**
  - **Control Arm (Marketed Prose SDD):** Generate `spec.md` $\to$ `design.md` $\to$ `tasks.md` + `constitution.md` system prompt $\to$ implementation.
  - **Treatment Arm (`jezz` `PCE` + `JBC` v1.1):** Execute the same initial prompt/mandate through `PceWorkflowOrchestrator`.
- **Scored Evaluators:**
  1. Unwaived `JBC` `LIFE_SAFETY` and `STRUCTURAL` violation counts on emitted code.
  2. `Hypothesis` adversarial property falsification survival rate.
  3. Structural maintainability (`LCOM4`, `WMC`, Cognitive Complexity, `elif` count).
  4. Total tokens, wall-clock latency, and human-reviewable specification line count (~20-line `EngineeringMandate` vs. multi-file Markdown volume).
