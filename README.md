# `jezz` — Licensure-Grade Software Engineering & Coding-Agent Framework

`jezz` is an experimental software engineering and coding-agent framework structured around four integrated pillars:

1. **[Pillar 1 — Complete Agent Behavior Basis (`jezz.behaviors`)](docs/pillar-1-behavior-basis.md):**
   An exhaustive taxonomy of atomic coding-agent behaviors with Fibonacci cardinality ($F_8 = 21$, `BHV-01`..`BHV-21`), local state-machine transition legality, and Kildall monotone dataflow verification.
2. **[Pillar 2 — Enforceable Software Building Codes (`jezz.codes`)](docs/pillar-2-building-codes.md):**
   The **Jezz Software Building Code (`JBC` Edition 1.1)**—30 machine-checkable provisions across 6 Articles (`DBC`, `FLT`, `SEC`, `COG`, `OBS`, `VRF`), enforced via `CodePermit`, `CodeVariance`, Rough-In & Final `BuildingInspector` gates, and SHA-256 `CertificateOfOccupancy` seals.
3. **Pillar 3 — SDD Forensic Teardown & Permit-and-Contract Engineering (`jezz.methodology`):**
   - **[Pillar 3A — Forensic Teardown of Industry "SDD"](docs/pillar-3a-sdd-teardown.md):** Primary-source critique of GitHub `spec-kit`, Fission `OpenSpec`, and AWS Kiro.
   - **[Pillar 3B — Permit-and-Contract Engineering (`PCE`)](docs/pillar-3b-pce-methodology.md):** A 5-phase, anti-Agile, anti-theatre engineering process (`Mandate` $\to$ `Permit` $\to$ `Contract` $\to$ `Construction` $\to$ `Occupancy`) audited deterministically by `audit_pce_dossier`.
4. **[Pillar 4 — Pydantic AI & Pydantic-Graph Runtime (`jezz.agents`)](docs/pillar-4-pydantic-ai-runtime.md):**
   Executable agent runtime combining least-privilege `WorkspaceSandbox` toolsets, `@output_validator` (`ModelRetry`) contract breakers, parallel `asyncio.TaskGroup` `DELEGATE`/`RECONCILE` slices, mandatory `RED_TAG` $\to$ `REVERT` $\to$ `HYPOTHESIZE` self-healing, and full `PCE` Dossier certification.

## Empirical Validation Roadmap
- **[Empirical Validation & Early Testing Roadmap](docs/empirical-testing-roadmap.md):** CLI harness, zero-cost brownfield `BuildingInspector` field trials, live-LLM `PceWorkflowOrchestrator` occupancy probes, and controlled `pydantic-evals` A/B benchmarks against prose "SDD".
- **[Track 2 — Brownfield Inspector Field Trial Report](docs/track-2-brownfield-field-trial.md):** Empirical audit across 6 repositories totaling 17,609 files / 7,414,726 LOC (`github/spec-kit`, `pydantic`, `pydantic-graph`, `pydantic-deepagents`, `KiroCrew`, and AWS Kiro E2E sample) and resulting engine/AST calibrations.
- **[Track 3 — First Live-Model Occupancy Trial Report](docs/track-3-live-occupancy-trial.md):** End-to-end `PceWorkflowOrchestrator` + `UvSubprocessGateRunner` trials on `google:gemini-3.8-flash` across ambiguity halting (`FRAME -> ELICIT`), adversarial integer-cent synthesis (`SPECIFY -> INSTRUMENT -> SYNTHESIZE`), and 3-worker parallel `TaskGroup` construction (`DELEGATE -> RECONCILE`).
- **[Track 4 — Controlled `pydantic-evals` A/B Benchmark Report](docs/track-4-sdd-vs-pce-benchmark.md):** 28-workspace live A/B evaluation across Waves 4A–4C (`Control: Prose SDD` vs. `Treatment: Jezz PCE` v1.2 on `google:gemini-3.8-flash`) achieving `6/6` (`100%`) task occupancy closure, `0%` silent defect escape (`18/18` held-out `Hypothesis` checks across Waves 4B + 4C), `16.1x` specification compression (`38.7` vs. `621.3` lines), and `-60%` to `-72%` peak Cognitive Complexity against `206` unwaived `JBC` violations (`15` `LIFE_SAFETY`) in checked-off `- [x]` SDD builds.

## CLI Usage

```bash
# Run JBC v1.1 Rough-In inspection on any Python repository (exit 0 = APPROVED, 1 = RED_TAG)
uv run jezz inspect <repo_path> [--permit permit.json] [--format markdown|json]

# Audit a 5-phase PCE EngineeringDossier against compiled workspace symbols and SHA-256 digest
uv run jezz audit <dossier.json> [--repo <repo_path>] [--format markdown|json]

# Execute a PCE prompt or mandate via PceWorkflowOrchestrator
uv run jezz run <repo_path> --model <provider:model> (--prompt "..." | --mandate mandate.json)

# Execute Track 4 controlled pydantic-evals A/B benchmark (Prose SDD vs. Jezz PCE)
uv run jezz benchmark <work_dir> --model <provider:model> [--max-concurrency 2] [--write-json ab.json]
```

## Verification Gates

```bash
uv run pytest
uv run mypy .
uv run ruff check .
uv run ruff format --check .
```
