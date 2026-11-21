# Track 2 — Brownfield `BuildingInspector` Field Trial Report (Tracks 2A & 2B)

This document records the empirical results of running `jezz inspect` (`JBC` Edition 1.1 Rough-In static/AST/cohesion/security inspection across 28 machine-checkable provisions) against **17,609 Python source files totaling 7,414,726 lines of code (7.41M LOC)** across six real-world repositories at **zero LLM token cost**.

> [!NOTE]
> **Language Scope Note on Fission `OpenSpec` & AWS Kiro:**
> - `Fission-AI/OpenSpec` (`@fission-ai/openspec`) is written in **TypeScript** (zero `.py` source files) and is therefore reserved for **Track 4**, where its prose-spec workflow (`openspec validate`) serves as a control arm when generating Python implementations.
> - While the proprietary **AWS Kiro IDE** binary is closed-source (`kirodotdev/Kiro` is an issue tracker only), AWS and `kirodotdev` publish two official Python repositories—**`kirodotdev/KiroCrew`** and **`aws-samples/sample-e2e-product-development-with-kiro`**—both of which were audited in full below.

---

## 1. Evaluated 6-Repository Corpus

| Wave | Target Repository | Domain & Layout | Python Units | Prod / Test Split | Total LOC |
| :---: | :--- | :--- | ---: | ---: | ---: |
| **2A** | **GitHub `spec-kit`** (`github/spec-kit`) | Flagship industry "SDD" CLI (`src/specify_cli`, `tests/`) | 699 | 308 / 391 | 291,626 |
| **2A** | **`pydantic-graph`** (`v2.71.0`) | Typed state-machine library (`src/pydantic_graph`) | 14 | 14 / 0 | 4,715 |
| **2A** | **`pydantic-deepagents`** (`vstorm-co/pydantic-deepagents`) | Multi-agent `pydantic-ai` harness & TUI (`pydantic_deep/`, `apps/`, `tests/`) | 298 | 214 / 84 | 90,331 |
| **2B** | **`pydantic`** (`pydantic/pydantic`) | Core schema/validation library, metaclasses, & test suite (`pydantic/`, `tests/`) | 3,162 | 67 / 3,095 | 225,134 |
| **2B** | **`KiroCrew`** (`kirodotdev/KiroCrew`) | Official Kiro persistent Python agent runtime (`src/kiro_crew`, `test/`) | 13,426 | 4,588 / 8,838 | 6,800,169 |
| **2B** | **AWS Kiro E2E Sample** (`aws-samples/sample-e2e-product-development-with-kiro`) | Official AWS Kiro "SDD" reference framework (`agent-lattice/`) | 10 | 10 / 0 | 2,751 |
| **All** | **Combined Field-Trial Total** | **6 heterogeneous Python codebases** | **17,609** | **5,201 / 12,408** | **7,414,726** |

---

## 2. Signal 1 — True-Positive Defect & Architectural Findings

### 2.1 GitHub `spec-kit` (`291,626` LOC — `RED_TAG`)
Despite marketing prose specifications as "executable artifacts" (*Pillar 3A*), `spec-kit`'s Python implementation exhibited severe structural and life-safety violations:
- **Command Injection & Dynamic Execution (`JBC-SEC-001`, `LIFE_SAFETY` — 4 hits):** `src/specify_cli/workflows/step/shell/__init__.py:120` invokes `subprocess.run(..., shell=True)`, plus three `exec()` calls in test parity harnesses.
- **Silent Fault Suppression (`JBC-FLT-001`, `LIFE_SAFETY` — 161 hits):** `except ...: pass` blocks silently discard runtime faults inside security-critical paths including `src/specify_cli/_download_security.py:183`, `_assets.py:125`, and `__init__.py:527`.
- **Implicit `None` Fallthrough in Security Routines (`JBC-DBC-003`, `STRUCTURAL` — 11 hits):** `read_response_limited` (`_download_security.py:405`) and `read_zip_member_limited` (`:479`) declare value returns while permitting control flow to fall off the end into implicit `None`.
- **Unbounded Subprocess & Stream Ceilings (`JBC-FLT-003`: 341 hits; `JBC-FLT-006`: 139 hits):** `341` `subprocess.run()` calls omit `timeout=`; `139` `.read_text()` / `.read_bytes()` calls read arbitrary files without byte ceilings.
- **Zero-Assertion Smoke Tests & God Classes (`JBC-VRF-001`: 29 hits; `JBC-COG-005`: 329 hits):** `CommandRegistrar` (`src/specify_cli/agents.py:40`) reaches **WMC = 395** (11.3x the `JBC-COG-005` limit of `35`), alongside `26,042` missing annotations / `Any` erasures (`JBC-DBC-002`).

