# Track 3 — First Live-Model Occupancy Trial (`PceWorkflowOrchestrator`)

## 1. Trial Objective & Experimental Configuration

Following Track 1 (`jezz` CLI harness) and Track 2 (zero-crash brownfield `BuildingInspector` audit across 6 repositories / 7.41M LOC), Track 3 connects a **live production LLM** and **real `uv` subprocess verification gates** (`UvSubprocessGateRunner`: `uv run pytest -q` + `uv run mypy .` with `strict = true` and `plugins = ["pydantic.mypy"]`) to `PceWorkflowOrchestrator` via `uv run jezz run`.

- **Model Under Test:** `google:gemini-3.8-flash` via `pydantic-ai` v2.71.0 (`GoogleModel` / `google-genai` SDK).
  - Using a fast Flash-tier model rather than a frontier Pro-tier model deliberately stresses `jezz`'s runtime guardrails (`WIRE_TUPLE_VALIDATOR`, `_tool_preview_rough_in`, Tier-1 `@output_validator` `ModelRetry`, and Phase III contract/test locking).
- **Dynamic Gate Runner:** `UvSubprocessGateRunner` (`uv run pytest -q` with `Hypothesis` + `icontract`, and `uv run mypy .` under `--strict` + `pydantic.mypy`) inside isolated `/tmp/jezz_track3_probe_*` scratch workspaces.
- **Bound Code Permit:** Full 30-provision **Jezz Software Building Code (`JBC` Edition 1.1)** book (`PERMIT-PCE-RUNTIME`, zero `CodeVariance` waivers).

---

## 2. Diagnostic Probe Summary

| Probe | Target Capability | PCE Lifecycle Path Executed | Kildall Graph Valid | JBC v1.1 Violations | Total Requests / Tool Calls | Tokens (In / Out) | Final Disposition / Seal |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **3A — Ambiguity Trap** | Epistemic humility under under-determined intent (`"Implement a retryable webhook delivery service."`) | `FRAME -> ELICIT` (`2` nodes) | `True` | N/A (halted pre-mutation; `0` files written) | `2` / `1` | `1,828` / `2,587` | `HALTED_FOR_ELICITATION` (`4` concrete domain ambiguities surfaced) |
| **3B — Adversarial Domain Synthesis** | Single-scope 4-tier basis-point fee & exact integer-cent ledger settlement (`MND-FEE-LEDGER-3B`) | `FRAME -> BIND_CODES -> INSPECT_CODE -> DECOMPOSE -> CHECKPOINT -> SPECIFY -> INSTRUMENT -> CHECKPOINT -> SYNTHESIZE -> VERIFY_STATIC -> VERIFY_DYNAMIC -> CERTIFY` (`12` nodes) | `True` | **`0` / 30 codes** (`5` units inspected) | `47` / `42` | `415,235` / `56,750` | `OCCUPANCY_CERTIFIED` (`sha256:739814a0...`) + `OCCUPANCY_SEALED` dossier |
| **3C — Multi-Scope Parallel Construction** | 3-scope concurrent `asyncio.TaskGroup` escrow tax, release gate, & statement reconciler (`MND-ESCROW-GRID-3C`) | `FRAME -> BIND_CODES -> INSPECT_CODE -> DECOMPOSE -> CHECKPOINT -> SPECIFY -> INSTRUMENT -> CHECKPOINT -> DELEGATE (x3) -> RECONCILE -> VERIFY_STATIC -> VERIFY_DYNAMIC -> CERTIFY` (`13` nodes) | `True` | **`0` / 30 codes** (`7` units inspected) | `49` / `42` | `266,985` / `57,989` | `OCCUPANCY_CERTIFIED` (`sha256:5cf5aad1...`) + `OCCUPANCY_SEALED` dossier |

---

## 3. Per-Probe Forensic Analysis

### 3.1 Probe 3A — The Ambiguity Trap (`BHV-01 FRAME -> BHV-02 ELICIT`)

- **Command:** `uv run --env-file .env jezz run /tmp/jezz_track3_probe_3a --model google:gemini-3.8-flash --prompt "Implement a retryable webhook delivery service." --format json`
- **Observed Behavior:**
  - `jezz_mandate_framer` inspected the empty workspace (`1` tool call to `_tool_list_authorized_files`) and emitted a strictly valid `EngineeringMandate` (`MND-WEBHOOK-DELIVERY-01`) on its first structured response (`0` anti-Agile / weasel-wording `ValidationError` retries).
  - Rather than hallucinating retry constants or persistence backends, the framer populated `open_ambiguities` with **4 concrete domain blockers**:
    1. Unspecified retry policy bounds (max retry ceiling, initial backoff interval, multiplier, max backoff cap).
    2. Unspecified persistence/storage interface for delivery state and retry logs.
    3. Unspecified per-request HTTP timeout ceiling and maximum payload size bounds.
    4. Unspecified deduplication semantics and idempotency window duration.
  - `FrameNode` routed immediately to terminal `ElicitNode` (`PceRunStatus.HALTED_FOR_ELICITATION`), verified the 2-node induced `BehaviorGraph` via Kildall monotone dataflow analysis, and left the filesystem completely untouched (`0` files created).

