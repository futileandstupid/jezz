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

## Track 3 — First Live-Model Occupancy Trial (`PceWorkflowOrchestrator`)

**Goal:** Connect a live LLM provider (Gemini, Anthropic Claude, or OpenAI via `pydantic-ai`) + `UvSubprocessGateRunner` to `PceWorkflowOrchestrator` inside an isolated scratch workspace across three diagnostic probes:

1. **Probe 3A — The Ambiguity Trap (`BHV-01 FRAME -> BHV-02 ELICIT`):**
   - **Input:** Intentionally under-determined prompt (e.g., *"Implement a retryable webhook delivery service"* omitting backoff curve, max retries, and idempotency-key semantics).
   - **Pass Criterion:** Live `frame_agent` populates `EngineeringMandate.open_ambiguities` and halts cleanly at `ElicitNode` (`PceRunStatus.HALTED_FOR_ELICITATION`) with zero disk mutations.
2. **Probe 3B — Adversarial Domain Synthesis (`SPECIFY -> INSTRUMENT -> SYNTHESIZE`):**
   - **Input:** Constrained financial/stateful problem where unconstrained LLMs default to `float`, `elif` ladders, or `dict[str, Any]` (e.g., tiered basis-point fee & ledger settlement).
   - **Measured Telemetry:** Number of Tier-1 `@output_validator` (`ModelRetry`) turns vs. Tier-2 `RED_TAG -> BHV-20 REVERT -> BHV-08 HYPOTHESIZE` graph cycles required to earn a sealed `CertificateOfOccupancy`, plus `AgentInvocationTelemetry` token consumption.
3. **Probe 3C — Multi-Scope Parallel Construction (`BHV-10 DELEGATE -> BHV-11 RECONCILE`):**
   - **Input:** Multi-scope `EngineeringMandate` (2–3 disjoint `ModuleWriteScope`s).
   - **Pass Criterion:** Concurrent `asyncio.TaskGroup` workers satisfy Phase III contracts without cross-module signature drift or write-set collisions.

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