### 2.2 Official AWS / Kiro Python Repositories (`KiroCrew` & `sample-e2e-product-development-with-kiro`)
- **AWS Kiro E2E SDD Showcase (`2,751` LOC — `RED_TAG`):**
  - **Zero Verification Suite:** AWS's official end-to-end showcase for Kiro's "Spec-Driven Development" workflow contains `10` production Python scripts (`agent-lattice/`) and **zero Python test files (`test=0`)**.
  - ** Pervasive Unvalidated I/O & Untyped Signatures:** `224` missing parameter/return type annotations (`JBC-DBC-002`), `25` untyped `json.load()` calls (`JBC-SEC-004`), `51` uncontained `open()` / `shutil.rmtree()` filesystem sinks (`JBC-SEC-005`), `16` unbounded `.read()` slurps (`JBC-FLT-006`), silent `except: pass` in `installer.py:349` (`JBC-FLT-001`), and an undeclared third-party `import jsonschema` (`al-manifest.py:179`, `JBC-VRF-003`) with no `pyproject.toml` or `setup.cfg` in the repository.
- **`kirodotdev/KiroCrew` (`13,426` units / `6,800,169` LOC — `RED_TAG`):**
  - **Silent Fault Suppression at Scale (`JBC-FLT-001`, `LIFE_SAFETY` — 3,864 hits; 3,362 in `src/`):** Widespread `except ...: pass` blocks including inside `_process_group_supervisor.py:81,283,288,362` and `_spawn_exec_shim.py:153`.
  - **Fire-and-Forget Async Task Spawns (`JBC-FLT-005`, `STRUCTURAL` — 5,362 hits; 1,151 in `src/`):** Over a thousand `asyncio.ensure_future()` / `create_task()` spawns outside structured `TaskGroup` scopes (`acp/batch_guard.py`, `acp/client_seed_ownership.py`).
  - **Error-Swallowing Smoke Tests (`JBC-VRF-001`, `LIFE_SAFETY` — 5,705 hits):** Thousands of test functions (explicitly named e.g. `test_a_write_failure_never_raises`, `test_agent_runner_emit_activity_swallows_a_sink_error`) execute code without a single `assert` or `pytest.raises` check.
  - **Gate-Silencing Directives (`JBC-VRF-004`, `STRUCTURAL` — 22,921 hits; 10,734 in `src/`):** Extensive `# noqa`, `# type: ignore`, and `# pragma: no cover` suppressions.
  - **Nuanced Forensic Observation on `JBC-SEC-001` (`326` hits; `207` in `src/`):** Inspecting `opencode_transcripts.py:108,163` revealed dynamic f-string SQL construction such as `conn.execute(f'DELETE FROM "{table}" WHERE "{column}" IN ({marks})', ids)` where row values (`ids`) *are* parameterized via `?` placeholders while `table`/`column` identifiers are regex-checked (`_TABLE_NAME_RE`). `JBC-SEC-001` intentionally retains `RED_TAG` enforcement on all f-string SQL sinks because value-parameter tuples on argument 2 do not prevent SQL injection if an unvalidated identifier or `WHERE` clause fragment is interpolated into argument 1.

### 2.3 `pydantic` (`225,134` LOC) & `pydantic-graph` (`4,715` LOC) — Typed Library Baselines
- **`pydantic` (`3,162` units inspected in `15.85s`):**
  - Cleanly parsed and analyzed all metaclass, `@dataclass_transform`, and generic-schema machinery with **zero crashes**.
  - Surfaced genuine findings: `yaml.load()` without `SafeLoader` in `docs/plugins/main.py:473` (`JBC-SEC-001`), missing `timeout=` on `subprocess.check_output()` in `pydantic/_internal/_git.py:17,27` and `requests.get()` in `docs/plugins/main.py:297` (`JBC-FLT-003`), unchained exception replacements in `pydantic/_internal/_validators.py:178,188,198` (`JBC-FLT-002`), silent `except: pass` blocks in `_generate_schema.py` / `_fields.py` (`JBC-FLT-001`), and `ConfigWrapper` holding `49` attributes (`_config.py:57`, `JBC-COG-005`).
- **`pydantic-graph` (`14` units):**
  - **0 violations** across all Security (`JBC-SEC-001..005`), Observability (`JBC-OBS-001..003`), `JBC-DBC-001`, and `JBC-FLT-003` provisions, with 1-to-1 alignment between `JBC-COG-001` complexity spikes and upstream `# noqa: C901` annotations.

### 2.4 `pydantic-deepagents` (`90,331` LOC — `RED_TAG`)
- Caught `subprocess.Popen(..., shell=True)` (`pydantic_deep/features/forking/editor.py:137`, `JBC-SEC-001`), uncontained `shutil.rmtree()` (`apps/cli/commands.py:1332`, `JBC-SEC-005`), `46` unstructured `asyncio.create_task()` / `ensure_future()` spawns (`JBC-FLT-005`), `44` undeclared transitive imports (`docker`, `httpx`, `fastmcp`, `JBC-VRF-003`), and `create_cli_agent` taking **45 parameters** at **CC = 104** (`apps/cli/agent.py:149`, `JBC-COG-001`/`002`).