### 3.2 Probe 3B — Adversarial Domain Synthesis (`MND-FEE-LEDGER-3B`)

- **Adversarial Traps Seeded:**
  1. **4-Tier Fee Schedule (`STANDARD=30 bps`, `SILVER=20 bps`, `GOLD=10 bps`, `INSTITUTIONAL=5 bps`):** Unconstrained LLMs default to 4-branch `if/elif/elif/else` ladders (`JBC-COG-004` violation: `> 1` `elif` or post-`return` `elif`).
  2. **Exact Basis-Point Settlement (`(gross_cents * tier_bps) // 10_000`):** Unconstrained LLMs convert to IEEE-754 `float` (`gross * 0.003`), failing cent-conservation invariants at large values (`0 .. 10^12` cents).
  3. **Strict Closed Boundaries:** Every `BaseModel` requires `STRICT_FROZEN_CONFIG` (`JBC-SEC-003`) and bounded fields (`JBC-DBC-005`), and every public callable requires `@jezz_callable` (`JBC-DBC-004`) + `@icontract` obligations (`JBC-DBC-001`).
- **Per-Node Agent Telemetry:**

| Behavior Step | Specialist Role | Requests | Tool Calls | Input Tokens | Output Tokens |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `BHV-04 INSPECT_CODE` | `code_inspector` | `3` | `1` | `2,789` | `466` |
| `BHV-14 SPECIFY` | `contract_specifier` | `15` | `14` | `172,728` | `23,137` |
| `BHV-15 INSTRUMENT` | `falsification_instrumenter` | `18` | `17` | `175,323` | `23,348` |
| `BHV-16 SYNTHESIZE` | `code_synthesizer` | `11` | `10` | `64,395` | `9,799` |
| **Total** | — | **`47`** | **`42`** | **`415,235`** | **`56,750`** |

- **Key Architectural Finding — Tier-1 Absorption Eliminated Tier-2 Rollbacks:**
  - Equipped with `_tool_preview_rough_in` and `@output_validator` (`ModelRetry`), the specialists iteratively self-checked and refined their ASTs inside Tier-1 (`falsified_paths = []`, **0 Tier-2 `RED_TAG -> REVERT -> HYPOTHESIZE` graph rollbacks**).
  - Generated `src/fee_ledger/contracts.py` used `types.MappingProxyType` for `TIER_BPS` (`0` `elif` chains), `StrEnum` for `AccountTier`, and `STRICT_FROZEN_CONFIG` + `NonNegativeInt` on `SettlementRequest` and `SettlementReceipt`.
  - Generated `tests/test_fee_ledger.py` authored 3 `@given(...)` `Hypothesis` property suites + explicit `@example(...)` boundary vectors up to `10^12` cents.
  - Generated `src/fee_ledger/settlement.py` attached `@jezz_callable` and `@icontract.require` / `@icontract.ensure` conservation predicates, passing both `uv run pytest -q` and `uv run mypy .` (`--strict`) on the first `VerifyDynamicNode` pass and sealing `sha256:739814a00f747f854b15d7cdb83a18061fbb64cfab4886faf1d1f58d2b255076` (independently verified via `uv run jezz audit`).

### 3.3 Probe 3C — Multi-Scope Parallel Construction (`MND-ESCROW-GRID-3C`)

- **Topology:** 3 pairwise-disjoint `ModuleWriteScope`s dispatched concurrently via `asyncio.TaskGroup` at `BHV-10 DELEGATE` and merged deterministically at `BHV-11 RECONCILE`:
  1. `scope_contracts_and_tax` $\to$ `src/escrow_grid/contracts.py`, `src/escrow_grid/tax.py`
  2. `scope_release_gate` $\to$ `src/escrow_grid/release.py`
  3. `scope_reconciler_and_tests` $\to$ `src/escrow_grid/reconciler.py`, `tests/test_escrow_grid.py`
- **Per-Node Agent Telemetry:**

| Behavior Step | Specialist Role | Requests | Tool Calls | Input Tokens | Output Tokens |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `BHV-04 INSPECT_CODE` | `code_inspector` | `3` | `1` | `2,980` | `1,009` |
| `BHV-14 SPECIFY` | `contract_specifier` | `10` | `9` | `57,979` | `14,190` |
| `BHV-15 INSTRUMENT` | `falsification_instrumenter` | `15` | `14` | `129,955` | `28,538` |
| `BHV-10 DELEGATE` (Worker 1: `tax`) | `delegation_coordinator` | `7` | `6` | `23,871` | `5,061` |
| `BHV-10 DELEGATE` (Worker 2: `release`) | `delegation_coordinator` | `5` | `4` | `13,131` | `2,117` |
| `BHV-10 DELEGATE` (Worker 3: `reconciler`) | `delegation_coordinator` | `9` | `8` | `39,069` | `7,074` |
| **Total** | — | **`49`** | **`42`** | **`266,985`** | **`57,989`** |

- **Key Architectural Finding — Phase III Contract Locking Prevented Multi-Agent Signature Drift:**
  - Because `SpecifyNode` (`contracts.py` with `@model_validator` conservation check) and `InstrumentNode` (`tests/test_escrow_grid.py` locking the exact call signatures of `compute_withholding_cents`, `evaluate_release_eligibility`, and `build_escrow_statement`) were checkpointed onto disk **prior** to `DelegateNode`, all 3 concurrent workers read the locked Phase III artifacts via `_tool_read_workspace_file` and converged with **zero cross-module signature mismatches** and **zero write-set collisions** (`Reconciled 4 disjoint artifact patch(es)`).
  - All 7 Python units passed all 30 `JBC` v1.1 provisions, `pytest` (`Hypothesis` + truth tables), `mypy --strict`, and standalone `uv run jezz audit` (`sha256:5cf5aad1e549db5ef686328547b8e41c8a7ae309588561a0c88b1558128ee3c5`).

---

## 4. Live-Runtime Calibrations Discovered & Landed in Track 3

Running against a live provider (`google:gemini-3.8-flash`) surfaced five real wire/runtime boundary friction points that deterministic `FunctionModel` unit tests could not expose—all resolved cleanly with zero `JBC` v1.1 variances:

1. **Pre-Decoded Provider JSON Array Coercion (`src/jezz/guards.py`, `mandate.py`, `schemas.py`):**
   - Unlike OpenAI (which returns JSON strings to `validate_json`), `GoogleModel` and `AnthropicModel` pass pre-parsed Python `dict`s (with `list` arrays) to `validate_python`. Under `STRICT_FROZEN_CONFIG` (`strict=True`), raw `tuple[...]` and `frozenset[...]` fields rejected `list` wire values. Added `WIRE_TUPLE_VALIDATOR` and `WIRE_FROZENSET_VALIDATOR` (`BeforeValidator`s converting `list -> tuple` / `frozenset` while keeping `strict=True` on inner scalars and rejecting strings/dicts).
2. **Non-Punitive Epistemic File Reads (`src/jezz/agents/workspace.py`, `nodes.py`):**
   - In greenfield runs, authorized files do not exist on disk before Phase III/IV creates them. Raising `ModelRetry` on `FileNotFoundError` inside `_tool_read_workspace_file` burned tool-retry budgets (`max_retries=2`) and extra LLM turns. `_tool_read_workspace_file` now returns an explicit `# ABSENT:` status comment on `FileNotFoundError` (preserving `ModelRetry` on path-traversal errors), and `_format_mandate_prompt` surfaces `Existing Authorized Files On Disk: [...]`.
3. **Domain-Agnostic Statutory Specialist Briefs (`src/jezz/agents/specialists.py`):**
   - Because `jezz` is a private framework absent from public LLM pretraining corpora, specialist system instructions now embed a concise, domain-agnostic `_JBC_STATUTORY_BRIEF` documenting `jezz.guards` primitives, Phase III vs. Phase IV file-role boundaries, and single-pass full-file `_tool_preview_rough_in` usage.
4. **`src.`-Prefixed Symbol Normalization (`src/jezz/methodology/dossier.py`):**
   - Live `frame_agent` outputs on `src/`-layout workspaces naturally qualify symbols as `src.<pkg>.<mod>.<Symbol>` (`Probe 3A`). `extract_compiled_symbols` now indexes both `<pkg>.<mod>.<Symbol>` and `src.<pkg>.<mod>.<Symbol>` for `src/` units.
5. **CLI Provider Error & Step Budget Handling (`src/jezz/cli.py`):**
   - `jezz run` now catches `ModelAPIError` and `AgentRunError` (`UsageLimitExceeded`) cleanly with POSIX exit code `2`, formats empty exception strings (`TimeoutError()`) via `type(exc).__name__`, and budgets `24` requests / `48` tool calls per step (`600s` default workflow timeout).

---

## 5. Operational Security Note — Transitioning Secret Injection to Proton Pass CLI (`pass-cli`)

For immediate Track 3 execution, `GEMINI_API_KEY` was injected at process launch via a gitignored local `.env` (`uv run --env-file .env ...`). Storing plaintext API keys on disk—even gitignored—is an interim compromise that does not meet `jezz`'s long-term `JBC-SEC-002` secret-containment posture against local credential-harvesting malware or accidental workspace snapshots.

- **Target Secret-Management Architecture:**
  - Store `GEMINI_API_KEY` (and future benchmark provider keys for Track 4) inside an encrypted **Proton Pass** vault (`pass://...` secret references in a committed, secret-free `.env.pass` template).
  - Inject credentials strictly into ephemeral child-process memory via **Proton Pass CLI**:
    ```bash
    pass-cli run --env-file .env.pass -- uv run jezz run <repo_path> --model google:gemini-3.8-flash ...
    ```
  - Delete plaintext `.env` once `pass-cli` vault references are provisioned.