---

## 3. Signal 2 — Engine Scaling & AST Idiom Calibrations (Waves 2A & 2B)

Across the two waves, empirical stress-testing drove **11 permanent engine and AST-precision upgrades**, locked in via `tests/test_codes.py`:

| # | Component / Provision | Empirical Trigger (Repo) | Root Cause & Implemented Upgrade |
| :-: | :--- | :--- | :--- |
| 1 | `rules_cog.py` (`JBC-COG-003`) | `KiroCrew` (`13,426` modules) | Replaced $O(V(V + E))$ all-pairs BFS cycle detection with iterative, `LoopBudget`-guarded **Tarjan Strongly Connected Components ($O(V + E)$)** (`211.5s` $\to$ `174.2s` on `5,005`-unit `src/` despite full 28-rule AST pass, scaling linearly to `13,426` units). |
| 2 | `permits.py`, `inspector.py`, `dossier.py` | `spec-kit` (`31.9K` findings), `KiroCrew` (`609.4K` findings) | Bounded preview formatting on `RawDiscrepancy.message` (**27 crashes $\to$ 0**), raised `InspectionReport.findings` cap to `1,000,000` and `units` cap to `100,000`, and added `max_findings` truncation to `InspectionReport.to_markdown()`. |
| 3 | `inspector.py` (`JBC-VRF-003`) | `pydantic-deepagents`, `KiroCrew` | Separated PEP 621 feature extras (`[project.optional-dependencies]`) into `runtime_roots` (`810` $\to$ `44`, **-94.6%** on `pydantic-deepagents`), added `setup.cfg` (`configparser`) fallback for dynamic setuptools manifests (`1,618` $\to$ `555`, **-65.7%** on `KiroCrew` `src/`), and expanded `_PYPI_IMPORT_ALIASES`. |
| 4 | `ast_utils.py`, `cli.py` | `KiroCrew` (`test/`), `aws-kiro-sample` (`al_paths.py`) | Added singular `test/` directory discovery and test-unit classification (`+8,421` test files discovered in `KiroCrew`), plus standalone script-stem workspace grounding (`13` $\to$ `1`, **-92.3%** on `aws-kiro-sample`). |
| 5 | `rules_dbc.py`, `rules_vrf.py` (`JBC-DBC-003`, `JBC-VRF-002`) | `pydantic` (`DocstringVisitor`) | Exempted stdlib `ast.NodeVisitor.visit_*` subtree-pruning `pass` hooks (`JBC-VRF-002`) and side-effect-only `visit_* -> Any` hooks (`JBC-DBC-003`). |
| 6 | `cohesion.py`, `rules_cog.py` (`JBC-COG-005`, `JBC-COG-003`) | `spec-kit`, `pydantic-graph` | Exempted stateless pytest `Test*` suite classes (`-62.2%` on `spec-kit`, `-69.4%` on `pydantic-deepagents`), tracked whole-object `self` value references (`StepNode(self, inputs)`), and allowed intra-package `_submodule` imports. |
| 7 | `rules_dbc.py`, `rules_flt.py`, `rules_vrf.py`, `rules_sec_obs.py` | All 6 repositories | Path-expression `ast.Div` detection (`tmp_path / name`, `-63.0%` on `spec-kit`), CLI/iterator exit signal exemptions (`typer.Exit()`, `Protocol` stubs), `.lstrip("_")` on `_assert_*` test helpers, leading-comment anchoring on `# TODO:` tags, and non-secret metadata exclusions (`*_env`, `*_id`, `*_prefix`, `method_*`, `-68.4%` on `pydantic` `JBC-SEC-002`). |

---

## 4. Signal 3 — Brownfield `CodeVariance` Permit Validation

Phased brownfield onboarding was verified on `pydantic_graph/exceptions.py`:
1. **Unwaived Inspection:** `inspect_rough_in` issued `OccupancyRuling.RED_TAG` (`5` `STRUCTURAL` findings on upstream `__init__` signatures).
2. **Signed `CodePermit` Onboarding:** Binding `PERMIT-PG-EXCEPTIONS-LEGACY` transitioned the module to `OccupancyRuling.APPROVED_WITH_VARIANCES` (`0` unwaived violations, `5` audited variances).
3. **`LIFE_SAFETY` Non-Waivability:** Attempting to construct a `CodeVariance` for `JBC-SEC-001` (`shell=True` command injection) raised `ValueError` (`Code 'JBC-SEC-001' (life_safety) is non-waivable; no CodeVariance may be granted`).
